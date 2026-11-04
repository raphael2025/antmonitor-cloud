"""Upgrade removes incompatible statistics without resetting site configuration."""
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import db
import validate
from test_data_integrity import START, snapshot
from test_customer_hashrates import customer


def _pre_upgrade(path):
    conn = db.connect(path)
    # Seed the same application data as the previous release, before this migration.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
        conn.execute("DELETE FROM schema_migrations WHERE name='clean_legacy_statistics_v1'")
    db.upsert_site(conn, "a", "矿场 A", "hydro", START)
    db.rename_site(conn, "a", "运维自定义名称")
    conn.execute("INSERT INTO site_management(site_id,deleted_name,deleted_ts,notification_epoch) "
                 "VALUES('deleted','已删除矿场',?,7)", (START,))
    snapshot(conn, START, sid="a")
    snapshot(conn, START + 1, sid="legacy", measurement_time_supported=None,
             unknown=None, hashrate_complete=None, power_complete=None)
    conn.execute("INSERT INTO daily VALUES('a','2026-01-01',24,100,101,1,1,3,72)")
    with patch.object(db.time, "time", return_value=START + 60):
        db.rollup_daily(conn)
    report = validate.customers([customer(worker="modern")])
    db.replace_customers(conn, "a", report, 24)
    conn.execute("UPDATE site_customers SET legacy_power_kwh=999 WHERE worker='modern'")
    conn.execute("INSERT INTO site_customers(site_id,worker,machines,uptime_pct,delivered_th_h,"
                 "power_kwh,hours,updated_ts) VALUES('a','old-worker',3,100,7200,360,24,?)", (START,))
    db.replace_customer_hashrates(conn, "a", [{"worker": "modern", "machines": 1,
                                 "hashrate_ths": 293, "hashrate_ts": START}], START)
    db.replace_alert_summary(conn, "a", [{"category": "temp", "label": "高温", "count": 1}])
    db.notification_save(conn, "a", 0,
        {"state": "alert", "drop_baseline": 100, "offline_active": False,
         "drop_pct": 30, "offline_threshold": 0},
        {"hashrate_drop": {"msg": "待重试", "created": START, "due": START + 300,
                           "attempts": 2, "remaining": {"email"}, "delivered": {"webhook"}}},
        {"recovered": START - 100})
    db.audit_add(conn, "admin", "127.0.0.1", "settings", "配置保留")
    conn.commit()
    return conn


def _rows(conn, table):
    return [tuple(row) for row in conn.execute("SELECT * FROM " + table)]


def test_upgrade_cleans_statistics_and_preserves_configuration(tmp_path):
    path = tmp_path / "cloud.db"
    config = tmp_path / "config.yaml"
    settings = tmp_path / "cloud_settings.json"
    config.write_text("sites: []\nauth: {enabled: true}\n", encoding="utf-8")
    settings.write_text(json.dumps({"ingest": {"tokens": {"a": "binding-preserved"}}}), encoding="utf-8")
    file_contents = {p: p.read_bytes() for p in (config, settings)}
    old = _pre_upgrade(path)
    keep = ("sites", "site_management", "site_customer_current", "site_customer_current_state",
            "site_customer_report_state", "site_alert_summary", "notification_state",
            "notification_outbox", "notification_cooldown", "audit_log", "daily_observed_v2")
    preserved = {table: _rows(old, table) for table in keep}
    old.close()

    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 0
    assert [r[0] for r in conn.execute("SELECT site_id FROM snapshots")] == ["a"]
    assert {table: _rows(conn, table) for table in keep} == preserved
    rows = {r["worker"]: dict(r) for r in conn.execute("SELECT * FROM site_customers")}
    assert rows["old-worker"]["machines"] == 3  # identity/inventory is not a statistics reset
    for key in ("uptime_pct", "delivered_th_h", "power_kwh"):
        assert rows["old-worker"][key] is None
        assert rows["modern"]["legacy_" + key] is None
    assert rows["modern"]["power_kwh"] == 120
    assert rows["modern"]["avg_hashrate_ths"] == 293
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations "
                        "WHERE name='clean_legacy_statistics_v1'").fetchone()[0] == 1
    assert {p: p.read_bytes() for p in file_contents} == file_contents
    conn.close()

    backups = list(tmp_path.glob("cloud.db.before-statistics-cleanup-*.sqlite3"))
    assert len(backups) == 1
    if os.name != "nt":
        assert backups[0].stat().st_mode & 0o777 == 0o600
    backup = sqlite3.connect(backups[0])
    assert backup.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 1
    assert backup.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    backup.close()

    reopened = db.connect(path)
    assert {table: _rows(reopened, table) for table in keep} == preserved
    snapshot(reopened, START + 120, sid="a", rate=0, power=0)
    reopened.close()
    again = db.connect(path)
    assert again.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    assert len(list(tmp_path.glob("cloud.db.before-statistics-cleanup-*.sqlite3"))) == 1
    again.close()


