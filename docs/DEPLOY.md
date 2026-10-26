# 部署与运维（云端总览）

> 给部署/值班的人看。接口细节见 [API.md](API.md)，改代码见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 1. 云服务器要求

- 任意 Linux 云主机即可（Ubuntu 20.04+/Debian 11+/CentOS Stream 等），**1核1G 都绰绰有余**（10 场地 ≈ 43 万行/月 SQLite）。
- 不需要提前装 git / Python，一键脚本会装。
- 入方向放行：**有域名**放行 TCP 80、443；**没有域名**放行 TCP 8900（或 `--port` 指定的端口）。
- 各场地只需要**能访问公网**（出方向），不需要公网 IP / 端口映射。

## 2. 一键部署（推荐）

```bash
# A. 有域名：域名 A 记录先指向本服务器
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com

# B. 没有域名：用公网 IP(自动探测，探测不准用 --ip 指定) + 自签证书
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
```

脚本做的事：装系统依赖 → 下载代码到 `/opt/antmonitor-cloud`（有 git 用 git clone，网页「🔄 更新」可用；
没有 git 下载源码包）→ 建 venv 装依赖 → 生成 `config.yaml`（600 权限，上报/取数 token 随机生成）→
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
- 公网 IP 变了：重跑 `curl … | sudo bash -s -- --ip 新IP`，会重新生成证书（指纹变，各场地要更新）。
- 80/443 已被 nginx/apache 占用时脚本会停下：要么停掉它，要么按 §3 自己配反代。

其它命令（都是同一个脚本）：

```bash
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash                          # 再跑一次 = 更新(保留配置/数据/证书)
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- update             # 只更新代码和依赖
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- info               # 面板地址 / 证书指纹 / 服务状态
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- reset-password     # 重设 admin 密码
curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- uninstall          # 卸载服务(数据目录默认保留)
```

选项：`--port 端口`、`--dir 安装目录`、`--branch 分支`、`--http`（明文，仅内网测试）、`--lang en`。
GitHub 访问慢：`curl -fsSL <镜像前缀>/https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo GH_PROXY=<镜像前缀>/ bash`（镜像能看到并篡改下载内容，只用信得过的）。
离线安装：把源码目录拷到服务器，在目录里 `sudo bash install.sh`（直接用这份源码）。

服务管理：`systemctl status antmonitor-cloud`、`journalctl -u antmonitor-cloud -f`。

### 手动部署（不用一键脚本时）

```bash
cd /opt && git clone https://github.com/raphael2025/antmonitor-cloud.git && cd antmonitor-cloud
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml && chmod 600 config.yaml
#  ingest.token / public_api.token 换新(两把不能相同): venv/bin/python auth.py token
#  admin 设强密码: venv/bin/python auth.py passwd admin
#  自检: venv/bin/python seccheck.py   (没有"✖ 高危"才算过)
#  HTTPS：前置 nginx(§3)；或填 server.tls_cert/tls_key 让服务自己跑 HTTPS
#  (自签证书再加 server.tls_self_signed: true，场地接入页才会显示指纹)
venv/bin/python server.py
```

systemd 单元可参考一键脚本生成的 `/etc/systemd/system/antmonitor-cloud.service`。
Windows 服务器用 NSSM：`nssm install CloudOverview "C:\...\python.exe" "D:\antmonitor-cloud\server.py"`。

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

## 4. 场地接入（网页里完成，每个场地一次）

1. 云端：admin 登录 → 右上 **「🔌 场地接入」**。这里能看到：
   - **云端地址**（各场地填这个；一键安装已自动设好，可在此修改）、模式（域名 / IP 自签 / 明文）
   - **证书指纹**（仅无域名自签证书时显示）
   - 共享 token、各场地专属 token（只显示末 4 位）
2. 在「管理员密码」里输入你的登录密码，然后二选一：
   - **共享接入码**：「查看 / 接入码」→ 复制。任何场地都能用它上报，首次上报自动出现在面板上。
   - **专属接入码（推荐）**：「＋ 新场地：生成专属接入码」→ 预先分配一个场地 ID + 专属 token。
     该场地只认自己的 token，一个场地被攻破不能冒充其它场地；即使关闭了自动注册也能接入。
     已在上报的场地也可以在表格里点「生成专属 token」，把新接入码发给该场地替换。
