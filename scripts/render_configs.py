#!/usr/bin/env python3
"""Validate config.env and render MMDVMHost/YSFGateway/optional gateway configs.

YSF is always enabled.  DMR (DMRGateway) and D-Star (DStarGateway) templates are
rendered only when ENABLE_DMR / ENABLE_DSTAR are set, so the default install
stays YSF-only.  The OLED settings are validated here and written to oled.env.

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


def parse_bool(value: str | None, label: str, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    lowered = value.strip().lower()
    if lowered in ("1", "yes", "true", "on"):
        return True
    if lowered in ("0", "no", "false", "off"):
        return False
    raise ConfigError(f"{label} must be 0/1, no/yes, false/true, or off/on")


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

    controller_raw = values.get("OLED_CONTROLLER", "auto").strip().lower() or "auto"
    controller_aliases = {
        "auto": "ssd1306", "ssd1306": "ssd1306", "ssd1309": "ssd1306",
        "ssd1315": "ssd1306", "sh1106": "sh1106",
    }
    if controller_raw not in controller_aliases:
        raise ConfigError("OLED_CONTROLLER must be auto, ssd1306, ssd1309, ssd1315 or sh1106")
    oled_controller = controller_aliases[controller_raw]

    oled_size = values.get("OLED_SIZE", "128x64").strip().lower() or "128x64"
    size_map = {"128x64": (128, 64), "128x32": (128, 32), "64x48": (64, 48)}
    if oled_size not in size_map:
        raise ConfigError("OLED_SIZE must be 128x64, 128x32 or 64x48")
    if oled_controller == "sh1106" and oled_size != "128x64":
        raise ConfigError("SH1106 support is limited to 128x64 in this add-on")
    oled_width, oled_height = size_map[oled_size]

    try:
        oled_rotate = int(values.get("OLED_ROTATE", "0").strip() or "0")
    except ValueError as exc:
        raise ConfigError("OLED_ROTATE must be 0 or 180") from exc
    if oled_rotate not in (0, 180):
        raise ConfigError("OLED_ROTATE must be 0 or 180")

    try:
        oled_contrast = int(values.get("OLED_CONTRAST", "0xCF").strip() or "0xCF", 0)
    except ValueError as exc:
        raise ConfigError("OLED_CONTRAST must be a number such as 0xCF") from exc
    if not 0 <= oled_contrast <= 255:
        raise ConfigError("OLED_CONTRAST must be between 0 and 255")

    enable_dmr = parse_bool(values.get("ENABLE_DMR"), "ENABLE_DMR")
    dmr_id = 0
    dmr_master = ""
    dmr_port = 62031
    dmr_password = ""
    dmr_color_code = 1
    if enable_dmr:
        try:
            dmr_id = int(require(values, "DMR_ID"))
        except ValueError as exc:
            raise ConfigError("DMR_ID must be a number") from exc
        if not 1 <= dmr_id <= 9999999:
            raise ConfigError("DMR_ID must be between 1 and 9999999")
        dmr_master = require(values, "DMR_MASTER")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", dmr_master):
            raise ConfigError("DMR_MASTER must be a hostname or IPv4 address")
        try:
            dmr_port = int(values.get("DMR_PORT", "62031").strip() or "62031")
        except ValueError as exc:
            raise ConfigError("DMR_PORT must be a number") from exc
        if not 1 <= dmr_port <= 65535:
            raise ConfigError("DMR_PORT must be between 1 and 65535")
        dmr_password = values.get("DMR_PASSWORD", "").strip()
        # Printable ASCII, no spaces: BrandMeister passwords may contain
        # punctuation, but the INI format has no quoting for whitespace.
        if not re.fullmatch(r"[!-~]{6,64}", dmr_password):
            raise ConfigError(
                "DMR_PASSWORD must be 6-64 printable non-space characters"
            )
        try:
            dmr_color_code = int(values.get("DMR_COLOR_CODE", "1").strip() or "1")
        except ValueError as exc:
            raise ConfigError("DMR_COLOR_CODE must be a number") from exc
        if not 0 <= dmr_color_code <= 15:
            raise ConfigError("DMR_COLOR_CODE must be between 0 and 15")

    enable_dstar = parse_bool(values.get("ENABLE_DSTAR"), "ENABLE_DSTAR")
    dstar_reflector = ""
    dstar_module = "C"
    if enable_dstar:
        if len(callsign) > 8:
            raise ConfigError("D-Star callsigns are limited to 8 characters")
        dstar_reflector = require(values, "DSTAR_REFLECTOR").strip()
        match = re.fullmatch(r"([A-Za-z0-9-]+)[ \t]+([A-Da-d])", dstar_reflector)
        if not match:
            raise ConfigError(
                "DSTAR_REFLECTOR must look like 'REF001 C' (reflector name + module A-D)"
            )
        dstar_reflector = f"{match.group(1).upper()} {match.group(2).upper()}"
        dstar_module = values.get("DSTAR_MODULE", "C").strip().upper() or "C"
        if not re.fullmatch(r"[A-D]", dstar_module):
            raise ConfigError("DSTAR_MODULE must be A, B, C or D")

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
        "oled_controller": oled_controller,
        "oled_size": oled_size,
        "oled_width": oled_width,
        "oled_height": oled_height,
        "oled_rotate": oled_rotate,
        "oled_contrast": oled_contrast,
        "enable_dmr": enable_dmr,
        "dmr_id": dmr_id,
        "dmr_master": dmr_master,
        "dmr_port": dmr_port,
        "dmr_password": dmr_password,
        "dmr_color_code": dmr_color_code,
        "enable_dstar": enable_dstar,
        "dstar_reflector": dstar_reflector,
        "dstar_module": dstar_module,
        "mmdvm_id": dmr_id if enable_dmr else 0,
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
        "ENABLE_DMR": "1" if values["enable_dmr"] else "0",
        "ENABLE_DSTAR": "1" if values["enable_dstar"] else "0",
        "DMR_ID": str(values["dmr_id"]),
        "MMDVM_ID": str(values["mmdvm_id"]),
        "DMR_MASTER": str(values["dmr_master"]),
        "DMR_PORT": str(values["dmr_port"]),
        "DMR_PASSWORD": str(values["dmr_password"]),
        "DMR_COLOR_CODE": str(values["dmr_color_code"]),
        "DSTAR_REFLECTOR": str(values["dstar_reflector"]),
        "DSTAR_MODULE": str(values["dstar_module"]),
        "FREQUENCY_MHZ": str(values["frequency_label"]),
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

    if values["enable_dmr"]:
        dmr_ini = expand(template("DMRGateway.ini.in"), mapping)
        (etc / "DMRGateway.ini").write_text(dmr_ini, encoding="utf-8")
        written.append(etc / "DMRGateway.ini")

    if values["enable_dstar"]:
        dstar_cfg = expand(template("DStarGateway.cfg.in"), mapping)
        (etc / "DStarGateway.cfg").write_text(dstar_cfg, encoding="utf-8")
        written.append(etc / "DStarGateway.cfg")

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
        f"OLED_CONTROLLER={values['oled_controller']}\n"
        f"OLED_SIZE={values['oled_size']}\n"
        f"OLED_ROTATE={values['oled_rotate']}\n"
        f"OLED_CONTRAST=0x{int(values['oled_contrast']):02X}\n"
    )
    (etc / "oled.env").write_text(oled_env, encoding="utf-8")
    written.append(etc / "oled.env")

    system_env = (
        f"MODEM_PORT={values['modem_port']}\n"
        f"MODEM_BAUD={values['modem_baud']}\n"
        f"ENABLE_OLED={values['enable_oled']}\n"
        f"ENABLE_DMR={'1' if values['enable_dmr'] else '0'}\n"
        f"ENABLE_DSTAR={'1' if values['enable_dstar'] else '0'}\n"
        f"DMR_ID={values['dmr_id']}\n"
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
            f"modes: YSF=on DMR={'on' if values['enable_dmr'] else 'off'} "
            f"D-Star={'on' if values['enable_dstar'] else 'off'}"
        )
        if values["enable_dmr"]:
            print(f"DMR: ID {values['dmr_id']} -> {values['dmr_master']}:{values['dmr_port']} "
                  "(password stored locally)")
        if values["enable_dstar"]:
            print(f"D-Star: {values['dstar_reflector']} module {values['dstar_module']}")
        print(
            f"OLED: {values['enable_oled']} (I2C bus {values['i2c_bus']}, "
            f"address 0x{int(values['i2c_address']):02X}, "
            f"{values['oled_controller']} {values['oled_size']})"
        )
        if values["enable_oled"] == "auto":
            print("      auto OLED probing happens during install (as root)")
        if values["oled_controller"] == "sh1106" or values["oled_size"] != "128x64":
            print("      non-default OLED controller/size is implemented but NOT hardware-tested.")
        if values["enable_dmr"] or values["enable_dstar"]:
            print("      DMR/D-Star are optional and NOT hardware-tested; "
                  "they also require network credentials/registration.")

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
    if values["enable_dmr"] and not args.quiet:
        print("warning: DMR support is optional and not hardware-tested; "
              "keep the DMR password local (config.env and DMRGateway.ini).",
              file=sys.stderr)
    if values["enable_dstar"] and not args.quiet:
        print("warning: D-Star support is optional and not hardware-tested; "
              "DStarGateway must be built and a registered callsign may be required.",
              file=sys.stderr)

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
