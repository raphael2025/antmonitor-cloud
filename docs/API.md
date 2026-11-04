# API 文档（云端总览）

> 基址：`http://<云服务器>:8900`（生产走 nginx TLS 后为 `https://overview.example.com`）。
> 所有响应为 JSON，UTF-8，含 `ok`(bool) 与 `ts`(Unix 秒，云端时间)。
> 时间戳一律 Unix 秒；实时／平均算力单位 TH/s（`hashrate_ths` / `avg_hashrate_ths`），累计交付为 TH·h（`delivered_th_h`），功率 kW（`power_kw`）、
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
  "scan_ts": 1783573880,      // 完整扫描的采样时间，随 customer_hashrates 提供
  "scan_age_s": 120,          // 场地本地上次扫描距现在多少秒(判"监控停滞"用)，可 null
  "customer_hashrates": [    // 可选：该次完整扫描的客户实时合计，不含单机 IP
    {"worker": "Asuna666", "machines": 320, "hashrate_ths": 100000.0,
     "hashrate_ts": 1783573880}
  ],
  "alert_summary": [          // 可选：活跃告警摘要(按类型分组，不含单机IP)。缺省字段=不动云端已存数据；
                              // 显式传 []=清空。上限 30 组、每组上限 30 个客户，超出截断。
    {"category": "offline", "label": "掉线", "count": 12,
     "since_ts": 1783573000,  // 该类告警最早触发时间(算"已持续多久")
     "workers": [{"worker": "Lolo3500", "count": 8}, {"worker": "BobFarm", "count": 4}]}
  ]
}
```
数字字段可缺省（按 0）；负数归零、超合理上限被钳制（防污染图表）。
`customer_hashrates` 的测量值例外：缺失／非法值为 `null`（未知），明确读到零才传 `0`。
这个可选完整列表随摘要更新，默认每 60 秒一次，避免客户实时值等待每 600 秒一次的历史报表；
旧场地端省略字段时保持已有实时值，超过新鲜度阈值后标为过期；显式 `[]` 表示本轮完整扫描没有客户机器。
`machines` 是该轮扫描的客户机器数；`hashrate_ts` 是采样时间，不能用报表发送时间代替。
云端优先用收到摘要的时间减 `scan_age_s` 还原采样时刻，并统一应用于列表内各行；缺少龄期时校验 `scan_ts`，避免未来时钟污染后续更新。
完整列表上限 5,000 行；非列表、非法行或重复客户名返回 `400`，不会把格式错误当成空场地。
**响应**：`{"ok": true, "ts": 1783574001}`

### 2. `POST /api/ingest/customers` — 客户报表（建议每 600s 一次）

更新该场地的客户历史报表（云端只存最新一份；历史对账以场地本地为准）。实时值随摘要独立更新；
收到首份完整实时列表后，该通道决定当前客户归属和算力，慢报表不再覆盖它。此前可使用报表携带的实时字段作为兼容来源。

**请求体**
```json
{
  "site_id": "st-3fa2b1c4d5e6",       // 同上，旧版可缺省
  "site": "内蒙一场",
  "hours": 24,                       // 统计周期(小时)
  "customers": [                      // 上限 5000 行，超出 400；worker 名超128字符截断
    {"worker": "Asuna666", "machines": 320,
     "uptime_pct": 99.2, "delivered_th_h": 2360160.0, "power_kwh": 423360.0,
     "hashrate_ths": 100000.0, "current_machines": 320,
     "avg_hashrate_ths": 98340.0, "hashrate_hours": 24,
     "observed_hours": 24.0, "coverage_pct": 100.0,
     "hashrate_ts": 1783573880}
  ]
}
```
**响应**：`{"ok": true, "ts": ..., "count": 1}`

新增字段均可省略，旧上报端与旧数据库继续兼容；缺失的新指标保留为 `null`，不会由累计交付伪造实时值或补成零。

| 字段 | 含义 |
|---|---|
| `hashrate_ths` | 最新完整扫描中该客户各台矿机实际算力的合计，TH/s；确认离线为零，无法确定完整合计时为 `null` |
| `current_machines` | 最新扫描的客户机器数，与历史报表的 `machines` 分开 |
| `avg_hashrate_ths` | `hashrate_hours` 窗口内，按有效观测持续时间加权的客户总平均算力，TH/s |
| `hashrate_hours` | 平均值所请求的回看窗口，默认 24；不等同旧累计报表可能截断后的 `hours` |
| `observed_hours` | 该客户有效观测覆盖的小时数，按时间计，**不是机器小时数** |
| `coverage_pct` | 有效覆盖比例：`observed_hours / hashrate_hours × 100`；不足 100% 时，平均值仅代表已观测时段 |
| `hashrate_ts` | 实时算力的采样时刻，Unix 秒，用来判断数据新鲜度 |

平均值包含确认离线的零值；监控停摆、没有读到算力的时段属于未知，不按零算，也不宣称覆盖完整一天。
例如一台机器稳定 293 TH/s 运行 24 小时，平均是 293 TH/s、累计交付是 7,032 TH·h；100 台同样运行时总平均是 29,300 TH/s（29.3 PH/s）。
`delivered_th_h` 保持原累计统计口径用于历史报表／CSV，不能直接改单位成 `/s`，也不能在不知道覆盖范围时直接除以 24。

### 3. `POST /api/ingest/ping` — 测试连接（只校验，不写数据）

场地端「🔌 测试连接」用。鉴权与上面两个接口完全相同（Bearer token + site_id），但不注册场地、不写任何数据。

```json
// 请求
{"site_id": "st-3fa2b1c4d5e6", "site": "内蒙一场"}
// 响应
{"ok": true, "ts": 1782660000, "registered": true, "site_name": "内蒙一场"}
```

### curl 示例

```bash
curl -X POST https://overview.example.com/api/ingest/summary \
  -H "Authorization: Bearer $INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"site":"测试场","type":"hydro","online":10,"total":12,"hashrate_ths":3300,"power_kw":59}'
