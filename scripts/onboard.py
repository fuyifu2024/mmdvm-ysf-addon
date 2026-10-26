#!/usr/bin/env python3
"""Interactive station configuration wizard for an installed MMDVM add-on.

This is the "quick change" path.  It never reinstalls packages or UART support;
it edits config.env, validates it with render_configs.py, and then hands the
candidate to safe-install.sh (snapshot, render, optional build, install,
service verification, rollback on failure).
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from addon_config import (
    DEFAULT_CONFIG,
    ETC_DIR,
    EXAMPLE_CONFIG,
    ImportResult,
    candidate_diff,
    import_existing_install,
    install_state,
    mask_secret,
    merge_missing_defaults,
    parse_env,
    read_version,
    update_env_values,
    validate_config,
    write_text_atomic,
)

REPO_DIR = Path(__file__).resolve().parent.parent
SECRET_KEYS = {"DMR_PASSWORD"}


def eprint(*args) -> None:
    print(*args, file=sys.stderr)


def ask(label: str, current: str, secret: bool = False) -> str:
    if secret:
        shown = "<hidden>" if current else "<empty>"
        value = getpass.getpass(f"{label} [{shown}, blank keeps]: ")
        return value if value else current
    value = input(f"{label} [{current}]: ").strip()
    return value if value else current


def ask_yes(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes", "1", "true", "on")


def valid_bool(value: str) -> bool:
    return value.strip().lower() in ("0", "1", "auto", "yes", "no", "true",
                                     "false", "on", "off")


def fmt_frequency(value: str) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return value


def current_value(values: dict[str, str], updates: dict[str, str], key: str,
                  default: str = "") -> str:
    return updates.get(key, values.get(key, default))


def is_on(value: str) -> bool:
    return value.strip().lower() in ("1", "yes", "true", "on")


def show_brief(values: dict[str, str], updates: dict[str, str]) -> None:
    def val(key, default=""):
        return current_value(values, updates, key, default)

    print()
    print(f"  callsign:   {val('CALLSIGN', 'N0CALL')}")
    print(f"  frequency:  {val('FREQUENCY', '438.8000')} MHz")
    print(f"  YSF:        {val('REFLECTOR_ID', '?')} "
          f"{val('REFLECTOR_COUNTRY', '?')}-{val('REFLECTOR_NAME', '?')}")
    print(f"  modem:      {val('MODEM_PORT', 'auto')} @ "
          f"{val('MODEM_BAUD', '115200')}")
    print(f"  OLED:       {val('ENABLE_OLED', 'auto')} / "
          f"{val('OLED_CONTROLLER', 'auto')} / {val('OLED_SIZE', '128x64')} "
          f"rotate={val('OLED_ROTATE', '0')}")
    print(f"  DMR:        {'on' if is_on(val('ENABLE_DMR', '0')) else 'off'}"
          f" (ID {val('DMR_ID', '0')})")
    print(f"  D-Star:     {'on' if is_on(val('ENABLE_DSTAR', '0')) else 'off'}"
          f" ({val('DSTAR_REFLECTOR', '-')})")
    print()


def show_all(values: dict[str, str], updates: dict[str, str]) -> None:
    merged = dict(values)
    merged.update(updates)
    print()
    for key in sorted(merged):
        print(f"  {key}={mask_secret(key, merged[key])}")
    if "DMR_PASSWORD" not in merged:
        print("  DMR_PASSWORD=<unset>")
    print()


def edit_station(values, updates) -> None:
    updates["CALLSIGN"] = ask("Callsign", current_value(values, updates, "CALLSIGN")).upper()
    freq = ask("Frequency MHz", current_value(values, updates, "FREQUENCY", "438.8000"))
    updates["FREQUENCY"] = fmt_frequency(freq)
    updates["SUFFIX"] = ask("YSF suffix", current_value(values, updates, "SUFFIX", "ND")).upper()
    updates["HOTSPOT_NAME"] = ask("Hotspot name",
                                  current_value(values, updates, "HOTSPOT_NAME"))
    updates["HOTSPOT_DESC"] = ask("Hotspot description",
                                  current_value(values, updates, "HOTSPOT_DESC"))


def edit_reflector(values, updates) -> None:
    rid = ask("YSF reflector ID", current_value(values, updates, "REFLECTOR_ID"))
    country = ask("Reflector country prefix",
                  current_value(values, updates, "REFLECTOR_COUNTRY")).upper()
    name = ask("Reflector short name",
               current_value(values, updates, "REFLECTOR_NAME")).upper()
    updates["REFLECTOR_ID"] = rid
    updates["REFLECTOR_COUNTRY"] = country
    updates["REFLECTOR_NAME"] = name
    updates["REFLECTOR_STARTUP"] = f"{country}-{name}" if country and name else ""
    updates["REFLECTOR_DESC"] = ask("Reflector description",
                                    current_value(values, updates, "REFLECTOR_DESC"))
    updates["REFLECTOR_HOST"] = ask("Reflector host/IP",
                                    current_value(values, updates, "REFLECTOR_HOST"))
    updates["REFLECTOR_PORT"] = ask("Reflector UDP port",
                                    current_value(values, updates, "REFLECTOR_PORT", "42000"))


def edit_modem(values, updates) -> None:
    updates["MODEM_PORT"] = ask("Serial port (auto or /dev/...)",
                                current_value(values, updates, "MODEM_PORT", "auto"))
    updates["MODEM_BAUD"] = ask("Baud rate",
                                current_value(values, updates, "MODEM_BAUD", "115200"))


def edit_oled(values, updates) -> None:
    while True:
        enable = ask("OLED enable (auto/1/0)",
                     current_value(values, updates, "ENABLE_OLED", "auto")).lower()
        if valid_bool(enable):
            break
        eprint("  use auto, 1 or 0")
    updates["ENABLE_OLED"] = enable
    if enable in ("0", "no", "false", "off"):
        return
    updates["I2C_BUS"] = ask("I2C bus", current_value(values, updates, "I2C_BUS", "1"))
    updates["I2C_ADDRESS"] = ask("I2C address",
                                 current_value(values, updates, "I2C_ADDRESS", "0x3C"))
    updates["OLED_CONTROLLER"] = ask(
        "Controller (auto/ssd1306/ssd1309/ssd1315/sh1106)",
        current_value(values, updates, "OLED_CONTROLLER", "auto")).lower()
    updates["OLED_SIZE"] = ask("Size (128x64/128x32/64x48)",
                               current_value(values, updates, "OLED_SIZE", "128x64")).lower()
    updates["OLED_ROTATE"] = ask("Rotation (0/180)",
                                 current_value(values, updates, "OLED_ROTATE", "0"))
    updates["OLED_CONTRAST"] = ask("Contrast (0x00-0xFF)",
                                   current_value(values, updates, "OLED_CONTRAST", "0xCF"))


def edit_dmr(values, updates) -> None:
    enabled = ask_yes("Enable DMR",
                      is_on(current_value(values, updates, "ENABLE_DMR", "0")))
    updates["ENABLE_DMR"] = "1" if enabled else "0"
    if not enabled:
        return
    updates["DMR_ID"] = ask("DMR ID", current_value(values, updates, "DMR_ID", "0"))
    updates["DMR_MASTER"] = ask("BrandMeister master",
                                current_value(values, updates,
                                              "DMR_MASTER",
                                              "4601.master.brandmeister.network"))
    updates["DMR_PORT"] = ask("BrandMeister port",
                              current_value(values, updates, "DMR_PORT", "62031"))
    updates["DMR_COLOR_CODE"] = ask("DMR color code",
                                    current_value(values, updates, "DMR_COLOR_CODE", "1"))
    current = current_value(values, updates, "DMR_PASSWORD")
    updates["DMR_PASSWORD"] = ask("BrandMeister password", current, secret=True)


def edit_dstar(values, updates) -> None:
    enabled = ask_yes("Enable D-Star",
                      is_on(current_value(values, updates, "ENABLE_DSTAR", "0")))
    updates["ENABLE_DSTAR"] = "1" if enabled else "0"
    if not enabled:
        return
    updates["DSTAR_REFLECTOR"] = ask(
        "D-Star reflector (e.g. REF001 C)",
        current_value(values, updates, "DSTAR_REFLECTOR", "")).upper()
    updates["DSTAR_MODULE"] = ask(
        "D-Star module (A-D)",
        current_value(values, updates, "DSTAR_MODULE", "C")).upper()


def import_config(config: Path) -> tuple[dict[str, str], bool, bool]:
    """Import /etc/mmdvm into config.env. Returns (values, adopted, ok)."""
    state = install_state()
    if state == "foreign":
        eprint("ERROR: Pi-Star/WPSD or a foreign MMDVM install was detected.")
        eprint("This add-on refuses to adopt it; back it up and remove it first.")
        return {}, False, False
    if state == "none":
        eprint("ERROR: no config.env and no installed MMDVM add-on found.")
        eprint("Run ./setup.sh for the first installation.")
        return {}, False, False

    try:
        imported = import_existing_install()
    except PermissionError as exc:
        imported = ImportResult(warnings=[str(exc)])
    permission_warn = any("cannot read" in w or "Permission" in w
                          for w in imported.warnings)
    if permission_warn or not imported.values:
        if not sys.stdin.isatty():
            eprint("ERROR: reading /etc/mmdvm requires sudo in this session.")
            return {}, False, False
        eprint("Reading the existing /etc/mmdvm requires a sudo session.")
        if subprocess.run(["sudo", "-v"]).returncode != 0:
            eprint("ERROR: sudo authentication failed.")
            return {}, False, False
        imported = import_existing_install(use_sudo=True)

    if not imported.values:
        eprint("ERROR: could not import any settings from /etc/mmdvm.")
        return {}, False, False
    for warning in imported.warnings:
        eprint(f"warning: {warning}")

    values = parse_env(EXAMPLE_CONFIG)
    values.update(imported.values)
    text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    config.write_text(text, encoding="utf-8")
    os.chmod(config, 0o600)
    update_env_values(config, imported.values)
    chmod_for_config(config, imported.values)
    print(f"Imported existing settings into {config}")
    print("Review the values and apply when ready.")
    return parse_env(config), state == "legacy", True


def chmod_for_config(config: Path, values: dict[str, str]) -> None:
    dmr = is_on(values.get("ENABLE_DMR", "0"))
    has_secret = bool(values.get("DMR_PASSWORD", "").strip())
    mode = 0o600 if dmr or has_secret else 0o644
    try:
        os.chmod(config, mode)
    except OSError:
        pass


def backup_config_sidecar(config: Path) -> Path | None:
    if not config.is_file():
        return None
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = config.with_name(f"{config.name}.bak-{stamp}")
    shutil.copy2(config, backup)
    os.chmod(backup, 0o600)
    return backup


def make_candidate(config: Path, values: dict[str, str],
                   updates: dict[str, str]) -> tuple[Path | None, dict[str, str] | None]:
    fd, name = tempfile.mkstemp(prefix=".mmdvm-onboard-", suffix=".env",
                                dir=str(config.parent))
    os.close(fd)
    candidate = Path(name)
    try:
        shutil.copyfile(config, candidate)
        os.chmod(candidate, 0o600)
        added = merge_missing_defaults(candidate)
        update_env_values(candidate, updates, add_missing=True)
        new_values = parse_env(candidate)
        if new_values.get("CALLSIGN", "").strip().upper() == "N0CALL":
            eprint("ERROR: CALLSIGN is N0CALL; set a real callsign before transmitting.")
            candidate.unlink(missing_ok=True)
            return None, None
        ok, output = validate_config(candidate)
        if not ok:
            eprint("Configuration is not valid:")
            eprint(output or "(no details)")
            candidate.unlink(missing_ok=True)
            return None, None
        if added:
            print("New options added with defaults: " + ", ".join(sorted(added)))
        return candidate, new_values
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def apply_candidate(config: Path, candidate: Path, adopted: bool,
                    no_apply: bool) -> int:
    if no_apply:
        backup = backup_config_sidecar(config)
        write_text_atomic(config, candidate.read_text(encoding="utf-8"))
        chmod_for_config(config, parse_env(config))
        print(f"Saved {config}; previous file: {backup}")
        print("Not applied; run ./mmdvm onboard again without --no-apply or rerun setup.")
        return 0

    cmd = [str(REPO_DIR / "scripts" / "safe-install.sh"),
           "--candidate", str(candidate), "--reason", "onboard", "--yes"]
    if adopted:
        cmd.append("--adopt")
    print("Applying configuration (snapshot + install + service check)...")
    result = subprocess.run(cmd)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quickly change an installed MMDVM add-on configuration")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="path to config.env (default: repo/config.env)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show changes but do not write or install")
    parser.add_argument("--no-apply", action="store_true",
                        help="save config.env only; do not install/restart")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for the final apply confirmation")
    parser.add_argument("--from-etc", action="store_true",
                        help="import settings from /etc/mmdvm even if config.env exists")
    args = parser.parse_args()

    if os.geteuid() == 0 and os.environ.get("MMDVM_TEST_MODE") != "1":
        eprint("ERROR: run onboard as your normal user, not root.")
        eprint("It calls sudo only for snapshots/install/restore.")
        return 1

    config = args.config.resolve()
    adopted = False
    from_etc_backup: Path | None = None
    if args.from_etc and config.exists():
        if not ask_yes(f"Overwrite {config} with values imported from /etc/mmdvm?",
                       False):
            return 1
        from_etc_backup = backup_config_sidecar(config)
        print(f"Previous config saved as {from_etc_backup}")
        config.unlink()

    if not config.exists():
        values, adopted, ok = import_config(config)
        if not ok:
            if from_etc_backup is not None:
                shutil.copy2(from_etc_backup, config)
                print(f"Restored previous config from {from_etc_backup}",
                      file=sys.stderr)
            return 1
    else:
        try:
            values = parse_env(config)
        except PermissionError as exc:
            eprint(f"ERROR: {exc}")
            return 1
        if not values:
            eprint(f"ERROR: {config} is empty or unreadable.")
            return 1
        state = install_state()
        if state == "foreign":
            eprint("ERROR: Pi-Star/WPSD or a foreign MMDVM install was detected.")
            return 1
        adopted = state == "legacy"
        if adopted:
            print("Legacy install detected; applying will adopt it after a backup.")

    updates: dict[str, str] = {}
    print(f"== MMDVM onboard ({config}) ==")
    print(f"repository version: {read_version()}")
    show_brief(values, updates)

    menu = """
  1) Station (callsign, frequency, suffix, labels)
  2) YSF reflector
  3) Modem
  4) OLED
  5) DMR
  6) D-Star
  7) Show all settings
  8) Review and apply
  q) Quit
