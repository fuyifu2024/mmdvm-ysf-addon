#!/usr/bin/env bash
# Read-only status report for the MMDVM YSF add-on.
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
section() { printf '\n== %s ==\n' "$*"; }

section "services"
for unit in ysfgateway mmdvmhost oled-display; do
	active="$(systemctl is-active "$unit.service" 2>/dev/null || true)"
	enabled="$(systemctl is-enabled "$unit.service" 2>/dev/null || true)"
	restarts="$(systemctl show -p NRestarts --value "$unit.service" 2>/dev/null || true)"
	printf '  %-14s active=%-8s enabled=%-8s restarts=%s\n' \
		"$unit" "${active:-missing}" "${enabled:-missing}" "${restarts:-?}"
done

section "processes"
ps -o pid,user,stat,etime,cmd -C MMDVM-Host,YSFGateway 2>/dev/null || true
pgrep -af 'oled_display.py' 2>/dev/null | head -2 || true

section "gateway link"
if [[ -r /var/log/mmdvm/ysfgateway.log ]]; then
	grep -E 'Loaded [0-9]+ YSF reflectors|Linked to|Link successful|Unable to load|Link lost' \
		/var/log/mmdvm/ysfgateway.log | tail -8
else
	echo "  cannot read /var/log/mmdvm/ysfgateway.log (try sudo)"
fi

section "modem / recent RF"
if [[ -r /var/log/mmdvm/mmdvmhost.log ]]; then
	grep -E 'MMDVM protocol version|YSF, received RF|YSF, received network|Opening the MMDVM' \
		/var/log/mmdvm/mmdvmhost.log | tail -8
else
	echo "  cannot read /var/log/mmdvm/mmdvmhost.log (try sudo)"
fi

section "UDP ports"
if command -v ss >/dev/null 2>&1; then
	ss -lun 2>/dev/null | grep -E ':(3200|4200|42000)[[:space:]]' || echo "  no MMDVM UDP listeners found"
else
	echo "  ss is missing"
fi

section "hardware"
[[ -e /dev/ttyAMA0 ]] && ls -l /dev/ttyAMA0 || echo "  /dev/ttyAMA0 missing"
[[ -e /dev/i2c-1 ]] && ls -l /dev/i2c-1 || echo "  /dev/i2c-1 missing"
if command -v i2cdetect >/dev/null 2>&1; then
	if i2cdetect -y 1 2>/dev/null | grep -qi '3c'; then
		echo "  OLED at 0x3C responds"
	else
		echo "  OLED at 0x3C not detected (or no i2c permission)"
	fi
fi
if command -v dtoverlay >/dev/null 2>&1; then
	dtoverlay -l 2>/dev/null | grep -q 'uart0-pi5' \
		&& echo "  uart0-pi5 overlay is loaded" \
		|| echo "  uart0-pi5 overlay is not loaded at runtime (config.txt may still load it on boot)"
fi

section "logs"
ls -l /var/log/mmdvm 2>/dev/null || true
