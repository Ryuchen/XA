param([string]$Project,[string]$Revision='uiux-typography-r4')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskFiles=@('content.json','campaign-state.json','layout.json','layout-adapter.mjs','output/manifest.json','output/DELIVERY.md','review/index.html','review/stage3-misans-preview.png','review/visual-qa.md','review/final-layout-audit.json','review/taste-preflight.md','source/workflow.mjs','source/render.mjs','source/scope.mjs','source/outline-text.ps1','source/layout-experience.mjs','source/verify-final.cjs','assets/backgrounds/experience-cover.png','assets/backgrounds/experience-social-r3.png','assets/backgrounds/experience-price.png')
$taskFiles+=@('review/uiux-typography-r4.md','review/typography-audit-r4.json','review/typography-before-after-r4.png','source/verify-typography-r4.cjs','source/build-final-review.cjs','source/package-confirmation.ps1','03-backgrounds/social-no-ticket-r3-prompt.txt')
foreach($page in $taskManifest){$taskFiles+=@($page.png,$page.outlinedSVG,$page.editableSVG,$page.preview,('review/'+$page.id+'-phone-final.png'),('review/'+$page.id+'-typography-proof-r4.png'),('review/'+$page.id+'-text-bounds.json'),('review/'+$page.id+'-text-overlaps.json'),('review/typography-before-r4/'+$page.id+'.png'))}
if($Revision -notmatch '^[a-z0-9-]+$'){throw 'Invalid revision'}
$taskZipPath=Join-Path $taskRoot ('campaign-'+$Revision+'.zip')
$taskStream=[System.IO.File]::Open($taskZipPath,[System.IO.FileMode]::Create)
$taskZip=[System.IO.Compression.ZipArchive]::new($taskStream,[System.IO.Compression.ZipArchiveMode]::Create)
try{foreach($name in ($taskFiles|Select-Object -Unique)){$full=[System.IO.Path]::GetFullPath((Join-Path $taskRoot $name));if(-not $full.StartsWith($taskRoot+'\',[System.StringComparison]::OrdinalIgnoreCase)){throw 'Outside campaign folder'};[System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip,$full,$name.Replace('\','/'),[System.IO.Compression.CompressionLevel]::Optimal)|Out-Null}}finally{$taskZip.Dispose();$taskStream.Dispose()}
$checkZip=[System.IO.Compression.ZipFile]::OpenRead($taskZipPath)
try{[PSCustomObject]@{Zip=$taskZipPath;Entries=$checkZip.Entries.Count;PngPages=@($checkZip.Entries|Where-Object FullName -like 'output/png/*').Count;EditablePages=@($checkZip.Entries|Where-Object FullName -like 'output/svg-editable/*').Count;OutlinedPages=@($checkZip.Entries|Where-Object FullName -like 'output/svg-outlined/*').Count}}finally{$checkZip.Dispose()}
