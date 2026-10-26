# AntMonitor Cloud · نظرة / تثبيت / استخدام

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

نظرة سحابية متعددة المواقع: يرسل AntMonitor المحلي ملخصًا إلى هنا.

لأصحاب المزارع والمقر. يعمل خلف NAT دون فتح منافذ.

تطبيق الموقع: https://github.com/raphael2025/antmonitor

## التثبيت

بسطر واحد (Ubuntu/Debian/CentOS):

```bash
# مع نطاق (موصى به؛ سجل A يشير إلى الخادم، افتح 80/443)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com
# دون نطاق (IP عام + شهادة موقعة ذاتيًا، افتح 8900)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
```

مع النطاق يحصل Caddy على شهادة HTTPS ويجددها؛ دون نطاق تعمل الخدمة على `https://IP:8900` وتتحقق المواقع من
**بصمة الشهادة**. إعادة الأمر = تحديث؛ `… -s -- info` يعرض العنوان والبصمة.

**ربط المواقع من المتصفح:** admin ← «🔌 ربط المواقع» ← كلمة المرور ← انسخ رمز الربط (مشترك أو «＋ موقع جديد»)
← الصقه في لوحة الموقع (☁ الإرسال للسحابة) ← تطبيق ← حفظ. عرض الرموز أو تغييرها يتطلب كلمة المرور ويُسجَّل.
التفاصيل: [DEPLOY](../DEPLOY.md).

## الاستخدام

مؤشرات، بطاقات، اتجاهات، عملاء. بدّل اللغة أعلى اليمين.

## تواصل

WeChat `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)
