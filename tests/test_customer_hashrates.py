"""Customer rates keep TH/s distinct from historical TH·h, across upgrades and sites."""
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
import validate
from test_site_management import cloud, login, write  # noqa: F401


def customer(worker="customer", rate=293, mean=293, hours=24, observed=None, machines=1):
    observed = hours if observed is None else observed
    end = int(time.time()) // 600 * 600
    return {"worker": worker, "machines": machines, "current_machines": machines,
            "uptime_pct": 100, "delivered_th_h": rate * hours, "power_kwh": 120,
            "hashrate_ths": rate, "avg_hashrate_ths": mean, "hashrate_hours": hours,
            "observed_hours": observed, "coverage_pct": observed / hours * 100,
            "billing_source": "observed", "billing_hours": hours,
            "delivery_observed_hours": observed, "power_observed_hours": observed,
            "uptime_observed_hours": observed,
            "period_start_ts": end - hours * 3600, "period_end_ts": end,
            "billing_period_start_ts": end - hours * 3600, "billing_period_end_ts": end,
            "hashrate_ts": int(time.time())}


def post_customers(client, rows, sid="st-a", hours=24):
    return client.post("/api/ingest/customers", headers={"Authorization": "Bearer reporting-test-token"},
                       json={"site_id": sid, "site": sid, "hours": hours, "customers": rows})


def post_summary(client, rows, sid="st-a", age=0, **extra):
    return client.post("/api/ingest/summary", headers={"Authorization": "Bearer reporting-test-token"},
                       json={"site_id": sid, "site": sid, "scan_ts": int(time.time()) - age,
                             "scan_age_s": age, "customer_hashrates": rows, **extra})


def payload(client):
    login(client)
    response = client.get("/api/customers")
    assert response.status_code == 200, response.text
    return response.json()


