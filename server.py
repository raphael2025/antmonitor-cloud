#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""矿场云端总览：FastAPI 服务 + 静态面板 + 后台采集线程。

数据流：各场地本地监控(E:\\ip 那套) --/api/public/* Token只读--> 本服务(每分钟拉取入库)
        --> /api/overview 等接口 --> web/ 总览面板

配置文件默认取同目录 config.yaml，可用环境变量 CO_CONFIG 指定其他路径(测试用)。
启动：python server.py    (默认 0.0.0.0:8900)
"""
import copy
import hmac
import ipaddress
import os
import sys
import threading
import time

import yaml
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import appconfig
import auth
import db
import seccheck
import updater
import validate
from collector import Collector

# Windows 控制台默认 GBK，打印 emoji/生僻字会炸；统一按 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.environ.get("CO_CONFIG") or os.path.join(BASE, "config.yaml")
# 二进制读 + 显式 UTF-8 解码：不给任何一层 locale/编码劫持的机会(Windows 控制台/启动器环境复杂)
with open(CFG_PATH, "rb") as f:
    CFG = yaml.safe_load(f.read().decode("utf-8-sig")) or {}

# 网页保存的通知设置(cloud_settings.json)叠加进 CFG，早于 Notifier/Collector 构造——
# 这样它们捕获的就是已经合并好的 dict，不需要事后再刷新一次引用。
appconfig.apply_settings(CFG, appconfig.load_settings())

_db_path = CFG.get("db", {}).get("path", "cloud_overview.db")
if not os.path.isabs(_db_path):
    _db_path = os.path.join(os.path.dirname(os.path.abspath(CFG_PATH)), _db_path)
CONN = db.connect(_db_path)
COLLECTOR = Collector(CFG, CONN)

app = FastAPI(title="矿场云端总览", docs_url=None, redoc_url=None)


def _update_event(msg):
    """自动更新的通知：打日志 + 借用告警通道推一条(不占告警冷却)。"""
    print(f"[update] {msg}")
    try:
        import notify as _n
        a = CFG.get("alerts") or {}
        tg = a.get("telegram") or {}
        if tg.get("enabled") and tg.get("bot_token"):
            _n._send_telegram(tg, f"云端总览: {msg}")
        wh = a.get("webhook") or {}
        if wh.get("enabled") and wh.get("url"):
            _n._send_webhook(wh, f"云端总览: {msg}")
    except Exception:
        pass


@app.on_event("startup")
def _startup():
    try:   # 安全自检放启动钩子里：python server.py 与 uvicorn server:app 两种启动方式都会打印
        _print_seccheck()
    except Exception as e:
        print(f"[seccheck] 自检异常: {e}")
    COLLECTOR.start()   # 幂等；uvicorn server:app 方式启动时也能拉起采集线程
    updater.start_auto(CFG.get("update") or {}, on_event=_update_event)


# ---------- 鉴权 ----------
_TRUST_PROXY = bool(CFG.get("server", {}).get("trust_proxy", False))
# 只有直连对端在这些网段里，才信它带来的 X-Forwarded-For/Proto——否则公网上的人直连 8900
# 自己伪造一个 XFF 就能绕过登录锁定(每次换个假IP无限试密码)。默认=本机+内网(nginx 同机
# 或 docker 网桥)；nginx 在别的机器上就把它的地址加进 server.trusted_proxies。
_TRUSTED_NETS = []
for _n in (CFG.get("server", {}).get("trusted_proxies")
           or ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"]):
    try:
        _TRUSTED_NETS.append(ipaddress.ip_network(str(_n), strict=False))
    except ValueError:
        print(f"⚠️  server.trusted_proxies 里 {_n!r} 不是合法 IP/网段，已忽略")


def _from_trusted_proxy(request: Request):
    if not _TRUST_PROXY or not request.client:
        return False
    try:
        ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    return any(ip in n for n in _TRUSTED_NETS)


def _client_ip(request: Request):
    """限速/登录锁定的来源键。在可信反代(nginx)后面时必须开 server.trust_proxy——
    否则所有请求源都是 127.0.0.1，一人触发锁定全员遭殃。取 X-Forwarded-For **最后一个**
    (最靠近我们的那层代理写进去的，客户端伪造的只能出现在前面)。"""
    if _from_trusted_proxy(request):
        xff = request.headers.get("x-forwarded-for") or ""
        last = xff.split(",")[-1].strip() if xff else ""
        if last:
            return last[:64]
    return request.client.host if request.client else ""


def _is_https(request: Request):
    if request.url.scheme == "https":
        return True
    return (_from_trusted_proxy(request)
            and (request.headers.get("x-forwarded-proto") or "").split(",")[-1].strip() == "https")


def _audit(request: Request, user, action, detail=""):
    try:
        db.audit_add(CONN, user, _client_ip(request), action, detail)
    except Exception as e:
        print(f"[audit] 写入失败: {e}")


def _require_login(request: Request):
    sess = auth.current(CFG, request.cookies.get(auth.COOKIE) or "")
    if not sess:
        raise HTTPException(401, "未登录")
    return sess


@app.post("/api/login")
def api_login(request: Request, response: Response, body: dict = Body(...)):
    """sync def：pbkdf2 20万轮约 0.1s，放线程池算，不卡事件循环(不然一波暴破就把面板拖死)。"""
    src = _client_ip(request)
    if auth.locked(src):
        raise HTTPException(429, "失败次数过多，请5分钟后再试")
    username = str(body.get("username", ""))[:64]
    token, why = auth.login(CFG, username, str(body.get("password", ""))[:256], src=src)
    known = username in auth._users(CFG)
    if not token:
        # 未知用户名不原样记录——常有人把密码误输进用户名框
        _audit(request, username if known else "(未知用户)", "login_fail", why or "")
        if why == "locked":
            raise HTTPException(429, "失败次数过多，请5分钟后再试")
        if why == "default_password":
            raise HTTPException(403, "该账号仍是出厂默认密码，已禁止登录。请在服务器上运行 "
                                     f"python auth.py passwd {username} 设置新密码(立即生效)")
        raise HTTPException(401, "用户名或密码错误")
    # Secure：配置显式打开，或本次请求本身就是 https(含可信反代转发的 https)——
    # 走 https 时 Cookie 永远只该在 https 上发，不用再手动改配置。
    secure = bool(CFG.get("auth", {}).get("secure_cookie", False)) or _is_https(request)
    response.set_cookie(auth.COOKIE, token, max_age=auth.TTL, httponly=True,
                        samesite="strict", secure=secure)
    sess = auth.session(token)
    _audit(request, sess["user"], "login", "https" if _is_https(request) else "http")
    return {"ok": True, "user": sess["user"], "role": sess["role"]}


@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    tok = request.cookies.get(auth.COOKIE) or ""
    sess = auth.session(tok)
    auth.logout(tok)
    if sess:
        _audit(request, sess["user"], "logout")
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    sess = auth.current(CFG, request.cookies.get(auth.COOKIE) or "")
    return {"authenticated": bool(sess),
            "user": sess["user"] if sess else None,
            "role": sess["role"] if sess else None,
            "auth_enabled": auth.enabled(CFG)}


# ---------- 场地清单：config 里的拉取场地 + 自动注册的推送场地(按稳定 site_id) ----------
def all_sites():
    return COLLECTOR.all_sites()


def _find_site(key):
    """按 id 精确找，找不到再按显示名兜底(方便人肉 curl)。"""
    ss = all_sites()
    return (next((s for s in ss if s["id"] == key), None)
            or next((s for s in ss if s["name"] == key), None))


# ---------- 数据接口(登录后只读) ----------
def _site_entry(scfg, latest, last_ok, cust_counts, now):
    """单场地在总览里的一条：状态 + 最后已知数据 + 数据龄期。一切按稳定 site_id 归档。"""
    sid = scfg["id"]
    mode = scfg.get("mode", "pull")
    snap = last_ok.get(sid)           # 最后一次成功的数据(失联时仍显示)
    cur = latest.get(sid)             # 最新一条(可能是失败记录)
    state = COLLECTOR.site_state(sid, snap, mode=mode, now=now)
    if mode == "push":
        fails = 0
        err = (f"超过 {max(1, (now - snap['ts']) // 60)} 分钟未收到该场地上报"
               if state == "unreachable" and snap else None)
    else:
        st = COLLECTOR.status.get(sid) or {}
        fails = st.get("fails", 0)
        err = (cur or {}).get("error") or st.get("last_err") or None
    d = {"id": sid, "name": scfg["name"], "type": scfg.get("type", "air"), "mode": mode,
         "state": state,                                   # ok|alert|stalled|unreachable|pending
         "fails": fails, "error": err,
         "data_ts": snap["ts"] if snap else None,
         "data_age_s": (now - snap["ts"]) if snap else None,
         "customers": cust_counts.get(sid, 0)}
    for k in ("online", "total", "hashrate_ths", "power_kw", "active_alerts",
              "containers", "containers_faulty", "containers_offline", "scan_age_s"):
        d[k] = snap.get(k) if snap else None
    return d


def _overview_payload():
    now = int(time.time())
    latest = db.latest_snapshots(CONN)
    last_ok = db.latest_ok_snapshots(CONN)
    cust_counts = db.customer_count_by_site(CONN)
    sites = [_site_entry(s, latest, last_ok, cust_counts, now)
             for s in all_sites()]
    tot = {"sites": len(sites),
           "sites_ok": sum(1 for s in sites if s["state"] in ("ok", "alert")),
           "sites_unreachable": sum(1 for s in sites if s["state"] == "unreachable"),
           "machines_total": sum(s["total"] or 0 for s in sites),
           "machines_online": sum(s["online"] or 0 for s in sites),
           "hashrate_ths": round(sum(s["hashrate_ths"] or 0 for s in sites), 1),
           "power_kw": round(sum(s["power_kw"] or 0 for s in sites), 1),
           "active_alerts": sum(s["active_alerts"] or 0 for s in sites),
           "containers": sum(s["containers"] or 0 for s in sites),
           "containers_faulty": sum(s["containers_faulty"] or 0 for s in sites),
           "containers_offline": sum(s["containers_offline"] or 0 for s in sites),
           "customers": sum(s["customers"] or 0 for s in sites)}
    return {"ok": True, "ts": now, "totals": tot, "sites": sites,
            "poll_interval": COLLECTOR.interval}


def _trend_payload(hours, points):
    out = []
    for s in all_sites():
        pts = db.trend(CONN, s["id"], hours=hours, points=points)
        out.append({"id": s["id"], "name": s["name"], "type": s.get("type", "air"),
                    "points": [[p["ts"], p["hashrate_ths"]] for p in pts]})
    return {"ok": True, "ts": int(time.time()), "hours": hours, "sites": out}


def _site_payload(key, hours):
    scfg = _find_site(key)
    if not scfg:
        raise HTTPException(404, "无此场地")
    now = int(time.time())
    latest = db.latest_snapshots(CONN)
    last_ok = db.latest_ok_snapshots(CONN)
    cust_counts = db.customer_count_by_site(CONN)
    entry = _site_entry(scfg, latest, last_ok, cust_counts, now)
    pts = db.trend(CONN, scfg["id"], hours=hours)
    return {"ok": True, "ts": now, "site": entry, "hours": hours,
            "trend": pts, "customers": _customer_rows(db.customers(CONN, site_id=scfg["id"])),
            "alert_summary": db.get_alert_summary(CONN, scfg["id"])}


def _daily_payload(key, days):
    scfg = _find_site(key)
    if not scfg:
        raise HTTPException(404, "无此场地")
    return {"ok": True, "ts": int(time.time()), "id": scfg["id"], "name": scfg["name"],
            "days": db.daily_report(CONN, scfg["id"], days)}


def _customer_rows(rows):
    """出库再清洗一遍数值列：修复前入库的脏行(数值列里存了字符串)不能再 500 或流到前端。"""
    out = []
    for r in rows:
        r = dict(r)
        r["machines"] = validate.num(r.get("machines"), int)
        for k in ("uptime_pct", "delivered_th_h", "power_kwh"):
            r[k] = validate.opt_num(r.get(k))
        out.append(r)
    return out


def _customers_payload():
    rows = _customer_rows(db.customers(CONN))
    agg = {}
    for r in rows:
        a = agg.setdefault(r["worker"], {"worker": r["worker"], "sites": 0, "machines": 0,
                                         "delivered_th_h": 0.0, "power_kwh": 0.0})
        a["sites"] += 1
        a["machines"] += r["machines"] or 0
        a["delivered_th_h"] += r["delivered_th_h"] or 0
        a["power_kwh"] += r["power_kwh"] or 0
    return {"ok": True, "ts": int(time.time()), "rows": rows,
            "by_worker": sorted(agg.values(), key=lambda x: -x["machines"])}


@app.get("/api/overview")
def api_overview(request: Request):
    _require_login(request)
    return _overview_payload()


@app.get("/api/trend")
def api_trend(request: Request, hours: int = 24, points: int = 288):
    """各场地算力趋势(多线图用)。"""
    _require_login(request)
    return _trend_payload(hours, points)


@app.get("/api/site/{key}")
def api_site(request: Request, key: str, hours: int = 24):
    """单场地详情(按 site_id 寻址, 显示名兜底)：最新数据 + 趋势 + 客户报表。"""
    _require_login(request)
    return _site_payload(key, hours)


@app.get("/api/daily")
def api_daily(request: Request, site: str, days: int = 14):
    """单场地日级汇总(永久保留)：均值/峰值算力、平均在线、耗电估算。"""
    _require_login(request)
    return _daily_payload(site, days)


@app.get("/api/customers")
def api_customers(request: Request):
    """跨场地客户明细 + 按矿工名(客户)汇总。"""
    _require_login(request)
    return _customers_payload()


# ---------- Agent 公共 API(Bearer Token、只读；给 AI agent/外部脚本取数汇报用) ----------
# 与本地监控 /api/public/* 同一套约定：fail-closed(未配置token则404)、短缓存、按来源限速。
_pub_cache = {}
_pub_lock = threading.Lock()
_PUB_RATE_MAX = 120   # 每来源每分钟请求上限


def _pub_cached(key, ttl, build):
    now = time.time()
    with _pub_lock:
        v = _pub_cache.get(key)
        if v and now - v[0] < ttl:
            return v[1]
    data = build()
    with _pub_lock:
        _pub_cache[key] = (time.time(), data)
        if len(_pub_cache) > 256:   # 防参数组合撑内存：剔最旧
            for k in sorted(_pub_cache, key=lambda k: _pub_cache[k][0])[:64]:
                _pub_cache.pop(k, None)
    return data


_pub_rate = {}      # src -> [窗口起点, 计数]
_ingest_rate = {}   # 同上，上报接口单独一张表(两类流量互不挤占)


def _rate_ok(table, src, maxn):
    now = time.time()
    with _pub_lock:
        if len(table) > 512:    # 剪掉过期窗口，防旧IP无限累积
            for k in [k for k, w in table.items() if now - w[0] >= 60]:
                table.pop(k, None)
        w = table.get(src)
        if not w or now - w[0] >= 60:
            table[src] = [now, 1]
            return True
        if w[1] >= maxn:
            return False
        w[1] += 1
        return True


def _pub_rate_ok(src):
    return _rate_ok(_pub_rate, src, _PUB_RATE_MAX)


def _clamp(v, lo, hi):
    return max(lo, min(int(v), hi))


def _check_agent_token(request: Request):
    tok = str((CFG.get("public_api") or {}).get("token") or "")
    if seccheck.is_placeholder(tok):
        raise HTTPException(404)   # 未配置/模板占位值 → 整组端点视为不存在(fail-closed)
    # 先限速再比对：错误 token 也计数，挡住对 token 的在线穷举
    if not _pub_rate_ok(_client_ip(request)):
        raise HTTPException(429, f"太频繁(每来源每分钟 {_PUB_RATE_MAX} 次)")
    got = request.headers.get("Authorization") or ""
    got = got[7:].strip() if got.startswith("Bearer ") else (request.query_params.get("token") or "")
    if not got or not hmac.compare_digest(got.encode(), tok.encode()):
        raise HTTPException(401, "token 错误")


@app.get("/api/public/overview")
def api_public_overview(request: Request):
    """全网汇总+分场地(agent 最常用；5秒缓存)。"""
    _check_agent_token(request)
    return _pub_cached("overview", 5, _overview_payload)


@app.get("/api/public/trend")
def api_public_trend(request: Request, hours: int = 24, points: int = 288):
    _check_agent_token(request)
    hours, points = _clamp(hours, 1, 2160), _clamp(points, 24, 1000)   # 先钳制再作缓存键
    return _pub_cached(("trend", hours, points), 15, lambda: _trend_payload(hours, points))


@app.get("/api/public/site/{key}")
def api_public_site(request: Request, key: str, hours: int = 24):
    _check_agent_token(request)
    hours = _clamp(hours, 1, 2160)
    return _pub_cached(("site", key, hours), 10, lambda: _site_payload(key, hours))


@app.get("/api/public/daily")
def api_public_daily(request: Request, site: str, days: int = 14):
    _check_agent_token(request)
    days = _clamp(days, 1, 3650)
    return _pub_cached(("daily", site, days), 60, lambda: _daily_payload(site, days))


@app.get("/api/public/customers")
def api_public_customers(request: Request):
    _check_agent_token(request)
    return _pub_cached("customers", 30, _customers_payload)


# ---------- 场地上报接口(推送模式：本地→云端，NAT 后无需端口映射) ----------
def _bearer(request: Request):
    got = request.headers.get("Authorization") or ""
    return got[7:].strip() if got.startswith("Bearer ") else ""


def _check_ingest_token(request: Request, site_id):
    """每场地钥匙优先：ingest.tokens 里配了该 site_id 就必须用它自己的钥匙
    (共享钥匙对它无效——防一个场地被攻破后冒充其它场地)；没配的场地用共享 ingest.token。
    模板占位值(CHANGE_ME…)一律视为没配。"""
    icfg = CFG.get("ingest") or {}
    # 专属钥匙是占位值 → 该场地无有效钥匙(拒收)，而不是悄悄退回共享钥匙
    per = {str(k): ("" if seccheck.is_placeholder(v) else str(v))
           for k, v in (icfg.get("tokens") or {}).items()}
    shared = str(icfg.get("token") or "")
    if seccheck.is_placeholder(shared):
        shared = ""
    if not shared and not any(per.values()):
        raise HTTPException(404)   # 完全未配置则整组端点视为不存在(fail-closed)
    rate = int(icfg.get("rate_per_min") or 120)
    if not _rate_ok(_ingest_rate, _client_ip(request), rate):   # 错 token 也计数，挡穷举
        raise HTTPException(429, f"上报太频繁(每来源每分钟 {rate} 次)")
    expected = per.get(site_id, shared)
    got = _bearer(request)
    if not expected or not got or not hmac.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(401, "上报 token 错误")
    if not db.site_exists(CONN, site_id):
        # 新场地自动注册：可关(ingest.auto_register:false → 只收 ingest.tokens 里登记过的)；
        # 总数封顶，防共享钥匙泄露后被灌几万个假场地把面板刷爆
        if icfg.get("auto_register", True) is False and site_id not in per:
            raise HTTPException(403, "未登记的场地(云端已关闭自动注册，请管理员在 ingest.tokens 里登记)")
        if db.site_count(CONN) >= int(icfg.get("max_sites") or 200):
            raise HTTPException(403, "场地数已达上限(ingest.max_sites)")


def _ingest_identity(body):
    """解析场地身份：site_id(稳定唯一,主) + site(显示名)。旧版无 site_id 时回退用名字当 id。"""
    raw_name = body.get("site")
    name = validate.site_key(raw_name)
    sid = validate.site_key(body.get("site_id")) if body.get("site_id") else name
    if not sid or (raw_name and not name):
        raise HTTPException(400, "site_id/site 非法(1-64字符)")
    return sid, (name or sid)


_num = validate.num


@app.post("/api/ingest/summary")
def api_ingest_summary(request: Request, body: dict = Body(...)):
    """场地摘要上报(本地 cloud_report.py 每分钟推一次)。ts 用云端时间，不信任场地时钟。
    sync def → FastAPI 自动放线程池执行，写库不占事件循环。"""
    sid, name = _ingest_identity(body)
    _check_ingest_token(request, sid)
    site_type = body.get("type") if body.get("type") in ("air", "hydro", "mixed") else "air"
    now = int(time.time())
    db.upsert_site(CONN, sid, name, site_type, now)
    snap = validate.snapshot_fields(body)
    snap.update({"ts": now, "site_id": sid, "ok": 1, "error": None,
                 "scan_age_s": (_num(body.get("scan_age_s"), int, None, hi=10**7)
                                if body.get("scan_age_s") is not None else None)})
    db.insert_snapshot(CONN, snap)
    if "alert_summary" in body:   # 字段缺失(旧版场地端)不动已存数据；显式空列表=清空
        db.replace_alert_summary(CONN, sid, _clean_alert_summary(body.get("alert_summary")))
    return {"ok": True, "ts": now}


def _clean_alert_summary(groups):
    """校验/钳制场地端推来的告警摘要，绝不让脏输入污染库或撑爆响应体。"""
    if not isinstance(groups, list):
        return []
    out = []
    now = int(time.time())
    for g in groups[:30]:
        if not isinstance(g, dict):
            continue
        raw_workers = g.get("workers")
        workers = []
        if isinstance(raw_workers, list):
            for w in raw_workers[:30]:
                if isinstance(w, dict) and w.get("worker"):
                    workers.append({"worker": validate.text(w["worker"], 64),
                                    "count": int(_num(w.get("count"), int, 0, hi=10**6))})
        since_ts = int(_num(g.get("since_ts"), int, now, hi=now + 86400))
        if since_ts <= 0:   # 0/负数不是合法"起始时间"，别让前端算出"已持续56年"
            since_ts = now
        out.append({
            "category": validate.text(g.get("category"), 64) or "?",
            "label": validate.text(g.get("label"), 64),
            "count": int(_num(g.get("count"), int, 0, hi=10**6)),
            "since_ts": since_ts,
            "workers": workers,
        })
    return out


@app.post("/api/ingest/customers")
def api_ingest_customers(request: Request, body: dict = Body(...)):
    """场地客户报表上报(默认每10分钟一次，整表替换)。"""
    sid, name = _ingest_identity(body)
    _check_ingest_token(request, sid)
    rows = body.get("customers") or []
    if not isinstance(rows, list) or len(rows) > 5000:
        raise HTTPException(400, "customers 非法")
    rows = validate.customers(rows)   # 数值列只进数字、文本去控制符截长(防存储型XSS/500)
    now = int(time.time())
    db.upsert_site(CONN, sid, name if body.get("site") else None, None, now)
    db.replace_customers(CONN, sid, rows, _num(body.get("hours"), int, 24, hi=24 * 90) or 24)
    return {"ok": True, "ts": now, "count": len(rows)}


@app.delete("/api/site/{key}")
def api_delete_site(request: Request, key: str):
    """删除场地及其全部数据(场地下线后清理)。仅 admin；config 里的拉取场地请改配置文件。"""
    sess = _require_login(request)
    if sess.get("role") != "admin":
        raise HTTPException(403, "需要 admin")
    scfg = _find_site(key)
    if scfg and scfg.get("mode") == "pull":
        raise HTTPException(400, "该场地在 config.yaml 的 sites 里(拉取模式)，请改配置文件")
    sid = scfg["id"] if scfg else key
    db.delete_site(CONN, sid)
    _audit(request, sess["user"], "site_delete", sid)
    return {"ok": True}


# ---------- 版本更新(git 部署时可用；仅 admin) ----------
def _require_admin(request: Request):
    sess = _require_login(request)
    if sess.get("role") != "admin":
        raise HTTPException(403, "需要 admin")
    return sess


@app.get("/api/update/check")
def api_update_check(request: Request):
    """对比 git 远端：{behind: 落后提交数, changes: 更新内容}。"""
    _require_admin(request)
    return updater.check((CFG.get("update") or {}).get("branch") or None)


@app.post("/api/update/apply")
def api_update_apply(request: Request):
    """拉取新代码(ff-only+编译自检+失败回滚)并重启服务(需 systemd/NSSM 守护)。"""
    sess = _require_admin(request)
    r = updater.apply((CFG.get("update") or {}).get("branch") or None)
    _audit(request, sess["user"], "update_apply",
           f"{r.get('from')} → {r.get('to')}" if r.get("ok") else f"失败: {r.get('msg')}")
    if r.get("ok"):
        _update_event(f"管理员 {sess['user']} 触发更新 {r['from']} → {r['to']}，服务重启中")
    return r


# ---------- 通知设置(网页可配, 即时生效, 仅 admin) ----------
# secret 字段(bot_token/webhook.url/email.password)一律掩码返回布尔哨兵，绝不回传明文——
# 即使是已登录 admin 的浏览器也不行(防肩窥/浏览器历史/请求日志截屏)。
_WEBHOOK_KINDS = {"wecom", "dingtalk", "raw"}


def _mask_alerts(a):
    a = a or {}
    tg = a.get("telegram") or {}
    wh = a.get("webhook") or {}
    em = a.get("email") or {}
    to = em.get("to") or []
    if isinstance(to, str):   # 容错：旧数据可能存成逗号分隔字符串
        to = [t.strip() for t in to.split(",") if t.strip()]
    return {
        "enabled": bool(a.get("enabled", True)),
        "cooldown": int(a.get("cooldown", 1800) or 0),
        "hashrate_drop_pct": float(a.get("hashrate_drop_pct", 30) or 0),
        "offline_threshold": int(a.get("offline_threshold", 0) or 0),
        "telegram": {"enabled": bool(tg.get("enabled")),
                    "bot_token_set": bool(tg.get("bot_token")),
                    "chat_id": tg.get("chat_id") or ""},
        "webhook": {"enabled": bool(wh.get("enabled")),
                   "url_set": bool(wh.get("url")),
                   "kind": wh.get("kind") or "wecom"},
        "email": {"enabled": bool(em.get("enabled")),
                 "smtp_host": em.get("smtp_host") or "",
                 "smtp_port": int(em.get("smtp_port") or 465),
                 "use_ssl": bool(em.get("use_ssl", True)),
                 "use_starttls": bool(em.get("use_starttls", True)),
                 "username": em.get("username") or "",
                 "password_set": bool(em.get("password")),
                 "from_addr": em.get("from_addr") or "",
                 "to": to,
                 "subject_prefix": em.get("subject_prefix") or ""},
    }


def _settings_str(v, maxlen):
    """字符串类字段统一取值：非字符串标量(数字/布尔)按习惯用法转成字符串接受
    (如 chat_id 填数字很常见)；dict/list 等容器类型明显是畸形输入，一律当空处理——
    否则 str(v) 会把 "{'a': 1}" 这种 Python repr 原样存成 bot_token/密码，
    静默用垃圾覆盖掉原本好好的值(掩码字段留空=保留原值，容器类值也应享受同等保护)。"""
    if v is None or isinstance(v, (dict, list)):
        return ""
    return validate.text(v, maxlen)   # 去掉换行等控制符：防邮件头注入(Subject/From/To)


def _settings_port(v, default):
    try:
        p = int(v)
    except (TypeError, ValueError):
        return default
    return p if 1 <= p <= 65535 else default


@app.get("/api/settings")
def api_settings_get(request: Request):
    """当前通知设置(掩码后)。仅 admin——viewer 不该看到通道是否配置。"""
    _require_admin(request)
    return {"ok": True, "alerts": _mask_alerts(CFG.get("alerts"))}


@app.post("/api/settings")
def api_settings_post(request: Request, body: dict = Body(...)):
    """保存通知设置：{"alerts": {...部分字段...}}。

    原地改 CFG["alerts"] 的嵌套字典(不能整体替换——collector.py 里 Notifier 持有的是
    同一个 dict 引用，替换成新对象它会读不到)，改完立即生效，无需重启；再持久化到
    cloud_settings.json，进程重启后 apply_settings() 会重新叠加回来。
    掩码字段(bot_token/webhook.url/email.password)留空/缺省=保留原值，不清空
    (标准"留空不改"表单语义)。所有数值/字符串字段按本文件既有的 _num 风格钳制，
    非法输入只回退默认值，绝不 500。
    """
    sess = _require_admin(request)
    inb = body.get("alerts")
    if not isinstance(inb, dict):
        raise HTTPException(400, "缺少 alerts")
    _new_url = _settings_str((inb.get("webhook") or {}).get("url") if isinstance(inb.get("webhook"), dict)
                             else None, 1024)
    if _new_url and not _new_url.lower().startswith(("https://", "http://")):   # 先校验，再动 CFG
        raise HTTPException(400, "Webhook URL 必须以 https:// 或 http:// 开头")
    a = CFG.setdefault("alerts", {})
    tg = a.setdefault("telegram", {})
    wh = a.setdefault("webhook", {})
    em = a.setdefault("email", {})

    if "enabled" in inb:
        a["enabled"] = bool(inb["enabled"])
    if "cooldown" in inb:
        a["cooldown"] = int(_num(inb.get("cooldown"), int, a.get("cooldown", 1800), hi=86400))
    if "hashrate_drop_pct" in inb:
        a["hashrate_drop_pct"] = _num(inb.get("hashrate_drop_pct"), float,
                                      a.get("hashrate_drop_pct", 30), hi=100)
    if "offline_threshold" in inb:
        a["offline_threshold"] = int(_num(inb.get("offline_threshold"), int,
                                          a.get("offline_threshold", 0), hi=10**6))

    itg = inb.get("telegram")
    if isinstance(itg, dict):
        if "enabled" in itg:
            tg["enabled"] = bool(itg["enabled"])
        v = _settings_str(itg.get("bot_token"), 256)
        if v:
            tg["bot_token"] = v
        if "chat_id" in itg:
            tg["chat_id"] = _settings_str(itg.get("chat_id"), 128)

    iwh = inb.get("webhook")
    if isinstance(iwh, dict):
        if "enabled" in iwh:
            wh["enabled"] = bool(iwh["enabled"])
        v = _settings_str(iwh.get("url"), 1024)
        if v:
            wh["url"] = v
        if "kind" in iwh:
            k = str(iwh.get("kind") or "wecom").strip().lower()
            wh["kind"] = k if k in _WEBHOOK_KINDS else "wecom"

    iem = inb.get("email")
    if isinstance(iem, dict):
        if "enabled" in iem:
            em["enabled"] = bool(iem["enabled"])
        if "smtp_host" in iem:
            em["smtp_host"] = _settings_str(iem.get("smtp_host"), 256)
        if "smtp_port" in iem:
            em["smtp_port"] = _settings_port(iem.get("smtp_port"), em.get("smtp_port", 465))
        if "use_ssl" in iem:
            em["use_ssl"] = bool(iem["use_ssl"])
        if "use_starttls" in iem:
            em["use_starttls"] = bool(iem["use_starttls"])
        if "username" in iem:
            em["username"] = _settings_str(iem.get("username"), 256)
        v = _settings_str(iem.get("password"), 256)
        if v:
            em["password"] = v
        if "from_addr" in iem:
            em["from_addr"] = _settings_str(iem.get("from_addr"), 256)
        if "to" in iem:
            raw_to = iem.get("to")
            if isinstance(raw_to, str):
                raw_to = raw_to.split(",")
            if isinstance(raw_to, list):
                em["to"] = [validate.text(t, 256) for t in raw_to[:50] if validate.text(t, 256)]
        if "subject_prefix" in iem:
            em["subject_prefix"] = _settings_str(iem.get("subject_prefix"), 64)

    appconfig.update_settings({"alerts": copy.deepcopy(a)})
    # 只记改了哪些块，不记值(值里有 bot_token/密码)
    _audit(request, sess["user"], "settings_save", ",".join(sorted(inb.keys()))[:200])
    return {"ok": True, "alerts": _mask_alerts(a)}


@app.post("/api/notify/test")
def api_notify_test(request: Request):
    """给当前每个已启用的通道各发一条测试消息，逐通道回报 true成功/false失败/null未启用未尝试。
    是"发送测试通知"按钮的后端：管理员配完立刻知道哪个通道没打通，不用等真实告警触发。"""
    sess = _require_admin(request)
    result = COLLECTOR.notifier.send_test("🔔 云端总览测试通知：收到此消息说明该通道配置正确。")
    _audit(request, sess["user"], "notify_test",
           ",".join(f"{k}={v}" for k, v in result.items() if v is not None))
    return {"ok": True, "result": result}


# ---------- 账号与安全中心 ----------
def _current_token(request: Request):
    return request.cookies.get(auth.COOKIE) or ""


@app.post("/api/password")
def api_password(request: Request, body: dict = Body(...)):
    """修改自己的密码(任何已登录用户)。成功后该用户其它会话全部下线，当前会话保留。"""
    if not auth.enabled(CFG):
        raise HTTPException(400, "登录未开启，无密码可改")
    sess = _require_login(request)
    src = _client_ip(request)
    if auth.locked(src):
        raise HTTPException(429, "失败次数过多，请5分钟后再试")
    err = auth.change_password(CFG, sess["user"], str(body.get("old") or "")[:256],
                               str(body.get("new") or "")[:256], keep_token=_current_token(request))
    if err:
        if err == "原密码错误":   # 被盗会话不能拿这个接口无限试原密码
            auth._record_fail(src, time.time())
        _audit(request, sess["user"], "password_fail", err)
        raise HTTPException(400, err)
    _audit(request, sess["user"], "password_change")
    return {"ok": True}


@app.get("/api/security")
def api_security(request: Request):
    """安全中心(仅 admin)：自检清单 + 最近审计记录 + 在线会话数。"""
    _require_admin(request)
    checks = seccheck.run(CFG, CFG_PATH, appconfig.SETTINGS_FILE)
    if not _is_https(request):
        checks.insert(0, {"level": "high", "msg": "当前通过 http 访问：密码和登录 Cookie 在网络上明文传输。"
                                                  "公网部署必须前置 nginx/caddy 配 https(见 docs/DEPLOY.md)"})
    return {"ok": True, "checks": checks, "sessions": auth.session_count(),
            "audit": db.audit_recent(CONN, 100)}


@app.post("/api/sessions/revoke")
def api_sessions_revoke(request: Request):
    """强制其它所有会话下线(怀疑密码/电脑泄露时一键踢人)，保留当前管理员自己。"""
    sess = _require_admin(request)
    n = auth.revoke_all(keep_token=_current_token(request))
    _audit(request, sess["user"], "sessions_revoke", f"踢下线 {n} 个")
    return {"ok": True, "revoked": n}


@app.get("/api/health")
def api_health():
    """云端服务自身心跳(免登录、不含业务数据)：外部看门狗探活用。"""
    now = int(time.time())
    return {"ok": True, "ts": now,
            "last_poll_age_s": (now - COLLECTOR.last_poll_ts) if COLLECTOR.last_poll_ts else None}


# ---------- 安全中间件 ----------
# CSP：脚本只许本站文件(Chart.js 已放 web/vendor 本地，不再走外部 CDN——CDN 被劫持=管理员
# 浏览器里跑别人的代码)；禁止被别的网站用 iframe 嵌套(点击劫持)。style 允许 inline 是因为
# 卡片模板里有 style="" 属性；脚本不允许 inline，即使哪里漏了转义，注入的 <script>/onerror 也不执行。
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
_STATE_CHANGING = ("POST", "PUT", "PATCH", "DELETE")
CSRF_HEADER = "x-co-csrf"


@app.middleware("http")
async def _security_mw(request: Request, call_next):
    """① CSRF：浏览器会话发起的写操作必须带自定义头 X-CO-CSRF(前端 fetch 统一加)。
    别的网站用表单/跨域请求没法加自定义头(会触发 CORS 预检，本服务不放行)，
    所以即使管理员登录着去点了恶意链接，也改不了设置/触发不了更新。
    场地上报 /api/ingest/* 用 Bearer token 不用 Cookie，不受此限。
    ② 统一加安全响应头；前端文件禁强缓存；/api/* 响应禁任何缓存(含代理)。"""
    path = request.url.path
    if (request.method in _STATE_CHANGING and path.startswith("/api/")
            and not path.startswith("/api/ingest/") and not request.headers.get(CSRF_HEADER)):
        return JSONResponse({"detail": "缺少 X-CO-CSRF 请求头(防跨站请求伪造)"}, status_code=403)
    resp = await call_next(request)
    h = resp.headers
    h["Content-Security-Policy"] = _CSP
    h["X-Frame-Options"] = "DENY"
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if _is_https(request):
        h["Strict-Transport-Security"] = "max-age=31536000"
    if path.startswith("/api/"):
        h["Cache-Control"] = "no-store"
    elif path.startswith("/web/") or path == "/":
        # 前端文件禁强缓存(每次向服务器复核, 未变返回304)——否则版本更新后
        # 浏览器拿着旧 JS 渲染新接口, 要手动 Ctrl+F5 才恢复。文件只有几KB, 代价可忽略。
        h["Cache-Control"] = "no-cache"
    return resp


class _BodyLimit:
    """请求体大小上限(纯 ASGI 层，早于 JSON 解析)：正常上报几 KB，客户报表最多几百 KB。
    裸跑 8900 没有 nginx client_max_body_size 兜底时，防一个几百 MB 的 POST 把内存吃光。
    无 Content-Length 的分块上传直接拒绝(本系统的场地端/浏览器都会带 Content-Length)。"""

    def __init__(self, app, limit):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in _STATE_CHANGING:
            hdr = dict(scope.get("headers") or [])
            cl = hdr.get(b"content-length")
            status = None
            if cl is None:
                if b"chunked" in (hdr.get(b"transfer-encoding") or b"").lower():
                    status = 411
            else:
                try:
                    if int(cl) > self.limit:
                        status = 413
                except ValueError:
                    status = 400
            if status:
                resp = JSONResponse({"detail": f"请求体过大或缺少长度(上限 {self.limit} 字节)"},
                                    status_code=status)
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


app.add_middleware(_BodyLimit, limit=int(CFG.get("server", {}).get("max_body_bytes") or 2 * 1024 * 1024))


# ---------- 静态面板 ----------

app.mount("/web", StaticFiles(directory=os.path.join(BASE, "web")), name="web")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "web", "index.html"))


def _print_seccheck():
    items = seccheck.run(CFG, CFG_PATH, appconfig.SETTINGS_FILE)
    icon = {"high": "✖ 高危", "warn": "⚠ 注意", "info": "ℹ 建议"}
    for it in items:
        print(f"{icon[it['level']]}  {it['msg']}")
    if any(it["level"] == "high" for it in items):
        print("   ↑ 高危项上线前必须处理(完整清单: python seccheck.py；网页: 管理员「🔒 安全中心」)")


if __name__ == "__main__":
    n_pull = len(COLLECTOR.pull_sites())
    icfg = CFG.get("ingest") or {}
    ingest_on = bool(icfg.get("token") or icfg.get("tokens"))
    notify_on = COLLECTOR.notifier.channels_on()
    print(f"云端总览启动：拉取场地 {n_pull} 个，上报接口{'已开启' if ingest_on else '未配置(关闭)'}"
          f"(推送场地首次上报自动注册)，告警通知{'已开启' if notify_on else '未配置(仅面板显示)'}；"
          f"数据库 → {_db_path}")
    COLLECTOR.start()
    srv = CFG.get("server", {})
    uvicorn.run(app, host=srv.get("host", "0.0.0.0"), port=int(srv.get("port", 8900)),
                log_level="warning")
