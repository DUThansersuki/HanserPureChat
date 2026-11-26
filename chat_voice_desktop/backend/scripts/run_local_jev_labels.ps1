param(
    [string]$Output,
    [int]$Limit = 0,
    [int]$Threads = 8
)
$ErrorActionPreference = 'Stop'
$taskProject = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../../')).Path
if (-not $Output) { $Output = Join-Path $taskProject 'audit_artifacts/local_jev_a_label_20261012_001' }
$taskRuntime = Join-Path $taskProject '.runtime/jev-v4'
$taskExe = Join-Path $taskRuntime 'runtime/jev/jev.exe'
$taskPython = Join-Path $PSScriptRoot '../.venv/Scripts/python.exe'
$taskScript = Join-Path $PSScriptRoot 'local_jev_a_labels.py'
$taskProcess = Start-Process -FilePath $taskExe -ArgumentList @(
    'serve', '--host', '127.0.0.1', '--port', '8017', '--threads', $Threads,
    '--warmup', '0', '--state-cache', '0'
) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRuntime 'server.stdout.log') `
    -RedirectStandardError (Join-Path $taskRuntime 'server.stderr.log') -PassThru
try {
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 60; $taskAttempt++) {
        if ($taskProcess.HasExited) { throw 'Local jevos exited during startup; see server.stderr.log' }
        try {
            $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8017/health' -TimeoutSec 1
            if ($taskHealth.model -eq 'jevos-v4' -and $taskHealth.status -eq 'ready') {
                $taskReady = $true
                break
            }
        } catch [System.Net.Http.HttpRequestException] {
            # A connection refusal is expected while the owned server compiles its model.
        } catch [System.Threading.Tasks.TaskCanceledException] {
            # The readiness request can time out during that same startup interval.
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $taskReady) { throw 'Local jevos did not become ready' }
    $taskArguments = @('-X', 'utf8', $taskScript, 'classify', '--output', $Output, '--workers', '1')
    if ($Limit -gt 0) { $taskArguments += @('--limit', $Limit) }
    & $taskPython @taskArguments
    if ($LASTEXITCODE -ne 0) { throw "Local labeling exited with $LASTEXITCODE" }
} finally {
    if (-not $taskProcess.HasExited) { Stop-Process -Id $taskProcess.Id }
}
