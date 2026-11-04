"""Trend subtotals remain visible without concealing missing miners or time."""
from unittest.mock import patch

import pytest

import db
from test_data_integrity import START, conn, snapshot, send_summary  # noqa: F401
from test_site_management import cloud, login  # noqa: F401


def trend(conn, now, hours=24, points=288):
    with patch.object(db.time, "time", return_value=now):
        return {p["ts"]: p for p in db.trend(conn, "a", hours=hours, points=points)}


def partial(conn, ts, rate=161400, power=2800, **extra):
    values = dict(total=539, online=538, unknown=1, hashrate_complete=False,
                  power_complete=False, hashrate_known_machines=538,
                  power_known_machines=525, known_hashrate_ths=rate, known_power_kw=power)
    values.update(extra)
    return snapshot(conn, ts, rate=None, power=None, **values)


def test_complete_and_partial_machine_totals_keep_separate_meanings(conn):
    snapshot(conn, START - 300, rate=161700, power=3000, total=539, online=539)
    partial(conn, START)
    rows = trend(conn, START + 300)
    full, part = rows[START - 300], rows[START]
    assert full["hashrate_ths"] == 161700
    assert full["power_kw"] == 3000
    assert full["partial_hashrate_ths"] is full["partial_power_kw"] is None
    assert part["hashrate_ths"] is part["power_kw"] is None
    assert part["partial_hashrate_ths"] == 161400
    assert part["partial_power_kw"] == 2800
    assert part["hashrate_coverage"] == dict(known_min=538, known_max=538,
        total_min=539, total_max=539, seconds=300, window_seconds=300, continuous=True)
    assert part["power_coverage"]["known_min"] == part["power_coverage"]["known_max"] == 525
    assert part["legacy_hashrate_ths"] is part["legacy_power_kw"] is None


def test_power_partial_does_not_remove_complete_hashrate(conn):
    snapshot(conn, START, rate=161700, power=None, total=539, online=539,
             power_complete=False, power_known_machines=525, known_power_kw=2800)
    p = trend(conn, START + 300)[START]
    assert p["hashrate_ths"] == 161700
    assert p["partial_hashrate_ths"] is None
    assert p["power_kw"] is None
    assert p["partial_power_kw"] == 2800


def test_mixed_bucket_never_silently_averages_only_its_complete_fragments(conn):
    snapshot(conn, START, rate=600, power=12, total=2, online=2)
    partial(conn, START + 60, rate=300, power=6, total=2, online=1, unknown=1,
            hashrate_known_machines=1, power_known_machines=1)
    p = trend(conn, START + 300)[START]
    assert p["hashrate_ths"] is p["power_kw"] is None
    assert p["partial_hashrate_ths"] == 360  # (600*60 + 300*240) / 300
    assert p["partial_power_kw"] == 7.2
    assert p["hashrate_coverage"] == dict(known_min=1, known_max=2,
        total_min=2, total_max=2, seconds=300, window_seconds=300, continuous=True)
    with patch.object(db.time, "time", return_value=START + 300):
        # Trend presentation must not change the alert/complete-observation mean.
        assert db.avg_hashrate(conn, "a", START, START + 300) == 600


def test_missing_time_inside_a_bucket_stays_discontinuous(conn):
    snapshot(conn, START, rate=100, power=100)
    snapshot(conn, START + 60, ok=0, rate=None, power=None)
    snapshot(conn, START + 180, rate=200, power=200)
    p = trend(conn, START + 300)[START]
    assert p["hashrate_ths"] is p["power_kw"] is None
    assert p["partial_hashrate_ths"] == p["partial_power_kw"] == 166.7
    assert p["hashrate_coverage"]["seconds"] == 180
    assert p["hashrate_coverage"]["window_seconds"] == 300
    assert p["hashrate_coverage"]["continuous"] is False


def test_gap_cap_keeps_empty_buckets_between_fresh_partial_readings(conn):
    partial(conn, START)
    partial(conn, START + 1000)
    rows = trend(conn, START + 1200)
    assert rows[START]["hashrate_coverage"]["continuous"] is True
    for stamp in (START + 300, START + 600):
        assert rows[stamp]["hashrate_ths"] is rows[stamp]["partial_hashrate_ths"] is None
        assert rows[stamp]["hashrate_coverage"]["seconds"] == 0
    p = rows[START + 900]
    assert p["partial_hashrate_ths"] == 161400
    assert p["hashrate_coverage"]["seconds"] == 200
    assert p["hashrate_coverage"]["continuous"] is False


