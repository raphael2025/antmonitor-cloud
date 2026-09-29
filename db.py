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
import time

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,                 -- 拉取/上报入库时间(unix秒, 云端时钟)
  site_id TEXT NOT NULL,
  ok INTEGER NOT NULL DEFAULT 0,
  online INTEGER, total INTEGER,
  hashrate_ths REAL, power_kw REAL,
  active_alerts INTEGER,
  containers INTEGER, containers_faulty INTEGER, containers_offline INTEGER,
  scan_age_s INTEGER,                  -- 场地本地上次扫描龄期(判「监控停滞」)
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
  hours INTEGER,
  updated_ts INTEGER,
  PRIMARY KEY(site_id, worker)
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
    conn.commit()
    return conn


def insert_snapshot(conn, snap):
    row = [snap.get(f) for f in _SNAP_FIELDS]
    with _lock:
        conn.execute(
            f"INSERT INTO snapshots({','.join(_SNAP_FIELDS)}) "
            f"VALUES({','.join('?' * len(_SNAP_FIELDS))})", row)
        conn.commit()


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


def trend(conn, site_id, hours=24, points=288):
    """单场地趋势(按时间桶降采样均值)。"""
    hours = max(1, min(int(hours), 24 * 90))
    bucket = max(60, int(hours * 3600 / max(24, points)))
    since = int(time.time()) - hours * 3600
    rows = conn.execute(
        "SELECT (ts/?)*? AS t, ROUND(AVG(online)) AS online, ROUND(AVG(total)) AS total,"
        " AVG(hashrate_ths) AS hashrate_ths, AVG(power_kw) AS power_kw,"
        " ROUND(AVG(active_alerts)) AS active_alerts"
        " FROM snapshots WHERE site_id=? AND ok=1 AND ts>=?"
        " GROUP BY t ORDER BY t", (bucket, bucket, site_id, since)).fetchall()
    return [{"ts": int(r["t"]), "online": r["online"], "total": r["total"],
             "hashrate_ths": round(r["hashrate_ths"] or 0.0, 1),
             "power_kw": round(r["power_kw"] or 0.0, 1),
             "active_alerts": r["active_alerts"]} for r in rows]


def avg_hashrate(conn, site_id, t1, t2):
    """[t1,t2) 内成功快照的平均算力(TH/s)；无数据返回 None。算力骤降检测用。"""
    r = conn.execute("SELECT AVG(hashrate_ths) FROM snapshots"
                     " WHERE site_id=? AND ok=1 AND ts>=? AND ts<?",
                     (site_id, t1, t2)).fetchone()
    return r[0]


def replace_customers(conn, site_id, customers, hours):
    now = int(time.time())
    with _lock:
        conn.execute("DELETE FROM site_customers WHERE site_id=?", (site_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO site_customers"
            "(site_id,worker,machines,uptime_pct,delivered_th_h,power_kwh,hours,updated_ts)"
            " VALUES(?,?,?,?,?,?,?,?)",
            [(site_id, str(c.get("worker") or "(未知)")[:128], c.get("machines") or 0,
              c.get("uptime_pct"), c.get("delivered_th_h"), c.get("power_kwh"),
              hours, now) for c in customers])
        conn.commit()


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
    """客户明细，带场地显示名(site_name, 场地未注册时回退 site_id)。"""
    q = ("SELECT c.*, COALESCE(s.name, c.site_id) AS site_name FROM site_customers c"
         " LEFT JOIN sites s ON s.id = c.site_id")
    if site_id:
        rows = conn.execute(q + " WHERE c.site_id=? ORDER BY c.machines DESC", (site_id,)).fetchall()
    else:
        rows = conn.execute(q + " ORDER BY site_name, c.machines DESC").fetchall()
    return [dict(r) for r in rows]


def customer_count_by_site(conn):
    rows = conn.execute(
        "SELECT site_id, COUNT(*) AS n FROM site_customers GROUP BY site_id").fetchall()
    return {r["site_id"]: r["n"] for r in rows}


# ---- 场地注册 ----
def upsert_site(conn, site_id, name, site_type, now):
    """场地上报/登记。name/site_type 传 None 表示不更新该字段。"""
    sets = ["last_push_ts=excluded.last_push_ts"]
    if name:
        sets.append("name=excluded.name")
    if site_type:
        sets.append("type=excluded.type")
    with _lock:
        conn.execute(
            "INSERT INTO sites(id, name, type, created_ts, last_push_ts) VALUES(?,?,?,?,?) "
            f"ON CONFLICT(id) DO UPDATE SET {', '.join(sets)}",
            (site_id, name or site_id, site_type or "air", now, now))
        conn.commit()


def push_sites(conn):
    """全部已注册场地。按 created_ts 排序保证图例颜色稳定(新场地只追加在最后)。"""
    rows = conn.execute("SELECT * FROM sites ORDER BY created_ts, id").fetchall()
    return [dict(r) for r in rows]


def delete_site(conn, site_id):
    """删除场地及其全部数据(场地下线后清理)。"""
    with _lock:
        for t in ("sites", "snapshots", "site_customers", "site_alert_summary",
                  "daily", "notify_log"):
            conn.execute(f"DELETE FROM {t} WHERE {'id' if t == 'sites' else 'site_id'}=?",
                         (site_id,))
        conn.commit()


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


def rollup_daily(conn, day_offsets=(0, 1)):
    """重算指定天(0=今天,1=昨天)的日汇总。幂等 REPLACE，每轮跑一次成本可忽略。

    耗电按**实际时间积分**：每个样本的功率 × 到下一个样本的实际间隔(封顶300秒)，
    与各场地上报/拉取节奏无关(5秒的mock和60秒的真实场地都算得对)；
    断档超过5分钟的时段不计电量(场地断电断网时本来也测不到)。"""
    for off in day_offsets:
        day_start8 = (int((time.time() + _TZ8) // 86400) - off) * 86400
        t1, t2 = day_start8 - _TZ8, day_start8 - _TZ8 + 86400
        day = datetime.datetime.utcfromtimestamp(day_start8).strftime("%Y-%m-%d")
        with _lock:
            conn.execute(
                "REPLACE INTO daily(site_id, day, samples, avg_hashrate_ths, max_hashrate_ths,"
                " avg_online, avg_total, avg_power_kw, power_kwh)"
                " SELECT site_id, ?, COUNT(*), AVG(hashrate_ths), MAX(hashrate_ths),"
                " AVG(online), AVG(total), AVG(power_kw), SUM(power_kw*dt)/3600.0"
                " FROM (SELECT site_id, hashrate_ths, online, total, power_kw,"
                "   MIN(COALESCE(LEAD(ts) OVER (PARTITION BY site_id ORDER BY ts), ts+60) - ts,"
                "       300) AS dt"
                "   FROM snapshots WHERE ok=1 AND ts>=? AND ts<?)"
                " GROUP BY site_id",
                (day, t1, t2))
            conn.commit()


def daily_report(conn, site_id, days=14):
    rows = conn.execute(
        "SELECT * FROM daily WHERE site_id=? ORDER BY day DESC LIMIT ?",
        (site_id, max(1, min(int(days), 3650)))).fetchall()
    return [dict(r) for r in rows]


# ---- 通知日志 ----
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
    return conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
