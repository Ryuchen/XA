param([string]$Project = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot = (Resolve-Path -LiteralPath $Project).Path
$taskFiles = Get-Content -LiteralPath (Join-Path $taskRoot 'output\delivery-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskManifest = Get-Content -LiteralPath (Join-Path $taskRoot 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
function Write-Package([string]$Name, $Entries) {
  $taskZipPath = Join-Path $taskRoot $Name
  $taskStream = [IO.File]::Open($taskZipPath,[IO.FileMode]::Create)
  $taskZip = [IO.Compression.ZipArchive]::new($taskStream,[IO.Compression.ZipArchiveMode]::Create)
  try {
    foreach($taskEntry in $Entries) {
      [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip,(Join-Path $taskRoot $taskEntry.Source),$taskEntry.Name,[IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
  } finally { $taskZip.Dispose(); $taskStream.Dispose() }
  Get-Item -LiteralPath $taskZipPath | Select-Object Name,Length
}
$taskSourceEntries = @($taskFiles | ForEach-Object { @{Source=$_;Name=$_.Replace('\','/')} })
Write-Package '你的护不行-三版封面广告-源稿-r3.zip' $taskSourceEntries
$taskPngEntries = @($taskManifest | ForEach-Object {
  $taskProduct = if($_.id.StartsWith('dogtag')){'狗牌版'}elseif($_.id.StartsWith('firearms')){'枪械版'}else{'左轮版'}
  $taskKind = if($_.id.EndsWith('cover')){'封面'}else{'广告'}
  @{Source=$_.png;Name="你的护不行-$taskProduct-$taskKind.png"}
})
Write-Package '你的护不行-三版封面广告-PNG-r3.zip' $taskPngEntries
foreach($taskPackage in @(@{Name='你的护不行-三版封面广告-源稿-r3.zip';Entries=$taskSourceEntries},@{Name='你的护不行-三版封面广告-PNG-r3.zip';Entries=$taskPngEntries})) {
  $taskZip = [IO.Compression.ZipFile]::OpenRead((Join-Path $taskRoot $taskPackage.Name))
  try {
    foreach($taskItem in $taskPackage.Entries | Where-Object Source -like 'output/png/*') {
      $taskEntry = $taskZip.GetEntry($taskItem.Name)
      $taskStream = $taskEntry.Open()
      try { $taskHash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($taskStream)) } finally {$taskStream.Dispose()}
      $taskOriginal=(Get-FileHash -LiteralPath (Join-Path $taskRoot $taskItem.Source) -Algorithm SHA256).Hash
      if($taskHash -ne $taskOriginal) {throw 'Packaged PNG differs from reviewed PNG'}
    }
  } finally {$taskZip.Dispose()}
}
Write-Output 'Both packages verified against the six final PNGs.'
