# AntMonitor Cloud · Überblick / Setup / Nutzung

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Cloud-Übersicht vieler Standorte: lokales AntMonitor sendet Kurzdaten hierher.

Für Betreiber und Zentrale. Funktioniert hinter NAT ohne Portfreigabe.

Standort-App: https://github.com/raphael2025/antmonitor

## Setup

```bash
git clone https://github.com/raphael2025/antmonitor-cloud.git
cd antmonitor-cloud
pip install -r requirements.txt
cp config.example.yaml config.yaml
python auth.py passwd admin
python server.py
```

## Nutzung

KPI, Karten, Trends, Kunden. Sprache oben rechts umschalten.

## Kontakt

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)
