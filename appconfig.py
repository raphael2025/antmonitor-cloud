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
# CO_SETTINGS 可把它放到别处：Docker 部署 config.yaml 只读挂载，这个文件得放进数据卷，
# 否则容器一重建，网页改的密码 / 生成的场地 token 全丢
SETTINGS_FILE = (os.environ.get("CO_SETTINGS")
                 or os.path.join(os.path.dirname(os.path.abspath(_CFG_PATH)), "cloud_settings.json"))

# 可重入锁：保护 cloud_settings.json 的"读-改-写"整体过程。
# 两个管理员同时保存面板设置时，若各自先 load 再 save，后写的会把先写的改动
# 整段覆盖掉(lost update)且无人知晓。用 update_settings() 才能真正避免。
# 用 RLock 是因为 update_settings() 在持锁状态下会再调用 load_settings()。
_LOCK = threading.RLock()


class SettingsError(RuntimeError):
    """cloud_settings.json 存在但读不了/解析不了：绝不能当成空文件再整份覆盖写回去。"""


def _read_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise SettingsError(f"{SETTINGS_FILE} 读取失败({e})。常见原因：用 sudo 跑过 auth.py，"
                            f"文件变成 root 所有——chown 回服务账号后重试") from e
    return d if isinstance(d, dict) else {}


def load_settings():
    with _LOCK:
        try:
            return _read_settings()
        except SettingsError as e:
            print(f"[appconfig] ⚠ {e}")   # 读路径降级为空(服务照常起)；写路径见 update_settings
            return {}


def update_settings(patch):
    """原子地把 patch 合并进 cloud_settings.json(加锁→读→浅合并→写)，返回合并后的完整 dict。

    与 save 相比的关键差异：整个读-改-写在同一把锁内完成，多个管理员并发保存
    (先 load 再改再 save)不会互相覆盖。patch 的每个顶层 key(如 "alerts")整体替换，
    调用方需自己把完整的合并结果传进来(参见 server.py 的 POST /api/settings)。
    """
    with _LOCK:
        merged = {**_read_settings(), **(patch or {})}   # 读失败直接抛错，不覆盖原文件
        _atomic_json(SETTINGS_FILE, merged)
        return merged


def _atomic_json(path, data):
    """先写临时文件再 os.replace：断电/崩溃也不会留下半截 JSON 让下次启动读不出配置。
    临时文件用 mkstemp 新建(随机名 + O_EXCL，0600)：固定名的 .tmp 可能被服务账号预先换成
    符号链接，root 跑 `sudo python auth.py passwd` 时就会顺着它截断改写系统文件。
    os.replace 是 rename，目标是符号链接也只替换链接本身。"""
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp",
                               dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
        cur = _read_settings().get("passwords")
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur[str(username)] = str(pw_hash)
        update_settings({"passwords": cur})
        _pw_cache["mtime"] = None   # 同一秒内连改两次 mtime 可能不变，强制下次重读


def apply_settings(cfg, s):
    """把 cloud_settings.json 里保存的 alerts / ingest / connect 叠加进运行中的 cfg（原地修改）。

    必须原地改嵌套字典、不能整段替换 cfg["alerts"] = xxx —— collector.py 里
    Notifier 持有的是同一个 dict 对象引用，替换成新对象会导致它继续读旧值
    (server.py/collector.py 里对这条约束有详细说明)。

    ingest(网页「场地接入」管理的上报 token)：
      token 键存在就覆盖 config.yaml 的共享 token(空串=停用共享 token)；
      tokens 按场地合并(网页生成的专属 token 覆盖同名的 config.yaml 项)；
      auto_register 覆盖同名开关。
    connect.public_url：各场地填的云端地址(覆盖 server.public_url)。
    """
    if not isinstance(s, dict):
        return cfg
    a = s.get("alerts")
    if isinstance(a, dict):
        _merge_inplace(cfg.setdefault("alerts", {}), a)
    ing = s.get("ingest")
    if isinstance(ing, dict):
        dst = cfg.get("ingest")
        if not isinstance(dst, dict):
            dst = cfg["ingest"] = {}
        if "token" in ing:
            dst["token"] = str(ing.get("token") or "")
        if not isinstance(dst.get("tokens"), dict):
            dst["tokens"] = {}
        if isinstance(ing.get("tokens"), dict):
            dst["tokens"].update({str(k): str(v) for k, v in ing["tokens"].items()})
        if "auto_register" in ing:
            dst["auto_register"] = bool(ing["auto_register"])
    con = s.get("connect")
    if isinstance(con, dict) and "public_url" in con:
        cfg.setdefault("server", {})["public_url"] = str(con.get("public_url") or "")
    return cfg


def _merge_inplace(dst, src):
    """深合并 src 进 dst(原地)：dict 递归合并，其它类型直接覆盖。"""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_inplace(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