def test_old_database_migrates_without_inventing_rates(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE site_customers(site_id TEXT,worker TEXT,machines INTEGER,"
                "uptime_pct REAL,delivered_th_h REAL,power_kwh REAL,hours INTEGER,"
                "updated_ts INTEGER,PRIMARY KEY(site_id,worker))")
    old.execute("INSERT INTO site_customers VALUES('a','customer',1,100,7032,120,24,100)")
    old.commit()
    old.close()
    for _ in range(2):  # migration is idempotent, old bills survive every startup
        conn = db.connect(path)
        row = db.customers(conn)[0]
        assert row["delivered_th_h"] == 7032
        assert row["power_kwh"] == 120
        for field in ("hashrate_ths", "avg_hashrate_ths", "hashrate_hours", "observed_hours",
                      "coverage_pct", "hashrate_ts", "current_machines"):
            assert row[field] is None
        conn.close()


@pytest.mark.parametrize("value", ["<img src=x onerror=alert(1)>", float("nan"),
                                  float("inf"), -1, True, False, 10**20])
def test_invalid_measurements_are_unknown_not_zero(value):
    row = validate.customers([{"worker": "c", "hashrate_ths": value,
                               "avg_hashrate_ths": value, "coverage_pct": value}])[0]
    assert row["hashrate_ths"] is None
    assert row["avg_hashrate_ths"] is None
    assert row["coverage_pct"] is None


def test_measured_zero_and_numeric_strings_are_preserved():
    row = validate.customers([{"hashrate_ths": 0, "avg_hashrate_ths": "293"}])[0]
    assert row["hashrate_ths"] == 0
    assert row["avg_hashrate_ths"] == 293


def test_realtime_snapshot_does_not_trust_future_client_clock():
    rows, stamp = validate.customer_snapshot({"scan_ts": 253402300799, "scan_age_s": 120,
        "customer_hashrates": [{"worker": "c", "machines": 1,
                                "hashrate_ths": 293, "hashrate_ts": 253402300799}]}, 2000000000)
    assert stamp is None
    assert rows[0]["hashrate_ts"] is None
    assert validate.customer_snapshot({"scan_ts": 253402300799, "customer_hashrates": []}, 2000000000) == ([], None)
    assert validate.customer_snapshot({}, 2000000000) is None


def test_single_machine_mean_is_rate_not_twenty_four_times_rate(cloud):
    server, client = cloud
    assert post_customers(client, [customer()]).status_code == 200
    result = payload(client)
    row = result["rows"][0]
    assert row["hashrate_ths"] == 293
    assert row["avg_hashrate_ths"] == 293
    assert row["delivered_th_h"] == 7032
    assert row["hashrate_hours"] == 24
    assert row["observed_hours"] == 24
    assert row["avg_complete"] is True
    assert row["hashrate_stale"] is False
    assert result["by_worker"][0]["avg_hashrate_ths"] == 293
    assert client.get("/api/site/st-a").json()["customers"][0]["avg_complete"] is True


def test_cross_site_rates_sum_without_weighting_or_multiplying_by_count(cloud):
    _, client = cloud
    first = customer(rate=300, mean=293, machines=10)
    first["current_machines"] = 1
    assert post_customers(client, [first], "st-a").status_code == 200
    assert post_customers(client, [customer(rate=290, mean=290)], "st-b").status_code == 200
    total = payload(client)["by_worker"][0]
    assert total["hashrate_ths"] == 590
    assert total["avg_hashrate_ths"] == 583
    assert total["machines"] == 11  # original billing peak remains backward compatible
    assert total["current_machines"] == 2
    assert total["hashrate_hours"] == 24
    assert total["periods"] == [24]
    assert total["avg_complete"] is True


def test_unknown_site_rate_is_not_treated_as_zero_in_total(cloud):
    _, client = cloud
    unknown = customer()
    unknown["hashrate_ths"] = None
    post_customers(client, [customer(rate=0, mean=0)], "st-a")
    post_customers(client, [unknown], "st-b")
    total = payload(client)["by_worker"][0]
    assert total["hashrate_ths"] is None
    assert total["hashrate_known_sites"] == 1
    assert total["hashrate_unavailable_reason"] == "partial_sites"
    assert total["avg_hashrate_ths"] == 293


def test_partial_site_mean_does_not_claim_full_period(cloud):
    _, client = cloud
    post_customers(client, [customer(observed=6)])
    result = payload(client)
    for row in (result["rows"][0], result["by_worker"][0]):
        assert row["avg_hashrate_ths"] == 293
        assert row["observed_hours"] == 6
        assert row["coverage_pct"] == 25
        assert row["avg_complete"] is False


def test_partial_means_from_different_sites_cannot_be_added(cloud):
    _, client = cloud
    post_customers(client, [customer(mean=100, observed=12)], "st-a")
    post_customers(client, [customer(mean=300, observed=12)], "st-b")
    result = payload(client)
    assert sorted(r["avg_hashrate_ths"] for r in result["rows"]) == [100, 300]
    total = result["by_worker"][0]
    assert total["avg_hashrate_ths"] is None  # two partial windows may have no overlap
    assert total["avg_unavailable_reason"] == "partial_coverage"
    assert total["avg_complete"] is False


def test_mixed_report_periods_are_not_labelled_twenty_four_hours(cloud):
    _, client = cloud
    post_customers(client, [customer(hours=24)], "st-a", hours=24)
    post_customers(client, [customer(hours=48)], "st-b", hours=48)
    total = payload(client)["by_worker"][0]
    assert total["hashrate_ths"] == 586
    assert total["avg_hashrate_ths"] is None
    assert total["hashrate_hours"] is None
    assert total["periods"] == [24, 48]
    assert total["avg_unavailable_reason"] == "mixed_periods"


def test_legacy_report_never_converts_integral_into_unknown_average(cloud):
    _, client = cloud
    post_customers(client, [{"worker": "c", "machines": 1, "delivered_th_h": 7032}])
    row = payload(client)["rows"][0]
    assert row["delivered_th_h"] is None
    assert row["legacy_delivered_th_h"] == 7032
    assert row["hashrate_ths"] is None
    assert row["avg_hashrate_ths"] is None
    assert row["current_machines"] is None
    assert row["avg_complete"] is False
    assert row["hashrate_unavailable_reason"] == "not_reported"
    assert row["avg_unavailable_reason"] == "not_reported"
    assert row["hashrate_age_s"] is None
    assert row["hashrate_stale"] is False


def test_fresh_site_summary_with_legacy_customer_report_is_not_expired(cloud, monkeypatch):
    """Screenshot regression: live 400.1 PH/s site, six-minute-old bill, no customer rates."""
    _, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now - 360)
    old = {"worker": "customer", "machines": 465, "uptime_pct": 98.8,
           "delivered_th_h": 3360000, "power_kwh": 59940}
    assert post_customers(client, [old]).status_code == 200
    monkeypatch.setattr(time, "time", lambda: now - 37)
    response = client.post("/api/ingest/summary", headers={
        "Authorization": "Bearer reporting-test-token"}, json={
        "site_id": "st-a", "site": "st-a", "online": 1330, "total": 1330,
        "hashrate_ths": 400100, "scan_age_s": 120})
    assert response.status_code == 200
    monkeypatch.setattr(time, "time", lambda: now)
    result = payload(client)
    detail = client.get("/api/site/st-a").json()
    assert detail["site"]["state"] == "ok"
    assert detail["site"]["online"] == detail["site"]["total"] == 1330
    assert detail["site"]["hashrate_ths"] == 400100
    assert detail["site"]["data_age_s"] == 37
    assert detail["site"]["scan_age_s"] is None  # no source timestamp: age alone cannot prove transport delay
    assert detail["site"]["data_quality"] == "legacy_unverified"
    for row in (detail["customers"][0], result["rows"][0], result["by_worker"][0]):
        assert row["machines"] == 465
        assert row["current_machines"] is None
        assert row["hashrate_ths"] is None
        assert row["avg_hashrate_ths"] is None
        assert row["hashrate_age_s"] is None
        assert row["hashrate_stale"] is False
        assert row["hashrate_unavailable_reason"] == "not_reported"
        assert row["avg_unavailable_reason"] == "not_reported"
        assert row["report_age_s"] == 360
        assert row["report_stale"] is False
        assert row["delivered_th_h"] is None
        assert row["power_kwh"] is None
        assert row["legacy_delivered_th_h"] == 3360000
        assert row["legacy_power_kwh"] == 59940