def test_fresh_receipt_does_not_extend_old_measurement_coverage(conn):
    partial(conn, START, measurement_ts=START - 850)
    partial(conn, START + 60, measurement_ts=START - 850)
    p = trend(conn, START + 300)[START]
    assert p["partial_hashrate_ths"] == 161400
    assert p["hashrate_coverage"]["seconds"] == 50
    assert p["hashrate_coverage"]["continuous"] is False


@pytest.mark.parametrize("extra", [
    {"measurement_ts": START - 901}, {"measurement_ts": START + 1},
    {"measurement_ts": None}, {"hashrate_known_machines": 0},
    {"hashrate_known_machines": None}, {"hashrate_known_machines": 539},
    {"known_hashrate_ths": None},
])
def test_invalid_or_stale_partial_readings_are_never_resurrected(conn, extra):
    partial(conn, START, **extra)
    p = trend(conn, START + 300)[START]
    assert p["hashrate_ths"] is p["partial_hashrate_ths"] is None
    assert p["hashrate_coverage"]["seconds"] == 0


def test_legacy_protocol_is_not_promoted_by_a_known_subtotal(conn):
    partial(conn, START, measurement_time_supported=False, measurement_ts=None)
    snapshot(conn, START + 300, rate=600, measurement_time_supported=False,
             measurement_ts=None, hashrate_complete=None)
    rows = trend(conn, START + 600)
    for p in (rows[START], rows[START + 300]):
        assert p["hashrate_ths"] is p["partial_hashrate_ths"] is None
    assert rows[START + 300]["legacy_hashrate_ths"] is None


def test_confirmed_full_and_partial_zero_are_valid_measurements(conn):
    snapshot(conn, START, rate=0, power=0)
    partial(conn, START + 300, rate=0, power=0)
    rows = trend(conn, START + 600)
    assert rows[START]["hashrate_ths"] == rows[START]["power_kw"] == 0
    assert rows[START + 300]["partial_hashrate_ths"] == rows[START + 300]["partial_power_kw"] == 0
    assert rows[START + 300]["hashrate_coverage"]["known_min"] == 538


def test_first_and_last_query_buckets_use_clipped_window_seconds(conn):
    for stamp in range(START - 3600, START + 61, 60):
        snapshot(conn, stamp)
    rows = trend(conn, START + 75, hours=1, points=60)
    first, last = rows[min(rows)], rows[max(rows)]
    assert first["hashrate_coverage"]["window_seconds"] == 45
    assert last["hashrate_coverage"]["window_seconds"] == 15
    for p in (first, last):
        assert p["hashrate_ths"] == 100
        assert p["partial_hashrate_ths"] is None
        assert p["hashrate_coverage"]["continuous"] is True


def test_current_instant_is_visible_without_claiming_observed_duration(conn):
    snapshot(conn, START)
    p = trend(conn, START)[START]
    assert p["hashrate_ths"] is None
    assert p["partial_hashrate_ths"] == 100
    assert p["hashrate_coverage"] == dict(known_min=1, known_max=1, total_min=1,
        total_max=1, seconds=0, window_seconds=0, continuous=False)


def test_unknown_machine_counts_are_not_invented_for_a_short_observation(conn):
    snapshot(conn, START + 30, total=None, online=None)
    p = trend(conn, START + 60, hours=1, points=60)[START]
    assert p["partial_hashrate_ths"] == 100
    assert p["hashrate_coverage"] == dict(known_min=None, known_max=None,
        total_min=None, total_max=None, seconds=30, window_seconds=60, continuous=False)


def test_http_trend_keeps_old_pairs_and_supplies_same_partial_evidence_as_detail(cloud, monkeypatch):
    server, client = cloud
    monkeypatch.setattr(db.time, "time", lambda: START)
    login(client)
    assert send_summary(client, total=2, online=1, unknown=1, hashrate_ths=None,
        hashrate_complete=False, known_hashrate_ths=293, hashrate_known_machines=1,
        power_kw=None, power_complete=False, known_power_kw=3, power_known_machines=1).status_code == 200
    monkeypatch.setattr(db.time, "time", lambda: START + 300)
    response = client.get("/api/trend?hours=24&points=288")
    assert response.status_code == 200
    site = response.json()["sites"][0]
    assert all(len(pair) == 2 for pair in site["points"] + site["legacy_points"])
    assert all(value is None for _, value in site["points"])
    assert len(site["partial_points"]) == 1
    part = site["partial_points"][0]
    assert part["ts"] == START and part["value"] == 293
    detail = client.get("/api/site/a?hours=24").json()["trend"]
    p = next(p for p in detail if p["ts"] == START)
    assert part["coverage"] == p["hashrate_coverage"]
    assert p["partial_power_kw"] == 3
