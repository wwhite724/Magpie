# Magpie

Nightly encrypted backups of Windows folders, copied to a TrueNAS SMB share and to Google Drive.
Each destination keeps this machine's **two newest** archives, and Magpie deletes older ones after each new upload.

## How it works

1. Packs the configured folders into one AES-256 encrypted 7z archive (file names are encrypted too):
   `magpie-<MACHINE>-<yyyyMMdd-HHmmss>.7z`, built in `staging/`.
2. Copies it to each destination: an SMB/local folder (copied as `.partial`, size-checked, then renamed) or an rclone remote
   (Google Drive; rclone verifies the upload).
3. Once a destination has the new archive, deletes that machine's archives there beyond `keep` (default 2).
   A destination whose upload failed is never pruned. The one exception is a destination with
   `prune_before_upload` set (see the config reference), which makes room first.
4. Deletes the staging copy once every destination has it. If any upload failed, the copy stays in `staging/`.

The machine name is part of the file name, so Apollo and Rocket can share the same destination folders without deleting
each other's backups.

## Setup (on each PC)

1. Install the prerequisites. Magpie needs no Python packages, but it builds every archive with 7-Zip:
   ```powershell
   winget install Python.Python.3.13
   winget install 7zip.7zip
   winget install Rclone.Rclone
   ```
2. Set up Google Drive in rclone: run `rclone config`, create a new remote named `gdrive` of type `drive` with scope `drive`,
   and sign in through the browser.
   Check it with `rclone lsd gdrive:`.
3. TrueNAS: create a dataset and an SMB share (for example `backups`) and a user with write access. Save the credentials
   in Windows so the task can use them:
   ```powershell
   cmdkey /add:truenas /user:truenas\backupuser /pass
   ```
4. Copy `config.example.json` to `config.json` and edit `sources` and the destination paths.
   Copy `.env.example` to `.env` and set `MAGPIE_PASSWORD`. **Keep that password in your password manager too.
   Without it, the backups can't be opened.**
5. Try it:
   ```powershell
   python -m magpie run --dry-run   # shows file count/size and what would be deleted
   python -m magpie run             # makes a real backup
   python -m magpie list            # shows the backups at each destination
   ```
6. Schedule it (it asks for your Windows password so the task can reach the share while you're logged off):
   ```powershell
   powershell -ExecutionPolicy Bypass -File install-task.ps1            # 02:00 nightly
   powershell -ExecutionPolicy Bypass -File install-task.ps1 -Time 03:30
   ```

## Restoring

Open the `.7z` in 7-Zip and enter the password, or run:
```powershell
& "C:\Program Files\7-Zip\7z.exe" x magpie-APOLLO-20261108-020000.7z -oC:\Restore
```
For Google Drive copies, download the file first: `rclone copy gdrive:Backups/Magpie/<file> .`

## Config reference (`config.json`)

| Key | Meaning |
| --- | --- |
| `sources` | Folders to back up. `%VARS%` are expanded. Each folder is stored under its own name, so the folder names must be unique. |
| `excludes` | Names or wildcards matched against every path part (`node_modules`, `*.tmp`). |
| `keep` | How many archives to keep per destination (default 2). |
| `machine_name` | Defaults to the computer name. |
| `seven_zip_path` | Default `C:\Program Files\7-Zip\7z.exe`. If it isn't there, `7z` on the PATH is used. |
| `destinations` | `{"type": "folder", "path": "\\\\truenas\\share\\dir"}` or `{"type": "rclone", "path": "remote:dir", "rclone_args": [...]}`. |
| `destinations[].keep` | Optional. Overrides `keep` for that destination. |
| `destinations[].prune_before_upload` | Optional, default `false`. Deletes old archives *before* uploading the new one, for a destination too small to hold `keep` + 1 archives. With `keep: 1` the destination holds no backup while the upload runs, and none at all if that upload fails. |
| `staging_dir` | Where the archive is built before it's copied out. Needs free space for one archive. Never backed up, even when it's inside a source. |

`--drive-use-trash=false` in the example config permanently deletes old Google Drive archives, so they don't fill up your
Drive's trash and storage quota.

## Exit codes

`0` success · `1` an upload or cleanup failed · `2` config or archive error · `3` success, but some files were skipped
(for example locked files; see the log).

Logs: `logs/magpie.log` (rotated, 5 × 1 MB).

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## The Magpie site (Google consent screen links)

Google needs a public home page and privacy policy URL for the **Magpie** Google Cloud project that rclone signs in
through. They're static pages in `site/`, served by NGINX on the `magpie` VM (192.168.10.16) in two ways:

| Address | Reach | How |
| --- | --- | --- |
| `https://whitesberg.net/` | Public | Cloudflare Tunnel to a localhost-only NGINX site on port 8081 |
| `https://magpie.whitesberg.net/` | LAN and WireGuard | Straight to the VM, Let's Encrypt certificate |

Pages: `/` (Whitesberg landing page), `/magpie/` (Magpie home page) and `/magpie/privacy.html` (privacy policy).
For Google's Branding page use `https://whitesberg.net/magpie/` and `https://whitesberg.net/magpie/privacy.html`.

Publish changes with `.\deploy\deploy-site.ps1` (add `-Full` to set up or re-harden the VM).

### Public tunnel

These pages are the only thing on the home network that the internet can reach. No ports are open on OPNsense.

1. Cloudflare **Networking → Tunnels → Create a tunnel**, named `whitesberg` (Networking is its own item in the
   dashboard sidebar, not under Zero Trust). Ignore the install instructions and the operating system choice. Use the
   copy button on the command box: the setup script below takes the whole copied command or only the token in it.
2. On the tunnel's **Public Hostname** tab add `whitesberg.net` (no subdomain, no path) with service
   **HTTP** `localhost:8081`. Never point it anywhere else.
3. On the VM: `sudo bash ~/magpie-site-deploy/setup-tunnel.sh` and paste the token. It installs `cloudflared`, keeps the
   token in `/etc/cloudflared/token` (root only), and stops the tunnel if a hostname points anywhere but port 8081.
4. Check from outside the house (phone on mobile data): `https://whitesberg.net`.

Status: `sudo bash ~/magpie-site-deploy/setup-tunnel.sh status`. To take the pages offline: `sudo systemctl disable --now cloudflared`.

### Site certificate

Let's Encrypt through Cloudflare DNS, the same way as the other internal hosts (HomeNetwork doc 15, Phase 3).

1. In Cloudflare, create an API token named `acme-magpie`: **Specified Domains → whitesberg.net** only,
   permissions **Zone: Read** and **DNS: Edit**.
2. Put it on the VM in `/etc/letsencrypt/cloudflare.ini` (root, mode 600):
   `dns_cloudflare_api_token = <token>`
3. Issue the certificate on the VM:
   ```bash
   sudo certbot certonly --dns-cloudflare --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \
     --dns-cloudflare-propagation-seconds 30 -d magpie.whitesberg.net --deploy-hook "systemctl reload nginx"
   ```
4. Run `.\deploy\deploy-site.ps1 -Full`. It sees the certificate, installs the HTTPS config and opens port 443.

`certbot.timer` renews the certificate and the deploy hook reloads NGINX. Test with `sudo certbot renew --dry-run`.
