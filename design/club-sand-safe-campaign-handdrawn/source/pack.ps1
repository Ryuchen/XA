param([string]$Project,[string]$Revision)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRelative=@('content.json','campaign-state.json','layout.json','layout-adapter.mjs','REBUILD.md','output/manifest.json','output/DELIVERY.md','review/index.html','review/visual-qa.md','review/typesetting-audit.json','review/final-placements.json','02-layouts/final-background-measurements.json','02-layouts/final-font-outlines.json','03-backgrounds/poster-bakhsh-sand-v3.png','03-backgrounds/bakhsh-sand-edit-prompt-v3.md','03-backgrounds/decor-edit-prompt-v2.md','references/decor-references.md','source/build-final-layout.mjs','source/outline-text.ps1','source/workflow.mjs','source/render.mjs','source/pack.ps1','source/review-final.mjs')
foreach($taskPage in $taskManifest){$taskRelative+=@($taskPage.png,$taskPage.outlinedSVG,$taskPage.editableSVG,$taskPage.preview)}
$taskPlan=Get-Content -LiteralPath (Join-Path $taskRoot 'layout.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($taskPage in $taskPlan.pages){
  $taskRelative+=('assets/backgrounds/'+$taskPage.bg)
  $taskRelative+=('review/'+$taskPage.id+'-text-bounds.json')
  if($taskPage.pageKind -eq 'cover'){
    foreach($taskSuffix in @('phone','final-preview','hero','reward')){$taskRelative+=('review/'+$taskPage.id+'-'+$taskSuffix+'.png')}
  }elseif($taskPage.pageKind -eq 'advert'){
    foreach($taskSuffix in @('phone','final-preview','header','price','targets','reward')){$taskRelative+=('review/'+$taskPage.id+'-'+$taskSuffix+'.png')}
  }else{
    foreach($taskSuffix in @('phone','final-preview','header','reward','price','objectives','notices','decor')){$taskRelative+=('review/'+$taskPage.sourcePlatform+'-'+$taskSuffix+'.png')}
  }
}
$taskRelative+=@('03-backgrounds/cover-standard-horizontal-v2.png','03-backgrounds/cover-standard-redraw-prompt-v2.md','references/standard-cover-references.json','references/standard-cover-background.png')
$taskRelative+=@('03-backgrounds/advert-bakhsh-v2.png','03-backgrounds/advert-bakhsh-prompt-v1.md','03-backgrounds/advert-bakhsh-refine-prompt-v2.md')
$taskRelative+=@('review/suite-preview.png')
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
