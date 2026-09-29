# 升级计划：场地唯一 ID + 安全/正确性修复 + 云端告警（2026-07-09）

> 本文是本轮改造的设计依据与执行清单。完成后保留作变更记录。
> 背景：架构评审发现 P0×3 / P1×5 问题与三个功能缺口；用户拍板新增核心需求——
> **每个本地运维端拥有独立 ID，云端按 ID 识别场地，永不搞错**。其余路线由工程侧决定。

---

## 0. 核心决策一览

| 决策点 | 结论 | 理由 |
|---|---|---|
| 场地身份 | **稳定唯一 site_id（机器生成）为主键，场地名只是显示标签** | 名字会改、会重复、会打错；ID 一次生成永不变，改名不再产生"幽灵场地" |
| ID 生成方 | 本地端自动生成并持久化，云端被动接受 | 场地第一次上报即自动注册的体验不变；云端不需要预先发号 |
| ID 格式 | `st-` + 12位随机hex（48bit，如 `st-3fa2b1c4d5e6`） | 可读可辨认、碰撞概率可忽略、不含场地信息（不泄密） |
| ID 存放 | `E:\ip\cloud_site_id.txt` 自动生成；`config.cloud.site_id` 可显式覆盖 | 配置文件保持人手可写；程序不回写 yaml（避免破坏注释） |
| 旧版兼容 | 上报体无 `site_id` 时回退用 `site` 名当 ID | 旧版本地端不升级也能继续用（等价于旧行为） |
| 旧库迁移 | 云端启动时自动迁移（列改名 + sites 表重建，旧名既当 id 又当 name） | 无人工步骤；当前无生产数据，迁移逻辑以后也用得上 |
| 每场地钥匙 | `ingest.tokens: {site_id: token}` 可选映射；**配了的场地必须用自己的钥匙**，没配的回退共享 `ingest.token` | 攻破一个场地只作废一把钥匙，不能再冒充其它场地 |
| 云端告警通道 | Telegram + Webhook（企业微信/钉钉群机器人同构，raw 兼容任意） | 云端是 Linux，跑不了本机的微信 GUI 工具；群机器人 webhook 一分钟配好 |

---

## 1. 场地唯一 ID（本轮核心改造）

### 1.1 本地端（E:\ip）

- `cloud_report.py` 增加 `_site_id(cfg)`：
  1. `config.cloud.site_id` 非空 → 直接用（迁移/恢复/模拟器场景）；
  2. 否则读 `cloud_site_id.txt`；不存在则生成 `st-<12hex>` 写入该文件。
- 两个上报体都带 `"site_id"`；启动日志打印本场地 ID。
- ⚠ 运维红线（写入文档）：**整目录克隆部署到新场地时，必须删除 `cloud_site_id.txt`**
  （下次启动重新生成），否则两个场地同 ID，数据在云端互相覆盖。

### 1.2 云端（E:\main）schema v2

```sql
sites(id TEXT PK, name TEXT, type, created_ts, last_push_ts)   -- name 可随时变
snapshots(..., site_id TEXT, ...)      -- 原 site 列改名
site_customers(site_id, worker, ...)   -- 同上
```

- ingest：按 `site_id` upsert（无则回退 `site` 名）；**name 每次上报都更新**——改名=改标签。
- 查询接口：场地条目返回 `id` + `name`；`/api/site/{key}`、`DELETE /api/site/{key}`
  按 id 寻址（找不到时按 name 兜底一次，方便人肉 curl）。
- 前端：卡片/弹窗全部用 `id` 寻址，界面只显示 `name`。
- 拉取模式场地：config 条目可选 `id`（缺省用 name 当 id，保持旧行为）。
- 迁移（db.connect 内，建表前执行）：
  `PRAGMA table_info` 探测旧列 → `ALTER TABLE ... RENAME COLUMN site TO site_id`；
  旧 sites(name 主键) → 重建为 (id,name) 双列，旧 name 同时填进 id 和 name。

## 2. P0 修复

| # | 问题 | 方案 |
|---|---|---|
| P0-1 | nginx 反代后全部请求源 IP=127.0.0.1，登录锁定/限速互相误伤 | `server.trust_proxy: false` 配置项；开启时 `_client_ip` 取 `X-Forwarded-For` 首个 IP。仅在确实有可信反代时开（该头可伪造） |
| P0-2 | 趋势图把断报时段直连成直线，掩盖事故 | 前端 `withGaps()`: 相邻点间隔 > max(300s, 4×中位间隔) 时插 `{x,y:null}`，`spanGaps:false` 断线 |
| P0-3 | 共享 ingest token 可横向伪造所有场地 | `ingest.tokens` 每场地钥匙（见 §0 决策）；token 校验移到解析出 site_id 之后 |

## 3. P1 修复

