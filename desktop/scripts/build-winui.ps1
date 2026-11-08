param(
    [string]$OutputDirectory = 'winui/publish',
    [string]$Python = '',
    [string]$DotNet = '',
    [switch]$BundleBackend,
    [switch]$SkipGuiSmoke,
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $taskRoot
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
if (-not $DotNet) {
    $taskLocalDotnet = Join-Path $taskRoot 'build/dotnet/dotnet.exe'
    $DotNet = if (Test-Path -LiteralPath $taskLocalDotnet) { $taskLocalDotnet } else { 'dotnet' }
}
if (-not $Python) {
    foreach ($taskVenv in @('.venv-build', '.venv', '.venv-ci')) {
        $taskCandidate = Join-Path $taskRoot "$taskVenv/Scripts/python.exe"
        if (Test-Path -LiteralPath $taskCandidate) { $Python = $taskCandidate; break }
    }
}
if (-not $Python) { throw 'Prepare a Python environment using desktop/requirements.txt first.' }
$taskOutput = [IO.Path]::GetFullPath((Join-Path $taskRoot $OutputDirectory))
$taskPrefix = $taskRoot + [IO.Path]::DirectorySeparatorChar
if (-not $taskOutput.StartsWith($taskPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'OutputDirectory must stay inside desktop.' }
if (-not $SkipTests) {
    & $Python -m unittest discover -s tests -p 'test_winui_bridge.py'
    if ($LASTEXITCODE -ne 0) { throw 'Native backend transport/worker tests failed.' }
}
& $DotNet publish winui/ChaoxingDesktop.csproj -c Release -p:Platform=x64 -o $taskOutput
if ($LASTEXITCODE -ne 0) { throw 'WinUI 3 build failed. Install .NET 8 SDK and Windows SDK.' }
if ($BundleBackend) {
    & $Python -m PyInstaller --clean --noconfirm --distpath build/winui-backend WinUIBackend.spec
    if ($LASTEXITCODE -ne 0) { throw 'Native backend packaging failed.' }
    Copy-Item -Path (Join-Path $taskRoot 'build/winui-backend/ChaoxingBackend/*') -Destination $taskOutput -Recurse -Force
    Copy-Item -LiteralPath README.md,LICENSE,THIRD_PARTY_NOTICES.md,requirements-lock.txt -Destination $taskOutput
    & $Python tests/release_smoke.py $taskOutput build/winui-release-smoke --worker-only
    if ($LASTEXITCODE -ne 0) { throw 'Frozen worker acceptance failed.' }
} else {
    $env:CHAOXING_BACKEND_ROOT = $taskRoot
    $env:CHAOXING_PYTHON = [IO.Path]::GetFullPath($Python)
}
if (-not $SkipGuiSmoke) {
    & (Join-Path $PSScriptRoot 'run_gui_smoke.ps1') -Executable (Join-Path $taskOutput 'ChaoxingDesktop.exe') -Destination (Join-Path $taskRoot 'build/winui-smoke') -Platform windows
    if ($LASTEXITCODE -ne 0) { throw 'WinUI 3 GUI acceptance failed.' }
}
Write-Output "Built native WinUI 3 application: $taskOutput/ChaoxingDesktop.exe"
