# 部署与运维（云端总览）

> 给部署/值班的人看。接口细节见 [API.md](API.md)，改代码见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 1. 云服务器要求

- 任意 Linux 云主机即可，**1核1G 都绰绰有余**（数据量：10 场地 ≈ 43 万行/月 SQLite）。
- Python 3.8+。
- 入方向放行：443（走 nginx TLS，推荐）或 8900（裸跑，仅测试）。
- 各场地只需要**能访问公网**（出方向），不需要公网 IP / 端口映射。

## 2. 部署步骤（Linux + systemd）

```bash
# ① 上传本目录到 /opt/cloud-overview，装依赖
cd /opt/cloud-overview
python3 -m venv venv && venv/bin/pip install -r requirements.txt

# ② cp config.example.yaml config.yaml && chmod 600 config.yaml，上线前必做：
#    ingest.token / public_api.token 换新(两把不能相同): venv/bin/python auth.py token
#    admin 设强密码(交互输入，不进 shell 历史，立即生效): venv/bin/python auth.py passwd admin
#    viewer 同上(或从 config.yaml 删掉 viewer 用户)
#    ⚠ 出厂默认口令 admin888/viewer888 是禁止登录的；CHANGE_ME 开头的 token 视为未配置
#    自检: venv/bin/python seccheck.py   (没有"✖ 高危"才算过)

# ③ systemd 服务
cat > /etc/systemd/system/cloud-overview.service <<'EOF'
[Unit]
Description=Mining Cloud Overview
After=network.target

[Service]
WorkingDirectory=/opt/cloud-overview
ExecStart=/opt/cloud-overview/venv/bin/python server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now cloud-overview
systemctl status cloud-overview     # 应看到"云端总览启动：...上报接口已开启"
```

Windows 服务器则用 NSSM 注册（同本地监控的做法）：
`nssm install CloudOverview "C:\...\python.exe" "E:\main\server.py"`。

## 3. nginx TLS（公网必须）

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

## 4. 场地接入 SOP（每个场地做一次）

1. 场地本地监控更新到含 `cloud_report.py` 的版本（E:\ip 现版本已含）。
2. **推荐走网页**：打开场地本地面板 → 右上「⚙ 网段」设置 → 「☁ 云端上报」填
   场地名/类型(风冷/水冷/混合)/云端地址/token → 勾选启用 → 保存**即时生效**
   (存 settings.json，不用碰 yaml、不用重启)。或者改 `config.yaml` 的 `cloud:` 段：

   ```yaml
   cloud:
     enabled: true
     url: "https://overview.example.com"   # 云端地址(nginx 后的域名)
     token: "<云端 config.yaml > ingest.token>"
     site_name: "内蒙一场"                  # 云端显示名，全网唯一，起好别改
     site_type: air                         # air=风冷 | hydro=水冷
   ```
3. 重启场地监控。控制台应出现 `[cloud] 云端上报已启动 → ...(场地: XX, ID: st-xxxx)`——
   这个 **ID 是场地在云端的唯一身份**（自动生成存 `cloud_site_id.txt`，一次生成永不变）。
4. 1 分钟内刷新云端面板，场地卡片自动出现（首扫没完成前不推数，新装场地等首扫）。
5. （推荐）给场地发专属钥匙：云端 `ingest.tokens` 加一行 `st-xxxx: "新随机串"` 重启云端，
   场地 `cloud.token` 换成这把重启——此后共享钥匙对该场地无效，
   一个场地被攻破不能冒充其它场地。
6. 验证不通看 §8 排查表。

> **场地身份 = site_id，名字随便改**：改 `site_name` 重启即可，云端自动换标签、数据连续。
> **⚠ 克隆红线**：把整个 E:\ip 目录克隆部署到新场地时，必须删掉 `cloud_site_id.txt`
> （下次启动重新生成新 ID），否则两个场地同 ID，云端数据互相覆盖！

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

- **改钥匙**：共享钥匙=改云端 `ingest.token` + 逐场地改 `cloud.token`；
  专属钥匙=只改云端 `ingest.tokens` 对应行 + 该场地 `cloud.token`（其它场地不受影响）。
  换钥匙期间旧钥匙上报 401（面板显示不可达），换完自动恢复。
- **删除下线场地**：
  `curl -b ck.txt -H "X-CO-CSRF: 1" -X DELETE "https://云端/api/site/<site_id>"`（admin）。改名不需要删除。
- **改密码**：网页右上「🔒 账号安全」自己改；忘了密码在服务器上 `python auth.py passwd 用户名`。
- **备份**：数据都可从场地重新积累，不算关键数据。要备份就每天
  `sqlite3 cloud_overview.db ".backup backup/co-$(date +%F).db"`（WAL 库别直接 copy 活文件）。