def test_backup_failure_keeps_original_statistics_and_marker_unset(tmp_path, monkeypatch):
    path = tmp_path / "cloud.db"
    old = _pre_upgrade(path)
    original = {table: _rows(old, table) for table in ("snapshots", "daily", "site_customers")}
    old.close()

    def cannot_backup(*args):
        raise OSError("backup disk is full")

    monkeypatch.setattr(db, "_backup_before_statistics_cleanup", cannot_backup, raising=False)
    with pytest.raises(OSError, match="backup disk is full"):
        db.connect(path)
    raw = sqlite3.connect(path)
    assert {table: _rows(raw, table) for table in original} == original
    assert raw.execute("SELECT COUNT(*) FROM schema_migrations "
                       "WHERE name='clean_legacy_statistics_v1'").fetchone()[0] == 0
    raw.close()


def test_cleanup_transaction_rolls_back_all_statistics_on_failure(tmp_path):
    path = tmp_path / "cloud.db"
    old = _pre_upgrade(path)
    original = {table: _rows(old, table) for table in ("snapshots", "daily", "site_customers")}
    old.execute("CREATE TRIGGER simulate_failure BEFORE DELETE ON daily "
                "BEGIN SELECT RAISE(ABORT, 'storage failure'); END")
    old.commit()
    old.close()
    with pytest.raises(sqlite3.DatabaseError, match="storage failure"):
        db.connect(path)
    raw = sqlite3.connect(path)
    assert {table: _rows(raw, table) for table in original} == original
    assert raw.execute("SELECT COUNT(*) FROM schema_migrations "
                       "WHERE name='clean_legacy_statistics_v1'").fetchone()[0] == 0
    raw.close()


def test_clean_database_does_not_create_empty_backup(tmp_path):
    path = tmp_path / "cloud.db"
    for _ in range(2):
        conn = db.connect(path)
        conn.close()
    assert not list(tmp_path.glob("*.sqlite3"))


def test_upgrade_backup_does_not_block_git_updates(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_bytes((Path(db.__file__).parent / ".gitignore").read_bytes())
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    path = tmp_path / "cloud.db"
    old = _pre_upgrade(path)
    old.close()
    conn = db.connect(path)
    conn.close()
    assert list(tmp_path.glob("*.sqlite3"))
    assert subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=tmp_path) == b""


def test_later_legacy_customer_report_does_not_restore_cleaned_statistics():
    conn = db.connect(":memory:")
    rows = validate.customers([{"worker": "kept", "machines": 2, "delivered_th_h": 7200,
                               "power_kwh": 360, "uptime_pct": 100}])
    assert db.replace_customers(conn, "a", rows, 24)
    result = db.customers(conn)[0]
    assert result["worker"] == "kept" and result["machines"] == 2
    for key in ("delivered_th_h", "power_kwh", "uptime_pct"):
        assert result[key] is None
        assert result["legacy_" + key] is None
    conn.close()


def test_legacy_ledger_is_not_used_as_a_report_fallback():
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO daily VALUES('a','2026-01-01',24,100,101,1,1,3,72)")
    conn.commit()
    assert db.daily_report(conn, "a") == []
    snapshot(conn, START)
    with patch.object(db.time, "time", return_value=START + 60):
        db.rollup_daily(conn)
        row = db.daily_report(conn, "a")[0]
    assert row["power_kwh"] == 6
    assert not any(key.startswith("legacy_") for key in row)
    conn.close()


def test_old_snapshot_cannot_reintroduce_legacy_trend_line():
    conn = db.connect(":memory:")
    snapshot(conn, START, measurement_time_supported=None, unknown=None,
             hashrate_complete=None, power_complete=None)
    with patch.object(db.time, "time", return_value=START + 60):
        points = db.trend(conn, "a", hours=1)
    assert all(p["hashrate_ths"] is None and p.get("legacy_hashrate_ths") is None for p in points)
    conn.close()
