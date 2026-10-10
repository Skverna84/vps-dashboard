#!/usr/bin/env bash
# ==============================================================================
# VPS Control & Monitoring Dashboard - Uninstaller
# ==============================================================================

set -e

INSTALL_DIR="/opt/vps-dashboard"
SERVICE_NAME="vps-dashboard"

RED="\033[0;31m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
RESET="\033[0m"

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}[ERROR] This uninstaller must be run as root (use sudo).${RESET}"
    exit 1
fi

echo -e "${BLUE}Stopping and disabling ${SERVICE_NAME} service...${RESET}"
systemctl stop "${SERVICE_NAME}.service" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true

echo -e "${BLUE}Removing systemd service file...${RESET}"
rm -f "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null || true
systemctl daemon-reload

echo -e "${BLUE}Removing installation files from ${INSTALL_DIR}...${RESET}"
rm -rf "${INSTALL_DIR}" 2>/dev/null || true

echo -e "${GREEN}VPS Dashboard has been completely uninstalled.${RESET}"
