param([string]$Project,[string]$Revision)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRelative=@('content.json','campaign-state.json','layout.json','layout-adapter.mjs','output/manifest.json','output/DELIVERY.md','review/index.html','review/visual-qa.md','review/final-verification.json','review/geometry-diagnostic.json')
foreach($taskPage in $taskManifest){$taskRelative+=@($taskPage.png,$taskPage.outlinedSVG,$taskPage.editableSVG,$taskPage.preview)}
foreach($taskDir in @('assets\backgrounds','assets\illustrations','source','review')){
  $taskRelative+=Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskDir) -File -Recurse | ForEach-Object {$_.FullName.Substring($taskRoot.Length+1)}
}
$taskZipPath=Join-Path $taskRoot ('campaign-'+$Revision+'.zip')
$taskStream=[System.IO.File]::Open($taskZipPath,[System.IO.FileMode]::Create)
$taskZip=[System.IO.Compression.ZipArchive]::new($taskStream,[System.IO.Compression.ZipArchiveMode]::Create)
try {
  foreach($taskName in ($taskRelative | Select-Object -Unique)){
    $taskFull=[System.IO.Path]::GetFullPath((Join-Path $taskRoot $taskName))
    if(-not $taskFull.StartsWith($taskRoot+'\',[System.StringComparison]::OrdinalIgnoreCase)){throw '文件超出海报项目目录'}
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip,$taskFull,$taskName.Replace('\','/'),[System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
  }
}finally{$taskZip.Dispose();$taskStream.Dispose()}
Write-Output $taskZipPath
$pngZipPath=Join-Path $taskRoot ('黄金矿工-成品PNG-'+$Revision+'.zip')
$pngStream=[System.IO.File]::Open($pngZipPath,[System.IO.FileMode]::Create)
$pngArchive=[System.IO.Compression.ZipArchive]::new($pngStream,[System.IO.Compression.ZipArchiveMode]::Create)
$pngNames=@{desktop='黄金矿工-端游.png';mobile='黄金矿工-手游.png';social='黄金矿工-双端价格封面.png';cover='黄金矿工-封面.png'}
try{
 foreach($taskPage in $taskManifest){[System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($pngArchive,(Join-Path $taskRoot $taskPage.png),$pngNames[$taskPage.id],[System.IO.Compression.CompressionLevel]::Optimal)|Out-Null}
}finally{$pngArchive.Dispose();$pngStream.Dispose()}
Write-Output $pngZipPath
