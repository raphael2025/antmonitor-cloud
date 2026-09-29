#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端告警通知：场地失联/监控停滞/恢复 + 算力骤降，主动推 Telegram / Webhook。

为什么必须在云端做：场地断电时本地监控跟着一起死，它自己没法呼救——
全网唯一知道出事的就是云端。没有主动通知，云端只是展示板不是值班员。

只报"本地报不了的事"：unreachable(失联)/stalled(监控停滞)/算力骤降 及其恢复。
场地本地的普通告警(单机掉线等)本地自己有 Telegram/语音，云端不重复轰炸。

例外：`offline_threshold` > 0 时，单场地掉线台数(total-online)达到这个数也会报——
这不是"任何单机掉线都报"(那还是本地的事)，是"掉线规模大到老板该知道"这条单独的线，
数台数比本地那种"哪台机器坏了"的告警粒度粗得多，两者不冲突。

config.yaml：
alerts:
  enabled: true
  cooldown: 1800            # 同场地同类告警冷却(秒)，恢复通知不受限
  hashrate_drop_pct: 30     # 当前10分钟均值比 45~90 分钟前基线跌超此百分比→告警；0=关
  offline_threshold: 0      # 单场地掉线台数(total-online)达到此值→告警；0=关
  telegram: {enabled: false, bot_token: "", chat_id: ""}
  webhook:  {enabled: false, url: "", kind: wecom}   # wecom|dingtalk(同构)|raw
  email:    {enabled: false, smtp_host: "", smtp_port: 465, use_ssl: true,
             username: "", password: "", from_addr: "", to: [], subject_prefix: "[矿场云端]"}
