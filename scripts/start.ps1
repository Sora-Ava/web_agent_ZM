param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path '.venv/Scripts/python.exe')) { throw 'Missing .venv. Run the installation steps in README.' }
if (-not (Test-Path '.env')) { throw 'Missing .env. Copy .env.example and configure it first.' }
if (-not (Test-Path 'frontend/dist/index.html')) { throw 'Missing frontend build. Run npm.cmd --prefix frontend run build.' }
& '.venv/Scripts/python.exe' -c "import fastapi, playwright, app.launcher"
if ($LASTEXITCODE -ne 0) { throw 'Missing Python dependencies. See README installation steps.' }
$connection = New-Object System.Net.Sockets.TcpClient
$occupied = $false
try { $connection.Connect('127.0.0.1', 8000); $occupied = $true } catch {} finally { $connection.Dispose() }
if ($occupied) {
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 3 } catch { $health = $null }
    if ($health -and $health.app -eq 'web-agent-zm') {
        Write-Host 'Web Agent is already running: http://127.0.0.1:8000/'
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8000/' }
        exit 0
    }
    throw 'Port 8000 is occupied by another or older service. Close that service before starting.'
}
Write-Host 'Web Agent: http://127.0.0.1:8000/ (Ctrl+C to stop)'
$launcherArgs = @('-m', 'app.launcher')
if (-not $NoBrowser) { $launcherArgs += '--open-browser' }
& '.venv/Scripts/python.exe' @launcherArgs
exit $LASTEXITCODE
