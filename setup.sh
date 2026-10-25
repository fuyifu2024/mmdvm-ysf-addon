#!/usr/bin/env bash
# One-command setup for the MMDVM YSF add-on.
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

echo "== MMDVM YSF add-on setup =="
echo "repo:     $REPO_DIR"
echo "platform: $PLATFORM"
echo

if (( DRY_RUN )); then
	if [[ -f "$CONFIG" ]]; then
		python3 "$REPO_DIR/scripts/render_configs.py" --config "$CONFIG" --check || exit 1
	else
		echo "config.env is missing; a real run would copy config.example.env and prompt"
		echo "for CALLSIGN, FREQUENCY and MODEM_PORT."
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

	# Values are validated above; escape & for sed replacements.
	esc_callsign="${CALLSIGN_IN//&/\\&}"
	esc_frequency="${FREQUENCY_IN//&/\\&}"
	esc_modem="${MODEM_IN//&/\\&}"
	sed -i "s|^CALLSIGN=.*|CALLSIGN=$esc_callsign|" "$CONFIG"
	sed -i "s|^FREQUENCY=.*|FREQUENCY=$esc_frequency|" "$CONFIG"
	sed -i "s|^MODEM_PORT=.*|MODEM_PORT=$esc_modem|" "$CONFIG"
	if grep -q '^HOTSPOT_NAME=N0CALL' "$CONFIG"; then
		sed -i "s|^HOTSPOT_NAME=.*|HOTSPOT_NAME=$esc_callsign YSF Hotspot|" "$CONFIG"
	fi
	echo "OLED stays auto (installer probes 0x3C); set ENABLE_OLED=0 to disable."
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
FREQUENCY_CFG="$(config_get FREQUENCY "$CONFIG")"
echo
echo "== summary =="
echo "  platform:  $PLATFORM"
echo "  callsign:  $(config_get CALLSIGN "$CONFIG")"
echo "  frequency: ${FREQUENCY_CFG} MHz"
echo "  modem:     $(config_get MODEM_PORT "$CONFIG")"
echo "  reflector: ${STARTUP}"
echo "  OLED:      ${ENABLE_OLED_CFG:-auto}"
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

if [[ -x "$REPO_DIR/build/bin/MMDVM-Host" && -x "$REPO_DIR/build/bin/YSFGateway" ]] && (( REBUILD == 0 )); then
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
echo "Radio: ${FREQUENCY_CFG} MHz, DN mode, DG-ID 0, no CTCSS/tone."
echo "Connect a suitable antenna or dummy load before a longer transmission."
echo "Use ./scripts/status.sh and tail -f /var/log/mmdvm/mmdvmhost.log to monitor."
