# 开发文档（云端总览）

> 给改代码的人看。接口细节见 [API.md](API.md)，部署运维见 [DEPLOY.md](DEPLOY.md)。

## 1. 定位与设计原则

- **云端只是总览**：每场地的运行情况（机器/客户/用电/在线），分钟级粒度。
  运维操作、完整历史、对账原始数据都在各场地本地（E:\ip 那套）。
- **云端对场地只读、无反向通道**：推送模式下云端连场地地址都不知道；
  即使云端被攻破也无法触碰任何矿机。永远不要往这个项目里加"下发命令"类功能——
  那属于本地运维系统。
- **简单优先**：单进程 FastAPI + SQLite(WAL) + 原生 JS。数据量极小
  （N 场地 × 1440 行/天），十年内不需要更重的架构。

## 2. 目录结构

```
E:\main\
  server.py        FastAPI：鉴权、查询接口、上报接口、静态托管；进程入口
  collector.py     后台线程：拉取轮询 + 告警评估 + 每小时清理/日汇总 + 场地状态判定
  notify.py        告警通知：状态跳变/算力骤降 → Telegram/Webhook(企业微信/钉钉)
  db.py            SQLite 存取（建表/迁移/写入/查询/降采样/日汇总/清理），写操作模块级锁串行
  auth.py          登录（pbkdf2 哈希 + 内存 Cookie 会话 + 失败限流 + 默认口令禁登 + 改密码）；
                   也是 CLI：python auth.py passwd 用户名 / token / hash
  validate.py      场地来数清洗(推送/拉取共用)：数值钳制、文本去控制符——场地数据一律不可信
  seccheck.py      安全自检清单(启动打印 / 网页安全中心 / python seccheck.py)
  appconfig.py     网页可改的通知设置运行期覆盖层（cloud_settings.json 读写，见下方说明）
  config.yaml      全部配置（见 §7）
  cloud_settings.json  网页保存的通知设置（不入库，见下方说明；首次未保存过则不存在）
  web\             前端面板（index.html / style.css / app.js；vendor/chart.umd.min.js = Chart.js 4.5.1 本地副本）
  docs\            本文档 / API.md / DEPLOY.md / UPGRADE_PLAN.md(变更记录)
  tools\           mock_site.py(假场地) / push_site_sim.py(假推送) —— 本地测试用
  Dockerfile       容器部署（config/db 走卷挂载，见 DEPLOY.md）
```

### 2.1 config.yaml 与 cloud_settings.json 的分层（运行期覆盖层）

`config.yaml` 只在进程启动时读一次——改它必须登服务器改文件+重启进程。但通知设置
（Telegram/Webhook/邮件）这类"管理员想在浏览器里改完立即生效、且重启后还在"的参数，
另存一份 `cloud_settings.json`（`appconfig.py` 管，`GET/POST /api/settings` 用），作为
**config.yaml 之上的运行期覆盖层**：

```
config.yaml(出厂默认，改需重启) ──启动时 appconfig.apply_settings()──▶ 深合并进 CFG["alerts"]
                                                                          ▲
cloud_settings.json(网页保存, 覆盖同名字段) ──每次 POST /api/settings 落盘──┘
```

- **不入库**：`cloud_settings.json` 在 `.gitignore` 里（与 `config.yaml`/`*.db` 同一条规则），
  `git pull` 更新代码永远不会碰它，与场地本地 `E:\ip\settings.json` 是同一套设计哲学。
- **原子写**：`appconfig.update_settings()` 加锁做"读-改-写"（先写 `.tmp` 再 `os.replace()`），
  两个管理员并发保存不会互相覆盖，进程崩在写一半也不会留半截 JSON。
- **原地合并、不整体替换**：`apply_settings(cfg, s)` 是深合并进 `cfg["alerts"]` 的嵌套字典，
  不是 `cfg["alerts"] = 新dict`——`collector.py` 里 `Notifier` 持有的是同一个 dict 对象引用，
  整体替换会导致它读到的还是旧对象（见 `notify.py`/`collector.py` 顶部注释）。
- **首次运行没有这个文件完全正常**——`load_settings()` 读不到就返回 `{}`，等同没有覆盖，
  全部按 `config.yaml` 的值来；第一次网页保存后才会创建它。

## 2.5 场地身份设计（site_id，核心约定）

- **数据主键是 `site_id`**（`st-`+12hex，场地本地自动生成持久化于 `cloud_site_id.txt`，
  或 `cloud.site_id` 显式指定）；**场地名只是显示标签**，随时可改不丢数据。
