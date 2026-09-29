# AntMonitor Cloud · Resumen / Despliegue / Uso

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Vista cloud multi-sitio: cada granja envía un resumen desde AntMonitor local.

Para dueños y HQ. Funciona detrás de NAT sin abrir puertos.

App de sitio: https://github.com/raphael2025/antmonitor

## Despliegue

```bash
git clone https://github.com/raphael2025/antmonitor-cloud.git
cd antmonitor-cloud
pip install -r requirements.txt
cp config.example.yaml config.yaml
python auth.py passwd admin
python server.py
```

## Uso

KPI, tarjetas, tendencias, clientes. Selector de idioma arriba a la derecha.

## Contacto

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)
