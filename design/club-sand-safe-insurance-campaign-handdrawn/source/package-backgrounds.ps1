$ErrorActionPreference = 'Stop'
$campaignRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stagingDirectory = Join-Path $campaignRoot ('output\backgrounds-package-' + [Guid]::NewGuid().ToString('N'))
$zipTarget = Join-Path $campaignRoot 'output\campaign-sand-safe-insurance-backgrounds-v1.zip'
if (Test-Path -LiteralPath $zipTarget) { throw 'The package already exists; choose a new revision instead of overwriting.' }
New-Item -ItemType Directory -Path $stagingDirectory | Out-Null
$fileNames = @(
  'README.md', 'content.json', 'campaign-state.json',
  'references\rules-confirmation.md',
  '03-backgrounds\image-metadata.json',
  '03-backgrounds\prompt-poster.md', '03-backgrounds\prompt-cover.md', '03-backgrounds\prompt-social.md',
  '03-backgrounds\reward-divider-edit-prompts.md',
  'assets\backgrounds\poster.png', 'assets\backgrounds\cover.png', 'assets\backgrounds\social.png',
  '04-png-export\desktop-absolute-background.png', '04-png-export\mobile-absolute-background.png',
  '04-png-export\cover-background.png', '04-png-export\social-background.png',
  '04-png-export\export-metadata.json',
  '02-layouts\generated-background-measurements.json', '02-layouts\layout-brief.md',
  '02-layouts\poster-actual-layout.png', '02-layouts\cover-actual-layout.png', '02-layouts\social-actual-layout.png',
  '02-layouts\poster-actual-layout.svg', '02-layouts\cover-actual-layout.svg', '02-layouts\social-actual-layout.svg',
  'review\backgrounds.html', 'review\background-overview.png', 'review\layout-overview.png', 'review\visual-qa.md',
  'review\phone-390\desktop-absolute-background.png', 'review\phone-390\mobile-absolute-background.png',
  'review\phone-390\cover-background.png', 'review\phone-390\social-background.png',
  'review\phone-390\poster-layout.png', 'review\phone-390\cover-layout.png', 'review\phone-390\social-layout.png',
  'review\details\poster-reward-background.png', 'review\details\poster-reward-layout.png',
  'review\details\poster-notes-background.png', 'review\details\poster-notes-layout.png',
  'review\details\social-reward-background.png', 'review\details\social-reward-layout.png'
)
foreach ($name in $fileNames) {
  $sourceFile = Join-Path $campaignRoot $name
  $destinationFile = Join-Path $stagingDirectory $name
  New-Item -ItemType Directory -Path (Split-Path -Parent $destinationFile) -Force | Out-Null
  Copy-Item -LiteralPath $sourceFile -Destination $destinationFile
}
$archivedHtml = Join-Path $stagingDirectory 'review\backgrounds.html'
$html = [System.IO.File]::ReadAllText($archivedHtml)
$html = $html -replace '<a href="../output/campaign-sand-safe-insurance-backgrounds-v1.zip">[^<]*</a>', ''
[System.IO.File]::WriteAllText($archivedHtml,$html,[System.Text.UTF8Encoding]::new($false))
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($stagingDirectory,$zipTarget,[System.IO.Compression.CompressionLevel]::Optimal,$false)
$archive = [System.IO.Compression.ZipFile]::OpenRead($zipTarget)
try {
  [pscustomobject]@{Package=$zipTarget;Files=$archive.Entries.Count;Bytes=(Get-Item -LiteralPath $zipTarget).Length;Stage='Background confirmation'} | ConvertTo-Json
} finally { $archive.Dispose() }
