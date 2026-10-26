#!/usr/bin/env bash
# Stop and remove the MMDVM YSF add-on.
# Keeps logs and the mmdvm user unless --purge is used.
# Run as: sudo ./scripts/uninstall.sh [--dry-run] [--purge]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$REPO_DIR/scripts/lib.sh"
PLATFORM="$(detect_platform)"
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
echo "platform: $PLATFORM"
while IFS= read -r unit; do
	if systemctl list-unit-files --no-legend "$unit.service" 2>/dev/null | grep -q .; then
		say_run systemctl disable --now "$unit.service" 2>/dev/null || true
		say_run rm -f "/etc/systemd/system/$unit.service"
	fi
done < <(service_units)
say_run rm -f /etc/systemd/system/mmdvmhost.service.d/20-port-group.conf
say_run rmdir /etc/systemd/system/mmdvmhost.service.d 2>/dev/null || true
say_run systemctl daemon-reload

say_run rm -f /usr/local/bin/MMDVM-Host
say_run rm -f /usr/local/bin/YSFGateway
say_run rm -f /usr/local/bin/oled_display.py
say_run rm -f /usr/local/bin/DMRGateway
say_run rm -f /usr/local/bin/dstargateway
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
echo "== restoring boot config (Pi only) =="
if platform_is_pi; then
	RESTORED=0
	for backup in /boot/firmware/config.txt.bak-mmdvm-addon /boot/config.txt.bak-mmdvm-addon; do
		if [[ -f "$backup" ]]; then
			config="${backup%.bak-mmdvm-addon}"
			echo "restoring $config from $backup (this reverts later edits to boot config)"
			say_run cp "$backup" "$config"
			RESTORED=1
		fi
	done
	if (( RESTORED == 0 )); then
		echo "WARNING: no MMDVM boot-config backup found." >&2
		echo "If needed, remove only the lines added by this add-on manually." >&2
	fi
	if command -v dtoverlay >/dev/null 2>&1 && dtoverlay -l 2>/dev/null | grep -q 'uart0-pi5'; then
		say_run dtoverlay -r uart0-pi5 2>/dev/null || true
	fi
else
	echo "non-Pi platform: no /boot config or GPIO overlay was installed."
fi

echo
echo "Update snapshots in /var/backups/mmdvm were kept (remove them manually if desired)."
echo
echo "Shared packages were NOT removed:"
echo "  build-essential patch curl libmosquitto-dev nlohmann-json3-dev python3-pil i2c-tools"
echo "  optional D-Star build deps: libcurl4-openssl-dev libboost-dev"
echo "Remove them manually only if no other software needs them."
echo
echo "Uninstall plan finished."
