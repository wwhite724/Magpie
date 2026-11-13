# Publishes the Magpie site (site/) to the magpie VM.
# Run from the repo folder in PowerShell:
#   .\deploy\deploy-site.ps1              publish the current working tree
#   .\deploy\deploy-site.ps1 -Full        first time: also set up the VM (updates, NGINX, firewall, SSH hardening)
#
# The VM is "magpie" (192.168.10.53). The same pages are served two ways:
#   https://magpie.whitesberg.net   LAN and WireGuard, straight to the VM
#   https://whitesberg.net          public, through the Cloudflare tunnel (deploy\setup-tunnel.sh, see README)
param(
  [string]$User = 'willw',
  [string]$SiteHost = '192.168.10.53',
  [switch]$Full
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$stage = Join-Path $env:TEMP 'magpie-site-deploy'
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $stage | Out-Null

tar -czf "$stage\site.tgz" -C (Join-Path $root 'site') .
if ($LASTEXITCODE -ne 0) { throw 'Packing site/ failed' }
foreach ($n in 'magpie-site.conf', 'magpie-site-http.conf', 'magpie-public.conf') { Copy-Item (Join-Path $PSScriptRoot $n) $stage }
# the setup script must have Unix line endings even if Git converted it on Windows
foreach ($n in 'setup-vm.sh', 'setup-tunnel.sh') {
  $sh = (Get-Content (Join-Path $PSScriptRoot $n) -Raw) -replace "`r`n", "`n"
  [IO.File]::WriteAllText("$stage\$n", $sh, (New-Object Text.UTF8Encoding $false))
}
$files = @('site.tgz', 'magpie-site.conf', 'magpie-site-http.conf', 'magpie-public.conf', 'setup-vm.sh', 'setup-tunnel.sh')

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
