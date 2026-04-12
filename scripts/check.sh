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
require_file "$BASE_DIR/webui_assets/app.css"
require_file "$BASE_DIR/webui_assets/app.js"

python3 - <<'PY'
import json
from pathlib import Path

for path in ["settings.json", "settings.multi-domain.example.json"]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"[+] json ok: {path}")
    if path == "settings.json" and "frontend_domains" not in data:
        raise SystemExit("[x] settings.json missing frontend_domains")
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
