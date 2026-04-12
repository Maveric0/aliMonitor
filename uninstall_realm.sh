#!/usr/bin/env bash
set -euo pipefail

echo "[1/6] stopping ali-forward.service and realm.service if present"
systemctl disable --now ali-forward.service >/dev/null 2>&1 || true
systemctl disable --now realm.service >/dev/null 2>&1 || true

echo "[2/6] clearing iptables forwarding rules"
if command -v iptables-restore >/dev/null 2>&1; then
cat <<'EOF' | iptables-restore
*nat
:PREROUTING ACCEPT [0:0]
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
COMMIT
*filter
:INPUT ACCEPT [0:0]
:FORWARD ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
COMMIT
EOF
fi

echo "[3/6] removing systemd units and helper files"
rm -f /etc/systemd/system/ali-forward.service
rm -f /etc/systemd/system/realm.service
rm -f /usr/local/bin/uninstall-realm
rm -f /etc/ali-forward.rules
rm -f /etc/sysctl.d/99-ali-forward.conf

echo "[4/6] removing realm files if they exist"
rm -f /usr/local/bin/realm
rm -rf /etc/realm

echo "[5/6] disabling IPv4 forwarding"
sysctl -w net.ipv4.ip_forward=0 >/dev/null || true

echo "[6/6] reloading systemd"
systemctl daemon-reload || true

echo "[+] uninstall finished"
