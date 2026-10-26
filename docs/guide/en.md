# AntMonitor Cloud · Overview / Deploy / Use

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Multi-site cloud overview: each farm runs AntMonitor locally and pushes a small summary here.

For owners and HQ ops. Works behind NAT — no port mapping on the farm.

Site app (open source): https://github.com/raphael2025/antmonitor

## Deploy

One line (Ubuntu/Debian/CentOS; no git/Python needed first). Pick one:

```bash
# With a domain (recommended): point its A record here, open 80/443
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com

# Without a domain: public IP + self-signed certificate, open 8900
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
```

- **Domain:** Caddy obtains and renews the HTTPS certificate; the app listens on 127.0.0.1:8900 only.
  (Mainland-China servers need ICP filing for a domain; otherwise use the no-domain mode.)
- **No domain:** the app serves HTTPS itself at `https://<public IP>:8900` with a self-signed certificate.
  Sites pin its **SHA-256 fingerprint** (stops impersonation/MITM). When the browser warns, compare the fingerprint shown at the end of the install.
- You set the admin password during install (Enter = generate one, shown once); reporting/API tokens are random.
- Run the same command again to update (config and data kept). `… | sudo bash -s -- info` shows URL and fingerprint;
  `reset-password`, `uninstall` are also available. Slow GitHub: `GH_PROXY=<mirror prefix>/`.

**Onboarding a site (in the browser):** log in as admin → **🔌 Site onboarding**: see the cloud URL, the certificate
fingerprint and the last 4 chars of each token. Enter your password, then **Show / code** (shared) or **＋ New site**
(a per-site code — recommended). In the site panel: ⚙ Segments → ☁ Cloud reporting → paste the code → Apply → name →
enable → save → 🔌 Test connection. Viewing or rotating a token needs your password and is audited; a connect code
contains the token — give it only to the site owner.

Manual / Docker install and operations: [DEPLOY](../DEPLOY.md).

## Use

KPI, site cards, trends, customers. Language switcher top-right. Outage / stall alerts; Telegram etc. optional.

## Contact

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)

Cloud https://github.com/raphael2025/antmonitor-cloud · Site https://github.com/raphael2025/antmonitor
