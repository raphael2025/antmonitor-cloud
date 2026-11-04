"""Durable alarm state: restart reconciles facts and sends only unacknowledged channels."""
import sqlite3
from unittest.mock import Mock

import pytest

import db
import notify
from test_notify_delivery import START, SITE, clock, conn, notifier, snap, tick  # noqa: F401


def test_process_restart_recovers_failed_channel_but_never_repeats_success(tmp_path, clock, monkeypatch):
    path = str(tmp_path / "durable.db")
    channels = {"webhook": {"enabled": True, "url": "https://example.invalid/audit"}}
    telegram, webhook = Mock(return_value=True), Mock(side_effect=[False, True])
    monkeypatch.setattr(notify, "_send_telegram", telegram)
    monkeypatch.setattr(notify, "_send_webhook", webhook)
    first = db.connect(path)
    value = notifier(first, **channels)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    first.close()
    second = db.connect(path)
    value = notifier(second, **channels)
    tick(value, clock, 120, "unreachable")
    assert telegram.call_count == 1 and webhook.call_count == 2
    assert not value.pending
    second.close()
    third = db.connect(path)
    tick(notifier(third, **channels), clock, 3600, "unreachable")
    assert telegram.call_count == 1 and webhook.call_count == 2
    third.close()


def test_restart_respects_existing_backoff(conn, clock, monkeypatch):
    sender = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    value = notifier(conn)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    tick(value, clock, 120, "unreachable")
    value = notifier(conn)
    tick(value, clock, 180, "unreachable")
    assert sender.call_count == 2
    tick(value, clock, 240, "unreachable")
    assert sender.call_count == 3
    assert value.pending[(SITE["id"], "unreachable")]["attempts"] == 3


def test_fault_recovered_while_stopped_is_not_replayed(conn, clock, monkeypatch):
    sender = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    value = notifier(conn)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    sender.reset_mock()
    value = notifier(conn)
    tick(value, clock, 120, "ok")
    assert all("场地失联" not in call.args[1] for call in sender.call_args_list)
    assert (SITE["id"], "unreachable") not in value.pending


def test_active_drop_preserves_original_baseline_when_restart_rolling_average_has_fallen(conn, clock, monkeypatch):
    sender = Mock(return_value=True)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    history = {"current": 100, "baseline": 100}
    monkeypatch.setattr(db, "avg_hashrate", lambda _c, _s, left, right, _age:
                        history["current"] if right - left == 600 else history["baseline"])
    value = notifier(conn, hashrate_drop_pct=30)
    snap(conn, START)
    tick(value, clock, 0, "ok")
    history["current"] = 50
    snap(conn, START + 60, rate=50)
    tick(value, clock, 60, "ok")
    assert value.active_drop[SITE["id"]] == 100
    history["baseline"] = 50
    value = notifier(conn, hashrate_drop_pct=30)
    snap(conn, START + 120, rate=50)
    tick(value, clock, 120, "ok")
    assert value.active_drop[SITE["id"]] == 100
    assert sender.call_count == 1
    history["current"] = 90
    snap(conn, START + 180, rate=90)
    tick(value, clock, 180, "ok")
    assert sender.call_count == 2 and "算力恢复" in sender.call_args.args[1]


def test_restart_keeps_success_cooldown_and_delayed_second_fault(conn, clock, monkeypatch):
    sender = Mock(return_value=True)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    value = notifier(conn)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    tick(value, clock, 120, "ok")
    value = notifier(conn)
    tick(value, clock, 180, "unreachable")
    assert sender.call_count == 2
    tick(value, clock, 1860, "unreachable")
    assert sender.call_count == 3


def test_event_and_current_state_are_durable_before_network_send(conn, clock, monkeypatch):
    value = notifier(conn)
    tick(value, clock, 0, "ok")
    observed = []
    def sender(_cfg, _msg):
        restart = notifier(conn)
        observed.append((restart.prev, set(restart.pending)))
        return True
    monkeypatch.setattr(notify, "_send_telegram", sender)
    tick(value, clock, 60, "unreachable")
    assert observed == [({SITE["id"]: "unreachable"}, {(SITE["id"], "unreachable")})]


def test_each_channel_receipt_survives_crash_before_next_channel(conn, clock, monkeypatch):
    channels = {"webhook": {"enabled": True, "url": "https://example.invalid/audit"}}
    telegram = Mock(return_value=True)
    monkeypatch.setattr(notify, "_send_telegram", telegram)
    monkeypatch.setattr(notify, "_send_webhook", Mock(side_effect=KeyboardInterrupt))
    value = notifier(conn, **channels)
    tick(value, clock, 0, "ok")
    with pytest.raises(KeyboardInterrupt):
        tick(value, clock, 60, "unreachable")
    webhook = Mock(return_value=True)
    monkeypatch.setattr(notify, "_send_webhook", webhook)
    value = notifier(conn, **channels)
    tick(value, clock, 120, "unreachable")
    assert telegram.call_count == webhook.call_count == 1
    assert not value.pending