| # | 问题 | 方案 |
|---|---|---|
| P1-4 | 每小时清理拿写锁全表扫描，卡住事件循环上的 ingest | ① 加 `idx_snap_ts(ts)` 单列索引 ② DELETE 分批（LIMIT 5000 循环）③ ingest 端点改 **sync def + Body(dict)**（FastAPI 自动扔线程池，不再占事件循环） |
| P1-5 | 推送场地按名字排序，新场地注册挤动全部图例颜色 | `push_sites` 改 `ORDER BY created_ts`（颜色跟随实体） |
| P1-6 | 公共 API 缓存键用未钳制参数可撑内存；限速表旧 IP 不清理 | hours/points 端点层先 clamp 再作缓存键；缓存超 256 键剔最旧；限速表超 512 项剪过期窗口 |
| P1-7 | ingest 数值不校验（负数/天文数字/超长字符串污染库） | `_num` 加 [0, 上限] 钳制（算力≤1e9 TH、功率≤1e6 kW、台数≤1e6…）；worker 名截 128 字符 |
| P1-8 | 本地客户报表上报失败要再等整整 10 分钟；失败误计入摘要计数 | customers 推送独立 try；成功才更新 `last_cust`（失败下一轮 60s 就重试） |

## 4. 云端告警通知（新功能，最大缺口）

> 场地断电时本地监控也一起死了——全网唯一知道出事的是云端。没有主动通知，
> 云端只是展示板不是值班员。

- 新模块 `notify.py`，collector 每轮（60s）调用 `tick()`：
  - **状态跳变**：`ok/alert → unreachable`（场地失联）、`→ stalled`（监控停滞）、
    以及反向恢复。进程启动第一轮只记状态不发（防重启风暴）。
  - **算力骤降**：当前 10 分钟均值 < 45~90 分钟前基线 × (1 - `hashrate_drop_pct`%)
    且基线>0 → 告警；回升过线 → 恢复通知。抓大面积掉算力/限电，state 仍是 ok 也能发现。
- **冷却**：同场地同类型告警 `cooldown`(默认1800s) 内不重发；恢复通知不受冷却限制。
- **通道**（可同时开多个）：
  - `telegram`: bot_token + chat_id（沿用本地那套的用法）
  - `webhook`: `kind: wecom|dingtalk`（`{"msgtype":"text","text":{"content":...}}`，
    企业微信/钉钉群机器人同构）或 `kind: raw`（`{"text": ...}`）
- 每次发送写 `notify_log(ts, site_id, type, msg, ok)` 表，排查"为什么没收到"用。
- 文案示例：`⚠ 场地失联: 内蒙一场(风冷) 已 3 分钟未上报，最后在线 509/520`、
  `✅ 场地恢复: ...`、`📉 算力骤降: XX场 923→560 PH/s (-39%)`。

## 5. 日级汇总（长期数据）

- 新表 `daily(site_id, day, samples, avg/max算力, 平均在线/总数, avg_power_kw, power_kwh)`
  永久保留（一年不过几千行）。`day` 按 UTC+8 切日（国内场地）；`power_kwh` 为
  Σ(功率×名义间隔) 估算口径，精确对账仍以场地本地为准。
- collector 每小时重算"昨天+今天"两天（幂等 REPLACE）；启动时先补算一次。
- 接口：`GET /api/daily?site_id=&days=`（登录）与 `/api/public/daily`（agent）。
- 面板：场地详情弹窗底部加"近 14 天日报"小表（日期/均值算力/在线率/耗电）。

## 6. 部署便利 + 文档

- `Dockerfile` + `.dockerignore` + compose 片段（数据卷挂 config 与 db）。
- 文档同步：API.md（id 字段、site_id 上报、每场地钥匙、daily 端点）、
  DEVELOPMENT.md（schema v2、notify、rollup、ID 设计）、DEPLOY.md（trust_proxy、
  发钥匙 SOP、告警通道配置、client_max_body_size、Docker 部署）、README、
  E:\ip README/config 注释（site_id 与克隆红线）。

## 7. 测试计划（全部实测，不靠目测代码）

1. **旧库自动迁移**：用现有旧 schema 演示库直接启动新代码 → 数据完好、列已改名。
2. 混合链路：mock×2(拉取) + push 模拟（带显式 site_id）→ 三场地正常。
3. **改名不产生幽灵**：模拟器改 site_name 再推 → 面板同一场地换名字，数据连续。
4. 每场地钥匙：给模拟器 site_id 配专属 token → 用对的 200 / 用共享的 401 / 其它场地共享 token 不受影响。
5. 告警：本地起 webhook 接收器 → 杀模拟器 → ~3 分钟收到"场地失联"POST →
   重启模拟器 → 收到"恢复"；推一条低算力数据 → 收到"算力骤降"。
6. 断档趋势：断报几分钟再恢复 → 图上是空档不是直线（截图目检）。
7. 日报：rollup 跑完 → `/api/daily` 有行、弹窗小表显示。
8. 兼容：不带 site_id 的旧式 ingest 请求仍 200。
9. `py_compile` + JS 语法 + 面板截图全检。

## 8. 明确不做（本轮）

- 云端控制矿机的任何能力（架构红线）。
- 多实例/HA、换 PostgreSQL（当前规模 SQLite 远未见顶）。
- 场地端断网补传历史（总览缺几分钟无妨；有需要再议）。
- CSV 导出、移动端深度适配（P3 留后）。
