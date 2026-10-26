#!/usr/bin/env python3
"""Lossless update path for an installed MMDVM add-on.

The source tree is updated first when it is a clean git checkout.  The user's
config is then imported/merged (never replaced with example values), a snapshot
is taken, and safe-install.sh performs the build/install with automatic
rollback.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from addon_config import (
    DEFAULT_CONFIG,
    EXAMPLE_CONFIG,
    ImportResult,
    candidate_diff,
    import_existing_install,
    install_state,
    merge_missing_defaults,
    parse_env,
    read_version,
    update_env_values,
    validate_config,
)

REPO_DIR = Path(__file__).resolve().parent.parent


def eprint(*args) -> None:
    print(*args, file=sys.stderr)


def ensure_sudo() -> bool:
    return subprocess.run(["sudo", "-v"]).returncode == 0


def import_settings() -> ImportResult | None:
    try:
        imported = import_existing_install()
    except PermissionError as exc:
        imported = ImportResult(warnings=[str(exc)])
    permission_warn = any("cannot read" in w or "Permission" in w
                          for w in imported.warnings)
    if permission_warn or not imported.values:
        if not sys.stdin.isatty():
            eprint("ERROR: reading /etc/mmdvm requires sudo in this session.")
            return None
        eprint("Reading the existing /etc/mmdvm requires sudo.")
        if not ensure_sudo():
            eprint("ERROR: sudo authentication failed.")
            return None
        imported = import_existing_install(use_sudo=True)
    for warning in imported.warnings:
        eprint(f"warning: {warning}")
    return imported


def make_candidate(config: Path, imported: ImportResult | None,
                   source_example: bool) -> tuple[Path | None, list[str]]:
    """Create a validated temporary config candidate."""
    fd, name = tempfile.mkstemp(prefix=".mmdvm-update-", suffix=".env",
                                dir=str(config.parent))
    os.close(fd)
    candidate = Path(name)
    try:
        if source_example:
            shutil.copyfile(EXAMPLE_CONFIG, candidate)
        else:
            shutil.copyfile(config, candidate)
        os.chmod(candidate, 0o600)
        added = merge_missing_defaults(candidate)
        if imported is not None:
            update_env_values(candidate, imported.values, add_missing=True)
            merge_missing_defaults(candidate)
        if parse_env(candidate).get("CALLSIGN", "").strip().upper() == "N0CALL":
            eprint("ERROR: imported CALLSIGN is N0CALL; edit config.env first.")
            candidate.unlink(missing_ok=True)
            return None, added
        ok, output = validate_config(candidate)
        if not ok:
            eprint("ERROR: configuration validation failed:")
            eprint(output or "(no details)")
            candidate.unlink(missing_ok=True)
            return None, added
        return candidate, added
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def git_pull() -> bool:
    git_dir = REPO_DIR / ".git"
    if not git_dir.exists():
        print("not a git checkout (Download ZIP?); using the current source tree.")
        print("Replace the source files manually if you wanted newer code.")
        return True
    status = subprocess.run(
        ["git", "-C", str(REPO_DIR), "status", "--porcelain",
         "--untracked-files=no"],
        capture_output=True, text=True)
    if status.returncode != 0:
        eprint("ERROR: git status failed; not updating.")
        return False
    if status.stdout.strip():
        eprint("ERROR: tracked files have local changes; commit/stash them first:")
        eprint(status.stdout.rstrip())
        return False
    print("== git pull --ff-only ==")
    result = subprocess.run(["git", "-C", str(REPO_DIR), "pull", "--ff-only"])
    if result.returncode != 0:
        eprint("ERROR: git pull failed; no system changes were made.")
        eprint("Retry later or use --no-pull after updating the code manually.")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update an installed MMDVM add-on without losing config")
    parser.add_argument("--no-pull", action="store_true",
                        help="do not run git pull; rebuild the current source")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the plan; do not pull, write or install")
    parser.add_argument("--yes", action="store_true",
                        help="skip the final confirmation")
    parser.add_argument("--adopt", action="store_true",
                        help="adopt a legacy /etc/mmdvm install")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="path to config.env (default: repo/config.env)")
    args = parser.parse_args()

    if os.geteuid() == 0 and os.environ.get("MMDVM_TEST_MODE") != "1":
        eprint("ERROR: run update as your normal user, not root.")
        eprint("It calls sudo only for snapshots/install/restore.")
        return 1

    config = args.config.resolve()
    old_version = read_version()
    state = install_state()
    if state == "foreign":
        eprint("ERROR: Pi-Star/WPSD or a foreign MMDVM install was detected.")
        eprint("Refusing to overwrite it; back it up and remove it first.")
        return 1
    if state == "none":
        eprint("ERROR: no installed MMDVM add-on found.")
        eprint("Run ./setup.sh for the first installation.")
        return 1

    adopt = args.adopt or state == "legacy"
    if state == "legacy" and not args.adopt and not args.dry_run:
        if not sys.stdin.isatty():
            eprint("ERROR: legacy install detected; rerun with --adopt after a backup.")
            return 1
        answer = input("Legacy add-on install detected; adopt it and keep its settings? [y/N]: ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted; nothing changed.")
            return 0
        adopt = True

    if args.dry_run:
        print(f"== update dry-run ==")
        print(f"  repository version: {old_version}")
        print(f"  install state:      {state}")
        print(f"  git pull:           {'no (--no-pull)' if args.no_pull else 'yes'}")
        print(f"  config:             {config}")
        if config.is_file():
            added = merge_missing_defaults(config, add_missing=False)
            if added:
                print("  new config keys:    " + ", ".join(added))
            else:
                print("  new config keys:    none")
        else:
            print("  config:             would import from /etc/mmdvm")
        print("  then: build (memory-aware), render, snapshot, install, verify")
        print("dry-run: no files changed, no sudo used.")
        return 0

    if not args.no_pull and not git_pull():
        return 1
    new_version = read_version()
    print(f"version: {old_version} -> {new_version}")

    imported = None
    source_example = not config.is_file()
    if source_example:
        print("== import existing /etc/mmdvm settings ==")
        imported = import_settings()
        if imported is None:
            return 1
        if not imported.values:
            eprint("ERROR: no settings could be imported; nothing changed.")
            return 1
        print("Imported: " + ", ".join(sorted(imported.values)))
    else:
        print("Using existing config.env (values are preserved).")

    candidate, added = make_candidate(config, imported, source_example)
    if candidate is None:
        return 1
    try:
        if added:
            print("New options added with defaults: " + ", ".join(sorted(added)))
        old_values = parse_env(config) if config.is_file() else {}
        new_values = parse_env(candidate)
        print("\nConfiguration changes:")
        diff = candidate_diff(old_values, new_values)
        if not diff:
            print("  (no config value changes)")
        for key, before, after in diff:
            print(f"  {key}: {before or '<unset>'} -> {after or '<empty>'}")

        if not args.yes:
            answer = input("\nUpdate, rebuild and restart the add-on services? [y/N]: ")
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted; nothing changed.")
                return 0

        cmd = [str(REPO_DIR / "scripts" / "safe-install.sh"),
               "--candidate", str(candidate), "--reason", "update",
               "--build", "always", "--yes"]
        if adopt:
            cmd.append("--adopt")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            return result.returncode
        print(f"Updated to {new_version}.")
        return 0
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        eprint("\nInterrupted; no changes applied.")
        raise SystemExit(130)
