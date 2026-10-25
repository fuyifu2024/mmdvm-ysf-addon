#!/usr/bin/env bash
# Shared platform / serial / OLED helpers.  Source this file; do not execute it.
# Only read-only detection belongs here.  Installer actions stay in the scripts.
# shellcheck shell=bash

detect_platform() {
	if [[ -n "${MMDVM_PLATFORM:-}" ]]; then
		echo "$MMDVM_PLATFORM"
		return 0
	fi
	if [[ -r /proc/device-tree/model ]]; then
		local model
		model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
		case "$model" in
			*"Raspberry Pi 5"*) echo "pi5" ;;
			*"Raspberry Pi 4"*|*"Raspberry Pi 3"*|*"Raspberry Pi Zero 2"*|*"Compute Module 4"*|*"Compute Module 3"*) echo "pi_legacy" ;;
			*"Raspberry Pi"*) echo "pi_other" ;;
			*) echo "arm-other" ;;
		esac
		return 0
	fi
	case "$(uname -m)" in
		x86_64|i?86|amd64) echo "x86" ;;
		*) echo "other" ;;
	esac
}

platform_is_pi() {
	case "$(detect_platform)" in
		pi5|pi_legacy|pi_other) return 0 ;;
		*) return 1 ;;
	esac
}

config_txt_path() {
	if [[ -e /boot/firmware/config.txt ]]; then
		echo /boot/firmware/config.txt
	elif [[ -e /boot/config.txt ]]; then
		echo /boot/config.txt
	fi
}

# Print unique serial candidates, preferring stable by-id paths.
modem_port_candidates() {
	local seen="" candidate real
	for candidate in /dev/serial/by-id/* /dev/ttyAMA0 /dev/serial0 \
		/dev/ttyUSB* /dev/ttyACM* /dev/ttySC*; do
		[[ -e "$candidate" ]] || continue
		real="$(readlink -f "$candidate" 2>/dev/null || printf '%s' "$candidate")"
		case " $seen " in
			*" $real "*) continue ;;
		esac
		seen="$seen $real"
		printf '%s\n' "$candidate"
	done
}

# Usage: detect_modem_port [configured]
# 0 = one resolved port on stdout; 1 = none; 2 = ambiguous (candidates on stderr).
detect_modem_port() {
	local requested="${1:-auto}"
	if [[ -n "$requested" && "$requested" != "auto" ]]; then
		if [[ -e "$requested" ]]; then
			printf '%s\n' "$requested"
			return 0
		fi
		printf 'serial device does not exist: %s\n' "$requested" >&2
		return 1
	fi
	local candidates=()
	while IFS= read -r candidate; do
		candidates+=("$candidate")
	done < <(modem_port_candidates)
	if (( ${#candidates[@]} == 0 )); then
		return 1
	fi
	if (( ${#candidates[@]} > 1 )); then
		printf 'multiple serial devices found; set MODEM_PORT explicitly:\n' >&2
		printf '  %s\n' "${candidates[@]}" >&2
		return 2
	fi
	printf '%s\n' "${candidates[0]}"
	return 0
}

port_group() {
	stat -c '%G' "$1" 2>/dev/null || echo dialout
}

# Usage: detect_oled [bus] [address]
# 0 = address responded; 1 = not found/no bus; 2 = cannot probe.
detect_oled() {
	local bus="${1:-1}"
	local address="${2:-0x3C}"
	local hex="${address#0x}"
	[[ -e "/dev/i2c-$bus" ]] || return 1
	if ! command -v i2cdetect >/dev/null 2>&1; then
		return 2
	fi
	# Retry because a running display can briefly NAK a probe.
	local attempt
	for attempt in 1 2 3; do
		if i2cdetect -y "$bus" 2>/dev/null | grep -qi "$hex"; then
			return 0
		fi
		sleep 0.2
	done
	return 1
}

service_units() {
	printf '%s\n' ysfgateway mmdvmhost oled-display
}

# Read a simple KEY=value from an env file without executing it.
config_get() {
	local key="$1" file="$2"
	[[ -f "$file" ]] || return 0
	awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$file"
}
