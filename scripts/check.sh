#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

require_file() {
  local path="$1"
  if [ ! -e "$path" ]; then
    echo "[x] missing required file: $path" >&2
    exit 1
  fi
}

require_file "$BASE_DIR/failover_realm.py"
require_file "$BASE_DIR/failover_webui.py"
require_file "$BASE_DIR/failover_webui_app.py"
require_file "$BASE_DIR/settings.json"
require_file "$BASE_DIR/webui_assets/index.html"

if [ ! -d "$BASE_DIR/webui_assets/assets" ] || [ -z "$(find "$BASE_DIR/webui_assets/assets" -maxdepth 1 -type f -print -quit)" ]; then
  echo "[x] missing built webui assets under webui_assets/assets" >&2
  exit 1
fi

python3 - <<'PY'
import json
from pathlib import Path
import failover_realm as fr

for path in ["settings.json", "settings.multi-domain.example.json"]:
    json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"[+] json ok: {path}")

fr.load_settings()
print("[+] settings.json load_settings ok")
PY

python3 failover_realm.py --help >/dev/null
echo "[+] failover_realm.py --help ok"

python3 failover_webui.py --help >/dev/null
echo "[+] failover_webui.py --help ok"

python3 - <<'PY'
import py_compile
for path in ["failover_realm.py", "failover_webui.py", "failover_webui_app.py"]:
    py_compile.compile(path, doraise=True)
    print(f"[+] py_compile ok: {path}")
PY

echo "[+] package check complete"
