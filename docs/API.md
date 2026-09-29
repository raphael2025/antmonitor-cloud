# API 文档（云端总览）

> 基址：`http://<云服务器>:8900`（生产走 nginx TLS 后为 `https://overview.example.com`）。
> 所有响应为 JSON，UTF-8，含 `ok`(bool) 与 `ts`(Unix 秒，云端时间)。
> 时间戳一律 Unix 秒；算力单位 TH/s（字段 `hashrate_ths`）、功率 kW（`power_kw`）、
> 能量 kWh（`power_kwh`）、时长秒（`*_s` / `*_ts`）。

接口分四组：

| 组 | 鉴权 | 用途 |
|---|---|---|
| 场地上报 `POST /api/ingest/*` | `Authorization: Bearer <ingest.token>` | 场地本地监控每分钟推数据（写入口，仅此两个） |
| 看板查询 `GET /api/*` | 登录 Cookie（`co_token`） | Web 面板/内部人员取数，全部只读 |
| **Agent 公共 API `GET /api/public/*`** | `Authorization: Bearer <public_api.token>` | AI agent/外部脚本取数做情况汇报，只读 |
| 心跳 `GET /api/health` | 免鉴权 | 外部看门狗探活，无业务数据 |

> 两把 token 职责分离：`ingest.token` 只能写上报、`public_api.token` 只能读汇总，泄露互不影响。

---

## 一、场地上报接口（推送模式的写入口）

**鉴权**：请求头 `Authorization: Bearer <token>`。
- 共享钥匙：`config.yaml > ingest.token`（所有场地通用）
- **每场地专属钥匙（推荐）**：`ingest.tokens: {<site_id>: <token>}`——配了的场地**必须**用
  自己的钥匙（共享钥匙对它无效），一个场地被攻破只作废一把，不能冒充其它场地
- 完全未配置（或仍是 `CHANGE_ME…` 模板值）→ 该组端点一律 `404`（fail-closed）；token 错误 → `401`；
  `site_id/site` 非法（空或超 64 字符）→ `400`
- 限速：每来源 IP 每分钟 `ingest.rate_per_min`（默认 120）次，错误 token 也计数，超出 `429`
- 新场地首次上报自动注册；`ingest.auto_register: false` 时只收 `ingest.tokens` 里登记过的
  site_id（其它 → `403`）；场地总数达 `ingest.max_sites`（默认 200）后新场地 → `403`
- 请求体上限 `server.max_body_bytes`（默认 2MB），超出 `413`；必须带 Content-Length
- **字段一律清洗**：数值字段只接受数字（字符串/NaN/负数 → 0 或钳到上限），文本字段去掉
  换行等控制字符并截长——场地端数据按不可信处理，脏数据进不了库也到不了面板

**场地身份**：`site_id`（稳定唯一，本地端自动生成，见 `E:\ip\cloud_report.py`）是数据主键；
`site` 只是显示名，**随时可改、改名不丢数据**。旧版上报体没有 `site_id` 时回退用名字当 id。

> 上报方通常是场地的 `E:\ip\cloud_report.py`（配好 `cloud:` 段自动推）。
> 协议是通用的——**任何系统按下面的 schema POST 都能接入**（比如以后有别的监控方案的场地）。

### 1. `POST /api/ingest/summary` — 场地摘要（建议每 60s 一次）

场地第一次上报会**自动注册**（出现在面板上），`type`/`site` 以最近一次上报为准。