"""
    while True:
        print(menu)
        choice = input("Select: ").strip().lower()
        if choice in ("q", "quit", "exit"):
            print("No changes applied.")
            return 0
        if choice == "1":
            edit_station(values, updates)
        elif choice == "2":
            edit_reflector(values, updates)
        elif choice == "3":
            edit_modem(values, updates)
        elif choice == "4":
            edit_oled(values, updates)
        elif choice == "5":
            edit_dmr(values, updates)
        elif choice == "6":
            edit_dstar(values, updates)
        elif choice == "7":
            show_all(values, updates)
            continue
        elif choice == "8":
            break
        else:
            eprint("Unknown selection.")
            continue
        changed = sum(1 for key, value in updates.items()
                      if values.get(key, "") != value)
        print(f"{changed} changed value(s); nothing has been installed yet.")
        show_brief(values, updates)

    candidate, new_values = make_candidate(config, values, updates)
    if candidate is None:
        return 1
    try:
        old = dict(values)
        print("\nPending changes:")
        for key, before, after in candidate_diff(old, new_values or {}):
            print(f"  {key}: {before or '<unset>'} -> {after or '<empty>'}")
        print()
        if args.dry_run:
            print("dry-run: candidate validated; no files changed.")
            return 0
        if not args.yes and not ask_yes("Apply these changes now?", True):
            print("Not applied.")
            return 0
        return apply_candidate(config, candidate, adopted, args.no_apply)
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        eprint("\nInterrupted; no changes applied.")
        raise SystemExit(130)
