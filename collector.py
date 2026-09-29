#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台采集线程：拉取模式轮询 + 告警评估 + 日汇总 + 过期清理。

- 每 poll.interval(默认60s) 拉一轮「拉取模式」场地 GET {url}/api/public/summary?lite=1
  (推送模式场地不在此列——它们自己往 /api/ingest/* 推)
- 每轮结束跑一次告警评估(notify.Notifier: 失联/停滞/恢复/算力骤降)
- 每小时: 清理过期快照(分批) + 重算今天/昨天的日汇总
- 场地一律按稳定 site_id 归档(拉取场地: config 的 id 字段, 缺省用 name)
"""
import threading
import time

import requests

import db
import validate
from notify import Notifier


class Collector:
    def __init__(self, cfg, conn):
        self.cfg = cfg
        self.conn = conn
        p = cfg.get("poll", {})
        self.interval = int(p.get("interval", 60))
        self.customer_interval = int(p.get("customer_interval", 600))
        self.customer_hours = int(p.get("customer_hours", 24))
        self.timeout = float(p.get("timeout", 10))
        self.fail_offline = int(p.get("fail_offline", 3))
        # 拉取场地状态: site_id -> {fails, last_ok_ts, last_err}
        self.status = {s["id"]: {"fails": 0, "last_ok_ts": 0, "last_err": ""}
                       for s in self.pull_sites()}
        self.notifier = Notifier(cfg, conn)
        self.last_poll_ts = 0
        self._stop = threading.Event()
        self._thread = None
        # 把拉取场地登记进 sites 表(客户表联名、统一注册视图用)
        now = int(time.time())
        for s in self.pull_sites():
            db.upsert_site(conn, s["id"], s["name"], s.get("type", "air"), now)

    # ---- 场地清单 ----
    def pull_sites(self):
        """config 里的拉取模式场地(带稳定 id, 缺省用 name)。"""
        out = []
        for s in (self.cfg.get("sites") or []):
            d = dict(s)
            d["id"] = str(s.get("id") or s["name"])
            out.append(d)
        return out

    def all_sites(self):
        """全部场地 [{id,name,type,mode}]：config 拉取场地(配置为准) + 其余注册场地(推送)。
        推送场地按注册时间排序——图例颜色跟随实体，新场地只追加在末尾。"""
        pulls = [{"id": s["id"], "name": s["name"], "type": s.get("type", "air"),
                  "mode": "pull"} for s in self.pull_sites()]
        seen = {p["id"] for p in pulls}
        for r in db.push_sites(self.conn):
            if r["id"] not in seen:
                pulls.append({"id": r["id"], "name": r["name"] or r["id"],
                              "type": r["type"] or "air", "mode": "push"})
        return pulls

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="collector", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        last_customers = 0.0
        last_hourly = 0.0
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.poll_summaries()
            except Exception as e:
                print(f"[collector] 摘要轮询异常: {e}")
            if t0 - last_customers >= self.customer_interval:
                last_customers = t0
                try:
                    self.poll_customers()
                except Exception as e:
                    print(f"[collector] 客户报表轮询异常: {e}")
            # 告警评估(每轮)：失联/停滞/恢复/算力骤降
            self.notifier.tick(self.all_sites(), self.site_state)
            try:   # 日汇总每轮重算今天+昨天(聚合查询很轻)，日报最多滞后一个轮询周期
                db.rollup_daily(self.conn)
            except Exception as e:
                print(f"[collector] 日汇总异常: {e}")
            if t0 - last_hourly >= 3600:
                last_hourly = t0
                try:
                    db.cleanup(self.conn, self.cfg.get("db", {}).get("retention_days", 30))
                except Exception as e:
                    print(f"[collector] 清理异常: {e}")
            self.last_poll_ts = int(time.time())
            elapsed = time.time() - t0
            self._stop.wait(max(5.0, self.interval - elapsed))

    # ---- 拉取 ----
    def _get(self, site, path, params=None):
        url = site["url"].rstrip("/") + path
        r = requests.get(url, params=params or {}, timeout=self.timeout,
                         headers={"Authorization": f"Bearer {site.get('token', '')}"})
        r.raise_for_status()
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(str(d.get("error") or "场地返回 ok=false"))
        return d

    def poll_summaries(self):
        threads = []
        for s in self.pull_sites():
            t = threading.Thread(target=self._poll_site, args=(s,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(self.timeout + 10)

    def _poll_site(self, site):
        sid = site["id"]
        st = self.status.setdefault(sid, {"fails": 0, "last_ok_ts": 0, "last_err": ""})
        now = int(time.time())
        try:
            d = self._get(site, "/api/public/summary", {"lite": 1})
            scan_ts = validate.num(d.get("scan_ts"), int, 0)
            # 场地返回的数据同样不可信(场地机器可能被攻破)：数值列一律清洗，见 validate.py
            snap = validate.snapshot_fields({**d, "hashrate_ths": d.get("total_hashrate_ths"),
                                             "power_kw": d.get("total_power_kw")})
            snap.update({"ts": now, "site_id": sid, "ok": 1, "error": None,
                         "scan_age_s": max(0, now - scan_ts) if scan_ts else None})
            db.insert_snapshot(self.conn, snap)
            st["fails"] = 0
            st["last_ok_ts"] = now
            st["last_err"] = ""
        except Exception as e:
            st["fails"] += 1
            st["last_err"] = str(e)[:300]
            db.insert_snapshot(self.conn, {"ts": now, "site_id": sid, "ok": 0,
                                           "error": st["last_err"]})

    def poll_customers(self):
        for s in self.pull_sites():
            try:
                d = self._get(s, "/api/public/customers", {"hours": self.customer_hours})
                db.replace_customers(self.conn, s["id"], validate.customers(d.get("customers")),
                                     validate.num(d.get("hours"), int, self.customer_hours,
                                                  hi=24 * 90) or self.customer_hours)
            except Exception as e:
                print(f"[collector] {s['name']} 客户报表拉取失败: {e}")

    # ---- 状态判定(API 层与告警共用) ----
    def site_state(self, site_id, snap_ok, mode="pull", now=None):
        """返回 unreachable | stalled | alert | ok | pending
        mode: pull=云端拉取(看连续失败次数) | push=场地上报(看上报龄期)"""
        now = now or int(time.time())
        if mode == "push":
            if not snap_ok:
                return "pending"
            # 推送场地按"场地上报周期"算失联阈值(poll.push_interval, 默认60s)，
            # 与云端自己的拉取轮询周期(poll.interval)无关——两者节奏可以不同
            push_iv = int(self.cfg.get("poll", {}).get("push_interval", 60))
            if now - snap_ok["ts"] > push_iv * self.fail_offline:
                return "unreachable"   # 超过 N 个上报周期没收到
        else:
            st = self.status.get(site_id) or {}
            if st.get("fails", 0) >= self.fail_offline:
                return "unreachable"
            if not snap_ok:
                return "pending" if st.get("fails", 0) == 0 else "unreachable"
        stalled_after = int(self.cfg.get("poll", {}).get("stalled_after_s", 1200))
        if (snap_ok.get("scan_age_s") or 0) > stalled_after:
            return "stalled"
        # 告警口径与场地本地完全一致：只看本地活跃告警数。
        # 集装箱真故障/箱体离线在本地会生成告警计入 active_alerts；
        # 被本地配置忽略的故障位(如供液流量低)只在卡片显示、不算告警——云端不越权升级。
        if (snap_ok.get("active_alerts") or 0) > 0:
            return "alert"
        return "ok"
