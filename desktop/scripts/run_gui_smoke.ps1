param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Destination,
    [ValidateSet("offscreen", "windows")][string]$Platform = "offscreen",
    # Native acceptance requires the report of this process to name its frontend.
    # Pass an empty string only for the archived QML launcher, whose report has
    # no frontend marker.
    [string]$Frontend = "WinUI 3"
)

$ErrorActionPreference = 'Stop'
$taskExecutable = [System.IO.Path]::GetFullPath($Executable)
$taskDestination = [System.IO.Path]::GetFullPath($Destination)
if (-not (Test-Path -LiteralPath $taskExecutable -PathType Leaf)) {
    throw 'GUI executable not found'
}
$taskReportPath = Join-Path $taskDestination 'result.json'
# A report left by an earlier build must never stand in for this run.
if (Test-Path -LiteralPath $taskReportPath) {
    Remove-Item -LiteralPath $taskReportPath -Force
}
# Launch the windowed program through the native Windows process API. The
# structured report carries diagnostics; no redirected console handles needed.
$env:QT_QPA_PLATFORM = $Platform
$env:QSG_RHI_BACKEND = 'software'
$taskArguments = @('--smoke-test', ('"' + $taskDestination + '"'))
$taskProcess = Start-Process -FilePath $taskExecutable -ArgumentList $taskArguments -WindowStyle Hidden -PassThru
if (-not $taskProcess.WaitForExit(55000)) {
    # This is exactly the process created above, never another open application.
    Stop-Process -Id $taskProcess.Id -ErrorAction SilentlyContinue
    throw 'Packaged GUI smoke timed out'
}
if ($taskProcess.ExitCode -ne 0) { throw "GUI exited with code $($taskProcess.ExitCode)" }
if (-not (Test-Path -LiteralPath $taskReportPath -PathType Leaf)) {
    throw "GUI smoke produced no report: $taskReportPath"
}
$taskReport = Get-Content -LiteralPath $taskReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $taskReport.success) { throw $taskReport.message }
if ($Frontend) {
    $taskFrontend = $taskReport.frontend
    if ($taskFrontend -ne $Frontend) {
        throw "GUI smoke report frontend is '$taskFrontend', expected '$Frontend'"
    }
}
Write-Output $taskReport.message