def test_new_report_waiting_for_first_scan_is_missing_not_unreported(cloud):
    _, client = cloud
    pending = {"worker": "customer", "machines": 1, "current_machines": 1,
               "hashrate_hours": 24, "observed_hours": 0, "coverage_pct": 0}
    assert post_customers(client, [pending]).status_code == 200
    result = payload(client)
    for row in (result["rows"][0], result["by_worker"][0]):
        assert row["current_machines"] == 1
        assert row["hashrate_ths"] is None
        assert row["avg_hashrate_ths"] is None
        assert row["hashrate_unavailable_reason"] == "missing_data"
        assert row["avg_unavailable_reason"] == "missing_data"
        assert row["hashrate_age_s"] is None
        assert row["hashrate_stale"] is False
        assert row["avg_complete"] is False


def test_customer_missing_fields_do_not_mark_metric_capable_site_unreported(cloud):
    _, client = cloud
    post_customers(client, [customer(worker="known"), {"worker": "unknown", "machines": 1}])
    unknown = next(r for r in payload(client)["rows"] if r["worker"] == "unknown")
    assert unknown["hashrate_ths"] is None
    assert unknown["hashrate_unavailable_reason"] == "missing_data"
    assert unknown["avg_unavailable_reason"] == "missing_data"


def test_current_snapshot_with_unknown_reading_and_no_history_is_not_expired(cloud):
    _, client = cloud
    assert post_summary(client, [{"worker": "customer", "machines": 1}]).status_code == 200
    result = payload(client)
    for row in (result["rows"][0], result["by_worker"][0]):
        assert row["current_machines"] == 1
        assert row["hashrate_ths"] is None
        assert row["hashrate_unavailable_reason"] == "missing_data"
        assert row["hashrate_age_s"] is not None
        assert row["hashrate_stale"] is False
        assert row["avg_unavailable_reason"] == "missing_data"
        assert row["report_age_s"] is None
        assert row["report_stale"] is False


@pytest.mark.parametrize("stamp", [None, 0, "invalid", 3600])
def test_unknown_or_untrusted_timestamps_do_not_expire_known_rates(cloud, monkeypatch, stamp):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    stamp = now + stamp if isinstance(stamp, int) and stamp else stamp
    report = {**customer(), "hashrate_ts": stamp}
    assert post_customers(client, [report]).status_code == 200
    # Cover unreadable timestamps already stored by an older version as well as absent reports.
    server.CONN.execute("UPDATE site_customers SET updated_ts=?", (stamp,))
    server.CONN.commit()
    result = payload(client)
    for row in (result["rows"][0], result["by_worker"][0]):
        assert row["hashrate_ths"] == 293
        assert row["avg_hashrate_ths"] == 293
        assert row["hashrate_unavailable_reason"] is None
        assert row["avg_unavailable_reason"] is None
        assert row["hashrate_age_s"] is None
        assert row["hashrate_stale"] is False
        assert row["report_age_s"] is None
        assert row["report_stale"] is False


