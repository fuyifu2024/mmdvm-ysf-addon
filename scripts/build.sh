#!/usr/bin/env bash
# Fetch pinned upstream sources, apply the YSF patches, and build.
# YSF is always built. DMRGateway and DStarGateway are optional and built only
# when ENABLE_DMR / ENABLE_DSTAR is set in build/etc/system.env or the env.
# Runs as your normal user; prepare-system.sh provides the build dependencies.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$REPO_DIR/build"
CACHE="$BUILD/cache"
SRC="$BUILD/src"
BIN="$BUILD/bin"
SHARE="$BUILD/share"
JOBS="${JOBS:-2}"
NO_FETCH=0

for arg in "$@"; do
	case "$arg" in
		--no-fetch) NO_FETCH=1 ;;
		-h|--help)
			echo "usage: $0 [--no-fetch]"
			echo "  JOBS=N  parallel make jobs (default 2; setup uses 1 on low memory)"
			echo "  ENABLE_DMR=1 ENABLE_DSTAR=1 can override build/etc/system.env"
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
file_value() {
	local key="$1" file="$BUILD/etc/system.env"
	[[ -f "$file" ]] || return 0
	awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$file"
}
as_bool() {
	case "${1,,}" in
		1|yes|true|on) echo 1 ;;
		*) echo 0 ;;
	esac
}

if [[ -n "${ENABLE_DMR:-}" ]]; then
	BUILD_DMR="$(as_bool "$ENABLE_DMR")"
else
	BUILD_DMR="$(as_bool "$(file_value ENABLE_DMR)")"
fi
if [[ -n "${ENABLE_DSTAR:-}" ]]; then
	BUILD_DSTAR="$(as_bool "$ENABLE_DSTAR")"
else
	BUILD_DSTAR="$(as_bool "$(file_value ENABLE_DSTAR)")"
fi

MMDVMHOST_URL="$(get_lock MMDVMHOST_URL)"
MMDVMHOST_SHA256="$(get_lock MMDVMHOST_SHA256)"
YSFCLIENTS_URL="$(get_lock YSFCLIENTS_URL)"
YSFCLIENTS_SHA256="$(get_lock YSFCLIENTS_SHA256)"
[[ -n "$MMDVMHOST_URL" && -n "$MMDVMHOST_SHA256" ]] || die "incomplete MMDVMHost entry in upstream.lock"
[[ -n "$YSFCLIENTS_URL" && -n "$YSFCLIENTS_SHA256" ]] || die "incomplete YSFClients entry in upstream.lock"

if (( BUILD_DMR )); then
	DMRGATEWAY_URL="$(get_lock DMRGATEWAY_URL)"
	DMRGATEWAY_SHA256="$(get_lock DMRGATEWAY_SHA256)"
	[[ -n "$DMRGATEWAY_URL" && -n "$DMRGATEWAY_SHA256" ]] || die "incomplete DMRGateway entry in upstream.lock"
fi
if (( BUILD_DSTAR )); then
	DSTARGATEWAY_URL="$(get_lock DSTARGATEWAY_URL)"
	DSTARGATEWAY_SHA256="$(get_lock DSTARGATEWAY_SHA256)"
	[[ -n "$DSTARGATEWAY_URL" && -n "$DSTARGATEWAY_SHA256" ]] || die "incomplete DStarGateway entry in upstream.lock"
fi

for tool in curl tar sha256sum make c++ patch; do
	command -v "$tool" >/dev/null 2>&1 || die "missing build tool: $tool (run sudo ./scripts/prepare-system.sh)"
done
[[ -f /usr/include/mosquitto.h ]] || die "libmosquitto-dev is missing (run sudo ./scripts/prepare-system.sh)"
[[ -f /usr/include/nlohmann/json.hpp ]] || die "nlohmann-json3-dev is missing (run sudo ./scripts/prepare-system.sh)"
if (( BUILD_DSTAR )); then
	# Multiarch distros put curl.h under /usr/include/<triplet>/curl, so probe
	# with the compiler instead of assuming a fixed header path.
	printf '#include <curl/curl.h>\n' | c++ -E -x c++ - >/dev/null 2>&1 \
		|| die "libcurl4-openssl-dev is missing (run sudo ./scripts/prepare-system.sh)"
	printf '#include <boost/version.hpp>\n' | c++ -E -x c++ - >/dev/null 2>&1 \
		|| die "libboost-dev is missing (run sudo ./scripts/prepare-system.sh)"
