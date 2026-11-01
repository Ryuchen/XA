param([Parameter(Mandatory=$true)][string]$Project,[string]$Revision='redesign-r1')
$ErrorActionPreference='Stop'
if($Revision -notmatch '^[a-zA-Z0-9._-]+$'){throw 'Invalid revision'}
Add-Type -AssemblyName System.IO.Compression
$taskRoot=[System.IO.Path]::GetFullPath($Project).TrimEnd([char]92,[char]47)
$taskPrefix=$taskRoot+[System.IO.Path]::DirectorySeparatorChar
$taskStatePath=[System.IO.Path]::Combine($taskRoot,'campaign-state.json')
$taskStateOriginal=[System.IO.File]::ReadAllText($taskStatePath)
$taskState=$taskStateOriginal|ConvertFrom-Json
if($taskState.finalQA.status -ne 'passed'){throw 'Actual visual QA must pass before packaging'}
$taskTarget=[System.IO.Path]::GetFullPath([System.IO.Path]::Combine($taskRoot,'output','campaign-sand-safe-'+$Revision+'.zip'))
if(-not $taskTarget.StartsWith($taskPrefix,[System.StringComparison]::OrdinalIgnoreCase)){throw 'Target escapes project'}
if([System.IO.File]::Exists($taskTarget)){throw 'Package exists; choose a new revision'}
$taskFilesPath=[System.IO.Path]::Combine($taskRoot,'output','package-files.json')
$taskFiles=[System.IO.File]::ReadAllText($taskFilesPath)|ConvertFrom-Json
$taskRequired=@('source/pack-final.ps1','source/package-final.mjs')
$taskNames=@($taskFiles.files|ForEach-Object {$_.file})+$taskRequired
$taskNames=@($taskNames|Sort-Object -Unique)
$taskUtf8=[System.Text.UTF8Encoding]::new($false)
$taskSHA=[System.Security.Cryptography.SHA256]::Create()
function Get-TaskHash([byte[]]$Bytes){return [System.BitConverter]::ToString($taskSHA.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant()}
try{
 $taskState.stage='delivered'
 $taskState.nextStep='本轮四张套图已完成并通过成图验收。'
 $taskState|Add-Member -MemberType NoteProperty -Name finalDelivery -Value @{package=('output/campaign-sand-safe-'+$Revision+'.zip');review='review/index.html';preview='review/final-overview.png';basis='插画确认、底图确认、矢量填字及实际PNG复检均已完成；数字单位baseline、间距及固定列已核对。'} -Force
 [System.IO.File]::WriteAllText($taskStatePath,($taskState|ConvertTo-Json -Depth 50),$taskUtf8)
 $taskEntries=@()
 foreach($taskName in $taskNames){
  $taskAbsolute=[System.IO.Path]::GetFullPath([System.IO.Path]::Combine($taskRoot,$taskName.Replace('/',[System.IO.Path]::DirectorySeparatorChar)))
  if(-not $taskAbsolute.StartsWith($taskPrefix,[System.StringComparison]::OrdinalIgnoreCase)){throw ('File escapes project: '+$taskName)}
  $taskBytes=[System.IO.File]::ReadAllBytes($taskAbsolute)
  $taskEntries+=@{file=$taskName;bytes=$taskBytes.Length;sha256=(Get-TaskHash $taskBytes)}
 }
 [System.IO.File]::WriteAllText($taskFilesPath,(@{revision=$Revision;files=$taskEntries}|ConvertTo-Json -Depth 10),$taskUtf8)
 $taskAllNames=$taskNames+@('output/package-files.json')
 $taskStream=[System.IO.File]::Open($taskTarget,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write)
 $taskZip=[System.IO.Compression.ZipArchive]::new($taskStream,[System.IO.Compression.ZipArchiveMode]::Create,$false)
 try{
  foreach($taskName in $taskAllNames){
   $taskAbsolute=[System.IO.Path]::Combine($taskRoot,$taskName.Replace('/',[System.IO.Path]::DirectorySeparatorChar))
   $taskEntry=$taskZip.CreateEntry($taskName,[System.IO.Compression.CompressionLevel]::Optimal)
   $taskIn=[System.IO.File]::OpenRead($taskAbsolute);$taskOut=$taskEntry.Open()
   try{$taskIn.CopyTo($taskOut)}finally{$taskOut.Dispose();$taskIn.Dispose()}
  }
 }finally{$taskZip.Dispose();$taskStream.Dispose()}
 $taskReadStream=[System.IO.File]::OpenRead($taskTarget)
 $taskReadZip=[System.IO.Compression.ZipArchive]::new($taskReadStream,[System.IO.Compression.ZipArchiveMode]::Read,$false)
 try{
  if($taskReadZip.Entries.Count -ne $taskAllNames.Count){throw 'ZIP file count mismatch'}
  foreach($taskExpected in $taskEntries){
   $taskEntry=$taskReadZip.GetEntry($taskExpected.file)
   if($null -eq $taskEntry){throw ('Missing ZIP file '+$taskExpected.file)}
   $taskEntryStream=$taskEntry.Open();$taskMemory=[System.IO.MemoryStream]::new()
   try{$taskEntryStream.CopyTo($taskMemory);$taskHash=Get-TaskHash $taskMemory.ToArray()}finally{$taskMemory.Dispose();$taskEntryStream.Dispose()}
   if($taskHash -ne $taskExpected.sha256){throw ('ZIP SHA mismatch '+$taskExpected.file)}
  }
 }finally{$taskReadZip.Dispose();$taskReadStream.Dispose()}
 $taskReport=@{status='passed';package=('output/campaign-sand-safe-'+$Revision+'.zip');bytes=([System.IO.FileInfo]::new($taskTarget)).Length;entryCount=$taskAllNames.Count;sha256=(Get-TaskHash ([System.IO.File]::ReadAllBytes($taskTarget)));checked='Every packaged file SHA-256 equals the manifest and current project; final PNGs already checked against actual visual-QA hashes.'}
 [System.IO.File]::WriteAllText([System.IO.Path]::Combine($taskRoot,'output','package-verification.json'),($taskReport|ConvertTo-Json -Depth 8),$taskUtf8)
 $taskReport|ConvertTo-Json -Depth 8 -Compress|Write-Output
}catch{[System.IO.File]::WriteAllText($taskStatePath,$taskStateOriginal,$taskUtf8);throw}finally{$taskSHA.Dispose()}
