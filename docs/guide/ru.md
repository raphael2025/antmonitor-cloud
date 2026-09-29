# AntMonitor Cloud · Обзор / Установка / Использование

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

Облачный обзор площадок: локальный AntMonitor шлёт краткую сводку сюда.

Для владельцев и штаба. Работает за NAT без проброса портов.

Локальное приложение: https://github.com/raphael2025/antmonitor

## Установка

```bash
git clone https://github.com/raphael2025/antmonitor-cloud.git
cd antmonitor-cloud
pip install -r requirements.txt
cp config.example.yaml config.yaml
python auth.py passwd admin
python server.py
```

На площадке включите cloud report и укажите URL + token.

## Использование

KPI, карточки, тренды, клиенты. Язык — справа вверху.

## Контакты

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)
