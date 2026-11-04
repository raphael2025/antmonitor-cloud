# 部署与运维（云端总览）

> 给部署/值班的人看。接口细节见 [API.md](API.md)，改代码见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 1. 云服务器要求

- 任意 Linux 云主机即可（Ubuntu 20.04+/Debian 11+/CentOS Stream 等），**1核1G 都绰绰有余**（10 场地 ≈ 43 万行/月 SQLite）。
- GitHub / Gitee 均提供一键安装，脚本自动准备 Git / Python，无需手动克隆仓库。
- 入方向放行：**有域名**放行 TCP 80、443；**没有域名**放行 TCP 8900（或 `--port` 指定的端口）。
- 各场地只需要**能访问公网**（出方向），不需要公网 IP / 端口映射。

## 2. 部署（GitHub / Gitee）

按网络情况选择下载源，再按有无域名选一条命令。示例域名替换为实际域名。

### GitHub 一键部署

```bash
# A. 有域名：域名 A 记录先指向本服务器
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --source github --domain overview.example.com

# B. 没有域名：用公网 IP(自动探测，探测不准用 --ip 指定) + 自签证书
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --source github
```

### Gitee 一键部署

```bash
# A. 有域名：域名 A 记录先指向本服务器
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- --source gitee --domain overview.example.com

# B. 没有域名：自动探测公网 IP，使用自签 HTTPS
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- --source gitee

# B 的替代：明确指定公网 IPv4，请替换占位内容
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- --source gitee --ip 你的公网IPv4
```

B 的两条命令选择一条即可。Gitee 模式会自动安装 Git，并从 Gitee 克隆源码；
安装 Git 或拉取失败时停止并显示错误，不会改从 GitHub 下载。
仓库归档下载页可能返回 HTML，Gitee 模式使用 Git 拉取，不把该页面当作 tar.gz / ZIP 解包。

### 自动选源

`--source auto` 是默认模式：新安装先尝试 GitHub，失败再尝试 Gitee；显式指定 `--source github` / `--source gitee` 会固定项目源码来源。
已有官方 Git 安装保留 `origin`，自动模式仅在 fetch 网络失败时临时尝试另一平台的同分支，仍只接受快进更新。
有本地源码改动、分支不符或自定义仓库时不会自动切换来源、强行覆盖或回退到旧版本。

