#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假场地（拉取模式数据源）：模拟本地监控的 /api/public/summary 和 /api/public/customers。

用法: python mock_site.py <端口> <air|hydro> <token>
数据带慢波动，方便在面板上看出趋势线。配合 tools/test_config.yaml 使用，见 docs/DEVELOPMENT.md §10。
"""
import json
import math
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(sys.argv[1])
KIND = sys.argv[2]
TOKEN = sys.argv[3]
START = time.time()

AIR = {"total": 520, "per_th": 120.0, "per_kw": 3.4, "containers": 0}
HYDRO = {"total": 3200, "per_th": 300.0, "per_kw": 5.9, "containers": 18}
CUSTOMERS = {
    "air": [("客户A", 300), ("客户B", 180)],
    "hydro": [("Asuna666", 1800), ("BobFarm", 900), ("CCC", 400)],
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        tok = self.headers.get("Authorization", "").replace("Bearer ", "") or (qs.get("token") or [""])[0]
        if tok != TOKEN:
            return self._json({"detail": "bad token"}, 401)
        now = int(time.time())
        p = AIR if KIND == "air" else HYDRO
        wave = math.sin((now - START) / 120.0)
        online = int(p["total"] * (0.95 + 0.03 * wave))
        if u.path == "/api/public/summary":
            faulty = 1 if (KIND == "hydro" and wave > 0.5) else 0
            return self._json({
                "ok": True, "ts": now, "cached": False, "scanned": True,
                "scan_id": 1, "scan_ts": now - 60, "scan_kind": "full",
                "total": p["total"], "online": online, "offline": p["total"] - online,
                "total_hashrate_ths": round(online * p["per_th"] * (1 + 0.02 * wave), 1),
                "avg_hashrate_ths": p["per_th"],
                "total_power_kw": round(online * p["per_kw"], 1),
                "active_alerts": max(0, int(3 * wave)),
                "containers": p["containers"], "containers_faulty": faulty,
                "containers_offline": 0,
                "progress": {"running": False},
            })
        if u.path == "/api/public/customers":
            hours = int((qs.get("hours") or ["24"])[0])
            out = []
            for name, machines in CUSTOMERS[KIND]:
                out.append({"worker": name, "machines": machines,
                            "uptime_pct": round(97 + 2 * abs(wave), 1),
                            "delivered_th_h": round(machines * p["per_th"] * hours * 0.97, 1),
                            "power_kwh": round(machines * p["per_kw"] * hours * 0.97, 1)})
            return self._json({"ok": True, "ts": now, "hours": hours,
                               "covered_hours": hours, "truncated": False, "customers": out})
        if u.path == "/api/public/health":
            return self._json({"ok": True, "ts": now, "scanning": False,
                               "last_scan_ts": now - 60, "last_scan_age_s": 60, "active_alerts": 0})
        return self._json({"detail": "not found"}, 404)


print(f"mock {KIND} site on :{PORT}")
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
