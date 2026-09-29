# AntMonitor Cloud · 简介 / 部署 / 使用

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

多场地云端总览：各矿场本地跑 AntMonitor，摘要推到云端，老板一张图看完。

适合多场地老板、总部值班；场地在 NAT 后也能上报，无需端口映射。

配套开源场地端：https://github.com/raphael2025/antmonitor

## 部署

```bash
git clone https://github.com/raphael2025/antmonitor-cloud.git
cd antmonitor-cloud
pip install -r requirements.txt
cp config.example.yaml config.yaml
python auth.py passwd admin
python server.py   # :8900，公网请加 HTTPS
```

在各场地面板「网段/云端」打开上报，填本地址与 token。首次推送后自动出现。

## 使用

登录后看 KPI、场地卡片、趋势、客户。右上角可切换语言。失联/停滞会标红，并可配置 Telegram 等通知。

## 联系

微信 `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)

云端 https://github.com/raphael2025/antmonitor-cloud · 场地端 https://github.com/raphael2025/antmonitor
