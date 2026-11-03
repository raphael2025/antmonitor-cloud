<div align="center">

# AntMonitor Cloud

### 多场地云端总览

**一台云服务器 · 看清所有矿场**

各场地本地跑 [AntMonitor](https://github.com/raphael2025/antmonitor)，  
每分钟把摘要推上来 — 你在手机或电脑上扫一眼全网。

<br/>

[中文](docs/guide/zh.md) ·
[English](docs/guide/en.md) ·
[Русский](docs/guide/ru.md) ·
[Español](docs/guide/es.md) ·
[Deutsch](docs/guide/de.md) ·
[العربية](docs/guide/ar.md)

<br/>

[☁ 云端 GitHub](https://github.com/raphael2025/antmonitor-cloud) / [Gitee](https://gitee.com/raphael01/antmonitor-cloud)
·
[🌐 场地端 GitHub](https://github.com/raphael2025/antmonitor) / [Gitee](https://gitee.com/raphael01/antmonitor)
·
[💬 Telegram](https://t.me/+W3J9yAypNgpjNTk9)
·
微信 `raphael-2024`

</div>

---

## 适合谁

| | |
|:---|:---|
| **多场地老板 / 总部** | 内蒙、新疆、海外……一张图看完 |
| **值班经理** | 哪场失联、哪场告警，不用钻进每台监控机 |
| **已部署场地端** | 打开云端上报即可，场地在 NAT 后也不用端口映射 |

本地继续做运维与存历史；云端只做**总览**，轻、稳、好打开。

---

## 你能看到什么

- **全网 KPI** — 场地在线、矿机在线率、总算力、功耗、告警、客户  
- **场地卡片** — 风冷 / 水冷分组；点开看趋势与该场客户  
- **算力对比** — 各场曲线，近几小时到一周  
- **失联发现** — 整场断电时本地监控也挂了，只有云端能发现  
- **面板多语言** — 中 / 英 / 俄 / 西 / 德 / 阿  

开源可自建。和 [场地端](https://github.com/raphael2025/antmonitor) 配套使用。

---

## 三分钟上手

Ubuntu / Debian / CentOS 均可一键部署，无需先克隆仓库或预装 Git / Python。
选择可访问的下载源，再按有无域名选一条命令；示例域名请替换成自己的。

### GitHub 一键安装

```bash
# 有域名（推荐）：域名 A 记录指向服务器，安全组放行 80/443
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --source github --domain overview.example.com

# 没有域名：自动探测公网 IP，使用自签 HTTPS，安全组放行 8900
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --source github
```

### Gitee 一键安装

```bash
# 有域名（推荐）：域名 A 记录指向服务器，安全组放行 80/443
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- --source gitee --domain overview.example.com

# 没有域名：自动探测公网 IP，使用自签 HTTPS，安全组放行 8900
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- --source gitee
```

公网 IP 探测不准确时，在命令末尾追加 `--ip 你的公网IPv4`，将占位内容换成实际地址。
Gitee 命令自动安装 Git 并从 Gitee 拉取源码。默认安装到 `/opt/antmonitor-cloud`；安装时设置 admin 密码，
直接回车会生成强密码并显示一次。结束时显示面板地址和无域名模式的证书指纹。

GitHub / Gitee 是代码下载来源，无需注册账号或安装它们的客户端。下载命令需要 `curl`；Ubuntu 没有时先运行
`sudo apt-get update && sudo apt-get install -y curl ca-certificates`。

<details><summary>自动判断 GitHub / Gitee，或者两边都打不开</summary>

不确定哪个来源可访问时，复制下面的一条命令；先完整下载脚本，再执行一次。脚本内的 `auto` 也会尝试可用的源码来源。
有域名时在末尾 `--source auto` 后追加 `--domain 你的域名`。

```bash
( f=$(mktemp) && trap 'rm -f "$f"' EXIT && { curl -fsSL --connect-timeout 10 --max-time 60 https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh -o "$f" || curl -fsSL --connect-timeout 10 --max-time 60 https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh -o "$f"; } && sudo bash "$f" --source auto )
```

两边都不通时，可以把完整源码 ZIP 拷到服务器，解压并进入目录执行 `sudo bash install.sh --dir /opt/antmonitor-cloud`，不要指定 `--source`。
系统包、Python 依赖和 HTTPS 所需资源仍需可用网络或事先准备。Python wheels 可放到项目 `offline/wheels`，或通过
`ANTCLOUD_WHEELHOUSE` 指定目录；这会禁用在线 pip 索引，不代表已包含所有系统依赖。详见[本地源码部署](docs/DEPLOY.md#本地源码与受限网络)。

</details>

### 后续更新与换源

更新代码、Python 依赖并重启，按希望使用的来源选一条：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- update --source github

# Gitee
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- update --source gitee
```

已有官方 Git 部署可以用 `--source github` / `--source gitee` 切换来源；不指定或 `--source auto` 时保留现有 `origin`。
自动模式遇到官方来源 fetch 网络失败时，会尝试另一平台的同分支并仅接受快进更新，不回退到旧镜像；自定义仓库不会被切换到官方仓库。
自定义安装目录需在安装和更新时都补 `--dir '/实际安装目录'`。
更新并重启后，正常刷新网页即可加载新版按钮和语言文件；资源版本自动生成，ZIP 安装也适用。
若本地端已更新、场地卡片有数字而顶部汇总为空，本版修复了合法的小数秒采集时间被误判无效的问题。只需更新并重启云端，等待下一轮有效上报；本地端无需再次安装。
系统软件、Python 依赖和域名证书仍需联网获取；两平台的版本同步与运维细节见[部署与运维](docs/DEPLOY.md)。

### 接入场地

**云端复制一次，本地粘贴一次，不用拆开填写地址和 token。**

1. admin 登录云端 →「🔌 场地接入」→ 填场地名称、类型和当前管理员密码 → **「生成并复制完整接入信息」**。
2. 本地端打开「☁ 连接云端」→ **「粘贴接入信息并连接」**。局域网 HTTP 页面若提示不能自动读剪贴板，直接按 **Ctrl+V**，程序会继续验证和保存。
3. 看到最近上报成功时间，并在云端看到该场地，即完成接入。尚未完成首轮扫描时，先在本地设置矿机网段并扫描。

复制的是同一份 `AMC1-…` 接入信息，已包含上报地址、token、证书指纹（需要时）、场地 ID、名称和类型；**网页和命令界面通用**。
`AMC1` 后面是 Base64URL **编码，不是加密**。编码方便整段复制，持有整段内容的人可以解出 token，因此整段都应作为上报凭据保管。
Ubuntu 首次安装时按提示粘贴；已安装后在安装目录运行 `sudo bash install.sh connect --dir .`。
Windows 已安装后也可以双击 `connect-cloud.bat`，按提示粘贴；原监控仍在运行时，保存后按提示重启原窗口即可生效。

场地端尚未安装时，展开结果区的安装说明，复制 **GitHub / Gitee** 安装命令。安装器会准备 Git / Python，无需用户先克隆仓库。
已有场地再次复制用 **「复制接入信息」**，不会更换正在使用的 token；旧接入码仍兼容，缺少名称或类型时再补填。

云端正常 HTTPS 地址可直接生成；经 SSH 隧道或 localhost 访问时，需要在「高级管理」先保存场地能够访问的地址。
接入信息只交给对应场地负责人，**不要写进安装命令**。2 分钟后隐藏只清空页面显示，凭据不会过期。

**清理旧测试场地**：在「场地接入 → 已分配的场地」找到对应 **ID**，点击「删除场地」。尚未上报、已经撤销密钥的场地也能删除；同名但不同 ID 的场地不受影响。
「撤销密钥」会停用上报并保留场地记录；「删除场地」会从接入列表移除记录、停用旧接入信息，并永久删除该 ID 的历史数据。误删可在顶部「已删除场地」中恢复，然后回到「场地接入」生成新的接入信息并粘贴到本地端；旧码和已删除的历史不会恢复。
详细步骤见[场地接入](docs/DEPLOY.md#4-场地接入每个场地一次)。

完整「简介 · 部署 · 使用」→ [选语言阅读](docs/guide/README.md)

### 日间与夜间主题

顶部和登录页可选择「跟随系统」「按时间自动」「日间」「夜间」，浏览器会记住选择。
「按时间自动」使用打开页面设备的本地时间：07:00（含）至 19:00（不含）为日间，其余为夜间；无需配置服务器。

运维细节（nginx、通知渠道等）见 [DEPLOY](docs/DEPLOY.md)。

### 客户实时算力与 24 小时平均

客户数按**当前机器所属的第一配置矿池账户**统计；备用池使用不同矿工名、主备切换，不会新增客户。
场地卡片按场地去重，全网客户数按账户名跨场地去重。机器暂时离线或算力为零，不代表客户已离场。
旧备用名或已离场账户在统计期内仍有记录时，报表保留并标为「历史记录，不计入当前客户数」，不自动改写历史算力或耗电。
需要同时更新并重启场地端和云端；场地完成一轮巡检并成功上报后，当前客户数即可刷新，无需等满 24 小时。

客户表分别显示**实时算力**和**近 24 小时平均算力**，单位为 TH/s、PH/s、EH/s；同一客户的跨场地实时合计需要各场地读数新鲜有效，周期平均还需要实际起止窗口一致、覆盖完整。
一台机器全天稳定 293 TH/s，24 小时平均仍是 293 TH/s；100 台均如此，客户合计就是 29.3 PH/s。
实际统计包含运行波动和确认离线时段，不能用标称算力乘台数代替。

旧版的 `EH·h`／`TH·h` 表示累计交付，属于时间积分，不是平均算力；原累计字段与场地 CSV 仍保留。
历史覆盖不足时，单场地仍显示已有的有效时段平均，同时标注「按有效时段计算」与覆盖时长，例如 `90.88 PH/s / 有效数据 23.26/24h`；这不是完整一天的平均。
跨场地覆盖不足或统计周期不同时，不拼出合计平均。未上报、缺测和数据过期会分别提示。
如果场地总算力正常，但客户表显示「本地未上报」，请检查对应**场地端是否已更新并重启**，不能只更新云端或下载源码后继续运行旧进程。
保留的原始扫描记录会自动用于计算近 24 小时平均，无需一律再等满 24 小时；缺失的历史仍如实标注。
默认客户实时数据随摘要每 60 秒更新，平均报表每 600 秒更新。详细口径见[中文指南 · 客户算力怎么看](docs/guide/zh.md#客户算力怎么看)。

本轮[数据逻辑审计](docs/DATA_LOGIC_AUDIT.md)覆盖采集、实时汇总、客户积分、跨场地合计、日报和通知。
算力、功率、机器状态各自检查覆盖；总览主数值显示有效合计，部分覆盖时标注「已统计 x/y 个场地」或「部分读数」。场地功耗缺少部分机器时显示已有有效功耗，并标注矿机覆盖台数。
网页当前功率固定用 **kW**，统计期累计耗电固定用 **kWh（度）**。功率总览来自矿机读数合计；AntBox 配电读数单独展示，不重复计入，也不等同于全场电表负荷。
客户窗口按共同的十分钟边界查询；日报按 **UTC+8** 切日，今天按已经经过的时间计算覆盖。旧日报和旧账本保留为明确标注的对照记录。

趋势中的实线表示完整读数，部分实测以虚线/点标明，悬停可查看覆盖情况。算力、功率分别判断有效性，
缺功率不等于缺算力；部分合计不代表完整场地总量。真正未采到有效数据的时段仍留空，零值则正常显示为零。
因此曲线空档不能单独作为停机证据，须结合对应时段的扫描和上报日志核对。图表不会拿旧值或插值补齐缺口。

云端主按钮继续复制 **AMC1 完整接入包**（地址、token、场地信息），并从实际包解析预览目标地址和场地；本地网页或命令界面均可整段粘贴，单独 token 只用于高级手动配置。
升级请**先更新并重启云端，再更新并重启场地端**，确认系统时间正确；延迟上报、时钟回拨和异常未来时间不会被自动“校正”为刚采集。
耗电是监控估算，不能替代电表。具体修复反例、API 变化、测试命令和现场核验限制见审计文档。

### 运维功能与通知可靠性

[运维与功能逻辑审查](docs/OPERATIONS_LOGIC_REVIEW.md)说明各项功能的现场用途与边界。
本次修复持续低算力误报恢复、通知失败后丢失、机器人业务拒绝却显示成功的问题；
失败通道会退避重试，已成功通道不重复补发，场地恢复后取消旧故障消息。
通知队列保存在运行进程中，不能当作跨重启的可靠消息服务。云端 viewer 是全网只读角色，不是客户隔离账号。

---

## 产品与仓库

| | GitHub | Gitee | 做什么 |
|:---|:---|:---|:---|
| **云端** | [antmonitor-cloud](https://github.com/raphael2025/antmonitor-cloud) | [antmonitor-cloud](https://gitee.com/raphael01/antmonitor-cloud) | 多场地总览（本仓库） |
| **场地端** | [antmonitor](https://github.com/raphael2025/antmonitor) | [antmonitor](https://gitee.com/raphael01/antmonitor) | 局域网监控、告警、批量运维 |

---

## 持续更新

与场地端一起迭代。欢迎提意见、提需求 — 多客户管理、更聪明的告警、Agent / MCP 等已在路线图上。

---

## 联系与定制

多场地接入、通知通道、权限与报表、私有化部署：

| | |
|:---|:---|
| **微信** | 搜索 **`raphael-2024`** |
| **Telegram** | [加入群组](https://t.me/+W3J9yAypNgpjNTk9) |
| **云端** | [GitHub](https://github.com/raphael2025/antmonitor-cloud) / [Gitee](https://gitee.com/raphael01/antmonitor-cloud) |
| **场地端** | [GitHub](https://github.com/raphael2025/antmonitor) / [Gitee](https://gitee.com/raphael01/antmonitor) |

---

<div align="center">

<sub>AntMonitor Cloud · Open source · See every site at a glance</sub>

</div>
