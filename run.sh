#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="/opt/aliMonitor"
LEGACY_TARGET_DIR="$TARGET_DIR/deploy/aliMonitor"
PRESERVE_FILES=(
  settings.json
  komari_state.json
  forward_installed.json
  tag_cache.json
  config.toml
  iepl_config.toml
)

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

ensure_linux() {
  if [ "$(uname -s)" != "Linux" ]; then
    echo "[x] run.sh only supports Linux servers" >&2
    exit 1
  fi
}

ensure_root() {
  if [ "$(id -u)" -eq 0 ]; then
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    echo "[*] re-running with sudo"
    exec sudo bash "$0" "$@"
  fi
  echo "[x] run.sh must run as root or through sudo" >&2
  exit 1
}

verify_source_tree() {
  require_file "$SOURCE_DIR/failover_realm.py"
  require_file "$SOURCE_DIR/failover_webui.py"
  require_file "$SOURCE_DIR/failover_webui_app.py"
  require_file "$SOURCE_DIR/settings.multi-domain.example.json"
  require_file "$SOURCE_DIR/config.toml"
  require_file "$SOURCE_DIR/iepl_config.toml"
  require_file "$SOURCE_DIR/scripts/install.sh"
  require_file "$SOURCE_DIR/scripts/check.sh"
  require_file "$SOURCE_DIR/systemd/aliMonitor.service"
  require_file "$SOURCE_DIR/systemd/aliMonitor-webui.service"
  require_file "$SOURCE_DIR/webui_assets/index.html"
  require_file "$SOURCE_DIR/webui_assets/app.css"
  require_file "$SOURCE_DIR/webui_assets/app.js"
}

copy_preserved_file() {
  local rel="$1"
  local bases=("$TARGET_DIR" "$LEGACY_TARGET_DIR")
  local base
  if [ "$rel" = "config.toml" ] || [ "$rel" = "iepl_config.toml" ]; then
    bases=("$LEGACY_TARGET_DIR" "$TARGET_DIR")
  fi
  for base in "${bases[@]}"; do
    if [ -e "$base/$rel" ]; then
      mkdir -p "$backup_dir/$(dirname "$rel")"
      cp -a "$base/$rel" "$backup_dir/$rel"
      return
    fi
  done
}

migrate_legacy_runtime_files() {
  if [ ! -d "$LEGACY_TARGET_DIR" ]; then
    return
  fi
  local rel
  for rel in "${PRESERVE_FILES[@]}"; do
    if [ "$rel" = "config.toml" ] || [ "$rel" = "iepl_config.toml" ]; then
      if [ -e "$LEGACY_TARGET_DIR/$rel" ]; then
        mkdir -p "$TARGET_DIR/$(dirname "$rel")"
        cp -a "$LEGACY_TARGET_DIR/$rel" "$TARGET_DIR/$rel"
      fi
    elif [ ! -e "$TARGET_DIR/$rel" ] && [ -e "$LEGACY_TARGET_DIR/$rel" ]; then
      mkdir -p "$TARGET_DIR/$(dirname "$rel")"
      cp -a "$LEGACY_TARGET_DIR/$rel" "$TARGET_DIR/$rel"
    fi
  done
}

settings_ready() {
  if [ ! -e "$TARGET_DIR/settings.json" ]; then
    return 1
  fi
  if (
    cd "$TARGET_DIR"
    python3 - <<'PY' >/dev/null 2>&1
import failover_realm as fr
fr.load_settings()
PY
  ); then
    return 0
  fi
  return 1
}

sync_project() {
  if [ "$SOURCE_DIR" = "$TARGET_DIR" ]; then
    return
  fi

  require_cmd tar

  mkdir -p "$TARGET_DIR"
  local backup_dir
  backup_dir="$(mktemp -d)"
  trap 'rm -rf "$backup_dir"' EXIT

  for rel in "${PRESERVE_FILES[@]}"; do
    copy_preserved_file "$rel"
  done

  find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

  (
    cd "$SOURCE_DIR"
    tar \
      --exclude='./.git' \
      --exclude='./__pycache__' \
      --exclude='./.pytest_cache' \
      --exclude='./settings.json' \
      --exclude='./komari_state.json' \
      --exclude='./forward_installed.json' \
      --exclude='./tag_cache.json' \
      -cf - .
  ) | (
    cd "$TARGET_DIR"
    tar -xf -
  )

  if [ -n "$(find "$backup_dir" -mindepth 1 -print -quit)" ]; then
    (
      cd "$backup_dir"
      tar -cf - .
    ) | (
      cd "$TARGET_DIR"
      tar -xf -
    )
  fi

  trap - EXIT
  rm -rf "$backup_dir"
}

main() {
  ensure_linux
  ensure_root "$@"
  verify_source_tree
  sync_project
  migrate_legacy_runtime_files

  cd "$TARGET_DIR"
  if settings_ready; then
    bash scripts/check.sh
  fi
  bash scripts/install.sh

  echo "[+] deployment complete"
  echo "[*] service status:"
  echo "    systemctl status aliMonitor.service --no-pager"
  echo "    systemctl status aliMonitor-webui.service --no-pager"
}

main "$@"
