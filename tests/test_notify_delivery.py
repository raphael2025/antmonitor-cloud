"""Cloud operational alarms: honest recovery, channel receipts and retry lifecycle."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
import notify

START = 2_000_000_000
SITE = {"id": "audit-site", "name": "Audit site", "type": "air", "mode": "push"}


@pytest.fixture
def conn():
    value = db.connect(":memory:")
    yield value
    value.close()


@pytest.fixture
def clock(monkeypatch):
    value = [START]
    monkeypatch.setattr(notify.time, "time", lambda: value[0])
    return value


def notifier(conn, **alerts):
    return notify.Notifier({"alerts": {
        "enabled": True, "hashrate_drop_pct": 0, "cooldown": 1800,
        "telegram": {"enabled": True, "bot_token": "audit-token", "chat_id": "audit-chat"},
        **alerts,
    }}, conn)


def tick(value, clock, offset, state, sites=None):
    clock[0] = START + offset
    value.tick([SITE] if sites is None else sites, lambda *_: state)


def snap(conn, ts, rate=100, **extra):
    row = {"site_id": SITE["id"], "ts": ts, "ok": 1, "online": 10, "total": 10,
           "unknown": 0, "hashrate_ths": rate, "hashrate_complete": True,
           "scan_ts": ts, "scan_age_s": 0, "measurement_ts": ts,
           "measurement_time_supported": True, "active_alerts": 0}
    row.update(extra)
    db.insert_snapshot(conn, row)


def test_sustained_half_hashrate_does_not_recover_as_rolling_baseline_falls(conn, clock, monkeypatch):
    value = notifier(conn, hashrate_drop_pct=30)
    send = Mock()
    monkeypatch.setattr(value, "_send", send)
    for offset in range(-5400, 1, 60):
        snap(conn, START + offset, 100 if offset < -600 else 50)
    value._check_drop(SITE["id"], SITE, START)
    assert send.call_args.args[1] == "hashrate_drop"
    assert value.active_drop[SITE["id"]] == 100
    for offset in range(60, 5401, 60):
        snap(conn, START + offset, 50)
    clock[0] = START + 5400
    assert db.avg_hashrate(conn, SITE["id"], START, START + 2700) == 50
    value._check_drop(SITE["id"], SITE, clock[0])
    assert send.call_count == 1  # Still half the original hashrate; no false recovery.
    for offset in range(5460, 6121, 60):
        snap(conn, START + offset, 90)
    clock[0] = START + 6120
    value._check_drop(SITE["id"], SITE, clock[0])
    assert [call.args[1] for call in send.call_args_list] == ["hashrate_drop", "hashrate_recovered"]


def test_missing_current_observation_does_not_recover_active_drop(conn, clock, monkeypatch):
    value = notifier(conn, hashrate_drop_pct=30)
    value.active_drop[SITE["id"]] = 100
    send = Mock()
    monkeypatch.setattr(value, "_send", send)
    value._check_drop(SITE["id"], SITE, START)  # Empty history = no current observation.
    assert value.active_drop[SITE["id"]] == 100
    send.assert_not_called()


def test_failed_site_loss_retries_without_waiting_for_success_cooldown(conn, clock, monkeypatch):
    value = notifier(conn)
    send = Mock(side_effect=[False, True])
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    assert send.call_count == 1 and not value.last_sent
    tick(value, clock, 119, "unreachable")
    assert send.call_count == 1
    tick(value, clock, 120, "unreachable")
    assert send.call_count == 2 and not value.pending
    tick(value, clock, 3600, "unreachable")
    assert send.call_count == 2  # The delivered event is not resent every tick.
    rows = conn.execute("SELECT type,ok FROM notify_log ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [("unreachable", 0), ("unreachable", 1)]


def test_retry_backoff_and_attempt_counter_are_bounded(conn, clock, monkeypatch):
    value = notifier(conn)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    key = (SITE["id"], "unreachable")
    delays = []
    for _ in range(10):
        pending = value.pending[key]
        delays.append(pending["due"] - clock[0])
        offset = pending["due"] - START
        before = send.call_count
        tick(value, clock, offset - 1, "unreachable")
        assert send.call_count == before
        tick(value, clock, offset, "unreachable")
        assert send.call_count == before + 1
    assert delays[:5] == [60, 120, 240, 480, 900]
    assert max(delays) == 900 and value.pending[key]["attempts"] == 5
    assert len(value.pending) == 1


def test_partial_channel_success_only_retries_failed_channel_and_logs_each(conn, clock, monkeypatch):
    value = notifier(conn, webhook={"enabled": True, "url": "https://example.invalid/audit"})
    telegram, webhook = Mock(return_value=True), Mock(side_effect=[False, True])
    monkeypatch.setattr(notify, "_send_telegram", telegram)
    monkeypatch.setattr(notify, "_send_webhook", webhook)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    assert telegram.call_count == webhook.call_count == 1
    assert not value.last_sent
    tick(value, clock, 120, "unreachable")
    assert telegram.call_count == 1 and webhook.call_count == 2
    assert not value.pending
    logs = conn.execute("SELECT msg,ok FROM notify_log ORDER BY id").fetchall()
    assert [(row["msg"].split()[0], row["ok"]) for row in logs] == [
        ("[telegram]", 1), ("[webhook]", 0), ("[webhook]", 1)]


def test_sender_exception_does_not_skip_other_channels(conn, clock, monkeypatch):
    value = notifier(conn, webhook={"enabled": True, "url": "https://example.invalid/audit"})
    monkeypatch.setattr(notify, "_send_telegram", Mock(side_effect=RuntimeError("test failure")))
    webhook = Mock(return_value=True)
    monkeypatch.setattr(notify, "_send_webhook", webhook)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    assert webhook.call_count == 1
    assert value.pending[(SITE["id"], "unreachable")]["remaining"] == {"telegram"}


def test_recovery_cancels_failed_loss_and_new_loss_cancels_failed_recovery(conn, clock, monkeypatch):
    value = notifier(conn)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    tick(value, clock, 90, "ok")
    assert set(value.pending) == {(SITE["id"], "recovered")}
    tick(value, clock, 120, "unreachable")
    assert set(value.pending) == {(SITE["id"], "unreachable")}
    tick(value, clock, 180, "unreachable")
    messages = [call.args[1] for call in send.call_args_list]
    assert sum("场地恢复" in msg for msg in messages) == 1
    assert sum("场地失联" in msg for msg in messages) == 3


@pytest.mark.parametrize("next_state", ["pending", "stalled"])
def test_state_change_does_not_retry_previous_connectivity_notice(conn, clock, monkeypatch, next_state):
    value = notifier(conn)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    tick(value, clock, 120, next_state)
    assert (SITE["id"], "unreachable") not in value.pending
    assert sum("场地失联" in call.args[1] for call in send.call_args_list) == 1


def test_deleted_site_cancels_notices_and_forgets_its_event_state(conn, clock, monkeypatch):
    value = notifier(conn)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    value.active_drop[SITE["id"]] = 100
    value.active_offline[SITE["id"]] = True
    tick(value, clock, 120, "unreachable", sites=[])
    assert not value.pending and not value.prev and not value.active_drop and not value.active_offline
    assert send.call_count == 1


def test_disabled_channels_pause_without_forgetting_failed_delivery(conn, clock, monkeypatch):
    value = notifier(conn, webhook={"enabled": True, "url": "https://example.invalid/audit"})
    telegram, webhook = Mock(return_value=False), Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", telegram)
    monkeypatch.setattr(notify, "_send_webhook", webhook)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    value.cfg["webhook"]["enabled"] = False
    tick(value, clock, 90, "unreachable")
    assert value.pending[(SITE["id"], "unreachable")]["remaining"] == {"telegram", "webhook"}
    value.cfg["telegram"]["enabled"] = False
    tick(value, clock, 100, "unreachable")
    assert value.pending and not value.last_sent
    assert telegram.call_count == webhook.call_count == 1


def test_new_event_during_success_cooldown_is_delayed_and_not_lost(conn, clock, monkeypatch):
    value = notifier(conn)
    send = Mock(return_value=True)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    tick(value, clock, 120, "ok")
    tick(value, clock, 180, "unreachable")
    assert send.call_count == 2
    tick(value, clock, 1859, "unreachable")
    assert send.call_count == 2
    tick(value, clock, 1860, "unreachable")
    assert send.call_count == 3 and not value.pending


def test_unknown_counts_pause_retry_then_real_recovery_replaces_old_fault(conn, clock, monkeypatch):
    value = notifier(conn, offline_threshold=4)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    snap(conn, START)
    tick(value, clock, 0, "ok")
    snap(conn, START + 60, online=5)
    tick(value, clock, 60, "alert")
    assert send.call_count == 1 and value.active_offline
    snap(conn, START + 120, online=None, unknown=10)
    tick(value, clock, 120, "alert")
    assert send.call_count == 1 and value.active_offline
    snap(conn, START + 180, online=10)
    tick(value, clock, 180, "ok")
    assert send.call_count == 2 and not value.active_offline
    assert set(value.pending) == {(SITE["id"], "offline_recovered")}
    assert "掉线恢复" in send.call_args.args[1]


def response(status, payload=None, text=None):
    value = requests.Response()
    value.status_code = status
    value._content = (text if text is not None else json.dumps(payload)).encode()
    return value


@pytest.mark.parametrize("kind", ["wecom", "dingtalk"])
@pytest.mark.parametrize("payload,expected", [
    ({"errcode": 0, "errmsg": "ok"}, True),
    ({"errcode": 310000, "errmsg": "keyword not found"}, False),
    ({"errcode": False}, False), ({"errcode": "0"}, False),
    ({"errmsg": "missing receipt"}, False), ([], False),
])
def test_robot_requires_business_success_receipt(monkeypatch, kind, payload, expected):
    post = Mock(return_value=response(200, payload))
    monkeypatch.setattr(notify.requests, "post", post)
    cfg = {"kind": kind, "url": "https://example.invalid/audit", "allow_internal": True}
    assert notify._send_webhook(cfg, "audit") is expected
    assert post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status,body,expected", [(204, "", True), (200, "accepted", True),
                                                   (302, "", False), (500, "", False)])
def test_raw_webhook_uses_2xx_and_needs_no_json(monkeypatch, status, body, expected):
    monkeypatch.setattr(notify.requests, "post", Mock(return_value=response(status, text=body)))
    assert notify._send_webhook({"kind": "raw", "url": "https://example.invalid/audit",
                                 "allow_internal": True}, "audit") is expected


@pytest.mark.parametrize("status,payload,expected", [
    (200, {"ok": True}, True), (200, {"ok": False, "error_code": 429}, False),
    (200, {"ok": 1}, False), (200, {}, False), (302, {"ok": True}, False),
    (500, {"ok": True}, False),
])
def test_telegram_requires_json_ok_true(monkeypatch, status, payload, expected):
    monkeypatch.setattr(notify.requests, "post", Mock(return_value=response(status, payload)))
    assert notify._send_telegram({"bot_token": "audit-token", "chat_id": "audit-chat"}, "audit") is expected


def test_provider_error_is_failed_in_test_button_result_and_log(conn, clock, monkeypatch):
    value = notifier(conn, telegram={"enabled": False}, webhook={
        "enabled": True, "url": "https://example.invalid/audit", "allow_internal": True, "kind": "wecom"})
    monkeypatch.setattr(notify.requests, "post", Mock(return_value=response(200, {"errcode": 93000})))
    result = value.send_test("audit")
    assert result == {"telegram": None, "webhook": False, "email": None}
    assert tuple(conn.execute("SELECT type,ok FROM notify_log").fetchone()) == ("test", 0)
    assert not value.pending  # Manual tests never enter automatic alarm retries.


@pytest.mark.parametrize("kind", ["wecom", "dingtalk"])
def test_invalid_json_receipt_is_failed(monkeypatch, kind):
    monkeypatch.setattr(notify.requests, "post", Mock(return_value=response(200, text="not json")))
    assert not notify._send_webhook({"kind": kind, "url": "https://example.invalid/audit",
                                     "allow_internal": True}, "audit")


@pytest.mark.parametrize("pause", ["channel", "global"])
def test_reenabled_notifications_resume_only_current_undelivered_event(conn, clock, monkeypatch, pause):
    value = notifier(conn)
    send = Mock(side_effect=[False, True])
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    scope = value.cfg["telegram"] if pause == "channel" else value.cfg
    scope["enabled"] = False
    tick(value, clock, 120, "unreachable")
    tick(value, clock, 2400, "unreachable")
    assert send.call_count == 1
    scope["enabled"] = True
    tick(value, clock, 2460, "unreachable")
    assert send.call_count == 2 and not value.pending
    assert "补发通知" in send.call_args.args[1]
    assert "事件时间 2033-05-18 03:34:20 UTC" in send.call_args.args[1]


def test_recovery_while_channels_are_paused_cancels_old_failed_alert(conn, clock, monkeypatch):
    value = notifier(conn)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    value.cfg["telegram"]["enabled"] = False
    tick(value, clock, 120, "unreachable")
    tick(value, clock, 180, "ok")
    assert (SITE["id"], "unreachable") not in value.pending and send.call_count == 1
    value.cfg["telegram"]["enabled"] = True
    tick(value, clock, 2400, "ok")
    assert send.call_count == 2 and "场地恢复" in send.call_args.args[1]
    assert sum("场地失联" in call.args[1] for call in send.call_args_list) == 1


@pytest.mark.parametrize("pause", ["global", "channel"])
def test_pause_discards_superseded_recovery_before_resuming(conn, clock, monkeypatch, pause):
    value = notifier(conn)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    scope = value.cfg if pause == "global" else value.cfg["telegram"]
    scope["enabled"] = False
    tick(value, clock, 120, "ok")
    tick(value, clock, 180, "unreachable")
    assert send.call_count == 1
    scope["enabled"] = True
    tick(value, clock, 240, "unreachable")
    assert send.call_count == 2
    assert all("场地恢复" not in call.args[1] for call in send.call_args_list)


def test_failed_channel_resume_does_not_repeat_already_delivered_channel(conn, clock, monkeypatch):
    value = notifier(conn, webhook={"enabled": True, "url": "https://example.invalid/audit"})
    telegram, webhook = Mock(return_value=True), Mock(side_effect=[False, True])
    monkeypatch.setattr(notify, "_send_telegram", telegram)
    monkeypatch.setattr(notify, "_send_webhook", webhook)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    value.cfg["webhook"]["enabled"] = False
    tick(value, clock, 120, "unreachable")
    tick(value, clock, 2400, "unreachable")
    value.cfg["webhook"]["enabled"] = True
    tick(value, clock, 2460, "unreachable")
    assert telegram.call_count == 1 and webhook.call_count == 2
    assert not value.pending


@pytest.mark.parametrize("kind", ["offline", "hashrate"])
def test_rule_pause_resume_retains_original_undelivered_fault(conn, clock, monkeypatch, kind):
    value = notifier(conn, offline_threshold=4 if kind == "offline" else 0,
                     hashrate_drop_pct=30 if kind == "hashrate" else 0)
    send = Mock(side_effect=[False, True])
    monkeypatch.setattr(notify, "_send_telegram", send)
    current_hashrate = [100]
    monkeypatch.setattr(db, "avg_hashrate", lambda _conn, _sid, left, right, _age:
                        current_hashrate[0] if right - left == 600 else 100)
    snap(conn, START)
    tick(value, clock, 0, "ok")
    current_hashrate[0] = 50
    snap(conn, START + 60, rate=50, online=5)
    tick(value, clock, 60, "alert")
    key = "offline_threshold" if kind == "offline" else "hashrate_drop_pct"
    previous = value.cfg[key]
    value.cfg[key] = 0
    snap(conn, START + 120, rate=50, online=5)
    tick(value, clock, 120, "alert")
    snap(conn, START + 2400, rate=50, online=5)
    tick(value, clock, 2400, "alert")
    assert send.call_count == 1
    value.cfg[key] = previous
    snap(conn, START + 2460, rate=50, online=5)
    tick(value, clock, 2460, "alert")
    assert send.call_count == 2 and not value.pending


@pytest.mark.parametrize("kind", ["offline", "hashrate"])
def test_rule_pause_tracks_recovery_and_new_fault_without_sending_old_recovery(
        conn, clock, monkeypatch, kind):
    value = notifier(conn, offline_threshold=4 if kind == "offline" else 0,
                     hashrate_drop_pct=30 if kind == "hashrate" else 0)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    current_hashrate = [100]
    monkeypatch.setattr(db, "avg_hashrate", lambda _conn, _sid, left, right, _age:
                        current_hashrate[0] if right - left == 600 else 100)
    snap(conn, START)
    tick(value, clock, 0, "ok")
    current_hashrate[0] = 50
    snap(conn, START + 60, rate=50, online=5)
    tick(value, clock, 60, "alert")
    key = "offline_threshold" if kind == "offline" else "hashrate_drop_pct"
    previous = value.cfg[key]
    value.cfg[key] = 0
    current_hashrate[0] = 100
    snap(conn, START + 120)
    tick(value, clock, 120, "ok")
    assert send.call_count == 1
    current_hashrate[0] = 50
    snap(conn, START + 180, rate=50, online=5)
    tick(value, clock, 180, "alert")
    value.cfg[key] = previous
    snap(conn, START + 240, rate=50, online=5)
    tick(value, clock, 240, "alert")
    assert send.call_count == 2
    assert all("恢复" not in call.args[1] for call in send.call_args_list)


def test_site_deleted_while_all_channels_paused_still_clears_pending(conn, clock, monkeypatch):
    value = notifier(conn)
    send = Mock(return_value=False)
    monkeypatch.setattr(notify, "_send_telegram", send)
    tick(value, clock, 0, "ok")
    tick(value, clock, 60, "unreachable")
    value.cfg["telegram"]["enabled"] = False
    tick(value, clock, 120, "unreachable", sites=[])
    assert not value.pending and not value.prev
    value.cfg["telegram"]["enabled"] = True
    tick(value, clock, 180, "unreachable", sites=[])
    assert send.call_count == 1
