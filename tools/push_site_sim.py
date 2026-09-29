#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假推送场地：复用本地监控的真实上报模块(E:\\ip\\cloud_report.py)往云端推模拟数据，
验证 推送→自动注册→面板显示→断报判不可达 的完整链路。

用法: python push_site_sim.py <云端url> <ingest_token> <场地名> [air|hydro] [site_id]
例:   python push_site_sim.py http://127.0.0.1:8900 ingest-test-tok "推送测试场" hydro st-sim001
依赖: 本机存在 E:\\ip (或设环境变量 IP_DIR 指向含 cloud_report.py 的目录)。
site_id 缺省 st-sim001(显式指定, 不会动 E:\\ip 的 cloud_site_id.txt)。
测试时为了快, 上报间隔压到 5s(生产是60s)。Ctrl+C 停止(停后约3个周期该场地变"不可达")。
"""
import math
import os
import sys
import time

sys.path.insert(0, os.environ.get("IP_DIR", r"E:\ip"))
import cloud_report  # noqa: E402

URL, TOKEN, SITE = sys.argv[1], sys.argv[2], sys.argv[3]
TYPE = sys.argv[4] if len(sys.argv) > 4 else "hydro"
SID = sys.argv[5] if len(sys.argv) > 5 else "st-sim001"
CFG = {"cloud": {"enabled": True, "url": URL, "token": TOKEN,
                 "site_name": SITE, "site_type": TYPE, "site_id": SID,
                 "interval": 5, "customer_interval": 10, "customer_hours": 24, "timeout": 5}}
START = time.time()


def get_summary():
    now = int(time.time())
    wave = math.sin((now - START) / 60.0)
    online = int(2000 * (0.96 + 0.02 * wave))
    return {"ok": True, "scanned": True, "scan_ts": now - 45,
            "online": online, "total": 2000,
            "total_hashrate_ths": round(online * 300.0, 1),
            "total_power_kw": round(online * 5.9, 1),
            "active_alerts": 1 if wave > 0.6 else 0,
            "containers": 12 if TYPE == "hydro" else 0,
            "containers_faulty": 0,
            "containers_offline": 1 if TYPE == "hydro" else 0}


def get_customers(hours):
    return [{"worker": "推送客户X", "machines": 1500, "uptime_pct": 99.1,
             "delivered_th_h": 1500 * 300 * hours * 0.99, "power_kwh": 1500 * 5.9 * hours * 0.99},
            {"worker": "推送客户Y", "machines": 500, "uptime_pct": 98.2,
             "delivered_th_h": 500 * 300 * hours * 0.98, "power_kwh": 500 * 5.9 * hours * 0.98}]


t = cloud_report.start(CFG, get_summary, get_customers)
print(f"推送中: {SITE}({TYPE}, id={SID}) → {URL} 每5s一次, Ctrl+C 停止")
while True:
    time.sleep(60)