**请求体**
```json
{
  "site_id": "st-3fa2b1c4d5e6", // 稳定唯一ID(主键)；旧版可缺省(用 site 名当 id)
  "site": "内蒙一场",           // 显示名(可改，改名=改标签)
  "type": "air",              // air=风冷 | hydro=水冷(其他值按 air 处理)
  "ts": 1783574000,           // 可选；云端不信任场地时钟，入库一律用云端时间
  "online": 3298, "total": 5180,
  "hashrate_ths": 1013328.0,  // 全场总算力 TH/s
  "power_kw": 18103.6,        // 全场总功耗 kW
  "active_alerts": 7,         // 场地本地活跃告警数
  "containers": 30,           // 水冷集装箱数(风冷场填0)
  "containers_faulty": 2,
  "containers_offline": 1,
  "scan_age_s": 120,          // 场地本地上次扫描距现在多少秒(判"监控停滞"用)，可 null
  "alert_summary": [          // 可选：活跃告警摘要(按类型分组，不含单机IP)。缺省字段=不动云端已存数据；
                              // 显式传 []=清空。上限 30 组、每组上限 30 个客户，超出截断。
    {"category": "offline", "label": "掉线", "count": 12,
     "since_ts": 1783573000,  // 该类告警最早触发时间(算"已持续多久")
     "workers": [{"worker": "Lolo3500", "count": 8}, {"worker": "BobFarm", "count": 4}]}
  ]
}
```
数字字段可缺省（按 0）；负数归零、超合理上限被钳制（防污染图表）。
**响应**：`{"ok": true, "ts": 1783574001}`

### 2. `POST /api/ingest/customers` — 客户报表（建议每 600s 一次）

整表替换该场地的客户数据（云端只存最新一份；历史对账以场地本地为准）。

**请求体**
```json
{
  "site_id": "st-3fa2b1c4d5e6",       // 同上，旧版可缺省
  "site": "内蒙一场",
  "hours": 24,                       // 统计周期(小时)
  "customers": [                      // 上限 5000 行，超出 400；worker 名超128字符截断
    {"worker": "Asuna666", "machines": 320,
     "uptime_pct": 99.2, "delivered_th_h": 2360160.0, "power_kwh": 423360.0}
  ]
}
```
**响应**：`{"ok": true, "ts": ..., "count": 1}`

### curl 示例

```bash
curl -X POST https://overview.example.com/api/ingest/summary \
  -H "Authorization: Bearer $INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"site":"测试场","type":"hydro","online":10,"total":12,"hashrate_ths":3300,"power_kw":59}'
```

---

## 二、看板查询接口（登录 Cookie，全部只读）

### 鉴权

