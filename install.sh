#!/usr/bin/env bash
# ==============================================================================
# VPS Control & Monitoring Dashboard - Automated Installer
# GitHub: https://github.com/Skverna84/vps-dashboard
# ==============================================================================

set -e

# Default settings
PORT="8080"
USERNAME="admin"
PASSWORD=""
INSTALL_DIR="/opt/vps-dashboard"
SERVICE_NAME="vps-dashboard"
GITHUB_REPO="Skverna84/vps-dashboard"
BRANCH="main"

# Text styles
BOLD="\033[1m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

echo -e "${BLUE}${BOLD}"
echo "=========================================================="
echo "    VPS Control & Monitoring Dashboard - Installer        "
echo "=========================================================="
echo -e "${RESET}"

# 1. Root check
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}[ERROR] This installer must be run as root (use sudo).${RESET}"
    exit 1
fi

# 2. Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --port) PORT="$2"; shift ;;
        --user) USERNAME="$2"; shift ;;
        --pass) PASSWORD="$2"; shift ;;
        -h|--help)
            echo "Usage: bash install.sh [options]"
            echo "Options:"
            echo "  --port <port>     Set web port (default: 8080)"
            echo "  --user <username> Set admin login (default: admin)"
            echo "  --pass <password> Set admin password (default: random 16-char)"
            echo "  -h, --help        Show this help message"
            exit 0
            ;;
        *) echo -e "${YELLOW}Unknown parameter: $1${RESET}"; ;;
    esac
    shift
done

# 3. Check or generate password
if [ -z "$PASSWORD" ]; then
    PASSWORD=$(tr -dc A-Za-z0-9 </dev/urandom | head -c 16 2>/dev/null || openssl rand -base64 12 | tr -dc A-Za-z0-9 | head -c 16)
fi

# 4. Check dependencies (Python 3, curl)
echo -e "${BLUE}[1/5] Checking dependencies...${RESET}"
if ! command -v python3 &>/dev/null; then
    echo -e "${YELLOW}Python 3 not found. Installing...${RESET}"
    if command -v apt-get &>/dev/null; then
        apt-get update -qq && apt-get install -y python3 curl
    elif command -v yum &>/dev/null; then
        yum install -y python3 curl
    elif command -v dnf &>/dev/null; then
        dnf install -y python3 curl
    elif command -v pacman &>/dev/null; then
        pacman -Sy --noconfirm python curl
    else
        echo -e "${RED}[ERROR] Unsupported package manager. Please install Python 3 manually.${RESET}"
        exit 1
    fi
fi

# 5. Create installation directory
echo -e "${BLUE}[2/5] Setting up directory: ${INSTALL_DIR}...${RESET}"
mkdir -p "$INSTALL_DIR"

# 6. Install files (from local directory or download from GitHub)
echo -e "${BLUE}[3/5] Deploying dashboard files...${RESET}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"

if [ -f "${SCRIPT_DIR}/app.py" ]; then
    cp "${SCRIPT_DIR}/app.py" "${INSTALL_DIR}/app.py"
    cp "${SCRIPT_DIR}/vps-dashboard.service" "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null || true
else
    # Download from GitHub
    RAW_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/${BRANCH}"
    curl -sSL "${RAW_URL}/app.py" -o "${INSTALL_DIR}/app.py"
    curl -sSL "${RAW_URL}/vps-dashboard.service" -o "/etc/systemd/system/${SERVICE_NAME}.service"
fi

chmod +x "${INSTALL_DIR}/app.py"

# Write config.json
cat << EOF > "${INSTALL_DIR}/config.json"
{
  "port": ${PORT},
  "username": "${USERNAME}",
  "password": "${PASSWORD}",
  "title": "VPS Control & Monitor",
  "refresh_interval_sec": 3
}
EOF
chmod 600 "${INSTALL_DIR}/config.json"

# Write systemd service if not present
if [ ! -f "/etc/systemd/system/${SERVICE_NAME}.service" ]; then
    cat << EOF > "/etc/systemd/system/${SERVICE_NAME}.service"
[Unit]
Description=VPS Control & Monitoring Web Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/app.py
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
fi

# 7. Start systemd service
echo -e "${BLUE}[4/5] Enabling and starting systemd service...${RESET}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl restart "${SERVICE_NAME}.service"

# 8. Firewall (UFW)
echo -e "${BLUE}[5/5] Configuring firewall...${RESET}"
if command -v ufw &>/dev/null; then
    if ufw status | grep -qw "active"; then
        ufw allow "${PORT}/tcp" comment "VPS Web Dashboard" >/dev/null 2>&1 || true
        echo -e "${GREEN}Port ${PORT}/tcp allowed in UFW.${RESET}"
    fi
fi

# Detect external IP
SERVER_IP=$(curl -s4 -m 5 https://api.ipify.org || curl -s4 -m 5 https://ifconfig.me || hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}${BOLD}==========================================================${RESET}"
echo -e "${GREEN}${BOLD}      VPS Dashboard successfully installed & running!     ${RESET}"
echo -e "${GREEN}${BOLD}==========================================================${RESET}"
echo ""
echo -e "  🌐 ${BOLD}Dashboard URL:${RESET}  ${BLUE}http://${SERVER_IP}:${PORT}${RESET}"
echo -e "  👤 ${BOLD}Username:${RESET}       ${YELLOW}${USERNAME}${RESET}"
echo -e "  🔑 ${BOLD}Password:${RESET}       ${YELLOW}${PASSWORD}${RESET}"
echo ""
echo -e "  ℹ️  ${BOLD}Service status:${RESET} systemctl status ${SERVICE_NAME}"
echo -e "  ⚙️  ${BOLD}Config file:${RESET}    ${INSTALL_DIR}/config.json"
echo -e "${GREEN}==========================================================${RESET}"
echo ""
