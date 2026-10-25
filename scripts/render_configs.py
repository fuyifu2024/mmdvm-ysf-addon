#!/usr/bin/env python3
"""Validate config.env and render MMDVM/YSFGateway/OLED configuration.

The renderer is deterministic for a given machine state.  It never sources the
env file through a shell and only writes inside the requested build directory.

Hardware notes:
  * MODEM_PORT=auto resolves to one serial device.  If several are present the
    renderer refuses to guess and asks for an explicit /dev/... path.
  * ENABLE_OLED=auto is deliberately deferred to install-services.sh, which
    runs as root and probes the configured I2C bus.  --check therefore never
    requires an OLED or a modem, which keeps CI hardware-free.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"
DEFAULT_CONFIG = REPO / "config.env"
DEFAULT_OUTPUT = REPO / "build"

PLACEHOLDER_RE = re.compile(r"@[A-Z0-9_]+@")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
SERIAL_PATTERNS = (
    "/dev/serial/by-id/*",
    "/dev/ttyAMA0",
    "/dev/serial0",
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
    "/dev/ttySC*",
)


class ConfigError(Exception):
    pass


def parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path}\n"
            f"copy {REPO / 'config.example.env'} to {DEFAULT_CONFIG} and edit it."
        )

    values: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path}:{lineno}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not KEY_RE.fullmatch(key):
            raise ConfigError(f"{path}:{lineno}: invalid key {key!r}")
        if value[:1] in ("'", '"'):
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError) as exc:
                raise ConfigError(f"{path}:{lineno}: invalid quoted value: {exc}") from exc
            if not isinstance(parsed, str):
                raise ConfigError(f"{path}:{lineno}: value must be a string")
            value = parsed
        values[key] = value
    return values


def require(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ConfigError(f"missing required key {key}")
    return value


def detect_platform() -> str:
    override = os.environ.get("MMDVM_PLATFORM")
    if override:
        return override
    model = ""
    try:
        model = Path("/proc/device-tree/model").read_bytes().replace(b"\0", b"").decode(
            "utf-8", "replace"
        )
    except OSError:
        pass
    if "Raspberry Pi 5" in model:
        return "pi5"
    if any(token in model for token in (
        "Raspberry Pi 4", "Raspberry Pi 3", "Raspberry Pi Zero 2",
        "Compute Module 4", "Compute Module 3",
    )):
        return "pi_legacy"
    if "Raspberry Pi" in model:
        return "pi_other"
    if model:
        return "arm-other"
    machine = os.uname().machine
    if machine in ("x86_64", "amd64", "i386", "i486", "i586", "i686"):
        return "x86"
    return "other"


def modem_candidates() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for pattern in SERIAL_PATTERNS:
        for path in sorted(glob.glob(pattern)):
            real = str(Path(path).resolve())
            if real in seen:
                continue
            seen.add(real)
            result.append(path)
    return result


def resolve_modem_port(requested: str, check: bool) -> str | None:
    if requested != "auto":
        if not Path(requested).exists():
            message = (
                f"serial device does not exist: {requested}; "
                "connect the modem or set MODEM_PORT to the correct /dev/... path"
            )
            if check:
                print(f"warning: {message}", file=sys.stderr)
                return requested
            raise ConfigError(message)
        return requested

    candidates = modem_candidates()
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        message = (
            "no serial modem found; connect a USB/GPIO modem or set MODEM_PORT "
            "explicitly"
        )
        if check:
            print(f"warning: {message}", file=sys.stderr)
            return None
        raise ConfigError(message)

    message = (
        "multiple serial devices found; refusing to guess. Set MODEM_PORT to one of: "
        + ", ".join(candidates)
    )
    if check:
        print(f"warning: {message}", file=sys.stderr)
        return None
    raise ConfigError(message)


def validate(values: dict[str, str]) -> dict[str, object]:
    callsign = require(values, "CALLSIGN").upper()
    if not re.fullmatch(r"[A-Z0-9]{3,10}", callsign):
        raise ConfigError("CALLSIGN must be 3-10 uppercase letters/digits")

    suffix = values.get("SUFFIX", "ND").strip().upper() or "ND"
    if not re.fullmatch(r"[A-Z0-9]{1,3}", suffix):
        raise ConfigError("SUFFIX must be 1-3 uppercase letters/digits")

    frequency_text = require(values, "FREQUENCY")
    try:
        frequency = float(frequency_text)
    except ValueError as exc:
        raise ConfigError("FREQUENCY must be a number in MHz, e.g. 438.8000") from exc
    if not (144.0 <= frequency <= 148.0 or 420.0 <= frequency <= 450.0
            or 862.0 <= frequency <= 950.0):
        raise ConfigError(
            "FREQUENCY must be in an amateur 2m/70cm/33cm range "
            "(144-148, 420-450, or 862-950 MHz)"
        )
    frequency_hz = int(round(frequency * 1_000_000))
    frequency_label = f"{frequency:.3f}"

    reflector_id = require(values, "REFLECTOR_ID")
    if not re.fullmatch(r"[0-9]{1,5}", reflector_id):
        raise ConfigError("REFLECTOR_ID must be 1-5 digits")

    country = require(values, "REFLECTOR_COUNTRY").upper()
    if not re.fullmatch(r"[A-Z]{2,3}", country):
        raise ConfigError("REFLECTOR_COUNTRY must be 2-3 uppercase letters")

    name = require(values, "REFLECTOR_NAME").upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,13}", name):
        raise ConfigError(
            "REFLECTOR_NAME must be 1-14 uppercase letters/digits/hyphens "
            "(the short name, without the country prefix)"
        )

    startup = values.get("REFLECTOR_STARTUP", "").strip().upper()
    expected_startup = f"{country}-{name}"
    if not startup:
        startup = expected_startup
    if startup != expected_startup:
        raise ConfigError(
            f"REFLECTOR_STARTUP must be {expected_startup!r} "
            f"(COUNTRY + '-' + NAME), got {startup!r}"
        )
    if len(startup) > 16:
        raise ConfigError("REFLECTOR_STARTUP must fit in 16 characters")

    host = require(values, "REFLECTOR_HOST")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        raise ConfigError("REFLECTOR_HOST must be an IPv4 address or hostname")

    try:
        port = int(require(values, "REFLECTOR_PORT"))
    except ValueError as exc:
        raise ConfigError("REFLECTOR_PORT must be a number") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("REFLECTOR_PORT must be between 1 and 65535")

    description = values.get("REFLECTOR_DESC", name).strip() or name
    hotspot_name = values.get("HOTSPOT_NAME", f"{callsign} YSF Hotspot").strip()
    hotspot_desc = values.get("HOTSPOT_DESC", "MMDVM YSF add-on").strip()
    for label, text in (("HOTSPOT_NAME", hotspot_name),
                        ("HOTSPOT_DESC", hotspot_desc)):
        if "\n" in text or "\r" in text:
            raise ConfigError(f"{label} must be a single line")

    modem_port = values.get("MODEM_PORT", "auto").strip() or "auto"
    if modem_port != "auto":
        if not modem_port.startswith("/dev/") or re.search(r"\s", modem_port):
            raise ConfigError("MODEM_PORT must be 'auto' or an absolute /dev/ path")

    try:
        modem_baud = int(values.get("MODEM_BAUD", "115200").strip() or "115200")
    except ValueError as exc:
        raise ConfigError("MODEM_BAUD must be a number") from exc
    if not 1200 <= modem_baud <= 921600:
        raise ConfigError("MODEM_BAUD must be between 1200 and 921600")

    enable_oled_raw = values.get("ENABLE_OLED", "auto").strip().lower() or "auto"
    oled_aliases = {
        "auto": "auto", "1": "1", "yes": "1", "true": "1", "on": "1",
        "0": "0", "no": "0", "false": "0", "off": "0",
    }
    if enable_oled_raw not in oled_aliases:
        raise ConfigError("ENABLE_OLED must be auto, 1/yes, or 0/no")
    enable_oled = oled_aliases[enable_oled_raw]

    try:
        i2c_bus = int(values.get("I2C_BUS", "1").strip() or "1")
    except ValueError as exc:
        raise ConfigError("I2C_BUS must be a number") from exc
    if not 0 <= i2c_bus <= 20:
        raise ConfigError("I2C_BUS must be between 0 and 20")

    try:
        i2c_address = int(values.get("I2C_ADDRESS", "0x3C").strip() or "0x3C", 0)
    except ValueError as exc:
        raise ConfigError("I2C_ADDRESS must be a number such as 0x3C") from exc
    if not 0x03 <= i2c_address <= 0x77:
        raise ConfigError("I2C_ADDRESS must be a 7-bit address between 0x03 and 0x77")

    return {
        "callsign": callsign,
        "suffix": suffix,
        "frequency": frequency,
        "frequency_hz": frequency_hz,
        "frequency_label": frequency_label,
        "reflector_id": reflector_id,
        "country": country,
        "name": name,
        "startup": startup,
        "description": description,
        "host": host,
        "port": port,
        "hotspot_name": hotspot_name,
        "hotspot_desc": hotspot_desc,
        "modem_port": modem_port,
        "modem_baud": modem_baud,
        "enable_oled": enable_oled,
        "i2c_bus": i2c_bus,
        "i2c_address": i2c_address,
    }


def template(name: str) -> str:
    path = TEMPLATES / name
    if not path.is_file():
        raise ConfigError(f"missing template: {path}")
    return path.read_text(encoding="utf-8")


def expand(text: str, mapping: dict[str, str]) -> str:
    result = text
    for key, value in mapping.items():
        result = result.replace(f"@{key}@", value)
    remaining = PLACEHOLDER_RE.search(result)
    if remaining:
        raise ConfigError(f"unresolved template placeholder {remaining.group(0)}")
    return result


def render(values: dict[str, object], output: Path, platform: str) -> list[Path]:
    mapping = {
        "CALLSIGN": str(values["callsign"]),
        "SUFFIX": str(values["suffix"]),
        "FREQUENCY_HZ": str(values["frequency_hz"]),
        "FREQUENCY_LABEL": str(values["frequency_label"]),
        "REFLECTOR_STARTUP": str(values["startup"]),
        "HOTSPOT_NAME": str(values["hotspot_name"]),
        "HOTSPOT_DESC": str(values["hotspot_desc"]),
        "MODEM_PORT": str(values["modem_port"]),
        "MODEM_BAUD": str(values["modem_baud"]),
    }

    etc = output / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    mmdvm_ini = expand(template("MMDVM-Host.ini.in"), mapping)
    (etc / "MMDVM-Host.ini").write_text(mmdvm_ini, encoding="utf-8")
    written.append(etc / "MMDVM-Host.ini")

    gateway_ini = expand(template("YSFGateway.ini.in"), mapping)
    (etc / "YSFGateway.ini").write_text(gateway_ini, encoding="utf-8")
    written.append(etc / "YSFGateway.ini")

    hosts = {
        "reflectors": [
            {
                "designator": str(values["reflector_id"]),
                "country": str(values["country"]),
                "name": str(values["name"]),
                "use_xx_prefix": False,
                "user_count": "000",
                "description": str(values["description"]),
                "port": int(values["port"]),
                "ipv4": str(values["host"]),
                "ipv6": None,
            }
        ]
    }
    (etc / "YSFHosts.json").write_text(
        json.dumps(hosts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    written.append(etc / "YSFHosts.json")

    oled_env = (
        f"OWN_CALL={values['callsign']}\n"
        f"REFLECTOR_ID={values['reflector_id']}\n"
        f"FREQUENCY_LABEL={values['frequency_label']}\n"
        f"I2C_BUS={values['i2c_bus']}\n"
        f"I2C_ADDRESS=0x{int(values['i2c_address']):02X}\n"
    )
    (etc / "oled.env").write_text(oled_env, encoding="utf-8")
    written.append(etc / "oled.env")

    system_env = (
        f"MODEM_PORT={values['modem_port']}\n"
        f"MODEM_BAUD={values['modem_baud']}\n"
        f"ENABLE_OLED={values['enable_oled']}\n"
        f"I2C_BUS={values['i2c_bus']}\n"
        f"I2C_ADDRESS=0x{int(values['i2c_address']):02X}\n"
        f"PLATFORM={platform}\n"
    )
    (etc / "system.env").write_text(system_env, encoding="utf-8")
    written.append(etc / "system.env")

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Render MMDVM YSF add-on configs")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="path to config.env (default: repo/config.env)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="build output directory (default: repo/build)")
    parser.add_argument("--check", action="store_true",
                        help="validate only; no hardware required and no files written")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        values = validate(parse_env(args.config))
        platform = detect_platform()
        resolved_port = resolve_modem_port(str(values["modem_port"]), args.check)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if resolved_port is not None:
        values["modem_port"] = resolved_port

    if not args.quiet:
        print(f"platform: {platform}")
        print(
            f"config OK: {values['callsign']} / {values['frequency_label']} MHz / "
            f"{values['startup']} ({values['host']}:{values['port']})"
        )
        print(f"modem: {values['modem_port']} @ {values['modem_baud']} baud")
        print(
            f"OLED: {values['enable_oled']} (I2C bus {values['i2c_bus']}, "
            f"address 0x{int(values['i2c_address']):02X})"
        )
        if values["enable_oled"] == "auto":
            print("      auto OLED probing happens during install (as root)")

    if str(values["callsign"]) == "N0CALL" and not args.quiet:
        print("warning: CALLSIGN is N0CALL; edit config.env before transmitting.",
              file=sys.stderr)
    if abs(float(values["frequency"]) - 144.3) < 0.0005 and not args.quiet:
        print(
            "warning: 144.300 MHz is the 2m weak-signal/SSB calling area. "
            "Use 438.8000 for a conventional hotspot unless you have a specific "
            "coordinated reason.",
            file=sys.stderr,
        )
    if int(values["modem_baud"]) != 115200 and not args.quiet:
        print("warning: 115200 is the tested MMDVM_HS baud rate; "
              "other values are experimental.", file=sys.stderr)

    if args.check:
        return 0

    try:
        written = render(values, args.output, platform)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        for path in written:
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