脚本下载本身发生在安装器运行前；如果 GitHub 的 raw 地址打不开，必须用 Gitee 入口，或使用 [README 的完整自动选源命令](../README.md#三分钟上手)。
该命令先将脚本完整下载到临时文件，成功后才执行一次；安装已开始后不会因为失败而再执行另一份脚本。
Ubuntu 上若没有 `curl`，先执行 `sudo apt-get update && sudo apt-get install -y curl ca-certificates`，也可以把下载好的脚本和完整源码拷过来运行。

### 本地源码与受限网络

两个代码网站都无法访问时，在能联网的电脑下载完整源码 ZIP，拷到服务器解压，进入源码目录后执行：

```bash
sudo bash install.sh --dir /opt/antmonitor-cloud
```

不要附加 `--source`，安装器会使用本地源码。部署域名模式仍可追加 `--domain 你的域名`，使用指定公网 IP 可追加 `--ip 你的公网IPv4`。
默认目录不变；配置、数据库和证书按现有保留规则处理。无 `.git` 的源码包能运行，但代码网站完全不可达时无法提供 Git 在线更新。

**源码包不是完整离线包**。Linux 的 Python、venv、Git、curl、CA 证书等系统包，以及所选 HTTPS 模式需要的工具，
仍需系统软件源或预先准备的系统安装资源。域名证书还需要可用 DNS / ACME；Gitee 不代理这些下载。
Python 依赖可提前准备为与目标 Linux / CPU / Python 兼容的 wheels，放入项目 `offline/wheels`，或通过 `ANTCLOUD_WHEELHOUSE` 指定绝对目录。
启用 wheels 目录时 pip 使用 `--no-index`，缺包会明确失败，不会悄悄回到在线索引。

### 安装结果与网络模式

脚本做的事：装系统依赖 → 从所选平台下载代码到 `/opt/antmonitor-cloud` → 建 venv 装依赖 →
生成 `config.yaml`（600 权限，上报/取数 token 随机生成）→
设置 admin 密码（终端里输入；直接回车 = 随机生成并在结尾显示一次）→ 按模式配置 HTTPS →
建系统用户 `antcloud` 和 systemd 服务 `antmonitor-cloud`（NoNewPrivileges / ProtectSystem / ProtectHome）→ 启动并自检。

| | A. 有域名 | B. 没有域名 |
|---|---|---|
| 访问地址 | `https://overview.example.com` | `https://公网IP:8900` |
| 证书 | Caddy 自动申请/续期 Let's Encrypt | 安装时生成自签证书(10年)，`certs/` |
| 服务监听 | 127.0.0.1:8900，只能经 Caddy(443) 访问 | 0.0.0.0:8900，服务自己跑 HTTPS |
| 场地端要填 | 地址 + token | 地址 + token + **证书指纹** |
| 浏览器 | 正常 | 首次提示"不安全"：核对证书指纹一致后继续 |

- **为什么无域名也要 HTTPS**：明文 HTTP 下 token、登录密码、Cookie 全在网上裸奔。自签证书 + 场地端钉指纹，
  中间人拿不到私钥就冒充不了云端，安全性不比买证书差（场地端按指纹认，不看 CA）。
- **国内服务器 + 域名须 ICP 备案**，否则云厂商会拦截 80/443，证书也申请不下来——没备案用 B。
- 公网 IP 变了：用同一下载源重跑安装命令并追加 `--ip 新IP`（Gitee 保留 `--source gitee`），会重新生成证书，场地端需更新证书指纹。
- 80/443 已被 nginx/apache 占用时脚本会停下：要么停掉它，要么按 §3 自己配反代。

### 更新与日常管理

更新代码、Python 依赖并重启服务，按希望使用的来源选一条：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- update --source github

# Gitee
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- update --source gitee
```

`update` 保留配置、数据库和证书。默认脚本安装可直接用本地脚本查看状态、重设 admin 密码或卸载服务：

```bash
sudo bash /opt/antmonitor-cloud/install.sh info
sudo bash /opt/antmonitor-cloud/install.sh reset-password
sudo bash /opt/antmonitor-cloud/install.sh uninstall
```

卸载服务时会另行询问是否删除数据目录，默认保留。服务日志见 `journalctl -u antmonitor-cloud -f`。

可用选项：`--source auto|github|gitee`、`--port 端口`、`--dir 安装目录`、`--branch 分支`、
`--http`（明文，仅内网测试）、`--lang en`。云端生产分支是 `master`，场地端是 `main`，不要混用。
自定义安装目录时，每次安装/更新/管理都传入相同的 `--dir`，本地脚本路径也改为该目录。

Gitee 负责源码下载与 Git 更新；系统软件、Python 依赖、Caddy 和域名证书申请仍需相应网络访问。
GitHub 模式也可使用可信的 `GH_PROXY` 镜像前缀。选择 Gitee 请直接用 `--source gitee`，
不要把 Gitee 仓库 URL 填入 `GH_PROXY` 或 GitHub `owner/repo` 参数。

### 已有安装切换到 Gitee

适用于已有官方 GitHub / Gitee Git 部署。先保存本地代码改动，并确认两个平台拥有相同提交历史。
然后直接执行 Gitee 的一行更新命令：

```bash
curl -fsSL https://gitee.com/raphael01/antmonitor-cloud/raw/master/install.sh | sudo bash -s -- update --source gitee
```

显式 `--source gitee` 会把已有官方仓库的 `origin` 切换到 Gitee，再更新代码、依赖并重启；
网页更新随后使用这一来源。默认安装可核对：

```bash
sudo -u antcloud git -C /opt/antmonitor-cloud remote get-url origin
# 应显示 https://gitee.com/raphael01/antmonitor-cloud.git
```

原来不带 `--source` 的命令仍兼容：新装默认 GitHub，已有 Git 部署继续使用原 `origin`。自维护仓库或 fork 会被拒绝自动换源，
应由维护者确认来源，或省略 `--source` 沿用原仓库。若提示无法快进，请检查分支历史和本地改动，不要强制覆盖。
切换部署机的来源不会同步两个托管平台；发布者仍需维护镜像，见[GitHub 与 Gitee 同步](#github-与-gitee-同步)。

### 手动部署（不用一键脚本时）

下面的克隆地址可替换为 `https://gitee.com/raphael01/antmonitor-cloud.git`，后续步骤相同。

```bash
cd /opt && git clone https://github.com/raphael2025/antmonitor-cloud.git && cd antmonitor-cloud
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml && chmod 600 config.yaml
#  ingest.token / public_api.token 换新(两把不能相同): venv/bin/python auth.py token
#  admin 设强密码: venv/bin/python auth.py passwd admin
#  自检: venv/bin/python seccheck.py   (没有"✖ 高危"才算过)
#  HTTPS：前置 nginx(§3)；或填 server.tls_cert/tls_key 让服务自己跑 HTTPS
#  (自签证书再加 server.tls_self_signed: true，场地接入页才会显示指纹)
venv/bin/python cloud_launcher.py
```

systemd 单元可参考一键脚本生成的 `/etc/systemd/system/antmonitor-cloud.service`。
Windows 服务器用 NSSM：`nssm install CloudOverview "C:\...\python.exe" "D:\antmonitor-cloud\cloud_launcher.py"`。
已有 `python server.py` 服务入口会自动转入启动器；非 Git 源码包仍可直接运行，Docker 继续通过重建镜像更新。

## 3. 自己配 nginx 反代（不用一键脚本的 Caddy 时）

```nginx
server {
    listen 443 ssl;
    server_name overview.example.com;
    ssl_certificate     /etc/letsencrypt/live/overview.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/overview.example.com/privkey.pem;
    client_max_body_size 2m;          # 挡超大 POST(正常上报只有几KB)
    location / {
        proxy_pass http://127.0.0.1:8900;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;   # 让云端知道是 https，自动给 Cookie 加 Secure
    }
}
# http 一律跳 https(密码不能明文走网络)
server { listen 80; server_name overview.example.com; return 301 https://$host$request_uri; }
```

配好后 `config.yaml` 改两处并重启：
- **`server.host: "127.0.0.1"`**（8900 只给本机 nginx 用，公网只能走 443）
- **`server.trust_proxy: true`**（登录锁定/限速按 X-Forwarded-For 真实 IP 计。
  不开的话所有请求源都是 127.0.0.1——一个人输错密码全员被锁；没有反代时必须保持 false）。
  只有来自 `server.trusted_proxies`（默认本机+内网网段）的连接才会被信任这个头，
  公网直连伪造 XFF 无效；nginx 在另一台公网机器上时把它的 IP 加进 `trusted_proxies`。
- `auth.secure_cookie` 可以不改：https 访问时自动加 Secure。

服务自带安全响应头：CSP(只允许本站脚本)、禁止被 iframe 嵌套、HSTS(https 时)、接口禁缓存。

### Docker 部署（可选）

```bash
mkdir data && vi config.yaml   # db.path 改成 "data/cloud_overview.db"
docker build -t cloud-overview .
docker run -d --name overview --restart always -p 127.0.0.1:8900:8900 \
  -v ./config.yaml:/app/config.yaml:ro -v ./data:/app/data cloud-overview
```

网页改的密码、「场地接入」生成的 token 存在 `cloud_settings.json`，镜像里已用 `CO_SETTINGS=/app/data/cloud_settings.json`
把它放进 `data` 卷——容器重建不丢。用 `auth.py passwd` 改密码要在容器里跑：`docker exec -it overview python auth.py passwd admin`。

## 4. 场地接入（每个场地一次）

### 云端管理员：生成并复制完整接入信息

1. admin 登录，打开右上 **「🔌 场地接入」**。
2. 在「添加场地」填写 **场地名称**（1–64 个字符），选择 **风冷 / 水冷 / 混合**，输入当前管理员密码。
3. 点击 **「生成并复制完整接入信息」**，交给这个场地的负责人。上报地址、token、证书指纹（需要时）、随机场地 ID、名称和类型已经打包成一份 `AMC1-…` 信息，无需分开复制。

`AMC1-…` 使用 Base64URL 编码打包，**不是加密**。编码只是让地址和 token 等信息可以整段复制；持有整段内容的人能够解码出凭据。请把完整接入信息作为上报密钥保管，本地网页和命令界面会自动解析。

生成后场地会以填写的名称出现在列表中，状态为「等待首次上报」。名称和类型与 token 一次写入 `cloud_settings.json`；
待接入场地关闭页面、重启云端或以后查看/更换 token，都能恢复这份描述；已经上报的场地则使用当前名称和类型。没有名称元数据的旧待接入记录仍可查看/更换接入码。

已有场地用对应行的 **「复制接入信息」**，会复制当前有效凭据，不会更换 token。更换、撤销密钥或删除场地会改变其上报权限。
如果浏览器无法自动复制，页面会展开并选中接入信息，请按 **Ctrl+C**；不会把复制失败显示成成功。

### 场地负责人：粘贴一次，自动验证和保存

**本地网页**：打开「☁ 连接云端」，点 **「粘贴接入信息并连接」**。
局域网 HTTP 页面通常不允许按钮自动读取剪贴板，按提示 **Ctrl+V** 即可继续验证和保存，不需要再拆开填参数。
也可以在输入框手动粘贴，再点连接。旧码缺少名称/类型时才需要补填；测试不通过时保留原绑定和其他设置。

**Ubuntu 命令界面**：同一份接入信息可以直接粘贴到首次安装的提示中。已经安装且更新到当前版本时，在安装目录执行：

```bash
sudo bash install.sh connect --dir .
```

按提示粘贴并回车，测试通过后保存并重启原服务。旧版本可用下方的一键入口，把参数改为 `connect`；无需先在本地打开网页。

**Windows 命令界面**：双击安装目录的 `connect-cloud.bat`，或在该目录的 PowerShell 执行：

```powershell
.\connect-cloud.bat
```

按提示粘贴同一份接入信息，输入不显示。验证成功后保存；原监控端口空闲时会打开原 `run.bat`。
若原窗口或服务仍在运行，会提示保存完成但需要重启原监控后生效，不会强行关闭进程或启动第二个实例。

**Ubuntu / Linux 尚未安装**：云端结果区有 GitHub/Gitee 的命令复制按钮，也可选择下面一条在场地电脑运行：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor/main/install.sh | sudo bash -s -- --source github --connect-code -

# Gitee
curl -fsSL https://gitee.com/raphael01/antmonitor/raw/main/install.sh | sudo bash -s -- --source gitee --connect-code -
```

按终端提示粘贴接入码，输入不显示。安装脚本先测试连接，通过后才保存并启用上报设置；**命令里不要填写真实接入码**。
手动指定场地名/类型仍可使用 `--site-name` / `--site-type`，但通常无需重复输入。

**Windows 尚未安装**：在云端结果区选择 Windows，复制所需来源的命令到场地电脑 PowerShell 运行；
安装后在网页点「粘贴接入信息并连接」，或双击 `connect-cloud.bat` 粘贴。GitHub 和 Gitee 安装器都会自动准备 Git / Python；
Gitee 的 Git 下载不依赖 GitHub。PowerShell 安装入口不使用 `-ConnectCode` 参数。

云端收到场地首次上报后才算接入完成。测试连接本身不注册场地；首轮扫描尚未完成时可能仍需等待。
场地端的上报状态显示最近成功时间或失败原因，可据此检查网络、地址和证书。

### 高级管理与凭据维护

- 一键部署已保存云端地址；通过正常 HTTPS 地址打开的云端也可直接生成。经 SSH 隧道 / localhost 访问时，需要先在「高级管理」保存场地实际能够访问的地址；公网地址须使用 HTTPS。
- 手动修改地址时，**先保存地址，再生成或复制接入信息**。地址格式不合适会直接提示，不会先写入一个无法使用的新 token。
- 地址、自签证书指纹、共享 token 和自动注册开关均在「高级管理」。平时新增场地使用专属码即可，不需要启用共享自动注册。
- 每份专属码只用于一个场地。即使关闭自动注册，已分配专属凭据的新场地仍能接入；共享 token 不能替代它的专属 token。
- 查看、更换、撤销凭据和修改地址仍需当前管理员密码，失败计入登录限流，操作记入「🔒 账号安全 → 最近操作记录」。
- 接入码含长期凭据，只交给对应负责人。**2 分钟后隐藏不等于失效**；需要失效时在对应场地行撤销或更换。撤销专属 token 后也不会退回接受共享 token。
- `cloud_settings.json` 使用 0600 权限。网页操作通过覆盖层替换或停用配置中的凭据，不会改写 `config.yaml`；备份与容器数据卷应包含覆盖层文件。

场地身份以 `site_id` 为准。部署另一场地时应在云端新建一份专属码，不能复用旧场地的接入码或复制其上报凭据，否则两个场地会以同一 ID 上报。
仅删除 `cloud_site_id.txt` 不会清除专属码写入的 ID；复制安装目录后也要使用新场地的专属码重新绑定。

### 撤销密钥、删除场地与恢复

admin 可以在「场地接入 → 已分配的场地」管理已经分配接入信息的推送场地。**等待首次上报、已经撤销密钥和正常上报的场地，都可以按 ID 删除**，不需要为了删除而先让它上报一次。

| 操作 | 结果 |
| --- | --- |
| 撤销密钥 | 停止接受该 ID 的上报，但保留场地记录和历史；共享 token 也不能代替已撤销的专属凭据。 |
| 删除场地 | 从当前场地和接入列表中移除，停用旧接入信息，永久删除该 ID 的历史、客户及告警数据；旧上报包不能把它重新创建出来。 |
| 恢复场地 | 在顶部「已删除场地」中恢复记录，然后到「场地接入」为该 ID 生成新的接入信息，并重新粘贴到本地端。旧凭据不会重新生效，已删除历史无法恢复。 |

删除确认会显示名称和稳定的场地 ID。**同名不代表同一个场地**：核对 ID 后再删除，其他 ID 的配置和数据不会受到影响。已经上报的场地也可从详情弹窗删除；`config.yaml` 中的拉取模式场地仍需从配置移除。

## 5. 告警通知（强烈建议开启）

场地断电时本地监控也一起死了——**全网唯一能发现"整场失联"的就是云端**。
`config.yaml > alerts` 配好通道后，云端在这些时刻主动推送：
场地失联（超3个周期没数据）/ 监控停滞（机器在但扫描死了）/ 恢复 /
算力骤降（比 1 小时前基线跌超 `hashrate_drop_pct`%，抓大面积掉算力和限电）/
掉线台数超阈值（`total-online` ≥ `offline_threshold`，跟场地本地"单机掉线"告警不冲突——
本地报的是"哪台坏了"，这个报的是"掉线规模大到该让老板知道"，只在超过设定台数时才发）。

```yaml
alerts:
  enabled: true
  cooldown: 1800            # 同场地同类告警冷却，恢复通知不受限
  hashrate_drop_pct: 30
  offline_threshold: 0      # 单场地掉线台数达到此值→告警；0=关(默认关，按需开)
  telegram: {enabled: true, bot_token: "12345:AA...", chat_id: "-100..."}
  webhook:                  # 企业微信/钉钉群机器人：建机器人拿 webhook 地址填这里即可
    enabled: true
    url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx"
    kind: wecom             # wecom|dingtalk|raw
  email:                    # SMTP 邮件通知，任意邮箱服务商都行(企业邮箱/QQ邮箱/Gmail...)
    enabled: true
    smtp_host: "smtp.exmail.qq.com"
    smtp_port: 465          # 465=SSL(推荐) | 587=STARTTLS(下面 use_ssl 改 false)
    use_ssl: true
    username: "alert@yourcompany.com"
    password: "登录密码或授权码(QQ邮箱等第三方登录要用授权码)"
    to: ["boss@yourcompany.com", "ops@yourcompany.com"]
```
三个通道可同时开，同一条告警会分别发到每个已启用的通道。

通道失败会按 60、120、240、480 秒、之后最高 900 秒退避补发，由采集循环在到期后处理；
已经确认接收的通道不会重复补发。每个通道单独记录结果，Telegram 和企业微信/钉钉还会检查业务返回值，
HTTP 200 本身不代表消息被接受。关闭通知暂停投递，重新启用后仅继续当前仍成立的待发事件；
故障已恢复或场地已删除时不补发旧故障。补发队列只在当前进程中保存，服务重启不会恢复它。
算力骤降恢复对照本次事件开始时的基线，长期维持低算力不会因滚动基线下降而被误报为恢复。

每次发送记录在 `notify_log` 表（`sqlite3 cloud_overview.db "SELECT * FROM notify_log ORDER BY id DESC LIMIT 20"`），
排查"为什么没收到"用。场地本地的普通告警（单机掉线等）本地自己推，云端不重复轰炸。

## 6. 更新与发布（git 更新器）

本地开发 → push 到 git 远端 → 已部署的云端/场地端**检测并更新**（手动或自动）。
更新来源由安装目录的 `origin` 决定；配置和数据库通过 `.gitignore` 排除，更新前仍应保存本地代码改动。

### 选择发布源

1. 使用官方公开仓库即可：[GitHub](https://github.com/raphael2025/antmonitor-cloud) 或 [Gitee](https://gitee.com/raphael01/antmonitor-cloud)，不需要另建私有仓库。首次安装与已有部署切换来源见 §2。
2. 自行修改代码时，可使用自己的 GitHub/Gitee 仓库或自建 Git 服务；部署机需要能读取所选远端。
3. 同时维护 GitHub 和 Gitee 时，发布者需将同一版本同步到两个仓库。部署机和网页更新器只检查自己的 `origin`，不会在两个平台之间自动切换。

### GitHub 与 Gitee 同步

推荐以 GitHub 为主库，在 Gitee「管理 → 仓库镜像管理」中配置 **Pull（GitHub → Gitee）自动镜像**。
平台是否为当前账户/仓库开放镜像功能，以管理入口和权限为准；需要按提示授权 GitHub 仓库并配置自动同步。
**导入仓库不等于已经开启自动同步**，未配置镜像时，后续 GitHub 提交不会仅因曾经导入就持续同步。

官方说明：[Gitee 仓库镜像管理](https://help.gitee.com/repository/settings/sync-between-gitee-github) ·
[2026 年官方介绍](https://blog.gitee.com/2026/01/23/github-gitee-mirror-sync-one-commit-two-repos/)。
自动 Pull 使用 GitHub webhook 触发；官方的 5 分钟是最短触发间隔，不是默认每 5 分钟轮询。
如没有自动镜像入口，发布者也可将**同一提交**分别推送到 GitHub 和 Gitee；不要在两边分别修改出不同历史。
发布后核对云端 `master`、场地端 `main` 的提交一致，再通知部署机更新。使用本项目公开仓库即可，无需另外建立私有仓库。

### 分支模型与发布流程（日常）

以下以云端为例：`dev` 分支开发迭代，`master` 主支 = 生产。场地端的生产分支为 `main`，不要混用。
历史提交天然就是"所有旧版本"，回退不需要专门的备份分支。

```bash
# ── 日常开发(dev 分支) ──
git checkout dev
# ...改代码、本地测试...
git add -A && git commit -m "改进xxx"

# ── 发布(测试通过 → 主支) ──
git checkout master
git merge --no-ff dev -m "发布: 改进xxx"   # --no-ff 留合并节点，回退只需 revert 一下
git push origin master

# ── 出问题回退上一个版本 ──
git revert -m 1 HEAD && git push origin master   # 反向提交往前推，部署机当成新版本更下去
```

- **云端**：面板右上角自动出现「🔄 更新 (N)」按钮（admin 可见，悬停看更新内容）→
  点击后先在独立候选目录编译、导入并执行隔离启动检查。依赖变化时创建单独环境，检查通过后才快进切换代码。
  新进程运行至少 60 秒且本次实例的 HTTP 健康接口确认成功后，更新才完成；依赖下载会增加等待时间。
  命令行分别执行 `python updater.py check`、`python updater.py apply`，后者准备完成后仍需重启服务。
  已有受保护 Git 安装重跑安装脚本或执行 `update` 时，也使用这一流程，并保持 GitHub/Gitee 及离线 wheels 选项。
- **场地端**：`config.yaml > update.auto: true` 则每小时自查自动更新重启（NAT 后无人值守的推荐方式）；
  或运维远程桌面到场地机器跑 `python updater.py apply` 再重启服务。
  也有 API：`GET /api/update/check`、`POST /api/update/apply`（admin）。

### 安全机制与红线

- **⚠ 红线：master 永远禁止 `git push --force` / `reset --hard` 后强推**——部署机只做
  快进合并，主支历史被改写后所有部署机快进失败、集体卡住不更新。回退一律用
  `git revert`（见上），它是"往前滚"的回退，与自动更新完美兼容。
- 只做**快进合并**，分叉历史会被拒绝。云端在候选检查前、真正切换前都要求干净工作区，
  暂存、未暂存和未忽略的新文件都会阻止更新；不会用强制重置覆盖现场代码改动。
- 云端候选代码或依赖检查失败时保留运行版本与原解释器。新版本启动崩溃、未确认健康便退出，
  或 180 秒内不能通过健康确认时，启动器有限重试；连续 3 次失败后恢复上一提交及依赖环境。
  回退前若发现现场改动，停止自动回退并报告人工检查，保留改动。
- 已失败的提交会被自动更新跳过；修复环境后可用网页手动更新或 CLI apply 重试。
  恢复状态与分版本依赖环境存于 Git 私有目录 `antcloud-update`（普通克隆为 `.git/antcloud-update`），
  运行中的解释器可能位于其中。保留该目录，配置和数据库继续按各自备份方案保存。
- 首次从没有启动器的旧版本升级时，该次更新仍由旧更新代码执行；本版成功启动后，后续 Git 更新才具有上述完整保护。
- 云端由 `cloud_launcher.py` 监督应用子进程，systemd 管理启动器；场地端继续使用 NSSM 或 `run.bat` 循环。
- **灰度习惯**：先只给一个试点场地开 `update.auto`（或手动先更它），推完看云端面板
  正常，其它场地再更；重要版本发布前打标签 `git tag v20260709` 方便对照
- 自动更新的场地如果更坏了（运行期错误编译自检拦不住）：守护进程重启循环 →
  场地上报中断 → **云端 3 分钟后发"场地失联"告警**，这就是兜底信号
- Docker 部署不用本更新器（镜像重建更新）；二选一

## 7. 日常运维

- **改钥匙**：网页「🔌 场地接入」→ 输入密码 →「更换」。共享 token 换了以后所有还在用旧 token 的场地立即 401，
  要逐个粘贴新接入码；专属 token 只影响那一个场地。场地端面板会显示「上报 token 不对(401)」。
- **删除下线或测试场地**：admin 在「场地接入」对应 ID 行点「删除场地」，尚未上报或已撤销密钥的记录也能删除；已上报场地也可从详情弹窗删除。误删后先在「已删除场地」恢复，再生成新接入信息交给本地端；历史无法恢复。改名不需要删除。
- **改密码**：网页右上「🔒 账号安全」自己改；忘了密码在服务器上 `python auth.py passwd 用户名`。
- **备份**：云端摘要可以继续接收，但已经清理的历史、通知记录、场地改名和删除记录不能靠下一次上报完整恢复。
  定期用 SQLite 在线备份保存数据库（默认路径示例：先运行 `mkdir -p backup`，再运行
  `sqlite3 cloud_overview.db ".backup backup/co-$(date +%F).db"`；自定义路径按 `config.yaml` 调整）。
  同时保存 `config.yaml`、`cloud_settings.json` 及使用的证书/私钥；这些文件包含账号和上报凭据，
  备份只交给管理员并保留受限文件权限。WAL 库不能直接复制正在写入的 `.db` 文件来代替在线备份。
- **云端自身监控**：外部看门狗（Uptime Kuma / crontab curl）盯
  `GET /api/health`——不通或 `last_poll_age_s` 远超 60 即报警。别用面板自己盯自己。
- **升级**：重跑一键安装命令，或面板右上「🔄 更新」；手动部署替换文件后 `systemctl restart antmonitor-cloud`。
  建表语句幂等（`CREATE TABLE IF NOT EXISTS`），加表自动生效；改列需手工迁移。
- **日志**：`journalctl -u antmonitor-cloud -f`（老的手动部署服务名是 `cloud-overview`）。拉取失败/客户报表失败都有 `[collector]` 行。

## 8. 故障排查表

| 现象 | 依次检查 |
|---|---|
| 场地端「连接云端」报证书指纹不匹配 | 云端换过证书(公网 IP 变了重装)或连错了服务器：到「🔌 场地接入」复制新接入码重新粘贴 |
| 场地端报 HTTPS 证书校验失败 | 云端没域名用的是自签证书，场地端没填证书指纹：粘贴接入码(自带指纹) |
| 场地配好了但面板上不出现 | 打开场地端顶部「☁ 连接云端」查看下方的上报状态；需重新连接时粘贴接入码并点「连接云端」，先测试、通过后再保存。错误提示含：401=token 不对；404=云端还没有有效 token；证书指纹不匹配；连接超时=场地出不了网或云端安全组没放行；「本地还没完成首轮扫描」=等首扫完 |
| 场地显示"✖ 不可达" | ① 场地断电/断网/监控进程挂了（最常见，本来就是这个信号）② 刚换过 token 没同步 ③ 看卡片上的错误文案：推送场地显示"超过N分钟未收到上报"，拉取场地显示具体连接错误 |
| 场地显示"⏸ 监控停滞" | 场地机器活着但扫描线程死了——上场地机器看本地监控（本地自己也有 stalled 告警） |
| 面板打不开 | `systemctl status antmonitor-cloud`；`curl -k https://127.0.0.1:8900/api/health`(IP 模式)或 `curl 127.0.0.1:8900/api/health`(域名模式)；`systemctl status caddy` / 证书；安全组是否放行 |
| 登录一直 401 | 密码不对：服务器上 `python auth.py passwd 用户名` 直接重设(立即生效)；429=试错太多等5分钟 |
| 登录提示"出厂默认密码，已禁止登录" | 同上，`python auth.py passwd admin` 设新密码 |
| 网页操作报"缺少 X-CO-CSRF 请求头" | 浏览器缓存了旧版前端：Ctrl+F5 刷新；自己写的 curl 脚本加 `-H "X-CO-CSRF: 1"` |
| 场地上报一直 401/404 | 「🔌 场地接入」看共享/专属 token 是否已设置(手动部署时 `ingest.token` 还是 `CHANGE_ME…` 也算未设置)，重新复制接入码给场地 |
| 旧测试场地已撤销密钥，但仍在接入列表 | 撤销保留记录；在对应 ID 行点「删除场地」。未上报过的场地也可以删除。 |
| 删除后旧接入信息不能上报 | 删除会停用旧凭据。若需恢复，在「已删除场地」恢复后，到「场地接入」为原 ID 生成新信息并粘贴到本地端。 |
| 图表是空的 | 数据点不足（新场地至少要 2 个上报周期）；时间范围里没数据（快照只留 30 天） |
| 中文变乱码 | 若是用 `curl \| python` 调试——那是调试管道的 GBK 解码假象，见 DEVELOPMENT.md §9；面板/接口本身全程 UTF-8 |
| 告警没收到 | ① `alerts.enabled` 与通道 enabled ② 查 `notify_log` 表：有行 ok=0=通道发送失败(token/网络)，有行 ok=1=已发出(查群机器人/静音)，没行=没触发(状态没跳变/冷却期内) ③ 服务刚重启第一轮不发(防重启风暴) |
| 面板出现重复场地 | 两个场地用了同一个 site_id(克隆目录没删 cloud_site_id.txt)或旧名残留——改正场地ID后 `DELETE /api/site/<残留id>` |
| 登录被莫名锁定 | nginx 后面没开 `server.trust_proxy: true`，所有人共享 127.0.0.1 的失败计数 |

## 9. 安全清单（上线前逐条勾）

> 一键检查：`python seccheck.py`（启动日志、管理员网页「🔒 账号安全」里也有同一份清单）。

- [ ] `ingest.token`、`public_api.token` 已换成新随机串且两者不同（一键安装自动生成；手动部署用 `python auth.py token`）
- [ ] 默认口令 admin888/viewer888 已改（`python auth.py passwd admin`；默认口令本来也登不进去）
- [ ] HTTPS 已开：一键安装的域名模式(Caddy)或 IP 模式(自签+指纹)；自己配 nginx 时 http 跳 https 并传 `X-Forwarded-Proto`
- [ ] `server.host: "127.0.0.1"` 或防火墙拦截 8900，外部只能走 443
- [ ] `config.yaml` / `cloud_settings.json` 权限 600
- [ ] 每个场地发专属接入信息（「🔌 场地接入 → 添加场地」，填写名称、类型和管理员密码后点「生成并复制接入信息」），全部发完后关闭自动注册并停用共享 token
- [ ] 开了 `update.auto` 的话：GitHub/Gitee 账号开两步验证，生产只跟踪受保护的分支
- [ ] 外部看门狗已盯 `/api/health`
- [ ] 确认：本服务对场地零控制能力（只收数），无需给它任何场地内网权限

### 怀疑被入侵时

1. 管理员登录 →「🔒 账号安全」→ 看「最近操作记录」有没有陌生 IP 登录 / 改设置 / 触发更新
2. 改自己的密码（其它会话自动下线）→ 点「强制其它所有会话下线」
3. 「🔌 场地接入」里更换共享/专属 token 并把新接入码发给各场地；`public_api.token` 在 config.yaml 里换后重启服务
4. 检查通知设置里的 webhook/邮箱是否被改成陌生地址
