param(
    [ValidateSet('all', 'boss', 'provider', 'admin')][string]$App = 'all',
    [string]$Backend = 'http://127.0.0.1:18080',
    [switch]$Acceptance
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLogRoot = Join-Path $taskRoot '.tools/fronted-preview'
New-Item -ItemType Directory -Path $taskLogRoot -Force | Out-Null
$taskNode = (Get-Command node -ErrorAction Stop).Source
$taskApps = if ($App -eq 'all') { @('boss', 'provider', 'admin') } else { @($App) }
$taskPorts = @{ boss = 13101; provider = 13102; admin = 13103 }
$taskOldBackend = $env:XA_API_PROXY
$taskOldAcceptance = $env:VITE_ACCEPTANCE
try {
    $env:XA_API_PROXY = $Backend
    $env:VITE_ACCEPTANCE = if ($Acceptance) { 'true' } else { 'false' }
    foreach ($taskName in $taskApps) {
        $taskPort = $taskPorts[$taskName]
        if (Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue) {
            Write-Output "$taskName already has a listener at http://127.0.0.1:$taskPort (left running)"
            continue
        }
        $taskDirectory = Join-Path $PSScriptRoot $taskName
        $taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $taskProcess = Start-Process -FilePath $taskNode -ArgumentList @(
            (Join-Path $PSScriptRoot 'node_modules/vite/bin/vite.js'),
            '--config', (Join-Path $taskDirectory 'vite.config.ts')
        ) -WorkingDirectory $taskDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $taskLogRoot "$taskName-$taskStamp.out.log") `
            -RedirectStandardError (Join-Path $taskLogRoot "$taskName-$taskStamp.err.log")
        [PSCustomObject]@{ app=$taskName; pid=$taskProcess.Id; port=$taskPort; backend=$Backend; acceptance=[bool]$Acceptance } |
            ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $taskLogRoot "$taskName.json")
        Write-Output "$taskName starting at http://127.0.0.1:$taskPort (PID $($taskProcess.Id))"
    }
} finally {
    $env:XA_API_PROXY = $taskOldBackend
    $env:VITE_ACCEPTANCE = $taskOldAcceptance
}
