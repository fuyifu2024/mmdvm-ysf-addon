#!/usr/bin/env bash
# Apply a validated config with a snapshot and rollback on failure.
# Run as your normal user; sudo is used for the privileged steps.
#
#   scripts/safe-install.sh --candidate FILE --reason onboard --yes
#   scripts/safe-install.sh --reason update --build always
#
# This is the shared apply path for ./mmdvm onboard and ./mmdvm update.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$REPO_DIR/scripts/lib.sh"
CONFIG="${MMDVM_CONFIG:-$REPO_DIR/config.env}"
CANDIDATE=""
REASON="manual"
BUILD_MODE="auto"
ASSUME_YES=0
ADOPT=0
DRY_RUN=0
NO_BACKUP=0
BACKUP=""

while (( $# > 0 )); do
	case "$1" in
		--candidate) CANDIDATE="${2:-}"; shift 2 ;;
		--reason) REASON="${2:-manual}"; shift 2 ;;
		--build) BUILD_MODE="${2:-auto}"; shift 2 ;;
		--yes) ASSUME_YES=1; shift ;;
		--adopt) ADOPT=1; shift ;;
		--no-backup) NO_BACKUP=1; shift ;;
		--dry-run) DRY_RUN=1; shift ;;
		-h|--help)
			cat <<'EOF'
usage: scripts/safe-install.sh [options]
  --candidate FILE   validated config.env to install
  --reason TEXT      onboard/update/manual (messages only)
  --build MODE       auto (default), always or never
  --yes              skip the confirmation prompt
  --adopt            allow a legacy /etc/mmdvm install to be taken over
  --no-backup        internal: caller already made a snapshot
  --dry-run          print the plan only
EOF
			exit 0
			;;
		*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
done

case "$BUILD_MODE" in auto|always|never) ;; *) echo "invalid --build mode: $BUILD_MODE" >&2; exit 2 ;; esac
[[ "$CANDIDATE" == "" || -f "$CANDIDATE" ]] || { echo "ERROR: candidate not found: $CANDIDATE" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "ERROR: $CONFIG missing; run ./setup.sh first" >&2; exit 1; }
if [[ "${EUID:-$(id -u)}" -eq 0 && "${MMDVM_TEST_MODE:-0}" != "1" ]]; then
	echo "ERROR: run safe-install.sh as your normal user, not root." >&2
	echo "It calls sudo for snapshot/install/restore." >&2
	exit 1
fi

STATE="$(python3 - "$REPO_DIR" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from addon_config import install_state
print(install_state())
PY
)"
case "$STATE" in
	addon) ;;
	legacy)
		(( ADOPT )) || { echo "ERROR: legacy add-on install; use --adopt (or ./mmdvm update)." >&2; exit 1; }
		;;
	foreign|none)
		echo "ERROR: no updateable add-on installation (state: $STATE)." >&2
		echo "Run ./setup.sh for a first installation." >&2
		exit 1
		;;
esac

VALIDATE_FILE="$CONFIG"
if [[ -n "$CANDIDATE" ]]; then
	VALIDATE_FILE="$CANDIDATE"
fi
if ! python3 "$REPO_DIR/scripts/render_configs.py" --config "$VALIDATE_FILE" --check --quiet; then
	echo "ERROR: configuration validation failed; nothing changed." >&2
	exit 1
fi
if grep -qE '^[[:space:]]*CALLSIGN=N0CALL[[:space:]]*$' "$VALIDATE_FILE"; then
	echo "ERROR: CALLSIGN is still N0CALL; set a real callsign first." >&2
	exit 1
fi

VERSION="$(tr -d '[:space:]' < "$REPO_DIR/VERSION" 2>/dev/null || echo unknown)"
echo "== safe install ($REASON) =="
echo "  platform:  $(detect_platform)"
echo "  config:    $CONFIG"
echo "  version:   $VERSION"
echo "  build:     $BUILD_MODE"
echo "  mode:      update (snapshot + rollback)"
if [[ -n "$CANDIDATE" ]]; then
	echo "  candidate: $CANDIDATE"
fi

if (( DRY_RUN )); then
	echo "dry-run: no snapshot, install or service restart performed."
	exit 0
fi

if (( ASSUME_YES == 0 )); then
	read -r -p "Apply and restart the add-on services? [y/N] " CONFIRM
	if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
		echo "Aborted; nothing changed."
		exit 0
	fi
fi

if ! sudo -v; then
	echo "ERROR: sudo authentication failed; nothing changed." >&2
	exit 1
fi

if (( NO_BACKUP == 0 )); then
	BACKUP="$(sudo env MMDVM_CONFIG="$CONFIG" python3 "$REPO_DIR/scripts/state_backup.py" create)" || {
		echo "ERROR: could not create a state snapshot; nothing changed." >&2
		exit 1
	}
	echo "snapshot: $BACKUP"
fi

