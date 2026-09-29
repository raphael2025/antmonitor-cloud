#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端总览登录（与本地监控 auth.py 同款 pbkdf2 + Cookie 会话）。

角色：viewer(只读) / admin(通知设置、版本更新、删场地、安全中心)。
会话令牌存内存，重启后失效（需重新登录）。

安全约定：
- 出厂默认口令(admin888/viewer888)**禁止登录**——公网上默认口令等于没有口令。
  必须先改：网页不行(登不进去)，在服务器上 `python auth.py passwd admin` 即可，立即生效。
  (仅本地测试可在 config.yaml 设 auth.allow_default_password: true 放行。)
- 改密码后该用户的其它会话全部作废(会话里记着登录时的口令哈希，对不上即失效)。
- 用户名不存在时也跑一次 pbkdf2，登录耗时一致，不能靠计时探测哪些用户名存在。

命令行：
  python auth.py passwd <用户名>   交互输入新密码(不进 shell 历史)，写入 cloud_settings.json，立即生效
  python auth.py hash              交互输入密码，打印哈希(粘到 config.yaml 的 password)
  python auth.py token             生成一个随机 token(ingest.token / public_api.token 用)
  python auth.py <明文密码>         旧用法：直接打印哈希
"""
import getpass
import hashlib
import hmac
import secrets
import sys
import threading
import time

import appconfig

ROLE_RANK = {"viewer": 1, "admin": 3}
COOKIE = "co_token"
TTL = 7 * 86400  # 令牌有效期
_PBKDF2_ITER = 200_000
MIN_PASSWORD_LEN = 10

_sessions = {}  # token -> {user, role, exp, pw}
_slock = threading.Lock()

_fails = {}            # 限流键(来源IP) -> [失败次数, 锁定到期时间戳]
_MAX_FAILS = 8         # 连续失败上限
_LOCK_SEC = 300        # 触顶后锁定秒数(5分钟)，挡在线暴破

_DEFAULT_PW = {"admin": "admin888", "viewer": "viewer888"}
_WEAK = {"admin888", "viewer888", "admin", "123456", "12345678", "password", "admin123"}


def hash_password(password, salt=None):
    """生成 pbkdf2$iter$salt$hex 格式的口令哈希，供 config.yaml 存储(替代明文)。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(password).encode(), bytes.fromhex(salt), _PBKDF2_ITER)
    return f"pbkdf2${_PBKDF2_ITER}${salt}${dk.hex()}"


_DUMMY_HASH = hash_password(secrets.token_hex(8))   # 用户名不存在时陪跑，抹平计时差


def _verify_password(stored, password):
    """stored 是 pbkdf2$... 则按哈希校验，否则按明文比较(向后兼容，建议改用哈希)。"""
    stored = str(stored or "")
    if stored.startswith("pbkdf2$"):
        try:
            _, iters, salt, hexd = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", str(password).encode(),
                                     bytes.fromhex(salt), int(iters))
            return hmac.compare_digest(dk.hex(), hexd)
        except Exception:
            return False
    if not stored:
        return False   # 空口令一律不放行
    return hmac.compare_digest(stored.encode(), str(password).encode())   # 明文回退


def _users(cfg):
    """config.yaml 的用户 + cloud_settings.json 里改过的口令(覆盖同名用户的 password)。"""
    over = appconfig.password_overrides()
    out = {}
    for u in (cfg.get("auth", {}).get("users") or []):
        if not isinstance(u, dict) or not u.get("username"):
            continue
        d = dict(u)
        name = str(d["username"])
        if name in over:
            d["password"] = over[name]
        if d.get("role") not in ROLE_RANK:
            d["role"] = "viewer"
        out[name] = d
    return out


def _is_default(name, stored):
    return name in _DEFAULT_PW and _verify_password(stored, _DEFAULT_PW[name])


def weak_default_users(cfg):
    """返回仍在使用出厂默认弱口令的用户名(供启动自检/安全中心告警)。"""
    return [name for name, u in _users(cfg).items() if _is_default(name, u.get("password", ""))]


def plaintext_users(cfg):
    """口令仍以明文写在 config.yaml 里的用户(应换成 pbkdf2 哈希)。"""
    return [name for name, u in _users(cfg).items()
            if not str(u.get("password") or "").startswith("pbkdf2$")]


def enabled(cfg):
    return cfg.get("auth", {}).get("enabled", False)


def _allow_default(cfg):
    return bool(cfg.get("auth", {}).get("allow_default_password", False))


def _record_fail(src, now):
    if not src:
        return
    with _slock:
        rec = _fails.get(src, [0, 0])
        rec[0] += 1
        if rec[0] >= _MAX_FAILS:
            rec[1] = now + _LOCK_SEC
            rec[0] = 0
        _fails[src] = rec


