#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场地接入信息：云端地址、证书指纹、接入码。

场地端要填三样东西才能上报：云端地址、上报 token、(没有域名时)证书指纹。以前要登服务器
`cat config.yaml` / `python auth.py token` 才拿得到；现在管理员在网页「场地接入」里看，
并可一键复制"接入码"——把这三样(加上可选的预分配场地 ID)打包成一行，场地端面板粘贴即可。

接入码格式：AMC1-<base64url(JSON {"u": 地址, "t": token, "f": 证书指纹?, "s": 场地ID?})>
它和 token 一样敏感：只发给场地负责人，别贴群里。
"""
import base64
import hashlib
import ipaddress
import json
import os
import ssl
from urllib.parse import urlsplit

PREFIX = "AMC1-"

_fp_cache = {"key": None, "fp": ""}


def cert_fingerprint(path):
    """PEM 证书(取第一张)的 SHA-256 指纹，AA:BB:… 大写格式；读不到返回空串。按 mtime 缓存。"""
    if not path:
        return ""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return ""
    if _fp_cache["key"] == key:
        return _fp_cache["fp"]
    try:
        with open(path, encoding="ascii", errors="ignore") as f:
            pem = f.read()
        end = pem.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")
        der = ssl.PEM_cert_to_DER_cert(pem[pem.index("-----BEGIN CERTIFICATE-----"):end])
    except (OSError, ValueError):
        return ""
    h = hashlib.sha256(der).hexdigest().upper()
    fp = ":".join(h[i:i + 2] for i in range(0, len(h), 2))
    _fp_cache.update(key=key, fp=fp)
    return fp


def host_is_ip(url):
    try:
        ipaddress.ip_address((urlsplit(url).hostname or "").strip("[]"))
        return True
    except ValueError:
        return False


def normalize_url(url):
    """校验管理员填的云端地址：必须 http(s)://主机[:端口]，去掉末尾斜杠。非法返回 None。"""
    u = str(url or "").strip().rstrip("/")
    if not u:
        return ""
    try:
        sp = urlsplit(u)
        port = sp.port   # 端口不是数字会抛 ValueError
    except ValueError:
        return None
    if sp.scheme not in ("http", "https") or not sp.hostname or sp.query or sp.fragment:
        return None
    if sp.username or sp.password:
        return None
    del port
    return u


def server_info(cfg):
    """当前部署模式：直连 TLS 的证书、是否自签(需要场地端认指纹)。"""
    srv = cfg.get("server") or {}
    cert = str(srv.get("tls_cert") or "")
    self_signed = bool(cert and srv.get("tls_self_signed"))
    return {"tls_direct": bool(cert and srv.get("tls_key")),
            "self_signed": self_signed,
            "fingerprint": cert_fingerprint(cert) if self_signed else ""}


def mode_of(url, info):
    if not url:
        return "unset"
    if url.startswith("http://"):
        return "http"
    if info["self_signed"]:
        return "ip" if host_is_ip(url) else "self_signed"
    return "ip" if host_is_ip(url) else "domain"


def make_code(url, token, fingerprint="", site_id=""):
    d = {"u": url, "t": token}
    if fingerprint:
        d["f"] = fingerprint
    if site_id:
        d["s"] = site_id
    raw = json.dumps(d, separators=(",", ":"), ensure_ascii=True).encode()
    return PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_code(code):
    """测试/排障用：解开接入码。非法返回 None。"""
    code = str(code or "").strip()
    if not code.startswith(PREFIX):
        return None
    b = code[len(PREFIX):]
    try:
        d = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and d.get("u") and d.get("t") else None
