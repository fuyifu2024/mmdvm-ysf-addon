#!/usr/bin/env bash
# Install and start the MMDVM YSF add-on services.
# Run as: sudo ./scripts/install-services.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$REPO_DIR/scripts/lib.sh"
BUILD="$REPO_DIR/build"
BIN="$BUILD/bin"
ETC="$BUILD/etc"
PLATFORM="$(detect_platform)"
DRY_RUN=0

for arg in "$@"; do
	case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		-h|--help)
			echo "usage: sudo $0 [--dry-run]"
			exit 0
			;;
		*) echo "unknown option: $arg" >&2; exit 2 ;;
	esac
done

[ -x "$BIN/MMDVM-Host" ] || { echo "ERROR: $BIN/MMDVM-Host missing; run ./scripts/build.sh" >&2; exit 1; }
[ -x "$BIN/YSFGateway" ] || { echo "ERROR: $BIN/YSFGateway missing; run ./scripts/build.sh" >&2; exit 1; }
for f in MMDVM-Host.ini YSFGateway.ini YSFHosts.json oled.env system.env; do
	[ -f "$ETC/$f" ] || { echo "ERROR: $ETC/$f missing; run python3 scripts/render_configs.py" >&2; exit 1; }
done
for f in ysfgateway.service mmdvmhost.service oled-display.service mmdvm.logrotate; do
	[ -f "$REPO_DIR/units/$f" ] || { echo "ERROR: units/$f missing" >&2; exit 1; }
done

MODEM_PORT="$(config_get MODEM_PORT "$ETC/system.env")"
MODEM_PORT="${MODEM_PORT:-auto}"
ENABLE_OLED="$(config_get ENABLE_OLED "$ETC/system.env")"
ENABLE_OLED="${ENABLE_OLED:-auto}"
I2C_BUS="$(config_get I2C_BUS "$ETC/system.env")"
I2C_BUS="${I2C_BUS:-1}"
I2C_ADDRESS="$(config_get I2C_ADDRESS "$ETC/system.env")"
I2C_ADDRESS="${I2C_ADDRESS:-0x3C}"
ENABLE_DMR="$(config_get ENABLE_DMR "$ETC/system.env")"
ENABLE_DMR="${ENABLE_DMR:-0}"
ENABLE_DSTAR="$(config_get ENABLE_DSTAR "$ETC/system.env")"
ENABLE_DSTAR="${ENABLE_DSTAR:-0}"
case "${ENABLE_DMR,,}" in 1|yes|true|on) DMR=1 ;; *) DMR=0 ;; esac
case "${ENABLE_DSTAR,,}" in 1|yes|true|on) DSTAR=1 ;; *) DSTAR=0 ;; esac

if (( DMR )); then
	[ -x "$BIN/DMRGateway" ] || { echo "ERROR: $BIN/DMRGateway missing; re-run ./scripts/build.sh" >&2; exit 1; }
	[ -f "$ETC/DMRGateway.ini" ] || { echo "ERROR: $ETC/DMRGateway.ini missing; re-run render_configs.py" >&2; exit 1; }
	[ -f "$REPO_DIR/units/dmrgateway.service" ] || { echo "ERROR: units/dmrgateway.service missing" >&2; exit 1; }
fi
if (( DSTAR )); then
	[ -x "$BIN/dstargateway" ] || { echo "ERROR: $BIN/dstargateway missing; re-run ./scripts/build.sh" >&2; exit 1; }
	[ -f "$ETC/DStarGateway.cfg" ] || { echo "ERROR: $ETC/DStarGateway.cfg missing; re-run render_configs.py" >&2; exit 1; }
	[ -d "$BUILD/share/dstargateway.d" ] || { echo "ERROR: $BUILD/share/dstargateway.d missing; re-run ./scripts/build.sh" >&2; exit 1; }
	[ -f "$REPO_DIR/units/dstargateway.service" ] || { echo "ERROR: units/dstargateway.service missing" >&2; exit 1; }
fi

if (( DRY_RUN == 0 )); then
	if (( EUID != 0 )); then
		echo "ERROR: run this script with sudo (or use --dry-run)." >&2
		exit 1
	fi
	if ! MMDVM_PLATFORM="$PLATFORM" bash "$REPO_DIR/scripts/preflight.sh"; then
		echo "Preflight failed; refusing to install over an existing MMDVM configuration." >&2
		exit 1
	fi
	if [[ ! -e "$MODEM_PORT" ]]; then
		echo "ERROR: modem port does not exist: $MODEM_PORT" >&2
		echo "Connect the modem or update MODEM_PORT in config.env, then re-render." >&2
		exit 1
	fi
else
	echo "dry-run: preflight is not enforced here; run ./scripts/preflight.sh separately."
fi

