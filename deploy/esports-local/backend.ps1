#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Restart', 'Status', 'Check')]
    [string]$Action = 'Status',
    [ValidateRange(1024, 65535)]
    [int]$Port = 18106
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'The loopback backend runner requires PowerShell 7 on Windows.' }

$backendRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$backendRunner = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'scripts/serve-backend.py')).Path
$backendLegacyRunner = Join-Path $backendRepo '.tools/fronted-acceptance/serve.py'
$backendPython = Join-Path $backendRepo '.venv/Scripts/python.exe'
$backendRuntime = Join-Path $PSScriptRoot '.runtime'
$backendState = Join-Path $backendRuntime "backend-$Port.json"
$backendOut = Join-Path $backendRuntime "backend-$Port.out.log"
$backendErr = Join-Path $backendRuntime "backend-$Port.err.log"
if (-not (Test-Path -LiteralPath $backendPython -PathType Leaf)) { throw 'Repository .venv/Scripts/python.exe is missing.' }
[IO.Directory]::CreateDirectory($backendRuntime) | Out-Null

# Win32_Process exposes the actual interpreter (the Windows venv launcher can
# spawn its base interpreter). Resolve both before trusting any existing PID.
$pythonInfo = & $backendPython -c 'import json,sys; print(json.dumps([sys.executable,sys._base_executable]))'
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the repository Python environment.' }
$backendExecutables = @($pythonInfo | ConvertFrom-Json | ForEach-Object { [IO.Path]::GetFullPath($_) })
if (-not ('XaEsportsBackend.CommandLine' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace XaEsportsBackend {
    public static class CommandLine {
        [DllImport("shell32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr CommandLineToArgvW(string command, out int count);
        [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr pointer);
        public static string[] Split(string command) {
            int count; IntPtr pointer = CommandLineToArgvW(command, out count);
            if (pointer == IntPtr.Zero) throw new InvalidOperationException("Cannot parse process command line.");
            try {
                string[] args = new string[count];
                for (int i = 0; i < count; i++) args[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer, i * IntPtr.Size));
                return args;
            } finally { LocalFree(pointer); }
        }
    }
}
'@
}

function Get-BackendProcess([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
}

function Get-BackendListeners {
    # Some Windows CIM providers return a null State even for an actual
    # listener. Include only their unconnected, wildcard-remote endpoint;
    # callers still verify the process command and probe HTTP before adoption.
    # A process is never stopped merely because it owns this port.
    @(Get-NetTCPConnection -ErrorAction Stop | Where-Object {
        $_.LocalPort -eq $Port -and ($_.State -eq 'Listen' -or
            ($null -eq $_.State -and $_.RemotePort -eq 0 -and $_.RemoteAddress -in @('0.0.0.0', '::')))
    })
}

function Test-BackendCommand($Process, [string]$Runner, [switch]$Legacy) {
    if (-not $Process -or $Process.ExecutablePath -notin $backendExecutables -or -not $Process.CommandLine) { return $false }
    $parts = [XaEsportsBackend.CommandLine]::Split($Process.CommandLine)
    if ($Legacy) {
        if ($parts.Count -ne 3 -or $parts[1] -ne '-u') { return $false }
        if ([IO.Path]::IsPathFullyQualified($parts[2])) { return [IO.Path]::GetFullPath($parts[2]) -eq $Runner }
        if ($parts[2].Replace('\', '/') -ne '.tools/fronted-acceptance/serve.py') { return $false }
        # The old private runner was launched with a relative script argument.
        # Accept that compatibility form only from this checkout's venv launcher;
        # it remains External and is never eligible for Stop or ownership capture.
        if ($Process.ExecutablePath -eq $backendPython) { return $true }
        $parent = Get-BackendProcess $Process.ParentProcessId
        if (-not $parent -or $parent.ExecutablePath -ne $backendPython -or -not $parent.CommandLine) { return $false }
        $parentParts = [XaEsportsBackend.CommandLine]::Split($parent.CommandLine)
        return ($parentParts.Count -eq 3 -and $parentParts[1] -eq '-u' -and $parentParts[2] -eq $parts[2])
    }
    return ($parts.Count -eq 5 -and $parts[1] -eq '-u' -and
        [IO.Path]::IsPathFullyQualified($parts[2]) -and
        [IO.Path]::GetFullPath($parts[2]) -eq $Runner -and
        $parts[3] -eq '--port' -and $parts[4] -eq [string]$Port)
}

function Read-BackendState {
    if (-not (Test-Path -LiteralPath $backendState)) { return $null }
    try { $record = Get-Content -LiteralPath $backendState -Raw | ConvertFrom-Json }
    catch { throw "Invalid managed backend state: $backendState. No process was changed." }
    if ($record.Schema -ne 1 -or $record.Port -ne $Port -or $record.Host -ne '127.0.0.1' -or
        $record.RunnerPath -ne $backendRunner -or $record.ProcessId -le 0 -or
        $record.ExecutablePath -notin $backendExecutables) {
        throw "Backend ownership record does not match this checkout/port: $backendState."
    }
    return $record
}

function Assert-ManagedBackend($Record, [switch]$AllowStopped) {
    $process = Get-BackendProcess $Record.ProcessId
    if (-not $process) {
        if ($AllowStopped) { return $null }
        throw 'Managed backend PID no longer exists.'
    }
    if ($process.CreationDate.ToUniversalTime().Ticks -ne [long]$Record.CreationTimeUtcTicks -or
        $process.ExecutablePath -ne $Record.ExecutablePath -or
        $process.CommandLine -cne $Record.CommandLine -or
        -not (Test-BackendCommand $process $backendRunner)) {
        throw 'Managed backend PID identity changed; refusing to adopt or stop it.'
    }
    $listeners = @(Get-BackendListeners)
    if ($listeners.Count -ne 1 -or $listeners[0].OwningProcess -ne $process.ProcessId -or
        $listeners[0].LocalAddress -ne '127.0.0.1') {
        throw 'Managed backend does not exclusively own its recorded loopback listener; no process was changed.'
    }
    return $process
}

function Test-BackendHttp {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(4)
    try {
        $response = $client.GetAsync("http://127.0.0.1:$Port/api/health/").GetAwaiter().GetResult()
        $data = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
        if ([int]$response.StatusCode -ne 200 -or $data.status -ne 'ok') { throw 'Backend readiness check failed.' }
    } finally { $client.Dispose(); $handler.Dispose() }
}

function Invoke-BackendCheck {
    $result = & $backendPython -u $backendRunner --port $Port --check
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency/configuration check failed; no bootstrap or migration was attempted.' }
    $result | ConvertFrom-Json
}

function Get-BackendStatus {
    $record = Read-BackendState
    $listeners = @(Get-BackendListeners)
    if ($record) {
        $process = Assert-ManagedBackend $record -AllowStopped
        if ($process) {
            Test-BackendHttp
            return [pscustomobject]@{ Status='Running'; Port=$Port; ProcessId=$process.ProcessId; Managed=$true; Runner=$backendRunner }
        }
    }
    if (-not $listeners.Count) {
        return [pscustomobject]@{ Status='Stopped'; Port=$Port; ProcessId=$null; Managed=$false; Runner=$backendRunner }
    }
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1') {
        throw "Port $Port has an unexpected listener; it was not changed."
    }
    $process = Get-BackendProcess $listeners[0].OwningProcess
    $known = (Test-BackendCommand $process $backendRunner) -or (Test-BackendCommand $process $backendLegacyRunner -Legacy)
    if (-not $known) { throw "Port $Port belongs to an unmanaged, unrecognized process; it was not changed." }
    Test-BackendHttp
    [pscustomobject]@{ Status='External'; Port=$Port; ProcessId=$process.ProcessId; Managed=$false; Runner=$process.CommandLine }
}

function Start-Backend {
    $status = Get-BackendStatus
    if ($status.Status -ne 'Stopped') { return $status }
    Invoke-BackendCheck | Out-Null
    # A dead PID record may be removed only after verifying its PID is absent.
    if (Test-Path -LiteralPath $backendState) { Remove-Item -LiteralPath $backendState }
    $launched = Start-Process -FilePath $backendPython -ArgumentList @('-u', "`"$backendRunner`"", '--port', [string]$Port) `
        -WorkingDirectory $backendRepo -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
    $deadline = [DateTime]::UtcNow.AddSeconds(35)
    do {
        $listeners = @(Get-BackendListeners)
        if ($listeners.Count) {
            if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1') { throw 'Unexpected listener appeared while starting; no process was stopped.' }
            $process = Get-BackendProcess $listeners[0].OwningProcess
            if (-not (Test-BackendCommand $process $backendRunner) -or
                ($process.ProcessId -ne $launched.Id -and $process.ParentProcessId -ne $launched.Id)) {
                throw 'Listener does not belong to the launched backend; no process was adopted.'
            }
            $record = [ordered]@{
                Schema=1; Host='127.0.0.1'; Port=$Port; ProcessId=$process.ProcessId
                CreationTimeUtcTicks=[string]$process.CreationDate.ToUniversalTime().Ticks
                ExecutablePath=$process.ExecutablePath; RunnerPath=$backendRunner; CommandLine=$process.CommandLine
            }
            $temporary = "$backendState.$([guid]::NewGuid().ToString('N')).tmp"
            $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
            Move-Item -LiteralPath $temporary -Destination $backendState
            Test-BackendHttp
            return Get-BackendStatus
        }
        $launched.Refresh()
        if ($launched.HasExited) { throw "Backend exited before listening; inspect $backendErr and $backendOut." }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Backend did not become ready in 35 seconds; inspect $backendErr and $backendOut. No unrelated process was stopped."
}

function Stop-Backend {
    $record = Read-BackendState
    if (-not $record) {
        if (@(Get-BackendListeners).Count) { throw "Port $Port is not managed by this script; Stop refused. Its owner must stop it." }
        return Get-BackendStatus
    }
    $process = Assert-ManagedBackend $record -AllowStopped
    if ($process) {
        # Pin a process handle before the final identity check, avoiding PID
        # reuse between validation and termination. Never invoke a port killer.
        $handle = [Diagnostics.Process]::GetProcessById($process.ProcessId)
        try {
            $null = $handle.Handle
            $null = Assert-ManagedBackend $record
            $handle.Kill()
            if (-not $handle.WaitForExit(10000)) { throw 'Managed backend did not exit within 10 seconds.' }
        } finally { $handle.Dispose() }
    }
    Remove-Item -LiteralPath $backendState
    if (@(Get-BackendListeners).Count) { throw "Port $Port acquired another listener after stopping; it was not changed." }
    [pscustomobject]@{ Status='Stopped'; Port=$Port; ProcessId=$null; Managed=$false; Runner=$backendRunner }
}

$backendLock = $null
try {
    try { $backendLock = [IO.File]::Open((Join-Path $backendRuntime "backend-$Port.lock"), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw "Another backend action is running for port $Port." }
    switch ($Action) {
        'Check' { Invoke-BackendCheck }
        'Status' { Get-BackendStatus }
        'Start' { Start-Backend }
        'Stop' { Stop-Backend }
        'Restart' { Stop-Backend | Out-Null; Start-Backend }
    }
} finally { if ($backendLock) { $backendLock.Dispose() } }
