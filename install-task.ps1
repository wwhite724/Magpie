<#
.SYNOPSIS
  Registers (or removes) the nightly Magpie backup in Windows Task Scheduler.

.DESCRIPTION
  The task runs "python -m magpie run" from this folder whether or not you are
  logged on. It's registered with your Windows password so the task can reach
  the TrueNAS SMB share (tasks without a stored password get no network
  credentials). If you sign in with a Microsoft account, use that account's
  password, not your PIN.

.EXAMPLE
  .\install-task.ps1                 # every night at 02:00
  .\install-task.ps1 -Time 03:30
  .\install-task.ps1 -Uninstall
#>
param(
    [string]$TaskName = "Magpie Nightly Backup",
    [string]$Time = "02:00",
    [string]$PythonExe,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

if (-not $PythonExe) {
    # Skip the Microsoft Store stub in WindowsApps, which isn't a real Python.
    $PythonExe = Get-Command python.exe -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike "*\WindowsApps\*" } |
        Select-Object -First 1 -ExpandProperty Source
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "Python not found. Install it (winget install Python.Python.3.13) or pass -PythonExe <path>."
}

$root = $PSScriptRoot
if (-not (Test-Path (Join-Path $root "config.json"))) { throw "config.json is missing in $root (copy config.example.json)." }
if (-not (Test-Path (Join-Path $root ".env")))        { throw ".env is missing in $root (copy .env.example)." }

& $PythonExe -c "import py7zr" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Warning "py7zr isn't installed for $PythonExe. Run: & '$PythonExe' -m pip install -r requirements.txt" }

$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = Get-Credential -UserName $user -Message "Windows password for $user (lets the task reach the TrueNAS share)"

$action   = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m magpie run" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null

Write-Host "Registered '$TaskName' to run daily at $Time using $PythonExe."
Write-Host "Test it now with: Start-ScheduledTask -TaskName '$TaskName'   (log: $root\logs\magpie.log)"
