# AntMonitor Cloud · 简介 / 部署 / 使用

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

多场地云端总览：各矿场本地跑 AntMonitor，摘要推到云端，老板一张图看完。

适合多场地老板、总部值班；场地在 NAT 后也能上报，无需端口映射。

配套开源场地端：https://github.com/raphael2025/antmonitor

## 部署

一行命令装好（Ubuntu/Debian/CentOS，不需要先装 git/Python），二选一：

```bash
# 有域名（推荐）：先把域名 A 记录指向服务器，安全组放行 80/443
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com

# 没有域名：用公网 IP + 自签证书，安全组放行 8900
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
```

- **有域名**：自动装 Caddy 申请并续期 HTTPS 证书，服务只监听本机 8900，外面只能走 443。
  国内服务器的域名须已 ICP 备案，否则 80/443 会被云厂商拦截——没备案就用无域名模式。
- **没有域名**：服务自己跑 HTTPS（`https://公网IP:8900`），证书是自签的。场地端按**证书指纹**认云端
  （防冒充、防中间人）；浏览器第一次打开会提示"不安全"，核对证书指纹与安装结束时显示的一致再继续。
- 安装时设置 admin 密码（直接回车=自动生成并显示一次）；上报 token、取数 token 随机生成。
- 再跑一次同样的命令 = 更新（保留配置和数据）；`… | sudo bash -s -- info` 看地址和指纹，
  `reset-password` 重设密码，`uninstall` 卸载。GitHub 慢可设 `GH_PROXY=<镜像前缀>/`。

### 场地接入（网页里完成，不用敲命令）

admin 登录 → 右上「🔌 场地接入」：

1. 看到**云端地址**、**证书指纹**（无域名时）和上报 token 的末 4 位。
2. 输入你的登录密码 → 「查看 / 接入码」复制共享接入码，或「＋ 新场地」给该场地生成**专属接入码**（推荐：一个场地泄露不影响其它场地）。
3. 场地端面板「⚙ 网段 → ☁ 云端上报」粘贴接入码 → 填入 → 场地名 → 启用 → 保存，点「🔌 测试连接」确认。
   也可以在场地端安装命令里直接带 `--connect-code`。

查看/更换/撤销 token 都要再输一次密码、写进操作记录；接入码含 token，只发给场地负责人。
所有场地都换成专属 token 后，可取消勾选「允许新场地用共享 token 自动注册」。

<details><summary>手动部署 / Docker</summary>

```bash
git clone https://github.com/raphael2025/antmonitor-cloud.git && cd antmonitor-cloud
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml && chmod 600 config.yaml
venv/bin/python auth.py token        # 生成 ingest.token / public_api.token 填进 config.yaml
venv/bin/python auth.py passwd admin
venv/bin/python server.py            # :8900；公网必须前置 HTTPS，见 DEPLOY.md
```
</details>

运维细节见 [DEPLOY](../DEPLOY.md)。

## 使用

登录后看 KPI、场地卡片、趋势、客户。右上角可切换语言。失联/停滞会标红，并可配置 Telegram 等通知。

## 联系

微信 `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)

云端 https://github.com/raphael2025/antmonitor-cloud · 场地端 https://github.com/raphael2025/antmonitor