def login(cfg, username, password, src=None):
    """src: 客户端 IP，用于失败限流(按来源 IP 计)。
    返回 (token, None) 或 (None, 原因)；原因: "locked" | "bad" | "default_password"。"""
    now = time.time()
    if src:   # 锁定期内直接拒绝，不再校验口令
        with _slock:
            rec = _fails.get(src)
            if rec and rec[1] > now:
                return None, "locked"
    u = _users(cfg).get(username)
    stored = u.get("password", "") if u else _DUMMY_HASH
    ok = _verify_password(stored, password)
    if not u or not ok:
        _record_fail(src, now)
        return None, "bad"
    if _is_default(username, stored) and not _allow_default(cfg):
        return None, "default_password"
    token = secrets.token_hex(24)
    with _slock:
        _fails.pop(src, None)
        for k in [k for k, v in _fails.items() if v[1] and v[1] < now]:
            _fails.pop(k, None)
        for t in [t for t, s in _sessions.items() if s["exp"] < now]:
            _sessions.pop(t, None)
        _sessions[token] = {"user": username, "role": u["role"], "exp": now + TTL,
                            "pw": str(stored)}
    return token, None


def locked(src):
    """该来源是否在锁定期内(供登录前快速拒绝返回 429)。"""
    if not src:
        return False
    with _slock:
        rec = _fails.get(src)
        return bool(rec and rec[1] > time.time())


def logout(token):
    with _slock:
        _sessions.pop(token, None)


def session(token):
    with _slock:
        s = _sessions.get(token)
        if not s:
            return None
        if s["exp"] < time.time():
            _sessions.pop(token, None)
            return None
        return s


def current(cfg, token):
    """返回 {user, role}；auth 关闭时视为匿名只读 viewer。
    口令已被改掉 / 用户已从配置删除 / 角色被改 → 旧会话立即作废。"""
    if not enabled(cfg):
        return {"user": "anonymous", "role": "viewer"}
    s = session(token)
    if not s:
        return None
    u = _users(cfg).get(s["user"])
    if not u or str(u.get("password", "")) != s.get("pw") or u["role"] != s["role"]:
        logout(token)
        return None
    return s


def password_problem(new, username=""):
    """新口令不合格的原因(中文)，合格返回 None。"""
    new = str(new or "")
    if len(new) < MIN_PASSWORD_LEN:
        return f"新密码至少 {MIN_PASSWORD_LEN} 位"
    if new.lower() in _WEAK or new in _DEFAULT_PW.values():
        return "新密码太常见，请换一个"
    if username and new.lower() == str(username).lower():
        return "新密码不能与用户名相同"
    if len(set(new)) < 4:
        return "新密码字符太单一"
    return None


def change_password(cfg, username, old, new, keep_token=None):
    """校验旧口令 → 写入口令覆盖层 → 作废该用户其它会话。返回 None 或错误原因。"""
    u = _users(cfg).get(username)
    if not u or not _verify_password(u.get("password", ""), old):
        return "原密码错误"
    if hmac.compare_digest(str(old).encode(), str(new).encode()):
        return "新密码不能与原密码相同"
    err = password_problem(new, username)
    if err:
        return err
    h = hash_password(new)
    appconfig.set_password_override(username, h)
    with _slock:
        for t in [t for t, s in _sessions.items() if s["user"] == username]:
            if t == keep_token:
                _sessions[t]["pw"] = h
            else:
                _sessions.pop(t, None)
    return None


def revoke_all(keep_token=None):
    """强制所有会话下线(保留当前操作者自己的)。返回踢掉的数量。"""
    with _slock:
        victims = [t for t in _sessions if t != keep_token]
        for t in victims:
            _sessions.pop(t, None)
    return len(victims)


def session_count():
    now = time.time()
    with _slock:
        return sum(1 for s in _sessions.values() if s["exp"] >= now)


def _ask_new_password(username=""):
    while True:
        p1 = getpass.getpass("新密码: ")
        err = password_problem(p1, username)
        if err:
            print("✗", err)
            continue
        if getpass.getpass("再输一次: ") != p1:
            print("✗ 两次输入不一致")
            continue
        return p1


def _cli(argv):
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "passwd":
        if len(argv) < 3:
            print("用法: python auth.py passwd <用户名>")
            return 1
        name = argv[2]
        try:
            import yaml
            with open(appconfig._CFG_PATH, "rb") as f:
                cfg = yaml.safe_load(f.read().decode("utf-8-sig")) or {}
        except (OSError, ValueError) as e:
            print(f"✗ 读取 {appconfig._CFG_PATH} 失败: {e}")
            return 1
        if name not in _users(cfg):
            print(f"✗ config.yaml 的 auth.users 里没有用户 {name}(新增用户请直接编辑 config.yaml)")
            return 1
        h = hash_password(_ask_new_password(name))
        appconfig.set_password_override(name, h)
        print(f"✓ 已更新用户 {name} 的密码(写入 {appconfig.SETTINGS_FILE})，"
              f"运行中的服务立即生效，该用户旧会话全部失效。")
        return 0
    if cmd == "hash":
        print(hash_password(_ask_new_password()))
        return 0
    if cmd == "token":
        print(secrets.token_hex(32))
        return 0
    if cmd and not cmd.startswith("-"):
        print(hash_password(cmd))   # 旧用法：python auth.py <明文密码>
        return 0
    print(__doc__.split("命令行：", 1)[1].rstrip())
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