> **所有 POST/DELETE（含登录）必须带请求头 `X-CO-CSRF: 1`**（任意非空值），否则 `403`。
> 这是防跨站请求伪造：别的网站没法让浏览器带上自定义头。网页前端已自动带；
> 用 curl 调写接口时记得加 `-H "X-CO-CSRF: 1"`。场地上报 `/api/ingest/*` 不受此限。

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/login` | POST | `{"username","password"}` → 设 HttpOnly + SameSite=Strict Cookie `co_token`(7天，https 访问时自动加 Secure)。失败 `401`；同源 IP 连续失败 8 次锁 5 分钟(`429`)；**出厂默认口令禁止登录 → `403`**（先用 `python auth.py passwd 用户名` 改） |
| `/api/logout` | POST | 注销 |
| `/api/me` | GET | `{authenticated, user, role, auth_enabled}` |
| `/api/password` | POST | 改自己的密码 `{"old","new"}`（任何已登录用户）。新密码至少 10 位且不能是常见弱口令；成功后该用户**其它会话全部下线**，当前会话保留。原密码错误计入登录失败锁定 |
| `/api/security` | GET | 安全中心（仅 admin）：`{checks:[{level:high\|warn\|info,msg}], sessions:在线会话数, audit:[最近100条审计]}` |
| `/api/sessions/revoke` | POST | 强制其它所有会话下线（仅 admin，保留自己）→ `{revoked: 踢掉数量}` |

角色：`viewer`(只读) / `admin`(只读 + 删除推送场地 + 通知设置 + 版本更新 + 安全中心)。
`auth.enabled: false` 时免登录按 viewer。改密码/删用户/改角色后，相关旧会话立即失效。

审计表 `audit_log` 记录：登录成功/失败、退出、改密码、改通知设置(只记改了哪块，不记值)、
测试通知、版本更新、删除场地、强制下线；含用户名与来源 IP，保留 180 天。

### 1. `GET /api/overview` — 全网汇总 + 分场地（面板主接口，30s 轮询）

```json
{
  "ok": true, "ts": 1783574000, "poll_interval": 60,
  "totals": {
    "sites": 3, "sites_ok": 2, "sites_unreachable": 1,
    "machines_total": 5720, "machines_online": 5441,
    "hashrate_ths": 1539388.2, "power_kw": 30861.9,
    "active_alerts": 2, "containers": 30, "containers_faulty": 1,
    "containers_offline": 0, "customers": 7
  },
  "sites": [{
    "id": "st-3fa2b1c4d5e6",         // 稳定唯一ID(寻址用它)；name 只是显示标签
    "name": "内蒙一场", "type": "air",
    "mode": "push",                  // push=场地上报 | pull=云端拉取
    "state": "ok",                   // ok|alert|stalled|unreachable|pending(见下表)
    "fails": 0,                      // 拉取模式的连续失败次数(推送模式恒0)
    "error": null,                   // 不可达原因(推送="超过N分钟未收到该场地上报")
    "data_ts": 1783573990, "data_age_s": 10,   // 最后一条成功数据的时间/龄期
    "scan_age_s": 60,                // 场地本地上次扫描龄期
    "online": 3298, "total": 5180, "hashrate_ths": 1013328.0, "power_kw": 18103.6,
    "active_alerts": 7, "containers": 0, "containers_faulty": 0, "containers_offline": 0,
    "customers": 2                   // 该场地客户数
  }]
}
```

**场地状态 `state`**

| 值 | 含义 | 判定 |
|---|---|---|
| `ok` | 正常 | 有数据、无告警 |
| `alert` | 有告警 | `active_alerts>0`（与场地本地口径一致；集装箱真故障/离线本地会生成告警计入其中，被本地忽略的故障位只显示不算告警） |
| `stalled` | 监控停滞 | `scan_age_s > poll.stalled_after_s`(默认1200)——场地扫描进程可能死了 |
| `unreachable` | 不可达 | 推送：超 `poll.push_interval×fail_offline`(默认3分钟)未收到上报；拉取：连续失败≥`fail_offline` |
| `pending` | 等待数据 | 刚配置/刚启动，还没有任何成功数据 |

> 不可达时 `online/total/...` 仍是**最后已知数据**（不清零），用 `data_age_s` 判断新鲜度。
> `totals` 聚合的是各场地最后已知数据（含不可达场地）。

### 2. `GET /api/trend?hours=24&points=288` — 各场地算力趋势

`hours` 1~2160；返回按时间桶降采样的均值点。

```json
{ "ok": true, "hours": 24,
  "sites": [ {"name": "内蒙一场", "type": "air",
              "points": [[1783570000, 61234.5], [1783570300, 61300.2]]} ] }
```

### 3. `GET /api/site/{key}?hours=24` — 单场地详情

`key` = 场地 `id`（推荐）；按 id 找不到时按显示名兜底一次（方便人肉 curl）。

```json
{
  "ok": true, "site": { ...同 overview 的场地条目... }, "hours": 24,
  "trend": [ {"ts": 1783570000, "online": 3298, "total": 5180,
              "hashrate_ths": 1013328.0, "power_kw": 18103.6, "active_alerts": 7} ],
  "customers": [ {"site": "内蒙一场", "worker": "Asuna666", "machines": 320,
                  "uptime_pct": 99.2, "delivered_th_h": 2360160.0, "power_kwh": 423360.0,
                  "hours": 24, "updated_ts": 1783573900} ],
  "alert_summary": [ {"category": "offline", "label": "掉线", "count": 12,
                      "since_ts": 1783573000,
                      "workers": [{"worker": "Lolo3500", "count": 8}]} ]
}
```
`alert_summary` = 场地最近一次上报带的活跃告警摘要（见上面 ingest summary 的字段说明），
场地从未推送过该字段时为 `[]`。场地不存在 → `404`。

### 4. `GET /api/customers` — 跨场地客户

```json
{ "ok": true,
  "rows": [ ...全部 (site, worker) 明细行, 字段同上... ],
  "by_worker": [ {"worker": "Asuna666", "sites": 2, "machines": 2100,
                  "delivered_th_h": ..., "power_kwh": ...} ] }