# Resolve the optional OLED.  auto is decided here, as root, not in the renderer.
OLED=0
OLED_PLAN="$ENABLE_OLED"
if (( DRY_RUN )); then
	if [[ "$ENABLE_OLED" == "0" || "$ENABLE_OLED" == "no" || "$ENABLE_OLED" == "false" ]]; then
		OLED_PLAN=0
		echo "OLED: disabled by config"
	elif [[ "$ENABLE_OLED" == "auto" ]]; then
		echo "OLED: auto (real install will probe i2c-$I2C_BUS at 0x${I2C_ADDRESS#0x})"
	else
		echo "OLED: forced on (real install will verify i2c-$I2C_BUS at 0x${I2C_ADDRESS#0x})"
	fi
elif [[ "$ENABLE_OLED" == "0" || "$ENABLE_OLED" == "no" || "$ENABLE_OLED" == "false" ]]; then
	OLED_PLAN=0
	echo "OLED: disabled by config"
else
	if detect_oled "$I2C_BUS" "$I2C_ADDRESS"; then
		rc=0
	else
		rc=$?
	fi
	if (( rc == 0 )); then
		OLED=1
		OLED_PLAN=1
		echo "OLED: detected at 0x${I2C_ADDRESS#0x} on i2c-$I2C_BUS"
	else
		OLED_PLAN=0
		if [[ "$ENABLE_OLED" == "auto" || "$ENABLE_OLED" == "yes" || "$ENABLE_OLED" == "1" ]]; then
			if (( rc == 2 )); then
				echo "OLED: could not probe i2c-$I2C_BUS (i2cdetect/permissions); skipping" >&2
			elif [[ "$ENABLE_OLED" != "auto" ]]; then
				echo "OLED: requested but not found at 0x${I2C_ADDRESS#0x}; skipping" >&2
			else
				echo "OLED: no device at 0x${I2C_ADDRESS#0x}; skipping"
			fi
		fi
	fi
fi

PORT_GROUP="$(port_group "$MODEM_PORT")"
PLANNED_GROUPS=dialout
if [[ "$PORT_GROUP" != "dialout" ]]; then
	PLANNED_GROUPS="dialout,$PORT_GROUP"
fi

echo
echo "== planned changes =="
cat <<EOF
  platform: $PLATFORM
  modem:    $MODEM_PORT (group $PORT_GROUP)
  modes:    YSF=on DMR=$([[ $DMR == 1 ]] && echo on || echo off) D-Star=$([[ $DSTAR == 1 ]] && echo on || echo off)
  add system user mmdvm (groups $PLANNED_GROUPS)
  install /usr/local/bin/MMDVM-Host, YSFGateway, oled_display.py
  install /etc/mmdvm/{MMDVM-Host.ini,YSFGateway.ini,YSFHosts.json,oled.env,system.env}
  create /var/lib/mmdvm and /var/log/mmdvm
  install ysfgateway, mmdvmhost, oled-display systemd units
  install /etc/logrotate.d/mmdvm
  enable and start MMDVM services; OLED enabled=$OLED_PLAN
EOF
if (( DMR )); then
	echo "  DMR:      install DMRGateway + DMRGateway.ini, enable dmrgateway"
fi
if (( DSTAR )); then
	echo "  D-Star:   install dstargateway + DStarGateway.cfg + host lists, enable dstargateway"
fi
echo
if (( DRY_RUN )); then
	echo "dry-run: no changes made."
	exit 0
fi

if ! id -u mmdvm >/dev/null 2>&1; then
	useradd --system --user-group --no-create-home --shell /usr/sbin/nologin mmdvm
fi
GROUPS_TO_ADD="dialout"
if getent group "$PORT_GROUP" >/dev/null 2>&1 && [[ "$PORT_GROUP" != "dialout" ]]; then
	GROUPS_TO_ADD="dialout,$PORT_GROUP"
fi
usermod -aG "$GROUPS_TO_ADD" mmdvm

install -d -m 0755 /etc/mmdvm /var/lib/mmdvm /var/log/mmdvm
chown mmdvm:mmdvm /var/lib/mmdvm /var/log/mmdvm

install -m 0755 "$BIN/MMDVM-Host" /usr/local/bin/MMDVM-Host
install -m 0755 "$BIN/YSFGateway" /usr/local/bin/YSFGateway
if [[ -f "$REPO_DIR/oled/oled_display.py" ]]; then
	install -m 0755 "$REPO_DIR/oled/oled_display.py" /usr/local/bin/oled_display.py
fi
if (( DMR )); then
	install -m 0755 "$BIN/DMRGateway" /usr/local/bin/DMRGateway
