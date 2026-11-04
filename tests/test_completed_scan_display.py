"""A progressing scan keeps its previous completed display, never freshens evidence."""
import time
from unittest.mock import patch

import pytest

import db
import validate
from test_site_management import cloud, login  # noqa: F401
from test_data_integrity import START, snapshot


def completed_body(now, **overrides):
    body = {"site_id": "a", "site": "A", "type": "hydro", "ts": now,
            "scan_ts": now - 60, "scan_started_ts": now - 1500,
            "measurement_ts": now - 1400, "scan_age_s": 60,
            "scanning": True, "scan_progress_ts": now,
            "online": 538, "total": 538, "unknown": 0,
            "hashrate_ths": 161400, "power_kw": 3000,
            "hashrate_complete": True, "power_complete": True,
            "hashrate_known_machines": 538, "power_known_machines": 538,
            "active_alerts": 0, "containers": 4, "containers_unknown": 0,
            "containers_faulty": 0, "containers_offline": 0,
            "customer_hashrates": [{"worker": "customer", "machines": 538,
                                    "hashrate_ths": 161400, "hashrate_ts": now - 1400}]}
    body.update(overrides)
    return body


def ingest(client, body):
    response = client.post("/api/ingest/summary", json=body,
                           headers={"Authorization": "Bearer reporting-test-token"})
    assert response.status_code == 200, response.text
    return response.json()


