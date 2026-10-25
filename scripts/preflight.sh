#!/usr/bin/env bash
# Read-only checks for the MMDVM YSF add-on.
# Never changes the system. Exits non-zero on hard conflicts, not on
# experimental platforms or optional hardware.
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$REPO_DIR/scripts/lib.sh"
CONFIG="$REPO_DIR/config.env"
PLATFORM="$(detect_platform)"
FAIL=0
WARN=0

ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; WARN=1; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=1; }
section() { printf '\n== %s ==\n' "$*"; }

section "host"
printf '  platform: %s\n' "$PLATFORM"
printf '  kernel:   %s\n' "$(uname -r)"
printf '  arch:     %s\n' "$(uname -m)"
case "$PLATFORM" in
	pi5)
		ok "Raspberry Pi 5 + uart0-pi5 path (tested configuration)"
		;;
	pi_legacy)
		warn "Pi 4/3/Zero 2 W is experimental; UART/BT overlay and a reboot are required"
		;;
	x86)
		warn "x86_64 uses a USB serial MMDVM modem; build/CI is tested, hardware is NOT tested"
		;;
	*)
		warn "platform '$PLATFORM' is manual/experimental; USB serial + systemd + apt are assumed"
		;;
esac
if [[ -r /etc/os-release ]]; then
	# shellcheck disable=SC1091
	. /etc/os-release
	printf '  os:       %s\n' "${PRETTY_NAME:-unknown}"
	case " ${ID:-} ${ID_LIKE:-} " in
		*" ubuntu "*|*" debian "*) ok "Debian/Ubuntu apt path" ;;
		*) warn "prepare-system.sh is only tested on Debian/Ubuntu" ;;
	esac
else
	warn "no /etc/os-release"
fi

section "configuration"
if [[ -f "$CONFIG" ]]; then
	if command -v python3 >/dev/null 2>&1; then
		if python3 "$REPO_DIR/scripts/render_configs.py" --config "$CONFIG" --check --quiet; then
			ok "config.env passes validation"
		else
			bad "config.env failed validation (run render_configs.py without --quiet)"
		fi
	else
		bad "python3 is missing"
	fi
else
	warn "config.env missing; copy config.example.env and edit it before installing"
fi

section "modem serial"
MODEM_REQUESTED="$(config_get MODEM_PORT "$CONFIG")"
MODEM_REQUESTED="${MODEM_REQUESTED:-auto}"
if MODEM_RESOLVED="$(detect_modem_port "$MODEM_REQUESTED" 2>/dev/null)"; then
	ok "modem port: $MODEM_RESOLVED"
	if command -v fuser >/dev/null 2>&1; then
		if fuser "$MODEM_RESOLVED" >/dev/null 2>&1; then
			bad "$MODEM_RESOLVED is currently in use by another process"
		else
			ok "$MODEM_RESOLVED is free"
		fi
	fi
else
	rc=$?
	if (( rc == 2 )); then
		warn "multiple serial devices; set MODEM_PORT explicitly"
	else
		warn "no modem serial device found yet"
	fi
fi
if platform_is_pi; then
	if [[ -e /dev/ttyAMA0 ]]; then
		ok "/dev/ttyAMA0 exists"
	else
		warn "/dev/ttyAMA0 is not present yet; prepare-system.sh/boot config handles it"
	fi
fi

if [[ "$PLATFORM" == "pi5" ]]; then
	section "Pi 5 UART overlay"
	if [[ -e /boot/firmware/current/overlays/uart0-pi5.dtbo ||
	      -e "/usr/lib/firmware/$(uname -r)/device-tree/overlays/uart0-pi5.dtbo" ]]; then
		ok "uart0-pi5 overlay is available"
	else
		bad "uart0-pi5 overlay not found; cannot enable GPIO14/15 UART"
	fi
	if command -v systemctl >/dev/null 2>&1; then
		if [[ "$(systemctl is-active serial-getty@ttyAMA0.service 2>/dev/null || true)" == "active" ]]; then
			warn "serial-getty@ttyAMA0 is active; prepare-system.sh will disable it"
		else
			ok "serial-getty@ttyAMA0 is not active"
		fi
	fi
	if (( EUID == 0 )) && command -v pinctrl >/dev/null 2>&1; then
		printf '  GPIO14: %s\n' "$(pinctrl get 14 2>/dev/null || true)"
		printf '  GPIO15: %s\n' "$(pinctrl get 15 2>/dev/null || true)"
	fi
