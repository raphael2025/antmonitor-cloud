#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场地来数的统一清洗(推送 /api/ingest/* 与拉取 collector 共用)。

为什么集中在这里：场地端数据一律视为不可信——场地机器被攻破、共享 ingest token 泄露，
对方就能往云端写任意 JSON。字段不清洗直接入库，轻则把 /api/customers 聚合搞成 500，
重则把 "<img onerror=...>" 这类字符串存进数值列，前端渲染时在管理员浏览器里执行
(存储型 XSS → 借管理员会话改通知设置/触发更新)。所以：数值列只进数字，文本列截长去控制符。
"""
import math


def num(v, cast=float, default=0, hi=None):
    """数值钳制：非法/NaN/Inf→default，负数→0，超上限→上限(防污染库和图表)。"""
    try:
        n = cast(v)
    except (TypeError, ValueError, OverflowError):
        return default
    if isinstance(n, float) and not math.isfinite(n):
        return default
    if n < 0:
        return 0
    if hi is not None and n > hi:
        return hi
    return n


def opt_num(v, cast=float, hi=None):
    """可空数值：None/非法 → None(表示"未知")，其余同 num()。"""
    if v is None:
        return None
    return num(v, cast, None, hi)


def text(v, maxlen):
    """文本：转字符串、去掉控制字符(换行/回车/NUL 等)、截长。"""
    if v is None:
        return ""
    s = "".join(ch for ch in str(v) if ch >= " " and ch != "\x7f")
    return s.strip()[:maxlen]


# 快照数值列上限(与 ingest 原有口径一致)
_SNAP_LIMITS = {
    "online": (int, 10**6), "total": (int, 10**6),
    "hashrate_ths": (float, 10**9),        # ≤1000 EH
    "power_kw": (float, 10**6),            # ≤1 GW
    "active_alerts": (int, 10**6),
    "containers": (int, 10**5), "containers_faulty": (int, 10**5),
    "containers_offline": (int, 10**5),
}


def snapshot_fields(src):
    """从场地来数里取快照数值列(已钳制)。src 的 key 与快照列同名。"""
    return {k: num(src.get(k), cast, hi=hi) for k, (cast, hi) in _SNAP_LIMITS.items()}


def customers(rows, limit=5000):
    """客户报表行清洗：非 dict 丢弃，worker 截 128 字符，数值列钳制。"""
    out = []
    if not isinstance(rows, list):
        return out
    for c in rows[:limit]:
        if not isinstance(c, dict):
            continue
        out.append({
            "worker": text(c.get("worker"), 128) or "(未知)",
            "machines": num(c.get("machines"), int, hi=10**6),
            "uptime_pct": opt_num(c.get("uptime_pct"), float, hi=100),
            "delivered_th_h": opt_num(c.get("delivered_th_h"), float, hi=10**13),
            "power_kwh": opt_num(c.get("power_kwh"), float, hi=10**10),
        })
    return out


def site_key(v, maxlen=64):
    """site_id / 场地名：去控制字符后 1..maxlen 字符；非法返回空串。"""
    s = text(v, maxlen + 1)
    return s if 0 < len(s) <= maxlen else ""
