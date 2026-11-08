[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\Program Files\KnowledgeBase"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$Python = Get-Command python -ErrorAction Stop
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$AppLog = Join-Path $ProjectRoot "logs\api.log"
$TaskName = "KnowledgeBase API"
$UserUrl = "http://127.0.0.1:8000/user"
$AdminUrl = "http://127.0.0.1:8000/admin"

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs"), (Join-Path $ProjectRoot "data\uploads") | Out-Null
if (-not (Test-Path $VenvPython)) {
    & $Python.Source -m venv (Join-Path $ProjectRoot ".venv")
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e "$ProjectRoot[dev]"
& $VenvPython -m alembic upgrade head

$RunScript = Join-Path $ProjectRoot "deployment\windows\run-api.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -ProjectRoot `"$ProjectRoot`" >> `"$AppLog`" 2>&1"
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$ShortcutRoot = Join-Path $env:PUBLIC "Desktop"
@("User Search|$UserUrl", "Admin Workspace|$AdminUrl") | ForEach-Object {
    $parts = $_ -split "\|"
    $shortcut = Join-Path $ShortcutRoot ("Knowledge Base - " + $parts[0] + ".url")
    Set-Content -Path $shortcut -Value "[InternetShortcut]`nURL=$($parts[1])" -Encoding ASCII
}
Write-Host "Knowledge Base API scheduled service installed."
Write-Host "User:  $UserUrl"
Write-Host "Admin: $AdminUrl"
