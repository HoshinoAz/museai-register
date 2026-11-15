param([int]$Port = 8787, [ValidateRange(1, 5)][int]$Workers = 1)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host 'Run setup.ps1 first.'
    exit 1
}
& $pythonPath run.py --port $Port --workers $Workers
exit $LASTEXITCODE