def test_old_report_with_new_snapshot_waits_for_mean_without_upgrade_claim(cloud):
    _, client = cloud
    post_customers(client, [{"worker": "customer", "machines": 1, "delivered_th_h": 7032}])
    post_summary(client, [{"worker": "customer", "machines": 1, "hashrate_ths": 293}])
    result = payload(client)
    for row in (result["rows"][0], result["by_worker"][0]):
        assert row["hashrate_ths"] == 293
        assert row["hashrate_unavailable_reason"] is None
        assert row["avg_hashrate_ths"] is None
        assert row["avg_unavailable_reason"] == "missing_data"


def test_all_unknown_sites_distinguish_unreported_from_missing_readings(cloud):
    _, client = cloud
    old = {"worker": "customer", "machines": 1, "delivered_th_h": 7032}
    post_customers(client, [old], "st-a")
    post_customers(client, [old], "st-b")
    total = payload(client)["by_worker"][0]
    assert total["hashrate_ths"] is None
    assert total["hashrate_unavailable_reason"] == "not_reported"
    assert total["avg_unavailable_reason"] == "not_reported"
    # A new channel with an unknown reading still does not justify a made-up total of zero.
    post_summary(client, [{"worker": "customer", "machines": 1}], "st-b")
    total = payload(client)["by_worker"][0]
    assert total["hashrate_ths"] is None
    assert total["hashrate_known_sites"] == 0
    assert total["hashrate_unavailable_reason"] == "missing_data"
    assert total["avg_unavailable_reason"] == "missing_data"


def test_partly_reported_cross_site_mean_is_explicitly_incomplete(cloud):
    _, client = cloud
    post_customers(client, [customer()], "st-a")
    post_customers(client, [{"worker": "customer", "machines": 1, "delivered_th_h": 7032}], "st-b")
    total = payload(client)["by_worker"][0]
    assert total["sites"] == 2
    assert total["avg_known_sites"] == 1
    assert total["avg_hashrate_ths"] is None
    assert total["avg_complete"] is False
    assert total["avg_unavailable_reason"] == "partial_sites"


