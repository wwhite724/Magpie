"""Builds the encrypted 7z archive with the 7-Zip executable (a prerequisite).

Each source folder is stored under its own folder name, for example
``Documents/...`` and ``dev/...``. AES-256 encryption covers file contents and
file names (header encryption).
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)


class ArchiveError(Exception):
    pass


@dataclass
class ArchiveResult:
    path: Path
    size: int
    warnings: list[str]


def is_excluded(rel_parts: tuple[str, ...], patterns: list[str]) -> bool:
    """True if any path component matches any pattern (case-insensitive, like 7-Zip's -xr!)."""
    return any(fnmatch.fnmatch(part.lower(), pat.lower()) for part in rel_parts for pat in patterns)


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(path))


def own_dirs(cfg: Config) -> list[Path]:
    """Magpie's staging and log folders. They're never backed up, even when they sit
    inside a source folder: otherwise the archive being written would include itself."""
    return [cfg.staging_dir, cfg.log_dir]


def iter_files(source: Path, patterns: list[str], skip_dirs: list[Path] = ()):
    """Yields (absolute path, archive name) for every file under source that isn't excluded."""
    skip = {_norm(d) for d in skip_dirs}
    for root, dirs, files in os.walk(source):
        rel_root = Path(root).relative_to(source)
        root_parts = rel_root.parts
        dirs[:] = [
            d for d in dirs
            if not is_excluded(root_parts + (d,), patterns) and _norm(Path(root) / d) not in skip
        ]
        for f in files:
            if is_excluded(root_parts + (f,), patterns):
                continue
            yield Path(root) / f, (Path(source.name) / rel_root / f).as_posix()


def scan(cfg: Config) -> tuple[int, int]:
    """Counts the files and bytes that would be archived (used by --dry-run).
    Uses the same exclude rules as the 7-Zip command line."""
    count = total = 0
    for src in cfg.sources:
        for path, _ in iter_files(src, cfg.excludes, own_dirs(cfg)):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                pass
    return count, total


def find_7z(cfg: Config) -> str | None:
    if Path(cfg.seven_zip_path).is_file():
        return cfg.seven_zip_path
    return shutil.which("7z") or shutil.which("7za")


def create_archive(cfg: Config, dest: Path) -> ArchiveResult:
    missing = [str(s) for s in cfg.sources if not s.is_dir()]
    if missing:
        raise ArchiveError(f"Source folders not found: {missing}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    partial.unlink(missing_ok=True)

    seven = find_7z(cfg)
    if not seven:
        raise ArchiveError(
            f"7-Zip not found at {cfg.seven_zip_path} or on PATH. Install it: winget install 7zip.7zip"
        )
    warnings = _with_7z(seven, cfg, partial)

    partial.replace(dest)
    return ArchiveResult(dest, dest.stat().st_size, warnings)


def _with_7z(exe: str, cfg: Config, out: Path) -> list[str]:
    log.info("Archiving with 7-Zip (%s)", exe)
    args = [
        exe, "a", "-t7z", f"-mx={cfg.compression_level}", "-mhe=on",
        f"-p{cfg.password}",
        "-ssw",          # include files that are open for writing
        "-bd", "-bb0",   # no progress indicator, quiet listing
        "-y",
    ]
    args += [f"-xr!{pat}" for pat in cfg.excludes]
    # Leave out Magpie's own staging/log folders when they're inside a source.
    for own in own_dirs(cfg):
        for src in cfg.sources:
            try:
                rel = Path(os.path.abspath(own)).relative_to(os.path.abspath(src))
            except ValueError:
                continue
            args.append("-x!" + str(Path(src.name) / rel))
    args.append(str(out))
    args += [str(s) for s in cfg.sources]

    proc = subprocess.run(args, capture_output=True, text=True, errors="replace")
    output = (proc.stdout + proc.stderr).strip()
    # 7-Zip exit codes: 0 OK, 1 warning (e.g. a locked file was skipped), 2+ error.
    if proc.returncode >= 2:
        out.unlink(missing_ok=True)
        raise ArchiveError(f"7-Zip failed with exit code {proc.returncode}:\n{output.replace(cfg.password, '***')}")
    if proc.returncode == 1:
        return seven_zip_warnings(output)
    return []


def seven_zip_warnings(output: str) -> list[str]:
    """The skipped files from 7-Zip's output, as "path : reason" lines.

    7-Zip ends with a summary block:
        Scan WARNINGS for files and folders:

        C:\\Users\\me\\Documents\\My Music\\ : Access is denied.
        ----------------
        Scan WARNINGS: 1
    """
    found, inside = [], False
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("Scan WARNINGS for files and folders"):
            inside = True
        elif inside and text.startswith("----"):
            inside = False
        elif inside and text:
            found.append(f"Skipped {text}")
    return found[:50] or ["7-Zip reported warnings (see its output)"]

