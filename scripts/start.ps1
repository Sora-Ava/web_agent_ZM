$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path '.venv/Scripts/python.exe')) { throw 'Run setup steps in README first.' }
if (-not (Test-Path 'frontend/dist/index.html')) { throw 'Run npm.cmd --prefix frontend run build first.' }
Write-Host 'Web Agent: http://127.0.0.1:8000  (Ctrl+C to stop)'
# No --reload: Windows Playwright needs the Proactor event loop for subprocesses.
& '.venv/Scripts/python.exe' -m app.launcher