def test_long_scan_keeps_completed_site_customer_and_account_values(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    login(client)
    body = completed_body(now)
    ingest(client, body)
    overview = client.get("/api/overview").json()
    site = overview["sites"][0]
    assert site["state"] == "ok" and site["scanning"] is True
    for key, expected in (("online", 538), ("total", 538), ("hashrate_ths", 161400), ("power_kw", 3000)):
        assert site["display_metrics"][key] == expected
        assert not site["metric_complete"][key]
    assert overview["display_totals"]["machines_online"] == 538
    assert overview["display_totals"]["hashrate_ths"] == 161400
    assert overview["display_totals"]["power_kw"] == 3000
    assert overview["totals"]["hashrate_ths"] is None
    result = client.get("/api/customers").json()
    for row in result["rows"] + result["by_worker"]:
        assert row["display_hashrate_ths"] == 161400
        assert row["display_scan_ts"] == now - 60
        assert row["hashrate_stale"] is True
    detail = client.get("/api/site/a").json()
    assert detail["customers"][0]["display_hashrate_ths"] == 161400
    assert any(point["display_hashrate_ths"] == 161400 for point in detail["trend"])
    assert all(point["hashrate_ths"] is None for point in detail["trend"])
    drawn = next(point for point in detail["trend"] if point["display_hashrate_ths"] is not None)
    assert drawn["display_scan_ts"] == now - 60
    chart = client.get("/api/trend?hours=1&points=60").json()["sites"][0]
    assert any(value == 161400 for _stamp, value in chart["display_points"])
    assert all(value is None for _stamp, value in chart["points"])
    stored = db.latest_ok_snapshots(server.CONN)["a"]
    assert stored["measurement_ts"] == now - 1400
    assert stored["scan_started_ts"] == now - 1500
    assert not db.snapshot_quality(stored, now)["hashrate_ths"]
    # Displaying a retained scan must not add energy or enable measurement alerts.
    assert db.avg_hashrate(server.CONN, "a", now - 3600, now) is None
    db.rollup_daily(server.CONN)
    assert db.daily_report(server.CONN, "a")[0]["power_kwh"] is None


def test_advancing_scan_retains_values_then_completed_zero_replaces_them(cloud, monkeypatch):
    server, client = cloud
    initial = int(time.time())
    clock = [initial]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    login(client)
    body = completed_body(initial)
    ingest(client, body)
    clock[0] += 1800
    # The application is alive and scanning, but its last completed scan and
    # underlying device measurement clocks must remain exactly unchanged.
    ingest(client, {**body, "ts": clock[0], "scan_age_s": 1860,
                    "scan_progress_ts": clock[0]})
    site = client.get("/api/overview").json()["sites"][0]
    assert site["state"] == "ok" and site["scanning"] is True
    assert site["display_metrics"]["online"] == 538
    assert site["display_metrics"]["hashrate_ths"] == 161400
    assert site["display_scan_ts"] == body["scan_ts"]
    assert client.get("/api/customers").json()["by_worker"][0]["display_hashrate_ths"] == 161400
    now = clock[0] + 1
    clock[0] = now
    ingest(client, completed_body(now, scan_ts=now, scan_started_ts=initial,
        measurement_ts=now, scan_age_s=0, scanning=False, scan_progress_ts=None,
        online=0, hashrate_ths=0, power_kw=0,
        customer_hashrates=[{"worker": "customer", "machines": 538, "hashrate_ths": 0, "hashrate_ts": now}]))
    overview = client.get("/api/overview").json()
    assert overview["sites"][0]["scanning"] is False
    assert overview["display_totals"]["machines_online"] == 0
    assert overview["display_totals"]["hashrate_ths"] == 0
    assert overview["display_totals"]["power_kw"] == 0
    assert client.get("/api/customers").json()["by_worker"][0]["display_hashrate_ths"] == 0


@pytest.mark.parametrize("heartbeat", [None, -1, True, "bad", "future", "expired"])
def test_bad_or_stopped_scan_heartbeat_cannot_hide_a_stall(cloud, monkeypatch, heartbeat):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    login(client)
    value = now + 1 if heartbeat == "future" else now - 301 if heartbeat == "expired" else heartbeat
    ingest(client, completed_body(now, scan_ts=now - 1800, scan_age_s=1800,
        scan_started_ts=now - 3600, measurement_ts=now - 3500, scan_progress_ts=value))
    site = client.get("/api/overview").json()["sites"][0]
    assert site["state"] == "stalled"
    assert site["scanning"] is False
    assert site["display_metrics"]["hashrate_ths"] is None
    assert client.get("/api/customers").json()["by_worker"][0]["display_hashrate_ths"] is None


def test_unreachable_site_does_not_keep_account_sum_current(cloud, monkeypatch):
    server, client = cloud
    clock = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    login(client)
    ingest(client, completed_body(clock[0]))
    clock[0] += 181  # Progress is <300 s old, but no new site report has arrived.
    overview = client.get("/api/overview").json()
    assert overview["sites"][0]["state"] == "unreachable"
    assert overview["display_totals"]["hashrate_ths"] is None
    assert overview["sites"][0]["display_metrics"]["hashrate_ths"] is None
    assert client.get("/api/customers").json()["by_worker"][0]["display_hashrate_ths"] is None


def test_pull_mode_transports_completed_scan_and_progress_clocks(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    login(client)
    site = {"id": "a", "name": "A", "url": "https://invalid.test", "token": "test"}
    server.CFG["sites"] = [site]
    reply = {**completed_body(now, scan_ts=now - 1800, scan_age_s=1800,
                 scan_started_ts=now - 3600, measurement_ts=now - 3500),
             "ok": True, "scanned": True, "total_hashrate_ths": 161400, "total_power_kw": 3000}
    monkeypatch.setattr(server.COLLECTOR, "_get", lambda *_args, **_kwargs: reply)
    server.COLLECTOR._poll_site(site)
    stored = db.latest_ok_snapshots(server.CONN)["a"]
    assert stored["scan_progress_ts"] == now
    assert stored["scan_started_ts"] == now - 3600
    assert stored["measurement_ts"] == now - 3500
    overview = client.get("/api/overview").json()
    assert overview["sites"][0]["state"] == "ok"
    assert overview["display_totals"]["hashrate_ths"] == 161400
    assert overview["totals"]["hashrate_ths"] is None


def test_delayed_progress_is_not_refreshed_by_receipt_time():
    src = {"ts": 2000000000, "scan_ts": 1999999940, "scan_started_ts": 1999998500,
           "scanning": True, "scan_progress_ts": 1999999990}
    normalized = validate.scan_progress_fields(src, 2000000600, 1999999940)
    assert normalized["scan_progress_ts"] == 1999999990
    assert not db.scan_in_progress(normalized, 2000000600)
    assert normalized["scan_started_ts"] == 1999998500
    assert validate.scan_progress_fields({**src, "scanning": "true"}, 2000000000, 1999999940)["scan_progress_ts"] is None


def test_invalid_measurement_time_is_not_repaired_for_display(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    login(client)
    ingest(client, completed_body(now, measurement_ts=None,
        customer_hashrates=[{"worker": "customer", "machines": 538,
                             "hashrate_ths": 161400, "hashrate_ts": None}]))
    overview = client.get("/api/overview").json()
    assert overview["display_totals"]["hashrate_ths"] is None
    assert client.get("/api/customers").json()["by_worker"][0]["display_hashrate_ths"] is None


def test_display_trend_uses_last_completed_value_without_filling_outages():
    conn = db.connect(":memory:")
    snapshot(conn, START, rate=100, scan_ts=START - 60, scan_age_s=60,
             measurement_ts=START - 1700, scanning=True, scan_progress_ts=START)
    snapshot(conn, START + 30, rate=200, scan_ts=START + 20, scan_age_s=10,
             measurement_ts=START - 1600, scanning=True, scan_progress_ts=START + 30)
    snapshot(conn, START + 300, rate=0, power=0, online=0)
    snapshot(conn, START + 600, ok=0, rate=None, power=None)
    snapshot(conn, START + 1560, rate=999, scan_ts=START + 300, scan_age_s=1260,
             measurement_ts=START + 300, scanning=False)
    snapshot(conn, START + 2100, rate=300)
    with patch.object(db.time, "time", return_value=START + 2400):
        rows = {p["ts"]: p for p in db.trend(conn, "a", hours=1, points=24)}
    # points=24 => 150-second buckets; the latest completed scan wins within one bucket.
    assert rows[START]["display_hashrate_ths"] == 200
    assert rows[START]["display_scan_ts"] == START + 20
    assert rows[START]["hashrate_ths"] is None
    assert rows[START + 300]["display_hashrate_ths"] == 0  # confirmed offline is actual zero
    for stamp in (START + 600, START + 900, START + 1500, START + 2400):
        assert rows[stamp]["display_hashrate_ths"] is None
    assert rows[START + 2100]["display_hashrate_ths"] == 300
    assert conn.execute("SELECT measurement_ts FROM snapshots ORDER BY id LIMIT 1").fetchone()[0] == START - 1700
    conn.close()
