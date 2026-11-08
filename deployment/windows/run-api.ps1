[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ProjectRoot)
$ErrorActionPreference = "Stop"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Set-Location $ProjectRoot
& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