fi

echo "build modes: YSF=on DMR=$([[ $BUILD_DMR == 1 ]] && echo on || echo off) D-Star=$([[ $BUILD_DSTAR == 1 ]] && echo on || echo off)"
mkdir -p "$CACHE" "$SRC" "$BIN" "$SHARE"

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
if (( BUILD_DMR )); then
	fetch "$DMRGATEWAY_URL" "$DMRGATEWAY_SHA256" "$CACHE/dmrgateway.tar.gz"
fi
if (( BUILD_DSTAR )); then
	fetch "$DSTARGATEWAY_URL" "$DSTARGATEWAY_SHA256" "$CACHE/dstargateway.tar.gz"
fi

echo
echo "== extracting upstream sources =="
rm -rf "$SRC/MMDVMHost" "$SRC/YSFClients"
mkdir -p "$SRC/MMDVMHost" "$SRC/YSFClients"
tar -xzf "$CACHE/mmdvmhost.tar.gz" -C "$SRC/MMDVMHost" --strip-components=1
tar -xzf "$CACHE/ysfclients.tar.gz" -C "$SRC/YSFClients" --strip-components=1
if (( BUILD_DMR )); then
	rm -rf "$SRC/DMRGateway"
	mkdir -p "$SRC/DMRGateway"
	tar -xzf "$CACHE/dmrgateway.tar.gz" -C "$SRC/DMRGateway" --strip-components=1
fi
if (( BUILD_DSTAR )); then
	rm -rf "$SRC/DStarGateway"
	mkdir -p "$SRC/DStarGateway"
	tar -xzf "$CACHE/dstargateway.tar.gz" -C "$SRC/DStarGateway" --strip-components=1
fi

echo
echo "== applying YSF add-on patches =="
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

if (( BUILD_DMR )); then
	echo "== building DMRGateway (make -j$JOBS) =="
	cd "$SRC/DMRGateway"
	make -j"$JOBS" > "$BUILD/build-dmrgateway.log" 2>&1 || {
		tail -n 40 "$BUILD/build-dmrgateway.log" >&2
		die "DMRGateway build failed (log: $BUILD/build-dmrgateway.log)"
	}
	install -m 0755 "$SRC/DMRGateway/DMRGateway" "$BIN/DMRGateway"
fi

if (( BUILD_DSTAR )); then
	echo "== building DStarGateway (make -j$JOBS) =="
	cd "$SRC/DStarGateway"
	# GCC 14+ treats the unchecked write() in DStarGateway's Daemon.cpp as an
	# error under its own -Werror, and its stricter transitive includes leave
	# some files without <cstdint>/<cstring>.  Keep other warnings fatal; work
	# around just these known issues in the pinned upstream.
	DSTAR_CPPFLAGS="-W -O3 -Wall -Werror -Wno-error=unused-result -include cstdint -include cstring -std=c++17"
	make -j"$JOBS" DStarGateway/dstargateway CPPFLAGS="$DSTAR_CPPFLAGS" \
		> "$BUILD/build-dstargateway.log" 2>&1 || {
		tail -n 40 "$BUILD/build-dstargateway.log" >&2
		die "DStarGateway build failed (log: $BUILD/build-dstargateway.log)"
	}
	install -m 0755 "$SRC/DStarGateway/DStarGateway/dstargateway" "$BIN/dstargateway"
	# The initial reflector host lists and voice prompts are required at
	# startup; DStarGateway can update them later in the writable data dir.
	rm -rf "$SHARE/dstargateway.d"
	mkdir -p "$SHARE/dstargateway.d"
	cp -a "$SRC/DStarGateway/Data/." "$SHARE/dstargateway.d/"
fi

echo
echo "== build result =="
ls -lh "$BIN/MMDVM-Host" "$BIN/YSFGateway"
(( BUILD_DMR )) && ls -lh "$BIN/DMRGateway"
(( BUILD_DSTAR )) && ls -lh "$BIN/dstargateway"
for binary in MMDVM-Host YSFGateway DMRGateway dstargateway; do
	[[ -f "$BIN/$binary" ]] || continue
	if ldd "$BIN/$binary" | grep -q 'not found'; then
		die "a runtime library is missing for $binary; check ldd output"
	fi
done
echo "build OK"
