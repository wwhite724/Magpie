"""Loads config.json and .env, and validates them."""

from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

PASSWORD_ENV = "MAGPIE_PASSWORD"


class ConfigError(Exception):
    pass


@dataclass
class Destination:
    name: str
    type: str  # "folder" (local path or SMB share) or "rclone"
    path: str  # folder path, or rclone remote such as "gdrive:Backups/Magpie"
    rclone_args: list[str] = field(default_factory=list)
    keep: int | None = None  # overrides the top-level "keep" for this destination
    # Delete old archives BEFORE uploading the new one. Needs less space, but the
    # destination holds fewer (with keep=1: zero) backups while the upload runs.
    prune_before_upload: bool = False


@dataclass
class Config:
    sources: list[Path]
    destinations: list[Destination]
    staging_dir: Path
    log_dir: Path
    excludes: list[str] = field(default_factory=list)
    keep: int = 2
    machine_name: str = ""
    archiver: str = "auto"  # "auto", "7z" or "py7zr"
    seven_zip_path: str = r"C:\Program Files\7-Zip\7z.exe"
    rclone_path: str = "rclone"
    compression_level: int = 5
    password: str = ""


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines. Values already in the environment win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _safe_machine_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    if not cleaned:
        raise ConfigError("machine_name is empty after removing unsupported characters")
    return cleaned.upper()


def load_config(config_path: Path, *, require_password: bool = True) -> Config:
    base = config_path.resolve().parent
    load_dotenv(base / ".env")

    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path} (copy config.example.json to config.json)")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{config_path} is not valid JSON: {e}") from e

    def resolve(p: str) -> Path:
        path = Path(os.path.expandvars(os.path.expanduser(p)))
        return path if path.is_absolute() else base / path

    sources = [resolve(s) for s in data.get("sources", [])]
    if not sources:
        raise ConfigError("'sources' must list at least one folder")
    names = [s.name.lower() for s in sources]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(
            f"Source folders must have distinct folder names (they become top-level folders in the archive): {dupes}"
        )

    destinations = []
    for i, d in enumerate(data.get("destinations", [])):
        dtype = d.get("type")
        if dtype not in ("folder", "rclone"):
            raise ConfigError(f"destinations[{i}].type must be 'folder' or 'rclone'")
        path = d.get("path") or d.get("remote")
        if not path:
            raise ConfigError(f"destinations[{i}] needs a 'path'")
        dest_keep = d.get("keep")
        if dest_keep is not None and int(dest_keep) < 1:
            raise ConfigError(f"destinations[{i}].keep must be at least 1")
        destinations.append(
            Destination(
                name=d.get("name") or f"{dtype}{i}",
                type=dtype,
                path=path,
                rclone_args=list(d.get("rclone_args", [])),
                keep=int(dest_keep) if dest_keep is not None else None,
                prune_before_upload=bool(d.get("prune_before_upload", False)),
            )
        )
    if not destinations:
        raise ConfigError("'destinations' must list at least one destination")

    keep = int(data.get("keep", 2))
    if keep < 1:
        raise ConfigError("'keep' must be at least 1")

    archiver = data.get("archiver", "auto")
    if archiver not in ("auto", "7z", "py7zr"):
        raise ConfigError("'archiver' must be 'auto', '7z' or 'py7zr'")

    password = os.environ.get(PASSWORD_ENV, "")
    if require_password and not password:
        raise ConfigError(f"{PASSWORD_ENV} is not set (put it in .env next to config.json)")

    return Config(
        sources=sources,
        destinations=destinations,
        staging_dir=resolve(data.get("staging_dir", "staging")),
        log_dir=resolve(data.get("log_dir", "logs")),
        excludes=list(data.get("excludes", [])),
        keep=keep,
        machine_name=_safe_machine_name(data.get("machine_name") or platform.node()),
        archiver=archiver,
        seven_zip_path=data.get("seven_zip_path", Config.seven_zip_path),
        rclone_path=data.get("rclone_path", Config.rclone_path),
        compression_level=int(data.get("compression_level", 5)),
        password=password,
    )
