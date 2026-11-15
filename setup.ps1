param([string]$NetworkProxy = '', [switch]$Dev)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($NetworkProxy) { & git -c "http.proxy=$NetworkProxy" submodule update --init --recursive }
else { & git submodule update --init --recursive }
if ($LASTEXITCODE -ne 0) { throw 'Could not fetch the protocol submodule.' }
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python virtual environment.' }
}
$requirementsPath = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.txt' }
$pipArgs = @('-m', 'pip', 'install', '-r', $requirementsPath)
if ($NetworkProxy) { $pipArgs += @('--proxy', $NetworkProxy) }
& $pythonPath @pipArgs
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Run .\start.ps1 and visit http://127.0.0.1:8787'