```

---

## 二、看板查询接口（登录 Cookie，全部只读）

### 鉴权

> **所有 POST/PUT/PATCH/DELETE（含登录）必须带请求头 `X-CO-CSRF: 1`**（任意非空值），否则 `403`。
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
                  "hashrate_ths": 100000.0, "avg_hashrate_ths": 98340.0,
                  "current_machines": 320, "hashrate_hours": 24,
                  "observed_hours": 24, "coverage_pct": 100, "hashrate_ts": 1783573880,
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
                  "hashrate_ths": ..., "avg_hashrate_ths": ..., "hashrate_hours": 24,
                  "delivered_th_h": ..., "power_kwh": ...} ] }
```
> `by_worker` 按矿工名跨场地合并。客户总实时算力是各场地实测总算力之和，不能再乘机器数；
> 总平均算力也按相同统计窗口相加，不按场地数或机器数再次求均值。
> 任一场地缺少对应指标时，完整合计为 `null`；平均窗口不同时也不拼成一个“24 小时平均”。
> 展示平均值时必须同时检查覆盖比例，不足完整窗口只能标为已观测时段平均。

多个场地都请求 24 小时，也可能分别只观测到不同的时间片段。协议没有这些片段的交集信息，
因此任一场地覆盖不足时，多场地合计平均返回 `null`；单场地的已观测平均仍可查看。

客户明细和 `by_worker` 还包含以下展示状态，公共 API 同样返回：

| 字段 | 含义 |
|---|---|
| `hashrate_age_s` / `hashrate_stale` | 实时采样距现在的秒数／是否过期；没有可信时间时龄期为 `null`，单场地不因此标为过期 |
| `hashrate_source` | 明细的实时值来源：`summary`（摘要）、`report`（含采样时间的报表）、`legacy`（缺采样时间的报表兼容路径）；单凭此字段不能确定场地端版本 |
| `hashrate_unavailable_reason` | `not_reported`（同场地没有新指标证据）、`missing_data`（新指标通道存在，但本项未知）、`partial_sites`（汇总缺少部分场地）；有值时为 `null` |
| `avg_complete` | 是否具有完整统计窗口的平均值；仅为 `true` 时可直接作为该周期完整平均展示 |
| `avg_unavailable_reason` | `not_reported`、`missing_data`、`partial_sites` 的含义同上；另有 `mixed_periods`（跨场地窗口不同）、`partial_coverage`（多场地覆盖不足）；平均值可用时为 `null` |
| `updated_ts` / `report_age_s` / `report_stale` | 历史报表接收时间／龄期／是否过期，与实时采样时间分开 |
| `periods` | `by_worker` 涉及的平均统计窗口列表，单位小时 |
| `hashrate_known_sites` / `avg_known_sites` | `by_worker` 中有对应指标的场地数，缺少部分场地不补成零 |

默认实时过期阈值取 `poll.stalled_after_s`（1,200 秒）；平均报表过期阈值至少是该值，且不短于 `poll.customer_interval` 的三倍（默认 1,800 秒）。
`avg_complete` 描述覆盖，`report_stale` 描述新鲜度；展示时两者都需要检查。
单场地有平均值但 `avg_complete=false` 时，页面显示数值并常显「按有效时段计算」和覆盖时长，不能将其称为完整 24 小时平均。
`not_reported` 只说明未收到指标，不证明具体软件版本；页面建议检查场地端是否已经更新并重启。
`hashrate_age_s` / `report_age_s` 显式为 `null` 时，前端不得再从原始时间戳重算为“0 秒前”；超过云端时间 300 秒的时间戳不可信。
跨场地 `stale` 取各场地的任一过期状态：即使另一个场地的时间未知导致合计龄期为 `null`，已知的过期警告仍应保留。

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
- 删除必须使用准确的 `site_id`，不按显示名匹配（同名场地的数据不受影响）；不存在的 ID 返回 `404`。
- 删除时持久化停收标记，旧终端后续摘要/客户上报返回 `410`，重启云端也不会自动复活。
- 重新安装本地端会生成新 ID；若仍用原名称，页面提示重名并显示 ID，管理员可核对最后上报时间后删除旧记录。
- 场地在 `config.yaml > sites`（拉取模式）里 → `400`（请改配置文件）
- 非 admin → `403`