- 旧版上报没有 `site_id` → 回退用名字当 id（兼容）。
- 拉取模式场地：config 条目可选 `id` 字段，缺省用 name 当 id。
- 每场地专属上报钥匙 `ingest.tokens: {site_id: token}`——配了的场地必须用自己的钥匙。
- v1 旧库（按场地名）在 `db.connect()` 里自动迁移（列改名 + sites 表重建，旧名既当 id 又当 name）。

## 3. 架构与数据流

```
【推送模式(主)】 场地 cloud_report.py ──每60s POST /api/ingest/summary──▶ ┐
                                     └─每600s POST /api/ingest/customers─▶ │ server.py
【拉取模式(兼容)】collector.py ──每60s GET 场地/api/public/summary?lite=1──▶ │   ▼
                                                                        db.py(SQLite)
 web面板 ◀──30s轮询 /api/overview /api/trend /api/customers ── server.py ◀──┘
```

- 两模式产出**同一张 snapshots 表**的行，查询层不区分来源；同名场地 config(拉取)优先。
- 上报入库时间戳用**云端时间**（不信任场地时钟，避免时钟漂移打乱趋势）。
- 场地端上报逻辑在 `E:\ip\cloud_report.py`（独立线程，失败只打日志不影响本地）。

## 4. 数据模型（SQLite，WAL）

```sql
snapshots(           -- 分钟级快照：每场地每次拉取/上报一行
  id, ts, site_id, ok, -- ok=0 是拉取失败记录(只有 error 字段, 推送模式不产生失败行)
  online, total, hashrate_ths, power_kw, active_alerts,
  containers, containers_faulty, containers_offline,
  scan_age_s,        -- 场地本地上次扫描龄期(判"监控停滞")
  error)
  INDEX (site_id, ts), INDEX (ts)   -- ts 单列索引给清理用(否则DELETE全表扫)

site_customers(      -- 每场地客户报表, 只存最新一份(整表替换)
  site_id, worker, machines, uptime_pct, delivered_th_h, power_kwh,
  hours, updated_ts, PRIMARY KEY(site_id, worker))

site_alert_summary(  -- 每场地活跃告警摘要, 只存最新一份(整表替换); 不含单机IP
  site_id, category, label, count, since_ts,
  workers_json,      -- [{"worker":.., "count":..}] 序列化; 场地未推送该字段=保留上次值
  updated_ts, PRIMARY KEY(site_id, category))

sites(               -- 场地注册表(推送首报自动 upsert；拉取场地 collector 启动登记)
  id PK, name,       -- id 稳定唯一; name/type 以最近一次上报为准
  type, created_ts, last_push_ts)

daily(               -- 日级汇总, 永久保留(UTC+8切日; kWh为估算口径)
  site_id, day, samples, avg/max_hashrate_ths,
  avg_online, avg_total, avg_power_kw, power_kwh, PK(site_id, day))

notify_log(id, ts, site_id, type, msg, ok)   -- 告警发送记录(留90天)
```

- 保留策略：`snapshots` 超 `db.retention_days`(默认30天) 由 collector 每小时清一次
  （**分批 DELETE LIMIT 5000**，短锁，不卡住 ingest）；`daily` 永久保留。
- 趋势查询 `db.trend()`：按 `hours*3600/points` 秒分桶取均值（`GROUP BY ts/bucket`），
  任意时间范围都返回 ≤ ~300 个点，前端不用降采样。
- 并发：单连接 `check_same_thread=False` 跨线程共享；**所有写操作**走 `db._lock` 串行。
  读不加锁（WAL 读写不互斥）。

## 5. 线程模型

| 线程 | 职责 | 说明 |
|---|---|---|
| uvicorn 事件循环 | HTTP 查询接口 | 查询是 sync def → FastAPI 自动扔线程池 |
| 线程池（FastAPI） | ingest 上报写库 | ingest 也是 sync def(Body 注入)，写库不占事件循环 |
| collector 守护线程 | 拉取轮询、告警评估(notify.tick)、每小时 cleanup+日汇总 | FastAPI startup 启动（幂等）；纯推送部署时 sites 为空、仍跑告警/清理/汇总 |
| （场地侧）cloud-report | 上报 | 属于 E:\ip 进程，见其 `cloud_report.py` |

## 6. 场地状态机（collector.site_state）

