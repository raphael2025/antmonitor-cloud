# -*- coding: utf-8 -*-
"""场地健康语义：上报过期 / 扫描停更 / 场内自报 site_down；通知短窗合并。"""
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
import notify
from collector import Collector

START = 2_100_000_000
SITE = {"id": "hs-site", "name": "Health", "type": "air", "mode": "push"}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_site_down_from_local_alert_summary(conn):
    cfg = {"poll": {"interval": 60, "fail_offline": 3, "stalled_after_s": 1200,
                    "push_interval": 60}, "sites": [], "alerts": {"enabled": False}}
    col = Collector(cfg, conn)
    now = START
    snap = {"ts": now, "online": 10, "total": 100, "active_alerts": 90, "scan_age_s": 5}
    assert col.site_state(SITE["id"], snap, mode="push", now=now) == "alert"
    db.replace_alert_summary(conn, SITE["id"], [
        {"category": "site_down", "label": "全场掉线", "count": 1, "since_ts": now, "workers": []},
    ])
    assert col.site_state(SITE["id"], snap, mode="push", now=now) == "site_down"
    # 上报过期优先于场内自报
    old = dict(snap, ts=now - 600)
    assert col.site_state(SITE["id"], old, mode="push", now=now) == "unreachable"


def test_notify_site_down_message_and_merge(conn, monkeypatch):
    clock = [START]
    monkeypatch.setattr(notify.time, "time", lambda: clock[0])
    n = notify.Notifier({"alerts": {
        "enabled": True, "cooldown": 0, "hashrate_drop_pct": 0,
        "merge_window_sec": 30,
        "telegram": {"enabled": True, "bot_token": "t", "chat_id": "c"},
    }}, conn)
    sent = []
    monkeypatch.setattr(notify, "_send_telegram", lambda cfg, msg: sent.append(msg) or True)
    # 先走一轮 tick 登记 epoch，再测跳变
    n.tick([SITE], lambda *_: "ok")
    assert not sent

    n._on_transition(SITE["id"], SITE, "ok", "site_down",
                     {"online": 5, "total": 100, "ts": START}, clock[0])
    assert not sent  # 合并窗内先不发
    assert (SITE["id"], "site_down") in n.pending

    n._on_transition(SITE["id"], SITE, "site_down", "unreachable",
                     {"online": 5, "total": 100, "ts": START - 400}, clock[0])
    # site_down 与 unreachable 同组：反向事件清掉旧 pending，只留新的
    assert (SITE["id"], "site_down") not in n.pending
    assert (SITE["id"], "unreachable") in n.pending

    clock[0] = START + 35
    n._attempt_pending((SITE["id"], "unreachable"), clock[0])
    assert len(sent) == 1
    assert "上报过期" in sent[0] or "收不到上报" in sent[0]


def test_merge_window_combines_drop_and_offline(conn, monkeypatch):
    clock = [START]
    monkeypatch.setattr(notify.time, "time", lambda: clock[0])
    n = notify.Notifier({"alerts": {
        "enabled": True, "cooldown": 0, "hashrate_drop_pct": 30, "offline_threshold": 5,
        "merge_window_sec": 20,
        "telegram": {"enabled": True, "bot_token": "t", "chat_id": "c"},
    }}, conn)
    sent = []
    monkeypatch.setattr(notify, "_send_telegram", lambda cfg, msg: sent.append(msg) or True)
    n.tick([SITE], lambda *_: "ok")
    n._send(SITE["id"], "hashrate_drop", "📉 drop")
    n._send(SITE["id"], "offline_count", "🔌 offline")
    assert not sent
    clock[0] = START + 25
    n._attempt_pending((SITE["id"], "hashrate_drop"), clock[0])
    assert len(sent) == 1
    assert "drop" in sent[0] and "offline" in sent[0]