elif [[ "$PLATFORM" == "pi_legacy" || "$PLATFORM" == "pi_other" ]]; then
	section "Pi legacy UART"
	BOOT_CFG="$(config_txt_path)"
	if [[ -n "$BOOT_CFG" ]]; then
		ok "boot config: $BOOT_CFG"
		if grep -qE '^[[:space:]]*enable_uart=1' "$BOOT_CFG"; then
			ok "enable_uart=1 already present"
		else
			warn "enable_uart=1 not present; prepare-system.sh will add it (reboot needed)"
		fi
	else
		bad "no /boot/firmware/config.txt or /boot/config.txt"
	fi
fi

section "OLED (optional)"
ENABLE_OLED="$(config_get ENABLE_OLED "$CONFIG")"
ENABLE_OLED="${ENABLE_OLED:-auto}"
I2C_BUS="$(config_get I2C_BUS "$CONFIG")"
I2C_BUS="${I2C_BUS:-1}"
I2C_ADDRESS="$(config_get I2C_ADDRESS "$CONFIG")"
I2C_ADDRESS="${I2C_ADDRESS:-0x3C}"
case "$ENABLE_OLED" in
	0|no|false|off)
		ok "OLED disabled by config"
		;;
	*)
		if [[ ! -e "/dev/i2c-$I2C_BUS" ]]; then
			warn "/dev/i2c-$I2C_BUS missing; the OLED service will be skipped"
		elif detect_oled "$I2C_BUS" "$I2C_ADDRESS"; then
			ok "OLED responded at 0x${I2C_ADDRESS#0x} on i2c-$I2C_BUS"
		else
			rc=$?
			if (( rc == 2 )); then
				warn "cannot probe OLED (i2cdetect missing/permission); install will decide"
			else
				warn "no OLED at 0x${I2C_ADDRESS#0x}; the OLED service will be skipped"
			fi
		fi
		;;
esac

section "add-on conflicts"
if [[ -e /etc/mmdvm ]]; then
	bad "/etc/mmdvm already exists; remove/uninstall the existing MMDVM setup first"
else
	ok "/etc/mmdvm is free"
fi
if command -v systemctl >/dev/null 2>&1; then
	while IFS= read -r unit; do
		if systemctl list-unit-files --no-legend "$unit.service" 2>/dev/null | grep -q .; then
			bad "systemd unit $unit.service already exists"
		fi
	done < <(service_units)
fi
if pgrep -x MMDVM-Host >/dev/null 2>&1 || pgrep -x YSFGateway >/dev/null 2>&1; then
	bad "an MMDVM-Host or YSFGateway process is already running"
else
	ok "no MMDVM-Host/YSFGateway process is running"
fi
if command -v ss >/dev/null 2>&1; then
	if ss -lun 2>/dev/null | grep -Eq ':(3200|4200|42000|62031|62032|20010|20011)[[:space:]]'; then
		bad "an MMDVM UDP port is already in use (3200/4200/42000/62031/62032/20010/20011)"
	else
		ok "MMDVM UDP ports (YSF/DMR/D-Star) are free"
	fi
else
	warn "ss is missing; could not check UDP ports"
fi
for marker in /etc/pistar-release /etc/wpsd_version /etc/wpsd-release; do
	if [[ -e "$marker" ]]; then
		bad "Pi-Star/WPSD marker found ($marker); this add-on is for a normal Linux system"
	fi
done

section "summary"
if (( FAIL > 0 )); then
	printf 'FAIL: hard conflicts found; do not install yet.\n'
	exit 1
fi
if (( WARN > 0 )); then
	printf 'OK with warnings. Experimental/untested paths are labeled as such.\n'
	exit 0
fi
printf 'All checks passed.\n'
exit 0
