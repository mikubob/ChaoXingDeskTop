param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Stage,
    [Parameter(Mandatory=$true)][string]$Destination,
    [string]$PreviousInstaller = '',
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $taskRoot 'build')) + [System.IO.Path]::DirectorySeparatorChar
$taskDestination = [System.IO.Path]::GetFullPath($Destination)
$taskStage = [System.IO.Path]::GetFullPath($Stage)
$taskInstaller = [System.IO.Path]::GetFullPath($Installer)
if (-not $taskDestination.StartsWith($taskBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Installer smoke destination must be inside desktop/build'
}
if (Test-Path -LiteralPath $taskDestination) { throw 'Use a new destination to preserve earlier acceptance output' }
New-Item -ItemType Directory -Path $taskDestination | Out-Null
$taskInstalled = Join-Path $taskDestination 'installed'
$taskPreviousProfile = ''
if ($PreviousInstaller) {
    $taskPreviousInstaller = [System.IO.Path]::GetFullPath($PreviousInstaller)
    if (-not (Test-Path -LiteralPath $taskPreviousInstaller -PathType Leaf)) { throw 'Previous installer missing' }
    $taskPrevious = Start-Process -FilePath $taskPreviousInstaller -ArgumentList @('/S', '/NoRegistration', '/NoShortcuts', ('/D=' + $taskInstalled)) -PassThru -Wait -WindowStyle Hidden
    if ($taskPrevious.ExitCode -ne 0) { throw 'Previous installer failed' }
    $taskPreviousProfile = (Get-Content -LiteralPath (Join-Path $taskInstalled 'installation.json') -Raw | ConvertFrom-Json).profile
    Set-Content -LiteralPath (Join-Path $taskInstalled 'keep-upgrade-file.txt') -Value 'KEEP_UPGRADE_DATA' -Encoding UTF8
}
# Seed only an isolated, synthetic profile before installing/upgrading. Resolve it
# with the same frozen installation marker rules, without touching LOCALAPPDATA.
$taskProfile = if ($taskPreviousProfile) { $taskPreviousProfile } else { (Get-Content -LiteralPath (Join-Path $taskStage 'installation.json') -Raw | ConvertFrom-Json).profile }
$taskLocalData = Join-Path $taskDestination 'synthetic-local-appdata'
$taskPersonal = Join-Path $taskLocalData ('ChaoxingDesktop/profiles/' + $taskProfile)
$taskPersonalFiles = @('settings.ini', 'accounts.json', 'account-cookies/synthetic.dat', 'discussions/synthetic.json', 'activities/synthetic.json', 'run_summaries/synthetic.json', 'draft_copies/synthetic.json', 'ai_profiles.json')
$taskPersonalHashes = @{}
foreach ($taskRelative in $taskPersonalFiles) {
    $taskFile = Join-Path $taskPersonal $taskRelative
    New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($taskFile)) | Out-Null
    Set-Content -LiteralPath $taskFile -Value ('SYNTHETIC_PRESERVATION_SENTINEL:' + $taskRelative) -Encoding UTF8
    $taskPersonalHashes[$taskRelative] = (Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash
}
function Assert-SyntheticProfilePreserved {
    foreach ($taskRelative in $taskPersonalFiles) {
        $taskFile = Join-Path $taskPersonal $taskRelative
        if (-not (Test-Path -LiteralPath $taskFile) -or (Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash -ne $taskPersonalHashes[$taskRelative]) {
            throw "Installation changed synthetic personal data: $taskRelative"
        }
    }
}
$taskStart = Start-Process -FilePath $taskInstaller -ArgumentList @('/S', '/NoRegistration', '/NoShortcuts', ('/D=' + $taskInstalled)) -PassThru -Wait -WindowStyle Hidden
if ($taskStart.ExitCode -ne 0) { throw "Silent installer failed: $($taskStart.ExitCode)" }
Assert-SyntheticProfilePreserved
$taskVerified = 0
foreach ($taskFile in Get-ChildItem -LiteralPath $taskStage -File -Recurse) {
    $taskRelative = $taskFile.FullName.Substring($taskStage.Length).TrimStart('\')
    $taskTarget = Join-Path $taskInstalled $taskRelative
    if (-not (Test-Path -LiteralPath $taskTarget)) { throw "Missing installed file: $taskRelative" }
    if ($taskPreviousProfile -and $taskRelative -eq 'installation.json') { continue }
    if ((Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $taskTarget -Algorithm SHA256).Hash) {
        throw "Installed content differs: $taskRelative"
    }
    $taskVerified++
}
if (Get-ChildItem -LiteralPath $taskInstalled -Recurse -File | Where-Object { $_.Name -in @('settings.ini', 'cookies.txt', 'cookies.dat', 'accounts.json', 'config.ini') }) {
    throw 'Installer contains local settings'
}
$taskMarker = Get-Content -LiteralPath (Join-Path $taskInstalled 'installation.json') -Raw | ConvertFrom-Json
$taskVersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $taskInstalled 'ChaoxingDesktop.exe'))
$taskVersionPrefix = 'installer-' + ('{0}_{1}_{2}' -f $taskVersionInfo.FileMajorPart, $taskVersionInfo.FileMinorPart, $taskVersionInfo.FileBuildPart) + '-'
if ($taskPreviousProfile) {
    if ($taskMarker.profile -ne $taskPreviousProfile) { throw 'Upgrade replaced existing data profile' }
    if ((Get-Content -LiteralPath (Join-Path $taskInstalled 'keep-upgrade-file.txt') -Raw).Trim() -ne 'KEEP_UPGRADE_DATA') { throw 'Upgrade replaced extra user file' }
} elseif ($taskMarker.profile -notmatch ('^' + [regex]::Escape($taskVersionPrefix) + '[a-f0-9]{16}$')) { throw 'Unexpected installation profile' }
$taskPython = if ($Python) { [IO.Path]::GetFullPath($Python) } else { Join-Path $taskRoot '.venv-build/Scripts/python.exe' }
$taskResolver = @'
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
os.environ.pop('CHAOXING_DESKTOP_DATA', None)
os.environ['LOCALAPPDATA'] = sys.argv[2]
sys.frozen = True
sys.executable = sys.argv[3]
from chaoxing_desktop.config import data_dir
assert data_dir().resolve() == pathlib.Path(sys.argv[4]).resolve(), 'Installed profile resolver changed'
'@
& $taskPython -B -c $taskResolver $taskRoot $taskLocalData (Join-Path $taskInstalled 'ChaoxingDesktop.exe') $taskPersonal
if ($LASTEXITCODE -ne 0) { throw 'Installed profile resolver failed' }
$taskRetired = Join-Path $taskRoot 'scripts/retired-runtime-0.3.5.txt'
foreach ($taskRelative in Get-Content -LiteralPath $taskRetired) {
    if (-not $taskRelative -or $taskRelative.StartsWith('#')) { continue }
    if (-not (Test-Path -LiteralPath (Join-Path $taskStage $taskRelative)) -and (Test-Path -LiteralPath (Join-Path $taskInstalled $taskRelative))) {
        throw "Upgrade retained obsolete runtime: $taskRelative"
    }
}
& $taskPython (Join-Path $PSScriptRoot 'release_smoke.py') $taskInstalled (Join-Path $taskDestination 'worker-smoke') --worker-only
if ($LASTEXITCODE -ne 0) { throw 'Installed worker smoke failed' }
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $taskRoot 'scripts/run_gui_smoke.ps1') -Executable (Join-Path $taskInstalled 'ChaoxingDesktop.exe') -Destination (Join-Path $taskDestination 'gui-smoke') -Platform windows
if ($LASTEXITCODE -ne 0) { throw 'Installed GUI smoke failed' }
Assert-SyntheticProfilePreserved
Set-Content -LiteralPath (Join-Path $taskInstalled 'keep-user-file.txt') -Value 'KEEP_ME' -Encoding UTF8
$taskUninstall = Start-Process -FilePath (Join-Path $taskInstalled 'Uninstall.exe') -ArgumentList '/S' -PassThru -Wait -WindowStyle Hidden
if ($taskUninstall.ExitCode -ne 0) { throw 'Uninstaller failed' }
$taskDeadline = [DateTime]::UtcNow.AddSeconds(15)
while ((Test-Path -LiteralPath (Join-Path $taskInstalled 'ChaoxingDesktop.exe')) -and [DateTime]::UtcNow -lt $taskDeadline) { Start-Sleep -Milliseconds 100 }
foreach ($taskName in @('ChaoxingDesktop.exe', 'ChaoxingWorker.exe')) {
    if (Test-Path -LiteralPath (Join-Path $taskInstalled $taskName)) { throw "Uninstall retained application: $taskName" }
}
if (-not (Test-Path -LiteralPath (Join-Path $taskInstalled 'keep-user-file.txt'))) { throw 'Uninstall removed extra user file' }
if ($taskPreviousProfile -and -not (Test-Path -LiteralPath (Join-Path $taskInstalled 'keep-upgrade-file.txt'))) { throw 'Uninstall removed upgrade user file' }
Assert-SyntheticProfilePreserved
$taskReport = [ordered]@{ success=$true; verifiedFiles=$taskVerified; profile=$taskMarker.profile; upgradeTested=[bool]$taskPreviousProfile; upgradeProfilePreserved=($taskPreviousProfile -and $taskMarker.profile -eq $taskPreviousProfile); workerProbe=$true; guiSmoke=$true; uninstalled=$true; extraFilePreserved=$true; syntheticDataPreserved=$true; syntheticFilesVerified=$taskPersonalFiles.Count; installedProfileResolved=$true; registryAndShortcutsSkipped=$true }
$taskReport | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskDestination 'result.json') -Encoding UTF8
$taskReport | ConvertTo-Json