3. 场地端：面板「⚙ 网段 → ☁ 云端上报」→ 粘贴接入码 →「填入」→ 填场地名/类型 → 勾选启用 → 保存 →
   「🔌 测试连接」。下方会一直显示上报状态（最近一次成功时间或失败原因）。
   新装场地也可以在安装命令里带上：`curl … | sudo bash -s -- --connect-code 'AMC1-…' --site-name XX一场`。
4. 1 分钟内云端面板出现场地卡片（场地首轮扫描完成前不推数）。

安全约定：
- 查看 / 更换 / 停用 token、生成接入码、改云端地址都要**再输一次密码**，错误次数计入登录限流（防会话被盗后在线爆破），
  每次操作写进「🔒 账号安全 → 最近操作记录」。
- 接入码 = 地址 + token (+ 指纹 + 场地 ID)，和 token 一样敏感：只发给场地负责人，别贴群里、别截图。显示 2 分钟后自动隐藏。
- 所有场地都换成专属 token 后，取消勾选「允许新场地用共享 token 自动注册」，再「停用」共享 token。
- 网页生成的 token 存在 `cloud_settings.json`（600 权限）；`config.yaml` 里写死的 token 网页只能覆盖、不能删除。

> **场地身份 = site_id，名字随便改**：改 `site_name` 即可，云端自动换标签、数据连续。
> **⚠ 克隆红线**：把整个场地端目录克隆部署到新场地时，必须删掉 `cloud_site_id.txt`
> （下次启动重新生成新 ID），否则两个场地同 ID，云端数据互相覆盖！（用专属接入码接入的场地 ID 写在接入码里，不受影响。）

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

每次发送记录在 `notify_log` 表（`sqlite3 cloud_overview.db "SELECT * FROM notify_log ORDER BY id DESC LIMIT 20"`），
排查"为什么没收到"用。场地本地的普通告警（单机掉线等）本地自己推，云端不重复轰炸。

## 6. 更新与发布（git 更新器）

本地开发 → push 到 git 远端 → 已部署的云端/场地端**检测并更新**（手动或自动），
配置和数据永远不被更新触碰（config.yaml/数据库都在 .gitignore 里）。

### 一次性准备

1. 建一个**私有** git 仓库（Gitee/GitHub 私有仓库，或云服务器上 `git init --bare /srv/git/main.git` 走 SSH）。
   ⚠ 必须私有——代码里虽无密钥，但架构/接口属敏感信息。
2. 开发机（本机）：`git remote add origin <仓库地址> && git push -u origin master`
3. 部署机改用 git 部署：`git clone <仓库地址> /opt/cloud-overview`，
   `cp config.example.yaml config.yaml` 填好配置再启动。
   （已在跑的老部署：`git init && git remote add origin <地址> && git fetch &&
   git reset --hard origin/master`，config.yaml 因被 ignore 不受影响）

### 分支模型与发布流程（日常）

`dev` 分支开发迭代，`master` 主支 = 生产（全部部署机跟踪它）。
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
  点击 → 自动拉取+编译自检+重启（约10秒），失败自动回滚。
  或命令行：`python updater.py check / apply`（apply 后需重启服务）。
- **场地端**：`config.yaml > update.auto: true` 则每小时自查自动更新重启（NAT 后无人值守的推荐方式）；
  或运维远程桌面到场地机器跑 `python updater.py apply` 再重启服务。
  也有 API：`GET /api/update/check`、`POST /api/update/apply`（admin）。

### 安全机制与红线

- **⚠ 红线：master 永远禁止 `git push --force` / `reset --hard` 后强推**——部署机只做
  快进合并，主支历史被改写后所有部署机快进失败、集体卡住不更新。回退一律用
  `git revert`（见上），它是"往前滚"的回退，与自动更新完美兼容。
- 只做**快进合并**（部署机本地有改动/分叉直接拒绝，不会丢东西）
- 更新后全量 `py_compile` 自检，**语法错误自动回滚**到原版本，绝不带病重启
- 重启交给守护进程（云端 systemd；场地端 NSSM 或 `run.bat` 循环）
- **灰度习惯**：先只给一个试点场地开 `update.auto`（或手动先更它），推完看云端面板
  正常，其它场地再更；重要版本发布前打标签 `git tag v20260709` 方便对照
- 自动更新的场地如果更坏了（运行期错误编译自检拦不住）：守护进程重启循环 →
  场地上报中断 → **云端 3 分钟后发"场地失联"告警**，这就是兜底信号
- Docker 部署不用本更新器（镜像重建更新）；二选一

## 7. 日常运维

