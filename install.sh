#!/usr/bin/env bash
# AntMonitor Cloud · 一键安装 / 更新 / 卸载（Ubuntu/Debian，也支持 CentOS/RHEL 系）
#
# 有域名(自动申请 HTTPS 证书，推荐)：
#   curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash -s -- --domain overview.example.com
# 没有域名(用公网 IP + 自签证书，场地端按证书指纹认云端)：
#   curl -fsSL https://raw.githubusercontent.com/raphael2025/antmonitor-cloud/master/install.sh | sudo bash
#
# 再跑一次同样的命令 = 更新(保留配置和数据)。其它：
#   ... | sudo bash -s -- update            只更新代码和依赖
#   ... | sudo bash -s -- info              显示面板地址/证书指纹/服务状态
#   ... | sudo bash -s -- reset-password    重设 admin 密码
#   ... | sudo bash -s -- uninstall         卸载服务(数据目录默认保留)
# 选项：--domain 域名 | --ip 公网IP | --port 端口(默认 8900) | --dir 安装目录 | --branch 分支
#       --http(仅内网测试：不加密) | --lang zh|en
# 环境变量：GH_PROXY=https://镜像前缀/  访问 GitHub 很慢时用(镜像能看到并篡改下载内容，只用信得过的)
set -euo pipefail

REPO="${ANTCLOUD_REPO:-raphael2025/antmonitor-cloud}"
BRANCH="${ANTCLOUD_BRANCH:-master}"
DIR="${ANTCLOUD_HOME:-/opt/antmonitor-cloud}"
SERVICE="antmonitor-cloud"
SVC_USER="antcloud"
GH_PROXY="${GH_PROXY:-}"
MARK_BEGIN="# >>> antmonitor-cloud"
MARK_END="# <<< antmonitor-cloud"

ACTION="install"
DOMAIN=""
PUBLIC_IP=""
PORT=""
MODE=""            # domain | ip | http；空=沿用现有配置(更新时)
LANG_CODE=""
GENERATED_PW=""

case "${LANG:-}" in zh*) LANG_CODE="zh" ;; esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    install|update|uninstall|reset-password|info) ACTION="$1" ;;
    --domain) DOMAIN="${2:-}"; MODE="domain"; shift ;;
    --ip) PUBLIC_IP="${2:-}"; [[ -z "$MODE" ]] && MODE="ip"; shift ;;
    --port) PORT="${2:-}"; shift ;;
    --dir) DIR="${2:-}"; shift ;;
    --branch) BRANCH="${2:-}"; shift ;;
    --http) MODE="http" ;;
    --lang) LANG_CODE="${2:-}"; shift ;;
    -h|--help) sed -n '2,20p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "未知参数 / unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ -z "$LANG_CODE" ]] && LANG_CODE="zh"

# 从解压/克隆好的源码目录里运行(sudo bash install.sh)：直接用这份源码，不联网下载(离线安装)
SRC=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  _d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  if [[ -f "$_d/server.py" && "$_d" != "$(cd "$DIR" 2>/dev/null && pwd -P || echo "$DIR")" ]]; then
    SRC="$_d"
  fi
