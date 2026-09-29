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

[☁ 云端](https://github.com/raphael2025/antmonitor-cloud)
·
[🌐 场地端](https://github.com/raphael2025/antmonitor)
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

```bash
git clone https://github.com/raphael2025/antmonitor-cloud.git
cd antmonitor-cloud
pip install -r requirements.txt
cp config.example.yaml config.yaml
python auth.py passwd admin    # 设强密码
python server.py               # 默认 :8900，公网请挂 HTTPS
```

然后在各场地 AntMonitor 设置里打开「云端上报」，填本服务地址与 token。  
第一次推送后，场地会自动出现在面板上。

完整「简介 · 部署 · 使用」→ [选语言阅读](docs/guide/README.md)

运维细节（nginx、通知渠道等）见 [DEPLOY](docs/DEPLOY.md)。

---

## 产品与仓库

| | 开源地址 | 做什么 |
|:---|:---|:---|
| **云端** | [antmonitor-cloud](https://github.com/raphael2025/antmonitor-cloud) | 多场地总览（本仓库） |
| **场地端** | [antmonitor](https://github.com/raphael2025/antmonitor) | 局域网监控、告警、批量运维 |

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
| **云端** | https://github.com/raphael2025/antmonitor-cloud |
| **场地端** | https://github.com/raphael2025/antmonitor |

---

<div align="center">

<sub>AntMonitor Cloud · Open source · See every site at a glance</sub>

</div>
