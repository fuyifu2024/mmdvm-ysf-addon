#!/usr/bin/env python3
"""Configuration helpers shared by the onboard wizard and the updater.

The add-on treats ``config.env`` as the source of truth.  This module keeps
line order and comments when editing it, imports known values from an existing
``/etc/mmdvm`` installation, and never prints secrets.
"""

from __future__ import annotations

import ast
import configparser
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = REPO_DIR / "config.example.env"
VERSION_FILE = REPO_DIR / "VERSION"
DEFAULT_CONFIG = REPO_DIR / "config.env"
ETC_DIR = Path(os.environ.get("MMDVM_ETC_DIR", "/etc/mmdvm"))
UNIT_DIR = Path(os.environ.get("MMDVM_UNIT_DIR", "/etc/systemd/system"))
BIN_DIR = Path(os.environ.get("MMDVM_BIN_DIR", "/usr/local/bin"))
BACKUP_ROOT = Path(os.environ.get("MMDVM_BACKUP_ROOT", "/var/backups/mmdvm"))
PISTAR_MARKERS = ("/etc/pistar-release", "/etc/wpsd_version", "/etc/wpsd-release")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
SECRET_RE = re.compile(r"(PASSWORD|TOKEN|SECRET|API_KEY)$", re.IGNORECASE)
EXPECTED_UNITS = ("ysfgateway", "mmdvmhost", "oled-display", "dmrgateway",
                  "dstargateway")