- **云端自身监控**：外部看门狗（Uptime Kuma / crontab curl）盯
  `GET /api/health`——不通或 `last_poll_age_s` 远超 60 即报警。别用面板自己盯自己。
- **升级**：替换 .py/web 文件后 `systemctl restart cloud-overview`。
  建表语句幂等（`CREATE TABLE IF NOT EXISTS`），加表自动生效；改列需手工迁移。
- **日志**：`journalctl -u cloud-overview -f`。拉取失败/客户报表失败都有 `[collector]` 行。

## 8. 故障排查表

| 现象 | 依次检查 |
|---|---|
| 场地配好了但面板上不出现 | ① 场地控制台有没有 `[cloud] 云端上报已启动`（没有→cloud 段 enabled/url/token/site_name 有空缺）② 场地控制台 `[cloud] 上报失败×N` 的报错内容：401=token 不对；连接超时=场地出不了网/域名不通/云端防火墙 ③ 场地是否完成过首扫（`scanned:false` 不推数据）④ 云端 `ingest.token` 是否为空（空=接口整体 404） |
| 场地显示"✖ 不可达" | ① 场地断电/断网/监控进程挂了（最常见，本来就是这个信号）② 刚换过 token 没同步 ③ 看卡片上的错误文案：推送场地显示"超过N分钟未收到上报"，拉取场地显示具体连接错误 |
| 场地显示"⏸ 监控停滞" | 场地机器活着但扫描线程死了——上场地机器看本地监控（本地自己也有 stalled 告警） |
| 面板打不开 | `systemctl status cloud-overview`；`curl 127.0.0.1:8900/api/health`；nginx 配置/证书 |
| 登录一直 401 | 密码不对：服务器上 `python auth.py passwd 用户名` 直接重设(立即生效)；429=试错太多等5分钟 |
| 登录提示"出厂默认密码，已禁止登录" | 同上，`python auth.py passwd admin` 设新密码 |
| 网页操作报"缺少 X-CO-CSRF 请求头" | 浏览器缓存了旧版前端：Ctrl+F5 刷新；自己写的 curl 脚本加 `-H "X-CO-CSRF: 1"` |
| 场地上报一直 401/404 | 云端 `ingest.token` 还是 `CHANGE_ME…` 模板值(视为未配置)——换成真随机串 |
| 图表是空的 | 数据点不足（新场地至少要 2 个上报周期）；时间范围里没数据（快照只留 30 天） |
| 中文变乱码 | 若是用 `curl \| python` 调试——那是调试管道的 GBK 解码假象，见 DEVELOPMENT.md §9；面板/接口本身全程 UTF-8 |
| 告警没收到 | ① `alerts.enabled` 与通道 enabled ② 查 `notify_log` 表：有行 ok=0=通道发送失败(token/网络)，有行 ok=1=已发出(查群机器人/静音)，没行=没触发(状态没跳变/冷却期内) ③ 服务刚重启第一轮不发(防重启风暴) |
| 面板出现重复场地 | 两个场地用了同一个 site_id(克隆目录没删 cloud_site_id.txt)或旧名残留——改正场地ID后 `DELETE /api/site/<残留id>` |
| 登录被莫名锁定 | nginx 后面没开 `server.trust_proxy: true`，所有人共享 127.0.0.1 的失败计数 |

## 9. 安全清单（上线前逐条勾）

> 一键检查：`python seccheck.py`（启动日志、管理员网页「🔒 账号安全」里也有同一份清单）。

- [ ] `ingest.token`、`public_api.token` 已换成新随机串且两者不同（`python auth.py token`）
- [ ] 默认口令 admin888/viewer888 已改（`python auth.py passwd admin`；默认口令本来也登不进去）
- [ ] nginx TLS 已配，http 跳 https，传了 `X-Forwarded-Proto`
- [ ] `server.host: "127.0.0.1"` 或防火墙拦截 8900，外部只能走 443
- [ ] `config.yaml` / `cloud_settings.json` 权限 600
- [ ] 每个场地发专属钥匙（`ingest.tokens`），全部发完后 `ingest.auto_register: false`
- [ ] 开了 `update.auto` 的话：GitHub 账号开两步验证，生产只跟踪受保护的分支
- [ ] 外部看门狗已盯 `/api/health`
- [ ] 确认：本服务对场地零控制能力（只收数），无需给它任何场地内网权限

### 怀疑被入侵时

1. 管理员登录 →「🔒 账号安全」→ 看「最近操作记录」有没有陌生 IP 登录 / 改设置 / 触发更新
2. 改自己的密码（其它会话自动下线）→ 点「强制其它所有会话下线」
3. 换 `ingest.token` / `public_api.token`（及各场地 `cloud.token`），重启服务
4. 检查通知设置里的 webhook/邮箱是否被改成陌生地址
