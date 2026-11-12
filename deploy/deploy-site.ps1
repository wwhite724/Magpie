# Publishes the Magpie site (site/) to the magpie VM.
# Run from the repo folder in PowerShell:
#   .\deploy\deploy-site.ps1              publish the current working tree
#   .\deploy\deploy-site.ps1 -Full        first time: also set up the VM (updates, NGINX, firewall, SSH hardening)
#
# The VM is "magpie" (192.168.10.16): https://magpie.whitesberg.net, LAN and WireGuard only.
param(
  [string]$User = 'willw',
  [string]$SiteHost = '192.168.10.16',
  [switch]$Full
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$stage = Join-Path $env:TEMP 'magpie-site-deploy'
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $stage | Out-Null

tar -czf "$stage\site.tgz" -C (Join-Path $root 'site') .
if ($LASTEXITCODE -ne 0) { throw 'Packing site/ failed' }
Copy-Item (Join-Path $PSScriptRoot 'magpie-site.conf'), (Join-Path $PSScriptRoot 'magpie-site-http.conf') $stage
# the setup script must have Unix line endings even if Git converted it on Windows
$sh = (Get-Content (Join-Path $PSScriptRoot 'setup-vm.sh') -Raw) -replace "`r`n", "`n"
[IO.File]::WriteAllText("$stage\setup-vm.sh", $sh, (New-Object Text.UTF8Encoding $false))
$files = @('site.tgz', 'magpie-site.conf', 'magpie-site-http.conf', 'setup-vm.sh')

$target = "$User@$SiteHost"
ssh $target 'rm -rf ~/magpie-site-deploy && mkdir -p ~/magpie-site-deploy'
if ($LASTEXITCODE -ne 0) { throw "Could not reach the magpie VM ($SiteHost) over SSH" }
scp ($files | ForEach-Object { Join-Path $stage $_ }) "${target}:magpie-site-deploy/"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed' }

$opts = if ($Full) { '' } else { '--deploy-only' }
# LogLevel=ERROR hides ssh's "Connection closed" note, which PowerShell 5.1 treats as an error when output is redirected
ssh -t -o LogLevel=ERROR $target "sudo bash ~/magpie-site-deploy/setup-vm.sh $opts"
if ($LASTEXITCODE -ne 0) { throw 'Install failed (see the messages above)' }
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Write-Host 'Published to magpie.whitesberg.net (the lines above show whether it is on HTTPS)' -ForegroundColor Green
