#!/usr/bin/env python3
"""Validate config.env and render MMDVM/YSFGateway/OLED configuration.

This script deliberately does not source the env file through a shell.  It
parses simple KEY=value lines, validates every value, and writes only inside
the requested build directory.  Root-owned installation scripts later copy the
generated files into /etc/mmdvm.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"
DEFAULT_CONFIG = REPO / "config.env"
DEFAULT_OUTPUT = REPO / "build"

PLACEHOLDER_RE = re.compile(r"@[A-Z0-9_]+@")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


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


def render(values: dict[str, object], output: Path) -> list[Path]:
    mapping = {
        "CALLSIGN": str(values["callsign"]),
        "SUFFIX": str(values["suffix"]),
        "FREQUENCY_HZ": str(values["frequency_hz"]),
        "FREQUENCY_LABEL": str(values["frequency_label"]),
        "REFLECTOR_STARTUP": str(values["startup"]),
        "HOTSPOT_NAME": str(values["hotspot_name"]),
        "HOTSPOT_DESC": str(values["hotspot_desc"]),
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
    )
    (etc / "oled.env").write_text(oled_env, encoding="utf-8")
    written.append(etc / "oled.env")

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Render MMDVM YSF add-on configs")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="path to config.env (default: repo/config.env)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="build output directory (default: repo/build)")
    parser.add_argument("--check", action="store_true",
                        help="validate only; do not write files")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        values = validate(parse_env(args.config))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

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

    if not args.quiet:
        print(
            f"config OK: {values['callsign']} / {values['frequency_label']} MHz / "
            f"{values['startup']} ({values['host']}:{values['port']})"
        )

    if args.check:
        return 0

    try:
        written = render(values, args.output)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        for path in written:
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
