# AntMonitor Cloud · Resumen / Despliegue / Uso

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Vista cloud multi-sitio: cada granja envía un resumen desde AntMonitor local.

Para dueños y HQ. Funciona detrás de NAT sin abrir puertos.

App de sitio: https://github.com/raphael2025/antmonitor

## Despliegue

En una línea (Ubuntu/Debian/CentOS):

```bash
# con dominio (recomendado; registro A al servidor, abrir 80/443)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com
# sin dominio (IP pública + certificado autofirmado, abrir 8900)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
```

Con dominio, Caddy obtiene y renueva el certificado HTTPS; sin dominio el servicio usa `https://IP:8900` y los sitios
verifican la **huella del certificado**. Repetir el comando = actualizar; `… -s -- info` muestra URL y huella.

**Alta de sitios en el navegador:** admin → «🔌 Alta de sitios» → contraseña → copiar el código de conexión
(compartido o «＋ Nuevo sitio») → pegarlo en el panel del sitio (☁ Envío a la nube) → Aplicar → guardar.
Ver o cambiar tokens pide la contraseña y queda auditado. Más en [DEPLOY](../DEPLOY.md).

## Uso

KPI, tarjetas, tendencias, clientes. Selector de idioma arriba a la derecha.

## Contacto

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)
