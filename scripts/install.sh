#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="$BASE_DIR/systemd"
TARGET_DIR="/opt/aliMonitor"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[x] missing required command: $cmd" >&2
    exit 1
  fi
}

require_file() {
  local path="$1"
  if [ ! -e "$path" ]; then
    echo "[x] missing required file: $path" >&2
    exit 1
  fi
}

if [ "$(id -u)" -ne 0 ]; then
  echo "[x] install.sh must run as root" >&2
  exit 1
fi

require_cmd python3
require_cmd systemctl
require_cmd ssh
require_cmd scp
require_cmd sshpass

require_file "$BASE_DIR/failover_realm.py"
require_file "$BASE_DIR/failover_webui.py"
require_file "$BASE_DIR/failover_webui_app.py"
require_file "$BASE_DIR/config.toml"
require_file "$BASE_DIR/iepl_config.toml"
require_file "$BASE_DIR/webui_assets/index.html"
require_file "$SYSTEMD_DIR/aliMonitor.service"
require_file "$SYSTEMD_DIR/aliMonitor-webui.service"

if [ ! -d "$BASE_DIR/webui_assets/assets" ] || [ -z "$(find "$BASE_DIR/webui_assets/assets" -maxdepth 1 -type f -print -quit)" ]; then
  echo "[x] missing built webui assets under $BASE_DIR/webui_assets/assets" >&2
  exit 1
fi

settings_ready() {
  if [ ! -e "$BASE_DIR/settings.json" ]; then
    return 1
  fi
  if (
    cd "$BASE_DIR"
    python3 - <<'PY' >/dev/null 2>&1
import failover_realm as fr
fr.load_settings()
PY
  )
  then
    return 0
  fi
  return 1
}

if [ "$BASE_DIR" != "$TARGET_DIR" ]; then
  echo "[x] current directory is $BASE_DIR, expected $TARGET_DIR" >&2
  echo "    copy this whole directory to $TARGET_DIR first, then run install.sh again" >&2
  exit 1
fi

install -m 0644 "$SYSTEMD_DIR/aliMonitor.service" /etc/systemd/system/aliMonitor.service
install -m 0644 "$SYSTEMD_DIR/aliMonitor-webui.service" /etc/systemd/system/aliMonitor-webui.service

systemctl daemon-reload
systemctl enable --now aliMonitor-webui.service

for legacy_service in failover-realm.service failover-webui.service; do
  systemctl disable --now "$legacy_service" >/dev/null 2>&1 || true
done

if settings_ready; then
  systemctl enable --now aliMonitor.service
  echo "[+] monitor service enabled"
else
  systemctl disable --now aliMonitor.service >/dev/null 2>&1 || true
  echo "[!] settings.json missing or invalid, started WebUI only"
  echo "[!] open the WebUI, complete initialization, then save settings there"
fi

echo "[+] install complete"
echo "[*] service status:"
echo "    systemctl status aliMonitor-webui.service --no-pager"
echo "    systemctl status aliMonitor.service --no-pager"
echo "[*] log tail:"
echo "    journalctl -u aliMonitor-webui.service -n 100 --no-pager"
echo "    journalctl -u aliMonitor.service -n 100 --no-pager"
