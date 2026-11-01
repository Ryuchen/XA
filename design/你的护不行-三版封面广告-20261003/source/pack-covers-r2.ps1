$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskRoot=Split-Path $PSScriptRoot -Parent
$taskIds=@('dogtag-cover','firearms-cover','revolver-cover')
$taskNames=@('狗牌版','枪械版','左轮版')
function Write-TaskZip($taskZipPath,$taskEntries){
 $taskStream=[IO.File]::Open($taskZipPath,[IO.FileMode]::Create)
 $taskArchive=New-Object IO.Compression.ZipArchive($taskStream,[IO.Compression.ZipArchiveMode]::Create,$false)
 try{foreach($taskEntry in $taskEntries){[IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskArchive,$taskEntry.source,$taskEntry.entry,[IO.Compression.CompressionLevel]::Optimal) | Out-Null}}
 finally{$taskArchive.Dispose();$taskStream.Dispose()}
}
$taskPngEntries=for($i=0;$i -lt 3;$i++){[pscustomobject]@{source=(Join-Path $taskRoot ('output\png\'+$taskIds[$i]+'.png'));entry=('你的护不行-'+$taskNames[$i]+'-封面.png')}}
$taskPngZip=Join-Path $taskRoot '你的护不行-蜂医灰鼠-三张封面-PNG-r2.zip'
Write-TaskZip $taskPngZip $taskPngEntries
$taskPaths=@('content.json','campaign-state.json','layout.json','layout-adapter.mjs','output/manifest.json','output/DELIVERY-r2.md','review/covers-r2.html','review/index.html','review/covers-overview-r2.png','review/covers-phone-r2.png','review/visual-qa.md','review/covers-layout-audit-r2.json','review/cover-background-stage-r2.json')
foreach($taskId in $taskIds){
 $taskVariant=$taskId.Replace('-cover','')
 $taskPaths+=@(('output/png/'+$taskId+'.png'),('output/svg-outlined/'+$taskId+'.svg'),('output/svg-editable/'+$taskId+'.svg'),('review/'+$taskId+'.png'),('review/'+$taskId+'-phone-r2.png'),('review/'+$taskId+'-text-bounds.json'),('review/'+$taskId+'-text-overlaps.json'),('assets/backgrounds/'+$taskId+'-medic-mouse-r2.png'),('assets/illustrations/medic-mouse-'+$taskVariant+'-r2.png'))
}
foreach($taskScript in @('workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','pack.ps1','layout-covers-r2.mjs','review-covers-r2.mjs','deliver-covers-r2.mjs','pack-covers-r2.ps1')){$taskPaths+=('source/'+$taskScript)}
foreach($taskPrompt in Get-ChildItem -LiteralPath (Join-Path $taskRoot 'source/prompts') -File){$taskPaths+=('source/prompts/'+$taskPrompt.Name)}
$taskSourceEntries=foreach($taskRelative in $taskPaths){[pscustomobject]@{source=(Join-Path $taskRoot $taskRelative);entry=$taskRelative}}
$taskSourceZip=Join-Path $taskRoot '你的护不行-蜂医灰鼠-三张封面-源稿-r2.zip'
Write-TaskZip $taskSourceZip $taskSourceEntries
$taskChecks=@()
foreach($taskZip in @($taskPngZip,$taskSourceZip)){
 $taskArchive=[IO.Compression.ZipFile]::OpenRead($taskZip)
 try{
  for($i=0;$i -lt 3;$i++){
   $taskEntryName=if($taskZip -eq $taskPngZip){$taskPngEntries[$i].entry}else{'output/png/'+$taskIds[$i]+'.png'}
   $taskEntry=$taskArchive.GetEntry($taskEntryName)
   if(-not $taskEntry){throw "缺少已核对封面：$taskEntryName"}
   $taskHash=[Security.Cryptography.SHA256]::Create();$taskEntryStream=$taskEntry.Open()
   try{$taskPackedHash=[BitConverter]::ToString($taskHash.ComputeHash($taskEntryStream)).Replace('-','')}finally{$taskEntryStream.Dispose();$taskHash.Dispose()}
   $taskLocalHash=(Get-FileHash -LiteralPath $taskPngEntries[$i].source).Hash
   if($taskPackedHash -ne $taskLocalHash){throw "包内图片与最终审核不一致：$taskEntryName"}
  }
  $taskChecks+=[pscustomobject]@{package=(Split-Path $taskZip -Leaf);entries=$taskArchive.Entries.Count;allThreeFinalPngHashesMatch=$true;bytes=(Get-Item -LiteralPath $taskZip).Length}
 }finally{$taskArchive.Dispose()}
}
$taskCheckPath=Join-Path $taskRoot 'review/package-verification-r2.json'
[IO.File]::WriteAllText($taskCheckPath,($taskChecks | ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
$taskChecks | ConvertTo-Json -Depth 5
