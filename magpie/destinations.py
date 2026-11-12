"""Upload, list and delete archives at each destination."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .config import Config, Destination

log = logging.getLogger(__name__)


class DestinationError(Exception):
    pass


class FolderDestination:
    """A local folder or an SMB share such as \\\\truenas\\backups\\magpie."""

    def __init__(self, dest: Destination):
        self.name = dest.name
        self.keep = dest.keep
        self.prune_before_upload = dest.prune_before_upload
        self.root = Path(dest.path)

    def upload(self, archive: Path) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / archive.name
            partial = self.root / (archive.name + ".partial")
            shutil.copyfile(archive, partial)
            if partial.stat().st_size != archive.stat().st_size:
                partial.unlink(missing_ok=True)
                raise DestinationError(f"Size mismatch after copying to {partial}")
            partial.replace(target)
        except OSError as e:
            raise DestinationError(f"Copy to {self.root} failed: {e}") from e

    def list(self) -> list[str]:
        try:
            return [p.name for p in self.root.iterdir() if p.is_file()]
        except FileNotFoundError:
            return []  # folder not created yet; upload() creates it
        except OSError as e:
            raise DestinationError(f"Can't list {self.root}: {e}") from e

    def delete(self, name: str) -> None:
        try:
            (self.root / name).unlink()
        except OSError as e:
            raise DestinationError(f"Can't delete {self.root / name}: {e}") from e


class RcloneDestination:
    """Any rclone remote, such as gdrive:Backups/Magpie."""

    def __init__(self, dest: Destination, rclone: str):
        self.name = dest.name
        self.keep = dest.keep
        self.prune_before_upload = dest.prune_before_upload
        self.remote = dest.path.rstrip("/")
        self.extra = dest.rclone_args
        self.rclone = rclone

    def _run(self, *args: str) -> str:
        cmd = [self.rclone, *args, *self.extra]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        except FileNotFoundError as e:
            raise DestinationError(f"rclone not found ({self.rclone}); install it or set rclone_path") from e
        if proc.returncode != 0:
            raise DestinationError(f"rclone {args[0]} failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout

    def _join(self, name: str) -> str:
        return f"{self.remote}/{name}" if not self.remote.endswith(":") else f"{self.remote}{name}"

    def upload(self, archive: Path) -> None:
        # copyto verifies size and hash where the remote supports it.
        self._run("copyto", str(archive), self._join(archive.name))

    def list(self) -> list[str]:
        try:
            out = self._run("lsf", "--files-only", self.remote)
        except DestinationError as e:
            if "directory not found" in str(e).lower():
                return []
            raise
        return [line.strip() for line in out.splitlines() if line.strip()]

    def delete(self, name: str) -> None:
        self._run("deletefile", self._join(name))


def make_destination(dest: Destination, cfg: Config):
    if dest.type == "folder":
        return FolderDestination(dest)
    return RcloneDestination(dest, cfg.rclone_path)
