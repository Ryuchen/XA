#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('Up','Status','Down','RestartBackend','Reload','Verify','Logs','Open')][string]$Action = 'Up',
    [string]$Distribution,
    [switch]$Hmr,
    [switch]$Open
)
$ErrorActionPreference = 'Stop'
$localRoot = Join-Path $PSScriptRoot 'esports-local'
. (Join-Path $localRoot 'common.ps1')
Initialize-DevContext $Distribution

function Confirm-DevHealth {
    Assert-DevOwnership
    if (-not (Test-DevHosts)) { throw 'Local hosts resolution is incomplete. Run deploy/dev.ps1 Up to configure it through normal UAC.' }
    $containers = @(Get-DevContainers)
    foreach ($service in $script:DevSettings.services) {
        $matches = @($containers | Where-Object { $_.Config.Labels.'com.docker.compose.service' -eq $service })
        if ($matches.Count -ne 1 -or -not $matches[0].State.Running -or $matches[0].State.Health.Status -ne 'healthy') { throw "Development service is not healthy: $service" }
    }
    $backendStatus = & (Join-Path $localRoot 'backend.ps1') -Action Status -Port $script:DevSettings.backendPort
    if ($backendStatus.Status -eq 'Stopped') { throw 'Development backend is stopped. Run deploy/dev.ps1 Up.' }
    & (Join-Path $localRoot 'backend.ps1') -Action Check -Port $script:DevSettings.backendPort | Out-Host
    $results = foreach ($url in $script:DevSettings.entries) {
        $reply = Get-DevHttp $url
        if ($reply.Status -ne 200 -or $reply.Body -notmatch '/@vite/client') { throw "Development entry did not serve Vite: $url (HTTP $($reply.Status))" }
        [pscustomobject]@{ Url=$url; Status=$reply.Status; Vite=$true }
    }
    foreach ($pair in @(@('sl.esports.club', 'shenglang'), @('xa.esports.club', 'xinan'))) {
        $reply = Get-DevHttp "http://$($pair[0])/api/stores/current/"
        if ($reply.Status -ne 200 -or ($reply.Body | ConvertFrom-Json).data.slug -ne $pair[1]) { throw "Store binding check failed: $($pair[0])" }
    }
    if ((Get-DevHttp 'http://admin.esports.club/api/stores/current/').Status -ne 401) { throw 'Anonymous admin entry must not select a business database.' }
    if ((Get-DevHttp 'http://esports.club/').Status -ne 404) { throw 'Apex domain must not silently select a store.' }
    $results | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:RuntimeRoot 'deployment-health.json') -Encoding utf8NoBOM
    $results | Format-Table -AutoSize | Out-Host
}

if ($Hmr -and $Action -notin @('Up','Verify')) { throw '-Hmr is supported only with Up or Verify.' }
$deploymentLock = $null
try {
if ($Action -in @('Up','Down','RestartBackend','Reload','Verify')) {
    try { $deploymentLock = [IO.File]::Open((Join-Path $script:RuntimeRoot 'deployment.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another deployment action is already running for this checkout.' }
}
switch ($Action) {
    'Up' {
        Assert-DevWsl
        Assert-DevOwnership
        Initialize-DevEnv
        Invoke-DevCompose @('config', '--quiet') | Out-Host
        Start-DevDependencies
        & (Join-Path $localRoot 'backend.ps1') -Action Start -Port $script:DevSettings.backendPort | Out-Host
        if (-not (Test-DevHosts)) { & (Join-Path $localRoot 'request-hosts.ps1') -Wait }
        if (-not (Test-DevHosts)) { throw 'Local DNS is not ready; complete the normal Windows UAC hosts operation and retry Up.' }
        Invoke-DevCompose @('up', '--detach', '--wait', '--wait-timeout', '180') | Out-Host
        Confirm-DevHealth
    }
    'Status' {
        Assert-DevOwnership
        Get-DevDependencies | Select-Object Name,Running,HostPort | Format-Table -AutoSize | Out-Host
        Invoke-DevCompose @('ps', '--all') | Out-Host
        & (Join-Path $localRoot 'backend.ps1') -Action Status -Port $script:DevSettings.backendPort | Out-Host
        Write-Host "Hosts ready: $(Test-DevHosts)"
    }
    'Down' {
        Assert-DevOwnership
        Invoke-DevCompose @('down') | Out-Host
        & (Join-Path $localRoot 'backend.ps1') -Action Stop -Port $script:DevSettings.backendPort | Out-Host
        Write-Host 'Development frontends/gateway and the managed backend are stopped. Shared DB, Redis, volumes and hosts are retained.'
    }
    'RestartBackend' {
        & (Join-Path $localRoot 'backend.ps1') -Action Restart -Port $script:DevSettings.backendPort | Out-Host
    }
    'Reload' {
        Assert-DevOwnership
        Invoke-DevCompose @('exec', '--no-TTY', 'gateway', 'nginx', '-t') | Out-Host
        Invoke-DevCompose @('exec', '--no-TTY', 'gateway', 'nginx', '-s', 'reload') | Out-Host
    }
    'Verify' { Confirm-DevHealth }
    'Logs' {
        Assert-DevOwnership
        Invoke-DevCompose @('logs', '--tail', '60', 'boss', 'provider', 'admin', 'gateway') | Out-Host
        Write-Host "Backend logs: $script:RuntimeRoot"
    }
    'Open' { $Open = $true }
}
if ($Hmr) {
    $previousDistro = $env:XA_WSL_DISTRIBUTION
    try {
        $env:XA_WSL_DISTRIBUTION = $script:DevSettings.distribution
        & node (Join-Path $localRoot 'verify-hmr.cjs')
        if ($LASTEXITCODE -ne 0) { throw 'Real browser HMR verification failed.' }
    } finally { $env:XA_WSL_DISTRIBUTION = $previousDistro }
}
if ($Open) { foreach ($url in $script:DevSettings.entries) { Start-Process $url } }
} finally { if ($deploymentLock) { $deploymentLock.Dispose() } }