```
pending ──首条数据──▶ ok ⇄ alert(告警/箱故障)
                      │ scan_age_s > stalled_after_s        → stalled
  push: 超 interval×fail_offline 秒未收到上报               → unreachable
  pull: 连续 fail_offline 次拉取失败                        → unreachable
  （unreachable 期间保留最后已知数据；恢复自动回归）
```

判定入口在 `server._site_entry()`，每次 `/api/overview` 实时算，无状态残留。

## 7. 配置项全表（config.yaml）

| 键 | 默认 | 说明 |
|---|---|---|
| `ingest.token` | — | 场地上报共享密钥；与 tokens 都空 = 上报接口整体 404(关闭) |
| `ingest.tokens` | `{}` | 每场地专属密钥 `{site_id: token}`；配了的场地必须用自己的钥匙 |
| `sites[]` | `[]` | 拉取模式场地：`{name, id?, type: air\|hydro, url, token}`；纯推送留空 |
| `alerts.enabled` | true | 告警评估总开关(通道都没配时自然不发) |
| `alerts.cooldown` | 1800 | 同场地同类告警冷却(秒)；恢复通知不受限 |
| `alerts.hashrate_drop_pct` | 30 | 算力较 45~90 分钟前基线跌超此百分比→告警；0=关 |
| `alerts.offline_threshold` | 0 | 单场地掉线台数(total-online)达到此值→告警；0=关(与本地单机告警不重复) |
| `alerts.telegram / webhook / email` | 关 | 通道配置(webhook kind: wecom\|dingtalk\|raw；email 为 SMTP，任意邮箱服务商)。以上 `alerts.*` 均可在网页"⚙ 通知设置"面板改（`GET/POST /api/settings`，仅 admin），保存即时生效、存 `cloud_settings.json`，见 §2.1 |
| `server.trust_proxy` | false | **nginx 反代后必须 true**(登录锁定/限速取 XFF 真实IP)；无反代保持 false |
| `poll.interval` | 60 | 拉取模式轮询周期(秒) |
| `poll.push_interval` | 60 | 推送场地上报周期(秒)，失联阈值=此值×fail_offline，须与场地端 cloud.interval 一致 |
| `poll.customer_interval` | 600 | 拉取模式客户报表周期(秒) |
| `poll.customer_hours` | 24 | 拉取模式客户报表统计周期(小时) |
| `poll.timeout` | 10 | 拉取单场地 HTTP 超时(秒) |
| `poll.fail_offline` | 3 | 连续失败/缺报 N 个周期 → 不可达 |
| `poll.stalled_after_s` | 1200 | 场地扫描龄期超此值 → 监控停滞 |
| `db.path` | cloud_overview.db | 相对路径时相对 config.yaml 所在目录 |
| `db.retention_days` | 30 | 快照保留天数 |
| `server.host/port` | 0.0.0.0 / 8900 | 监听地址 |
| `auth.enabled` | true | 关=免登录匿名 viewer（内网演示可用，公网必须开） |
| `auth.secure_cookie` | false | 走 HTTPS 后改 true |
| `auth.users[]` | admin/viewer | `{username, password(pbkdf2哈希), role: admin\|viewer}`；`python auth.py passwd 用户名` 改密码(写 cloud_settings.json 覆盖层，立即生效) |
| `auth.allow_default_password` | false | 仅本地测试：放行出厂默认口令登录 |
| `ingest.auto_register` / `max_sites` / `rate_per_min` | true / 200 / 120 | 新场地自动注册开关 / 场地数上限 / 每IP每分钟上报次数 |
| `server.trusted_proxies` | 本机+内网网段 | 只信这些地址转发来的 X-Forwarded-For/Proto |
| `server.max_body_bytes` | 2097152 | 请求体上限，超出 413 |
| `alerts.webhook.allow_internal` / `alerts.email.allow_internal` | false | 允许通知发往本机/链路本地等内部地址(仅配置文件可改) |

环境变量 `CO_CONFIG=<路径>` 可指定配置文件（测试/多实例用），默认取同目录 config.yaml。

## 8. 前端（web/）

- 原生 JS 单页（无构建步骤）+ Chart.js 4.5.1（本地 web/vendor，不走 CDN；CSP 只允许本站脚本，前端禁止内联 script/onclick）。刷新节奏 30s 轮询 `/api/overview`
  + `/api/customers` + `/api/trend`；失败显示顶部红色失联横幅。
