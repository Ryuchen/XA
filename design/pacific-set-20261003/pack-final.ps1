param([string]$Project,[string]$Revision)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskState=Get-Content -LiteralPath (Join-Path $taskRoot 'campaign-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskQA=Get-Content -LiteralPath (Join-Path $taskRoot $taskState.finalVisualQA.geometry) -Raw -Encoding UTF8 | ConvertFrom-Json
if($taskQA.failures.Count -ne 0 -or $taskQA.missingPages.Count -ne 0 -or -not $taskState.finalVisualQA.passed){throw 'Final QA is incomplete'}
function Write-TaskZip([string]$target,[object[]]$files){
  $taskZipFull=[System.IO.Path]::GetFullPath((Join-Path $taskRoot $target))
  if(-not $taskZipFull.StartsWith($taskRoot+'\',[System.StringComparison]::OrdinalIgnoreCase)){throw 'Zip outside project'}
  $taskStream=[System.IO.File]::Open($taskZipFull,[System.IO.FileMode]::Create)
  $taskZip=[System.IO.Compression.ZipArchive]::new($taskStream,[System.IO.Compression.ZipArchiveMode]::Create)
  try {
    foreach($taskFile in $files){
      $taskFull=[System.IO.Path]::GetFullPath((Join-Path $taskRoot $taskFile.Source))
      if(-not $taskFull.StartsWith($taskRoot+'\',[System.StringComparison]::OrdinalIgnoreCase)){throw 'File outside project'}
      [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip,$taskFull,$taskFile.Entry.Replace('\','/'),[System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
  }finally{$taskZip.Dispose();$taskStream.Dispose()}
  Write-Output $taskZipFull
}
$taskPNGFiles=@()
foreach($taskPage in $taskManifest){
  $taskPNGFiles+=@{Source=$taskPage.png;Entry=$taskState.deliveryFiles.pngEntryNames.($taskPage.id)}
}
Write-TaskZip $taskState.deliveryFiles.pngZip $taskPNGFiles
$taskReviewPath=Join-Path $taskRoot 'review\index.html'
$taskHTML=Get-Content -LiteralPath $taskReviewPath -Raw -Encoding UTF8
$taskPNGLink='<a href="../'+$taskState.deliveryFiles.pngZip+'">PNG ZIP</a> | '
$taskHTML=$taskHTML.Replace('<p><a href="../campaign-','<p>'+$taskPNGLink+'<a href="../campaign-')
[System.IO.File]::WriteAllText($taskReviewPath,$taskHTML,[System.Text.UTF8Encoding]::new($false))
$taskRelative=@('content.json','campaign-state.json','layout.json','layout-adapter.mjs','output/manifest.json','output/DELIVERY.md','review/index.html','review/visual-qa.md','review/visual-qa-stage2-r2.md','review/visual-qa-r1.md','review/background-measured-zones-r2.json',$taskState.finalVisualQA.geometry,$taskState.finalVisualQA.business,$taskState.finalVisualQA.hashes,$taskState.finalVisualQA.contactSheet)
$taskRelative+=@($taskState.finalVisualQA.priceDetails)
foreach($taskPage in $taskManifest){
  $taskRelative+=@($taskPage.png,$taskPage.outlinedSVG,$taskPage.editableSVG,$taskPage.preview,('review/'+$taskPage.id+'-mobile390.png'),('review/'+$taskPage.id+'-text-bounds.json'),('review/'+$taskPage.id+'-text-overlaps.json'))
  if($taskPage.id -match '^(desktop|mobile)-'){$taskRelative+=@(('review/'+$taskPage.id+'-price-detail.png'),('review/'+$taskPage.id+'-rules-detail.png'))}
}
$taskRelative+=@($taskState.artwork.illustration)
foreach($taskBG in $taskState.artwork.backgrounds){$taskRelative+=@($taskBG.path)}
foreach($taskDir in @('source','references')){
  $taskRelative+=Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskDir) -File -Recurse | ForEach-Object {$_.FullName.Substring($taskRoot.Length+1)}
}
$taskAllFiles=@($taskRelative | Select-Object -Unique | ForEach-Object {@{Source=$_;Entry=$_}})
Write-TaskZip ('campaign-'+$Revision+'.zip') $taskAllFiles
