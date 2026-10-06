param([string]$Python = 'py', [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$environmentRoot = Join-Path $projectRoot '.windows-runtime'
$environmentPython = Join-Path $environmentRoot 'Scripts\python.exe'
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $environmentPython)) {
        if ($Python -eq 'py') { & py -3.12 -m venv $environmentRoot }
        else { & $Python -m venv $environmentRoot }
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 from python.org or pass -Python with its full path.' }
    }
    if (-not $SkipInstall) {
        & $environmentPython -m pip install -r (Join-Path $PSScriptRoot 'requirements-build.txt') --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    & $environmentPython (Join-Path $PSScriptRoot 'build.py')
    if ($LASTEXITCODE -ne 0) { throw 'Windows build failed; inspect the reported error.' }
} finally { Pop-Location }