fi
if (( DSTAR )); then
	install -m 0755 "$BIN/dstargateway" /usr/local/bin/dstargateway
	install -d -m 0755 /var/lib/mmdvm/dstargateway.d
	if [[ -d "$BUILD/share/dstargateway.d" ]]; then
		# Keep files the gateway may have refreshed on a previous install.
		cp -an "$BUILD/share/dstargateway.d/." /var/lib/mmdvm/dstargateway.d/
	fi
	chown -R mmdvm:mmdvm /var/lib/mmdvm/dstargateway.d
fi

install -m 0640 -o root -g mmdvm "$ETC/MMDVM-Host.ini" /etc/mmdvm/MMDVM-Host.ini
install -m 0640 -o root -g mmdvm "$ETC/YSFGateway.ini" /etc/mmdvm/YSFGateway.ini
install -m 0640 -o root -g mmdvm "$ETC/oled.env" /etc/mmdvm/oled.env
install -m 0644 "$ETC/YSFHosts.json" /etc/mmdvm/YSFHosts.json
install -m 0644 "$ETC/system.env" /etc/mmdvm/system.env
if (( DMR )); then
	install -m 0640 -o root -g mmdvm "$ETC/DMRGateway.ini" /etc/mmdvm/DMRGateway.ini
else
	rm -f /etc/mmdvm/DMRGateway.ini
fi
if (( DSTAR )); then
	install -m 0640 -o root -g mmdvm "$ETC/DStarGateway.cfg" /etc/mmdvm/DStarGateway.cfg
else
	rm -f /etc/mmdvm/DStarGateway.cfg
fi

install -m 0644 "$REPO_DIR/units/ysfgateway.service" /etc/systemd/system/ysfgateway.service
install -m 0644 "$REPO_DIR/units/mmdvmhost.service" /etc/systemd/system/mmdvmhost.service
install -m 0644 "$REPO_DIR/units/oled-display.service" /etc/systemd/system/oled-display.service
install -m 0644 "$REPO_DIR/units/mmdvm.logrotate" /etc/logrotate.d/mmdvm
if (( DMR )); then
	install -m 0644 "$REPO_DIR/units/dmrgateway.service" /etc/systemd/system/dmrgateway.service
else
	rm -f /etc/systemd/system/dmrgateway.service
	rm -f /usr/local/bin/DMRGateway
fi
if (( DSTAR )); then
	install -m 0644 "$REPO_DIR/units/dstargateway.service" /etc/systemd/system/dstargateway.service
else
	rm -f /etc/systemd/system/dstargateway.service
	rm -f /usr/local/bin/dstargateway
fi

# If the serial device belongs to a group other than dialout, make sure the
# static unit gets that group too (the user's groups are also applied by systemd).
if [[ "$PORT_GROUP" != "dialout" ]]; then
	install -d -m 0755 /etc/systemd/system/mmdvmhost.service.d
	printf '[Service]\nSupplementaryGroups=%s\n' "$PORT_GROUP" \
		> /etc/systemd/system/mmdvmhost.service.d/20-port-group.conf
else
	rm -rf /etc/systemd/system/mmdvmhost.service.d
fi

systemctl daemon-reload
systemctl enable ysfgateway.service mmdvmhost.service
systemctl restart ysfgateway.service
sleep 2
if (( DMR )); then
	systemctl enable dmrgateway.service
	systemctl restart dmrgateway.service
else
	systemctl disable --now dmrgateway.service 2>/dev/null || true
fi
if (( DSTAR )); then
	systemctl enable dstargateway.service
	systemctl restart dstargateway.service
else
	systemctl disable --now dstargateway.service 2>/dev/null || true
fi
sleep 2
systemctl restart mmdvmhost.service
sleep 2
if (( OLED )); then
	systemctl enable oled-display.service
	systemctl restart oled-display.service
	sleep 1
else
	systemctl disable --now oled-display.service 2>/dev/null || true
fi

echo
echo "== service status =="
systemctl --no-pager --full status ysfgateway.service || true
systemctl --no-pager --full status mmdvmhost.service || true
(( DMR )) && systemctl --no-pager --full status dmrgateway.service || true
(( DSTAR )) && systemctl --no-pager --full status dstargateway.service || true
if (( OLED )); then
	systemctl --no-pager --full status oled-display.service || true
fi
echo
echo "== recent logs =="
tail -n 20 /var/log/mmdvm/ysfgateway.log 2>/dev/null || true
tail -n 20 /var/log/mmdvm/mmdvmhost.log 2>/dev/null || true
(( DMR )) && tail -n 20 /var/log/mmdvm/dmrgateway.log 2>/dev/null || true
if (( DSTAR )); then
	journalctl -u dstargateway.service -n 20 --no-pager 2>/dev/null || true
fi
echo
echo "Installed. Check with: ./scripts/status.sh"
echo "If MMDVM-Host is restarting, inspect $MODEM_PORT and the logs."
