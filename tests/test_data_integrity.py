"""Numerical audit: observation time, missing measurements and whole-site totals."""
import time
from unittest.mock import patch

import pytest

import db
import validate
from collector import Collector
from test_site_management import cloud, login  # noqa: F401

START = (2000000000 + 8 * 3600) // 86400 * 86400 - 8 * 3600


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def snapshot(conn, ts, rate=100, power=360, sid="a", **extra):
    row = dict(ts=ts, site_id=sid, ok=1, online=1, total=1, hashrate_ths=rate,
               power_kw=power, unknown=0, hashrate_complete=True, power_complete=True,
               scan_ts=ts, scan_age_s=0, measurement_ts=ts, measurement_time_supported=True,
               active_alerts=0, containers=0, containers_unknown=0, containers_faulty=0, containers_offline=0)
    row.update(extra)
    return db.insert_snapshot(conn, row)


def rollup(conn, now, offsets=(0, 1)):
    with patch.object(db.time, "time", return_value=now):
        db.rollup_daily(conn, offsets)
        return db.daily_report(conn, "a")


def test_daily_and_alert_mean_weight_actual_seconds(conn):
    snapshot(conn, START, rate=100, power=100)
    snapshot(conn, START + 60, rate=300, power=300)
    snapshot(conn, START + 360, rate=0, power=0)
    day = rollup(conn, START + 360)[0]
    assert day["avg_hashrate_ths"] == pytest.approx(266.6666667)
    assert day["avg_power_kw"] == pytest.approx(266.6666667)
    assert day["power_kwh"] == pytest.approx(26.6666667)
    assert day["observed_hours"] == pytest.approx(.1)
    assert day["coverage_pct"] == 100
    with patch.object(db.time, "time", return_value=START + 360):
        assert db.avg_hashrate(conn, "a", START, START + 360) == pytest.approx(266.6666667)


def test_first_measurement_does_not_bill_future_minute(conn):
    snapshot(conn, START + 10)
    day = rollup(conn, START + 10)[0]
    assert day["power_kwh"] is None
    assert day["avg_hashrate_ths"] is None
    assert day["power_observed_hours"] == 0
    assert rollup(conn, START + 15)[0]["power_kwh"] == pytest.approx(.5)


def test_midnight_splits_interval_and_does_not_borrow_next_days_peak(conn):
    snapshot(conn, START - 10, rate=100)
    snapshot(conn, START, rate=900)
    snapshot(conn, START + 10, rate=900)
    today, yesterday = rollup(conn, START + 10)
    assert today["power_kwh"] == yesterday["power_kwh"] == 1
    assert yesterday["max_hashrate_ths"] == 100
    assert today["max_hashrate_ths"] == 900
    assert today["avg_hashrate_ths"] == 900
    assert yesterday["avg_hashrate_ths"] == 100


def test_failed_poll_ends_the_previous_observation(conn):
    snapshot(conn, START)
    snapshot(conn, START + 60, ok=0, rate=None, power=None)
    snapshot(conn, START + 300, rate=0, power=0)
    day = rollup(conn, START + 300)[0]
    assert day["power_kwh"] == 6
    assert day["power_observed_hours"] == pytest.approx(1 / 60)
    assert day["metric_coverage_pct"]["power_kw"] == pytest.approx(20)


def test_repeated_stalled_scan_never_becomes_fresh_energy(conn):
    for dt in range(0, 3601, 60):
        snapshot(conn, START + dt, scan_ts=START - 3600, scan_age_s=3600 + dt)
    day = rollup(conn, START + 3600)[0]
    assert day["power_kwh"] is None
    assert day["avg_hashrate_ths"] is None
    assert day["coverage_pct"] == 0


