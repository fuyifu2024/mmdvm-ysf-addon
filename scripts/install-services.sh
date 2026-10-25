#!/usr/bin/env bash
# Install and start the MMDVM YSF add-on services.
# Run as: sudo ./scripts/install-services.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$REPO_DIR/build"
BIN="$BUILD/bin"
ETC="$BUILD/etc"
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

say_run() {
	if (( DRY_RUN )); then
		printf '+ %s\n' "$*"
	else
		"$@"
	fi
}

[ -x "$BIN/MMDVM-Host" ] || { echo "ERROR: $BIN/MMDVM-Host missing; run ./scripts/build.sh" >&2; exit 1; }
[ -x "$BIN/YSFGateway" ] || { echo "ERROR: $BIN/YSFGateway missing; run ./scripts/build.sh" >&2; exit 1; }
for f in MMDVM-Host.ini YSFGateway.ini YSFHosts.json oled.env; do
	[ -f "$ETC/$f" ] || { echo "ERROR: $ETC/$f missing; run python3 scripts/render_configs.py" >&2; exit 1; }
done
for f in ysfgateway.service mmdvmhost.service oled-display.service mmdvm.logrotate; do
	[ -f "$REPO_DIR/units/$f" ] || { echo "ERROR: units/$f missing" >&2; exit 1; }
done
[ -f "$REPO_DIR/oled/oled_display.py" ] || { echo "ERROR: oled/oled_display.py missing" >&2; exit 1; }

if (( DRY_RUN == 0 )); then
	if (( EUID != 0 )); then
		echo "ERROR: run this script with sudo (or use --dry-run)." >&2
		exit 1
	fi
	if ! bash "$REPO_DIR/scripts/preflight.sh"; then
		echo "Preflight failed; refusing to install over an existing MMDVM configuration." >&2
		exit 1
	fi
	if [[ ! -e /dev/ttyAMA0 ]]; then
		echo "ERROR: /dev/ttyAMA0 is not ready. Reboot once after prepare-system.sh, then rerun." >&2
		exit 1
	fi
else
	echo "dry-run: preflight is not enforced here; run ./scripts/preflight.sh separately."
fi

OLED=0
if [[ -e /dev/i2c-1 ]]; then
	if command -v i2cdetect >/dev/null 2>&1 && i2cdetect -y 1 2>/dev/null | grep -qi '3c'; then
		OLED=1
	elif (( DRY_RUN )); then
		echo "dry-run: cannot confirm 0x3C (permission or no OLED); assume OLED is present for this plan."
		OLED=1
	else
		echo "WARNING: no SSD1306 at 0x3C; oled-display will not be enabled." >&2
	fi
else
	echo "WARNING: /dev/i2c-1 missing; oled-display will not be enabled." >&2
fi

echo
echo "== planned changes =="
cat <<'EOF'
  add system user mmdvm (group dialout)
  install /usr/local/bin/MMDVM-Host, YSFGateway, oled_display.py
  install /etc/mmdvm/{MMDVM-Host.ini,YSFGateway.ini,YSFHosts.json,oled.env}
  create /var/lib/mmdvm and /var/log/mmdvm
  install ysfgateway, mmdvmhost, oled-display systemd units
  install /etc/logrotate.d/mmdvm
  enable and start the services
EOF
echo
if (( DRY_RUN )); then
	echo "dry-run: no changes made."
	exit 0
fi

if ! id -u mmdvm >/dev/null 2>&1; then
	useradd --system --user-group --no-create-home --shell /usr/sbin/nologin mmdvm
fi
usermod -aG dialout mmdvm

install -d -m 0755 /etc/mmdvm /var/lib/mmdvm /var/log/mmdvm
chown mmdvm:mmdvm /var/lib/mmdvm /var/log/mmdvm

install -m 0755 "$BIN/MMDVM-Host" /usr/local/bin/MMDVM-Host
install -m 0755 "$BIN/YSFGateway" /usr/local/bin/YSFGateway
install -m 0755 "$REPO_DIR/oled/oled_display.py" /usr/local/bin/oled_display.py

install -m 0640 -o root -g mmdvm "$ETC/MMDVM-Host.ini" /etc/mmdvm/MMDVM-Host.ini
install -m 0640 -o root -g mmdvm "$ETC/YSFGateway.ini" /etc/mmdvm/YSFGateway.ini
install -m 0640 -o root -g mmdvm "$ETC/oled.env" /etc/mmdvm/oled.env
install -m 0644 "$ETC/YSFHosts.json" /etc/mmdvm/YSFHosts.json

install -m 0644 "$REPO_DIR/units/ysfgateway.service" /etc/systemd/system/ysfgateway.service
install -m 0644 "$REPO_DIR/units/mmdvmhost.service" /etc/systemd/system/mmdvmhost.service
install -m 0644 "$REPO_DIR/units/oled-display.service" /etc/systemd/system/oled-display.service
install -m 0644 "$REPO_DIR/units/mmdvm.logrotate" /etc/logrotate.d/mmdvm

systemctl daemon-reload
systemctl enable ysfgateway.service mmdvmhost.service
systemctl restart ysfgateway.service
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
if (( OLED )); then
	systemctl --no-pager --full status oled-display.service || true
fi
echo
echo "== recent logs =="
tail -n 20 /var/log/mmdvm/ysfgateway.log 2>/dev/null || true
tail -n 20 /var/log/mmdvm/mmdvmhost.log 2>/dev/null || true
echo
echo "Installed.  Check with: ./scripts/status.sh"
echo "If MMDVM-Host is restarting, inspect /dev/ttyAMA0 and the logs before changing config."