"""
import ipaddress
import smtplib
import socket
import time
from email.header import Header
from email.mime.text import MIMEText
from urllib.parse import urlsplit

import requests

import db

_TYPE_LABEL = {"air": "风冷", "hydro": "水冷", "mixed": "混合"}


class Notifier:
    def __init__(self, cfg, conn):
        a = cfg.get("alerts") or {}
        self.cfg = a
        self.conn = conn
        self.enabled = bool(a.get("enabled", True))
        self.cooldown = int(a.get("cooldown", 1800))
        self.drop_pct = float(a.get("hashrate_drop_pct", 30) or 0)
        self.prev = None          # site_id -> state(首轮只记不发，防重启风暴)
        self.active_drop = {}     # site_id -> 触发时基线(TH/s)
        self.active_offline = {}  # site_id -> True(掉线台数超阈值中，去抖动用)
        self.last_sent = {}       # (site_id, type) -> ts

    def channels_on(self):
        return bool((self.cfg.get("telegram") or {}).get("enabled")
                    or (self.cfg.get("webhook") or {}).get("enabled")
                    or (self.cfg.get("email") or {}).get("enabled"))

    # ---- 主入口：collector 每轮调用 ----
    def tick(self, sites, site_state_fn):
        """sites: [{id,name,type,mode}]; site_state_fn(id, snap_ok, mode, now) -> state"""
        # enabled 现场读 self.cfg（不用 __init__ 时缓存的 self.enabled）：
        # 网页改配置是原地改同一个 dict，这样才能不重启立即生效。
        if not (bool(self.cfg.get("enabled", True)) and self.channels_on()):
            return
        try:
            self._tick(sites, site_state_fn)
        except Exception as e:
            print(f"[notify] 评估异常: {e}")

    def _tick(self, sites, site_state_fn):
        now = int(time.time())
        last_ok = db.latest_ok_snapshots(self.conn)
        cur = {}
        for s in sites:
            cur[s["id"]] = (site_state_fn(s["id"], last_ok.get(s["id"]), s["mode"], now), s)
        if self.prev is None:                     # 进程刚启动：记状态不发通知
            self.prev = {k: v[0] for k, v in cur.items()}
            return
        for sid, (state, s) in cur.items():
            old = self.prev.get(sid)
            if old is not None and state != old:
                self._on_transition(sid, s, old, state, last_ok.get(sid), now)
            self.prev[sid] = state
        drop_pct = float(self.cfg.get("hashrate_drop_pct", 30) or 0)   # 现场读，见 tick() 注释
        if drop_pct > 0:
            for sid, (state, s) in cur.items():
                if state in ("ok", "alert"):
                    self._check_drop(sid, s, now)
        offline_threshold = int(self.cfg.get("offline_threshold", 0) or 0)   # 现场读
        if offline_threshold > 0:
            for sid, (state, s) in cur.items():
                if state in ("ok", "alert"):
                    self._check_offline(sid, s, last_ok.get(sid), now)

    # ---- 状态跳变 ----
    def _on_transition(self, sid, s, old, new, snap, now):
        label = f"{s['name']}({_TYPE_LABEL.get(s['type'], s['type'])})"
        if new == "unreachable":
            age = (now - snap["ts"]) // 60 if snap else None
            tail = (f"，最后在线 {snap['online']}/{snap['total']}"
                    f"（{max(1, age)} 分钟前）" if snap else "，尚无历史数据")
            self._send(sid, "unreachable", f"⚠ 场地失联: {label} 收不到数据{tail}")
        elif new == "stalled":
            self._send(sid, "stalled",
                       f"⏸ 监控停滞: {label} 机器可达但本地扫描已 "
                       f"{(snap.get('scan_age_s') or 0) // 60} 分钟未更新，请上场地机器检查监控进程")
        elif old in ("unreachable", "stalled") and new in ("ok", "alert"):
            tail = (f"，当前在线 {snap['online']}/{snap['total']}" if snap else "")
            self._send(sid, "recovered", f"✅ 场地恢复: {label}{tail}", recovery=True)

    # ---- 算力骤降 ----
    def _check_drop(self, sid, s, now):
        drop_pct = float(self.cfg.get("hashrate_drop_pct", 30) or 0)   # 现场读，见 tick() 注释
        base = db.avg_hashrate(self.conn, sid, now - 5400, now - 2700)   # 45~90分钟前
        curr = db.avg_hashrate(self.conn, sid, now - 600, now)           # 最近10分钟
        if base is None or curr is None or base < 1:
            return
        label = f"{s['name']}({_TYPE_LABEL.get(s['type'], s['type'])})"
        threshold = base * (1 - drop_pct / 100.0)
        if sid not in self.active_drop and curr < threshold:
            self.active_drop[sid] = base
            pct = 100 * (base - curr) / base
            self._send(sid, "hashrate_drop",
                       f"📉 算力骤降: {label} {_fmt_hr(base)} → {_fmt_hr(curr)} (-{pct:.0f}%)")
        elif sid in self.active_drop and curr >= base * (1 - drop_pct / 200.0):
            self.active_drop.pop(sid, None)      # 回升过半程才算恢复，防边界抖动
            self._send(sid, "hashrate_recovered",
                       f"✅ 算力恢复: {label} 当前 {_fmt_hr(curr)}", recovery=True)

    # ---- 掉线台数超阈值 ----
    def _check_offline(self, sid, s, snap, now):
        if not snap:
            return
        threshold = int(self.cfg.get("offline_threshold", 0) or 0)   # 现场读，见 tick() 注释
        if threshold <= 0:
            return
        total, online = snap.get("total") or 0, snap.get("online") or 0
        offline = max(0, total - online)
        label = f"{s['name']}({_TYPE_LABEL.get(s['type'], s['type'])})"
        if sid not in self.active_offline and offline >= threshold:
            self.active_offline[sid] = True
            self._send(sid, "offline_count",
                       f"🔌 掉线告警: {label} 掉线 {offline}/{total} 台(≥{threshold})")
        # 回落到阈值一半以下才算恢复，跟算力骤降同一个防抖动思路，别在临界值来回抖
        elif sid in self.active_offline and offline <= threshold // 2:
            self.active_offline.pop(sid, None)
            self._send(sid, "offline_recovered",
                       f"✅ 掉线恢复: {label} 掉线降至 {offline}/{total} 台", recovery=True)

    # ---- 发送 ----
    def _send(self, sid, ntype, msg, recovery=False):
        now = int(time.time())
        cooldown = int(self.cfg.get("cooldown", 1800) or 0)   # 现场读，见 tick() 注释
        if not recovery:
            last = self.last_sent.get((sid, ntype), 0)
            if now - last < cooldown:
                return
            self.last_sent[(sid, ntype)] = now
        ok = False
        tg = self.cfg.get("telegram") or {}
        if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
            ok = _send_telegram(tg, msg) or ok
        wh = self.cfg.get("webhook") or {}
        if wh.get("enabled") and wh.get("url"):
            ok = _send_webhook(wh, msg) or ok
        em = self.cfg.get("email") or {}
        if em.get("enabled") and em.get("smtp_host"):
            ok = _send_email(em, msg) or ok
        try:
            db.notify_log_add(self.conn, now, sid, ntype, msg, ok)
        except Exception:
            pass
        try:   # GBK 控制台打不出 emoji 也不能影响告警流程(日志已入库)
            print(f"[notify] {'OK' if ok else 'FAIL'} {msg}")
        except Exception:
            pass

    # ---- 测试通知(管理面板"发送测试通知"按钮) ----
    def send_test(self, msg):
        """给当前每个已启用的通道各发一条 msg，不占用/不受冷却限制。
        返回 {"telegram": bool, "webhook": bool, "email": bool}，某通道未启用则值为 None
        (未尝试，区别于 False=尝试过但失败)——管理员靠这个立刻分辨"没配"还是"配错了"。
        每个实际尝试的通道额外写一行 notify_log(type="test")，与真实告警共用同一张
        排查表，不单独维护一套"为什么测试没收到"的路径。"""
        now = int(time.time())
        result = {"telegram": None, "webhook": None, "email": None}
        tg = self.cfg.get("telegram") or {}
        if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
            ok = _send_telegram(tg, msg)
            result["telegram"] = ok
            self._log_test(now, "telegram", ok)
        wh = self.cfg.get("webhook") or {}
        if wh.get("enabled") and wh.get("url"):
            ok = _send_webhook(wh, msg)
            result["webhook"] = ok
            self._log_test(now, "webhook", ok)
        em = self.cfg.get("email") or {}
        if em.get("enabled") and em.get("smtp_host"):
            ok = _send_email(em, msg)
            result["email"] = ok
            self._log_test(now, "email", ok)
        return result

    def _log_test(self, now, channel, ok):
        try:   # msg 固定文案，不含任何密钥/URL——notify_log 是排查表，绝不能落地敏感信息
            db.notify_log_add(self.conn, now, None, "test", f"[{channel}] 管理员手动测试", ok)
        except Exception:
            pass


def _fmt_hr(th):
    if th >= 1e6:
        return f"{th / 1e6:.2f} EH/s"
    if th >= 1e3:
        return f"{th / 1e3:.1f} PH/s"
    return f"{th:.0f} TH/s"


def _redact(text, *secrets):
    """把 secrets 里出现的敏感值从 text 中抹掉再返回，用于打印/落库前的脱敏。

    为什么需要：requests 的网络异常(ConnectTimeout/ConnectionError/...)会把完整请求 URL
    原样塞进异常文本——而 Telegram 的 URL 里带 bot_token、Webhook 的 URL 常带 ?key=xxx，
    一旦异常文本直接 print() 出去，secret 就原样落进了服务器控制台/日志文件，绕开了
    GET /api/settings 的掩码防线。故所有可能带 secret 的异常信息打印前都要先过这层。
    """
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(str(s), "***")
    return text


def _url_tail(url):
    """URL 去掉 scheme+host 后的 path+query。requests/urllib3 的连接类异常
    (ConnectionError/Timeout/...)常把 host/port 拆开单独展示("...Pool(host=.., port=..)
    ... with url: /path?query...")，异常文本里其实并不包含完整 URL 字符串——对完整
    URL 做整串替换会因为片段不完整而漏网(secret 恰好就编码在 path/query 里，如
    ?key=xxx)，所以要把这段单独摘出来也作为一个脱敏目标传给 _redact()。"""
    try:
        p = urlsplit(url or "")
        return p.path + (("?" + p.query) if p.query else "")
    except Exception:
        return ""


def _internal_target(host):
    """host 解析到本机/链路本地/保留地址 → 返回原因字符串，否则 None。

    为什么要拦：webhook URL / SMTP 主机是网页可改的。万一管理员会话被盗，对方可把它指向
    云服务器自己的内网服务(如云厂商元数据 169.254.169.254、只监听 127.0.0.1 的管理端口)，
    借"发送测试通知"让服务器替他去打内网(SSRF)。正常的企业微信/钉钉/邮箱都在公网，不受影响；
    确需发往内网接收端的，在 config.yaml 该通道下写 allow_internal: true(网页改不了这个开关)。
    局域网私有地址(10./172.16./192.168.)默认放行——自建接收端常见。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return None   # 解析不了就让真正的发送去报错
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            continue
        if ip.version == 6 and ip.ipv4_mapped:   # ::ffff:127.0.0.1 这类写法
            ip = ip.ipv4_mapped
        if (ip.is_loopback or ip.is_link_local or ip.is_unspecified
                or ip.is_multicast or ip.is_reserved):
            return f"{host} 解析到内部地址 {ip}"
    return None


def _send_telegram(tg, msg):
    token = tg.get("bot_token") or ""
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": tg["chat_id"], "text": msg}, timeout=10)
        return r.ok
    except Exception as e:
        print(f"[notify] telegram 发送失败: {_redact(e, token)}")
        return False


def _send_webhook(wh, msg):
    kind = (wh.get("kind") or "wecom").lower()
    if kind in ("wecom", "dingtalk"):   # 企业微信/钉钉群机器人同构
        payload = {"msgtype": "text", "text": {"content": msg}}
    else:                               # raw: 任意自定义接收端
        payload = {"text": msg}
    url = wh.get("url") or ""
    try:
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            print("[notify] webhook URL 必须是 http(s)://，已跳过")
            return False
        if not wh.get("allow_internal"):
            why = _internal_target(p.hostname)
            if why:
                print(f"[notify] webhook 拒绝发往内部地址({why})；确需如此请在 config.yaml "
                      f"alerts.webhook 下设 allow_internal: true")
                return False
        # 不跟随重定向：防止公网地址 302 到内网绕过上面的检查
        r = requests.post(url, json=payload, timeout=10, allow_redirects=False)
        return r.ok
    except Exception as e:
        print(f"[notify] webhook 发送失败: {_redact(e, url, _url_tail(url))}")
        return False


def _send_email(em, msg):
    to = em.get("to") or []
    if isinstance(to, str):   # 容错：填成逗号分隔字符串也认
        to = [t.strip() for t in to.split(",") if t.strip()]
    if not to:
        print("[notify] email 未配置收件人(to)，跳过发送")
        return False
    from_addr = em.get("from_addr") or em.get("username") or ""
    mail = MIMEText(msg, "plain", "utf-8")
    prefix = em.get("subject_prefix") or ""
    mail["Subject"] = Header(f"{prefix} {msg}".strip()[:78], "utf-8")   # 避免超长主题
    mail["From"] = from_addr
    mail["To"] = ", ".join(to)
    try:
        host = em["smtp_host"]
        port = int(em.get("smtp_port") or 465)
        if not em.get("allow_internal"):
            why = _internal_target(host)
            if why:
                print(f"[notify] email 拒绝连接内部地址({why})；确需如此请在 config.yaml "
                      f"alerts.email 下设 allow_internal: true")
                return False
        cls = smtplib.SMTP_SSL if em.get("use_ssl", True) else smtplib.SMTP
        with cls(host, port, timeout=10) as s:
            if not em.get("use_ssl", True) and em.get("use_starttls", True):
                s.starttls()
            if em.get("username") and em.get("password"):
                s.login(em["username"], em["password"])
            s.sendmail(from_addr, to, mail.as_string())
        return True
    except Exception as e:
        # 防御性脱敏：SMTP 异常一般不回显密码，但保持三个发送函数处理方式一致，
        # 也顺手兜住服务商把 AUTH 报文原样回显在错误里的极端情况。
        print(f"[notify] email 发送失败: {_redact(e, em.get('password'))}")
        return False
