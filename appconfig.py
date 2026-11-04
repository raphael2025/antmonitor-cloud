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


# 每个配置路径分别保存最后一次完整、可信的覆盖层。读错误绝不能把密码/撤销项变回 YAML。
# 仅进程内保留：新进程读到坏文件直接停止启动，让管理员修复原文件。
_trusted = {}
# 见过坏文件/断链/读取权限错误后，删除它也不能重新当作首次安装。
# 与可信快照分开：观察到文件存在不等于其内容可以作为凭据覆盖层。
_observed_existing = set()


def _validate_settings(data):
    if not isinstance(data, dict):
        raise SettingsError("配置根节点必须是对象")
    for section in ("passwords", "ingest", "alerts", "connect"):
        if section in data and not isinstance(data[section], dict):
            raise SettingsError("配置 %s 必须是对象" % section)
    passwords = data.get("passwords", {})
    if any(not isinstance(v, str) or not v for v in passwords.values()):
        raise SettingsError("passwords 中的口令覆盖必须是非空字符串")
    ingest = data.get("ingest", {})
    if "token" in ingest and not isinstance(ingest["token"], str):
        raise SettingsError("ingest.token 必须是字符串（空串表示停用）")
    for key in ("tokens", "site_meta"):
        if key in ingest and not isinstance(ingest[key], dict):
            raise SettingsError("ingest.%s 必须是对象" % key)
    if any(not isinstance(v, str) for v in ingest.get("tokens", {}).values()):
        raise SettingsError("场地 token 必须是字符串（空串表示撤销）")
    for key in ("telegram", "webhook", "email"):
        alerts = data.get("alerts", {})
        if key in alerts and not isinstance(alerts[key], dict):
            raise SettingsError("alerts.%s 必须是对象" % key)
    return data


def _read_settings():
    """严格读取；仅首次安装且文件从未出现时允许空覆盖。写路径也使用它。"""
    path = os.path.abspath(SETTINGS_FILE)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            _observed_existing.add(path)
            data = _validate_settings(json.load(f))
    except FileNotFoundError as e:
        if os.path.lexists(SETTINGS_FILE):  # 断链符号链接仍是已有的配置，不能回落到 YAML。
            _observed_existing.add(path)
        if path not in _observed_existing and not _trusted.get(path, {}).get("existed"):
            _trusted[path] = {"data": {}, "existed": False}
            return {}
        raise SettingsError("%s 已有配置丢失；请恢复后重试" % SETTINGS_FILE) from e
    except (OSError, ValueError, SettingsError) as e:
        _observed_existing.add(path)
        raise SettingsError("%s 读取失败(%s)；请修复文件或权限后重试" % (SETTINGS_FILE, e)) from e
    _trusted[path] = {"data": copy.deepcopy(data), "existed": True}
    return data


def load_settings():
    with _LOCK:
        try:
            return _read_settings()
        except SettingsError:
            trusted = _trusted.get(os.path.abspath(SETTINGS_FILE))
            if not trusted or not trusted["existed"]:
                raise  # 缺失文件的空值不是可信覆盖层；首次读取坏文件仍须失败关闭。
            return copy.deepcopy(trusted["data"])


def update_settings(patch):
    """在同一锁中严格读取、合并并原子保存；失败不会污染最后可信覆盖层。"""
    with _LOCK:
        merged = _validate_settings({**_read_settings(), **(patch or {})})
        _atomic_json(SETTINGS_FILE, merged)
        _observed_existing.add(os.path.abspath(SETTINGS_FILE))
        _trusted[os.path.abspath(SETTINGS_FILE)] = {"data": copy.deepcopy(merged), "existed": True}
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
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---- 口令覆盖层(网页/命令行改密码) ----
def password_overrides():
    """使用与启动/保存相同的可信读路径；命令行改密码下一次鉴权即生效。"""
    with _LOCK:
        return dict(load_settings().get("passwords", {}))


def set_password_override(username, pw_hash):
    with _LOCK:
        cur = dict(_read_settings().get("passwords", {}))
        cur[str(username)] = str(pw_hash)
        update_settings({"passwords": cur})


def apply_settings(cfg, s):
    """把 cloud_settings.json 里保存的 alerts / ingest / connect 叠加进运行中的 cfg（原地修改）。

    必须原地改嵌套字典、不能整段替换 cfg["alerts"] = xxx —— collector.py 里
    Notifier 持有的是同一个 dict 对象引用，替换成新对象会导致它继续读旧值
    (server.py/collector.py 里对这条约束有详细说明)。

    ingest(网页「场地接入」管理的上报 token)：
      token 键存在就覆盖 config.yaml 的共享 token(空串=停用共享 token)；
      tokens 按场地合并(网页生成的专属 token 覆盖同名的 config.yaml 项)；
      site_meta 按场地合并，保存待接入场地的名称/类型；
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
        if isinstance(ing.get("site_meta"), dict):
            if not isinstance(dst.get("site_meta"), dict):
                dst["site_meta"] = {}
            dst["site_meta"].update(copy.deepcopy(ing["site_meta"]))
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
