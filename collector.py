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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        # Fixed worker bound: a large site list must not create unbounded threads.
        try:
            configured_workers = int(p.get("max_workers", 8))
        except (TypeError, ValueError):
            configured_workers = 8
        self.max_workers = max(1, min(32, configured_workers))
        self.fail_offline = int(p.get("fail_offline", 3))
        # 拉取场地状态: site_id -> {fails, last_ok_ts, last_err}
        self.status = {s["id"]: {"fails": 0, "last_ok_ts": 0, "last_err": ""}
                       for s in self.pull_sites()}
        self.notifier = Notifier(cfg, conn)
        self.last_poll_ts = 0
        self._stop = threading.Event()
        self._thread = None
        self._notify_thread = None
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
        management = db.site_management(self.conn)
        out = []
        for s in pulls:
            m = management.get(s["id"], {})
            if m.get("deleted_ts"):
                continue
            s["reported_name"] = s["name"]
            s["name_override"] = m.get("name_override") or ""
            s["name"] = s["name_override"] or s["name"]
            out.append(s)
        names = {}
        for s in out:
            names[s["name"]] = names.get(s["name"], 0) + 1
        for s in out:
            s["duplicate_name"] = names[s["name"]] > 1
        return out

    # ---- 生命周期 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="collector", daemon=True)
        self._thread.start()
        if not self._notify_thread or not self._notify_thread.is_alive():
            self._notify_thread = threading.Thread(
                target=self._notification_loop, name="notification-worker", daemon=True
            )
            self._notify_thread.start()

    def stop(self):
        self._stop.set()

    def _notification_loop(self):
        """Run alert evaluation/delivery away from the site polling loop.

        Notifier persists pending deliveries before sending and records each
        channel receipt. A slow external endpoint can delay this worker, but
        cannot hold up site polling, daily rollups, or retention cleanup.
        """
        while not self._stop.is_set():
            try:
                self.notifier.tick(self.all_sites(), self.site_state)
            except Exception as exc:
                print(f"[collector] 通知工作线程异常: {exc}")
            self._stop.wait(max(5.0, self.interval))

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
            try:   # 日汇总每轮重算今天+昨天(聚合查询很轻)，日报最多滞后一个轮询周期
                db.rollup_daily(self.conn, stalled_after=int(self.cfg.get("poll", {}).get("stalled_after_s", 1200)))
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
        """Poll sites with bounded concurrency; do not overlap collector rounds."""
        sites = self.pull_sites()
        if not sites:
            return
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(sites)),
            thread_name_prefix="site-poll",
        ) as pool:
            futures = {pool.submit(self._poll_site, site): site for site in sites}
            for future in as_completed(futures):
                site = futures[future]
                try:
                    future.result()
                except Exception as exc:  # _poll_site normally records its own errors
                    print(f"[collector] {site.get('name', site['id'])} 采集任务异常: {exc}")

    def _poll_site(self, site):
        sid = site["id"]
        st = self.status.setdefault(sid, {"fails": 0, "last_ok_ts": 0, "last_err": ""})
        now = int(time.time())
        try:
            d = self._get(site, "/api/public/summary", {"lite": 1})
            now = int(time.time())
            if d.get("scanned") is False:
                st.update(fails=0, last_err="", pending=True)
                db.insert_snapshot(self.conn, {"ts": now, "site_id": sid, "ok": 0,
                                               "error": "等待首次扫描"})
                return
            scan_ts = validate.scan_timestamp(d, now)
            customer_snapshot = validate.customer_snapshot(d, now)
            if customer_snapshot is not None:
                scan_ts = customer_snapshot[1]
            # 场地返回的数据同样不可信(场地机器可能被攻破)：数值列一律清洗，见 validate.py
            snap = validate.snapshot_fields({**d, "hashrate_ths": d.get("total_hashrate_ths"),
                                             "power_kw": d.get("total_power_kw")})
            snap.update({"ts": now, "site_id": sid, "ok": 1, "error": None,
                         "scan_ts": scan_ts,
                         "measurement_ts": validate.measurement_timestamp(d, scan_ts),
                         **validate.source_time_fields(d),
                         **validate.scan_progress_fields(d, now, scan_ts),
                         "scan_age_s": max(0, now - scan_ts) if scan_ts else None})
            stored = db.insert_snapshot(self.conn, snap)
            if stored and customer_snapshot is not None:
                db.replace_customer_hashrates(self.conn, sid, *customer_snapshot)
            st["pending"] = False
            st["fails"] = 0
            st["last_ok_ts"] = now
            st["last_err"] = ""
        except Exception as e:
            st["pending"] = False
            st["fails"] += 1
            st["last_err"] = str(e)[:300]
            db.insert_snapshot(self.conn, {"ts": now, "site_id": sid, "ok": 0,
                                           "error": st["last_err"]})

    def poll_customers(self):
        for s in self.pull_sites():
            try:
                d = self._get(s, "/api/public/customers", {"hours": self.customer_hours,
                                                          "until_ts": int(time.time()) // 600 * 600})
                rows = validate.customers(d.get("customers"), strict=True)
                period = validate.customer_report_period(d, rows, int(time.time()))
                db.replace_customers(self.conn, s["id"], rows,
                                     validate.num(d.get("hours"), int, self.customer_hours,
                                                  hi=24 * 90) or self.customer_hours, period)
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
            if st.get("pending"):
                return "pending"
            if st.get("fails", 0) >= self.fail_offline:
                return "unreachable"
            if not snap_ok:
                return "pending" if st.get("fails", 0) == 0 else "unreachable"
            if now - snap_ok["ts"] > self.interval * self.fail_offline:
                return "unreachable"
        stalled_after = int(self.cfg.get("poll", {}).get("stalled_after_s", 1200))
        age = db.scan_age(snap_ok, now)
        if age is not None and age > stalled_after and not db.scan_in_progress(snap_ok, now):
            return "stalled"
        # 场内自报全场大面积掉线：链路仍通（还能上报），与「云端收不到/扫描停更」分开。
        if self._local_site_down(site_id):
            return "site_down"
        # 告警口径与场地本地完全一致：只看本地活跃告警数。
        # 集装箱真故障/箱体离线在本地会生成告警计入 active_alerts；
        # 被本地配置忽略的故障位(如供液流量低)只在卡片显示、不算告警——云端不越权升级。
        if (snap_ok.get("active_alerts") or 0) > 0:
            return "alert"
        return "ok"

    def _local_site_down(self, site_id):
        try:
            rows = db.get_alert_summary(self.conn, site_id)
        except Exception:
            return False
        return any((r.get("category") == "site_down" and (r.get("count") or 0) > 0)
                   for r in (rows or []))
