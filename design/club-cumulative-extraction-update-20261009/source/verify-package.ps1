param([string]$Project)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=[System.IO.Path]::GetFullPath($Project)
$taskFinal=Get-Content -LiteralPath (Join-Path $taskRoot 'output/final-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($taskType in @('complete','png')){
 $taskPath=if($taskType -eq 'complete'){Join-Path $taskRoot 'campaign-cumulative-r2.zip'}else{Join-Path $taskRoot 'output/campaign-cumulative-r2-png.zip'}
 $taskZip=[System.IO.Compression.ZipFile]::OpenRead($taskPath)
 try{
  foreach($taskRecord in $taskFinal.pngs){
   $taskName=if($taskType -eq 'complete'){$taskRecord.path}else{[System.IO.Path]::GetFileName($taskRecord.path)}
   $taskEntry=$taskZip.GetEntry($taskName)
   if($null -eq $taskEntry){throw "Missing PNG $taskName"}
   $taskStream=$taskEntry.Open()
   $taskHash=[System.Security.Cryptography.SHA256]::Create()
   try{$taskActual=[System.BitConverter]::ToString($taskHash.ComputeHash($taskStream)).Replace('-','').ToLowerInvariant()}finally{$taskStream.Dispose();$taskHash.Dispose()}
   if($taskActual -ne $taskRecord.sha256){throw "PNG mismatch $taskName"}
  }
  if($taskType -eq 'complete'){
   $taskReader=[System.IO.StreamReader]::new($taskZip.GetEntry('content.json').Open())
   try{$taskContent=$taskReader.ReadToEnd() | ConvertFrom-Json}finally{$taskReader.Dispose()}
   if($taskContent.guaranteePolicy.suffix -ne 88){throw 'Wrong suffix policy'}
   $taskReader=[System.IO.StreamReader]::new($taskZip.GetEntry('review/index.html').Open())
   try{$taskHTML=$taskReader.ReadToEnd()}finally{$taskReader.Dispose()}
   if($taskHTML -notmatch 'cumulative-r2' -or $taskHTML -notmatch '后两位'){throw 'Stale review page'}
  }
  Write-Output "$taskType package: 7 PNG hashes verified; $($taskZip.Entries.Count) entries."
 }finally{$taskZip.Dispose()}
}
