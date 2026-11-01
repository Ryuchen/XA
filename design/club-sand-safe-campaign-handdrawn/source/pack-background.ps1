param([Parameter(Mandatory=$true)][string]$Project)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath($Project)
$stageRoot = Join-Path $projectRoot 'output/background-package-v1'
$stageFiles = @(
  'content.json',
  'campaign-state.json',
  '01-illustrations/sand-safe-medic-mouse-v1.png',
  '01-illustrations/prompts.md',
  '02-layouts/layout-zones.json',
  '02-layouts/generated-background-measurements.json',
  '02-layouts/background-position-guide.svg',
  '03-backgrounds/poster-gold-v1.png',
  '03-backgrounds/prompts.md',
  '04-png-export/sand-safe-absolute-background.png',
  'review/backgrounds.html',
  'review/background-native.png',
  'review/background-phone.png',
  'review/background-position-guide.png',
  'review/background-position-phone.png',
  'review/visual-qa.md'
)
foreach ($relativeFile in $stageFiles) {
  $sourceFile = Join-Path $projectRoot $relativeFile
  $targetFile = Join-Path $stageRoot $relativeFile
  $targetParent = Split-Path -Parent $targetFile
  New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
  Copy-Item -LiteralPath $sourceFile -Destination $targetFile -Force
}
$zipFile = Join-Path $projectRoot 'output/campaign-sand-safe-background-v1.zip'
Compress-Archive -Path (Join-Path $stageRoot '*') -DestinationPath $zipFile -CompressionLevel Optimal -Force
Get-Item -LiteralPath $zipFile | Select-Object FullName, Length
