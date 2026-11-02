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

GitHub / Gitee 是代码下载网站，不需要注册账号或安装专门的客户端；缺少的 Git / Python 由安装器准备。
Ubuntu 的命令下载入口需要 `curl`，没有时先执行 `sudo apt-get update && sudo apt-get install -y curl ca-certificates`。
不确定哪个网站可用时，见 [README 的自动选择入口](../../README.md#三分钟上手)：先完整下载脚本，再执行一次，源码也会自动选源。
两网站均无法访问时，可拷入完整源码，解压并进入目录执行 `sudo bash install.sh --dir /opt/antmonitor-cloud`，不加 `--source`。
系统包、Python 包和 HTTPS 所需网络仍须准备；本地 wheels 与限制见 [DEPLOY · 本地源码与受限网络](../DEPLOY.md#本地源码与受限网络)。

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
不指定 `--source` 或选择 `auto` 时保留现有 `origin`。自动模式在官方来源网络 fetch 失败时可临时尝试另一平台同分支，只接受快进，不回退到旧镜像版本。
自维护仓库或 fork 不会被自动换成官方来源，使用这类仓库时请省略该选项。
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
2. 在 **「添加场地」** 填名称、选择风冷/水冷/混合，输入当前管理员密码，点 **「生成并复制完整接入信息」**。
3. 场地端已安装：打开 **「☁ 连接云端」**，点 **「粘贴接入信息并连接」**。局域网 HTTP 页面如果不能自动读取剪贴板，按提示 **Ctrl+V** 即继续连接。名称、类型、地址、token、证书指纹和场地 ID 自动填好。
4. 等待云端收到首次上报。列表先显示填写的名称和「等待首次上报」；收到数据后完成接入。单独测试连接不会注册场地。

场地电脑还没安装时，云端结果区提供 **GitHub / Gitee** 安装命令复制按钮。Ubuntu / Linux 选择一条运行，提示时粘贴接入码；
也可直接使用下面的命令，接入码隐藏输入，不会写入命令历史：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor/main/install.sh | sudo bash -s -- --source github --connect-code -

# Gitee
curl -fsSL https://gitee.com/raphael01/antmonitor/raw/main/install.sh | sudo bash -s -- --source gitee --connect-code -
```

Windows 在结果区选择 Windows，复制安装命令到场地电脑 PowerShell 运行，装好后在网页点「粘贴接入信息并连接」。
GitHub 和 Gitee 安装器都会准备 Git / Python，Gitee 方式无需手动安装 Git，也不依赖 GitHub 下载便携 Git。

**网页和终端使用同一份 `AMC1-…` 接入信息**：Ubuntu 已安装后，在安装目录运行 `sudo bash install.sh connect --dir .`；
Windows 可以双击安装目录的 `connect-cloud.bat`。按提示粘贴并回车，输入不显示，通过连接测试才保存。
这整段已包含地址和 token 等信息，使用 Base64URL **编码，不是加密**；拿到整段内容就能解出凭据，应该把它作为上报密钥保管。
Windows 原监控还在运行时，需要按提示重启原窗口或服务后生效；Ubuntu 命令会重启原服务。
已有场地再次复制用「复制接入信息」，不会为了复制而更换 token。复制受浏览器限制时会选中内容，按 **Ctrl+C** 即可。

一键部署已填好云端地址和指纹；需要修改时展开 **「高级管理」**，先「保存地址」，再生成码。
共享 token 与自动注册开关也在高级管理，正常新增场地不用修改它们。旧接入码继续有效，缺少名称/类型时在场地端补填。

名称和类型会随专属凭据一起保存，重启或以后查看/更换接入码不会丢失。查看、更换、撤销仍需当前管理员密码并记入操作记录。
接入码只发给对应场地负责人，别放进命令或链接；**显示 2 分钟后隐藏不等于凭据失效**。一个新场地使用一份新专属码，不复用其它场地的码。

**旧测试场地怎么清理？** 在「场地接入 → 已分配的场地」核对 ID，点「删除场地」。等待首次上报、已撤销密钥的场地也能删除；只撤销密钥会保留记录。
删除会停用旧接入信息，并永久删除该 ID 的历史数据，同名的其他 ID 不受影响。误删后在顶部「已删除场地」点「恢复场地」，然后回到「场地接入」生成新信息，重新粘贴到本地端。旧码和已删除的历史不会恢复。

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

监控新装、历史不足或中途断档时，单场地已有的平均值会正常显示，同时注明「按有效时段计算」和覆盖时长。
例如 `90.88 PH/s` 下方显示 `有效数据 23.26/24h`，表示近一天中有 23.26 小时有效记录；不能当成完整 24 小时平均，也不能把缺失时段当成机器停机。
若场地配置了其他统计周期，页面会显示实际周期；跨场地只有可比较的周期才合成平均值。
缺失指标显示未知，实时数据过期会提示，不能把旧的在线值当成现在仍在运行。
场地矿机列表中由固件提供的「平均算力」，也不保证是软件统计的近 24 小时平均。

请同时更新场地端与云端；旧场地端没有上报新指标时，云端不会用累计值伪造实时算力或填零。
默认实时客户数据随摘要每 60 秒上报，平均报表每 600 秒更新；观察时留意采样时间。

#### 总算力正常，客户算力却是横线怎么办？

场地总览和客户指标使用不同上报字段。总览刚更新，不表示客户实时算力和平均值已经上报。

- **本地未上报**：云端未收到该场地的新客户指标。检查对应场地的本地端是否已经更新并重启，不能只刷新云端网页，也不能只下载新代码后继续运行原进程。
- **本轮数据不完整 / 等待有效数据**：已支持客户指标，但本轮没有足够的有效读数或历史。等待完成下一次扫描，并检查本地矿机读取状态。
- **数据已过期 / 报表已过期**：之前收到过数据，但距离采样或报表接收时间过久。检查本地扫描是否运行、云端上报是否成功；旧数值仍会保留并明确提示。
- **部分场地数据不足**：同一客户的跨场地合计不完整，先看按场地明细。一个场地缺数据，不能用其余场地的合计冒充客户总算力。

更新并重启对应本地端后，完成扫描即可开始发送新指标。保留的原始扫描记录会自动参与近 24 小时重算，**不必一律重新等满 24 小时**。
如果原始记录已经清理、只剩旧计费累计量，则无法还原缺失时段；界面会继续注明实际有效覆盖。无需为此删除数据库或重新绑定云端。

## 联系

微信 `raphael-2024` · [Telegram](https://t.me/+W3J9yAypNgpjNTk9)

云端：[GitHub](https://github.com/raphael2025/antmonitor-cloud) / [Gitee](https://gitee.com/raphael01/antmonitor-cloud) · 场地端：[GitHub](https://github.com/raphael2025/antmonitor) / [Gitee](https://gitee.com/raphael01/antmonitor)
