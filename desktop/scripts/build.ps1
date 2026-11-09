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
foreach ($taskUiTest in @('qml_ui_smoke.py', 'ocs_ui_smoke.py', 'accounts_ui_smoke.py', 'audit_ui_smoke.py', 'activity_ui_smoke.py')) {
    & $taskPython (Join-Path 'tests' $taskUiTest)
    if ($LASTEXITCODE -ne 0) { throw "Source UI acceptance failed: $taskUiTest" }
}
& $taskPython tests/services_media_smoke.py build/services-media-smoke
if ($LASTEXITCODE -ne 0) { throw 'Service scrolling and media UI acceptance failed' }
& $taskPython tests/matching_ui_smoke.py build/matching-ui-smoke
if ($LASTEXITCODE -ne 0) { throw 'Answer matching UI acceptance failed' }
& $taskPython tests/workbench_ui_smoke.py build/workbench-ui-smoke
if ($LASTEXITCODE -ne 0) { throw 'Workbench source UI acceptance failed' }
& $taskPython tests/dialog_ui_smoke.py build/dialog-ui-smoke
if ($LASTEXITCODE -ne 0) { throw 'Dialog actions and feedback UI acceptance failed' }
& $taskPython tests/verification_performance_smoke.py --max-heartbeat-p95-ms 100 --max-heartbeat-ms 250 --max-stop-gui-ms 200 --output build/verification-performance.json
if ($LASTEXITCODE -ne 0) { throw 'Verification delivery or responsiveness acceptance failed' }
$taskOutput = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $OutputDirectory))
$taskDistRoot = [System.IO.Path]::GetFullPath((Join-Path $taskRoot 'dist')) + [System.IO.Path]::DirectorySeparatorChar
if (-not $taskOutput.StartsWith($taskDistRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must stay inside desktop/dist'
}
if ([System.IO.Path]::GetFileName($taskOutput) -ne 'ChaoxingDesktop') {
    throw 'OutputDirectory must end with ChaoxingDesktop'
}
$taskDist = [System.IO.Directory]::GetParent($taskOutput).FullName
& $taskPython scripts/make_app_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Application icon generation failed' }
& $taskPython -m PyInstaller --clean --noconfirm --distpath $taskDist ChaoxingDesktop.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Copy-Item -LiteralPath README.md,LICENSE,THIRD_PARTY_NOTICES.md,requirements-lock.txt -Destination $taskOutput
Set-Location -LiteralPath ([System.IO.Directory]::GetParent($taskRoot).FullName)
$taskSmoke = Join-Path $taskRoot 'build/release-smoke'
& $taskPython (Join-Path $taskRoot 'tests/release_smoke.py') $taskOutput $taskSmoke --worker-only
if ($LASTEXITCODE -ne 0) { throw 'Packaged worker smoke test failed' }
# The GUI is launched natively from PowerShell, independently of the Python
# test runner's process and standard-handle inheritance.
if ($SkipGuiSmoke) {
    Write-Output 'GUI smoke NOT RUN: this CI build still requires interactive Windows acceptance before release.'
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $taskRoot 'scripts/run_gui_smoke.ps1') -Executable (Join-Path $taskOutput 'ChaoxingDesktop.exe') -Destination $taskSmoke
    if ($LASTEXITCODE -ne 0) { throw 'Packaged GUI smoke test failed' }
}
if ($SkipGuiSmoke) {
    & $taskPython (Join-Path $taskRoot 'scripts/provenance.py') record $taskOutput
} else {
    & $taskPython (Join-Path $taskRoot 'scripts/provenance.py') record $taskOutput --gui-verified
}
if ($LASTEXITCODE -ne 0) { throw 'Build record failed' }
Write-Output "Built $taskOutput/ChaoxingDesktop.exe. Keep the entire folder together."
