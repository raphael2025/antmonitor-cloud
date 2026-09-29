#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页可改的通知设置(cloud_settings.json)：原子读写 + 启动时叠加进运行中的 CFG。

为什么要另开一份文件：config.yaml 改 alerts(Telegram/Webhook/邮件) 要登服务器改文件+
重启进程。管理员想在浏览器里改完立即生效、且重启后还在——所以另存一份 JSON 作为
"运行期覆盖层"：config.yaml 仍是出厂默认，cloud_settings.json 是网页保存的最新值，
每次进程启动时叠加在 config.yaml 之上。

镜像 E:\\ip\\appconfig.py 的 settings.json 模式（RLock 保护的原子 read-modify-write，
写文件走 .tmp+os.replace 防半截 JSON），但范围收窄到 alerts 这一块——云端没有
scan/schedule 那些参数。

另存 "passwords": {用户名: pbkdf2哈希}——网页/命令行改密码的结果，覆盖 config.yaml
里同名用户的 password(角色仍以 config.yaml 为准)。改密码不用再登服务器改 yaml + 重启。
文件含 bot_token/邮箱密码/口令哈希，权限一律 0600(仅属主可读)。
"""
import copy
import json
import os
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
# 与 server.py 的 CFG_PATH 用同一条环境变量/默认值逻辑，确保 settings 文件
# 落在 config.yaml 同一目录（测试用 CO_CONFIG=tools/test_config.yaml 时也一致）。
_CFG_PATH = os.environ.get("CO_CONFIG") or os.path.join(BASE, "config.yaml")
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(_CFG_PATH)), "cloud_settings.json")

# 可重入锁：保护 cloud_settings.json 的"读-改-写"整体过程。
# 两个管理员同时保存面板设置时，若各自先 load 再 save，后写的会把先写的改动
# 整段覆盖掉(lost update)且无人知晓。用 update_settings() 才能真正避免。
# 用 RLock 是因为 update_settings() 在持锁状态下会再调用 load_settings()。
_LOCK = threading.RLock()


def load_settings():
    with _LOCK:
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                print(f"[appconfig] cloud_settings.json 读取失败，忽略: {e}")
        return {}


def update_settings(patch):
    """原子地把 patch 合并进 cloud_settings.json(加锁→读→浅合并→写)，返回合并后的完整 dict。

    与 save 相比的关键差异：整个读-改-写在同一把锁内完成，多个管理员并发保存
    (先 load 再改再 save)不会互相覆盖。patch 的每个顶层 key(如 "alerts")整体替换，
    调用方需自己把完整的合并结果传进来(参见 server.py 的 POST /api/settings)。
    """
    with _LOCK:
        merged = {**load_settings(), **(patch or {})}
        _atomic_json(SETTINGS_FILE, merged)
        return merged


def _atomic_json(path, data):
    """先写 .tmp 再 os.replace：断电/崩溃也不会留下半截 JSON 让下次启动读不出配置。
    .tmp 以 0600 创建(文件里有通知密钥和口令哈希，同机其它用户不可读)。"""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)   # 已存在的旧 .tmp 不受 O_CREAT 的 mode 影响，补一刀
    except OSError:
        pass
    os.replace(tmp, path)


# ---- 口令覆盖层(网页/命令行改密码) ----
_pw_cache = {"mtime": None, "data": {}}


def password_overrides():
    """{用户名: 哈希}。按文件 mtime 缓存：命令行 `python auth.py passwd` 改完，
    运行中的服务下一次登录/鉴权就读到新值，无需重启。"""
    with _LOCK:
        try:
            mt = os.path.getmtime(SETTINGS_FILE)
        except OSError:
            mt = None
        if mt != _pw_cache["mtime"]:
            p = load_settings().get("passwords") if mt is not None else None
            _pw_cache["data"] = ({str(k): str(v) for k, v in p.items()}
                                 if isinstance(p, dict) else {})
            _pw_cache["mtime"] = mt
        return dict(_pw_cache["data"])


def set_password_override(username, pw_hash):
    with _LOCK:
        cur = load_settings().get("passwords")
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur[str(username)] = str(pw_hash)
        update_settings({"passwords": cur})
        _pw_cache["mtime"] = None   # 同一秒内连改两次 mtime 可能不变，强制下次重读


def apply_settings(cfg, s):
    """把 cloud_settings.json 里保存的 alerts 深合并进运行中的 cfg["alerts"]（原地修改）。

    必须原地改嵌套字典、不能整段替换 cfg["alerts"] = xxx —— collector.py 里
    Notifier 持有的是同一个 dict 对象引用，替换成新对象会导致它继续读旧值
    (server.py/collector.py 里对这条约束有详细说明)。
    """
    a = s.get("alerts") if isinstance(s, dict) else None
    if not isinstance(a, dict):
        return cfg
    dst = cfg.setdefault("alerts", {})
    _merge_inplace(dst, a)
    return cfg


def _merge_inplace(dst, src):
    """深合并 src 进 dst(原地)：dict 递归合并，其它类型直接覆盖。"""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_inplace(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
