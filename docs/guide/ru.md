# AntMonitor Cloud · Обзор / Установка / Использование

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Облачный обзор площадок: локальный AntMonitor шлёт краткую сводку сюда.

Для владельцев и штаба. Работает за NAT без проброса портов.

Локальное приложение: https://github.com/raphael2025/antmonitor

## Установка

Одной командой (Ubuntu/Debian/CentOS):

```bash
# с доменом (рекомендуется; A-запись на сервер, открыть 80/443)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com
# без домена (публичный IP + самоподписанный сертификат, открыть 8900)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
```

С доменом Caddy сам получает HTTPS-сертификат; без домена сервис работает по `https://IP:8900`, а площадки
проверяют **отпечаток сертификата**. Повторный запуск = обновление; `… -s -- info` показывает адрес и отпечаток.

**Подключение площадок — в браузере:** admin → «🔌 Подключение площадок» → введите пароль → скопируйте
код подключения (общий или «＋ Новая площадка») → вставьте его в панели площадки (☁ Отправка в облако) → «Применить» → сохранить.
Просмотр и смена токенов требуют пароль и пишутся в журнал. Подробнее: [DEPLOY](../DEPLOY.md).

## Использование

KPI, карточки, тренды, клиенты. Язык — справа вверху.

## Контакты

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)
