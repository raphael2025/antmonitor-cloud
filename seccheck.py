#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全自检：把"上线前必须改的东西"变成一张清单。

三处使用：启动时打印；管理员网页「🔒 安全中心」顶部显示；命令行 `python seccheck.py`。
每条 {level: high|warn|info, msg}。high = 公网上能被直接利用，必须处理。
"""
import os
import stat

import auth

TOKEN_MIN_LEN = 24


def is_placeholder(tok):
    """模板占位 token(CHANGE_ME...)视同未配置——照抄模板上线等于把钥匙挂门上。"""
    t = str(tok or "").strip()
    return not t or t.upper().startswith("CHANGE_ME") or t.lower() in ("changeme", "token", "test")


def _token_checks(cfg):
    out = []
    icfg = cfg.get("ingest") or {}
    shared = icfg.get("token")
    per = icfg.get("tokens") or {}
    if shared and is_placeholder(shared):
        out.append(("high", "ingest.token 仍是模板占位值(CHANGE_ME…)，已自动视为未配置——"
                             "共享钥匙上报被拒绝。用 `python auth.py token` 生成新值"))
    elif shared and len(str(shared)) < TOKEN_MIN_LEN:
        out.append(("warn", f"ingest.token 太短(<{TOKEN_MIN_LEN}位)，建议用 `python auth.py token` 生成"))
    bad = [k for k, v in per.items() if is_placeholder(v) or len(str(v)) < TOKEN_MIN_LEN]
    if bad:
        out.append(("warn", f"ingest.tokens 里这些场地的专属钥匙太短或是占位值: {', '.join(map(str, bad))}"))
    if shared and not is_placeholder(shared) and not per:
        out.append(("info", "所有场地共用一把 ingest.token：任一场地机器被攻破，对方就能冒充所有场地上报。"
                            "建议逐步给每个场地发专属钥匙(ingest.tokens)"))
    pub = (cfg.get("public_api") or {}).get("token")
    if pub and is_placeholder(pub):
        out.append(("high", "public_api.token 仍是模板占位值，已自动视为未配置(Agent 取数接口关闭)"))
    elif pub and len(str(pub)) < TOKEN_MIN_LEN:
        out.append(("warn", f"public_api.token 太短(<{TOKEN_MIN_LEN}位)"))
    if pub and shared and str(pub) == str(shared):
        out.append(("high", "public_api.token 与 ingest.token 相同——读钥匙泄露等于写钥匙泄露，必须分开"))
    return out


def _file_mode_checks(paths):
    out = []
    if os.name != "posix":
        return out
    for p in paths:
        try:
            m = os.stat(p).st_mode
        except OSError:
            continue
        if m & (stat.S_IRGRP | stat.S_IROTH):
            out.append(("warn", f"{os.path.basename(p)} 同机其它用户可读(含密钥)，建议 chmod 600 {p}"))
    return out


def run(cfg, cfg_path=None, settings_path=None):
    out = []
    a = cfg.get("auth") or {}
    if not a.get("enabled", False):
        out.append(("high", "登录已关闭(auth.enabled=false)：任何能访问本服务的人都能看全部数据"))
    weak = auth.weak_default_users(cfg)
    if weak:
        if a.get("allow_default_password"):
            out.append(("high", f"用户 {', '.join(weak)} 仍是出厂默认口令，且 allow_default_password=true "
                                f"放行了登录——仅限本地测试！"))
        else:
            out.append(("high", f"用户 {', '.join(weak)} 仍是出厂默认口令(已禁止登录)。"
                                f"在服务器上运行 `python auth.py passwd {weak[0]}` 设新密码"))
    plain = [u for u in auth.plaintext_users(cfg) if u not in weak]
    if plain:
        out.append(("warn", f"用户 {', '.join(plain)} 的口令以明文写在 config.yaml，"
                            f"建议 `python auth.py passwd 用户名` 改成哈希"))
    out += _token_checks(cfg)
    srv = cfg.get("server") or {}
    if srv.get("trust_proxy") and str(srv.get("host", "0.0.0.0")) not in ("127.0.0.1", "localhost", "::1"):
        out.append(("warn", "已配 trust_proxy(前面有 nginx)，但服务仍监听 0.0.0.0——8900 端口直连可绕过 nginx 的 "
                            "TLS。建议 server.host 改 127.0.0.1，或防火墙拦掉 8900"
                            "(Docker 部署用 -p 127.0.0.1:8900:8900 映射即可忽略本条)"))
    if (cfg.get("update") or {}).get("auto"):
        out.append(("info", "已开自动更新：GitHub 仓库里的代码会自动部署到本服务器。"
                            "务必给 GitHub 账号开两步验证，生产只跟踪受保护的 release 分支"))
    out += _file_mode_checks([p for p in (cfg_path, settings_path) if p])
    rank = {"high": 0, "warn": 1, "info": 2}
    out.sort(key=lambda x: rank[x[0]])
    return [{"level": lv, "msg": m} for lv, m in out]


if __name__ == "__main__":
    import sys
    import yaml
    import appconfig
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    with open(appconfig._CFG_PATH, "rb") as f:
        _cfg = yaml.safe_load(f.read().decode("utf-8-sig")) or {}
    items = run(_cfg, appconfig._CFG_PATH, appconfig.SETTINGS_FILE)
    icon = {"high": "✖ 高危", "warn": "⚠ 注意", "info": "ℹ 建议"}
    for it in items:
        print(f"{icon[it['level']]}  {it['msg']}")
    if not items:
        print("✓ 未发现问题")
    sys.exit(1 if any(i["level"] == "high" for i in items) else 0)