def test_realtime_summary_visible_before_history_and_slow_report_cannot_rollback(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    current = {"worker": "customer", "machines": 2, "hashrate_ths": 600}
    assert post_summary(client, [current]).status_code == 200
    row = payload(client)["rows"][0]
    assert row["hashrate_ths"] == 600
    assert row["current_machines"] == 2
    assert row["avg_hashrate_ths"] is None
    assert db.customer_count_by_site(server.CONN) == {"st-a": 1}
    old_report = customer(rate=290)
    old_report["hashrate_ts"] = now - 300
    assert post_customers(client, [old_report]).status_code == 200
    row = payload(client)["rows"][0]
    assert row["hashrate_ths"] == 600
    assert row["current_machines"] == 2
    assert row["hashrate_source"] == "summary"
    assert row["avg_hashrate_ths"] == 293
    assert post_summary(client, [{**current, "hashrate_ths": 100}], age=600).status_code == 200
    assert payload(client)["rows"][0]["hashrate_ths"] == 600


def test_missing_current_worker_is_zero_but_missing_field_preserves_live_snapshot(cloud):
    _, client = cloud
    post_customers(client, [customer(worker="old")])
    post_summary(client, [{"worker": "new", "machines": 1, "hashrate_ths": 300}])
    rows = {r["worker"]: r for r in payload(client)["rows"]}
    assert rows["old"]["current_machines"] == 0
    assert rows["old"]["hashrate_ths"] == 0
    assert rows["new"]["hashrate_ths"] == 300
    client.post("/api/ingest/summary", headers={"Authorization": "Bearer reporting-test-token"},
                json={"site_id": "st-a", "site": "st-a"})
    assert {r["worker"]: r for r in payload(client)["rows"]}["new"]["hashrate_ths"] == 300
    assert post_summary(client, []).status_code == 200
    rows = payload(client)["rows"]
    assert [r["worker"] for r in rows] == ["old"]
    assert rows[0]["hashrate_ths"] == 0
    assert rows[0]["current_machines"] == 0


def test_site_clock_skew_cannot_make_slow_report_override_live_roster(cloud, monkeypatch):
    _, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    post_summary(client, [{"worker": "new", "machines": 2, "hashrate_ths": 600}])
    old = customer(worker="old", rate=290)
    old["hashrate_ts"] = now + 60  # real scan N-60, site clock is +120 seconds
    new = {**old, "worker": "new"}
    post_customers(client, [old, new])
    rows = {r["worker"]: r for r in payload(client)["rows"]}
    assert rows["new"]["hashrate_ths"] == 600
    assert rows["new"]["current_machines"] == 2
    assert rows["old"]["hashrate_ths"] == 0
    assert rows["old"]["current_machines"] == 0


@pytest.mark.parametrize("invalid", [None, {}, [None], [{"worker": "a"}, {"worker": "a"}], [{}] * 5001])
def test_bad_snapshot_cannot_clear_previous_roster(cloud, invalid):
    server, client = cloud
    post_summary(client, [{"worker": "c", "machines": 1, "hashrate_ths": 293}])
    before = server.CONN.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    response = post_summary(client, invalid)
    assert response.status_code == 400
    assert server.CONN.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == before
    assert payload(client)["rows"][0]["hashrate_ths"] == 293


def test_old_measurements_are_flagged_stale_not_silently_zeroed(cloud, monkeypatch):
    _, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    post_customers(client, [customer()])
    post_summary(client, [{"worker": "customer", "machines": 1, "hashrate_ths": 300}])
    monkeypatch.setattr(time, "time", lambda: now + 3600)
    result = payload(client)
    for row in (result["rows"][0], result["by_worker"][0]):
        assert row["hashrate_ths"] == 300
        assert row["hashrate_age_s"] == 3600
        assert row["hashrate_stale"] is True
        assert row["hashrate_unavailable_reason"] is None
        assert row["avg_unavailable_reason"] is None
        assert row["report_age_s"] == 3600
        assert row["report_stale"] is True


def test_pull_mode_keeps_new_rates_and_actual_mean_window(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    site = {"id": "pull-a", "name": "Pull", "url": "https://unused.invalid", "token": "test"}
    server.COLLECTOR.cfg["sites"] = [site]
    db.upsert_site(server.CONN, site["id"], site["name"], "air", now)
    def get(_site, path, _params):
        if path.endswith("summary"):
            return {"ok": True, "scan_ts": now, "customer_hashrates": [
                {"worker": "customer", "machines": 2, "hashrate_ths": 600}]}
        return {"ok": True, "hours": 48, "covered_hours": 6, "truncated": True,
                "customers": [customer(rate=590, mean=586, hours=48, observed=6, machines=2)]}
    monkeypatch.setattr(server.COLLECTOR, "_get", get)
    server.COLLECTOR._poll_site(site)
    server.COLLECTOR.poll_customers()
    row = payload(client)["rows"][0]
    assert row["hashrate_ths"] == 600
    assert row["avg_hashrate_ths"] == 586
    assert row["hashrate_hours"] == 48
    assert row["observed_hours"] == 6
    assert row["avg_complete"] is False


def test_pull_mode_fresh_legacy_data_is_unreported_not_expired(cloud, monkeypatch):
    server, client = cloud
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    site = {"id": "pull-a", "name": "Pull", "url": "https://unused.invalid", "token": "test"}
    server.COLLECTOR.cfg["sites"] = [site]
    db.upsert_site(server.CONN, site["id"], site["name"], "air", now)

    def get(_site, path, _params):
        if path.endswith("summary"):
            return {"ok": True, "scan_ts": now - 120, "total_hashrate_ths": 400100}
        return {"ok": True, "hours": 24, "customers": [
            {"worker": "customer", "machines": 465, "delivered_th_h": 3360000}]}

    monkeypatch.setattr(server.COLLECTOR, "_get", get)
    server.COLLECTOR._poll_site(site)
    server.COLLECTOR.poll_customers()
    row = payload(client)["rows"][0]
    assert row["hashrate_ths"] is None
    assert row["avg_hashrate_ths"] is None
    assert row["hashrate_unavailable_reason"] == "not_reported"
    assert row["avg_unavailable_reason"] == "not_reported"
    assert row["hashrate_stale"] is False
    assert row["report_stale"] is False


def test_deleting_site_removes_current_roster_and_its_empty_state(cloud):
    server, client = cloud
    post_customers(client, [customer()])
    post_summary(client, [{"worker": "customer", "machines": 1, "hashrate_ths": 293}])
    login(client)
    assert write(client, "DELETE", "/api/site/st-a").status_code == 200
    for table in ("site_customers", "site_customer_current", "site_customer_current_state"):
        assert server.CONN.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert client.get("/api/customers").json()["rows"] == []
