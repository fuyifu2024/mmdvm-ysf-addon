#!/usr/bin/env python3
"""Snapshot and restore the installed add-on state.

Only add-on-owned files are touched.  Logs, D-Star runtime data and the Pi
boot configuration are deliberately left alone.

    sudo python3 scripts/state_backup.py create [--keep 5]
    sudo python3 scripts/state_backup.py restore /var/backups/mmdvm/<stamp>
    sudo python3 scripts/state_backup.py prune [--keep 5]

MMDVM_TEST_MODE=1 plus MMDVM_* path overrides makes the file operations
testable without root or systemd.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from addon_config import (
    BIN_DIR,
    BACKUP_ROOT,
    ETC_DIR,
    EXPECTED_UNITS,
    REPO_DIR,
    UNIT_DIR,
)

TEST_MODE = os.environ.get("MMDVM_TEST_MODE") == "1"
CONFIG = Path(os.environ.get("MMDVM_CONFIG", REPO_DIR / "config.env"))
LOGROTATE = Path(os.environ.get("MMDVM_LOGROTATE", "/etc/logrotate.d/mmdvm"))
DROPIN = Path(os.environ.get("MMDVM_DROPIN_DIR",
                             UNIT_DIR / "mmdvmhost.service.d"))
MANAGED_BINS = ("MMDVM-Host", "YSFGateway", "DMRGateway", "dstargateway",
                "oled_display.py")
MANAGED_UNITS = tuple(f"{unit}.service" for unit in EXPECTED_UNITS)


def require_root() -> None:
    if not TEST_MODE and os.geteuid() != 0:
        raise SystemExit("ERROR: run this command with sudo (or set MMDVM_TEST_MODE=1)")


def _run(cmd, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _systemctl(*args: str) -> None:
    if not TEST_MODE:
        _run(["systemctl", *args])


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-a", str(src), str(dst)], check=True)


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-a", f"{src}/.", str(dst)], check=True)


def _service_states() -> list[dict[str, str]]:
    states = []
    for unit in EXPECTED_UNITS:
        enabled = ""
        active = ""
        if not TEST_MODE:
            result = _run(["systemctl", "is-enabled", f"{unit}.service"])
            enabled = result.stdout.strip() if result.returncode == 0 else "disabled"
            result = _run(["systemctl", "is-active", f"{unit}.service"])
            active = result.stdout.strip() if result.returncode == 0 else "inactive"
        states.append({"unit": unit, "enabled": enabled, "active": active})
    return states


def create_backup(root: Path = BACKUP_ROOT, keep: int = 5) -> Path:
    require_root()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / stamp
    suffix = 1
    while target.exists():
        target = root / f"{stamp}-{suffix}"
        suffix += 1
    target.mkdir(parents=True, mode=0o700)
    os.chmod(target, 0o700)

    manifest = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "etc_present": ETC_DIR.exists(),
        "config_present": CONFIG.is_file(),
        "dropin_present": DROPIN.exists(),
        "logrotate_present": LOGROTATE.is_file(),
    }
    if ETC_DIR.exists():
        _copy_tree(ETC_DIR, target / "etc")
    if CONFIG.is_file():
        _copy(CONFIG, target / "config" / "config.env")
    for name in MANAGED_BINS:
        source = BIN_DIR / name
        if source.is_file():
            _copy(source, target / "bin" / name)
    for name in MANAGED_UNITS:
        source = UNIT_DIR / name
        if source.is_file():
            _copy(source, target / "units" / name)
    if DROPIN.exists():
        _copy_tree(DROPIN, target / "units" / DROPIN.name)
    if LOGROTATE.is_file():
        _copy(LOGROTATE, target / "logrotate" / "mmdvm")
    (target / "services.json").write_text(
        json.dumps(_service_states(), indent=2) + "\n", encoding="utf-8")
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    prune_backups(root, keep, keep_current=target)
    return target


def prune_backups(root: Path = BACKUP_ROOT, keep: int = 5,
                  keep_current: Path | None = None) -> None:
    if not root.is_dir():
        return
    candidates = sorted((p for p in root.iterdir() if p.is_dir()),
                        key=lambda p: p.name)
    protected = {keep_current.resolve()} if keep_current else set()
    for old in candidates[:-keep] if keep > 0 else candidates:
        if old.resolve() in protected:
            continue
        shutil.rmtree(old, ignore_errors=True)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def restore_backup(target: Path) -> None:
    require_root()
    if not target.is_dir() or not (target / "manifest.json").is_file():
        raise SystemExit(f"ERROR: not a valid add-on backup: {target}")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    states = []
    services_file = target / "services.json"
    if services_file.is_file():
        states = json.loads(services_file.read_text(encoding="utf-8"))

    for unit in MANAGED_UNITS:
        _systemctl("disable", "--now", unit)
        _systemctl("stop", unit)

    _remove_path(ETC_DIR)
    for name in MANAGED_BINS:
        _remove_path(BIN_DIR / name)
    for name in MANAGED_UNITS:
        _remove_path(UNIT_DIR / name)
    _remove_path(DROPIN)
    _remove_path(LOGROTATE)
    _remove_path(CONFIG)

    if manifest.get("etc_present") and (target / "etc").is_dir():
        _copy_tree(target / "etc", ETC_DIR)
    if manifest.get("config_present") and (target / "config" / "config.env").is_file():
        _copy(target / "config" / "config.env", CONFIG)
    for name in MANAGED_BINS:
        source = target / "bin" / name
        if source.is_file():
            _copy(source, BIN_DIR / name)
    for name in MANAGED_UNITS:
        source = target / "units" / name
        if source.is_file():
            _copy(source, UNIT_DIR / name)
    if manifest.get("dropin_present") and (target / "units" / DROPIN.name).is_dir():
        _copy_tree(target / "units" / DROPIN.name, DROPIN)
    if manifest.get("logrotate_present") and (target / "logrotate" / "mmdvm").is_file():
        _copy(target / "logrotate" / "mmdvm", LOGROTATE)

    _systemctl("daemon-reload")
    for entry in states:
        unit = f"{entry.get('unit')}.service"
        enabled = entry.get("enabled", "")
        active = entry.get("active", "")
        if enabled == "enabled":
            _systemctl("enable", unit)
        elif enabled == "disabled":
            _systemctl("disable", unit)
        if active == "active":
            _systemctl("start", unit)
        elif active in ("inactive", "failed"):
            _systemctl("stop", unit)


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot/restore the MMDVM add-on")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--keep", type=int, default=5)
    restore = sub.add_parser("restore")
    restore.add_argument("backup", type=Path)
    prune = sub.add_parser("prune")
    prune.add_argument("--keep", type=int, default=5)
    args = parser.parse_args()

    if args.command == "create":
        target = create_backup(keep=args.keep)
        print(target)
    elif args.command == "restore":
        restore_backup(args.backup)
        print(f"restored {args.backup}")
    else:
        prune_backups(keep=args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