@dataclass
class ImportResult:
    values: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def read_version(repo: Path = REPO_DIR) -> str:
    try:
        return (repo / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def installed_version(etc: Path = ETC_DIR) -> str:
    try:
        values = parse_env(etc / "addon.env")
    except PermissionError:
        return ""
    return values.get("ADDON_VERSION", "")


def read_text(path: Path, use_sudo: bool = False) -> str:
    """Read a small text file, optionally through a cached sudo session."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except PermissionError:
        if not use_sudo:
            raise
    result = subprocess.run(["sudo", "-n", "cat", str(path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PermissionError(
            f"cannot read {path}; run 'sudo -v' first or add the user to the "
            "mmdvm group"
        )
    return result.stdout


def mask_secret(key: str, value: str) -> str:
    if SECRET_RE.search(key or ""):
        return "<hidden>" if value else "<empty>"
    return value


def parse_env(path: Path, use_sudo: bool = False) -> dict[str, str]:
    """Parse KEY=value without executing it. First occurrence wins."""
    values: dict[str, str] = {}
    try:
        text = read_text(path, use_sudo=use_sudo)
    except FileNotFoundError:
        return values
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not KEY_RE.fullmatch(key):
            continue
        if value[:1] in ("'", '"'):
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, str):
                value = parsed
        values.setdefault(key, value)
    return values


def _ini_parser(path: Path, use_sudo: bool = False) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(strict=False, interpolation=None,
                                          delimiters=("=",))
    try:
        parser.read_string(read_text(path, use_sudo=use_sudo), source=str(path))
    except FileNotFoundError:
        return configparser.RawConfigParser()
    except configparser.Error:
        return configparser.RawConfigParser()
    return parser


def ini_get(parser: configparser.RawConfigParser, section: str, key: str,
            default: str = "") -> str:
    for section_name in parser.sections():
        if section_name.lower() != section.lower():
            continue
        for option in parser.options(section_name):
            if option.lower() == key.lower():
                return parser.get(section_name, option).strip()
    return default


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "yes", "true", "on")


def _hz_to_mhz(value: str) -> str:
    try:
        hz = int(float(value))
    except (TypeError, ValueError):
        return ""
    if hz <= 0:
        return ""
    return f"{hz / 1_000_000:.4f}"


def _read_json(path: Path, use_sudo: bool = False):
    try:
        return json.loads(read_text(path, use_sudo=use_sudo))
    except FileNotFoundError:
        return None
    except (ValueError, TypeError):
        return None


def _set_if(values: dict[str, str], key: str, value: str) -> None:
    value = (value or "").strip()
    if value and not value.startswith("@"):
        values.setdefault(key, value)


def import_existing_install(etc: Path = ETC_DIR,
                            use_sudo: bool = False) -> ImportResult:
    """Import known values from a v0.1-v0.4 /etc/mmdvm installation."""
    result = ImportResult()
    values = result.values

    def parser(name: str):
        try:
            return _ini_parser(etc / name, use_sudo=use_sudo)
        except PermissionError as exc:
            result.warnings.append(str(exc))
            return configparser.RawConfigParser()

    def env(name: str):
        try:
            return parse_env(etc / name, use_sudo=use_sudo)
        except PermissionError as exc:
            result.warnings.append(str(exc))
            return {}

    host_ini = parser("MMDVM-Host.ini")
    ysf_ini = parser("YSFGateway.ini")
    oled_env = env("oled.env")
    system_env = env("system.env")
    dmr_ini = parser("DMRGateway.ini")
    dstar_cfg = parser("DStarGateway.cfg")

    _set_if(values, "CALLSIGN", ini_get(host_ini, "General", "Callsign")
            or ini_get(ysf_ini, "General", "Callsign")
            or oled_env.get("OWN_CALL", ""))
    _set_if(values, "SUFFIX", ini_get(ysf_ini, "General", "Suffix"))
    _set_if(values, "MODEM_PORT",
            system_env.get("MODEM_PORT", "") or ini_get(host_ini, "Modem", "UARTPort"))
    _set_if(values, "MODEM_BAUD", ini_get(host_ini, "Modem", "UARTSpeed"))

    frequency = (ini_get(host_ini, "Modem", "RXFrequency")
                 or ini_get(ysf_ini, "Info", "RXFrequency"))
    mhz = _hz_to_mhz(frequency)
    if not mhz:
        mhz = oled_env.get("FREQUENCY_LABEL", "")
    _set_if(values, "FREQUENCY", mhz)

    try:
        hosts = _read_json(etc / "YSFHosts.json", use_sudo=use_sudo) or {}
    except PermissionError as exc:
        result.warnings.append(str(exc))
        hosts = {}
    reflectors = hosts.get("reflectors") if isinstance(hosts, dict) else None
    if isinstance(reflectors, list) and reflectors:
        ref = reflectors[0] if isinstance(reflectors[0], dict) else {}
        _set_if(values, "REFLECTOR_ID", str(ref.get("designator", "")))
        _set_if(values, "REFLECTOR_COUNTRY", str(ref.get("country", "")))
        _set_if(values, "REFLECTOR_NAME", str(ref.get("name", "")))
        _set_if(values, "REFLECTOR_DESC", str(ref.get("description", "")))
        _set_if(values, "REFLECTOR_HOST", str(ref.get("ipv4") or ""))
        _set_if(values, "REFLECTOR_PORT", str(ref.get("port", "")))
        if ref.get("country") and ref.get("name"):
            values.setdefault("REFLECTOR_STARTUP",
                              f"{ref['country']}-{ref['name']}")
        result.sources.append(str(etc / "YSFHosts.json"))

    startup = ini_get(ysf_ini, "Network", "Startup")
    if startup and "REFLECTOR_COUNTRY" not in values:
        match = re.fullmatch(r"([A-Za-z]{2,3})-(.+)", startup)
        if match:
            values.setdefault("REFLECTOR_COUNTRY", match.group(1).upper())
            values.setdefault("REFLECTOR_NAME", match.group(2).upper())
            values.setdefault("REFLECTOR_STARTUP", startup.upper())
    _set_if(values, "REFLECTOR_ID", oled_env.get("REFLECTOR_ID", ""))
    _set_if(values, "HOTSPOT_NAME", ini_get(ysf_ini, "Info", "Name"))
    _set_if(values, "HOTSPOT_DESC",
            ini_get(ysf_ini, "Info", "Description")
            or ini_get(ysf_ini, "APRS", "Description"))

    _set_if(values, "ENABLE_OLED", system_env.get("ENABLE_OLED", ""))
    _set_if(values, "I2C_BUS",
            oled_env.get("I2C_BUS", "") or system_env.get("I2C_BUS", ""))
    _set_if(values, "I2C_ADDRESS",
            oled_env.get("I2C_ADDRESS", "") or system_env.get("I2C_ADDRESS", ""))
    for key in ("OLED_CONTROLLER", "OLED_SIZE", "OLED_ROTATE", "OLED_CONTRAST"):
        _set_if(values, key, oled_env.get(key, ""))

    dmr_enable = ini_get(host_ini, "DMR", "Enable")
    _set_if(values, "ENABLE_DMR", dmr_enable or system_env.get("ENABLE_DMR", ""))
    _set_if(values, "DMR_ID", ini_get(host_ini, "DMR", "Id")
            or ini_get(dmr_ini, "General", "Id")
            or system_env.get("DMR_ID", ""))
    _set_if(values, "DMR_COLOR_CODE", ini_get(host_ini, "DMR", "ColorCode")
            or ini_get(dmr_ini, "Info", "ColorCode"))
    _set_if(values, "DMR_MASTER", ini_get(dmr_ini, "DMR Network 1", "Address"))
    _set_if(values, "DMR_PORT", ini_get(dmr_ini, "DMR Network 1", "Port"))
    _set_if(values, "DMR_PASSWORD", ini_get(dmr_ini, "DMR Network 1", "Password"))

    dstar_enable = ini_get(host_ini, "D-Star", "Enable")
    _set_if(values, "ENABLE_DSTAR",
            dstar_enable or system_env.get("ENABLE_DSTAR", ""))
    _set_if(values, "DSTAR_MODULE",
            ini_get(host_ini, "D-Star", "Module")
            or ini_get(dstar_cfg, "Repeater_1", "band"))
    _set_if(values, "DSTAR_REFLECTOR",
            ini_get(dstar_cfg, "Repeater_1", "reflector"))

    if not values.get("ENABLE_DMR") and ini_get(dmr_ini, "DMR Network 1", "Enabled"):
        values["ENABLE_DMR"] = "1" if _truthy(
            ini_get(dmr_ini, "DMR Network 1", "Enabled")) else "0"
    if not values.get("ENABLE_DSTAR"):
        repeater_enabled = _truthy(ini_get(dstar_cfg, "Repeater_1", "enabled"))
        if repeater_enabled and values.get("DSTAR_REFLECTOR"):
            values["ENABLE_DSTAR"] = "1"

    for name in ("MMDVM-Host.ini", "YSFGateway.ini", "oled.env", "system.env",
                 "DMRGateway.ini", "DStarGateway.cfg"):
        if (etc / name).is_file():
            result.sources.append(str(etc / name))

    if not values.get("CALLSIGN"):
        result.warnings.append("existing install has no readable Callsign")
    if not values.get("REFLECTOR_HOST"):
        result.warnings.append("existing install has no readable YSF reflector host")
    return result


def _format_env_line(key: str, value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{key} must be a single line")
    return f"{key}={value}\n"


def write_text_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Replace a small config file atomically, preserving its mode by default."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        old_mode = 0o600
    if mode is None:
        mode = old_mode
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def update_env_values(path: Path, updates: dict[str, str],
                      add_missing: bool = True) -> list[str]:
    """Update values in place (line preserving) and return keys that changed."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines(keepends=True)
    pending = dict(updates)
    for key, value in pending.items():
        if not KEY_RE.fullmatch(key):
            raise ValueError(f"invalid config key: {key}")
    changed: list[str] = []
    out: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.rstrip("\n"))
        if not match or match.group(1) not in pending:
            out.append(line)
            continue
        key = match.group(1)
        new_value = pending.pop(key)
        old_value = match.group(2).strip()
        if old_value != new_value:
            changed.append(key)
        out.append(_format_env_line(key, new_value))
    if pending and add_missing:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append("\n# Added by mmdvm onboard/update for new options.\n")
        for key, value in pending.items():
            out.append(_format_env_line(key, value))
            changed.append(key)
    elif pending:
        raise ValueError("missing config keys: " + ", ".join(pending))
    write_text_atomic(path, "".join(out))
    return changed


