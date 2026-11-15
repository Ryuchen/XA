#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot '.runtime'
[IO.Directory]::CreateDirectory($runtime) | Out-Null
$result = Join-Path $runtime 'hosts-result.json'
try {
  $hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
  $backup = Join-Path $runtime ('hosts.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
  Copy-Item -LiteralPath $hostsPath -Destination $backup
  $text = [IO.File]::ReadAllText($hostsPath)
  $text = [regex]::Replace($text, '(?ms)^# BEGIN XA ESPORTS LOCAL\r?\n.*?^# END XA ESPORTS LOCAL(?:\r?\n)?', '')
  $names = @('esports.club', 'sl.esports.club', 'xa.esports.club', 'admin.esports.club')
  $kept = foreach ($line in ($text -split '\r?\n')) {
    $parts = $line -split '#', 2
    $fields = @($parts[0].Trim() -split '\s+' | Where-Object { $_ })
    if ($fields.Count -gt 1 -and @($fields[1..($fields.Count - 1)] | Where-Object { $names -contains $_.ToLowerInvariant() }).Count) {
      $aliases = @($fields[1..($fields.Count - 1)] | Where-Object { $names -notcontains $_.ToLowerInvariant() })
      if ($aliases.Count) { $fields[0] + "`t" + ($aliases -join ' ') + $(if ($parts.Count -gt 1) { ' #' + $parts[1] }) }
      elseif ($parts.Count -gt 1) { '#' + $parts[1] }
    } else { $line }
  }
  $managed = "# BEGIN XA ESPORTS LOCAL`r`n127.0.0.1 esports.club sl.esports.club xa.esports.club admin.esports.club`r`n# END XA ESPORTS LOCAL`r`n"
  [IO.File]::WriteAllText($hostsPath, (($kept -join "`r`n").TrimEnd() + "`r`n`r`n" + $managed), [Text.UTF8Encoding]::new($false))
  & ipconfig.exe /flushdns | Out-Null
  @{ success = $true; domains = $names; address = '127.0.0.1'; backup = $backup; completedAt = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $result -Encoding utf8
} catch {
  @{ success = $false; error = $_.Exception.Message; completedAt = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $result -Encoding utf8
  throw
}
