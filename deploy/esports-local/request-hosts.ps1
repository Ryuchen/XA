#Requires -Version 7.0
param([switch]$Wait)
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'install-hosts.ps1'
$runtime = Join-Path $PSScriptRoot '.runtime'
[IO.Directory]::CreateDirectory($runtime) | Out-Null
$result = Join-Path $runtime 'hosts-result.json'
if (Test-Path -LiteralPath $result) { Remove-Item -LiteralPath $result }
# A normal Windows UAC prompt is required. No execution-policy or security-policy changes.
$shell = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $shell)) { throw 'Run this helper from PowerShell 7 (pwsh). Windows PowerShell 5 may refuse local scripts under Restricted policy.' }
$process = Start-Process -FilePath $shell -Verb RunAs -WindowStyle Hidden -ArgumentList @('-NoProfile', '-File', ('"' + $script + '"')) -PassThru
Write-Output ('UAC process started. Result: ' + $result)
if ($Wait) {
  $deadline = [DateTime]::UtcNow.AddSeconds(120)
  while (-not $process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 250; $process.Refresh() }
  if (-not (Test-Path -LiteralPath $result)) { throw 'Hosts setup did not complete. Complete the normal Windows UAC prompt, then run deploy/dev.ps1 Up again.' }
  $status = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
  if (-not $status.success) { throw "Hosts setup failed: $($status.error)" }
  Write-Host 'Local hosts configuration completed.'
}
