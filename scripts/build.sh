#!/usr/bin/env bash
# Fetch pinned upstream sources, apply the add-on patches, and build.
# Runs as your normal user; prepare-system.sh provides the build dependencies.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$REPO_DIR/build"
CACHE="$BUILD/cache"
SRC="$BUILD/src"
BIN="$BUILD/bin"
JOBS="${JOBS:-2}"
NO_FETCH=0

for arg in "$@"; do
	case "$arg" in
		--no-fetch) NO_FETCH=1 ;;
		-h|--help)
			echo "usage: $0 [--no-fetch]"
			echo "  JOBS=N  parallel make jobs (default 2, useful on a 4 GB Pi 5)"
			exit 0
			;;
		*) echo "unknown option: $arg" >&2; exit 2 ;;
	esac
done

die() { echo "ERROR: $*" >&2; exit 1; }
get_lock() {
	local key="$1"
	awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' \
		"$REPO_DIR/upstream.lock"
}

MMDVMHOST_URL="$(get_lock MMDVMHOST_URL)"
MMDVMHOST_SHA256="$(get_lock MMDVMHOST_SHA256)"
YSFCLIENTS_URL="$(get_lock YSFCLIENTS_URL)"
YSFCLIENTS_SHA256="$(get_lock YSFCLIENTS_SHA256)"
[[ -n "$MMDVMHOST_URL" && -n "$MMDVMHOST_SHA256" ]] || die "incomplete MMDVMHost entry in upstream.lock"
[[ -n "$YSFCLIENTS_URL" && -n "$YSFCLIENTS_SHA256" ]] || die "incomplete YSFClients entry in upstream.lock"

for tool in curl tar sha256sum make c++ patch; do
	command -v "$tool" >/dev/null 2>&1 || die "missing build tool: $tool (run sudo ./scripts/prepare-system.sh)"
done
[[ -f /usr/include/mosquitto.h ]] || die "libmosquitto-dev is missing (run sudo ./scripts/prepare-system.sh)"
[[ -f /usr/include/nlohmann/json.hpp ]] || die "nlohmann-json3-dev is missing (run sudo ./scripts/prepare-system.sh)"

mkdir -p "$CACHE" "$SRC" "$BIN"

fetch() {
	local url="$1" sha="$2" out="$3"
	if [[ -f "$out" ]] && printf '%s  %s\n' "$sha" "$out" | sha256sum -c --status 2>/dev/null; then
		echo "using cached $(basename "$out")"
		return
	fi
	if (( NO_FETCH )); then
		die "cache miss for $(basename "$out") and --no-fetch was requested"
	fi
	echo "fetching $url"
	rm -f "$out"
	curl -fsSL --http1.1 --retry 3 --retry-all-errors --max-time 300 \
		"$url" -o "$out" || die "download failed: $url"
	printf '%s  %s\n' "$sha" "$out" | sha256sum -c - \
		|| die "checksum mismatch for $(basename "$out"); upstream may have changed, update upstream.lock only after testing patches"
}

fetch "$MMDVMHOST_URL" "$MMDVMHOST_SHA256" "$CACHE/mmdvmhost.tar.gz"
fetch "$YSFCLIENTS_URL" "$YSFCLIENTS_SHA256" "$CACHE/ysfclients.tar.gz"

echo
echo "== extracting upstream sources =="
rm -rf "$SRC/MMDVMHost" "$SRC/YSFClients"
mkdir -p "$SRC/MMDVMHost" "$SRC/YSFClients"
tar -xzf "$CACHE/mmdvmhost.tar.gz" -C "$SRC/MMDVMHost" --strip-components=1
tar -xzf "$CACHE/ysfclients.tar.gz" -C "$SRC/YSFClients" --strip-components=1

echo
echo "== applying add-on patches =="
cd "$SRC/YSFClients"
for patch_file in "$REPO_DIR"/patches/*.patch; do
	echo "  $(basename "$patch_file")"
	patch -p1 --dry-run < "$patch_file" >/dev/null || die "patch does not apply: $patch_file"
	patch -p1 < "$patch_file" >/dev/null
done

echo
echo "== building MMDVM-Host (make -j$JOBS) =="
cd "$SRC/MMDVMHost"
make -j"$JOBS" > "$BUILD/build-mmdvmhost.log" 2>&1 || {
	tail -n 40 "$BUILD/build-mmdvmhost.log" >&2
	die "MMDVM-Host build failed (log: $BUILD/build-mmdvmhost.log)"
}

echo "== building YSFGateway (make -j$JOBS) =="
cd "$SRC/YSFClients/YSFGateway"
make -j"$JOBS" > "$BUILD/build-ysfgateway.log" 2>&1 || {
	tail -n 40 "$BUILD/build-ysfgateway.log" >&2
	die "YSFGateway build failed (log: $BUILD/build-ysfgateway.log)"
}

install -m 0755 "$SRC/MMDVMHost/MMDVM-Host" "$BIN/MMDVM-Host"
install -m 0755 "$SRC/YSFClients/YSFGateway/YSFGateway" "$BIN/YSFGateway"

echo
echo "== build result =="
ls -lh "$BIN/MMDVM-Host" "$BIN/YSFGateway"
if ldd "$BIN/MMDVM-Host" | grep -q 'not found' || ldd "$BIN/YSFGateway" | grep -q 'not found'; then
	die "a runtime library is missing; check ldd output"
fi
echo "build OK"
