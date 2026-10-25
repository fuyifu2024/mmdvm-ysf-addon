#!/usr/bin/env bash
# Stop and remove the MMDVM YSF add-on.
# Keeps logs and the mmdvm user unless --purge is used.
# Run as: sudo ./scripts/uninstall.sh [--dry-run] [--purge]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_TXT=/boot/firmware/config.txt
BACKUP="$CONFIG_TXT.bak-mmdvm-addon"
DRY_RUN=0
PURGE=0

for arg in "$@"; do
	case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		--purge) PURGE=1 ;;
		-h|--help)
			echo "usage: sudo $0 [--dry-run] [--purge]"
			echo "  --purge also removes /var/log/mmdvm and the mmdvm user"
			exit 0
			;;
		*) echo "unknown option: $arg" >&2; exit 2 ;;
	esac
done

if (( DRY_RUN == 0 )) && (( EUID != 0 )); then
	echo "ERROR: run this script with sudo (or use --dry-run)." >&2
	exit 1
fi

say_run() {
	if (( DRY_RUN )); then
		printf '+ %s\n' "$*"
	else
		"$@"
	fi
}

echo "== MMDVM YSF add-on: uninstall =="
for unit in ysfgateway mmdvmhost oled-display; do
	if systemctl list-unit-files --no-legend "$unit.service" 2>/dev/null | grep -q .; then
		say_run systemctl disable --now "$unit.service" 2>/dev/null || true
		say_run rm -f "/etc/systemd/system/$unit.service"
	fi
done
say_run systemctl daemon-reload

say_run rm -f /usr/local/bin/MMDVM-Host
say_run rm -f /usr/local/bin/YSFGateway
say_run rm -f /usr/local/bin/oled_display.py
say_run rm -rf /etc/mmdvm
say_run rm -rf /var/lib/mmdvm
say_run rm -f /etc/logrotate.d/mmdvm

if (( PURGE )); then
	say_run rm -rf /var/log/mmdvm
	if id -u mmdvm >/dev/null 2>&1; then
		if pgrep -u mmdvm >/dev/null 2>&1; then
			echo "WARNING: mmdvm still has running processes; not deleting the user." >&2
		else
			say_run userdel mmdvm 2>/dev/null || true
		fi
	fi
else
	echo "kept /var/log/mmdvm (use --purge to remove logs and the mmdvm user)"
fi

echo
echo "== restoring boot config =="
if [[ -f "$BACKUP" ]]; then
	say_run cp "$BACKUP" "$CONFIG_TXT"
	echo "restored $CONFIG_TXT from $BACKUP"
else
	echo "WARNING: backup not found: $BACKUP" >&2
	echo "Remove this line from $CONFIG_TXT manually if it was added by this add-on:" >&2
	echo "  dtoverlay=uart0-pi5" >&2
fi

if command -v dtoverlay >/dev/null 2>&1 && dtoverlay -l 2>/dev/null | grep -q 'uart0-pi5'; then
	say_run dtoverlay -r uart0-pi5 2>/dev/null || true
fi

echo
echo "Shared packages were NOT removed:"
echo "  build-essential patch curl libmosquitto-dev nlohmann-json3-dev python3-pil i2c-tools"
echo "Remove them manually only if no other software needs them."
echo
echo "Uninstall plan finished."
