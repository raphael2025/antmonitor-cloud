# AntMonitor Cloud

<p align="center">
  <strong>多场地云端总览 · Multi-site overview for AntMonitor</strong><br/>
  <sub>场地本地运维 · 云端只看汇总</sub>
</p>

<p align="center">
  <a href="https://github.com/raphael2025/antmonitor">场地端 antmonitor</a> ·
  <a href="https://github.com/raphael2025/antmonitor-cloud">本仓库</a> ·
  <a href="https://t.me/+W3J9yAypNgpjNTk9">Telegram</a>
</p>

多场地汇聚看板，**部署在云服务器上**。各场地的 [AntMonitor](https://github.com/raphael2025/antmonitor) 每分钟把
摘要数据推上来，云端只看**每个场地的运行情况**——机器在线、总算力、功耗、告警、
集装箱、客户——运维细节和完整历史仍在各场地本地（本地=运维+数据保存，云端=总览）。

> 📚 **文档**：[部署与运维](docs/DEPLOY.md)（上线步骤/场地接入SOP/故障排查）｜
> [API 文档](docs/API.md)（上报协议+查询接口，字段级）｜
> [开发文档](docs/DEVELOPMENT.md)（架构/数据模型/前端规范/本地测试）｜
> [MCP 服务器](mcp_server/README.md)（把 Agent 公共 API 包装成 MCP 工具，给 MCP 客户端用）

```
 场地A本地监控          场地B本地监控          场地C本地监控
 (AntMonitor, 内网:8800)     (AntMonitor, 内网:8800)     (AntMonitor, 内网:8800)
      │ 每60s推摘要 + 每10min推客户报表 (HTTPS + ingest token, 几KB/次)
      └───────────────┼───────────────┘
              ▼ POST /api/ingest/*
        云端总览(本服务, 云服务器:8900)
        场地首次上报自动注册 · SQLite存分钟级快照
              → Web面板: KPI + 风冷/水冷场地卡片 + 趋势 + 客户
```

## 两种接入方式（可混用）

| 模式 | 方向 | 场地要求 | 配置位置 |
|---|---|---|---|
| **推送(推荐, 默认)** | 场地→云端 | 无：NAT 后面即可，不需要端口映射 | 场地本地 `config.yaml > cloud` 段 |
| 拉取 | 云端→场地 | 云端能访问场地 8800(公网/映射/frp) | 云端 `config.yaml > sites` 列表 |

**推送模式**：场地在自己的 `AntMonitor\config.yaml` 里填云端地址和场地名即可，
第一次上报自动出现在云端面板上（云端不用改任何配置）：

```yaml
cloud:
  enabled: true
  url: "https://overview.example.com"    # 云端总览地址(本服务)
  token: "<云端 ingest.token 或分配给本场地的专属token>"
  site_name: "内蒙一场"                   # 显示名(可随时改，改名不丢数据)
  site_type: air                          # air=风冷 | hydro=水冷
```

**场地身份 = 自动生成的唯一 site_id**（存场地的 `cloud_site_id.txt`，一次生成永不变）：
云端按 ID 识别场地永不搞错——改名只是换标签；克隆目录部署新场地时删掉该文件重新生成。

断网期间上报自动中断（云端该场地变灰显示"未收到上报"+最后已知数据），恢复自动续传；
上报线程完全独立，绝不影响本地扫描/告警。

## 快速开始（云服务器）

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml && chmod 600 config.yaml
python auth.py token        # 生成随机串，分别填 ingest.token / public_api.token(两把不同)
python auth.py passwd admin # 交互设 admin 密码(出厂默认口令 admin888 禁止登录)
python seccheck.py          # 安全自检，没有"✖ 高危"再上线
python server.py            # 默认 0.0.0.0:8900；公网务必前置 nginx https，见 docs/DEPLOY.md §3
# 然后去各场地 config.yaml 填 cloud 段(见上)，重启场地监控即可上线
```

**场地下线/改名清理**：`curl -H "X-CO-CSRF: 1" -X DELETE https://云端/api/site/<场地名>`（admin 登录 Cookie）。

## 场地状态判定

| 状态 | 条件 |
|---|---|
| ● 正常 | 数据正常且无告警 |
| ⚠ 告警 | 场地本地活跃告警 > 0（口径与本地一致；被本地忽略的集装箱故障位只显示不计入） |
| ⏸ 监控停滞 | 场地本地上次扫描龄期 > `poll.stalled_after_s`(默认1200s)——场地扫描进程可能死了 |
| ✖ 不可达 | 推送模式：超过 `poll.push_interval×fail_offline`(默认3分钟)未收到上报；拉取模式：连续拉取失败≥3次——断网/断电/服务挂了 |

不可达期间卡片继续显示**最后已知数据**并标注数据龄期；恢复后自动回归正常。

## 面板功能

- **全网 KPI**：场地在线数、矿机在线/总数、在线率、总算力(自动 TH/PH/EH)、总功耗(kW/MW)、活跃告警、集装箱故障、客户数
- **场地卡片**：按 ❄风冷 / 💧水冷 分组；每卡显示算力、在线率、功耗、告警、客户数（水冷多一行集装箱 总数/故障/离线）
- **各场地算力趋势**：多线对比，近 6h/24h/3天/7天
- **场地详情**（点卡片）：算力/在线台数/功耗三条趋势 + 该场地客户报表
- **客户**：跨场地按矿工名汇总 + 按场地明细（台数/可用率/交付算力/耗电，24h口径，对账以场地本地 CSV 为准）
- **日报**：场地详情内近14天日汇总（均值/峰值算力、在线率、耗电估算）；日汇总永久保留
- **告警通知**：场地失联/监控停滞/恢复/算力骤降 → Telegram / 企业微信/钉钉群机器人 / 邮件
  （场地断电时本地监控也死了——只有云端能发现整场失联，配置见 [docs/DEPLOY.md §5](docs/DEPLOY.md)）
- 30 秒自动刷新；断报时段趋势图留空不连线；云端服务失联时顶部红色横幅

## 接口（详见 [docs/API.md](docs/API.md)）

登录后只读：`GET /api/overview` 全网汇总+分场地 ｜ `GET /api/trend?hours=` 各场地算力趋势 ｜
`GET /api/site/{name}?hours=` 场地详情 ｜ `GET /api/customers` 客户 ｜
`DELETE /api/site/{name}` 删除推送场地(admin)
场地上报（Bearer `ingest.token`）：`POST /api/ingest/summary` ｜ `POST /api/ingest/customers`
**Agent 取数**（Bearer `public_api.token`，只读+限速，给 AI agent 做情况汇报）：
`GET /api/public/{overview, site/{name}, trend, customers}`
免登录：`GET /api/health` 心跳（外部看门狗探活用，无业务数据）

## 数据与容量

SQLite（WAL）。分钟级快照保留 `db.retention_days`(默认30) 天——10 个场地约 43 万行/月，
可忽略。客户报表只存每场地最新一份（历史对账在场地本地）。

## 生产部署 / 本地测试

- 云服务器上线（systemd、nginx TLS、场地接入 SOP、日常运维、故障排查表）→ **[docs/DEPLOY.md](docs/DEPLOY.md)**
- 本机先跑通全链路：`tools/` 里有假场地（mock_site.py）、假推送场地（push_site_sim.py）
  和测试配置（test_config.yaml），步骤见 [docs/DEVELOPMENT.md §10](docs/DEVELOPMENT.md)
- 项目结构、数据模型、场地状态机、配置项全表、前端图表规范 → [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)

## 安全清单

> `python seccheck.py` 一键自检；管理员登录后网页顶部也会红条提示高危项，
> 「🔒 账号安全」里有自检清单、在线会话（可一键踢下线）、最近操作记录（审计）。

1. 改默认口令：`python auth.py passwd admin`（出厂默认口令**禁止登录**；登录后也可网页自己改）
2. 换掉 `ingest.token` 与 `public_api.token`（`CHANGE_ME…` 模板值视为未配置，接口直接关闭）；
   场地逐个发**专属钥匙**（`ingest.tokens`，一场地一把，被攻破只作废一把），
   全部发完后 `ingest.auto_register: false`
3. 公网部署必须 TLS（nginx 反代，传 `X-Forwarded-Proto`）+ **`trust_proxy: true`** +
   `server.host: 127.0.0.1`（8900 不对外）
4. 云端对场地**永远只读、无任何反向通道**——推送模式云端连场地地址都不知道，
   云端被攻破也碰不到矿机；场地端也不读取云端响应里的任何指令
5. 场地来数一律当不可信数据：数值列只进数字、文本去控制符，前端再转义一遍 + CSP 禁止
   内联脚本——即使某个场地被攻破，也没法借上报数据在管理员浏览器里执行脚本
6. 浏览器侧：写操作要 `X-CO-CSRF` 头 + SameSite=Strict Cookie（防跨站伪造），
   禁止 iframe 嵌套（防点击劫持），Chart.js 本地托管（不依赖外部 CDN）
