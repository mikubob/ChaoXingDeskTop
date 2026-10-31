param(
    [string]$OutputDirectory = 'dist/ChaoxingDesktop',
    [string]$Wheelhouse = '',
    [switch]$SkipGuiSmoke
)

$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv-build/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .venv-build
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
if ($Wheelhouse) {
    $taskWheels = [System.IO.Path]::GetFullPath($Wheelhouse)
    & $taskPython -m pip install --no-index --find-links $taskWheels --no-deps -r requirements-lock.txt
} else {
    & $taskPython -m pip install --no-deps -r requirements-lock.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $taskPython scripts/verify_dependencies.py
if ($LASTEXITCODE -ne 0) { throw 'Build environment differs from requirements-lock.txt; use a clean .venv-build' }
& $taskPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
& $taskPython -m unittest discover -s tests -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& (Join-Path $PSScriptRoot 'build-winui.ps1') -OutputDirectory $OutputDirectory -Python $taskPython -BundleBackend -SkipTests -SkipGuiSmoke:$SkipGuiSmoke
if ($LASTEXITCODE -ne 0) { throw 'Native WinUI 3 release build failed' }
$taskNativeOutput = [IO.Path]::GetFullPath((Join-Path $taskRoot $OutputDirectory))
if ($SkipGuiSmoke) {
    & $taskPython scripts/provenance.py record $taskNativeOutput
} else {
    & $taskPython scripts/provenance.py record $taskNativeOutput --gui-verified
}
if ($LASTEXITCODE -ne 0) { throw 'Native build record failed' }
