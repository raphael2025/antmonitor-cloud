#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP 服务器：把矿场云端总览的 Agent 公共 API（`GET /api/public/*`，只读）包装成
MCP 工具，让任何支持 MCP 的 agent（Claude Code / Claude Desktop / 其它运行时）
无需手写 HTTP 调用即可查询矿场状态。

设计原则（与 E:\\main 主项目一致，见 docs/DEVELOPMENT.md §1）：
  - **只读、无反向通道**：本文件只包装 `GET /api/health` 和 `GET /api/public/*`，
    绝不包装 `/api/ingest/*`、`DELETE /api/site/*`、登录/会话等写接口。
    任何"下发/控制"类想法都不应该加到这里。
  - stdio 传输：由 MCP 客户端（Claude Code 等）作为子进程启动，走标准输入输出
    传递 JSON-RPC 消息 —— 因此**本文件绝不能向 stdout 打印任何东西**（会破坏协议
    流），所有诊断信息一律走工具返回值里的 error 字段。

配置解析优先级：
  1. 环境变量 CO_URL（+ 可选 CO_PUBLIC_TOKEN）
  2. 环境变量 CO_MCP_CONFIG 指向的 JSON 文件，形如 {"url": "...", "token": "..."}
     （与 ~/.claude/farm-report.config.json 同款形状，但本文件不硬编码任何具体路径，
     必须显式通过 CO_MCP_CONFIG 指定，保持与任何单一用户的文件布局解耦）
  两者都没配置 → 工具调用返回清晰的可操作报错，而不是让进程崩溃。

用法（用官方 mcp SDK 的 FastMCP，stdio transport）：
  python server.py