def test_gap_cap_and_scan_expiry_bound_coverage(conn):
    snapshot(conn, START)
    day = rollup(conn, START + 600)[0]
    assert day["power_kwh"] == 30
    assert day["coverage_pct"] == 50
    conn.execute("DELETE FROM snapshots")
    conn.commit()
    for dt in range(0, 1801, 60):
        snapshot(conn, START + dt, scan_ts=START, scan_age_s=dt, measurement_ts=START)
    day = rollup(conn, START + 1800)[0]
    assert day["power_kwh"] == 90
    assert day["coverage_pct"] == pytest.approx(100 * 900 / 1800)


def test_metrics_have_independent_coverage_and_true_zero(conn):
    snapshot(conn, START, power=None, power_complete=False)
    day = rollup(conn, START + 60)[0]
    assert day["avg_hashrate_ths"] == 100
    assert day["power_kwh"] is None
    assert day["metric_coverage_pct"]["hashrate_ths"] == 100
    assert day["metric_coverage_pct"]["power_kw"] == 0
    snapshot(conn, START + 60, rate=0, power=0)
    day = rollup(conn, START + 120)[0]
    assert day["power_kwh"] == 0
    assert day["metric_coverage_pct"]["power_kw"] == 50


def test_duplicate_receipt_timestamp_does_not_double_weight(conn):
    snapshot(conn, START, rate=100, power=100)
    snapshot(conn, START, rate=300, power=300)
    day = rollup(conn, START + 60)[0]
    assert day["avg_hashrate_ths"] == 300
    assert day["power_kwh"] == 5


def test_legacy_daily_ledger_is_preserved_and_never_claims_coverage(conn):
    day = time.strftime("%Y-%m-%d", time.gmtime(START + 8 * 3600))
    old = ("a", day, 10, 222, 333, 1, 2, 20, 999)
    conn.execute("INSERT INTO daily VALUES(?,?,?,?,?,?,?,?,?)", old)
    conn.commit()
    snapshot(conn, START, unknown=None, hashrate_complete=None, power_complete=None)
    report = rollup(conn, START + 60)[0]
    assert report["method"] == "legacy_sample_mean"
    assert report["coverage_pct"] is None
    assert report["legacy_power_kwh"] == 999
    assert tuple(conn.execute("SELECT * FROM daily").fetchone()) == old
    snapshot(conn, START + 60)
    report = rollup(conn, START + 120)[0]
    assert report["method"] == "time_weighted_v2"
    assert report["power_kwh"] == 6
    assert report["legacy_power_kwh"] == 999
    assert tuple(conn.execute("SELECT * FROM daily").fetchone()) == old


def test_trend_keeps_legacy_values_separate_and_unknown_is_not_zero(conn):
    snapshot(conn, START, unknown=None, hashrate_complete=None, power_complete=None)
    snapshot(conn, START + 60, rate=None, power=None, hashrate_complete=False, power_complete=False)
    with patch.object(db.time, "time", return_value=START + 120):
        points = db.trend(conn, "a", hours=1, points=60)
    old = next(p for p in points if p["ts"] == START)
    unknown = next(p for p in points if p["ts"] == START + 60)
    assert old["hashrate_ths"] is None
    assert old["legacy_hashrate_ths"] == 100
    assert unknown["hashrate_ths"] is None
    assert unknown["legacy_hashrate_ths"] is None


@pytest.mark.parametrize("bad", [None, True, False, -1, -.5, float("nan"), float("inf"), "bad"])
def test_snapshot_unknown_measurement_is_never_zero(bad):
    row = validate.snapshot_fields({"hashrate_ths": bad, "power_kw": bad, "online": bad})
    assert row["hashrate_ths"] is None
    assert row["power_kw"] is None
    assert row["online"] is None


def send_summary(client, sid="a", age=0, rate=300, **extra):
    stamp = int(time.time()) - age
    body = dict(site_id=sid, site=sid, ts=int(time.time()), online=1, total=1, hashrate_ths=rate, power_kw=5,
                scan_age_s=age, scan_ts=stamp, measurement_ts=stamp,
                unknown=0, hashrate_complete=True, power_complete=True,
                active_alerts=0, containers=0, containers_unknown=0, containers_faulty=0, containers_offline=0,
                customer_hashrates=[{"worker": "same-worker", "machines": 1, "hashrate_ths": rate}])
    body.update(extra)
    return client.post("/api/ingest/summary", headers={"Authorization": "Bearer reporting-test-token"}, json=body)


