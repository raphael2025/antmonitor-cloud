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
  merge_window_sec: 45      # 建议 45：同一场地短时多事件合并成一条再发；0=立即逐条发
  telegram: {enabled: false, bot_token: "", chat_id: ""}
  webhook:  {enabled: false, url: "", kind: wecom}   # wecom|dingtalk(同构)|raw
  email:    {enabled: false, smtp_host: "", smtp_port: 465, use_ssl: true,
             username: "", password: "", from_addr: "", to: [], subject_prefix: "[矿场云端]"}
"""
import ipaddress
import smtplib
import socket
import time
import json
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from urllib.parse import urlsplit

import requests

import db

_TYPE_LABEL = {"air": "风冷", "hydro": "水冷", "mixed": "混合"}
# 健康语义：unreachable=上报过期收不到；stalled=还能报但扫描停更；site_down=场内自报全场掉线
_NOTICE_GROUPS = (
    frozenset(("unreachable", "stalled", "site_down", "recovered")),
    frozenset(("hashrate_drop", "hashrate_recovered")),
    frozenset(("offline_count", "offline_recovered")),
)
_FRESH_STATES = frozenset(("ok", "alert", "site_down"))  # 仍有可用快照，可评算力/掉线台数


class Notifier:
    def __init__(self, cfg, conn):
        a = cfg.get("alerts")
        if not isinstance(a, dict):
            a = cfg["alerts"] = {}
        self.cfg = a
        self.poll = cfg.get("poll") or {}
        self.conn = conn
        self.enabled = bool(a.get("enabled", True))
        self.cooldown = int(a.get("cooldown", 1800))
        # 规则关闭时暂停投递，但用最近启用的阈值确认旧事件是否结束，避免重开补发过期告警。
        self.drop_pct = float(a.get("hashrate_drop_pct", 30) or 0)
        self.offline_threshold = int(a.get("offline_threshold", 0) or 0)
        self.prev = None          # site_id -> state(无检查点时首轮只记不发)
        self.active_drop = {}     # site_id -> 触发时基线(TH/s)
        self.active_offline = {}  # site_id -> True(掉线台数超阈值中，去抖动用)
        self.last_sent = {}       # (site_id, type) -> ts
        # 仅当前事件的未送达通道。成功通道不重发；失败按 60s~15min 退避。
        # SQLite 检查点保存活动事件、原始基线、重试计划和已送达通道。
        self.pending = {}         # (site_id, type) -> {msg, remaining, delivered, attempts, due, created}
        self._epochs = {}         # 删除代次：防在途任务复活已删/已恢复场地的旧事件。
        self._saved = {}          # 单场地变更检测，空转轮次不写 SQLite。
        self._delivery_budget = None
        states, pending, sent = db.notification_load(conn)
        if states:
            self.prev = {r["site_id"]: r["state"] for r in states}
        for row in states:
            sid = row["site_id"]
            self._epochs[sid] = row["epoch"]
            if row["drop_baseline"] is not None:
                self.active_drop[sid] = row["drop_baseline"]
            if row["offline_active"]:
                self.active_offline[sid] = True
            if self.drop_pct <= 0:
                self.drop_pct = row["drop_pct"]
            if self.offline_threshold <= 0:
                self.offline_threshold = row["offline_threshold"]
        for row in pending:
            self.pending[(row["site_id"], row["type"])] = {
                "msg": row["msg"], "remaining": set(json.loads(row["remaining_json"])),
                "delivered": set(json.loads(row["delivered_json"])), "created": row["created_ts"],
                "due": row["due_ts"], "attempts": row["attempts"]}
        self.last_sent = {(r["site_id"], r["type"]): r["sent_ts"] for r in sent}

    def _forget_site(self, sid):
        if self.prev is not None:
            self.prev.pop(sid, None)
        self.active_drop.pop(sid, None)
        self.active_offline.pop(sid, None)
        for table in (self.pending, self.last_sent):
            for key in list(table):
                if key[0] == sid:
                    table.pop(key, None)

    def _persist(self, site_id=None):
        """先保存再发请求；每个成功通道都马上提交回执，不等其它通道。"""
        targets = list(self._epochs.items()) if site_id is None else [(site_id, self._epochs.get(site_id))]
        for sid, epoch in targets:
            if epoch is None:
                continue
            state = None
            if self.prev is not None and sid in self.prev:
                state = {"state": self.prev[sid], "drop_baseline": self.active_drop.get(sid),
                         "offline_active": sid in self.active_offline, "drop_pct": self.drop_pct,
                         "offline_threshold": self.offline_threshold}
            pending = {kind: notice for (site, kind), notice in self.pending.items() if site == sid}
            sent = {kind: ts for (site, kind), ts in self.last_sent.items() if site == sid}
            signature = json.dumps([state, pending, sent], sort_keys=True, default=sorted)
            if signature == self._saved.get(sid):
                continue
            if not db.notification_save(self.conn, sid, epoch, state, pending, sent):
                self._forget_site(sid)
                self._saved.pop(sid, None)
                continue
            self._saved[sid] = signature

    def channels_on(self):
        return bool((self.cfg.get("telegram") or {}).get("enabled")
                    or (self.cfg.get("webhook") or {}).get("enabled")
                    or (self.cfg.get("email") or {}).get("enabled"))

    # ---- 主入口：collector 每轮调用 ----
    def tick(self, sites, site_state_fn):
        """sites: [{id,name,type,mode}]; site_state_fn(id, snap_ok, mode, now) -> state"""
        # enabled 现场读 self.cfg（不用 __init__ 时缓存的 self.enabled）：
        # 网页改配置是原地改同一个 dict，这样才能不重启立即生效。
        if self.prev is None and not (bool(self.cfg.get("enabled", True)) and self.channels_on()):
            return
        # 开始值班后即使暂时关通知，也继续确认事件是否恢复/场地是否删除。
        # 只暂停投递；未送达通道不能忘记，否则修好配置重开后原故障永远不会再通知。
        self._delivery_budget = 20  # 每轮最多 20 个通道请求，避免停机期间积压在重启时集中轰炸。
        try:
            self._tick(sites, site_state_fn)
        except Exception as e:
            print(f"[notify] 评估异常: {e}")
        finally:
            self._delivery_budget = None

    def _tick(self, sites, site_state_fn):
        now = int(time.time())
        last_ok = db.latest_ok_snapshots(self.conn)
        cur = {}
        for s in sites:
            sid = s["id"]
            epoch, deleted = db.notification_generation(self.conn, sid)
            if deleted:
                self._forget_site(sid)
                continue
            if sid in self._epochs and self._epochs[sid] != epoch:
                self._forget_site(sid)
                self._saved.pop(sid, None)
            self._epochs[sid] = epoch
            cur[sid] = (site_state_fn(sid, last_ok.get(sid), s["mode"], now), s)
        # 删除场地后不能继续补发旧告警；也清理原事件与冷却，避免恢复同 ID 时串状态。
        for table in (self.active_drop, self.active_offline):
            for sid in list(table):
                if sid not in cur:
                    table.pop(sid, None)
        for table in (self.pending, self.last_sent):
            for key in list(table):
                if key[0] not in cur:
                    table.pop(key, None)
        if self.prev is None:                     # 首次安装/无检查点：记状态不发通知。
            self.prev = {k: v[0] for k, v in cur.items()}
            self._persist()
            return
        self.prev = {sid: state for sid, state in self.prev.items() if sid in cur}
        for sid, (state, s) in cur.items():
            old = self.prev.get(sid)
            self.prev[sid] = state  # 与新事件一起持久化；中途崩溃不能重复识别同一次跳变。
            if old is not None and state != old:
                self._on_transition(sid, s, old, state, last_ok.get(sid), now)
        drop_pct = float(self.cfg.get("hashrate_drop_pct", 30) or 0)   # 现场读，见 tick() 注释
        if drop_pct > 0:
            self.drop_pct = drop_pct
        if self.drop_pct > 0:
            for sid, (state, s) in cur.items():
                if state in _FRESH_STATES and db.snapshot_quality(last_ok.get(sid) or {}, now,
                        int(self.poll.get("stalled_after_s", 1200)))["hashrate_ths"]:
                    self._check_drop(sid, s, now)
        offline_threshold = int(self.cfg.get("offline_threshold", 0) or 0)   # 现场读
        if offline_threshold > 0:
            self.offline_threshold = offline_threshold
        if self.offline_threshold > 0:
            for sid, (state, s) in cur.items():
                if state in _FRESH_STATES:
                    self._check_offline(sid, s, last_ok.get(sid), now)
        self._retry_pending(cur, last_ok, now)
        self._persist()

    # ---- 状态跳变 ----
    def _on_transition(self, sid, s, old, new, snap, now):
        label = f"{s['name']}({_TYPE_LABEL.get(s['type'], s['type'])})"
        if new == "unreachable":
            age = (now - snap["ts"]) // 60 if snap else None
            tail = (f"，最后在线 {snap['online']}/{snap['total']}"
                    f"（{max(1, age)} 分钟前）" if snap else "，尚无历史数据")
            self._send(sid, "unreachable", f"⚠ 场地失联: {label} 收不到上报（上报过期）{tail}")
        elif new == "stalled":
            age = db.scan_age(snap or {}, now)
            minutes = str(age // 60) if age is not None else "未知"
            self._send(sid, "stalled",
                       f"⏸ 监控停滞: {label} 上报仍在但本地扫描已 "
                       f"{minutes} 分钟未更新（心跳/扫描停更），请上场地机器检查监控进程")
        elif new == "site_down":
            offline = ""
            if snap and snap.get("total") is not None and snap.get("online") is not None:
                offline = f"，在线 {snap['online']}/{snap['total']}"
            self._send(sid, "site_down",
                       f"🔴 场内自报全场掉线: {label}{offline}（场地监控可达，问题在场内供电/网络）")
        elif old in ("unreachable", "stalled", "site_down") and new in ("ok", "alert"):
            tail = (f"，当前在线 {snap['online']}/{snap['total']}" if snap else "")
            self._send(sid, "recovered", f"✅ 场地恢复: {label}{tail}", recovery=True)

    # ---- 算力骤降 ----
    def _check_drop(self, sid, s, now):
        drop_pct = float(self.cfg.get("hashrate_drop_pct", 30) or 0) or self.drop_pct
        stalled_after = int(self.poll.get("stalled_after_s", 1200))
        # 已触发事件固定使用原基线。持续低算力会逐渐拉低滚动基线，不能据此报“恢复”。
        base = self.active_drop.get(sid)
        if base is None:
            base = db.avg_hashrate(self.conn, sid, now - 5400, now - 2700, stalled_after)
        curr = db.avg_hashrate(self.conn, sid, now - 600, now, stalled_after)           # 最近10分钟
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
        threshold = int(self.cfg.get("offline_threshold", 0) or 0) or self.offline_threshold
        if threshold <= 0:
            return
        quality = db.snapshot_quality(snap, now, int(self.poll.get("stalled_after_s", 1200)))
        if not (quality["online"] and quality["total"]):
            return  # 未知状态既不能触发掉线，也不能伪造恢复。
        total, online = snap["total"], snap["online"]
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

    # ---- 发送与失败补发 ----
    def _channels(self, include_paused=False):
        """可用配置；暂停通道仅供记录投递意向，实际发送仍要求 enabled。"""
        channels = {}
        tg = self.cfg.get("telegram") or {}
        if (include_paused or tg.get("enabled")) and tg.get("bot_token") and tg.get("chat_id"):
            channels["telegram"] = (tg, _send_telegram)
        wh = self.cfg.get("webhook") or {}
        if (include_paused or wh.get("enabled")) and wh.get("url"):
            channels["webhook"] = (wh, _send_webhook)
        em = self.cfg.get("email") or {}
        if (include_paused or em.get("enabled")) and em.get("smtp_host"):
            channels["email"] = (em, _send_email)
        return channels

    def _send(self, sid, ntype, msg, recovery=False):
        now = int(time.time())
        key = (sid, ntype)
        group = next((g for g in _NOTICE_GROUPS if ntype in g), (ntype,))
        for old in list(self.pending):
            if old[0] == sid and old[1] in group and old != key:
                self.pending.pop(old, None)  # 状态已反转，不补发相反的旧消息。
        if key in self.pending:
            return
        # 全部通道暂停期间也可能“恢复后又故障”；保存最新事件，否则 prev 已前移，
        # 重开通道后仍没有新的跳变可触发。没有配置过任何接收端时才无需排队。
        channels = self._channels() or self._channels(include_paused=True)
        if not channels:
            return
        cooldown = int(self.cfg.get("cooldown", 1800) or 0)
        due = now
        if not recovery and key in self.last_sent:
            due = max(due, self.last_sent[key] + cooldown)
        # 短窗合并：同一场地多事件先入队，等到 merge_window 再一次发出，避免刷屏。
        merge = int(self.cfg.get("merge_window_sec", 0) or 0)
        if not recovery and merge > 0:
            due = max(due, now + merge)
        self.pending[key] = {"msg": msg, "remaining": set(channels), "delivered": set(),
                             "attempts": 0, "due": due, "created": now}
        self._persist(sid)   # 事件/活动状态必须在任何外部请求之前落地，包括暂停和冷却中的事件。
        if recovery or merge <= 0:
            self._attempt_pending(key, now)

    def _due_keys_for_site(self, sid, now):
        return sorted(
            (k for k, n in self.pending.items() if k[0] == sid and now >= n["due"]),
            key=lambda k: (self.pending[k]["created"], k[1]),
        )

    def _attempt_pending(self, key, now):
        notice = self.pending.get(key)
        if notice is None or now < notice["due"]:
            return
        if not self.cfg.get("enabled", True):
            return
        if key[1] in _NOTICE_GROUPS[1] and not float(self.cfg.get("hashrate_drop_pct", 30) or 0):
            return
        if key[1] in _NOTICE_GROUPS[2] and not int(self.cfg.get("offline_threshold", 0) or 0):
            return
        # 同一场地到期事件合并成一条外发，避免短时多跳变刷屏。
        keys = self._due_keys_for_site(key[0], now)
        if not keys or key != keys[0]:
            return
        channels = self._channels()
        ready = set(channels)
        for k in keys:
            ready &= self.pending[k]["remaining"]
        if not ready:
            return  # 通道关闭只暂停，不丢失失败状态，也不占用成功冷却。
        if self._delivery_budget is not None and self._delivery_budget <= 0:
            return
        parts = []
        for k in keys:
            n = self.pending[k]
            part = n["msg"]
            if now > n["created"]:
                created = datetime.fromtimestamp(n["created"], timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC")
                part = f"⏳ 补发通知（事件时间 {created}）\n{part}"
            parts.append(part)
        msg = "\n".join(parts) if len(parts) > 1 else parts[0]
        self._persist(key[0])
        failed = False
        for channel in sorted(ready):
            if key not in self.pending:
                return
            epoch, deleted = db.notification_generation(self.conn, key[0])
            if deleted or epoch != self._epochs.get(key[0]):
                self._forget_site(key[0])
                return
            if self._delivery_budget is not None:
                if self._delivery_budget <= 0:
                    break
                self._delivery_budget -= 1
            cfg, sender = channels[channel]
            try:
                ok = bool(sender(cfg, msg))
            except Exception:
                ok = False  # 不打印可能带 URL/token 的异常；其它通道继续尝试。
            if ok:
                for k in list(keys):
                    n = self.pending.get(k)
                    if not n:
                        continue
                    n["remaining"].discard(channel)
                    n["delivered"].add(channel)
                    if not n["remaining"]:
                        self.last_sent[k] = now
                        self.pending.pop(k, None)
                self._persist(key[0])  # webhook/email 失败或进程随后退出，也不能重发已送达 Telegram。
            else:
                failed = True
            logged = f"[{channel}] {msg}"
            try:
                # 合并发送时按首条类型记日志；细节已在正文。
                db.notify_log_add(self.conn, now, key[0], key[1], logged, ok)
            except Exception:
                pass
            try:   # GBK 控制台打不出 emoji 也不能影响补发状态。
                print(f"[notify] {'OK' if ok else 'FAIL'} {logged}")
            except Exception:
                pass
        if failed:
            for k in keys:
                n = self.pending.get(k)
                if not n:
                    continue
                n["attempts"] = min(n["attempts"] + 1, 5)
                n["due"] = now + min(900, 60 * 2 ** (n["attempts"] - 1))
            self._persist(key[0])

    def _retry_pending(self, cur, last_ok, now):
        for key in sorted(self.pending, key=lambda k: (self.pending[k]["due"], self.pending[k]["created"], k)):
            sid, ntype = key
            if sid not in cur:
                self.pending.pop(key, None)
                continue
            state = cur[sid][0]
            if ntype in _NOTICE_GROUPS[0]:
                current = state if state in ("unreachable", "stalled", "site_down") else "recovered"
                if state == "pending" or ntype != current:
                    self.pending.pop(key, None)
                    continue
            else:
                is_drop = ntype in _NOTICE_GROUPS[1]
                setting = "hashrate_drop_pct" if is_drop else "offline_threshold"
                if float(self.cfg.get(setting, 30 if is_drop else 0) or 0) <= 0:
                    continue
                # 缺测/失联不是恢复：保留当前故障，但没有新证据时暂停补发测量类消息。
                if state not in _FRESH_STATES:
                    continue
                quality = db.snapshot_quality(last_ok.get(sid) or {}, now,
                                              int(self.poll.get("stalled_after_s", 1200)))
                if not (quality["hashrate_ths"] if is_drop else quality["online"] and quality["total"]):
                    continue
                active = sid in (self.active_drop if is_drop else self.active_offline)
                if active != (ntype in ("hashrate_drop", "offline_count")):
                    self.pending.pop(key, None)
                    continue
            self._attempt_pending(key, now)

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


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


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
                or ip.is_multicast or ip.is_reserved or ip in _CGNAT):
            # 100.64.0.0/10(运营商级 NAT)里有阿里云元数据 100.100.100.200，
            # Python 不把它算 private/reserved/link_local，得单独拦
            return f"{host} 解析到内部地址 {ip}"
    return None


def _send_telegram(tg, msg):
    token = tg.get("bot_token") or ""
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": tg["chat_id"], "text": msg}, timeout=10)
        if not 200 <= r.status_code < 300:
            return False
        result = r.json()
        return isinstance(result, dict) and result.get("ok") is True
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
        if not 200 <= r.status_code < 300:
            return False
        if kind not in ("wecom", "dingtalk"):
            return True   # 自定义接收端只约定 HTTP 2xx，不强加厂商 JSON 协议。
        result = r.json()
        # 企业微信/钉钉会用 HTTP 200 返回业务失败，HTTP 可达不能冒充消息已接收。
        code = result.get("errcode") if isinstance(result, dict) else None
        return isinstance(code, int) and not isinstance(code, bool) and code == 0
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
