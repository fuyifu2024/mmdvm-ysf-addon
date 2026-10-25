#!/usr/bin/env bash
# Read-only status report for the MMDVM YSF add-on.
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$REPO_DIR/scripts/lib.sh"
PLATFORM="$(detect_platform)"
SYSTEM_ENV=/etc/mmdvm/system.env
MODEM_PORT="$(config_get MODEM_PORT "$SYSTEM_ENV")"
MODEM_PORT="${MODEM_PORT:-unknown}"
I2C_BUS="$(config_get I2C_BUS "$SYSTEM_ENV")"
I2C_BUS="${I2C_BUS:-1}"
I2C_ADDRESS="$(config_get I2C_ADDRESS "$SYSTEM_ENV")"
I2C_ADDRESS="${I2C_ADDRESS:-0x3C}"
ENABLE_OLED="$(config_get ENABLE_OLED "$SYSTEM_ENV")"
ENABLE_OLED="${ENABLE_OLED:-unknown}"

# Backward-compatible status for a v0.1 installation that has no system.env.
if [[ "$MODEM_PORT" == "unknown" && -r /etc/mmdvm/MMDVM-Host.ini ]]; then
	MODEM_PORT="$(grep -m1 -E '^UARTPort=' /etc/mmdvm/MMDVM-Host.ini 2>/dev/null | cut -d= -f2-)"
	MODEM_PORT="${MODEM_PORT:-unknown}"
fi
if [[ "$ENABLE_OLED" == "unknown" && -r /etc/mmdvm/oled.env ]]; then
	ENABLE_OLED="auto (oled.env present)"
fi
section() { printf '\n== %s ==\n' "$*"; }

section "platform"
printf '  %s (%s)\n' "$PLATFORM" "$(uname -m)"
printf '  modem: %s\n' "$MODEM_PORT"
printf '  OLED config: %s (i2c-%s 0x%s)\n' "$ENABLE_OLED" "$I2C_BUS" "${I2C_ADDRESS#0x}"

section "services"
while IFS= read -r unit; do
	active="$(systemctl is-active "$unit.service" 2>/dev/null || true)"
	enabled="$(systemctl is-enabled "$unit.service" 2>/dev/null || true)"
	restarts="$(systemctl show -p NRestarts --value "$unit.service" 2>/dev/null || true)"
	printf '  %-14s active=%-8s enabled=%-8s restarts=%s\n' \
		"$unit" "${active:-missing}" "${enabled:-missing}" "${restarts:-?}"
done < <(service_units)

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
	grep -E 'MMDVM protocol version|YSF, received RF|YSF, received network|Opening the MMDVM|UARTPort' \
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
if [[ -e "$MODEM_PORT" ]]; then
	ls -l "$MODEM_PORT"
else
	echo "  modem port missing: $MODEM_PORT"
fi
if platform_is_pi; then
	BOOT_CFG="$(config_txt_path)"
	[[ -n "$BOOT_CFG" ]] && printf '  boot config: %s\n' "$BOOT_CFG"
	if command -v dtoverlay >/dev/null 2>&1; then
		dtoverlay -l 2>/dev/null | grep -q 'uart0-pi5' \
			&& echo "  uart0-pi5 overlay is loaded" \
			|| echo "  uart0-pi5 overlay is not loaded at runtime (config.txt may load it on boot)"
	fi
fi
if [[ -e "/dev/i2c-$I2C_BUS" ]]; then
	ls -l "/dev/i2c-$I2C_BUS"
	detect_oled "$I2C_BUS" "$I2C_ADDRESS"
	rc=$?
	if (( rc == 0 )); then
		echo "  OLED at 0x${I2C_ADDRESS#0x} responds"
	elif (( rc == 2 )); then
		echo "  cannot probe OLED (i2cdetect missing/permission)"
	else
		echo "  no OLED at 0x${I2C_ADDRESS#0x}"
	fi
else
	echo "  /dev/i2c-$I2C_BUS missing"
fi

section "logs"
ls -l /var/log/mmdvm 2>/dev/null || true