```bash
# 先登录拿 Cookie(写接口都要带 X-CO-CSRF 头)
curl -c ck.txt -X POST https://云端/api/login -H "Content-Type: application/json" \
     -H "X-CO-CSRF: 1" -d '{"username":"admin","password":"..."}'
curl -b ck.txt -H "X-CO-CSRF: 1" -X DELETE "https://云端/api/site/st-xxxxxxxxxxxx"
```

#### 场地名称和恢复管理（仅 admin）

- `PATCH /api/site/{site_id}`，JSON `{"name":"云海二期（新环境）"}`：设置云端显示名，后续上报不会覆盖；`{"name":""}` 恢复跟随本地名称。非法名称返回 `400`，与其他场地的当前显示名重复返回 `409`。
- `GET /api/sites/deleted`：返回已删除场地的 ID、名称和删除时间。
- `POST /api/site/{site_id}/restore`：移除停收标记；下次本地上报后重新出现。只恢复接收上报，**不恢复已删除的历史数据**。
- `/api/overview` 和场地详情中的 `reported_name` 是本地上报名，`name_override` 是云端覆盖名，`duplicate_name` 表示与其他 ID 的当前显示名重复。
- 管理操作均有权限及 CSRF 检查，并写入审计记录。姓名兜底查询遇到重名返回 `409`，请改用 ID。

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

### 10. 场地接入（仅 admin，网页「🔌 场地接入」用）

| 接口 | 说明 |
|---|---|
| `GET /api/connect` | 云端地址 `public_url`(及来源 config/guess)、模式 `mode`(domain/ip/self_signed/http/unset)、证书指纹 `fingerprint`(自签时)、共享 token 是否设置+末4位、各场地的 `id/name/type/registered` 与专属 token 状态、`auto_register`、`warnings`。未首次上报的场地也显示保存的名称/类型。**不含任何 token 明文** |
| `POST /api/connect/reveal` | `{"password", "target": "shared"\|<site_id>}` → `{target, token, code, site_name, site_type}`。需再输登录密码；已上报场地使用当前名称/类型，待接入场地恢复保存的描述 |
| `POST /api/connect/token` | `{"password", "target", "action": "rotate"\|"revoke"}` 更换/撤销；`{"password", "action": "new_site", "site_name", "site_type"}` 创建命名场地与专属 token → `{target, token, code, site_name, site_type}` |
| `POST /api/connect/settings` | `{"password", "public_url"?, "auto_register"?}` 改云端地址 / 自动注册开关，返回同 `GET /api/connect` |

- 密码错误返回 403 并计入来源 IP 的登录失败次数（8 次锁 5 分钟，429）；每次查看/更换都写审计（`token_reveal` / `token_rotate` / `token_revoke` / `token_new_site` / `connect_settings`）。
- 新建要求 `site_name` 为去除首尾空白后 1–64 个字符的字符串，不允许换行/控制字符；`site_type` 必须为 `air` / `hydro` / `mixed`。非法输入返回 400，不生成凭据。
- 网页生成的 token 与名称/类型元数据存 `cloud_settings.json`（0600）的 `ingest.tokens` / `ingest.site_meta`，同一次原子写入，立即生效。查看/轮换时，已上报场地使用当前名称/类型（含管理员设置的显示名），待接入场地恢复保存的交接描述；旧待接入项没有元数据仍可查看/轮换。
- 网页不改写 `config.yaml`；更换/撤销使用覆盖层。撤销专属 token 保存空值，拒绝该场地上报，不会回退到共享 token。
- **接入码**：`AMC1-` + base64url(JSON `{"u": 云端地址, "t": token, "f": 证书指纹?, "s": 场地ID?, "n": 场地名称?, "y": 场地类型?}`)。`n/y` 为可选扩展，旧码保持兼容，旧版场地端会忽略新增字段。
- 新版场地网页可粘贴后「连接云端」，安装脚本可使用 `--connect-code -` 隐藏输入。接入码包含长期凭据，不能写进命令行或 URL；页面 2 分钟后隐藏只清空显示，不撤销凭据。

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
- 客户维度问询用 `/api/public/customers`（`by_worker` 已跨场地合并）。实时看 `hashrate_ths`，平均看 `avg_hashrate_ths`，
  同时检查统计窗口、覆盖和采样时间；`delivered_th_h` 是累计交付，不能当作实时或平均算力。
- 想知道某场地"具体是谁受影响"（掉线/零算力等，不含单机IP）用 `/api/public/site/{key}`
  的 `alert_summary`：按类型分组，带受影响客户名+台数+已持续时长，不用追问场地本地。
- 算力显示按千进制换档：1000 TH=1 PH，1000 PH=1 EH。
- 本机已配好现成的汇报 skill：`~/.claude/skills/farm-report/`（读
  `~/.claude/farm-report.config.json` 的 url+token，可接 wechat-send 推微信群）。

---

## 四、心跳

### `GET /api/health`（免登录，无业务数据）

```json
{"ok": true, "ts": 1783574000, "instance": "本次启动的随机标识", "last_poll_age_s": 12}
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
