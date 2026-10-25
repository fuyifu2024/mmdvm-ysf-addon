#!/usr/bin/env bash
# Prepare an existing Ubuntu/Pi 5 for the MMDVM YSF add-on.
# Adds the uart0-pi5 overlay, build dependencies, and serial/i2c groups.
# Does not install the services and does not reboot.
#
# Run as: sudo ./scripts/prepare-system.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_TXT=/boot/firmware/config.txt
BACKUP="$CONFIG_TXT.bak-mmdvm-addon"
DRY_RUN=0
SKIP_PREFLIGHT=0
TARGET_USER="${SUDO_USER:-${USER:-}}"

for arg in "$@"; do
	case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		--skip-preflight) SKIP_PREFLIGHT=1 ;;
		-h|--help)
			echo "usage: sudo $0 [--dry-run] [--skip-preflight]"
			exit 0
			;;
		*) echo "unknown option: $arg" >&2; exit 2 ;;
	esac
done

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
	echo "ERROR: run this with sudo from your normal user account, not as root directly." >&2
	exit 1
fi
if (( DRY_RUN == 0 )) && (( EUID != 0 )); then
	echo "ERROR: run this script with sudo (or use --dry-run)." >&2
	exit 1
fi

run() {
	if (( DRY_RUN )); then
		printf '+ %s\n' "$*"
	else
		"$@"
	fi
}

echo "== MMDVM YSF add-on: system preparation =="
echo "target user: $TARGET_USER"
echo

if [[ ! -f /proc/device-tree/model ]] || ! tr -d '\0' < /proc/device-tree/model | grep -q 'Raspberry Pi 5'; then
	echo "ERROR: this add-on currently requires a Raspberry Pi 5." >&2
	exit 1
fi

if (( SKIP_PREFLIGHT == 0 )); then
	echo "== preflight =="
	if ! bash "$REPO_DIR/scripts/preflight.sh"; then
		echo
		echo "Preflight found hard conflicts.  Resolve them, or use --skip-preflight only if you" >&2
		echo "are intentionally repairing a partial installation." >&2
		exit 1
	fi
	echo
fi

echo "== UART overlay =="
if [[ ! -f "$CONFIG_TXT" ]]; then
	echo "ERROR: $CONFIG_TXT not found." >&2
	exit 1
fi
if [[ ! -f "$BACKUP" ]]; then
	run cp "$CONFIG_TXT" "$BACKUP"
	echo "backup: $BACKUP"
else
	echo "backup already exists: $BACKUP"
fi

if grep -qE '^[[:space:]]*dtoverlay=uart0-pi5([[:space:]]|$)' "$CONFIG_TXT"; then
	echo "dtoverlay=uart0-pi5 already present in config.txt"
else
	if (( DRY_RUN )); then
		echo "+ append '[pi5] dtoverlay=uart0-pi5' to $CONFIG_TXT"
	else
		printf '\n[pi5]\ndtoverlay=uart0-pi5\n' >> "$CONFIG_TXT"
		echo "added [pi5] dtoverlay=uart0-pi5"
	fi
fi

if command -v dtoverlay >/dev/null 2>&1 &&
   ! dtoverlay -l 2>/dev/null | grep -q 'uart0-pi5'; then
	for overlay_dir in \
		/boot/firmware/current/overlays \
		"/usr/lib/firmware/$(uname -r)/device-tree/overlays"; do
		if [[ -e "$overlay_dir/uart0-pi5.dtbo" ]]; then
			if (( DRY_RUN )); then
				echo "+ dtoverlay -d $overlay_dir uart0-pi5"
			elif dtoverlay -d "$overlay_dir" uart0-pi5; then
				echo "loaded uart0-pi5 at runtime"
				break
			fi
		fi
	done
fi

if (( DRY_RUN == 0 )); then
	sleep 1
	if [[ -e /dev/ttyAMA0 ]]; then
		ls -l /dev/ttyAMA0
	else
		echo "WARNING: /dev/ttyAMA0 is not present yet; reboot once before building/installing." >&2
	fi
	systemctl disable --now serial-getty@ttyAMA0.service 2>/dev/null || true
fi

echo
echo "== build dependencies =="
run env DEBIAN_FRONTEND=noninteractive apt-get update
run env DEBIAN_FRONTEND=noninteractive apt-get install -y \
	build-essential patch curl libmosquitto-dev nlohmann-json3-dev \
	python3-pil i2c-tools

echo
echo "== user groups (manual testing) =="
GROUPS_TO_ADD=dialout
if getent group i2c >/dev/null 2>&1; then
	GROUPS_TO_ADD=dialout,i2c
fi
run usermod -aG "$GROUPS_TO_ADD" "$TARGET_USER"

echo
echo "Preparation finished."
echo "Next: copy config.example.env to config.env, edit it, then run:"
echo "  python3 scripts/render_configs.py"
echo "  ./scripts/build.sh"
echo "  sudo ./scripts/install-services.sh"
if [[ ! -e /dev/ttyAMA0 ]]; then
	echo
	echo "NOTE: /dev/ttyAMA0 is not ready; reboot once before running install-services.sh,"
	echo "or the modem service will not be able to open the UART."
fi