ROLLBACK=0
restore_on_failure() {
	local rc=$?
	trap - EXIT
	if (( ROLLBACK == 0 && rc != 0 )) && [[ -n "$BACKUP" ]]; then
		ROLLBACK=1
		echo
		echo "ERROR: apply failed; restoring snapshot $BACKUP" >&2
		if sudo env MMDVM_CONFIG="$CONFIG" python3 "$REPO_DIR/scripts/state_backup.py" restore "$BACKUP"; then
			echo "Restored the previous config, binaries, units and service state." >&2
		else
			echo "RESTORE FAILED. Manual rollback: sudo env MMDVM_CONFIG='$CONFIG' python3 scripts/state_backup.py restore '$BACKUP'" >&2
		fi
	fi
	exit "$rc"
}
trap restore_on_failure EXIT

if [[ -n "$CANDIDATE" ]]; then
	tmp="$CONFIG.tmp.$$"
	install -m 0600 "$CANDIDATE" "$tmp"
	mv -f "$tmp" "$CONFIG"
fi

echo
echo "== render configuration =="
if ! python3 "$REPO_DIR/scripts/render_configs.py" --config "$CONFIG"; then
	echo "ERROR: rendering failed." >&2
	exit 1
fi

NEED_BUILD=0
if [[ "$BUILD_MODE" == "always" ]]; then
	NEED_BUILD=1
elif [[ "$BUILD_MODE" == "auto" ]]; then
	[[ -x "$REPO_DIR/build/bin/MMDVM-Host" && -x "$REPO_DIR/build/bin/YSFGateway" ]] || {
		echo "ERROR: no base build found; run ./setup.sh (onboard does not bootstrap)." >&2
		exit 1
	}
	DMR_FLAG="$(config_get ENABLE_DMR "$REPO_DIR/build/etc/system.env")"
	DSTAR_FLAG="$(config_get ENABLE_DSTAR "$REPO_DIR/build/etc/system.env")"
	case "${DMR_FLAG,,}" in 1|yes|true|on) [[ -x "$REPO_DIR/build/bin/DMRGateway" ]] || NEED_BUILD=1 ;; esac
	case "${DSTAR_FLAG,,}" in 1|yes|true|on)
		[[ -x "$REPO_DIR/build/bin/dstargateway" && -d "$REPO_DIR/build/share/dstargateway.d" ]] || NEED_BUILD=1
		;;
	esac
fi

if (( NEED_BUILD )); then
	MEM_AVAILABLE_KB="$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)"
	if (( MEM_AVAILABLE_KB > 0 && MEM_AVAILABLE_KB < 600000 )); then
		echo "ERROR: less than 600 MB available; close apps/reboot before building." >&2
		exit 1
	fi
	if (( MEM_AVAILABLE_KB > 0 && MEM_AVAILABLE_KB < 1500000 )); then
		JOBS=1
		echo "== build (low memory: JOBS=1) =="
	else
		JOBS=2
		echo "== build =="
	fi
	if ! JOBS="$JOBS" "$REPO_DIR/scripts/build.sh"; then
		echo "ERROR: build failed." >&2
		exit 1
	fi
fi

echo
echo "== install-services (update mode) =="
INSTALL_ARGS=(--update --no-backup)
(( ADOPT )) && INSTALL_ARGS+=(--adopt)
if ! sudo env MMDVM_CONFIG="$CONFIG" "$REPO_DIR/scripts/install-services.sh" "${INSTALL_ARGS[@]}"; then
	echo "ERROR: install-services failed." >&2
	exit 1
fi

echo
echo "== verify services =="
sleep 2
FAILED=0
for unit in ysfgateway mmdvmhost; do
	state="$(systemctl is-active "$unit.service" 2>/dev/null || true)"
	printf '  %-14s %s\n' "$unit" "${state:-unknown}"
	[[ "$state" == "active" ]] || FAILED=1
done
SYSTEM_ENV=/etc/mmdvm/system.env
DMR_FLAG="$(config_get ENABLE_DMR "$SYSTEM_ENV")"
DSTAR_FLAG="$(config_get ENABLE_DSTAR "$SYSTEM_ENV")"
case "${DMR_FLAG,,}" in
	1|yes|true|on)
		state="$(systemctl is-active dmrgateway.service 2>/dev/null || true)"
		printf '  %-14s %s\n' dmrgateway "${state:-unknown}"
		[[ "$state" == "active" ]] || FAILED=1
		;;
esac
case "${DSTAR_FLAG,,}" in
	1|yes|true|on)
		state="$(systemctl is-active dstargateway.service 2>/dev/null || true)"
		printf '  %-14s %s\n' dstargateway "${state:-unknown}"
		[[ "$state" == "active" ]] || FAILED=1
		;;
esac
if (( FAILED )); then
	echo "ERROR: one or more services are not active." >&2
	exit 1
fi

ROLLBACK=1
trap - EXIT
echo
echo "Installed version $VERSION."
echo "Snapshot kept at: ${BACKUP:-<none>}"
echo "Check: ./mmdvm status"