```
> `by_worker` 按矿工名跨场地合并（同一客户在多场地托管时汇总台数/交付/耗电）。

### 5. `GET /api/daily?site=<id>&days=14` — 单场地日级汇总

日汇总**永久保留**（分钟快照只留30天），长期趋势/月度对比用。UTC+8 切日；
`power_kwh` 为 Σ(功率×名义间隔) 估算，精确对账以场地本地为准。

```json
{ "ok": true, "id": "st-...", "name": "内蒙一场",
  "days": [ {"site_id": "st-...", "day": "2026-07-09", "samples": 1440,
             "avg_hashrate_ths": 61234.5, "max_hashrate_ths": 62890.1,
             "avg_online": 509.2, "avg_total": 520.0,
             "avg_power_kw": 1730.4, "power_kwh": 41530.2} ] }
```

### 6. `DELETE /api/site/{key}` — 删除场地（仅 admin）

删除该场地的注册信息、全部快照、客户/日汇总/通知数据（场地下线后清理）。
改名**不需要**删除——场地按 `site_id` 识别，改 `site_name` 自动更新标签。
- 场地在 `config.yaml > sites`（拉取模式）里 → `400`（请改配置文件）
- 非 admin → `403`

```bash
# 先登录拿 Cookie(写接口都要带 X-CO-CSRF 头)
curl -c ck.txt -X POST https://云端/api/login -H "Content-Type: application/json" \
     -H "X-CO-CSRF: 1" -d '{"username":"admin","password":"..."}'
curl -b ck.txt -H "X-CO-CSRF: 1" -X DELETE "https://云端/api/site/st-xxxxxxxxxxxx"
```

### 7. `GET /api/settings` — 当前告警通知设置（仅 admin）

网页版"⚙ 通知设置"面板用。**secret 字段一律掩码**，从不回传明文——`telegram.bot_token`、
`webhook.url`（常内嵌密钥，如 `...?key=xxxx`）、`email.password` 分别用布尔哨兵
`bot_token_set` / `url_set` / `password_set` 代替，前端靠这个区分"从未配置"和"已配置"。
其余字段（`chat_id`、`smtp_host`、`username` 等）原样返回。

```json
{ "ok": true, "alerts": {
  "enabled": true, "cooldown": 1800, "hashrate_drop_pct": 30, "offline_threshold": 0,
  "telegram": {"enabled": false, "bot_token_set": false, "chat_id": ""},
  "webhook":  {"enabled": true,  "url_set": true, "kind": "wecom"},
  "email":    {"enabled": false, "smtp_host": "", "smtp_port": 465, "use_ssl": true,
               "use_starttls": true, "username": "", "password_set": false,
               "from_addr": "", "to": [], "subject_prefix": "[矿场云端]"}
} }
```
非 admin → `403`。

### 8. `POST /api/settings` — 保存告警通知设置（仅 admin）

请求体 `{"alerts": {...部分字段...}}`，只需传要改的字段。**掩码字段留空/缺省 = 保留原值**
（标准"留空不改"表单语义，不会被清空）；非空则覆盖。`webhook.kind` 非法值回退
`wecom`；`smtp_port` 越界回退原值；字符串字段按长度截断；数字字段非法输入回退默认/原值
——恶意或畸形输入永远不会 500，只会被静默钳制。字符串去掉换行等控制字符（防邮件头注入）。
`webhook.url` 必须 `http(s)://` 开头否则 `400`；发送时拒绝发往本机/`169.254.x`(云元数据)
等内部地址、不跟随重定向（防会话被盗后借通知功能打服务器内网）——确需发往本机接收端，
在 config.yaml 该通道下写 `allow_internal: true`（网页改不了这个开关）。

保存后**原地**合并进运行中的 `CFG["alerts"]`（同一个 dict 对象，`Notifier` 共享同一引用，
改动**立即生效，无需重启**），并持久化到 `cloud_settings.json`（进程重启后仍生效，见
DEVELOPMENT.md §2/§7）。响应与 `GET` 同一掩码形状。

```bash
curl -b ck.txt -X POST https://云端/api/settings -H "Content-Type: application/json" -H "X-CO-CSRF: 1" \
     -d '{"alerts":{"webhook":{"enabled":true,"url":"https://qyapi.weixin.qq.com/...","kind":"wecom"}}}'
```

### 9. `POST /api/notify/test` — 发送测试通知（仅 admin）

