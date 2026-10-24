[CmdletBinding()]
param(
    [string]$PythonExe,
    [string]$EnvFile,
    [string]$SmsEnvFile,
    [string]$StateDir,
    [ValidateRange(1, 65535)][int]$Port = 8512,
    [ValidateRange(1, 3600)][int]$Interval = 300,
    [switch]$NoWorker
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if (-not $PythonExe) { $PythonExe = Join-Path $repoRoot '.venv/Scripts/python.exe' }
$pythonPath = (Resolve-Path -LiteralPath $PythonExe).Path
if (-not $StateDir) { $StateDir = Join-Path $repoRoot '.terra-local/runtime' }
if (-not [IO.Path]::IsPathRooted($StateDir)) { $StateDir = Join-Path (Get-Location).Path $StateDir }
$runtimeDir = [IO.Path]::GetFullPath($StateDir)
$webRoot = Join-Path $repoRoot 'web/dist'
if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'index.html'))) {
    throw 'Build the web client first: pnpm --dir web install --frozen-lockfile; pnpm --dir web build'
}
$arguments = @('-m', 'terra_monitoring.runtime', '--host', '127.0.0.1', '--port', "$Port",
    '--state-dir', $runtimeDir, '--interval', "$Interval", '--web-root', $webRoot)
foreach ($entry in @(@('--env-file', $EnvFile), @('--sms-env-file', $SmsEnvFile))) {
    if ($entry[1]) {
        $privatePath = (Resolve-Path -LiteralPath $entry[1]).Path
        $arguments += @($entry[0], $privatePath)
    }
}
if ($NoWorker) { $arguments += '--no-worker' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Stop its owner before starting another monitor."
}
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
# Start-Process joins arguments on Windows. Double trailing backslashes inside
# quotes without changing the actual argument (including a drive-root path).
$quoted = ($arguments | ForEach-Object { '"' + ($_ -replace '(\\+)$', '$1$1') + '"' }) -join ' '
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $repoRoot 'backend'
    $process = Start-Process -FilePath $pythonPath -ArgumentList $quoted -WorkingDirectory $repoRoot `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'service.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'service.stderr.log') -PassThru
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
$record = [ordered]@{
    process_id = $process.Id
    python = $pythonPath
    port = $Port
    state_dir = $runtimeDir
    repository = $repoRoot
    started_at_utc = [DateTime]::UtcNow.ToString('o')
}
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDir 'service.json') -Encoding utf8
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $process.Refresh()
    if ($process.HasExited) { throw "Terra could not start. Inspect service.stderr.log in $runtimeDir." }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1
        if ($health.status -eq 'ok') { $ready = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) { throw "Terra started but health is not ready. Inspect service.stderr.log in $runtimeDir." }
Write-Output "Terra started as process $($process.Id): http://127.0.0.1:$Port/"
Write-Output "State and logs: $runtimeDir"
Write-Output 'Starting Terra does not enable SMS or create an orchard. Keep this computer awake for monitoring.'