fi

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; CYN=$'\033[36m'; BLD=$'\033[1m'; RST=$'\033[0m'
# L "中文" "English"
L() { if [[ "$LANG_CODE" == "zh" ]]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
info() { echo "${CYN}→${RST} $*"; }
ok()   { echo "${GRN}✓${RST} $*"; }
warn() { echo "${YLW}!${RST} $*"; }
die()  { echo "${RED}✗${RST} $*" >&2; exit 1; }

# curl | bash 时标准输入是脚本本身：交互一律走 /dev/tty，没有终端(cloud-init 等)就用默认值
has_tty() { ( exec </dev/tty ) 2>/dev/null; }
ask() {  # ask 变量名 提示 [silent]
  local __v="" ; has_tty || return 1
  if [[ "${3:-}" == "silent" ]]; then read -r -s -p "$2" __v </dev/tty; echo >/dev/tty
  else read -r -p "$2" __v </dev/tty; fi
  printf -v "$1" '%s' "$__v"
}

[[ "${EUID}" -eq 0 ]] || die "$(L '请用 root 运行：curl ... | sudo bash' 'Run as root: curl ... | sudo bash')"
PY="$DIR/venv/bin/python"
CONF="$DIR/config.yaml"

# ---------- 依赖 ----------
install_packages() {
  info "$(L '安装系统依赖(python3 / venv / openssl / curl / git)…' 'Installing system packages…')"
  if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq || warn "apt-get update $(L '失败，继续尝试' 'failed, continuing')"
    apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates openssl tar >/dev/null
    apt-get install -y -qq git >/dev/null 2>&1 || warn "$(L 'git 装不上，改用源码包下载(网页一键更新不可用，可再跑本命令更新)' 'git unavailable; using tarball download')"
  elif command -v dnf >/dev/null || command -v yum >/dev/null; then
    local pm; pm="$(command -v dnf || command -v yum)"
    "$pm" install -y -q python3 python3-pip curl ca-certificates openssl tar >/dev/null
    "$pm" install -y -q git >/dev/null 2>&1 || warn "$(L 'git 装不上，改用源码包下载' 'git unavailable; using tarball')"
  else
    die "$(L '只支持 apt / dnf / yum 系统' 'Only apt/dnf/yum based systems are supported')"
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "$(L "python3 版本太低($(python3 -V 2>&1))，需要 3.8+" "python3 too old, need 3.8+")"
}

# ---------- 代码 ----------
git_dir() { git -c safe.directory="$DIR" -C "$DIR" "$@"; }

fetch_tarball() {
  local tmp url
  tmp="$(mktemp -d)"
  url="${GH_PROXY}https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz"
  info "$(L '下载源码包' 'Downloading tarball') $url"
  if ! curl -fsSL --retry 3 --connect-timeout 15 "$url" | tar xz -C "$tmp" --strip-components=1; then
    rm -rf "$tmp"
    die "$(L '下载失败：检查服务器能否访问 GitHub；国内服务器可设 GH_PROXY 镜像前缀后重试' 'Download failed; check GitHub access or set GH_PROXY')"
  fi
  mkdir -p "$DIR"
  cp -a "$tmp"/. "$DIR"/      # config.yaml / 数据库 / 证书不在源码包里，不会被覆盖
  rm -rf "$tmp"
}

copy_local_src() {
  info "$(L '使用本地源码' 'Using local source') $SRC → $DIR"
  mkdir -p "$DIR"
  tar -C "$SRC" --exclude=./venv --exclude=./.venv --exclude=./.git --exclude=./config.yaml \
    --exclude=./cloud_settings.json --exclude='./*.db' --exclude='./*.db-*' --exclude=./certs \
    --exclude=./data --exclude='./__pycache__' --exclude=./.pytest_cache -cf - . | tar -C "$DIR" -xf -
}

fetch_code() {
  if [[ -n "$SRC" ]]; then
    copy_local_src
    return
  fi
  if [[ -d "$DIR/.git" ]] && command -v git >/dev/null; then
    info "$(L '更新代码' 'Updating code') (git, $BRANCH)"
    if git_dir fetch -q origin "$BRANCH" && git_dir merge -q --ff-only FETCH_HEAD; then
      return
    fi
    warn "$(L 'git 更新失败(网络或本地有改动)，代码保持不变' 'git update failed; code unchanged')"
    return
  fi
  if [[ -f "$DIR/server.py" ]]; then   # 以前用源码包装的：继续用源码包覆盖更新
    fetch_tarball
    return
  fi
  if command -v git >/dev/null; then
    info "$(L '下载代码' 'Cloning') (git clone)"
    if git clone -q --branch "$BRANCH" "${GH_PROXY}https://github.com/${REPO}.git" "$DIR"; then
      return
    fi
    rm -rf "$DIR"
    warn "$(L 'git clone 失败，改用源码包' 'git clone failed; trying tarball')"
  fi
  fetch_tarball
}

setup_venv() {
  info "$(L '安装 Python 依赖…' 'Installing Python dependencies…')"
  [[ -x "$PY" ]] || python3 -m venv "$DIR/venv"
  "$DIR/venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
  "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
}

cfg_set() { "$PY" "$DIR/tools/configure.py" "$CONF" "$@"; }
cfg_get() {  # cfg_get section key
  "$PY" - "$CONF" "$1" "$2" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
v = (d.get(sys.argv[2]) or {}).get(sys.argv[3])
print("" if v is None else (str(v).lower() if isinstance(v, bool) else v))
PY
}
rand() { "$PY" -c 'import secrets; print(secrets.token_urlsafe(32))'; }

ensure_config() {
  if [[ ! -f "$CONF" ]]; then
    cp "$DIR/config.example.yaml" "$CONF"
    cfg_set ingest.token "$(rand)" public_api.token "$(rand)"
    ok "$(L '已生成 config.yaml，上报 token / 取数 token 已随机生成' 'config.yaml created with random tokens')"
  fi
  chmod 600 "$CONF"
}

admin_is_default() {
  (cd "$DIR" && "$PY" -c 'import yaml,auth,sys; c=yaml.safe_load(open("config.yaml",encoding="utf-8")) or {}; sys.exit(0 if "admin" in auth.weak_default_users(c) else 1)')
}

set_admin_password() {
  local p1="" p2=""
  if has_tty; then
    while true; do
      ask p1 "$(L '设置 admin 登录密码(至少10位，直接回车=自动生成): ' 'Set admin password (10+ chars, Enter = generate): ')" silent || break
      [[ -z "$p1" ]] && break
      ask p2 "$(L '再输一次: ' 'Repeat: ')" silent || break
      [[ "$p1" == "$p2" ]] && break
      warn "$(L '两次不一致' 'Mismatch')"
    done
  fi
  if [[ -z "$p1" ]]; then
    p1="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(12))')"
    GENERATED_PW="$p1"
  fi
  local out
  if ! out="$(cd "$DIR" && printf '%s\n' "$p1" | "$PY" auth.py passwd admin --stdin 2>&1)"; then
    die "$(L '密码不合格，请重跑并换一个更复杂的密码' 'Password rejected; rerun with a stronger one'): $out"
  fi
  ok "$(L 'admin 密码已设置' 'admin password set')"
}

# ---------- 网络/证书 ----------
detect_ip() {
  local ip="" u
  for u in https://api.ipify.org https://ifconfig.me/ip https://ipinfo.io/ip https://api.ip.sb/ip; do
    ip="$(curl -fsS -4 --max-time 6 "$u" 2>/dev/null | tr -d '[:space:]' || true)"
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && { echo "$ip"; return; }
  done
  hostname -I 2>/dev/null | awk '{print $1}'
}

make_self_signed() {  # make_self_signed IP
  local ip="$1" crt="$DIR/certs/cert.pem" key="$DIR/certs/key.pem"
  mkdir -p "$DIR/certs"
  if [[ -f "$crt" ]] && openssl x509 -in "$crt" -noout -text 2>/dev/null | grep -q "IP Address:${ip}\b"; then
    return   # 证书已含这个 IP：保持不变(换证书=指纹变，各场地都得重填)
  fi
  [[ -f "$crt" ]] && warn "$(L '公网 IP 变了，重新生成证书：各场地需要更新证书指纹' 'IP changed: new certificate, sites must update the fingerprint')"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -subj "/CN=AntMonitor Cloud" -addext "subjectAltName=IP:${ip},DNS:localhost" \
    -keyout "$key" -out "$crt" 2>/dev/null
  chmod 600 "$key"
  ok "$(L '已生成自签证书' 'Self-signed certificate created') ($ip)"
}

fingerprint() { openssl x509 -in "$DIR/certs/cert.pem" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2; }

open_firewall() {
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    local p; for p in "$@"; do ufw allow "$p/tcp" >/dev/null && ok "ufw allow $p/tcp"; done
  fi
}

install_caddy() {
  command -v caddy >/dev/null && return
  info "$(L '安装 Caddy(自动申请/续期 HTTPS 证书)…' 'Installing Caddy…')"
  if command -v apt-get >/dev/null; then
    if ! apt-get install -y -qq caddy >/dev/null 2>&1; then
      apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
      apt-get update -qq && apt-get install -y -qq caddy >/dev/null
    fi
  else
    local pm; pm="$(command -v dnf || command -v yum)"
    "$pm" install -y -q 'dnf-command(copr)' >/dev/null 2>&1 || true
    "$pm" copr enable -y @caddy/caddy >/dev/null 2>&1 || true
    "$pm" install -y -q caddy >/dev/null
  fi
  command -v caddy >/dev/null || die "$(L 'Caddy 安装失败' 'Caddy install failed')"
}

port_taken_by_other() {  # 80/443 被 nginx/apache 等占用(不是 caddy)
  command -v ss >/dev/null || return 1
  ss -ltnpH "( sport = :$1 )" 2>/dev/null | grep -v caddy | grep -q .
}

write_caddy() {
  local f=/etc/caddy/Caddyfile block
  block="$MARK_BEGIN
${DOMAIN} {
	encode gzip
	request_body {
		max_size 2MB
	}
	reverse_proxy 127.0.0.1:${PORT}
}
$MARK_END"
  mkdir -p /etc/caddy
  if [[ -f "$f" ]] && grep -q "$MARK_BEGIN" "$f"; then
    "$PY" - "$f" "$MARK_BEGIN" "$MARK_END" "$block" <<'PY'
import re, sys
p, b, e, block = sys.argv[1:]
s = open(p, encoding="utf-8").read()
s = re.sub(re.escape(b) + r".*?" + re.escape(e), lambda _m: block, s, flags=re.S)
open(p, "w", encoding="utf-8").write(s)
PY
  elif [[ ! -s "$f" ]] || grep -q "/usr/share/caddy" "$f"; then
    [[ -f "$f" ]] && cp "$f" "$f.bak.$(date +%s)"
    printf '%s\n' "$block" > "$f"            # 默认欢迎页配置：直接换掉
  else
    cp "$f" "$f.bak.$(date +%s)"
    printf '\n%s\n' "$block" >> "$f"         # 已有别的网站：追加，不动原来的
  fi
  caddy validate --config "$f" --adapter caddyfile >/dev/null 2>&1 \
    || die "$(L 'Caddyfile 校验失败，请检查' 'Caddyfile invalid') $f"
  systemctl enable caddy >/dev/null 2>&1 || true
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
}

configure_mode() {
  local cur_port
  cur_port="$(cfg_get server port)"
  PORT="${PORT:-${cur_port:-8900}}"
  case "$MODE" in
    domain)
      [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "$(L '域名格式不对' 'Invalid domain'): $DOMAIN"
      for p in 80 443; do
        if port_taken_by_other "$p"; then
          die "$(L "端口 $p 已被其它程序(nginx/apache?)占用。请停掉它，或自己配反向代理(见 docs/DEPLOY.md)后用 --http 模式" "Port $p is used by another program")"
        fi
      done
      local ip resolved
      ip="$(detect_ip)"
      resolved="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1; exit}')"
      if [[ -z "$resolved" ]]; then
        warn "$(L "域名 $DOMAIN 还解析不到：先在 DNS 里加 A 记录指向 $ip，证书会在解析生效后自动申请" "DNS for $DOMAIN not found yet")"
      elif [[ -n "$ip" && "$resolved" != "$ip" ]]; then
        warn "$(L "域名解析到 $resolved，但本机公网 IP 是 $ip：请确认 A 记录正确(套了 CDN 可忽略)" "DNS points to $resolved but this server is $ip")"
      fi
      install_caddy
      cfg_set server.host 127.0.0.1 server.port "$PORT" server.trust_proxy true \
        server.trusted_proxies '["127.0.0.1/32", "::1/128"]' \
        server.public_url "https://$DOMAIN" server.tls_cert "" server.tls_key "" server.tls_self_signed false
      write_caddy
      open_firewall 80 443
      ;;
    ip)
      PUBLIC_IP="${PUBLIC_IP:-$(detect_ip)}"
      [[ "$PUBLIC_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "$(L '探测不到公网 IP，请用 --ip 指定' 'Cannot detect public IP; use --ip')"
      make_self_signed "$PUBLIC_IP"
      cfg_set server.host 0.0.0.0 server.port "$PORT" server.trust_proxy false \
        server.public_url "https://$PUBLIC_IP:$PORT" \
        server.tls_cert "$DIR/certs/cert.pem" server.tls_key "$DIR/certs/key.pem" server.tls_self_signed true
      open_firewall "$PORT"
      ;;
    http)
      PUBLIC_IP="${PUBLIC_IP:-$(detect_ip)}"
      warn "$(L '明文 HTTP：只适合内网测试，公网上 token 和密码会被看到' 'Plain HTTP: LAN testing only')"
      cfg_set server.host 0.0.0.0 server.port "$PORT" server.trust_proxy false \
        server.public_url "http://$PUBLIC_IP:$PORT" server.tls_cert "" server.tls_key "" server.tls_self_signed false
      open_firewall "$PORT"
      ;;
  esac
}

current_mode() {
  local url; url="$(cfg_get server public_url)"
  if [[ "$(cfg_get server tls_self_signed)" == "true" ]]; then echo ip
  elif [[ "$url" == https://* ]]; then echo domain
  elif [[ -n "$url" ]]; then echo http
  else echo ""; fi
}

# ---------- 服务 ----------
ensure_user() {
  id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --no-create-home \
    --shell /usr/sbin/nologin "$SVC_USER" 2>/dev/null || useradd -r -d "$DIR" -s /sbin/nologin "$SVC_USER"
  chown -R "$SVC_USER:$SVC_USER" "$DIR"
  chmod 700 "$DIR"
  [[ -f "$DIR/cloud_settings.json" ]] && chmod 600 "$DIR/cloud_settings.json"
  [[ -f "$DIR/certs/key.pem" ]] && chmod 600 "$DIR/certs/key.pem"
  return 0
}

write_service() {
  local cap=""
  [[ "${PORT:-8900}" -lt 1024 ]] && cap="AmbientCapabilities=CAP_NET_BIND_SERVICE"
  cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=AntMonitor Cloud
After=network-online.target
Wants=network-online.target

[Service]
User=${SVC_USER}
WorkingDirectory=${DIR}
ExecStart=${DIR}/venv/bin/python server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes
${cap}

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1
}

wait_healthy() {
  local scheme="http" i
  [[ "$(cfg_get server tls_self_signed)" == "true" || -n "$(cfg_get server tls_cert)" ]] && scheme="https"
  for i in $(seq 1 30); do
    curl -fsk --max-time 2 "${scheme}://127.0.0.1:${PORT:-8900}/api/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

show_info() {
  local url mode fp
  url="$(cfg_get server public_url)"; mode="$(current_mode)"
  echo
  echo "${BLD}========================================${RST}"
  echo "${BLD}  AntMonitor Cloud${RST}"
  echo "${BLD}========================================${RST}"
  echo "  $(L '面板地址' 'Panel'):  ${BLD}${url:-?}${RST}"
  if [[ "$mode" == "ip" ]]; then
    fp="$(fingerprint)"
    echo "  $(L '证书指纹' 'Fingerprint'): ${fp}"
    echo "  $(L '(自签证书：浏览器提示"不安全"时核对上面的指纹一致再继续；场地端要填这个指纹)' '(self-signed: compare this fingerprint when the browser warns; sites need it)')"
  fi
  echo "  $(L '登录账号' 'Login'):  admin${GENERATED_PW:+  /  ${BLD}${GENERATED_PW}${RST}  $(L '(自动生成，请马上记下并在网页里修改)' '(generated, note it down)')}"
  echo "  $(L '场地接入' 'Onboarding'): $(L '登录面板 → 右上「🔌 场地接入」→ 复制接入码 → 场地端面板粘贴' 'Panel → 🔌 Site onboarding → copy the connect code into each site')"
  echo "  $(L '服务管理' 'Service'):  systemctl status ${SERVICE}  |  journalctl -u ${SERVICE} -f"
  case "$mode" in
    domain) echo "  $(L '云服务器安全组/防火墙放行' 'Open in cloud firewall'): TCP 80, 443" ;;
    *) echo "  $(L '云服务器安全组/防火墙放行' 'Open in cloud firewall'): TCP $(cfg_get server port)" ;;
  esac
  [[ "$mode" == "domain" ]] && echo "  $(L '国内服务器用域名须已 ICP 备案，否则 80/443 会被拦截；没备案请改用 IP 模式' 'Mainland China servers need ICP filing for domains')"
  echo
}

do_install() {
  install_packages
  local fresh=0
  [[ -f "$CONF" ]] || fresh=1
  fetch_code
  [[ -f "$DIR/server.py" ]] || die "$(L '代码下载不完整' 'Incomplete download')"
  setup_venv
  ensure_config
  # 首次安装没给 --domain：默认 IP 模式。已有配置且没给模式参数：不动现有网络配置
  # (比如原来自己配的 nginx 反代，重跑本脚本只更新代码)
  [[ -z "$MODE" && $fresh -eq 1 ]] && MODE="ip"
  if [[ -n "$MODE" ]]; then configure_mode; fi
  PORT="$(cfg_get server port)"
  admin_is_default && set_admin_password
  ensure_user
  write_service
  systemctl restart "$SERVICE"
  if wait_healthy; then ok "$(L '服务已启动' 'Service is running')"
  else warn "$(L '服务没起来，查看' 'Service not healthy, see'): journalctl -u ${SERVICE} -n 50 --no-pager"; fi
  show_info
}

do_update() {
  [[ -f "$DIR/server.py" ]] || die "$(L '还没安装' 'Not installed'): $DIR"
  command -v python3 >/dev/null || install_packages
  fetch_code
  setup_venv
  PORT="$(cfg_get server port)"
  ensure_user
  write_service
  systemctl restart "$SERVICE"
  wait_healthy && ok "$(L '已更新并重启' 'Updated and restarted')" \
    || warn "journalctl -u ${SERVICE} -n 50 --no-pager"
}

do_uninstall() {
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload
  if [[ -f /etc/caddy/Caddyfile ]] && grep -q "$MARK_BEGIN" /etc/caddy/Caddyfile; then
    sed -i "/$MARK_BEGIN/,/$MARK_END/d" /etc/caddy/Caddyfile
    systemctl reload caddy 2>/dev/null || true
  fi
  ok "$(L '服务已卸载' 'Service removed')"
  local a=""
  ask a "$(L "同时删除 $DIR (含数据库和配置，不可恢复)？[y/N] " "Also delete $DIR (data + config)? [y/N] ")" || true
  if [[ "$a" =~ ^[Yy]$ ]]; then rm -rf "$DIR"; ok "$(L '已删除' 'Deleted') $DIR"
  else echo "$(L '数据目录保留' 'Data kept'): $DIR"; fi
}

case "$ACTION" in
  install) do_install ;;
  update) do_update ;;
  uninstall) do_uninstall ;;
  reset-password)
    [[ -x "$PY" ]] || die "$(L '还没安装' 'Not installed')"
    set_admin_password; chown "$SVC_USER:$SVC_USER" "$DIR/cloud_settings.json" 2>/dev/null || true
    show_info ;;
  info) [[ -f "$CONF" ]] || die "$(L '还没安装' 'Not installed')"; PORT="$(cfg_get server port)"; show_info
        systemctl --no-pager --lines=0 status "$SERVICE" || true ;;
esac
