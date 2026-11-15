#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
$script:DevRoot = $PSScriptRoot
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$script:DevSettings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'settings.json') -Raw | ConvertFrom-Json
$script:RuntimeRoot = Join-Path $PSScriptRoot '.runtime'

function Initialize-DevContext([string]$Distribution) {
    if ($Distribution) { $script:DevSettings.distribution = $Distribution }
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL is required for this development environment.' }
    $distros = ((& wsl.exe --list --quiet) -join "`n").Replace([string][char]0, '') -split "`r?`n" | ForEach-Object { $_.Trim() }
    if ($LASTEXITCODE -ne 0 -or $script:DevSettings.distribution -notin $distros) { throw "WSL distribution not found: $($script:DevSettings.distribution)" }
    $script:LinuxRoot = ((& wsl.exe -d $script:DevSettings.distribution -u root --exec wslpath -a -u $script:RepoRoot.Replace('\','/')) -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $script:LinuxRoot.StartsWith('/')) { throw 'Cannot resolve repository path in the selected WSL distribution.' }
    $script:LinuxDeploy = "$script:LinuxRoot/deploy/esports-local"
    [IO.Directory]::CreateDirectory($script:RuntimeRoot) | Out-Null
}

function Invoke-DevDocker([string[]]$DockerArgs) {
    $result = & wsl.exe -d $script:DevSettings.distribution -u root -- docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($DockerArgs[0]) (WSL $($script:DevSettings.distribution))" }
    return $result
}

function Invoke-DevCompose([string[]]$ComposeArgs) {
    $result = & wsl.exe -d $script:DevSettings.distribution -u root --cd $script:DevRoot -- docker compose --project-name $script:DevSettings.project --file compose.yml @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Development Compose command failed: $($ComposeArgs[0])" }
    return $result
}

function Assert-DevWsl {
    $mode = ((& wsl.exe -d $script:DevSettings.distribution -u root -- wslinfo --networking-mode) -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or $mode -ne 'mirrored') { throw 'This deployment requires WSL2 mirrored networking. See README; scripts do not change the machine-wide .wslconfig.' }
    $version = & wsl.exe -d $script:DevSettings.distribution -u root -- docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -ne 0) {
        & wsl.exe -d $script:DevSettings.distribution -u root -- systemctl start docker
        if ($LASTEXITCODE -ne 0) { throw 'Docker Engine could not be started in the selected WSL distribution.' }
        $version = Invoke-DevDocker @('info', '--format', '{{.ServerVersion}}')
    }
    Write-Host "WSL: $($script:DevSettings.distribution); network: $mode; Docker: $version"
}

function Get-DevContainers {
    $ids = @(Invoke-DevDocker @('ps', '--all', '--filter', "label=com.docker.compose.project=$($script:DevSettings.project)", '--format', '{{.ID}}'))
    if (-not $ids.Count) { return @() }
    return @(Invoke-DevDocker (@('inspect') + $ids) | ConvertFrom-Json)
}

function Assert-DevOwnership {
    foreach ($container in @(Get-DevContainers)) {
        $labels = $container.Config.Labels
        if ($labels.'com.docker.compose.project.working_dir' -cne $script:LinuxDeploy -or
            $labels.'com.docker.compose.project.config_files' -cne "$script:LinuxDeploy/compose.yml" -or
            $labels.'com.docker.compose.service' -notin @($script:DevSettings.services + 'dependencies')) {
            throw "Compose project name belongs to another checkout or service: $($container.Name). No container was changed."
        }
        $fronted = @($container.Mounts | Where-Object Destination -eq '/workspace/fronted')
        if ($labels.'com.docker.compose.service' -ne 'gateway' -and ($fronted.Count -ne 1 -or $fronted[0].Source -cne "$script:LinuxRoot/fronted")) {
            throw "Source mount does not belong to this checkout: $($container.Name)"
        }
        if ($labels.'com.docker.compose.service' -eq 'gateway') {
            foreach ($pair in @(@('/etc/nginx/nginx.conf', "$script:LinuxDeploy/nginx/nginx.conf"), @('/etc/nginx/conf.d', "$script:LinuxDeploy/nginx/conf.d"))) {
                $mount = @($container.Mounts | Where-Object Destination -eq $pair[0])
                if ($mount.Count -ne 1 -or $mount[0].Source -cne $pair[1] -or $mount[0].RW) { throw "Gateway config mount is not the read-only config of this checkout: $($pair[0])" }
            }
        }
    }
}

function Get-DevDependencies {
    foreach ($binding in $script:DevSettings.dependencies) {
        $container = @(Invoke-DevDocker @('inspect', $binding.name) | ConvertFrom-Json)[0]
        $ports = @($container.HostConfig.PortBindings.($binding.containerPort))
        if ($container.Config.Image -ne $binding.image -or $ports.Count -ne 1 -or
            $ports[0].HostIp -ne '127.0.0.1' -or $ports[0].HostPort -ne $binding.hostPort) {
            throw "Shared dependency identity/loopback binding mismatch: $($binding.name). No replacement or migration is permitted."
        }
        if ($binding.project -and ($container.Config.Labels.'com.docker.compose.project' -ne $binding.project -or
            $container.Config.Labels.'com.docker.compose.service' -ne $binding.service -or
            $container.Config.Labels.'com.docker.compose.project.working_dir' -cne $script:LinuxRoot)) {
            throw "Shared database belongs to another project: $($binding.name)"
        }
        [pscustomobject]@{ Name=$binding.name; Running=$container.State.Running; HostPort=$binding.hostPort; Id=$container.Id }
    }
}

function Start-DevDependencies {
    $dependencies = @(Get-DevDependencies) # Validate every binding before starting any dependency.
    foreach ($item in $dependencies) {
        if (-not $item.Running) { Invoke-DevDocker @('start', $item.Name) | Out-Host }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $ready = $true
        foreach ($item in $dependencies) {
            $container = @(Invoke-DevDocker @('inspect', $item.Name) | ConvertFrom-Json)[0]
            if (-not $container.State.Running -or ($container.State.Health -and $container.State.Health.Status -ne 'healthy')) { $ready = $false }
        }
        if ($ready) { return }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Shared development dependencies did not become healthy; see docker logs for the named dependency.'
}

function Test-DevHosts {
    foreach ($domain in $script:DevSettings.domains) {
        try { $addresses = @([Net.Dns]::GetHostAddresses($domain)) } catch { return $false }
        if (-not $addresses.Count -or @($addresses | Where-Object { -not [Net.IPAddress]::IsLoopback($_) }).Count) { return $false }
    }
    return $true
}

function Get-DevHttp([string]$Url) {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(30)
    try {
        $response = $client.GetAsync($Url).GetAwaiter().GetResult()
        [pscustomobject]@{ Status=[int]$response.StatusCode; Body=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult() }
    } finally { $client.Dispose(); $handler.Dispose() }
}

function Initialize-DevEnv {
    $envFile = Join-Path $script:DevRoot '.env'
    if (Test-Path -LiteralPath $envFile) { return }
    $cache = Join-Path $env:LOCALAPPDATA 'npm-cache'
    [IO.Directory]::CreateDirectory($cache) | Out-Null
    $linuxCache = ((& wsl.exe -d $script:DevSettings.distribution -u root --exec wslpath -a -u $cache.Replace('\','/')) -join '').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the local read-only npm cache in WSL.' }
    @('XA_NODE_IMAGE=node:22-slim', 'XA_NGINX_IMAGE=nginx:alpine', 'XA_NPM_PROXY=', "XA_NPM_CACHE_HOST=$linuxCache") | Set-Content -LiteralPath $envFile -Encoding utf8NoBOM
}
