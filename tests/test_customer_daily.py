# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db


def test_replace_and_get_customer_daily():
    conn = db.connect(":memory:")
    n = db.replace_customer_daily(conn, "st-a", [
        {"day": "2026-11-12", "worker": "alice", "machines": 10,
         "delivered_th_h": 100.5, "power_kwh": 50.1, "uptime_pct": 99.0, "observed_s": 86400},
        {"day": "2026-11-12", "worker": "bob", "machines": 2,
         "delivered_th_h": 20, "power_kwh": 8, "uptime_pct": 80, "observed_s": 86400},
    ])
    assert n == 2
    rows = db.get_customer_daily(conn, "st-a", days=30)
    assert {r["worker"] for r in rows} == {"alice", "bob"}
    conn.close()
