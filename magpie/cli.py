"""Command line: ``python -m magpie [run|list] [--dry-run] [--config PATH]``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, naming
from .archive import ArchiveError, create_archive, scan
from .config import Config, ConfigError, load_config
from .destinations import DestinationError, make_destination

log = logging.getLogger("magpie")

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def setup_logging(log_dir: Path | None, verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "magpie.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n} B"


def prune(target, machine: str, keep: int, *, dry_run: bool = False, pending: str | None = None) -> list[str]:
    """Deletes this machine's oldest archives at a destination so ``keep`` remain.

    ``pending`` is an archive not uploaded yet (dry run) that should count as kept.
    """
    names = target.list() + ([pending] if pending else [])
    doomed = naming.to_delete(machine, names, keep)
    for name in doomed:
        if dry_run:
            log.info("[%s] would delete %s", target.name, name)
        else:
            target.delete(name)
            log.info("[%s] deleted old backup %s", target.name, name)
    return doomed


def prune_staging(cfg: Config) -> None:
    if not cfg.staging_dir.is_dir():
        return
    names = [p.name for p in cfg.staging_dir.iterdir() if p.is_file()]
    for name in naming.to_delete(cfg.machine_name, names, cfg.keep):
        (cfg.staging_dir / name).unlink(missing_ok=True)


def run_backup(cfg: Config, *, dry_run: bool = False, now: datetime | None = None) -> int:
    """Archive, upload to each destination, then prune. Returns a process exit code."""
    now = now or datetime.now()
    name = naming.archive_name(cfg.machine_name, now)
    targets = [make_destination(d, cfg) for d in cfg.destinations]

    if dry_run:
        count, total = scan(cfg)
        log.info("Would archive %d files (%s before compression) into %s", count, human(total), name)
        for t in targets:
            try:
                prune(t, cfg.machine_name, t.keep or cfg.keep, dry_run=True, pending=name)
            except DestinationError as e:
                log.error("[%s] %s", t.name, e)
        return 0

    log.info("Magpie %s starting backup %s", __version__, name)
    try:
        result = create_archive(cfg, cfg.staging_dir / name)
    except ArchiveError as e:
        log.error("Archive failed: %s", e)
        return 2
    for w in result.warnings:
        log.warning(w)
    log.info("Archive ready: %s (%s)", result.path, human(result.size))

    failed = []
    for t in targets:
        keep = t.keep or cfg.keep
        if t.prune_before_upload:
            # Opt-in for destinations too small to hold keep + 1 archives: make room
            # first, counting the archive about to be uploaded as one of the kept.
            try:
                prune(t, cfg.machine_name, keep, pending=name)
            except DestinationError as e:
                log.error("[%s] cleanup before upload failed, skipping this destination: %s", t.name, e)
                failed.append(t.name)
                continue
        try:
            log.info("[%s] uploading...", t.name)
            t.upload(result.path)
            log.info("[%s] upload complete", t.name)
        except DestinationError as e:
            # Never prune a destination whose newest backup didn't arrive.
            if t.prune_before_upload:
                log.error("[%s] upload failed AFTER old backups were removed to make room: %s", t.name, e)
            else:
                log.error("[%s] upload failed, keeping old backups there: %s", t.name, e)
            failed.append(t.name)
            continue
        try:
            prune(t, cfg.machine_name, keep)
        except DestinationError as e:
            log.error("[%s] cleanup failed: %s", t.name, e)
            failed.append(t.name)

    if failed:
        log.error("Finished with errors (%s). The archive is kept in %s", ", ".join(failed), cfg.staging_dir)
        prune_staging(cfg)
        return 1

    result.path.unlink(missing_ok=True)
    log.info("Backup %s finished successfully", name)
    return 3 if result.warnings else 0


def list_backups(cfg: Config) -> int:
    code = 0
    for d in cfg.destinations:
        t = make_destination(d, cfg)
        try:
            names = naming.backups_for(cfg.machine_name, t.list())
        except DestinationError as e:
            log.error("[%s] %s", t.name, e)
            code = 1
            continue
        print(f"{t.name} ({d.path}):")
        for n in reversed(names):
            print(f"  {n}")
        if not names:
            print("  (none)")
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="magpie", description="Nightly encrypted backups to TrueNAS and Google Drive.")
    p.add_argument("command", nargs="?", default="run", choices=["run", "list"])
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.json")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen without archiving or deleting")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"magpie {__version__}")
    args = p.parse_args(argv)

    try:
        cfg = load_config(args.config, require_password=args.command == "run" and not args.dry_run)
    except ConfigError as e:
        setup_logging(None, args.verbose)
        log.error("%s", e)
        return 2

    setup_logging(cfg.log_dir if args.command == "run" else None, args.verbose)
    if args.command == "list":
        return list_backups(cfg)
    return run_backup(cfg, dry_run=args.dry_run)
