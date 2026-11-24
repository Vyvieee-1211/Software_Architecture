param(
    [ValidateRange(1, 65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
# Mặc định một tiến trình API: 20 + 10 = tối đa 30 kết nối.
$env:DB_POOL_SIZE = '20'
$env:DB_MAX_OVERFLOW = '10'
$env:DB_POOL_TIMEOUT_SECONDS = '2'
Push-Location $projectRoot
try {
    & $pythonPath -m uvicorn app.main:app --workers 1 --port $Port
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
