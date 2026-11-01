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

### 后续更新与换源

更新代码、Python 依赖并重启，按希望使用的来源选一条：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- update --source github

# Gitee
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- update --source gitee
```

已有官方 Git 部署可以用 `--source` 切换来源；不指定时沿用现有 `origin`，自定义仓库不会被自动改写。
自定义安装目录需在安装和更新时都补 `--dir '/实际安装目录'`。
系统软件、Python 依赖和域名证书仍需联网获取；两平台的版本同步与运维细节见[部署与运维](docs/DEPLOY.md)。

### 接入场地

**名称和类型只填一次**：admin 登录 → 右上「🔌 场地接入」→ 在「添加场地」填写名称、类型和当前管理员密码 → **生成专属接入码**。

- 场地端已安装：在「☁ 连接云端」粘贴接入码，点 **「连接云端」**。名称、类型、云端地址、证书指纹和场地 ID 自动填好。
- 场地端尚未安装：页面提供 **GitHub / Gitee** 两个安装命令复制按钮。在场地电脑运行其中一条；Ubuntu / Linux 提示时粘贴接入码，Windows 安装后在网页粘贴。Windows 的 Gitee 安装需先准备 Git for Windows。
- 云端收到首次上报后，场地才从「等待首次上报」变成已接入。已有旧接入码仍可用；旧码没有名称/类型时，在场地端补填即可。

地址、证书和共享 token 收在「高级管理」；修改地址要先保存，再生成码。接入码只交给对应场地负责人，**不要把它写进安装命令**。
2 分钟后自动隐藏只是清空页面显示，凭据不会过期；以后仍可在对应场地行查看或更换，保存的名称和类型不会丢失。详细步骤见[场地接入](docs/DEPLOY.md#4-场地接入每个场地一次)。

完整「简介 · 部署 · 使用」→ [选语言阅读](docs/guide/README.md)

### 日间与夜间主题

顶部和登录页可选择「跟随系统」「按时间自动」「日间」「夜间」，浏览器会记住选择。
「按时间自动」使用打开页面设备的本地时间：07:00（含）至 19:00（不含）为日间，其余为夜间；无需配置服务器。

运维细节（nginx、通知渠道等）见 [DEPLOY](docs/DEPLOY.md)。

### 客户实时算力与 24 小时平均

客户表分别显示**实时算力**和**近 24 小时平均算力**，单位为 TH/s、PH/s、EH/s；同一客户跨场地的算力按实际值相加。
一台机器全天稳定 293 TH/s，24 小时平均仍是 293 TH/s；100 台均如此，客户合计就是 29.3 PH/s。
实际统计包含运行波动和确认离线时段，不能用标称算力乘台数代替。

旧版的 `EH·h`／`TH·h` 表示累计交付，属于时间积分，不是平均算力；原累计字段与场地 CSV 仍保留。
历史覆盖不足、不同统计周期和过期数据会分别提示；旧场地端缺少的新指标显示未知，请同时更新场地端和云端。
默认客户实时数据随摘要每 60 秒更新，平均报表每 600 秒更新。详细口径见[中文指南 · 客户算力怎么看](docs/guide/zh.md#客户算力怎么看)。

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
