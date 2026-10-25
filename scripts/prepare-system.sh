#!/usr/bin/env bash
# Prepare an existing Linux system for the MMDVM YSF add-on.
# Pi 5: adds uart0-pi5. Pi 4/3: adds enable_uart + a Bluetooth overlay.
# x86/other: does not touch boot config; uses a USB serial MMDVM modem.
# Installs build/runtime dependencies and serial groups. Does not reboot.
#
# Run as: sudo ./scripts/prepare-system.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$REPO_DIR/scripts/lib.sh"
CONFIG="$REPO_DIR/config.env"
PLATFORM="$(detect_platform)"
TARGET_USER="${SUDO_USER:-${USER:-}}"
DRY_RUN=0
SKIP_PREFLIGHT=0

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

say_run() {
	if (( DRY_RUN )); then
		printf '+ %s\n' "$*"
	else
		"$@"
	fi
}

overlay_available() {
	local name="$1"
	local dir
	for dir in \
		/boot/firmware/current/overlays \
		/boot/firmware/overlays \
		/boot/overlays \
		"/usr/lib/firmware/$(uname -r)/device-tree/overlays"; do
		[[ -e "$dir/$name.dtbo" ]] && return 0
	done
	return 1
}

append_boot_lines() {
	local config_file="$1"
	shift
	local line
	local missing=()
	for line in "$@"; do
		if grep -qE "^[[:space:]]*${line%%=*}=${line#*=}([[:space:]]|$)" "$config_file"; then
			echo "already present: $line"
		else
			missing+=("$line")
		fi
	done
	(( ${#missing[@]} > 0 )) || return 0
	if (( DRY_RUN )); then
		printf '+ append [all] block to %s: %s\n' "$config_file" "${missing[*]}"
		return 0
	fi
	if [[ ! -f "$config_file.bak-mmdvm-addon" ]]; then
		cp "$config_file" "$config_file.bak-mmdvm-addon"
		echo "backup: $config_file.bak-mmdvm-addon"
	fi
	printf '\n[all]\n' >> "$config_file"
	printf '%s\n' "${missing[@]}" >> "$config_file"
	printf 'added to [all]: %s\n' "${missing[*]}"
}

echo "== MMDVM YSF add-on: system preparation =="
echo "platform:    $PLATFORM"
echo "target user: $TARGET_USER"
echo

if (( SKIP_PREFLIGHT == 0 )); then
	echo "== preflight =="
	if ! MMDVM_PLATFORM="$PLATFORM" bash "$REPO_DIR/scripts/preflight.sh"; then
		echo
		echo "Preflight found hard conflicts. Resolve them, or use --skip-preflight only" >&2
		echo "if you are intentionally repairing a partial installation." >&2
		exit 1
	fi
	echo
fi

case "$PLATFORM" in
	pi5)
		echo "== Pi 5 UART overlay =="
		CONFIG_TXT="$(config_txt_path)"
		[[ -n "$CONFIG_TXT" ]] || { echo "ERROR: no /boot/firmware/config.txt" >&2; exit 1; }
		append_boot_lines "$CONFIG_TXT" "dtoverlay=uart0-pi5"
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
		;;
	pi_legacy|pi_other)
		echo "== Pi legacy UART/BT setup (experimental) =="
		CONFIG_TXT="$(config_txt_path)"
		[[ -n "$CONFIG_TXT" ]] || { echo "ERROR: no /boot/firmware/config.txt or /boot/config.txt" >&2; exit 1; }
		if [[ "$PLATFORM" == "pi_legacy" ]]; then
			if overlay_available miniuart-bt; then
				BT_OVERLAY=dtoverlay=miniuart-bt
				echo "using miniuart-bt (Bluetooth moves to mini-UART)"
			elif overlay_available disable-bt; then
				BT_OVERLAY=dtoverlay=disable-bt
				echo "WARNING: miniuart-bt unavailable; using disable-bt (Bluetooth disabled)" >&2
			else
				BT_OVERLAY=""
				echo "WARNING: no Bluetooth UART overlay found; UART may remain assigned to Bluetooth" >&2
			fi
			if [[ -n "$BT_OVERLAY" ]]; then
				append_boot_lines "$CONFIG_TXT" "enable_uart=1" "$BT_OVERLAY"
			else
				append_boot_lines "$CONFIG_TXT" "enable_uart=1"
			fi
		else
			append_boot_lines "$CONFIG_TXT" "enable_uart=1"
		fi
		echo "A reboot is required for the Pi legacy UART configuration."
		;;
	x86|other)
		echo "== USB serial modem =="
		echo "No boot config or GPIO overlay will be changed on this platform."
		;;
	*)
		echo "== unknown platform =="
		echo "No boot config changes; USB serial + systemd are assumed."
		;;
esac

if platform_is_pi; then
	if (( DRY_RUN == 0 )); then
		systemctl disable --now serial-getty@ttyAMA0.service 2>/dev/null || true
		systemctl disable --now serial-getty@ttyS0.service 2>/dev/null || true
	fi
fi

echo
echo "== dependencies =="
PACKAGES=(build-essential patch curl libmosquitto-dev nlohmann-json3-dev python3-pil i2c-tools)
DSTAR_FLAG="$(config_get ENABLE_DSTAR "$CONFIG")"
case "${DSTAR_FLAG,,}" in
	1|yes|true|on) PACKAGES+=(libcurl4-openssl-dev libboost-dev) ;;
esac
if command -v apt-get >/dev/null 2>&1; then
	say_run env DEBIAN_FRONTEND=noninteractive apt-get update
	say_run env DEBIAN_FRONTEND=noninteractive apt-get install -y "${PACKAGES[@]}"
else
	echo "WARNING: apt-get not found; install these manually:" >&2
	printf '  %s\n' "${PACKAGES[*]}" >&2
fi

echo
echo "== serial/i2c groups =="
GROUPS_TO_ADD=dialout
if getent group i2c >/dev/null 2>&1; then
	GROUPS_TO_ADD=dialout,i2c
fi
MODEM_REQUESTED="$(config_get MODEM_PORT "$CONFIG")"
MODEM_REQUESTED="${MODEM_REQUESTED:-auto}"
if MODEM_RESOLVED="$(detect_modem_port "$MODEM_REQUESTED" 2>/dev/null)"; then
	PORT_GROUP="$(port_group "$MODEM_RESOLVED")"
	case ",$GROUPS_TO_ADD," in
		*",$PORT_GROUP,"*) ;;
		*) GROUPS_TO_ADD="$GROUPS_TO_ADD,$PORT_GROUP" ;;
	esac
	echo "modem: $MODEM_RESOLVED (group $PORT_GROUP)"
else
	echo "WARNING: no single modem serial device found; set MODEM_PORT later if needed." >&2
fi
say_run usermod -aG "$GROUPS_TO_ADD" "$TARGET_USER"

echo
echo "Preparation finished."
echo "Next: copy config.example.env to config.env, edit it, then run:"
echo "  python3 scripts/render_configs.py"
echo "  ./scripts/build.sh"
echo "  sudo ./scripts/install-services.sh"
if platform_is_pi && [[ ! -e /dev/ttyAMA0 ]]; then
	echo
	echo "NOTE: /dev/ttyAMA0 is not ready; reboot once before install-services.sh."
fi
if [[ "$PLATFORM" == "x86" || "$PLATFORM" == "other" ]]; then
	echo
	echo "NOTE: x86/other uses a USB MMDVM modem. Copy the stable"
	echo "      /dev/serial/by-id/... path into MODEM_PORT if auto is ambiguous."
fi