def test_delayed_summary_cannot_rollback_site_but_leave_customer_newer(cloud):
    server, client = cloud
    assert send_summary(client).status_code == 200
    response = send_summary(client, rate=100, age=300, site="old-name")
    assert response.status_code == 200
    assert response.json()["stored"] is False
    site = server._overview_payload()["sites"][0]
    assert site["hashrate_ths"] == 300
    assert site["name"] == "a"
    assert server._customers_payload()["rows"][0]["hashrate_ths"] == 300
    assert server.CONN.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_total_is_unknown_when_site_is_lost_but_observed_subtotal_survives(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(server.time, "time", lambda: now)
    send_summary(client, "a", rate=300)
    monkeypatch.setattr(server.time, "time", lambda: now + 3600)
    send_summary(client, "b", rate=200)
    o = server._overview_payload()
    assert o["totals"]["hashrate_ths"] is None
    assert o["observed_totals"]["hashrate_ths"] == 200
    assert o["metric_known_sites"]["hashrate_ths"] == 1
    assert o["totals"]["machines_online"] is None
    assert o["totals"]["machines_total"] is None
    assert o["observed_totals"]["machines_online"] == o["observed_totals"]["machines_total"] == 1
    assert o["sites"][0]["scan_age_s"] == 3600
    assert o["totals"]["customers"] == 1
    assert o["totals"]["customer_site_pairs"] == 2


def test_unknown_machine_count_invalidates_both_online_fraction_terms(cloud):
    server, client = cloud
    send_summary(client, unknown=1, total=2)
    o = server._overview_payload()
    assert o["totals"]["machines_online"] is o["totals"]["machines_total"] is None
    assert o["metric_known_sites"]["machines_online"] == o["metric_known_sites"]["machines_total"] == 0
    assert o["observed_totals"]["machines_online"] is None


def test_fresh_explicit_zero_remains_known_and_old_protocol_is_unverified(cloud):
    server, client = cloud
    send_summary(client, rate=0, online=0, total=0, power_kw=0, customer_hashrates=[])
    assert server._overview_payload()["totals"]["hashrate_ths"] == 0
    send_summary(client, "old", hashrate_complete=None, power_complete=None, unknown=None)
    o = server._overview_payload()
    assert o["totals"]["hashrate_ths"] is None
    assert o["observed_totals"]["hashrate_ths"] == 0
    assert o["sites"][1]["data_quality"] == "legacy_unverified"


def test_pull_before_first_scan_is_pending_and_old_success_after_restart_is_not_current(cloud, monkeypatch):
    server, _ = cloud
    site = {"id": "pull", "name": "pull", "url": "http://fixture.invalid"}
    server.CFG["sites"] = [site]
    monkeypatch.setattr(server.COLLECTOR, "_get", lambda *a, **kw: {"ok": True, "scanned": False})
    server.COLLECTOR._poll_site(site)
    entry = server._overview_payload()["sites"][0]
    assert entry["state"] == "pending"
    assert entry["hashrate_ths"] is None
    restarted = Collector({"sites": [site]}, server.CONN)
    assert restarted.site_state("pull", {"ts": int(time.time()) - 86400, "scan_age_s": 10}, mode="pull") == "unreachable"


def billing_row(power=12, coverage=24, source="observed"):
    end = int(time.time()) // 600 * 600
    return dict(worker="same-worker", machines=1, billing_source=source, billing_hours=24,
                delivered_th_h=7032, power_kwh=power, uptime_pct=100,
                delivery_observed_hours=coverage, power_observed_hours=coverage, uptime_observed_hours=coverage,
                billing_period_start_ts=end - 86400, billing_period_end_ts=end)


def send_customers(client, sid, rows, **extra):
    return client.post("/api/ingest/customers", headers={"Authorization": "Bearer reporting-test-token"},
                       json={"site_id": sid, "site": sid, "hours": 24, "customers": rows, **extra})


def test_billing_period_coverage_and_zero_survive_protocol(cloud):
    server, client = cloud
    assert send_customers(client, "a", [billing_row(power=0, coverage=6)]).status_code == 200
    row = server._customers_payload()["rows"][0]
    assert row["power_kwh"] == 0
    assert row["power_coverage_pct"] == 25
    assert row["delivery_coverage_pct"] == 25
    assert row["billing_source"] == "observed"
    send_customers(client, "b", [billing_row(power=12)])
    total = server._customers_payload()["by_worker"][0]
    assert total["power_kwh"] is None
    assert total["observed_power_kwh"] == 12
    assert total["power_unavailable_reason"] == "partial_coverage"


def test_legacy_energy_stays_legacy_and_missing_never_turns_into_zero(cloud):
    server, client = cloud
    send_customers(client, "a", [{"worker": "same-worker", "power_kwh": 12, "delivered_th_h": 7032}])
    row = server._customers_payload()["rows"][0]
    assert row["power_kwh"] is None
    assert row["legacy_power_kwh"] == 12
    assert row["legacy_delivered_th_h"] == 7032
    assert row["billing_source"] == "legacy_only"
    total = server._customers_payload()["by_worker"][0]
    assert total["power_kwh"] is None
    assert total["legacy_power_kwh"] == 12


@pytest.mark.parametrize("invalid", [None, False, [None], [{"worker": "same"}, {"worker": "same"}]])
def test_malformed_full_customer_report_does_not_erase_previous_data(cloud, invalid):
    server, client = cloud
    assert send_customers(client, "a", [billing_row()]).status_code == 200
    assert send_customers(client, "a", invalid).status_code == 400
    assert len(db.customers(server.CONN)) == 1


def test_impossible_average_coverage_is_not_accepted_as_full():
    row = validate.customers([{"hashrate_hours": 24, "observed_hours": 48, "coverage_pct": 100, "avg_hashrate_ths": 293}])[0]
    assert row["avg_hashrate_ths"] is None
    assert row["observed_hours"] is None


def test_customer_measurement_age_is_not_replaced_with_whole_scan_time():
    rows, scan_ts = validate.customer_snapshot({"scan_ts": START + 60, "ts": START + 120, "scan_age_s": 60,
        "customer_hashrates": [
            {"worker": "slow", "machines": 1, "hashrate_ths": 293, "hashrate_ts": START - 240},
            {"worker": "unknown", "machines": 1, "hashrate_ths": None, "hashrate_ts": None},
            {"worker": "old-protocol", "machines": 1, "hashrate_ths": 293},
            {"worker": "future", "machines": 1, "hashrate_ths": 293, "hashrate_ts": START + 7200}]}, START + 120)
    assert scan_ts == START + 60
    assert [r["hashrate_ts"] for r in rows] == [START - 240, None, START + 60, None]


def test_customer_explicit_unknown_time_is_not_replaced_in_storage(conn):
    db.replace_customer_hashrates(conn, "a", [{"worker": "c", "machines": 1,
        "hashrate_ths": None, "hashrate_ts": None}], START)
    assert db.customers(conn)[0]["hashrate_ts"] is None


def test_partial_known_machine_count_cannot_claim_complete_summary():
    row = validate.snapshot_fields({"online": 2, "total": 2, "unknown": 0,
        "hashrate_ths": 293, "hashrate_complete": True, "hashrate_known_machines": 1,
        "power_kw": 6, "power_complete": True, "power_known_machines": 2})
    assert row["hashrate_complete"] is False
    assert row["power_complete"] is True
    assert validate.measurement(1.5, int) is None


def aligned_report():
    from test_customer_hashrates import customer
    return customer(worker="same-worker")


@pytest.mark.parametrize("prefix,alignment,reason", [
    ("period", "unaligned", "unaligned_windows"),
    ("period", "unverified", "unverified_windows"),
    ("billing_period", "unaligned", "unaligned_windows"),
    ("billing_period", "unverified", "unverified_windows")])
def test_multisite_windows_need_real_matching_endpoints(cloud, prefix, alignment, reason):
    server, client = cloud
    first, second = aligned_report(), aligned_report()
    for part in ("start_ts", "end_ts"):
        key = prefix + "_" + part
        if alignment == "unaligned":
            second[key] -= 600
        else:
            second.pop(key)
    assert send_customers(client, "a", [first]).status_code == 200
    assert send_customers(client, "b", [second]).status_code == 200
    total = server._customers_payload()["by_worker"][0]
    if prefix == "period":
        assert total["window_alignment"] == alignment
        assert total["avg_hashrate_ths"] is None
        assert total["avg_unavailable_reason"] == reason
        assert total["reported_avg_hashrate_ths"] == 586
        assert total["power_kwh"] == 240
    else:
        assert total["billing_window_alignment"] == alignment
        assert total["power_kwh"] is None
        assert total["power_unavailable_reason"] == reason
        assert total["observed_power_kwh"] == 240
        assert total["avg_hashrate_ths"] == 586


def test_newly_received_old_window_is_stale_not_current_complete_total(cloud):
    server, client = cloud
    row = aligned_report()
    for key in validate.PERIOD_FIELDS:
        row[key] -= 7200
    send_customers(client, "a", [row])
    send_customers(client, "b", [row])
    result = server._customers_payload()
    assert all(r["report_stale"] and r["report_age_s"] < 10 for r in result["rows"])
    total = result["by_worker"][0]
    assert total["avg_hashrate_ths"] is None
    assert total["avg_unavailable_reason"] == "stale_reports"
    assert total["power_kwh"] is None
    assert total["power_unavailable_reason"] == "stale_reports"


@pytest.mark.parametrize("unknown_time", [False, True])
def test_multisite_realtime_never_sums_old_or_unverified_readings(cloud, monkeypatch, unknown_time):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    old = aligned_report()
    old["hashrate_ts"] = None if unknown_time else now - 3600
    fresh = aligned_report()
    fresh["hashrate_ths"] = 0
    send_customers(client, "a", [old])
    send_customers(client, "b", [fresh])
    result = server._customers_payload()
    assert result["rows"][0]["hashrate_ths"] == 293  # last-known value remains reviewable
    total = result["by_worker"][0]
    assert total["hashrate_ths"] is None
    assert total["observed_hashrate_ths"] == 0  # an observed zero is still known
    assert total["hashrate_known_sites"] == 1
    assert total["hashrate_complete"] is False
    assert total["hashrate_unavailable_reason"] == ("unverified_time" if unknown_time else "stale_sites")


def test_all_stale_customers_have_no_observed_realtime_subtotal(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    row = aligned_report()
    row["hashrate_ts"] = now - 3600
    send_customers(client, "a", [row])
    send_customers(client, "b", [row])
    total = server._customers_payload()["by_worker"][0]
    assert total["hashrate_ths"] is None
    assert total["observed_hashrate_ths"] is None
    assert total["hashrate_known_sites"] == 0


def test_trend_explicitly_breaks_the_line_across_no_report_gap(conn):
    snapshot(conn, START)
    snapshot(conn, START + 3600)
    with patch.object(db.time, "time", return_value=START + 3660):
        points = db.trend(conn, "a", hours=2, points=120)
    by_time = {p["ts"]: p for p in points}
    assert by_time[START + 240]["hashrate_ths"] == 100
    assert by_time[START + 300]["hashrate_ths"] is None
    assert by_time[START + 1800]["hashrate_ths"] is None
    assert by_time[START + 3600]["hashrate_ths"] == 100


def test_daily_coverage_denominator_does_not_freeze_when_monitor_stops(conn):
    snapshot(conn, START)
    day = rollup(conn, START + 60, offsets=(0,))[0]
    assert day["coverage_pct"] == 100
    with patch.object(db.time, "time", return_value=START + 2 * 86400):
        stopped = db.daily_report(conn, "a")[0]
    assert stopped["elapsed_hours"] == 24
    assert stopped["power_kwh"] == 6
    assert stopped["observed_hours"] == pytest.approx(1 / 60)
    assert stopped["coverage_pct"] == pytest.approx(100 / 1440)


def test_new_receipt_and_scan_do_not_extend_old_measurements_lifetime(conn):
    snapshot(conn, START, measurement_ts=START - 899)
    day = rollup(conn, START + 60)[0]
    assert day["power_kwh"] == pytest.approx(.1)  # 360 kW for one valid second
    assert day["power_observed_hours"] == pytest.approx(1 / 3600)
    snapshot(conn, START + 60, measurement_ts=START - 899)
    assert rollup(conn, START + 120)[0]["power_kwh"] == pytest.approx(.1)


def test_measurement_time_clock_correction_preserves_age_difference():
    source = {"scan_ts": START + 3600, "measurement_ts": START + 3300}
    assert validate.measurement_timestamp(source, START) == START - 300
    source["measurement_ts"] = START + 7200
    assert validate.measurement_timestamp(source, START) is None


def test_old_time_protocol_stays_legacy_but_explicit_unknown_is_not_legacy(conn):
    snapshot(conn, START, measurement_ts=None, measurement_time_supported=False)
    snapshot(conn, START + 60, measurement_ts=None, measurement_time_supported=True)
    with patch.object(db.time, "time", return_value=START + 120):
        points = {p["ts"]: p for p in db.trend(conn, "a", hours=1, points=60)}
    assert points[START]["hashrate_ths"] is None
    assert points[START]["legacy_hashrate_ths"] == 100
    assert points[START + 60]["hashrate_ths"] is None
    assert points[START + 60]["legacy_hashrate_ths"] is None


def test_unknown_container_is_not_certified_healthy_or_offline(cloud):
    server, client = cloud
    send_summary(client, containers=2, containers_unknown=1, containers_faulty=0, containers_offline=0)
    overview = server._overview_payload()
    assert overview["totals"]["containers"] == 2
    assert overview["totals"]["containers_unknown"] == 1
    assert overview["totals"]["containers_faulty"] is None
    assert overview["totals"]["containers_offline"] is None
    send_summary(client, "old", containers=2, containers_unknown=None)
    assert server._overview_payload()["totals"]["containers_unknown"] is None


def test_notify_unknown_counts_neither_trigger_offline_nor_claim_recovered(conn, monkeypatch):
    from notify import Notifier
    notifier = Notifier({"alerts": {"offline_threshold": 1}}, conn)
    sent = []
    monkeypatch.setattr(notifier, "_send", lambda *args, **kwargs: sent.append(args))
    site = {"name": "test", "type": "air"}
    snapshot(conn, START, online=None, total=100, unknown=100)
    notifier._check_offline("a", site, db.latest_ok_snapshots(conn)["a"], START)
    assert sent == []
    snapshot(conn, START + 1, online=0, total=100)
    notifier._check_offline("a", site, db.latest_ok_snapshots(conn)["a"], START + 1)
    assert sent[-1][1] == "offline_count"
    snapshot(conn, START + 2, online=None, total=None, unknown=None)
    notifier._check_offline("a", site, db.latest_ok_snapshots(conn)["a"], START + 2)
    assert len(sent) == 1
    snapshot(conn, START + 3, online=100, total=100)
    notifier._check_offline("a", site, db.latest_ok_snapshots(conn)["a"], START + 3)
    assert sent[-1][1] == "offline_recovered"


def test_notify_stalled_age_includes_time_since_receipt(conn, monkeypatch):
    from notify import Notifier
    notifier = Notifier({}, conn)
    sent = []
    monkeypatch.setattr(notifier, "_send", lambda *args, **kwargs: sent.append(args))
    notifier._on_transition("a", {"name": "test", "type": "air"}, "ok", "stalled",
        {"ts": START, "scan_ts": START - 120, "scan_age_s": 120}, START + 1800)
    assert "32 分钟" in sent[0][2]


def test_customer_machine_count_source_survives_protocol(cloud):
    server, client = cloud
    row = aligned_report()
    row["machines_source"] = "observed"
    send_customers(client, "a", [row])
    result = server._customers_payload()
    assert result["rows"][0]["machines_source"] == "observed"
    assert result["by_worker"][0]["machines_source"] == "observed"
    row.pop("machines_source")
    send_customers(client, "b", [row])
    assert server._customers_payload()["by_worker"][0]["machines_source"] == "legacy"


def test_delayed_first_packet_age_zero_is_still_an_hour_old(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    sent = now - 3600
    response = send_summary(client, ts=sent, scan_ts=sent, measurement_ts=sent, age=0)
    assert response.status_code == 200
    snap = db.latest_ok_snapshots(server.CONN)["a"]
    assert snap["source_sent_ts"] == snap["source_scan_ts"] == sent
    assert snap["scan_ts"] == snap["measurement_ts"] == sent
    assert snap["scan_age_s"] == 3600
    overview = server._overview_payload()
    assert overview["totals"]["hashrate_ths"] is None
    assert overview["observed_totals"]["hashrate_ths"] is None
    assert overview["sites"][0]["state"] == "stalled"
    with patch.object(db.time, "time", return_value=now + 60):
        db.rollup_daily(server.CONN)
        assert db.daily_report(server.CONN, "a")[0]["power_kwh"] is None


def test_delayed_age_zero_packet_cannot_replace_newer_source_scan(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    assert send_summary(client, rate=300).status_code == 200
    monkeypatch.setattr(time, "time", lambda: now + 60)
    response = send_summary(client, rate=100, ts=now - 3600, scan_ts=now - 3600,
                            measurement_ts=now - 3600, age=0)
    assert response.json()["stored"] is False
    assert db.latest_ok_snapshots(server.CONN)["a"]["hashrate_ths"] == 300
    assert server._customers_payload()["rows"][0]["hashrate_ths"] == 300


def test_future_source_clock_never_becomes_a_fresh_zero_age_measurement(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    response = send_summary(client, ts=now + 3600, scan_ts=now + 3600,
                            measurement_ts=now + 3600, age=0)
    assert response.status_code == 200
    snap = db.latest_ok_snapshots(server.CONN)["a"]
    assert snap["scan_ts"] is snap["measurement_ts"] is None
    assert server._overview_payload()["totals"]["hashrate_ths"] is None


def test_future_historical_window_never_claims_complete_single_site_report(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    row = aligned_report()
    for key in validate.PERIOD_FIELDS:
        row[key] += 3600
    send_customers(client, "a", [row])
    result = server._customers_payload()
    for r in (result["rows"][0], result["by_worker"][0]):
        assert r["avg_hashrate_ths"] is None
        assert r["power_kwh"] is None
        assert r["avg_unavailable_reason"] == "unverified_windows"
        assert r["power_unavailable_reason"] == "unverified_windows"


def test_delayed_customer_report_does_not_replace_newer_window_or_name(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    current = aligned_report()
    assert send_customers(client, "a", [current]).json()["stored"] is True
    old = {**current, "avg_hashrate_ths": 100}
    for key in validate.PERIOD_FIELDS:
        old[key] -= 600
    response = send_customers(client, "a", [old], site="old-name")
    assert response.json()["stored"] is False
    result = server._customers_payload()
    assert result["rows"][0]["avg_hashrate_ths"] == 293
    assert result["rows"][0]["site_name"] == "a"
    state = server.CONN.execute("SELECT * FROM site_customer_report_state WHERE site_id='a'").fetchone()
    assert state["period_end_ts"] == current["period_end_ts"]


def test_empty_report_needs_real_new_window_and_keeps_watermark_after_restart(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    current = aligned_report()
    assert send_customers(client, "a", [current]).json()["stored"] is True
    assert send_customers(client, "a", []).json()["stored"] is False
    old_window = {key: current[key] - 600 for key in validate.PERIOD_FIELDS}
    assert send_customers(client, "a", [], **old_window).json()["stored"] is False
    assert len(db.customers(server.CONN)) == 1
    monkeypatch.setattr(time, "time", lambda: now + 600)
    new_window = {key: current[key] + 600 for key in validate.PERIOD_FIELDS}
    assert send_customers(client, "a", [], **new_window).json()["stored"] is True
    assert db.customers(server.CONN) == []
    assert send_customers(client, "a", [current]).json()["stored"] is False
    assert send_customers(client, "a", [], **new_window).json()["stored"] is True  # same-window retry
    restarted = db.connect(server._db_path)
    assert db.replace_customers(restarted, "a", validate.customers([{"worker": "old"}]), 24) is False
    assert db.customers(restarted) == []
    restarted.close()


def test_legacy_customer_reports_work_until_trustworthy_window_received(cloud):
    server, client = cloud
    legacy = {"worker": "same-worker", "machines": 1, "delivered_th_h": 7032}
    assert send_customers(client, "a", [legacy]).json()["stored"] is True
    assert send_customers(client, "a", []).json()["stored"] is True
    assert send_customers(client, "a", [aligned_report()]).json()["stored"] is True
    assert send_customers(client, "a", [legacy]).json()["stored"] is False
    assert server._customers_payload()["rows"][0]["avg_hashrate_ths"] == 293


@pytest.mark.parametrize("old_prefix", ["period", "billing_period"])
def test_report_order_checks_both_independent_window_ends(cloud, monkeypatch, old_prefix):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    current = aligned_report()
    send_customers(client, "a", [current])
    incoming = dict(current)
    monkeypatch.setattr(time, "time", lambda: now + 600)
    for prefix in ("period", "billing_period"):
        for suffix in ("start_ts", "end_ts"):
            incoming[prefix + "_" + suffix] += -600 if prefix == old_prefix else 600
    assert send_customers(client, "a", [incoming]).json()["stored"] is False
    row = server._customers_payload()["rows"][0]
    assert all(row[key] == current[key] for key in validate.PERIOD_FIELDS)


def test_inconsistent_full_report_windows_do_not_erase_previous_report(cloud):
    server, client = cloud
    current = aligned_report()
    send_customers(client, "a", [current])
    second = {**current, "worker": "different-worker"}
    for key in validate.PERIOD_FIELDS:
        second[key] -= 600
    assert send_customers(client, "a", [current, second]).status_code == 400
    assert send_customers(client, "a", [current], period_end_ts=current["period_end_ts"] - 600).status_code == 400
    assert len(db.customers(server.CONN)) == 1


def test_pull_customer_order_guard_uses_top_level_empty_window(cloud, monkeypatch):
    server, _ = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    site = {"id": "pull-a", "name": "pull-a", "url": "http://fixture.invalid"}
    server.CFG["sites"] = [site]
    row = aligned_report()
    old_window = {key: row[key] - 600 for key in validate.PERIOD_FIELDS}
    new_window = {key: row[key] + 600 for key in validate.PERIOD_FIELDS}
    replies = iter([{"ok": True, "hours": 24, "customers": [row]},
                    {"ok": True, "hours": 24, "customers": [], **old_window},
                    {"ok": True, "hours": 24, "customers": [], **new_window}])
    monkeypatch.setattr(server.COLLECTOR, "_get", lambda *args, **kwargs: next(replies))
    server.COLLECTOR.poll_customers()
    server.COLLECTOR.poll_customers()
    assert len(db.customers(server.CONN)) == 1
    monkeypatch.setattr(time, "time", lambda: now + 600)
    server.COLLECTOR.poll_customers()
    assert db.customers(server.CONN) == []
