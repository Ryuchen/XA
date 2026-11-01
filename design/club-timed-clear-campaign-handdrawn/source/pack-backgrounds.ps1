param([string]$Project)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskNames=@('content.json','campaign-state.json','STAGE-2-README.md','package-backgrounds.mjs','source/pack-backgrounds.ps1','review/index.html','review/backgrounds.html','review/background-overview.png','review/layout-overview.png','review/export-manifest.json')
foreach($taskDir in @('01-illustrations','02-layouts','03-backgrounds','04-png-export')){
  $taskNames+=Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskDir) -File -Recurse | ForEach-Object {$_.FullName.Substring($taskRoot.Length+1)}
}
$taskZipPath=Join-Path $taskRoot 'campaign-timed-clear-backgrounds-v1.zip'
$taskStream=[System.IO.File]::Open($taskZipPath,[System.IO.FileMode]::Create)
$taskZip=[System.IO.Compression.ZipArchive]::new($taskStream,[System.IO.Compression.ZipArchiveMode]::Create)
try {
  foreach($taskName in ($taskNames | Select-Object -Unique)){
    $taskFull=[System.IO.Path]::GetFullPath((Join-Path $taskRoot $taskName))
    if(-not $taskFull.StartsWith($taskRoot+'\',[System.StringComparison]::OrdinalIgnoreCase)){throw '文件超出本海报项目目录'}
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip,$taskFull,$taskName.Replace('\','/'),[System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
  }
}finally{$taskZip.Dispose();$taskStream.Dispose()}
Get-Item -LiteralPath $taskZipPath | Select-Object FullName,Length
