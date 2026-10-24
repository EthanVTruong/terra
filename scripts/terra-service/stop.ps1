[CmdletBinding()]
param([string]$StateDir)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if (-not $StateDir) { $StateDir = Join-Path $repoRoot '.terra-local/runtime' }
$runtimeDir = [IO.Path]::GetFullPath($StateDir)
$recordFile = Join-Path $runtimeDir 'service.json'
if (-not (Test-Path -LiteralPath $recordFile)) { throw 'No Terra service record exists in that state directory.' }
$record = Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$record.process_id)"
if (-not $process) { Write-Output 'The recorded Terra service is already stopped.'; exit 0 }
# The venv launcher can have a Python child. Refuse PID reuse or unrelated apps.
if ($process.CommandLine -notmatch ' -m terra_monitoring\.runtime ' -or
    -not $process.CommandLine.Contains([string]$record.state_dir) -or
    [string]$record.state_dir -ne $runtimeDir -or
    ([DateTime]$process.CreationDate).ToUniversalTime() -gt ([DateTime]$record.started_at_utc).ToUniversalTime().AddSeconds(5)) {
    throw 'The recorded PID does not match this Terra runtime. No process was stopped.'
}
$children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$([int]$record.process_id)" |
    Where-Object { $_.CommandLine -match ' -m terra_monitoring\.runtime ' -and $_.CommandLine.Contains($runtimeDir) })
foreach ($child in $children) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
Write-Output 'Terra stopped. Its persistent state was retained.'
