#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端总览 SQLite 存储（schema v2：一切按稳定 site_id 归档，场地名只是显示标签）。

- sites          全部已知场地(推送首报自动注册；拉取场地由 collector 启动时登记)
- snapshots      分钟级快照(每场地每次拉取/上报一行；拉取失败也记 ok=0 行)
- site_customers 每场地客户报表最新一份(整表替换，历史对账在场地本地)
- site_alert_summary 每场地活跃告警摘要最新一份(按类型分组，标客户不带单机IP)
- daily          日级汇总(永久保留，长期趋势/月度对比用)
- notify_log     告警通知发送记录(排查"为什么没收到")
- audit_log      安全审计(登录成败/改设置/改密码/触发更新/删场地，保留180天)
写入由模块级锁串行化(单连接跨线程共享)；旧库(v1, 按场地名)启动时自动迁移。
"""
import datetime
import json
import sqlite3
import threading
from contextlib import contextmanager
import time
import validate

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,                 -- 拉取/上报入库时间(unix秒, 云端时钟)
  site_id TEXT NOT NULL,
  ok INTEGER NOT NULL DEFAULT 0,
  online INTEGER, total INTEGER,
  hashrate_ths REAL, power_kw REAL,
  active_alerts INTEGER,
  containers INTEGER, containers_faulty INTEGER, containers_offline INTEGER, containers_unknown INTEGER,
  scan_age_s INTEGER,                  -- 场地本地上次扫描龄期(判「监控停滞」)
  scan_ts INTEGER,
  source_scan_ts INTEGER, source_sent_ts INTEGER,
  measurement_ts INTEGER, measurement_time_supported INTEGER,
  unknown INTEGER,
  hashrate_complete INTEGER, power_complete INTEGER,
  hashrate_known_machines INTEGER, power_known_machines INTEGER,
  known_hashrate_ths REAL, known_power_kw REAL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_site_ts ON snapshots(site_id, ts);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS site_customers(
  site_id TEXT NOT NULL,
  worker TEXT NOT NULL,
  machines INTEGER,
  uptime_pct REAL,
  delivered_th_h REAL,
  power_kwh REAL,
  hashrate_ths REAL,                   -- 最新扫描的实际算力合计(TH/s)，未知为 NULL
  avg_hashrate_ths REAL,               -- 已观测时间的加权平均(TH/s)，不是累计 TH·h
  hashrate_hours REAL,                 -- 平均值请求窗口；不能拿报表的旧截断时长替代
  observed_hours REAL,                -- 平均值实际覆盖的时长(非机器小时)
  coverage_pct REAL,
  hashrate_ts INTEGER,                -- 场地扫描时间(不是云端收到报表的时间)
  current_machines INTEGER,
  hours INTEGER,
  updated_ts INTEGER,
  PRIMARY KEY(site_id, worker)
);

-- 轻量实时算力随摘要每分钟同步，与每十分钟一次的历史报表分开，避免慢报表覆盖新扫描。
CREATE TABLE IF NOT EXISTS site_customer_current(
  site_id TEXT NOT NULL,
  worker TEXT NOT NULL,
  machines INTEGER,
  hashrate_ths REAL,
  hashrate_ts INTEGER,
  PRIMARY KEY(site_id, worker)
);
CREATE TABLE IF NOT EXISTS site_customer_current_state(
  site_id TEXT PRIMARY KEY,
  scan_ts INTEGER NOT NULL,
  updated_ts INTEGER NOT NULL
);

-- 独立全场报表水位；合法空报表也保留时段，迟到旧包不能复活或清掉新客户。
CREATE TABLE IF NOT EXISTS site_customer_report_state(
  site_id TEXT PRIMARY KEY,
  period_start_ts INTEGER NOT NULL, period_end_ts INTEGER NOT NULL,
  billing_period_start_ts INTEGER NOT NULL, billing_period_end_ts INTEGER NOT NULL,
  updated_ts INTEGER NOT NULL
);

-- 场地活跃告警摘要最新一份(整表替换)：按类型分组，不含单机IP，只标受影响客户+台数，
-- 给"老板看一眼就知道影响了谁"用；场地本地不推该字段时保持上次已知内容不动。
CREATE TABLE IF NOT EXISTS site_alert_summary(
  site_id TEXT NOT NULL,
  category TEXT NOT NULL,
  label TEXT,
  count INTEGER,
  since_ts INTEGER,
  workers_json TEXT,
  updated_ts INTEGER,
  PRIMARY KEY(site_id, category)
);

-- 场地注册表：id 稳定唯一(本地端生成)，name 可随时改(改名=改标签，数据不串)
CREATE TABLE IF NOT EXISTS sites(
  id TEXT PRIMARY KEY,
  name TEXT,
  type TEXT DEFAULT 'air',             -- air=风冷 | hydro=水冷(以最近一次上报/配置为准)
  created_ts INTEGER,
  last_push_ts INTEGER
);

-- 云端显示名及删除标记与上报数据分开；旧终端重报不会复活已删除场地。
CREATE TABLE IF NOT EXISTS site_management(
  site_id TEXT PRIMARY KEY,
  name_override TEXT,
  deleted_name TEXT,
  deleted_ts INTEGER NOT NULL DEFAULT 0
);

-- 日级汇总(UTC+8 切日, 永久保留)。power_kwh 为 Σ(功率×名义间隔) 估算，精确对账在场地本地。
CREATE TABLE IF NOT EXISTS daily(
  site_id TEXT NOT NULL,
  day TEXT NOT NULL,                   -- 'YYYY-MM-DD'
  samples INTEGER,
  avg_hashrate_ths REAL, max_hashrate_ths REAL,
  avg_online REAL, avg_total REAL,
  avg_power_kw REAL, power_kwh REAL,
  PRIMARY KEY(site_id, day)
);

-- v2 使用真实观测区间；旧 daily 估算账本保留，不原地改写。
CREATE TABLE IF NOT EXISTS daily_observed_v2(
  site_id TEXT NOT NULL, day TEXT NOT NULL, samples INTEGER,
  avg_hashrate_ths REAL, max_hashrate_ths REAL,
  avg_online REAL, avg_total REAL, avg_power_kw REAL, power_kwh REAL,
  elapsed_hours REAL, observed_hours REAL, coverage_pct REAL,
  hashrate_observed_hours REAL, machines_observed_hours REAL, power_observed_hours REAL,
  method TEXT NOT NULL DEFAULT 'time_weighted_v2',
  PRIMARY KEY(site_id, day)
);

CREATE TABLE IF NOT EXISTS notify_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  site_id TEXT,
  type TEXT,                           -- unreachable|stalled|recovered|hashrate_drop|hashrate_recovered
  msg TEXT,
  ok INTEGER                           -- 是否至少一个通道发送成功
);

-- 安全审计：谁、从哪个IP、做了什么(登录失败/改设置/改密码/更新/删场地)。
-- 被入侵后第一件事就是查这张表；绝不写入密码/token 等敏感值。
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  user TEXT,
  ip TEXT,
  action TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""

_SNAP_FIELDS = ("ts", "site_id", "ok", "online", "total", "hashrate_ths", "power_kw",
                "active_alerts", "containers", "containers_faulty",
                "containers_offline", "scan_age_s", "error")
_SNAP_FIELDS += ("scan_ts", "unknown", "hashrate_complete", "power_complete")
_SNAP_FIELDS += ("hashrate_known_machines", "power_known_machines", "known_hashrate_ths", "known_power_kw")
_SNAP_FIELDS += ("measurement_ts", "measurement_time_supported", "containers_unknown")
_SNAP_FIELDS += ("source_scan_ts", "source_sent_ts")


def _migrate_v1(conn):
    """v1(按场地名) → v2(按site_id)：列改名 + sites 表重建(旧名同时当 id 和 name)。"""
    def cols(t):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
    snap = cols("snapshots")
    if "site" in snap and "site_id" not in snap:
        conn.execute("ALTER TABLE snapshots RENAME COLUMN site TO site_id")
    sc = cols("site_customers")
    if "site" in sc and "site_id" not in sc:
        conn.execute("ALTER TABLE site_customers RENAME COLUMN site TO site_id")
    st = cols("sites")
    if st and "id" not in st:            # 旧版 sites: name 主键
        conn.execute("ALTER TABLE sites RENAME TO _sites_v1")
        conn.execute("CREATE TABLE sites(id TEXT PRIMARY KEY, name TEXT, "
                     "type TEXT DEFAULT 'air', created_ts INTEGER, last_push_ts INTEGER)")
        conn.execute("INSERT INTO sites(id,name,type,created_ts,last_push_ts) "
                     "SELECT name,name,type,created_ts,last_push_ts FROM _sites_v1")
        conn.execute("DROP TABLE _sites_v1")
    conn.commit()


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _migrate_v1(conn)
    conn.executescript(SCHEMA)
    # 旧报表只包含累计算力，不能用累计值伪造实时/平均算力；新增列保持 NULL。
    customer_cols = {r[1] for r in conn.execute("PRAGMA table_info(site_customers)")}
    for column, kind in (("hashrate_ths", "REAL"), ("avg_hashrate_ths", "REAL"),
                         ("hashrate_hours", "REAL"), ("observed_hours", "REAL"),
                         ("coverage_pct", "REAL"), ("hashrate_ts", "INTEGER"),
                         ("current_machines", "INTEGER")):
        if column not in customer_cols:
            conn.execute(f"ALTER TABLE site_customers ADD COLUMN {column} {kind}")
    for table, additions in (("snapshots", (("scan_ts", "INTEGER"), ("unknown", "INTEGER"),
                              ("hashrate_complete", "INTEGER"), ("power_complete", "INTEGER"),
                              ("hashrate_known_machines", "INTEGER"), ("power_known_machines", "INTEGER"),
                              ("known_hashrate_ths", "REAL"), ("known_power_kw", "REAL"),
                              ("measurement_ts", "INTEGER"), ("measurement_time_supported", "INTEGER"),
                              ("source_scan_ts", "INTEGER"), ("source_sent_ts", "INTEGER"),
                              ("containers_unknown", "INTEGER"))),):
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in additions:
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.execute("UPDATE snapshots SET scan_ts=ts-scan_age_s WHERE scan_ts IS NULL "
                 "AND typeof(scan_age_s) IN ('integer','real') AND scan_age_s>=0 AND ts>scan_age_s")
    for column in validate.REPORT_FIELDS:
        if column not in customer_cols:
            conn.execute(f"ALTER TABLE site_customers ADD COLUMN {column} " +
                         ("TEXT" if column in ("billing_source", "machines_source") else "REAL"))
    conn.commit()
    return conn


def insert_snapshot(conn, snap):
    row = [snap.get(f) for f in _SNAP_FIELDS]
    with _lock:
        if snap.get("ok") and snap.get("scan_ts"):
            previous = conn.execute("SELECT scan_ts,source_scan_ts,source_sent_ts FROM snapshots WHERE site_id=? AND ok=1 "
                                    "AND scan_ts IS NOT NULL ORDER BY id DESC LIMIT 1",
                                    (snap["site_id"],)).fetchone()
            if previous and previous[0] > snap["scan_ts"]:
                return False
            if previous and any(snap.get(k) is not None and previous[k] is not None and
                                snap[k] < previous[k] for k in ("source_scan_ts", "source_sent_ts")):
                return False
        conn.execute(
            f"INSERT INTO snapshots({','.join(_SNAP_FIELDS)}) "
            f"VALUES({','.join('?' * len(_SNAP_FIELDS))})", row)
        conn.commit()
    return True


def _rows_to_map(rows):
    return {r["site_id"]: dict(r) for r in rows}


def latest_snapshots(conn):
    """每场地最新一条(不论成败)——拉取模式判可达性用。"""
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE id IN (SELECT MAX(id) FROM snapshots GROUP BY site_id)"
    ).fetchall()
    return _rows_to_map(rows)


def latest_ok_snapshots(conn):
    """每场地最新一条成功快照——场地暂时失联时卡片仍显示最后已知数据。"""
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE id IN "
        "(SELECT MAX(id) FROM snapshots WHERE ok=1 GROUP BY site_id)"
    ).fetchall()
    return _rows_to_map(rows)


MAX_OBSERVATION_GAP = 300
MAX_MEASUREMENT_AGE = 900


def scan_age(snap, now):
    stamp = validate.measurement(snap.get("scan_ts"), int)
    if stamp and stamp <= now + 300:
        return max(0, now - stamp)
    age = validate.measurement(snap.get("scan_age_s"), int)
    received = validate.measurement(snap.get("ts"), int)
    if age is not None and received and received <= now + 300:
        return age + max(0, now - received)
    return None


def _snapshot_freshness(snap, now, stalled_after):
    age = scan_age(snap, now)
    fresh_scan = bool(snap.get("ok") and age is not None and age <= stalled_after)
    measured = validate.measurement(snap.get("measurement_ts"), int)
    fresh = bool(fresh_scan and snap.get("measurement_time_supported") == 1 and measured and
                 0 <= now - measured <= MAX_MEASUREMENT_AGE)
    return fresh_scan, fresh


def snapshot_quality(snap, now, stalled_after=1200):
    """完整性与新鲜度分开；缺新协议证据不能默认成完整场地。"""
    fresh_scan, fresh = _snapshot_freshness(snap, now, stalled_after)
    online = validate.measurement(snap.get("online"))
    total = validate.measurement(snap.get("total"))
    machines = (fresh and snap.get("unknown") == 0 and online is not None and
                total is not None and online <= total)
    out = {"online": bool(machines), "total": bool(machines),
           "hashrate_ths": bool(fresh and snap.get("hashrate_complete") == 1 and
                                validate.measurement(snap.get("hashrate_ths")) is not None),
           "power_kw": bool(fresh and snap.get("power_complete") == 1 and
                            validate.measurement(snap.get("power_kw")) is not None)}
    for prefix, field in (("hashrate", "hashrate_ths"), ("power", "power_kw")):
        known_count = snap.get(prefix + "_known_machines")
        if (snap.get("unknown") is not None and snap["unknown"] > 0) or (
                known_count is not None and known_count != total):
            out[field] = False
    out["active_alerts"] = bool(fresh_scan and validate.measurement(snap.get("active_alerts")) is not None)
    containers = validate.measurement(snap.get("containers"), int)
    unknown = validate.measurement(snap.get("containers_unknown"), int)
    container_evidence = (fresh_scan and containers is not None and unknown is not None and unknown <= containers)
    out["containers"] = out["containers_unknown"] = bool(container_evidence)
    faulty = validate.measurement(snap.get("containers_faulty"), int)
    offline = validate.measurement(snap.get("containers_offline"), int)
    counts_valid = (faulty is not None and offline is not None and containers is not None and
                    faulty + offline <= containers)
    for key in ("containers_faulty", "containers_offline"):
        out[key] = bool(container_evidence and unknown == 0 and counts_valid)
    return out


def snapshot_observed(snap, now, stalled_after=1200):
    """当前可用读数；已知小计可展示，但不能冒充完整场地或用于完整耗电账。"""
    quality = snapshot_quality(snap, now, stalled_after)
    out = {key: snap.get(key) if complete else None for key, complete in quality.items()}
    _fresh_scan, fresh = _snapshot_freshness(snap, now, stalled_after)
    total = validate.measurement(snap.get("total"), int)
    unknown = validate.measurement(snap.get("unknown"), int)
    if not fresh or total is None or unknown is None or unknown > total:
        return out
    for prefix, field in (("hashrate", "hashrate_ths"), ("power", "power_kw")):
        if out[field] is not None or snap.get(prefix + "_complete") not in (0, 1):
            continue
        count = validate.measurement(snap.get(prefix + "_known_machines"), int)
        value = validate.measurement(snap.get("known_" + field))
        # A positive number of observed machines proves that a zero subtotal is
        # a real reading. Zero machines with missing readings cannot prove zero.
        if count is not None and 0 < count <= total - unknown and value is not None:
            out[field] = value
    return out


def _observation_intervals(conn, begin, end, site_id=None, stalled_after=1200):
    """左值保持，真实时长加权；失败/重复/午夜/过期/当前时刻都明确切断区间。"""
    args = [begin - MAX_OBSERVATION_GAP, end]
    query = "SELECT * FROM snapshots WHERE ts>=? AND ts<=?"
    if site_id is not None:
        query += " AND site_id=?"
        args.append(site_id)
    query += " ORDER BY site_id,ts,id"

    def segment(row, following):
        start = max(begin, row["ts"])
        stop = min(end, following, row["ts"] + MAX_OBSERVATION_GAP)
        age = scan_age(row, row["ts"])
        if age is not None:
            stop = min(stop, row["ts"] + max(0, stalled_after - age))
        measured = validate.measurement(row.get("measurement_ts"), int)
        if row.get("measurement_time_supported") == 1 and measured:
            stop = min(stop, measured + MAX_MEASUREMENT_AGE)
        quality = snapshot_quality(row, row["ts"], stalled_after)
        return row, start, max(start, stop), quality

    with _lock:
        previous = None
        for raw in conn.execute(query, args):
            row = dict(raw)
            if previous is not None:
                same_site = row["site_id"] == previous["site_id"]
                if same_site and row["ts"] == previous["ts"]:
                    previous = row  # 同一接收秒最后一条为准，不重复增加时长或权重。
                    continue
                yield segment(previous, row["ts"] if same_site else end)
            previous = row
        if previous is not None:
            yield segment(previous, end)


def trend(conn, site_id, hours=24, points=288, stalled_after=1200):
    """按真实观测秒数降采样，未知/失败区间为 null，不能画成零算力。"""
    hours = max(1, min(int(hours), 24 * 90))
    points = max(24, min(int(points), 2000))
    bucket = max(60, int(hours * 3600 / points))
    now = int(time.time())
    begin = now - hours * 3600
    keys = ("online", "total", "hashrate_ths", "power_kw", "active_alerts")
    bins = {}
    for row, left, right, quality in _observation_intervals(conn, begin, now, site_id, stalled_after):
        while left <= right:
            stamp = left // bucket * bucket
            stop = min(right, stamp + bucket)
            slot = bins.setdefault(stamp, {key: [0.0, 0, None] for key in
                                           (*keys, *("legacy_" + k for k in keys))})
            for key in keys:
                value = validate.measurement(row.get(key))
                if quality[key] and value is not None:
                    slot[key][0] += value * (stop - left)
                    slot[key][1] += stop - left
                    if stop > left or row["ts"] == left:
                        slot[key][2] = value
                flag = {"hashrate_ths": "hashrate_complete", "power_kw": "power_complete",
                        "online": "unknown", "total": "unknown"}.get(key)
                legacy_evidence = row.get(flag) is None or not row.get("measurement_time_supported")
                if row.get("ok") and flag and legacy_evidence and value is not None:
                    legacy = slot["legacy_" + key]
                    legacy[0] += value * (stop - left)
                    legacy[1] += stop - left
                    if stop > left or row["ts"] == left:
                        legacy[2] = value
            if stop >= right:
                break
            left = stop
    result = []
    # 没有快照的长缺口也必须有 null 点，否则折线会直接连接缺口两端，伪装连续观测。
    for stamp in range(begin // bucket * bucket, now // bucket * bucket + 1, bucket):
        slot = bins.get(stamp, {key: [0.0, 0, None] for key in
                                (*keys, *("legacy_" + k for k in keys))})
        point = {"ts": stamp}
        for key, (integral, duration, instant) in slot.items():
            point[key] = round(integral / duration, 1) if duration else instant
        result.append(point)
    return result


def avg_hashrate(conn, site_id, t1, t2, stalled_after=1200):
    """已观测时段的时间加权平均，告警不把缺测当零。"""
    integral = duration = 0.0
    for row, left, right, quality in _observation_intervals(conn, t1, min(t2, int(time.time())), site_id, stalled_after):
        if quality["hashrate_ths"] and right > left:
            integral += row["hashrate_ths"] * (right - left)
            duration += right - left
    return integral / duration if duration else None


def replace_customers(conn, site_id, customers, hours, period=None):
    now = int(time.time())
    period = validate.customer_report_period({"hours": hours, **(period or {})}, customers, now)
    extra = [k for k in validate.REPORT_FIELDS if k not in ("delivered_th_h", "power_kwh", "uptime_pct")]
    with _lock, conn:
        previous = conn.execute("SELECT * FROM site_customer_report_state WHERE site_id=?", (site_id,)).fetchone()
        if previous and (period is None or any(period[k] < previous[k]
                         for k in ("period_end_ts", "billing_period_end_ts"))):
            return False
        conn.execute("DELETE FROM site_customers WHERE site_id=?", (site_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO site_customers"
            "(site_id,worker,machines,uptime_pct,delivered_th_h,power_kwh,"
            "hashrate_ths,avg_hashrate_ths,hashrate_hours,observed_hours,coverage_pct,"
            "hashrate_ts,current_machines,hours,updated_ts," + ",".join(extra) + ")"
            " VALUES(" + ",".join("?" for _ in range(15 + len(extra))) + ")",
            [(site_id, str(c.get("worker") or "(未知)")[:128], c.get("machines") or 0,
              c.get("uptime_pct"), c.get("delivered_th_h"), c.get("power_kwh"),
              c.get("hashrate_ths"), c.get("avg_hashrate_ths"), c.get("hashrate_hours"),
              c.get("observed_hours"), c.get("coverage_pct"), c.get("hashrate_ts"),
              c.get("current_machines"),
              hours, now, *(c.get(k) for k in extra)) for c in customers])
        if period is not None:
            conn.execute("INSERT INTO site_customer_report_state VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT(site_id) DO UPDATE SET period_start_ts=excluded.period_start_ts,"
                         "period_end_ts=excluded.period_end_ts,billing_period_start_ts=excluded.billing_period_start_ts,"
                         "billing_period_end_ts=excluded.billing_period_end_ts,updated_ts=excluded.updated_ts",
                         (site_id, *(period[k] for k in validate.PERIOD_FIELDS), now))
    return True


def replace_customer_hashrates(conn, site_id, rows, scan_ts):
    """替换一轮完整扫描的客户实时值；旧扫描不能倒退/复活已离场客户，空列表也是有效扫描。"""
    if not scan_ts:
        return False
    now = int(time.time())
    with _lock:
        old = conn.execute("SELECT scan_ts FROM site_customer_current_state WHERE site_id=?",
                           (site_id,)).fetchone()
        if old and old["scan_ts"] > scan_ts:
            return False
        conn.execute("DELETE FROM site_customer_current WHERE site_id=?", (site_id,))
        conn.executemany(
            "INSERT INTO site_customer_current(site_id,worker,machines,hashrate_ths,hashrate_ts)"
            " VALUES(?,?,?,?,?)",
            [(site_id, c["worker"], c.get("machines"), c.get("hashrate_ths"),
              c.get("hashrate_ts") if "hashrate_ts" in c else scan_ts) for c in rows])
        conn.execute("INSERT INTO site_customer_current_state(site_id,scan_ts,updated_ts)"
                     " VALUES(?,?,?) ON CONFLICT(site_id) DO UPDATE SET "
                     "scan_ts=excluded.scan_ts,updated_ts=excluded.updated_ts", (site_id, scan_ts, now))
        conn.commit()
    return True


def replace_alert_summary(conn, site_id, groups):
    """整表替换该场地的活跃告警摘要(与 replace_customers 同一模式)。groups 已由调用方
    校验/钳制过字段，这里只再兜底截断一次，防止异常输入把库撑大。"""
    now = int(time.time())
    with _lock:
        conn.execute("DELETE FROM site_alert_summary WHERE site_id=?", (site_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO site_alert_summary"
            "(site_id,category,label,count,since_ts,workers_json,updated_ts)"
            " VALUES(?,?,?,?,?,?,?)",
            [(site_id, str(g.get("category") or "?")[:64], str(g.get("label") or "")[:64],
              g.get("count") or 0, g.get("since_ts"),
              json.dumps(g.get("workers") or [], ensure_ascii=False)[:4000], now)
             for g in (groups or [])[:30]])
        conn.commit()


def get_alert_summary(conn, site_id):
    """该场地当前活跃告警摘要，按受影响台数降序。场地从未推送过该字段时返回 []。"""
    rows = conn.execute(
        "SELECT category, label, count, since_ts, workers_json FROM site_alert_summary"
        " WHERE site_id=? ORDER BY count DESC", (site_id,)).fetchall()
    out = []
    for r in rows:
        try:
            workers = json.loads(r["workers_json"] or "[]")
        except (TypeError, ValueError):
            workers = []
        out.append({"category": r["category"], "label": r["label"], "count": r["count"],
                    "since_ts": r["since_ts"], "workers": workers})
    return out


def customers(conn, site_id=None):
    """历史报表叠加独立完整扫描；首份历史报表到达前也能看到实时客户。"""
    q = ("SELECT c.*, COALESCE(NULLIF(m.name_override, ''), s.name, c.site_id) AS site_name"
         " FROM site_customers c LEFT JOIN sites s ON s.id = c.site_id"
         " LEFT JOIN site_management m ON m.site_id = c.site_id")
    live_q = q.replace("site_customers c", "site_customer_current c")
    where, args = (" WHERE c.site_id=?", (site_id,)) if site_id else ("", ())
    with _lock:
        rows = conn.execute(q + where, args).fetchall()
        current = conn.execute(live_q + where, args).fetchall()
        states = conn.execute("SELECT * FROM site_customer_current_state" +
                              (" WHERE site_id=?" if site_id else ""), args).fetchall()
    state_map = {r["site_id"]: dict(r) for r in states}
    out = {(r["site_id"], r["worker"]): dict(r) for r in rows}
    current_keys = {(r["site_id"], r["worker"]) for r in current}

    def machine_count(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    for key, r in out.items():
        r["hashrate_updated_ts"] = r.get("updated_ts")
        r["hashrate_source"] = "report" if r.get("hashrate_ts") else "legacy"
        state = state_map.get(r["site_id"])
        if state and key not in current_keys:
            # 完整新扫描里没有该客户：当前无机器，不能把历史机器/算力当作现在仍在运行。
            r.update(current_machines=0, hashrate_ths=0.0, hashrate_ts=state["scan_ts"],
                     hashrate_updated_ts=state["updated_ts"], hashrate_source="summary")
    for c in current:
        key = (c["site_id"], c["worker"])
        r = out.setdefault(key, {"site_id": c["site_id"], "worker": c["worker"],
                                "site_name": c["site_name"], "machines": c["machines"] or 0,
                                "uptime_pct": None, "delivered_th_h": None, "power_kwh": None,
                                "hours": None, "updated_ts": None, "avg_hashrate_ths": None,
                                "hashrate_hours": None, "observed_hours": None, "coverage_pct": None})
        # 摘要是完整且独立排序的实时通道。慢报表使用场地时钟，不能与云端校准后的
        # 摘要时间直接比大小，否则仅两分钟的时钟偏差就能用旧报表覆盖新扫描。
        state = state_map[c["site_id"]]
        r.update(current_machines=c["machines"], hashrate_ths=c["hashrate_ths"],
                 hashrate_ts=c["hashrate_ts"], hashrate_updated_ts=state["updated_ts"],
                 hashrate_source="summary")
    return sorted(out.values(), key=lambda r: (r["site_name"], -machine_count(r.get("machines"))))


def customer_count_by_site(conn):
    with _lock:
        rows = conn.execute(
            "SELECT site_id, COUNT(*) AS n FROM (SELECT site_id,worker FROM site_customers "
            "UNION SELECT site_id,worker FROM site_customer_current) GROUP BY site_id").fetchall()
    return {r["site_id"]: r["n"] for r in rows}


# ---- 场地注册 ----
class SiteDeletedError(ValueError):
    pass


def site_management(conn):
    return {r["site_id"]: dict(r) for r in conn.execute("SELECT * FROM site_management")}


@contextmanager
def site_write(conn, site_id):
    """串行化删除和整轮上报，防止已通过鉴权的在途上报复活被删场地。"""
    with _lock:
        row = conn.execute("SELECT deleted_ts FROM site_management WHERE site_id=?",
                           (site_id,)).fetchone()
        if row and row["deleted_ts"]:
            raise SiteDeletedError("场地已删除，请管理员恢复后再上报")
        yield


def rename_site(conn, site_id, name):
    with site_write(conn, site_id), conn:
        conn.execute("INSERT INTO site_management(site_id, name_override) VALUES(?,?) "
                     "ON CONFLICT(site_id) DO UPDATE SET name_override=excluded.name_override",
                     (site_id, name or None))


def restore_site(conn, site_id):
    with _lock, conn:
        row = conn.execute("SELECT deleted_ts FROM site_management WHERE site_id=?",
                           (site_id,)).fetchone()
        if not row or not row["deleted_ts"]:
            return False
        conn.execute("UPDATE site_management SET deleted_ts=0 WHERE site_id=?", (site_id,))
        return True


def upsert_site(conn, site_id, name, site_type, now):
    """场地上报/登记。name/site_type 传 None 表示不更新该字段。"""
    sets = ["last_push_ts=excluded.last_push_ts"]
    if name:
        sets.append("name=excluded.name")
    if site_type:
        sets.append("type=excluded.type")
    with site_write(conn, site_id):
        conn.execute(
            "INSERT INTO sites(id, name, type, created_ts, last_push_ts) VALUES(?,?,?,?,?) "
            f"ON CONFLICT(id) DO UPDATE SET {', '.join(sets)}",
            (site_id, name or site_id, site_type or "air", now, now))
        conn.commit()


def push_sites(conn):
    """全部已注册场地。按 created_ts 排序保证图例颜色稳定(新场地只追加在最后)。"""
    rows = conn.execute("SELECT * FROM sites ORDER BY created_ts, id").fetchall()
    return [dict(r) for r in rows]


def delete_site(conn, site_id, name=None):
    """删除场地及其全部数据(场地下线后清理)。"""
    with _lock, conn:
        conn.execute("INSERT INTO site_management(site_id, deleted_name, deleted_ts) "
                     "VALUES(?,?,?) ON CONFLICT(site_id) DO UPDATE SET "
                     "deleted_name=excluded.deleted_name, deleted_ts=excluded.deleted_ts",
                     (site_id, name or site_id, int(time.time())))
        for t in ("sites", "snapshots", "site_customers", "site_customer_current",
                  "site_customer_current_state", "site_customer_report_state", "site_alert_summary",
                  "daily", "daily_observed_v2", "notify_log"):
            conn.execute(f"DELETE FROM {t} WHERE {'id' if t == 'sites' else 'site_id'}=?",
                         (site_id,))


# ---- 清理(分批, 短锁) ----
def cleanup(conn, retention_days):
    cutoff = int(time.time()) - int(retention_days) * 86400
    while True:
        with _lock:
            cur = conn.execute(
                "DELETE FROM snapshots WHERE id IN "
                "(SELECT id FROM snapshots WHERE ts<? LIMIT 5000)", (cutoff,))
            conn.commit()
        if cur.rowcount < 5000:
            break
    with _lock:
        conn.execute("DELETE FROM notify_log WHERE ts<?", (int(time.time()) - 90 * 86400,))
        conn.execute("DELETE FROM audit_log WHERE ts<?", (int(time.time()) - 180 * 86400,))
        conn.commit()


# ---- 日级汇总(UTC+8 切日) ----
_TZ8 = 8 * 3600


def rollup_daily(conn, day_offsets=(0, 1), stalled_after=1200):
    """只写 v2 观测日报，旧 daily 估算账本原样保留。"""
    now = int(time.time())
    for off in day_offsets:
        day_start8 = (int((now + _TZ8) // 86400) - off) * 86400
        begin = day_start8 - _TZ8
        end = min(now, begin + 86400)
        if end < begin:
            continue
        day = datetime.datetime.utcfromtimestamp(day_start8).strftime("%Y-%m-%d")
        slots = {}
        for row, left, right, quality in _observation_intervals(conn, begin, end, stalled_after=stalled_after):
            dt = max(0, right - left)
            in_day = begin <= row["ts"] < begin + 86400 and row["ts"] <= now
            if not dt and not in_day:
                continue
            # 缺完整性证据的旧原始快照不能把旧日报自动升级为“可靠 v2”。
            supported = row.get("measurement_time_supported") == 1 and any(
                row.get(k) is not None for k in ("unknown", "hashrate_complete", "power_complete"))
            if not supported:
                continue
            slot = slots.setdefault(row["site_id"], {"samples": 0, "common": 0.0, "peak": None,
                **{key: [0.0, 0.0] for key in ("hashrate_ths", "online", "total", "power_kw")}})
            if in_day:
                slot["samples"] += 1
            if quality["hashrate_ths"] and (dt or in_day):
                value = validate.measurement(row.get("hashrate_ths"))
                slot["peak"] = value if slot["peak"] is None else max(slot["peak"], value)
            for key in ("hashrate_ths", "online", "total", "power_kw"):
                if quality[key] and dt:
                    slot[key][0] += validate.measurement(row[key]) * dt
                    slot[key][1] += dt
            if all(quality[k] for k in ("hashrate_ths", "online", "power_kw")):
                slot["common"] += dt
        elapsed = max(0, end - begin) / 3600.0
        rows = []
        for sid, slot in slots.items():
            mean = lambda key: slot[key][0] / slot[key][1] if slot[key][1] else None
            rows.append((sid, day, slot["samples"], mean("hashrate_ths"), slot["peak"],
                         mean("online"), mean("total"), mean("power_kw"),
                         slot["power_kw"][0] / 3600.0 if slot["power_kw"][1] else None,
                         elapsed, slot["common"] / 3600.0,
                         slot["common"] / (end - begin) * 100 if end > begin else 0.0,
                         slot["hashrate_ths"][1] / 3600.0, slot["online"][1] / 3600.0,
                         slot["power_kw"][1] / 3600.0, "time_weighted_v2"))
        with _lock, conn:
            conn.executemany("INSERT OR REPLACE INTO daily_observed_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def daily_report(conn, site_id, days=14):
    limit = max(1, min(int(days), 3650))
    with _lock:
        old = {r["day"]: dict(r) for r in conn.execute(
            "SELECT * FROM daily WHERE site_id=? ORDER BY day DESC LIMIT ?", (site_id, limit))}
        observed = {r["day"]: dict(r) for r in conn.execute(
            "SELECT * FROM daily_observed_v2 WHERE site_id=? ORDER BY day DESC LIMIT ?", (site_id, limit))}
    result = []
    for day in sorted(set(old) | set(observed), reverse=True)[:limit]:
        legacy = old.get(day)
        r = observed.get(day)
        if r is None:
            r = dict(legacy, method="legacy_sample_mean", elapsed_hours=None,
                     observed_hours=None, coverage_pct=None, hashrate_observed_hours=None,
                     machines_observed_hours=None, power_observed_hours=None)
        if legacy:
            for key in ("avg_hashrate_ths", "max_hashrate_ths", "avg_online", "avg_total", "avg_power_kw", "power_kwh"):
                r["legacy_" + key] = legacy[key]
        if r["method"] == "time_weighted_v2":
            # 中断数日后只重算今天/昨天，旧日的分母不能永远停在关机时刻而显示 100%。
            day_begin = int(datetime.datetime.strptime(day, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            r["elapsed_hours"] = min(24.0, max(0.0, (time.time() - day_begin) / 3600.0))
            r["coverage_pct"] = (min(100.0, r["observed_hours"] / r["elapsed_hours"] * 100)
                                  if r["elapsed_hours"] else 0.0)
        mapping = {"hashrate_ths": r["hashrate_observed_hours"], "online": r["machines_observed_hours"],
                   "total": r["machines_observed_hours"], "power_kw": r["power_observed_hours"]}
        r["metric_observed_hours"] = mapping
        elapsed = r["elapsed_hours"]
        r["metric_coverage_pct"] = {key: min(100, value / elapsed * 100) if value is not None and elapsed else
                                   (0.0 if value == 0 and elapsed == 0 else None) for key, value in mapping.items()}
        result.append(r)
    return result


def notify_log_add(conn, ts, site_id, ntype, msg, ok):
    with _lock:
        conn.execute("INSERT INTO notify_log(ts, site_id, type, msg, ok) VALUES(?,?,?,?,?)",
                     (ts, site_id, ntype, msg, 1 if ok else 0))
        conn.commit()


# ---- 安全审计 ----
def audit_add(conn, user, ip, action, detail=""):
    with _lock:
        conn.execute("INSERT INTO audit_log(ts, user, ip, action, detail) VALUES(?,?,?,?,?)",
                     (int(time.time()), str(user or "")[:64], str(ip or "")[:64],
                      str(action)[:32], str(detail or "")[:300]))
        conn.commit()


def audit_recent(conn, limit=100):
    rows = conn.execute("SELECT ts, user, ip, action, detail FROM audit_log"
                        " ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 1000)),)).fetchall()
    return [dict(r) for r in rows]


def site_exists(conn, site_id):
    return conn.execute("SELECT 1 FROM sites WHERE id=?", (site_id,)).fetchone() is not None


def site_count(conn):
    # 单连接跨线程共享：COUNT 的 execute + fetchone 也必须与其它 COUNT/审计写入串行，
    # 否则并发复用 SQLite 语句时可能得到空行或错误计数，导致新场地接入失败。
    with _lock:
        return conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
