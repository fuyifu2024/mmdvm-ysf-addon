#!/usr/bin/env bash
# One-command setup for the MMDVM hotspot add-on (YSF + optional DMR/D-Star).
#
#   ./setup.sh                 interactive: prompts, then prepares/builds/installs
#   ./setup.sh --dry-run       show the plan without changing anything
#   ./setup.sh --yes           skip the final confirmation
#   ./setup.sh --non-interactive  require an existing config.env; no prompts
#   ./setup.sh --rebuild       force a rebuild even if build/bin exists
#
# Run as your normal user, not as root; the script calls sudo where needed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
. "$REPO_DIR/scripts/lib.sh"
CONFIG="$REPO_DIR/config.env"
PLATFORM="$(detect_platform)"
DRY_RUN=0
ASSUME_YES=0
NON_INTERACTIVE=0
REBUILD=0

for arg in "$@"; do
	case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		--yes) ASSUME_YES=1 ;;
		--non-interactive) NON_INTERACTIVE=1; ASSUME_YES=1 ;;
		--rebuild) REBUILD=1 ;;
		-h|--help)
			sed -n '2,12p' "$0" | sed -n 's/^# \{0,1\}//p'
			exit 0
			;;
		*) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
	esac
done

if (( DRY_RUN == 0 )) && (( EUID == 0 )); then
	echo "ERROR: run ./setup.sh as your normal user, not as root." >&2
	echo "The script will call sudo for the privileged steps." >&2
	exit 1
fi

esc_sed() {
	local value="$1"
	value="${value//\\/\\\\}"
	value="${value//&/\\&}"
	value="${value//|/\\|}"
	printf '%s' "$value"
}

echo "== MMDVM YSF add-on setup =="
echo "repo:     $REPO_DIR"
echo "platform: $PLATFORM"
echo

if (( DRY_RUN )); then
	if [[ -f "$CONFIG" ]]; then
		python3 "$REPO_DIR/scripts/render_configs.py" --config "$CONFIG" --check || exit 1
	else
		echo "config.env is missing; a real run would copy config.example.env and prompt"
		echo "for CALLSIGN, FREQUENCY, MODEM_PORT, the OLED panel, and optional DMR/D-Star."
	fi
	echo
	echo "== planned steps =="
	cat <<'EOF'
  1. create/edit config.env (if needed)
  2. scripts/preflight.sh
  3. sudo scripts/prepare-system.sh --skip-preflight
  4. render configs (requires a usable serial device)
  5. scripts/build.sh (memory-aware JOBS)
  6. sudo scripts/install-services.sh
  7. scripts/status.sh
EOF
	echo
	echo "dry-run: no changes made."
	exit 0
fi