def merge_missing_defaults(config_path: Path, example_path: Path = EXAMPLE_CONFIG,
                           add_missing: bool = True) -> list[str]:
    """Append keys present in the example but absent from the user config."""
    existing = parse_env(config_path)
    example = parse_env(example_path)
    missing = {key: value for key, value in example.items() if key not in existing}
    if not missing:
        return []
    if not add_missing:
        return sorted(missing)
    return update_env_values(config_path, missing, add_missing=True)


def validate_config(config_path: Path, repo: Path = REPO_DIR) -> tuple[bool, str]:
    cmd = [sys.executable, str(repo / "scripts" / "render_configs.py"),
           "--config", str(config_path), "--check", "--quiet"]
    result = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def candidate_diff(old: dict[str, str], new: dict[str, str]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for key in sorted(set(old) | set(new)):
        if old.get(key, "") == new.get(key, ""):
            continue
        rows.append((key, mask_secret(key, old.get(key, "")),
                     mask_secret(key, new.get(key, ""))))
    return rows


def is_pistar_wpsd() -> bool:
    return any(Path(marker).exists() for marker in PISTAR_MARKERS)


def install_state(etc: Path = ETC_DIR, unit_dir: Path = UNIT_DIR) -> str:
    """Return 'addon', 'legacy', 'foreign' or 'none'."""
    if is_pistar_wpsd():
        return "foreign"
    marker = parse_env(etc / "addon.env")
    if marker.get("ADDON") == "mmdvm-ysf-addon":
        return "addon"
    has_etc = (etc / "MMDVM-Host.ini").is_file()
    has_unit = any((unit_dir / f"{name}.service").is_file()
                   for name in ("ysfgateway", "mmdvmhost"))
    if has_etc and has_unit:
        return "legacy"
    if etc.exists() or has_unit:
        return "foreign"
    return "none"
