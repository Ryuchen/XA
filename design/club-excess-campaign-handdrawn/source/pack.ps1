param([string]$Project,[string]$Revision)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskPlan=Get-Content -LiteralPath (Join-Path $taskRoot 'layout.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskState=Get-Content -LiteralPath (Join-Path $taskRoot 'campaign-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (@($taskManifest).Count -ne @($taskState.deliveryScope.pageIds).Count -or @($taskManifest | Where-Object {$_.id -notin $taskState.deliveryScope.pageIds}).Count) {throw 'Manifest must match the current delivery scope'}
$taskRelative=@('content.json','campaign-state.json','layout.json','layout-adapter.mjs','output/manifest.json','output/DELIVERY.md','review/index.html')
$taskRelative+=@($taskState.vectorDelivery.reviewFiles | Where-Object {$_})
foreach($taskPage in $taskManifest){$taskRelative+=@($taskPage.png,$taskPage.outlinedSVG,$taskPage.editableSVG,$taskPage.preview)}
foreach($taskPage in $taskPlan.pages){$taskRelative+=('assets/backgrounds/'+$taskPage.bg)}
foreach($taskDir in @('source','source-references')){
  $taskRelative+=Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskDir) -File -Recurse | ForEach-Object {$_.FullName.Substring($taskRoot.Length+1)}
}
$taskZipPath=Join-Path $taskRoot ('campaign-'+$Revision+'.zip')
$taskStream=[System.IO.File]::Open($taskZipPath,[System.IO.FileMode]::Create)
$taskZip=[System.IO.Compression.ZipArchive]::new($taskStream,[System.IO.Compression.ZipArchiveMode]::Create)
try {
  foreach($taskName in ($taskRelative | Select-Object -Unique)){
    $taskFull=[System.IO.Path]::GetFullPath((Join-Path $taskRoot $taskName))
    if(-not $taskFull.StartsWith($taskRoot+'\',[System.StringComparison]::OrdinalIgnoreCase)){throw 'File is outside the poster project directory'}
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip,$taskFull,$taskName.Replace('\','/'),[System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
  }
}finally{$taskZip.Dispose();$taskStream.Dispose()}
Write-Output $taskZipPath