if (( NON_INTERACTIVE == 0 )) && [[ ! -f "$CONFIG" ]]; then
	if [[ ! -t 0 ]]; then
		echo "ERROR: no config.env and stdin is not interactive." >&2
		echo "Copy config.example.env to config.env, edit it, then use --non-interactive." >&2
		exit 1
	fi

	cp "$REPO_DIR/config.example.env" "$CONFIG"
	echo "Created config.env from the example."
	echo "Press Enter to accept defaults. Edit config.env later for a different reflector."
	echo

	while true; do
		read -r -p "Callsign (uppercase, not N0CALL): " CALLSIGN_IN
		CALLSIGN_IN="${CALLSIGN_IN^^}"
		if [[ "$CALLSIGN_IN" =~ ^[A-Z0-9]{3,10}$ && "$CALLSIGN_IN" != "N0CALL" ]]; then
			break
		fi
		echo "  invalid callsign: use 3-10 letters/digits, not N0CALL" >&2
	done

	FREQUENCY_IN=""
	while true; do
		read -r -p "Frequency MHz [438.8000]: " FREQUENCY_IN
		FREQUENCY_IN="${FREQUENCY_IN:-438.8000}"
		if ! [[ "$FREQUENCY_IN" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
			echo "  invalid number" >&2
			continue
		fi
		if python3 -c 'import sys; f=float(sys.argv[1]); sys.exit(0 if (144<=f<=148 or 420<=f<=450 or 862<=f<=950) else 1)' "$FREQUENCY_IN"; then
			break
		fi
		echo "  frequency must be in 144-148, 420-450 or 862-950 MHz" >&2
	done
	if [[ "$FREQUENCY_IN" == "144.3" || "$FREQUENCY_IN" == "144.3000" ]]; then
		echo "WARNING: 144.300 is the 2m weak-signal/SSB calling area; not a normal hotspot default." >&2
	fi

	MODEM_IN=""
	while true; do
		read -r -p "MODEM_PORT [auto]: " MODEM_IN
		MODEM_IN="${MODEM_IN:-auto}"
		if [[ "$MODEM_IN" == "auto" ]]; then
			break
		fi
		if [[ "$MODEM_IN" == /dev/* && ! "$MODEM_IN" =~ [[:space:]] && "$MODEM_IN" != *"|"* ]]; then
			break
		fi
		echo "  use 'auto' or an absolute /dev/... path" >&2
	done

	echo
	echo "OLED panel (I2C address 0x3C):"
	echo "  1) 128x64 SSD1306   (default, tested 0.96\" module)"
	echo "  2) 128x32 SSD1306   (0.91\" module)"
	echo "  3) 64x48 SSD1306    (small module)"
	echo "  4) 128x64 SH1106    (1.3\" module)"
	echo "  5) no OLED / another address"
	read -r -p "OLED panel [1]: " OLED_CHOICE
	OLED_CHOICE="${OLED_CHOICE:-1}"
	OLED_ENABLE_IN=auto
	OLED_CONTROLLER_IN=auto
	OLED_SIZE_IN=128x64
	OLED_ROTATE_IN=0
	case "$OLED_CHOICE" in
		1) ;;
		2) OLED_SIZE_IN=128x32 ;;
		3) OLED_SIZE_IN=64x48 ;;
		4) OLED_CONTROLLER_IN=sh1106 ;;
		5) OLED_ENABLE_IN=0 ;;
		*) echo "  unknown choice; keeping the 128x64 SSD1306 default" >&2 ;;
	esac
	if [[ "$OLED_ENABLE_IN" != "0" ]]; then
		read -r -p "Is the panel mounted upside down (rotate 180)? [y/N] " OLED_ROTATE_IN
		if [[ "$OLED_ROTATE_IN" =~ ^[Yy]$ ]]; then
			OLED_ROTATE_IN=180
		else
			OLED_ROTATE_IN=0
		fi
	fi

	echo
	read -r -p "Enable DMR network (BrandMeister)? [y/N] " ENABLE_DMR_IN
	ENABLE_DMR_IN="${ENABLE_DMR_IN:-N}"
	DMR_ENABLE=0
	DMR_ID_IN=0
	DMR_MASTER_IN=4601.master.brandmeister.network
	DMR_PASSWORD_IN=
	if [[ "$ENABLE_DMR_IN" =~ ^[Yy]$ ]]; then
		DMR_ENABLE=1
		while true; do
			read -r -p "DMR ID (numeric): " DMR_ID_IN
			[[ "$DMR_ID_IN" =~ ^[1-9][0-9]{0,6}$ ]] && break
			echo "  DMR ID must be 1-7 digits" >&2
		done
		read -r -p "BrandMeister master [$DMR_MASTER_IN]: " MASTER_IN
		MASTER_IN="${MASTER_IN:-$DMR_MASTER_IN}"
		while [[ ! "$MASTER_IN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]]; do
			echo "  invalid master hostname" >&2
			read -r -p "BrandMeister master [$DMR_MASTER_IN]: " MASTER_IN
			MASTER_IN="${MASTER_IN:-$DMR_MASTER_IN}"
		done
		DMR_MASTER_IN="$MASTER_IN"
		while true; do
			read -r -s -p "BrandMeister hotspot password (6-64 chars): " DMR_PASSWORD_IN
			echo
			[[ "$DMR_PASSWORD_IN" =~ ^[!-~]{6,64}$ ]] && break
			echo "  use 6-64 printable characters without spaces" >&2
		done
	fi
	echo
	read -r -p "Enable D-Star network? [y/N] " ENABLE_DSTAR_IN
	ENABLE_DSTAR_IN="${ENABLE_DSTAR_IN:-N}"
	DSTAR_ENABLE=0
	DSTAR_REFLECTOR_IN=
	DSTAR_MODULE_IN=C
	if [[ "$ENABLE_DSTAR_IN" =~ ^[Yy]$ ]]; then
		DSTAR_ENABLE=1
		while true; do
			read -r -p "D-Star reflector (e.g. REF001 C): " DSTAR_REFLECTOR_IN
			[[ "$DSTAR_REFLECTOR_IN" =~ ^[A-Za-z0-9-]+[[:space:]]+[A-Da-d]$ ]] && break
			echo "  use reflector + module, e.g. REF001 C" >&2
		done
		while true; do
			read -r -p "D-Star module [C]: " DSTAR_MODULE_IN
			DSTAR_MODULE_IN="${DSTAR_MODULE_IN:-C}"
			DSTAR_MODULE_IN="${DSTAR_MODULE_IN^^}"
			[[ "$DSTAR_MODULE_IN" =~ ^[A-D]$ ]] && break
			echo "  module must be A, B, C or D" >&2
		done
	fi
	echo

	# Values are validated above; escape sed's replacement metacharacters.
	esc_callsign="$(esc_sed "$CALLSIGN_IN")"
	esc_frequency="$(esc_sed "$FREQUENCY_IN")"
	esc_modem="$(esc_sed "$MODEM_IN")"
	esc_dmr_master="$(esc_sed "$DMR_MASTER_IN")"
	esc_dmr_password="$(esc_sed "$DMR_PASSWORD_IN")"
	esc_dstar_reflector="$(esc_sed "$DSTAR_REFLECTOR_IN")"
	sed -i "s|^CALLSIGN=.*|CALLSIGN=$esc_callsign|" "$CONFIG"
	sed -i "s|^FREQUENCY=.*|FREQUENCY=$esc_frequency|" "$CONFIG"
	sed -i "s|^MODEM_PORT=.*|MODEM_PORT=$esc_modem|" "$CONFIG"
	sed -i "s|^ENABLE_DMR=.*|ENABLE_DMR=$DMR_ENABLE|" "$CONFIG"
	sed -i "s|^DMR_ID=.*|DMR_ID=$DMR_ID_IN|" "$CONFIG"
	sed -i "s|^DMR_MASTER=.*|DMR_MASTER=$esc_dmr_master|" "$CONFIG"
	sed -i "s|^DMR_PASSWORD=.*|DMR_PASSWORD=$esc_dmr_password|" "$CONFIG"
	sed -i "s|^ENABLE_DSTAR=.*|ENABLE_DSTAR=$DSTAR_ENABLE|" "$CONFIG"
	sed -i "s|^DSTAR_REFLECTOR=.*|DSTAR_REFLECTOR=$esc_dstar_reflector|" "$CONFIG"
	sed -i "s|^DSTAR_MODULE=.*|DSTAR_MODULE=$DSTAR_MODULE_IN|" "$CONFIG"
	sed -i "s|^ENABLE_OLED=.*|ENABLE_OLED=$OLED_ENABLE_IN|" "$CONFIG"
	sed -i "s|^OLED_CONTROLLER=.*|OLED_CONTROLLER=$OLED_CONTROLLER_IN|" "$CONFIG"
	sed -i "s|^OLED_SIZE=.*|OLED_SIZE=$OLED_SIZE_IN|" "$CONFIG"
	sed -i "s|^OLED_ROTATE=.*|OLED_ROTATE=$OLED_ROTATE_IN|" "$CONFIG"
	if grep -q '^HOTSPOT_NAME=N0CALL' "$CONFIG"; then
		sed -i "s|^HOTSPOT_NAME=.*|HOTSPOT_NAME=$esc_callsign YSF Hotspot|" "$CONFIG"
	fi
	if (( DMR_ENABLE )); then
		chmod 600 "$CONFIG"
	fi
	echo "OLED: ${OLED_ENABLE_IN} controller=${OLED_CONTROLLER_IN} size=${OLED_SIZE_IN} rotate=${OLED_ROTATE_IN}"
	echo "For another I2C address or contrast, edit I2C_ADDRESS/OLED_CONTRAST in config.env."
	echo "Reflector stays at the example (46001); edit config.env for another one."
	echo
fi

if [[ ! -f "$CONFIG" ]]; then
	echo "ERROR: config.env is required for --non-interactive." >&2
	exit 1
fi

if grep -qE '^[[:space:]]*CALLSIGN=N0CALL[[:space:]]*$' "$CONFIG"; then
	echo "ERROR: CALLSIGN is still N0CALL; edit config.env before setting up a transmitter." >&2
	exit 1
fi

echo "== validate configuration =="
python3 "$REPO_DIR/scripts/render_configs.py" --config "$CONFIG" --check || exit 1

echo
echo "== preflight (read-only) =="
if ! ./scripts/preflight.sh; then
	echo "Preflight found a hard conflict. Resolve it, then rerun ./setup.sh." >&2
	exit 1
fi

STARTUP="$(config_get REFLECTOR_STARTUP "$CONFIG")"
ENABLE_OLED_CFG="$(config_get ENABLE_OLED "$CONFIG")"
OLED_CONTROLLER_CFG="$(config_get OLED_CONTROLLER "$CONFIG")"
OLED_SIZE_CFG="$(config_get OLED_SIZE "$CONFIG")"
OLED_ROTATE_CFG="$(config_get OLED_ROTATE "$CONFIG")"
FREQUENCY_CFG="$(config_get FREQUENCY "$CONFIG")"
MODES_DMR="$(config_get ENABLE_DMR "$CONFIG")"
MODES_DSTAR="$(config_get ENABLE_DSTAR "$CONFIG")"
DMR_ID_CFG="$(config_get DMR_ID "$CONFIG")"
DMR_MASTER_CFG="$(config_get DMR_MASTER "$CONFIG")"
DSTAR_REFLECTOR_CFG="$(config_get DSTAR_REFLECTOR "$CONFIG")"
DSTAR_MODULE_CFG="$(config_get DSTAR_MODULE "$CONFIG")"
echo
echo "== summary =="
echo "  platform:  $PLATFORM"
echo "  callsign:  $(config_get CALLSIGN "$CONFIG")"
echo "  frequency: ${FREQUENCY_CFG} MHz"
echo "  modem:     $(config_get MODEM_PORT "$CONFIG")"
echo "  reflector: ${STARTUP}"
echo "  modes:     YSF=on DMR=${MODES_DMR:-0} D-Star=${MODES_DSTAR:-0}"
if [[ "${MODES_DMR:-0}" == "1" ]]; then
	echo "  DMR:       ID ${DMR_ID_CFG} -> ${DMR_MASTER_CFG} (password hidden)"
fi
if [[ "${MODES_DSTAR:-0}" == "1" ]]; then
	echo "  D-Star:    ${DSTAR_REFLECTOR_CFG} module ${DSTAR_MODULE_CFG}"
fi
echo "  OLED:      ${ENABLE_OLED_CFG:-auto} ${OLED_CONTROLLER_CFG:-ssd1306} ${OLED_SIZE_CFG:-128x64} rotate=${OLED_ROTATE_CFG:-0}"
echo
if (( ASSUME_YES == 0 )); then
	read -r -p "Proceed with system changes and installation? [y/N] " CONFIRM
	if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
		echo "Aborted; no system changes were made (config.env is kept)."
		exit 0
	fi
fi

if ! sudo -v; then
	echo "ERROR: sudo authentication failed." >&2
	exit 1
fi

echo
echo "== prepare system (UART/dependencies/groups) =="
sudo ./scripts/prepare-system.sh --skip-preflight

MODEM_REQUESTED="$(config_get MODEM_PORT "$CONFIG")"
MODEM_REQUESTED="${MODEM_REQUESTED:-auto}"
if ! MODEM_RESOLVED="$(detect_modem_port "$MODEM_REQUESTED" 2>/dev/null)"; then
	if platform_is_pi; then
		echo
		echo "The modem UART is not ready yet. Reboot once, then rerun ./setup.sh."
		echo "Your config.env and system preparation are kept."
		exit 0
	fi
	echo
	echo "No USB serial modem found. Connect it, set MODEM_PORT if needed, and rerun." >&2
	echo "Typical stable path: /dev/serial/by-id/..." >&2
	exit 1
fi
echo "using modem: $MODEM_RESOLVED"

echo
echo "== render configuration =="
python3 "$REPO_DIR/scripts/render_configs.py" --config "$CONFIG"

NEED_BUILD=0
[[ -x "$REPO_DIR/build/bin/MMDVM-Host" && -x "$REPO_DIR/build/bin/YSFGateway" ]] || NEED_BUILD=1
BUILD_DMR_FLAG="$(config_get ENABLE_DMR "$REPO_DIR/build/etc/system.env")"
BUILD_DSTAR_FLAG="$(config_get ENABLE_DSTAR "$REPO_DIR/build/etc/system.env")"
case "${BUILD_DMR_FLAG,,}" in
	1|yes|true|on) [[ -x "$REPO_DIR/build/bin/DMRGateway" ]] || NEED_BUILD=1 ;;
esac
case "${BUILD_DSTAR_FLAG,,}" in
	1|yes|true|on) [[ -x "$REPO_DIR/build/bin/dstargateway" ]] || NEED_BUILD=1 ;;
esac
if (( NEED_BUILD == 0 && REBUILD == 0 )); then
	echo
	echo "== build =="
	echo "existing build found; use --rebuild to recompile"
else
	MEM_AVAILABLE_KB="$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)"
	if (( MEM_AVAILABLE_KB > 0 && MEM_AVAILABLE_KB < 600000 )); then
		echo "ERROR: less than 600 MB available; close apps/reboot before building." >&2
		exit 1
	fi
	if (( MEM_AVAILABLE_KB > 0 && MEM_AVAILABLE_KB < 1500000 )); then
		JOBS=1
		echo
		echo "== build =="
		echo "low memory: using JOBS=1 (close desktop apps if the build is slow)"
	else
		JOBS=2
		echo
		echo "== build =="
	fi
	JOBS="$JOBS" ./scripts/build.sh || exit 1
fi

echo
echo "== install services =="
sudo ./scripts/install-services.sh

echo
echo "== status =="
./scripts/status.sh || true

echo
echo "Setup finished."
echo "Radio: ${FREQUENCY_CFG} MHz; select the matching mode (YSF DN / DMR / D-Star)."
echo "Connect a suitable antenna or dummy load before a longer transmission."
echo "Use ./scripts/status.sh and tail -f /var/log/mmdvm/mmdvmhost.log to monitor."