给**当前每个已启用**的通道各发一条测试消息，逐通道回报结果——管理员配完立刻知道
哪个通道没打通，不用等真实告警触发。返回 `true`=发送成功、`false`=尝试过但失败、
`null`=该通道未启用（未尝试，不算失败）。每个**实际尝试**的通道都会在 `notify_log`
留一行 `type="test"`，与真实告警共用同一张排查表。

```json
{ "ok": true, "result": {"telegram": null, "webhook": true, "email": false} }
```

---

## 三、Agent 公共 API（`GET /api/public/*`，只读）

给 AI agent（hermes/Claude 等）和外部脚本做**情况汇报**用，与本地监控的 `/api/public/*`
同一套约定：

- **鉴权**：`Authorization: Bearer <token>`（推荐）或 `?token=<token>`；
  token = `config.yaml > public_api.token`。未配置或仍是 `CHANGE_ME…` → 整组 `404`（fail-closed）；错误 → `401`。
  也兼容 `?token=` 查询参数，但会出现在 nginx 访问日志里，**优先用请求头**。
- **限速**：每来源 IP 每分钟 120 次（错误 token 也计数），超出 `429`。
- **缓存**：overview 5s / site 10s / trend 15s / customers 30s（响应即最新缓存，
  agent 侧无需再节流到秒级以下）。
- **永远只读**：无任何写/控制能力，token 泄露最多泄露汇总数据。

| 端点 | 返回 | 缓存 |
|---|---|---|
| `GET /api/public/overview` | 同 `GET /api/overview`（全网 totals + 分场地 state/算力/在线/功耗/告警，**agent 最常用**） | 5s |
| `GET /api/public/site/{key}?hours=24` | 同 `GET /api/site/{key}`（详情+趋势+客户+活跃告警摘要） | 10s |
| `GET /api/public/trend?hours=&points=` | 同 `GET /api/trend` | 15s |
| `GET /api/public/daily?site=&days=` | 同 `GET /api/daily`（日级汇总，长期数据） | 60s |
| `GET /api/public/customers` | 同 `GET /api/customers` | 30s |

```bash
TOKEN=<public_api.token>
curl -s -H "Authorization: Bearer $TOKEN" https://overview.example.com/api/public/overview
```

**Agent 接入建议**

- 先看 `GET /api/health`（免token）：不通=云端总览自己挂了，这本身就是要汇报的事故。
- 汇报主体用 `/api/public/overview`：`sites[].state` 非 `ok` 的置顶
  （`unreachable`=场地断网断电、`stalled`=场地监控停滞、`alert`=有告警），
  每场地报 名称/类型(风冷air/水冷hydro)/算力/在线率/功耗；`data_age_s` 大=数据旧要注明。
- 客户维度问询用 `/api/public/customers`（`by_worker` 已跨场地合并）。
- 想知道某场地"具体是谁受影响"（掉线/零算力等，不含单机IP）用 `/api/public/site/{key}`
  的 `alert_summary`：按类型分组，带受影响客户名+台数+已持续时长，不用追问场地本地。
- 算力显示按千进制换档：1000 TH=1 PH，1000 PH=1 EH。
- 本机已配好现成的汇报 skill：`~/.claude/skills/farm-report/`（读
  `~/.claude/farm-report.config.json` 的 url+token，可接 wechat-send 推微信群）。

---

## 四、心跳

### `GET /api/health`（免登录，无业务数据）

```json
{"ok": true, "ts": 1783574000, "last_poll_age_s": 12}
```
`last_poll_age_s` = 采集循环上次跑完距现在秒数（远超 `poll.interval` 说明云端服务自身卡死，
外部看门狗应报警）。纯推送部署下该循环也在跑（做数据清理），此值同样有效。

---

## 错误码

| 码 | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 参数非法（site 名称、customers 超限、删除拉取场地等） |
| 401 | 未登录 / 密码错误 / ingest token 错误 |
| 403 | 已登录但权限不足（DELETE 需 admin）/ 写请求缺 `X-CO-CSRF` 头 / 默认口令禁止登录 / 未登记场地或场地数超限 |
| 404 | 场地不存在 / ingest.token 未配置（上报接口整体隐藏） |
| 411 / 413 | 请求体无长度 / 超过 `server.max_body_bytes` |
| 429 | 登录失败次数过多（锁定 5 分钟）/ 上报或 Agent API 超频 |
