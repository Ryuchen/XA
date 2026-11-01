$ErrorActionPreference = 'Stop'
$taskStaging = Split-Path -Parent $PSScriptRoot
$taskLibrary = 'C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法'
$taskDestination = Join-Path $taskLibrary '你的护不行-三版封面广告-20261003'
$taskVault = 'C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库'
if(Test-Path -LiteralPath $taskDestination) {throw 'Destination already exists; inspect before writing.'}
$taskFiles = Get-Content -LiteralPath (Join-Path $taskStaging 'output\delivery-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($taskFile in @($taskFiles)+@('output/delivery-files.json','output/png-sha256.json','你的护不行-三版封面广告-源稿-r3.zip','你的护不行-三版封面广告-PNG-r3.zip')) {
  $taskTarget = Join-Path $taskDestination $taskFile
  New-Item -ItemType Directory -Path (Split-Path -Parent $taskTarget) -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $taskStaging $taskFile) -Destination $taskTarget
}
$taskNote = @'

## 2026-10-03 · 你的护不行三版封面艺术字调整与广告交付 r3

- 用户要求重新生成狗牌、枪械、左轮三版封面和广告，调整原封面艺术字大小；本轮六张全部完成，端游/手游同价。
- 复用已确认蜂医灰鼠封面底图和独立插画。三张封面主标题统一含装饰宽610.40（1600宽布局），狗牌/左轮比r2增宽24.9%，枪械增宽35.9%，全部按所属文字区居中。三张广告同步改用蜂医灰鼠，维持双难度21档价目。
- 21档售价、保底、基础分/枪数和各档枪名未改；源数据补存已确认左轮机密r6无缴获时计一把自带左轮的条件例外。封面无须知或结算例外。
- 最终六张实际PNG、390px预览、标题/价格局部复查通过；无文字相交或面板越界，标题上下左右留白通过。封面3200×2000，广告3000×4000，另有可改文字SVG与转曲SVG。底图原生1586×992、独立插画1536×1024，输出放大不代表原生绘制尺寸。
- 新成品目录：C:/Users/chenh/Documents/ChatGPT/兴安电竞玩法/你的护不行-三版封面广告-20261003/。查看review/index.html；包名“你的护不行-三版封面广告-PNG-r3.zip”和“你的护不行-三版封面广告-源稿-r3.zip”。旧20261001项目及旧包保留。
- 原因：统一系列标题尺度、补齐采用已确认角色的三张广告。影响仅本轮宣传物料与源稿记录，未外部发布。
'@
foreach($taskRelative in @('01_玩法\你的护不行.md','01_玩法\你的护不行-左轮版.md','00_入口\当前共识.md','05_变更日志\变更日志.md')) {
  $taskNotePath = Join-Path $taskVault $taskRelative
  if(-not (Test-Path -LiteralPath $taskNotePath)) {throw "Missing memory file: $taskRelative"}
  if(-not ([IO.File]::ReadAllText($taskNotePath).Contains('2026-10-03 · 你的护不行三版封面艺术字调整与广告交付 r3'))) {
    [IO.File]::AppendAllText($taskNotePath,"`n"+$taskNote+"`n",[Text.UTF8Encoding]::new($false))
  }
}
& (Join-Path $taskLibrary 'sync-obsidian-memory.ps1') -VaultPath 'C:\Users\chenh\Documents\Obsidian Vault'
foreach($taskRoot in @($taskStaging,$taskDestination)) {
  $taskStatePath=Join-Path $taskRoot 'campaign-state.json'
  $taskState=Get-Content -LiteralPath $taskStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
  $taskState.memorySync=[pscustomobject]@{status='completed';date='2026-10-03';scope='对应玩法卡、当前共识、变更日志和素材索引'}
  [IO.File]::WriteAllText($taskStatePath,($taskState | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false))
}
& (Join-Path $taskStaging 'source\pack-r3.ps1') -Project $taskStaging
Copy-Item -LiteralPath (Join-Path $taskStaging '你的护不行-三版封面广告-源稿-r3.zip') -Destination $taskDestination -Force
$taskManifest=Get-Content -LiteralPath (Join-Path $taskStaging 'output\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($taskPage in $taskManifest) {
  $taskFrom=(Get-FileHash -LiteralPath (Join-Path $taskStaging $taskPage.png) -Algorithm SHA256).Hash
  $taskTo=(Get-FileHash -LiteralPath (Join-Path $taskDestination $taskPage.png) -Algorithm SHA256).Hash
  if($taskFrom -ne $taskTo){throw 'Archived PNG hash mismatch'}
}
Write-Output "Saved and verified: $taskDestination"