- **改钥匙**：网页「🔌 场地接入」→ 输入密码 →「更换」。共享 token 换了以后所有还在用旧 token 的场地立即 401，
  要逐个粘贴新接入码；专属 token 只影响那一个场地。场地端面板会显示「上报 token 不对(401)」。
- **删除下线场地**：场地详情弹窗里点「删除场地」（admin）；误删可在「已删除场地」里恢复接收上报。改名不需要删除。
- **改密码**：网页右上「🔒 账号安全」自己改；忘了密码在服务器上 `python auth.py passwd 用户名`。
- **备份**：数据都可从场地重新积累，不算关键数据。要备份就每天
  `sqlite3 cloud_overview.db ".backup backup/co-$(date +%F).db"`（WAL 库别直接 copy 活文件）。
- **云端自身监控**：外部看门狗（Uptime Kuma / crontab curl）盯
  `GET /api/health`——不通或 `last_poll_age_s` 远超 60 即报警。别用面板自己盯自己。
- **升级**：重跑一键安装命令，或面板右上「🔄 更新」；手动部署替换文件后 `systemctl restart antmonitor-cloud`。
  建表语句幂等（`CREATE TABLE IF NOT EXISTS`），加表自动生效；改列需手工迁移。
- **日志**：`journalctl -u antmonitor-cloud -f`（老的手动部署服务名是 `cloud-overview`）。拉取失败/客户报表失败都有 `[collector]` 行。

## 8. 故障排查表

| 现象 | 依次检查 |
|---|---|
| 场地端「测试连接」报证书指纹不匹配 | 云端换过证书(公网 IP 变了重装)或连错了服务器：到「🔌 场地接入」复制新接入码重新粘贴 |
| 场地端报 HTTPS 证书校验失败 | 云端没域名用的是自签证书，场地端没填证书指纹：粘贴接入码(自带指纹) |
| 场地配好了但面板上不出现 | 先看场地端面板「☁ 云端上报」下方的上报状态 / 点「🔌 测试连接」，会直接说原因：401=token 不对；404=云端还没有有效 token；证书指纹不匹配；连接超时=场地出不了网或云端安全组没放行；「本地还没完成首轮扫描」=等首扫完 |
| 场地显示"✖ 不可达" | ① 场地断电/断网/监控进程挂了（最常见，本来就是这个信号）② 刚换过 token 没同步 ③ 看卡片上的错误文案：推送场地显示"超过N分钟未收到上报"，拉取场地显示具体连接错误 |
| 场地显示"⏸ 监控停滞" | 场地机器活着但扫描线程死了——上场地机器看本地监控（本地自己也有 stalled 告警） |
| 面板打不开 | `systemctl status antmonitor-cloud`；`curl -k https://127.0.0.1:8900/api/health`(IP 模式)或 `curl 127.0.0.1:8900/api/health`(域名模式)；`systemctl status caddy` / 证书；安全组是否放行 |
| 登录一直 401 | 密码不对：服务器上 `python auth.py passwd 用户名` 直接重设(立即生效)；429=试错太多等5分钟 |
| 登录提示"出厂默认密码，已禁止登录" | 同上，`python auth.py passwd admin` 设新密码 |
| 网页操作报"缺少 X-CO-CSRF 请求头" | 浏览器缓存了旧版前端：Ctrl+F5 刷新；自己写的 curl 脚本加 `-H "X-CO-CSRF: 1"` |
| 场地上报一直 401/404 | 「🔌 场地接入」看共享/专属 token 是否已设置(手动部署时 `ingest.token` 还是 `CHANGE_ME…` 也算未设置)，重新复制接入码给场地 |
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
- [ ] 每个场地发专属接入码（「🔌 场地接入 → ＋ 新场地」），全部发完后关闭自动注册并停用共享 token
- [ ] 开了 `update.auto` 的话：GitHub 账号开两步验证，生产只跟踪受保护的分支
- [ ] 外部看门狗已盯 `/api/health`
- [ ] 确认：本服务对场地零控制能力（只收数），无需给它任何场地内网权限

### 怀疑被入侵时

1. 管理员登录 →「🔒 账号安全」→ 看「最近操作记录」有没有陌生 IP 登录 / 改设置 / 触发更新
2. 改自己的密码（其它会话自动下线）→ 点「强制其它所有会话下线」
3. 「🔌 场地接入」里更换共享/专属 token 并把新接入码发给各场地；`public_api.token` 在 config.yaml 里换后重启服务
4. 检查通知设置里的 webhook/邮箱是否被改成陌生地址