- **场地卡片必须事件委托**（`document` 上 `closest(".site")`）——卡片 DOM 每 30s 重建，
  直接绑元素会丢点击。卡片寻址用 `data-site-id`（稳定 id），界面只显示 name。
- **断档必须断线**（`withGaps()` + `spanGaps:false`）：相邻点间隔 > max(300s, 4×中位间隔)
  时插 null——否则断报/停电时段被直线连过去，图在说谎。
- 单位：算力千进制自动换档（`fmtHash`: 1000 TH=1 PH=1000⁻¹ EH），功率 kW→MW，
  能量 kWh→MWh→GWh；表格数字列 `tabular-nums` 对齐。
- 图表规范（换色前先跑 dataviz 校验器）：
  - 面板底色 `#161b22`；场地序列色**按 config/注册顺序固定分配**不随筛选重排：
    `#3987e5 #199e70 #c98500 #008300 #9085e9 #e66767 #d55181 #d95926`（已通过 CVD 校验）
  - 状态色（只配合图标+文字用，不单独靠颜色传义）：正常 `#0ca30c` 告警 `#fab219`
    停滞 `#ec835a` 不可达 `#d03b3b`
  - 折线 2px、无点标记、hover 十字提示（`mode:nearest axis:x`）；多序列时图例+线尾直标
    （文字用墨色 `#adbac7`，线端色块即身份）；**一图一轴**，算力/在线/功耗分三张图，
    永远不做双 Y 轴。
  - 图表高度用固定高度容器 + `maintainAspectRatio:false`（否则 Chart.js 按宽高比撑满全屏）。

## 9. 编码与平台注意（Windows 踩过的坑）

- 配置读取用**二进制 + `decode("utf-8-sig")`**，不给任何一层 locale 劫持机会。
- 进程启动即 `sys.stdout/stderr.reconfigure(encoding="utf-8")`——GBK 控制台打 emoji 会炸。
- 调试中文 API **别用** `curl | python -c "json.load(sys.stdin)"`：Windows 管道 stdin 按
  GBK 解码 UTF-8 响应，会造出"服务端乱码"的假象。用
  `json.loads(sys.stdin.buffer.read().decode("utf-8"))`。
- 重启服务后先 `Get-NetTCPConnection -LocalPort 8900` 核对监听进程的 CreationDate——
  SO_REUSEADDR 下旧进程可能还在应答，让你误以为改动没生效；venv python 是
  launcher，shim+真实解释器成对出现，杀要杀一对。

## 10. 本地开发与测试

```bash
# ① 起两个假场地(拉取模式数据源, 带慢波动方便看趋势)
python tools/mock_site.py 8801 air  tokA
python tools/mock_site.py 8802 hydro tokB

# ② 用测试配置起云端(拉取上面两个场地, 5秒一轮, 独立测试库)
CO_CONFIG=tools/test_config.yaml python server.py

# ③ 模拟一个推送场地(直接复用 E:\ip\cloud_report.py 的真实上报代码; 末参数=site_id)
python tools/push_site_sim.py http://127.0.0.1:8900 ingest-test-tok "推送测试场" hydro st-sim001

# ④ 浏览器 http://127.0.0.1:8900 (admin/admin888)；或 curl 验证：
curl -c ck.txt -X POST http://127.0.0.1:8900/api/login \
     -H "Content-Type: application/json" -d '{"username":"admin","password":"admin888"}'
curl -b ck.txt http://127.0.0.1:8900/api/overview
```

回归清单（改动后至少过一遍）：登录 401/429、overview 三场地齐全（条目带 id）、
趋势多线+断档断线、场地详情三图+客户表+日报表、杀掉 mock/推送模拟 → 对应场地
3 个周期后变"不可达"且保留最后已知数据 + **webhook 收到失联告警** → 恢复后自动回 ok
+ 收到恢复通知、同 site_id 改名重推 → 同一场地换标签数据连续、专属钥匙 401/200、
旧式无 site_id 上报仍 200、错误 ingest token 401、admin DELETE 场地、
算力骤降单测（tools 里没有，构造快照直接调 `Notifier._check_drop`）。

## 11. 约定

- 字段命名 snake_case，单位挂后缀：`hashrate_ths` / `power_kw` / `power_kwh` / `*_s` / `*_ts`。
- 时间一律 Unix 秒、云端时间为准（上报体里的 `ts` 仅供参考不入库）。
- 面板文案全中文；状态永远"图标+文字"，不只靠颜色。
- 新增查询一律只读；任何写场地/控制矿机的想法 → 放到本地运维系统去。
