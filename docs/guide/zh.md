# AntMonitor Cloud · 简介 / 部署 / 使用

**Language:** [中文](zh.md) · [English](en.md) · [Русский](ru.md) · [Español](es.md) · [Deutsch](de.md) · [العربية](ar.md)

---

多场地云端总览：各矿场本地跑 AntMonitor，摘要推到云端，老板一张图看完。

适合多场地老板、总部值班；场地在 NAT 后也能上报，无需端口映射。

云端仓库：[GitHub](https://github.com/raphael2025/antmonitor-cloud) / [Gitee](https://gitee.com/raphael01/antmonitor-cloud)

配套开源场地端：[GitHub](https://github.com/raphael2025/antmonitor) / [Gitee](https://gitee.com/raphael01/antmonitor)

## 部署

Ubuntu / Debian / CentOS 均可一键安装；无需手动克隆或提前准备 Git / Python。
先选择下载源，再按有无域名选一条命令，示例域名替换成自己的。

### GitHub 一键安装

```bash
# 有域名（推荐）：先把域名 A 记录指向服务器，安全组放行 80/443
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --source github --domain overview.example.com

# 没有域名：自动探测公网 IP，使用自签 HTTPS，安全组放行 8900
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --source github
```

### Gitee 一键安装

```bash
# 有域名（推荐）：先把域名 A 记录指向服务器，安全组放行 80/443
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- --source gitee --domain overview.example.com

# 没有域名：自动探测公网 IP，使用自签 HTTPS，安全组放行 8900
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- --source gitee
```

公网 IP 探测不准确时，可在所选命令末尾追加 `--ip 你的公网IPv4`，把占位内容换成实际地址。
Gitee 模式自动安装 Git 并从 `raphael01/antmonitor-cloud` 的 `master` 分支拉取源码；
Git 安装或拉取失败时会停止，请修复网络或系统软件源后重试。

两种下载源的部署结果相同：

- 默认目录 `/opt/antmonitor-cloud`，服务名 `antmonitor-cloud`。
- **有域名**：自动装 Caddy 申请并续期 HTTPS 证书，服务只监听本机 8900，外面通过 443 访问。
  国内服务器使用域名时，请先完成适用的 ICP 备案要求，再部署域名模式。
- **没有域名**：服务自己跑 HTTPS（`https://公网IP:8900`），使用自签证书；场地端按证书指纹验证。
  浏览器首次访问提示证书不受信任时，先核对指纹与安装结束时显示的一致。
- 安装时设置 admin 密码，直接回车会自动生成并显示一次；上报 token、取数 token 随机生成。
- 系统软件源、Python 依赖源和域名证书申请仍需联网；自定义目录在安装和后续操作中都加 `--dir '/实际安装目录'`。

### 更新与选择下载源

按希望使用的更新源选一条；安装脚本会同时更新代码、Python 依赖并重启服务：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- update --source github

# Gitee
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- update --source gitee
```

已有官方 GitHub / Gitee Git 部署可以通过 `--source` 切换 `origin`，网页更新随后使用所选仓库。
不指定 `--source` 时保留现有 `origin`；自维护仓库或 fork 不会被自动改写，使用这类仓库时请省略该选项。
两平台各自能更新到哪个版本，取决于发布者是否同步了相应分支。

默认脚本安装可核对更新来源：

```bash
sudo -u antcloud git -C /opt/antmonitor-cloud remote get-url origin
```

查看地址、证书指纹和服务状态，或重设密码，可使用已安装的脚本：

```bash
sudo bash /opt/antmonitor-cloud/install.sh info
sudo bash /opt/antmonitor-cloud/install.sh reset-password
```

已有部署的详细操作见 [DEPLOY · 已有安装切换到 Gitee](../DEPLOY.md#已有安装切换到-gitee)，
两平台同步说明见 [DEPLOY · GitHub 与 Gitee 同步](../DEPLOY.md#github-与-gitee-同步)。

### 场地接入（网页里完成，不用敲命令）

admin 登录 → 右上「🔌 场地接入」：

1. 看到**云端地址**、**证书指纹**（无域名时）和上报 token 的末 4 位。
2. 输入你的登录密码 → 「查看 / 接入码」复制共享接入码，或「＋ 新场地」给该场地生成**专属接入码**（推荐：一个场地泄露不影响其它场地）。
3. 场地端面板「⚙ 网段 → ☁ 云端上报」粘贴接入码 → 填入 → 场地名 → 启用 → 保存，点「🔌 测试连接」确认。
   也可以在场地端安装命令里直接带 `--connect-code`。

查看/更换/撤销 token 都要再输一次密码、写进操作记录；接入码含 token，只发给场地负责人。
所有场地都换成专属 token 后，可取消勾选「允许新场地用共享 token 自动注册」。

<details><summary>手动部署 / Docker</summary>

源码也可从 `https://gitee.com/raphael01/antmonitor-cloud.git` 克隆，下面的 GitHub/Gitee 地址任选一个。

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

云端：[GitHub](https://github.com/raphael2025/antmonitor-cloud) / [Gitee](https://gitee.com/raphael01/antmonitor-cloud) · 场地端：[GitHub](https://github.com/raphael2025/antmonitor) / [Gitee](https://gitee.com/raphael01/antmonitor)
