# Magpie

Nightly encrypted (AES-256 7z) backups of Windows folders to a TrueNAS SMB share and to Google Drive (via rclone),
keeping the newest N (default 2) per destination. Python 3.10+, run by Windows Task Scheduler (`install-task.ps1`).

## Layout
- `magpie/config.py`: loads `config.json` and `.env` (`MAGPIE_PASSWORD`). Both are git-ignored; keep `config.example.json` / `.env.example` current.
- `magpie/naming.py`: archive names and the retention rule. This is the only code that decides what gets deleted.
- `magpie/archive.py`: 7-Zip executable if found, py7zr fallback. Excludes are matched per path component in both.
- `magpie/destinations.py`: `folder` (local/SMB) and `rclone` destinations: upload, list and delete.
- `magpie/cli.py`: the run sequence (archive → upload → prune per destination → remove staging copy).

## Rules
- Retention only ever touches files matching `magpie-<THIS MACHINE>-<timestamp>.7z`. Never widen that pattern.
- Never prune a destination whose upload failed in the same run.
- Never log the password (7-Zip errors are scrubbed before logging).

## Test
- `python -m pytest` (on Rocket, from WSL: `~/.venvs/magpie/bin/python -m pytest`). The 7-Zip test is skipped where 7z isn't installed.
