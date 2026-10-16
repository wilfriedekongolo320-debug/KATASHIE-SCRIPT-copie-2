#!/bin/bash
# ============================================================
#  Nexus Tunnel Web — Installer
#  Installs Node.js, dependencies, builds TypeScript,
#  creates systemd service and config.
# ============================================================

set -uo pipefail

NEXUS_WEB_DIR="/opt/nexus-tunnel-web"
CONFIG_DIR="/etc/nexus-tunnel-web"
CONFIG_FILE="$CONFIG_DIR/config.json"
SERVICE_FILE="/etc/systemd/system/nexus-web.service"
NODE_MIN_VERSION=18

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

log_info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

require_root() {
  [ "$EUID" -ne 0 ] && { log_error "Run as root."; exit 1; }
}

# ─── Find available port ─────────────────────────────────────────────────────
find_available_port() {
  local candidates=(2087 2096 8787 3001 9090 8088 9180)
  for port in "${candidates[@]}"; do
    if ! ss -tlnp 2>/dev/null | grep -q ":$port " && \
       ! netstat -tlnp 2>/dev/null | grep -q ":$port "; then
      echo "$port"
      return 0
    fi
  done
  echo "2087"  # fallback
}

# ─── Install Node.js ─────────────────────────────────────────────────────────
install_nodejs() {
  if command -v node &>/dev/null; then
    local ver
    ver=$(node -e "process.stdout.write(process.version.replace('v','').split('.')[0])")
    if [ "$ver" -ge "$NODE_MIN_VERSION" ]; then
      log_ok "Node.js $(node --version) already installed."
      return 0
    fi
    log_warn "Node.js $ver found but need >= $NODE_MIN_VERSION. Upgrading..."
  fi

  log_info "Installing Node.js $NODE_MIN_VERSION..."
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MIN_VERSION}.x" | bash - >/dev/null 2>&1
  apt-get install -y nodejs >/dev/null 2>&1
  log_ok "Node.js $(node --version) installed."
}