见 mcp_server/README.md 里 .mcp.json 的注册示例。
"""
import json
import os
from urllib.parse import quote

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("farm-cloud-overview")

_HTTP_TIMEOUT = 10  # 秒；云端接口本身有 5-30s 服务端缓存，这里不做重试/轮询


# ---------- 配置解析 ----------
def _load_config():
    """返回 (cfg_dict, error_str)。cfg_dict 形如 {"url":..., "token":...}（token 可能为空）。"""
    url = os.environ.get("CO_URL") or ""
    token = os.environ.get("CO_PUBLIC_TOKEN") or ""

    if not url or not token:
        cfg_path = os.environ.get("CO_MCP_CONFIG")
        if cfg_path:
            try:
                with open(cfg_path, "rb") as f:
                    data = json.loads(f.read().decode("utf-8-sig"))
            except FileNotFoundError:
                return None, f"CO_MCP_CONFIG 指向的文件不存在: {cfg_path}"
            except (OSError, ValueError) as e:
                return None, f"读取/解析 CO_MCP_CONFIG={cfg_path} 失败: {e}"
            url = url or str(data.get("url") or "")
            token = token or str(data.get("token") or "")

    if not url:
        return None, (
            "未配置云端总览地址。请设置环境变量 CO_URL（如 http://127.0.0.1:8900），"
            "可选再设 CO_PUBLIC_TOKEN；或设置环境变量 CO_MCP_CONFIG 指向一个 JSON 文件，"
            '形如 {"url": "http://your-cloud:8900", "token": "<public_api.token>"}。'
        )
    return {"url": url.rstrip("/"), "token": token}, None


# ---------- HTTP 请求封装（所有错误都转成结构化返回值，绝不抛未捕获异常） ----------
def _request(path, params=None, need_token=True):
    cfg, err = _load_config()
    if err:
        return {"ok": False, "error": err}

    url = cfg["url"]
    token = cfg["token"]
    if need_token and not token:
        return {"ok": False, "error": (
            "未配置 public_api token。请设置环境变量 CO_PUBLIC_TOKEN，或在 CO_MCP_CONFIG "
            "指向的 JSON 文件里提供 token 字段（值 = 云端 config.yaml > public_api.token）。"
        )}

    headers = {"Authorization": f"Bearer {token}"} if need_token else {}
    full_url = url + path

    try:
        resp = requests.get(full_url, params=params, headers=headers, timeout=_HTTP_TIMEOUT)
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"请求超时（{_HTTP_TIMEOUT}s）：{full_url}"}
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "error": (
            f"连不上云端总览 {url}（服务未启动/地址错误/网络不通）：{e}"
        )}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"请求异常：{e}"}

    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError:
            return {"ok": False, "error": f"响应不是合法 JSON（前 200 字符）：{resp.text[:200]}"}

    if resp.status_code == 401:
        return {"ok": False, "http_status": 401, "error": (
            "401 未授权：token 不对。请核对 CO_PUBLIC_TOKEN / 配置文件里的 token 是否等于"
            "云端 config.yaml > public_api.token。"
        )}
    if resp.status_code == 404:
        return {"ok": False, "http_status": 404, "error": (
            "404：该云端实例未配置 public_api.token（fail-closed，未配置时整组 "
            "/api/public/* 端点视为不存在），或场地/路径不存在。"
        )}
    if resp.status_code == 429:
        return {"ok": False, "http_status": 429, "error": (
            "429：请求过于频繁（每来源 IP 每分钟 120 次上限）。响应本身有 5-30s 服务端"
            "缓存，无需高频轮询，请降低调用频率后重试。"
        )}
    return {"ok": False, "http_status": resp.status_code, "error": (
        f"HTTP {resp.status_code}：{resp.text[:300]}"
    )}


# ---------- MCP 工具 ----------
@mcp.tool()
def farm_health() -> dict:
    """探活矿场云端总览服务本身（免 token，无业务数据）。

    应该作为查询矿场状态前的第一步：如果这个接口都连不上，或者返回的
    last_poll_age_s 远大于云端采集周期（通常 60s，具体看部署），说明云端总览
    服务自己挂了/卡死了——这本身就是需要最优先汇报的事故，不要拿旧数据
    冒充"一切正常"。

    返回字段：
      ok: bool —— 云端进程是否存活并能响应
      ts: int —— 服务器当前 Unix 秒时间戳
      last_poll_age_s: int|None —— 后台采集循环上次跑完距现在的秒数；
        远超正常轮询周期 = 采集/清理循环卡死的信号（纯推送部署下该循环也在跑
        数据清理，此值同样有效）
    """
    return _request("/api/health", need_token=False)


@mcp.tool()
def farm_overview() -> dict:
    """全网总览：所有场地的汇总 KPI + 逐场地状态/算力/在线/功耗/告警。这是
    agent 做"矿场情况汇报"时最常用的接口（服务端缓存 5 秒，无需更高频调用）。

    返回结构（节选）：
      totals: 全网汇总 —— sites(场地数)/sites_ok/sites_unreachable、
        machines_total/machines_online、hashrate_ths(全网算力,单位 TH/s)、
        power_kw(全网功耗,单位 kW)、active_alerts(全网活跃告警数)、
        containers/containers_faulty/containers_offline(集装箱总数/故障/离线，
        水冷场地才有意义)、customers(客户数)
      sites: 逐场地列表，每项含：
        id/name —— 场地稳定 id 与显示名（改名不变 id）
        type —— air(风冷) | hydro(水冷)
        state —— 场地状态机，取值：
          ok        正常
          alert     有活跃告警（active_alerts > 0，口径与场地本地一致；
                    被本地忽略的集装箱故障位只展示不计入告警数）
          stalled   场地本地扫描进程停滞（scan_age_s 超阈值）但仍有网络连通
          unreachable 场地断网/断电/上报中断（超过推送周期×失联倍数未收到
                    数据，或拉取模式连续失败）——此时展示的是**最后已知数据**，
                    务必结合 data_age_s 说明数据有多旧
          pending   新场地，还没收到过第一条数据
        online/total —— 在线/总矿机台数
        hashrate_ths —— 算力，单位 TH/s（显示时按千进制换档：
          ≥1000 TH/s 显示 PH/s，≥1000 PH/s 显示 EH/s）
        power_kw —— 实时功耗，单位 kW（≥1000 kW 时可换算 MW 展示）
        active_alerts —— 该场地当前活跃告警数
        containers/containers_faulty/containers_offline —— 集装箱总数/故障/离线
          （水冷场地才有意义，风冷通常为 0）
        data_age_s —— 该场地数据距现在多少秒（越大说明数据越旧，unreachable/
          stalled 状态下尤其要看这个字段）
        customers —— 该场地客户数
    """
    return _request("/api/public/overview")


@mcp.tool()
def farm_site_detail(site: str, hours: int = 24) -> dict:
    """单个场地的详情：最新状态 + 算力趋势 + 客户报表。用于用户问某个具体场地
    的细节/趋势时（服务端缓存 10 秒）。

    参数：
      site —— 场地的 site_id 或显示名（**可以包含中文**，本工具会自动做好
        URL 编码，调用方直接传中文名即可，无需自己转义）
      hours —— 趋势回看窗口，小时数，默认 24（云端会自动钳制到合理范围）

    返回结构：
      site —— 同 farm_overview() 里 sites[] 的单项结构（state/hashrate_ths/
        power_kw/active_alerts/data_age_s 等字段含义相同）
      trend —— [[ts, hashrate_ths], ...] 时间序列点（按 hours 窗口分桶均值），
        相邻点若时间间隔明显偏大代表期间断报/停电，不应线性插值理解
      customers —— 该场地客户明细（worker/machines/uptime_pct/delivered_th_h/
        power_kwh/hours）
      alert_summary —— 活跃告警摘要，按类型分组（category/label/count/since_ts/
        workers），不含单机IP，只标"哪类故障、多少台、受影响客户、已持续多久"；
        场地本地未推送该字段时为 []

      找不到该场地（site_id/名字都不匹配）会返回 {"ok": false, "http_status":
      404, "error": ...}。
    """
    path = f"/api/public/site/{quote(site, safe='')}"
    return _request(path, params={"hours": hours})


@mcp.tool()
def farm_trend(hours: int = 24, points: int = 288) -> dict:
    """多场地算力趋势线（用于对比各场地走势；服务端缓存 15 秒）。

    参数：
      hours —— 回看时长小时数，默认 24（云端会钳制到 [1, 2160]）
      points —— 期望采样点数，默认 288（云端会钳制到 [24, 1000]，按
        hours*3600/points 秒分桶取均值，返回点数 ≤ points）

    返回结构：
      sites —— 每项 {id, name, type, points: [[ts, hashrate_ths], ...]}，
        hashrate_ths 单位 TH/s；同上，相邻点时间间隔明显偏大代表期间断报，
        不代表真实算力经历了线性变化。
    """
    return _request("/api/public/trend", params={"hours": hours, "points": points})


@mcp.tool()
def farm_daily(site: str, days: int = 14) -> dict:
    """单场地按天汇总（长期趋势/日报用，服务端缓存 60 秒，数据永久保留不会
    被滚动清理）。

    参数：
      site —— 场地的 site_id 或显示名（同 farm_site_detail，支持中文，自动
        URL 编码）
      days —— 回看天数，默认 14（云端会钳制到 [1, 3650]）

    返回结构：
      days —— 每天一条：{day(YYYY-MM-DD, UTC+8 切日), samples(当天样本数),
        avg_hashrate_ths/max_hashrate_ths(均值/峰值算力,TH/s),
        avg_online/avg_total(平均在线/总台数),
        avg_power_kw(平均功耗,kW),
        power_kwh(当天耗电估算,kWh —— 按 功率×真实采样间隔 时间积分，
        断档超 5 分钟不计入，是估算口径，精确对账以场地本地为准)}

      找不到该场地会返回 {"ok": false, "http_status": 404, "error": ...}。
    """
    return _request("/api/public/daily", params={"site": site, "days": days})


@mcp.tool()
def farm_customers() -> dict:
    """跨场地客户表（服务端缓存 30 秒）。用于按客户/矿工名维度问询时。

    返回结构：
      rows —— 每场地每客户一条明细：{site_id, worker, machines, uptime_pct,
        delivered_th_h, power_kwh, hours}（24h 口径，对账以场地本地 CSV 为准）
      by_worker —— 已跨场地按 worker(客户名) 合并汇总：{worker, sites(涉及
        场地数), machines, delivered_th_h, power_kwh}，按 machines 降序
    """
    return _request("/api/public/customers")


if __name__ == "__main__":
    mcp.run(transport="stdio")
