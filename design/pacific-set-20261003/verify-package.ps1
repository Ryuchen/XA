param([string]$Project)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd('\')
$taskState=Get-Content -LiteralPath (Join-Path $taskRoot 'campaign-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskHashes=Get-Content -LiteralPath (Join-Path $taskRoot $taskState.finalVisualQA.hashes) -Raw -Encoding UTF8 | ConvertFrom-Json
$taskZip=[System.IO.Compression.ZipFile]::OpenRead((Join-Path $taskRoot $taskState.deliveryFiles.sourceZip))
$taskChecks=@()
try {
  foreach($taskFile in $taskHashes.files){
    $taskEntry=$taskZip.GetEntry($taskFile.file)
    if(-not $taskEntry){throw ('Missing source zip entry '+$taskFile.file)}
    $taskSHA=[System.Security.Cryptography.SHA256]::Create()
    $taskEntryStream=$taskEntry.Open()
    try{$taskActual=([System.BitConverter]::ToString($taskSHA.ComputeHash($taskEntryStream))).Replace('-','').ToLowerInvariant()}finally{$taskEntryStream.Dispose();$taskSHA.Dispose()}
    if($taskActual -ne $taskFile.sha256){throw ('Wrong final file in source zip '+$taskFile.file)}
    $taskChecks+=@{package='source';file=$taskFile.file;passed=$true}
  }
  $taskEntries=$taskZip.Entries.Count
}finally{$taskZip.Dispose()}
$taskZip=[System.IO.Compression.ZipFile]::OpenRead((Join-Path $taskRoot $taskState.deliveryFiles.pngZip))
try{
  if($taskZip.Entries.Count -ne 6){throw 'PNG ZIP must contain six images'}
  foreach($taskPage in $taskState.deliveryScope.pageIds){
    $taskEntry=$taskZip.GetEntry($taskState.deliveryFiles.pngEntryNames.($taskPage))
    if(-not $taskEntry){throw ('Missing PNG zip entry '+$taskPage)}
    $taskExpected=($taskHashes.files | Where-Object {$_.file -eq ('output/png/'+$taskPage+'.png')}).sha256
    $taskSHA=[System.Security.Cryptography.SHA256]::Create();$taskEntryStream=$taskEntry.Open()
    try{$taskActual=([System.BitConverter]::ToString($taskSHA.ComputeHash($taskEntryStream))).Replace('-','').ToLowerInvariant()}finally{$taskEntryStream.Dispose();$taskSHA.Dispose()}
    if($taskActual -ne $taskExpected){throw ('Wrong final PNG '+$taskPage)}
    $taskChecks+=@{package='png';file=$taskEntry.FullName;passed=$true}
  }
}finally{$taskZip.Dispose()}
$taskResult=@{revision=$taskState.revision;passed=$true;checks=$taskChecks;sourceEntries=$taskEntries;pngEntries=6;pngMB=[Math]::Round((Get-Item -LiteralPath (Join-Path $taskRoot $taskState.deliveryFiles.pngZip)).Length/1MB,2);sourceMB=[Math]::Round((Get-Item -LiteralPath (Join-Path $taskRoot $taskState.deliveryFiles.sourceZip)).Length/1MB,2)}
$taskJSON=$taskResult | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText((Join-Path $taskRoot ('review\package-verification-'+$taskState.revision+'.json')),$taskJSON,[System.Text.UTF8Encoding]::new($false))
Write-Output ($taskResult | Select-Object passed,sourceEntries,pngEntries,pngMB,sourceMB | ConvertTo-Json)
