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


def measurement(v, cast=float, hi=None):
    """可空测量值：非法、负值、布尔值和越界值都表示未知，不能当作实测 0。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        if cast is int:
            raw = float(v)
            if not math.isfinite(raw) or raw < 0 or not raw.is_integer():
                return None
            n = int(raw)
        else:
            n = cast(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(n, float) and not math.isfinite(n):
        return None
    if n < 0 or (hi is not None and n > hi):
        return None
    return n


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
    "containers_offline": (int, 10**5), "containers_unknown": (int, 10**5),
}


def snapshot_fields(src):
    """缺测与非法值保留未知；不能把缺字段、负值或布尔值当作实测零。"""
    out = {k: measurement(src.get(k), cast, hi=hi) for k, (cast, hi) in _SNAP_LIMITS.items()}
    out["unknown"] = measurement(src.get("unknown"), int, hi=10**6)
    for key in ("hashrate_complete", "power_complete"):
        out[key] = src[key] if isinstance(src.get(key), bool) else None
    for key in ("hashrate_known_machines", "power_known_machines"):
        out[key] = measurement(src.get(key), int, hi=10**6)
    out["known_hashrate_ths"] = measurement(src.get("known_hashrate_ths"), hi=10**9)
    out["known_power_kw"] = measurement(src.get("known_power_kw"), hi=10**6)
    for prefix in ("hashrate", "power"):
        known_key = prefix + "_known_machines"
        if out[prefix + "_complete"] is True and (
                (out["unknown"] is not None and out["unknown"] > 0) or
                (known_key in src and (out[known_key] is None or out["total"] is None or out[known_key] != out["total"]))):
            out[prefix + "_complete"] = False
    return out


BILLING_FIELDS = ("billing_hours", "billing_source", "delivery_observed_hours", "delivery_coverage_pct",
                  "power_observed_hours", "power_coverage_pct", "uptime_observed_hours", "uptime_coverage_pct",
                  "legacy_delivered_th_h", "legacy_power_kwh", "legacy_uptime_pct",
                  "delivered_th_h", "power_kwh", "uptime_pct")
PERIOD_FIELDS = ("period_start_ts", "period_end_ts", "billing_period_start_ts", "billing_period_end_ts")
REPORT_FIELDS = BILLING_FIELDS + PERIOD_FIELDS + ("machines_source",)


def customers(rows, limit=5000, strict=False):
    """客户报表保留未知、真实零和独立覆盖；旧估算只进入 legacy 字段。"""
    out = []
    if not isinstance(rows, list):
        if strict:
            raise ValueError("customers 非法")
        return out
    if strict and len(rows) > limit:
        raise ValueError("customers 行数超限")
    seen = set()
    for c in rows[:limit]:
        if not isinstance(c, dict):
            if strict:
                raise ValueError("customers 行非法")
            continue
        worker = text(c.get("worker"), 128) or "(未知)"
        if strict and worker in seen:
            raise ValueError("customers 客户重复")
        seen.add(worker)
        source = c.get("billing_source")
        if source not in ("observed", "legacy_only", "missing"):
            source = "legacy_only" if any(c.get(k) is not None for k in
                                         ("delivered_th_h", "power_kwh", "uptime_pct")) else "missing"
        row = {
            "worker": worker,
            "machines": num(c.get("machines"), int, hi=10**6),
            "hashrate_ths": measurement(c.get("hashrate_ths"), hi=10**9),
            "avg_hashrate_ths": measurement(c.get("avg_hashrate_ths"), hi=10**9),
            "hashrate_hours": measurement(c.get("hashrate_hours"), hi=24 * 90),
            "observed_hours": measurement(c.get("observed_hours"), hi=24 * 90),
            "coverage_pct": measurement(c.get("coverage_pct"), hi=100),
            "hashrate_ts": measurement(c.get("hashrate_ts"), int, hi=253402300799),
            "current_machines": measurement(c.get("current_machines"), int, hi=10**6),
            "billing_source": source,
            "billing_hours": measurement(c.get("billing_hours"), hi=24 * 90),
            "machines_source": "observed" if c.get("machines_source") == "observed" else "legacy",
        }
        for key, maximum in (("uptime_pct", 100), ("delivered_th_h", 10**13), ("power_kwh", 10**10)):
            row[key] = measurement(c.get(key), hi=maximum) if source == "observed" else None
            legacy = c.get("legacy_" + key)
            if legacy is None and source == "legacy_only":
                legacy = c.get(key)
            row["legacy_" + key] = measurement(legacy, hi=maximum)
        for prefix in ("delivery", "power", "uptime"):
            observed = measurement(c.get(prefix + "_observed_hours"), hi=24 * 90)
            hours = row["billing_hours"]
            if hours is None or hours <= 0 or observed is None or observed > hours + 1 / 3600:
                observed = None
            row[prefix + "_observed_hours"] = observed
            row[prefix + "_coverage_pct"] = min(100.0, observed / hours * 100) if observed is not None else None
        for key, prefix in (("delivered_th_h", "delivery"), ("power_kwh", "power"), ("uptime_pct", "uptime")):
            if source == "observed" and not row[prefix + "_observed_hours"]:
                row[key] = None
        h, obs = row["hashrate_hours"], row["observed_hours"]
        if h and obs is not None:
            if obs > h + 1 / 3600:
                row["observed_hours"] = row["coverage_pct"] = row["avg_hashrate_ths"] = None
            else:
                row["coverage_pct"] = min(100.0, obs / h * 100)
        for key in PERIOD_FIELDS:
            row[key] = measurement(c.get(key), int, hi=253402300799)
        for prefix, hours_key in (("period", "hashrate_hours"), ("billing_period", "billing_hours")):
            start, end = row[prefix + "_start_ts"], row[prefix + "_end_ts"]
            if start is not None and end is not None and (not start or end <= start or
                    (row[hours_key] and abs((end - start) - row[hours_key] * 3600) > 1)):
                row[prefix + "_start_ts"] = row[prefix + "_end_ts"] = None
                if prefix == "period":
                    row["avg_hashrate_ths"] = None
                else:
                    row["power_kwh"] = row["delivered_th_h"] = row["uptime_pct"] = None
        out.append(row)
    return out


def customer_report_period(src, rows, now):
    """全场报表窗口证据。空列表只能由实际查询的顶层端点证明，不用接收时间补造。"""
    provided = {k: measurement(src.get(k), int, hi=253402300799)
                for k in PERIOD_FIELDS if k in src}
    if any(value is None for value in provided.values()):
        raise ValueError("客户报表顶层时段端点非法")
    if rows:
        row_windows = [tuple(row.get(k) for k in PERIOD_FIELDS) for row in rows]
        windows = {values for values in row_windows if all(value is not None for value in values)}
        if len(windows) > 1:
            raise ValueError("客户报表各行统计时段不一致")
        period = dict(zip(PERIOD_FIELDS, next(iter(windows)))) if windows else None
        if period and any(period[key] != value for key, value in provided.items()):
            raise ValueError("客户报表顶层和明细时段不一致")
        if not windows or any(any(value is None for value in values) for values in row_windows):
            if len(provided) == len(PERIOD_FIELDS):
                raise ValueError("客户报表行缺少顶层声明的时段")
            return None  # 旧协议只能作为未验证报表，不能盖过已验证的新报表。
    elif len(provided) == len(PERIOD_FIELDS):
        period = provided
        hours = measurement(src.get("hours"), hi=24 * 90)
        if not hours or any(abs(period[prefix + "_end_ts"] - period[prefix + "_start_ts"] - hours * 3600) > 1
                            for prefix in ("period", "billing_period")):
            raise ValueError("空客户报表时段与请求小时数不一致")
    else:
        return None
    if not all(0 < period[prefix + "_start_ts"] < period[prefix + "_end_ts"] <= now + 60
               for prefix in ("period", "billing_period")):
        return None  # 未来或无效源时钟不能建立“最新可信窗口”水位。
    return period


def customer_hashrates(rows, limit=5000):
    """完整实时客户列表；格式错误必须拒绝，不能把坏消息误认为空场地。"""
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("customer_hashrates 非法")
    out, seen = [], set()
    for c in rows:
        if not isinstance(c, dict):
            raise ValueError("customer_hashrates 行非法")
        worker = text(c.get("worker"), 128) or "(未知)"
        if worker in seen:
            raise ValueError("customer_hashrates 客户重复")
        seen.add(worker)
        out.append({"worker": worker,
                    "machines": measurement(c.get("machines"), int, hi=10**6),
                    "hashrate_ths": measurement(c.get("hashrate_ths"), hi=10**9),
                    "hashrate_ts": measurement(c.get("hashrate_ts"), int, hi=253402300799)})
    return out


def customer_snapshot(src, now):
    """可选完整扫描：缺字段兼容旧端；坏字段拒绝；时钟漂移不能锁死后续的实时扫描。"""
    if "customer_hashrates" not in src:
        return None
    rows = customer_hashrates(src["customer_hashrates"])
    if src.get("scan_age_s") is not None:
        age = measurement(src["scan_age_s"], int, hi=10**7)
        if age is None or age >= now:
            raise ValueError("customer_hashrates scan_age_s 非法")
    scan_ts = scan_timestamp(src, now)
    raw_scan_ts = measurement(src.get("scan_ts"), int)
    if not raw_scan_ts and src.get("scan_age_s") is not None:
        sent_ts = measurement(src.get("ts"), int)
        age = measurement(src.get("scan_age_s"), int)
        if sent_ts and age is not None and sent_ts > age:
            raw_scan_ts = sent_ts - age
    for original, row in zip(src["customer_hashrates"], rows):
        if "hashrate_ts" not in original:
            row["hashrate_ts"] = scan_ts  # 旧协议只有整轮时间。
            continue
        measured = row["hashrate_ts"]
        # 保留每客户最早实际采样相对整轮时间的差，不能把慢扫描/保留记录刷新成年轻读数。
        if not scan_ts or not measured or not raw_scan_ts or measured > raw_scan_ts + 60:
            row["hashrate_ts"] = None
        else:
            adjusted = scan_ts - max(0, raw_scan_ts - measured)
            row["hashrate_ts"] = adjusted if adjusted > 0 else None
    return rows, scan_ts


def scan_timestamp(src, now):
    """源时钟保守归一：传输延迟只能让读数变旧，不能用 now-age 将旧包刷新到现在。"""
    raw = measurement(src.get("scan_ts"), int, hi=253402300799)
    sent = measurement(src.get("ts"), int, hi=253402300799)
    age = measurement(src.get("scan_age_s"), int, hi=10**7)
    if not raw and sent and age is not None and sent > age:
        raw = sent - age
    if not raw or raw > now + 60:
        return None
    if "ts" in src and (not sent or sent > now + 60):
        return None
    stamp = min(raw, now)
    if age is not None:
        stamp = min(stamp, now - age)
        if sent:
            stamp = min(stamp, now - age - max(0, now - sent))
    return stamp if stamp > 0 else None


def source_time_fields(src):
    return {"source_scan_ts": measurement(src.get("scan_ts"), int, hi=253402300799),
            "source_sent_ts": measurement(src.get("ts"), int, hi=253402300799),
            "measurement_time_supported": all(k in src for k in ("scan_ts", "ts", "measurement_ts"))}


def measurement_timestamp(src, scan_ts):
    """用原始整轮时间的相对差校正最早单机采样，不延长该读数的有效寿命。"""
    measured = measurement(src.get("measurement_ts"), int, hi=253402300799)
    raw_scan = measurement(src.get("scan_ts"), int, hi=253402300799)
    if not raw_scan:
        sent = measurement(src.get("ts"), int, hi=253402300799)
        age = measurement(src.get("scan_age_s"), int)
        if sent and age is not None and sent > age:
            raw_scan = sent - age
    if not measured or not scan_ts or not raw_scan or measured > raw_scan + 60:
        return None
    corrected = scan_ts - max(0, raw_scan - measured)
    return corrected if corrected > 0 else None


def site_key(v, maxlen=64):
    """site_id / 场地名：去控制字符后 1..maxlen 字符；非法返回空串。"""
    s = text(v, maxlen + 1)
    return s if 0 < len(s) <= maxlen else ""
