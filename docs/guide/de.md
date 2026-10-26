# AntMonitor Cloud · Überblick / Setup / Nutzung

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Cloud-Übersicht vieler Standorte: lokales AntMonitor sendet Kurzdaten hierher.

Für Betreiber und Zentrale. Funktioniert hinter NAT ohne Portfreigabe.

Standort-App: https://github.com/raphael2025/antmonitor

## Setup

In einer Zeile (Ubuntu/Debian/CentOS):

```bash
# mit Domain (empfohlen; A-Record auf den Server, 80/443 öffnen)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com
# ohne Domain (öffentliche IP + selbstsigniertes Zertifikat, 8900 öffnen)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
```

Mit Domain holt und erneuert Caddy das HTTPS-Zertifikat; ohne Domain läuft der Dienst unter `https://IP:8900` und die
Standorte prüfen den **Zertifikat-Fingerabdruck**. Erneut ausführen = Update; `… -s -- info` zeigt URL und Fingerabdruck.

**Standorte im Browser anbinden:** admin → „🔌 Standorte anbinden“ → Passwort → Verbindungscode kopieren
(gemeinsam oder „＋ Neuer Standort“) → im Standort-Panel (☁ Cloud-Meldung) einfügen → Übernehmen → speichern.
Anzeigen/Ändern von Tokens verlangt das Passwort und wird protokolliert. Mehr: [DEPLOY](../DEPLOY.md).

## Nutzung

KPI, Karten, Trends, Kunden. Sprache oben rechts umschalten.

## Kontakt

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)