def test_deleted_site_clears_persisted_queue_and_inflight_state_cannot_resurrect_it(conn, clock, monkeypatch):
    sender = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    value = notifier(conn)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    db.delete_site(conn, SITE["id"])
    assert not notifier(conn).pending
    tick(value, clock, 120, "unreachable")  # Stale collector site list already in flight.
    assert sender.call_count == 1
    assert not notifier(conn).pending
    db.restore_site(conn, SITE["id"])
    tick(value, clock, 180, "ok")
    assert sender.call_count == 1
    assert not notifier(conn).pending


def test_unknown_counts_after_restart_pause_old_fault_until_fresh_evidence(conn, clock, monkeypatch):
    sender = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    value = notifier(conn, offline_threshold=4)
    snap(conn, START)
    tick(value, clock, 0, "ok")
    snap(conn, START + 60, online=5)
    tick(value, clock, 60, "alert")
    value = notifier(conn, offline_threshold=4)
    snap(conn, START + 120, online=None, unknown=10)
    tick(value, clock, 120, "alert")
    assert sender.call_count == 1
    assert value.pending and value.active_offline
    snap(conn, START + 180, online=10)
    tick(value, clock, 180, "ok")
    assert all("掉线告警" not in call.args[1] for call in sender.call_args_list[1:])


def test_restart_backlog_is_bounded_per_tick(conn, clock, monkeypatch):
    sender = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    sites = [{**SITE, "id": "site-%02d" % i} for i in range(30)]
    value = notifier(conn)
    tick(value, clock, 0, "ok", sites)
    value.cfg["telegram"]["enabled"] = False
    tick(value, clock, 60, "unreachable", sites)
    assert len(value.pending) == 30 and sender.call_count == 0
    sender.return_value = True
    value = notifier(conn)
    tick(value, clock, 120, "unreachable", sites)
    assert 0 < sender.call_count <= 20
    tick(value, clock, 180, "unreachable", sites)
    assert sender.call_count == 30 and not value.pending


def test_upgrade_from_existing_database_preserves_site_metadata_and_history(tmp_path):
    path = str(tmp_path / "legacy.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE site_management(
          site_id TEXT PRIMARY KEY, name_override TEXT, deleted_name TEXT,
          deleted_ts INTEGER NOT NULL DEFAULT 0);
        INSERT INTO site_management VALUES('retained-site', 'User name', NULL, 0);
        INSERT INTO site_management VALUES('deleted-site', NULL, 'Former name', 123);
        CREATE TABLE notify_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
          site_id TEXT, type TEXT, msg TEXT, ok INTEGER);
        INSERT INTO notify_log(ts,site_id,type,msg,ok)
          VALUES(100,'retained-site','unreachable','Existing history',1);
    """)
    old.close()
    value = db.connect(path)
    try:
        metadata = db.site_management(value)
        assert metadata["retained-site"]["name_override"] == "User name"
        assert metadata["deleted-site"]["deleted_ts"] == 123
        assert db.notification_generation(value, "retained-site") == (0, False)
        assert db.notification_generation(value, "deleted-site") == (0, True)
        assert db.notification_load(value) == ([], [], [])
        assert value.execute("SELECT msg FROM notify_log").fetchone()[0] == "Existing history"
    finally:
        value.close()
    # A second startup must see an already migrated schema without duplicating it.
    value = db.connect(path)
    try:
        assert db.notification_generation(value, "retained-site") == (0, False)
        assert value.execute("SELECT COUNT(*) FROM notify_log").fetchone()[0] == 1
    finally:
        value.close()


def test_uncommitted_notification_is_never_sent_and_recovers_when_storage_returns(conn, clock, monkeypatch):
    sender = Mock(return_value=True)
    monkeypatch.setattr(notify, "_send_telegram", sender)
    value = notifier(conn)
    tick(value, clock, 0, "ok")
    save = db.notification_save
    monkeypatch.setattr(db, "notification_save", Mock(side_effect=sqlite3.OperationalError("disk full")))
    tick(value, clock, 60, "unreachable")
    assert sender.call_count == 0
    assert notifier(conn).prev == {SITE["id"]: "ok"}
    monkeypatch.setattr(db, "notification_save", save)
    tick(value, clock, 120, "unreachable")
    assert sender.call_count == 1
    assert not notifier(conn).pending
    assert notifier(conn).prev == {SITE["id"]: "unreachable"}
