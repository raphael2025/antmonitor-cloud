# AntMonitor Cloud · Overview / Deploy / Use

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Multi-site cloud overview: each farm runs AntMonitor locally and pushes a small summary here.

For owners and HQ ops. Works behind NAT — no port mapping on the farm.

Site app (open source): https://github.com/raphael2025/antmonitor

## Deploy

```bash
git clone https://github.com/raphael2025/antmonitor-cloud.git
cd antmonitor-cloud
pip install -r requirements.txt
cp config.example.yaml config.yaml
python auth.py passwd admin
python server.py   # :8900 — use HTTPS on the public internet
```

On each site, enable cloud reporting and enter this URL + token. Sites appear after the first push.

## Use

KPI, site cards, trends, customers. Language switcher top-right. Outage / stall alerts; Telegram etc. optional.

## Contact

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)

Cloud https://github.com/raphael2025/antmonitor-cloud · Site https://github.com/raphael2025/antmonitor
