#!/usr/bin/env bash
# Read-only checks for the MMDVM YSF add-on.
# This script never changes the system.  It exits non-zero on a hard conflict.
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_DIR/config.env"
FAIL=0
WARN=0

ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; WARN=1; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=1; }
section() { printf '\n== %s ==\n' "$*"; }

section "host"
if [[ -r /proc/device-tree/model ]]; then
	MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
	printf '  model: %s\n' "${MODEL:-unknown}"
	if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
		ok "Raspberry Pi 5 detected (uart0-pi5 overlay is Pi 5 specific)"
	else
		bad "this add-on is currently tested only on Raspberry Pi 5"
	fi
else
	bad "cannot read /proc/device-tree/model"
fi
if [[ -r /etc/os-release ]]; then
	# shellcheck disable=SC1091
	. /etc/os-release
	printf '  os: %s\n' "${PRETTY_NAME:-unknown}"
	if [[ "${ID:-}" == "ubuntu" ]]; then
		ok "Ubuntu detected"
	else
		warn "tested on Ubuntu 26.04; ${ID:-unknown} may need different paths"
	fi
else
	warn "no /etc/os-release"
fi
printf '  kernel: %s\n' "$(uname -r)"
printf '  arch:   %s\n' "$(uname -m)"

section "configuration"
if [[ -f "$CONFIG" ]]; then
	if command -v python3 >/dev/null 2>&1; then
		if python3 "$REPO_DIR/scripts/render_configs.py" --config "$CONFIG" --check --quiet; then
			ok "config.env passes validation"
		else
			bad "config.env failed validation (run render_configs.py without --quiet for details)"
		fi
	else
		bad "python3 is missing"
	fi
else
	warn "config.env missing; copy config.example.env and edit it before installing"
fi

section "UART / modem pins"
if [[ -e /dev/ttyAMA0 ]]; then
	ok "/dev/ttyAMA0 exists"
	if command -v fuser >/dev/null 2>&1; then
		if fuser /dev/ttyAMA0 >/dev/null 2>&1; then
			bad "/dev/ttyAMA0 is currently in use by another process"
		else
			ok "/dev/ttyAMA0 is free"
		fi
	fi
else
	warn "/dev/ttyAMA0 is not present yet; prepare-system.sh will load uart0-pi5"
	if [[ -e /boot/firmware/current/overlays/uart0-pi5.dtbo ||
	      -e "/usr/lib/firmware/$(uname -r)/device-tree/overlays/uart0-pi5.dtbo" ]]; then
		ok "uart0-pi5 overlay is available"
	else
		bad "uart0-pi5 overlay not found; cannot enable GPIO14/15 UART"
	fi
fi

if command -v systemctl >/dev/null 2>&1; then
	GETTY_STATE="$(systemctl is-active serial-getty@ttyAMA0.service 2>/dev/null || true)"
	if [[ "$GETTY_STATE" == "active" ]]; then
		warn "serial-getty@ttyAMA0 is active; prepare-system.sh will disable it"
	else
		ok "serial-getty@ttyAMA0 is not active"
	fi
fi

if command -v pinctrl >/dev/null 2>&1; then
	printf '  GPIO14: %s\n' "$(pinctrl get 14 2>/dev/null || true)"
	printf '  GPIO15: %s\n' "$(pinctrl get 15 2>/dev/null || true)"
fi

section "I2C / OLED"
if [[ -e /dev/i2c-1 ]]; then
	ok "/dev/i2c-1 exists"
	if command -v i2cdetect >/dev/null 2>&1; then
		if i2cdetect -y 1 2>/dev/null | grep -qi '3c'; then
			ok "SSD1306 responded at 0x3C"
		else
			warn "no 0x3C device found; the OLED service will be skipped"
		fi
	else
		warn "i2cdetect is missing (prepare-system.sh installs i2c-tools)"
	fi
else
	warn "/dev/i2c-1 is missing; the OLED service will be skipped"
fi

section "add-on conflicts"
if [[ -e /etc/mmdvm ]]; then
	bad "/etc/mmdvm already exists; remove/uninstall the existing MMDVM setup first"
else
	ok "/etc/mmdvm is free"
fi

if command -v systemctl >/dev/null 2>&1; then
	for unit in ysfgateway mmdvmhost oled-display; do
		if systemctl list-unit-files --no-legend "$unit.service" 2>/dev/null | grep -q .; then
			bad "systemd unit $unit.service already exists"
		else
			ok "no installed $unit.service"
		fi
	done
fi

if pgrep -x MMDVM-Host >/dev/null 2>&1 || pgrep -x YSFGateway >/dev/null 2>&1; then
	bad "an MMDVM-Host or YSFGateway process is already running"
else
	ok "no MMDVM-Host/YSFGateway process is running"
fi

if command -v ss >/dev/null 2>&1; then
	if ss -lun 2>/dev/null | grep -Eq ':(3200|4200|42000)[[:space:]]'; then
		bad "UDP port 3200, 4200 or 42000 is already in use"
	else
		ok "MMDVM UDP ports 3200/4200/42000 are free"
	fi
else
	warn "ss is missing; could not check UDP ports"
fi

for marker in /etc/pistar-release /etc/wpsd_version /etc/wpsd-release; do
	if [[ -e "$marker" ]]; then
		bad "Pi-Star/WPSD marker found ($marker); this add-on is for a normal Ubuntu system"
	fi
done

section "summary"
if (( FAIL > 0 )); then
	printf 'FAIL: hard conflicts found; do not install yet.\n'
	exit 1
fi
if (( WARN > 0 )); then
	printf 'OK with warnings.  Resolve warnings before expecting a working RF path.\n'
	exit 0
fi
printf 'All checks passed.\n'
exit 0
