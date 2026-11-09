"""Archive file names and the retention rule.

Names look like ``magpie-APOLLO-20261108-020000.7z``. The machine name is part of the
pattern, so Apollo and Rocket can share a destination folder without pruning each
other's backups.
"""

from __future__ import annotations

import re
from datetime import datetime

PREFIX = "magpie"
EXT = ".7z"
TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def archive_name(machine: str, when: datetime) -> str:
    return f"{PREFIX}-{machine}-{when.strftime(TIMESTAMP_FORMAT)}{EXT}"


def _pattern(machine: str) -> re.Pattern[str]:
    return re.compile(rf"^{PREFIX}-{re.escape(machine)}-(\d{{8}}-\d{{6}}){re.escape(EXT)}$", re.IGNORECASE)


def backups_for(machine: str, names: list[str]) -> list[str]:
    """This machine's archive names, oldest first. Anything else is ignored."""
    pat = _pattern(machine)
    matched = [(m.group(1), n) for n in names if (m := pat.match(n))]
    return [n for _, n in sorted(matched)]


def to_delete(machine: str, names: list[str], keep: int) -> list[str]:
    """The archives to remove so only the newest ``keep`` remain, oldest first."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    ours = backups_for(machine, names)
    return ours[:-keep] if len(ours) > keep else []