# ─── Main install ─────────────────────────────────────────────────────────────
main() {
  require_root
  clear

  echo -e "${CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
  echo -e "${CYAN}┃${NC} ${BOLD}        NEXUS TUNNEL WEB — INSTALLER             ${NC} ${CYAN}┃${NC}"
  echo -e "${CYAN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
  echo ""

  # ── Ask for admin credentials ──
  local admin_user admin_pass admin_pass2

  while true; do
    read -rp " ➤ Admin Username : " admin_user
    [[ -n "$admin_user" ]] && break
    log_error "Username cannot be empty."
  done

  while true; do
    read -srp " ➤ Admin Password : " admin_pass; echo ""
    [[ ${#admin_pass} -ge 6 ]] && break
    log_error "Password must be at least 6 characters."
  done

  read -srp " ➤ Confirm Password : " admin_pass2; echo ""
  if [[ "$admin_pass" != "$admin_pass2" ]]; then
    log_error "Passwords do not match. Aborting."
    exit 1
  fi

  # ── Detect port ──
  local port
  port=$(find_available_port)
  log_info "Using port: $port"

  # ── Generate JWT secret ──
  local jwt_secret
  jwt_secret=$(openssl rand -hex 48 2>/dev/null || head -c 48 /dev/urandom | base64 | tr -d '=\n+/')

  # ── Install system dependencies ──
  log_info "Installing system dependencies..."
  apt-get update -y -q >/dev/null 2>&1
  apt-get install -y -q curl git build-essential python3 make chrony >/dev/null 2>&1
  # Ensure chrony is running so the server clock is always accurate
  systemctl enable chrony --now 2>/dev/null || systemctl enable chronyd --now 2>/dev/null || true
  log_ok "System dependencies installed. NTP (chrony) enabled."
  install_nodejs

  # ── Copy source files ──
  log_info "Deploying Nexus Tunnel Web to $NEXUS_WEB_DIR..."
  mkdir -p "$NEXUS_WEB_DIR"

  # Determine source dir (where install.sh lives)
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cp -r "$SCRIPT_DIR"/* "$NEXUS_WEB_DIR/"
  cd "$NEXUS_WEB_DIR"

  # ── Build React frontend ──
  if [ -d "$NEXUS_WEB_DIR/frontend" ]; then
    log_info "Building React frontend..."
    cd "$NEXUS_WEB_DIR/frontend"
    npm install --quiet 2>&1 | tail -5
    if ! npm run build 2>&1; then
      log_warn "Frontend build had warnings, continuing..."
    else
      log_ok "React frontend built successfully."
    fi
    cd "$NEXUS_WEB_DIR"
  fi

  # ── Install Node.js dependencies (server) ──
  log_info "Installing server Node.js dependencies..."
  npm install --production=false --quiet 2>&1 | tail -5

  # ── Build TypeScript server ──
  log_info "Compiling TypeScript server..."
  if ! npm run build 2>&1; then
    log_warn "TypeScript build had warnings, checking dist..."
    if [ ! -f "$NEXUS_WEB_DIR/dist/server/index.js" ]; then
      log_error "Build failed. Check TypeScript errors."
      exit 1
    fi
  fi
  log_ok "TypeScript compiled successfully."

  # ── Create config ──
  log_info "Writing configuration..."
  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"

  cat > "$CONFIG_FILE" <<JSON
{
  "port": $port,
  "admin_user": "$admin_user",
  "admin_password": "$admin_pass",
  "jwt_secret": "$jwt_secret",
  "scripts_dir": "/usr/local/sbin",
  "db_dir": "$CONFIG_DIR"
}
JSON
  chmod 600 "$CONFIG_FILE"
  log_ok "Config written to $CONFIG_FILE"

  # ── Create systemd service ──
  log_info "Creating systemd service..."
  cat > "$SERVICE_FILE" <<SVC
[Unit]
Description=Nexus Tunnel Web Panel
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$NEXUS_WEB_DIR
ExecStart=/usr/bin/node $NEXUS_WEB_DIR/dist/server/index.js
Restart=always
RestartSec=5
StartLimitBurst=10
StartLimitIntervalSec=60
Environment=NODE_ENV=production
Environment=NEXUS_CONFIG=$CONFIG_FILE
Environment=NEXUS_DB_DIR=$CONFIG_DIR
Environment=NEXUS_JWT_SECRET=$jwt_secret
Environment=NEXUS_ADMIN_USER=$admin_user
Environment=NEXUS_ADMIN_PASS=$admin_pass
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC

  chmod 600 "$SERVICE_FILE"

  # ── External health watchdog ──
  # If the Node.js process hangs (event loop blocked) and doesn't respond,
  # this cron restarts the service automatically (runs every minute).
  cat > /usr/local/bin/nexus-web-watchdog.sh <<'WATCHDOG'
#!/bin/bash
CONFIG_FILE="/etc/nexus-tunnel-web/config.json"
# Extract port from config using grep + awk (no Python required)
PORT=$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$CONFIG_FILE" 2>/dev/null | awk -F: '{gsub(/[^0-9]/,"",$2); print $2}')
[ -z "$PORT" ] && PORT=2087
FAIL_COUNT_FILE="/tmp/.nexus-web-watchdog-fails"
if curl -sf --max-time 8 "http://localhost:${PORT}/api/health" > /dev/null 2>&1; then
  rm -f "$FAIL_COUNT_FILE"
else
  count=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$FAIL_COUNT_FILE"
  if [ "$count" -ge 3 ]; then
    echo "[$(date -u)] Health check failed ${count} times — restarting nexus-web" >> /var/log/nexus-web-watchdog.log
    systemctl restart nexus-web
    rm -f "$FAIL_COUNT_FILE"
  fi
fi
WATCHDOG
  chmod 755 /usr/local/bin/nexus-web-watchdog.sh

  # Install cron job (runs every minute)
  echo "* * * * * root /usr/local/bin/nexus-web-watchdog.sh" > /etc/cron.d/nexus-web-watchdog
  chmod 644 /etc/cron.d/nexus-web-watchdog
  log_ok "Health watchdog cron installed (/etc/cron.d/nexus-web-watchdog)"

  # ── Enable and start service ──
  systemctl daemon-reload
  systemctl enable nexus-web
  systemctl restart nexus-web
  sleep 2

  if systemctl is-active --quiet nexus-web; then
    log_ok "Nexus Tunnel Web service is running!"
  else
    log_warn "Service may not be running. Check: journalctl -u nexus-web -n 30"
  fi

  # ── Install shell menu command ──
  ln -sf /usr/local/sbin/web /usr/local/sbin/web 2>/dev/null || true

  # ── Done ──
  local server_ip
  server_ip=$(curl -s4 ipv4.icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')

  echo ""
  echo -e "${CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
  echo -e "${CYAN}┃${NC} ${GREEN}      NEXUS TUNNEL WEB — INSTALLED!              ${NC} ${CYAN}┃${NC}"
  echo -e "${CYAN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
  echo -e "${CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
  echo -e "${CYAN}┃${NC}  URL      : ${GREEN}http://$server_ip:$port${NC}"
  echo -e "${CYAN}┃${NC}  Admin    : ${GREEN}$admin_user${NC}"
  echo -e "${CYAN}┃${NC}  Config   : $CONFIG_FILE"
  echo -e "${CYAN}┃${NC}  Service  : systemctl status nexus-web"
  echo -e "${CYAN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
  echo ""
}

main "$@"
