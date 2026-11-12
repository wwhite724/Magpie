# Magpie

Nightly encrypted (AES-256 7z) backups of Windows folders to a TrueNAS SMB share and to Google Drive (via rclone),
keeping the newest N (default 2) per destination. Python 3.10+, run by Windows Task Scheduler (`install-task.ps1`).

## Layout
- `magpie/config.py`: loads `config.json` and `.env` (`MAGPIE_PASSWORD`). Both are git-ignored; keep `config.example.json` / `.env.example` current.
- `magpie/naming.py`: archive names and the retention rule. This is the only code that decides what gets deleted.
- `magpie/archive.py`: 7-Zip executable if found, py7zr fallback. Excludes are matched per path component in both.
- `magpie/destinations.py`: `folder` (local/SMB) and `rclone` destinations: upload, list and delete.
- `magpie/cli.py`: the run sequence (archive → upload → prune per destination → remove staging copy).

## Google Drive access and the site
- rclone uses Magpie's own OAuth client (Google Cloud project **Magpie**, published *In production*, scope `drive.file`).
  rclone's shared client ID is retired during 2026, so never go back to a blank `client_id`.
- `site/` holds the Whitesberg landing page (`/`) and Magpie's home page and privacy policy (`/magpie/`), static, no
  scripts. NGINX on the `magpie` VM (192.168.10.16) serves them twice: publicly as `https://whitesberg.net` through a
  Cloudflare Tunnel to a localhost-only site on port 8081 (`deploy/magpie-public.conf`, `deploy/setup-tunnel.sh`), and
  on the LAN as `https://magpie.whitesberg.net` (Let's Encrypt through certbot and Cloudflare DNS, token `acme-magpie`).
- The tunnel must only ever point at `http://localhost:8081`. Keep `site/` free of anything private: it is public.
- Deploy: `.\deploy\deploy-site.ps1` (add `-Full` to set up or re-harden the VM). SSH `willw@192.168.10.16`, key only.
  Releases are in `/var/www/magpie-releases` (last 3 kept); `/var/www/magpie` links to the live one.
- **cpd** = commit, push, run `.\deploy\deploy-site.ps1`. The backup tool itself has no staging; it runs from this folder.

## Rules
- Retention only ever touches files matching `magpie-<THIS MACHINE>-<timestamp>.7z`. Never widen that pattern.
- Never prune a destination whose upload failed in the same run. The only exception is a destination with
  `prune_before_upload: true` in `config.json`, an explicit opt-in for destinations short on space. Never make it a default.
- Never log the password (7-Zip errors are scrubbed before logging).

## Test
- `python -m pytest` (on Rocket, from WSL: `~/.venvs/magpie/bin/python -m pytest`). The 7-Zip test is skipped where 7z isn't installed.
