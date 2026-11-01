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

### 场地接入（名字和类型只填一次）

1. admin 登录云端，打开右上 **「🔌 场地接入」**。
2. 在 **「添加场地」** 填名称、选择风冷/水冷/混合，输入当前管理员密码，点 **「生成专属接入码」**。
3. 场地端已安装：打开 **「☁ 连接云端」**，粘贴接入码，点 **「连接云端」**。名称、类型、地址、证书指纹和场地 ID 自动填好。
4. 等待云端收到首次上报。列表先显示填写的名称和「等待首次上报」；收到数据后完成接入。单独测试连接不会注册场地。

场地电脑还没安装时，云端结果区提供 **GitHub / Gitee** 安装命令复制按钮。Ubuntu / Linux 选择一条运行，提示时粘贴接入码；
也可直接使用下面的命令，接入码隐藏输入，不会写入命令历史：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor/main/install.sh | sudo bash -s -- --source github --connect-code -

# Gitee
curl -fsSL https://gitee.com/raphael01/antmonitor/raw/main/install.sh | sudo bash -s -- --source gitee --connect-code -
```

Windows 在结果区选择 Windows，复制安装命令到场地电脑 PowerShell 运行，装好后在网页粘贴码并「连接云端」。
Gitee 的 Windows 安装需要先安装 Git for Windows 并加入 PATH。

一键部署已填好云端地址和指纹；需要修改时展开 **「高级管理」**，先「保存地址」，再生成码。
共享 token 与自动注册开关也在高级管理，正常新增场地不用修改它们。旧接入码继续有效，缺少名称/类型时在场地端补填。

名称和类型会随专属凭据一起保存，重启或以后查看/更换接入码不会丢失。查看、更换、撤销仍需当前管理员密码并记入操作记录。
接入码只发给对应场地负责人，别放进命令或链接；**显示 2 分钟后隐藏不等于凭据失效**。一个新场地使用一份新专属码，不复用其它场地的码。

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

### 日间与夜间主题

顶部和登录页都可选择「跟随系统」「按时间自动」「日间」「夜间」，浏览器会记住选择。
「按时间自动」按打开页面设备的本地时间切换：07:00（含）至 19:00（不含）使用日间主题，其余时间使用夜间主题。
不用修改服务器时间或配置。

登录后看 KPI、场地卡片、趋势、客户。右上角可切换语言。失联/停滞会标红，并可配置 Telegram 等通知。

### 客户算力怎么看

客户汇总、按场地明细和场地弹窗都分别显示**实时算力**与**近 24 小时平均算力**。
实时值是各台机器最新实测算力的合计；平均值按记录持续时间计算，确认离线的时段计为零。
一台机器全天稳定 293 TH/s，平均仍为 293 TH/s；100 台均如此，客户总平均为 29.3 PH/s。
一台机器只有 12 小时运行在 293 TH/s，另 12 小时确认离线，全天平均为 146.5 TH/s。
`台数 × 标称算力` 是理论值，实际波动、降频和停机都会改变结果。

累计交付单位 **TH·h** 与平均算力不同：293 TH/s 持续一天累计 7,032 TH·h，但平均仍为 293 TH/s。
原累计字段继续保留，周期累计和 CSV 对账在场地端查看。显示换算按千进制：1 PH/s = 1,000 TH/s，1 EH/s = 1,000 PH/s。

监控新装、历史不足或中途断档时，页面会标出覆盖不足；不能把未观测时段当成机器停机。
若场地配置了其他统计周期，页面会显示实际周期；跨场地只有可比较的周期才合成平均值。
缺失指标显示未知，实时数据过期会提示，不能把旧的在线值当成现在仍在运行。
场地矿机列表中由固件提供的「平均算力」，也不保证是软件统计的近 24 小时平均。

请同时更新场地端与云端；旧场地端没有上报新指标时，云端不会用累计值伪造实时算力或填零。
默认实时客户数据随摘要每 60 秒上报，平均报表每 600 秒更新；观察时留意采样时间。

## 联系

微信 `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)

云端：[GitHub](https://github.com/raphael2025/antmonitor-cloud) / [Gitee](https://gitee.com/raphael01/antmonitor-cloud) · 场地端：[GitHub](https://github.com/raphael2025/antmonitor) / [Gitee](https://gitee.com/raphael01/antmonitor)
