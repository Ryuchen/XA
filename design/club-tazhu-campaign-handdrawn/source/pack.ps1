param([string]$Project,[string]$Revision)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskPlan=Get-Content -LiteralPath (Join-Path $taskRoot 'layout.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRelative=@('content.json','campaign-state.json','layout.json','layout-adapter.mjs','build-backgrounds.mjs','build-cover.mjs','prepare-props.mjs','build-review.mjs','output/manifest.json','output/DELIVERY.md','review/index.html','review/visual-qa.md','review/typesetting-audit.json','review/typesetting-overview.png','review/final-overview-sand-r2.png','review/prop-overview-v1.png','02-layouts/layout-spec.json','02-layouts/decoration-plan.md','02-layouts/cover-tazhu-sand-v1.svg','assets/backgrounds/poster-gold.png')
foreach($taskPage in $taskManifest){$taskRelative+=@($taskPage.png,$taskPage.outlinedSVG,$taskPage.editableSVG,$taskPage.preview)}
foreach($taskPage in $taskPlan.pages){$taskRelative+='assets/backgrounds/'+$taskPage.bg}
foreach($taskDir in @('assets\props','assets\typography','01-illustrations','references\game-items','source','review\phone-390','review\details')){
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
