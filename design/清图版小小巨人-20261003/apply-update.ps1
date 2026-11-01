param(
  [string]$Project = 'C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\清图版小小巨人-四版价目广告封面-20260930',
  [string]$Node = 'C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe',
  [string]$Modules = 'C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
)
$ErrorActionPreference = 'Stop'

function Save-Json([string]$Path, $Value) {
  [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
}
function Save-Text([string]$Path, [string]$Text) {
  [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}
function Replace-First([string]$Text, [string]$Old, [string]$New) {
  $i = $Text.IndexOf($Old, [StringComparison]::Ordinal)
  if ($i -lt 0) { throw "未找到待替换文本：$Old" }
  return $Text.Substring(0, $i) + $New + $Text.Substring($i + $Old.Length)
}

$contentPath = Join-Path $Project 'content.json'
$statePath = Join-Path $Project 'campaign-state.json'
$backup = Join-Path $Project 'archive\r4-before-r5'
if (-not (Test-Path -LiteralPath $backup)) {
  New-Item -ItemType Directory -Path $backup -Force | Out-Null
  foreach ($name in @('content.json','campaign-state.json','layout.json','layout-adapter.mjs')) {
    $file = Join-Path $Project $name
    if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination $backup }
  }
}
$content = Get-Content -LiteralPath $contentPath -Raw -Encoding UTF8 | ConvertFrom-Json
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json

$gain = @(1, 1, 1, 2)
foreach ($mode in $content.modes) {
  for ($i = 0; $i -lt $mode.tiers.Count; $i++) { $mode.tiers[$i].failureGain = $gain[$i] }
}
$loss = @(5, 6, 7, 8, 9, 11)
for ($i = 0; $i -lt $content.deductions.Count; $i++) { $content.deductions[$i].healthLoss = $loss[$i] }
$content.gameRules | Where-Object id -eq 'settle' | ForEach-Object { $_.text = '免费吃不扣血；炸单按体验/基础/进阶/豪华分别加1/1/1/2滴。' }
$content.sources | Where-Object description -like '用户确认：保留5分钟档*' | ForEach-Object { $_.description = '用户追加：保留5分钟档；六档限时清图扣血统一下调1滴。' }
$content.verification.deductionInterpretation = '本轮用户将六档限时清图扣血统一下调1滴，形成5/6/7/8/9/11；失败加血按体验/基础/进阶/豪华为1/1/1/2。'
Save-Json $contentPath $content

$state.revision = 'r5'
$state.contentVerificationBasis = '本轮2026-10-03用户追加：撤离失败加血改为1/1/1/2；六档限时清图扣血改为5/6/7/8/9/11。原有价格、清图判定、黑屋流局和双平台范围保持。'
for ($i = 0; $i -lt $state.decisions.Count; $i++) {
  if ($state.decisions[$i] -like '保留5分钟档*') { $state.decisions[$i] = '六档限时清图扣血改为5/6/7/8/9/11；撤离失败加血按体验/基础/进阶/豪华为1/1/1/2。' }
}
$state.finalVisualQa = [pscustomobject]@{ status = 'pending-manual-review'; revision = 'r5'; report = 'review/visual-qa.md'; basis = '业务数字已按2026-10-03用户指令更新并重新导出，需查看实际PNG和390px预览。' }
Save-Json $statePath $state

$qaPath = Join-Path $Project 'review\visual-qa.md'
$qa = @'
# 成图复检 · r5

本轮业务数据修订：撤离失败加血为体验/基础/进阶/豪华 `+1/+1/+1/+2`；25/21/17/13/9/5分钟扣血为 `5/6/7/8/9/11`。价格、清图判定、第8人击杀计时、黑屋局流局和端手游范围保持。

已由源数据重新生成布局并导出六张页面；请打开实际 PNG、390px 手机预览及扣血/血量局部，确认数值、baseline、区块垂直居中和无文字相交后，将本报告状态标为通过。
'@
Save-Text $qaPath $qa

$nodeSource = Join-Path $Project 'source\workflow.mjs'
& $Node $nodeSource layout --project $Project
& $Node $nodeSource package --project $Project --modules $Modules --revision r5

$card = 'C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库\01_玩法\清图版小小巨人.md'
if (Test-Path -LiteralPath $card) {
  if (-not (Test-Path -LiteralPath ($card + '.bak-20261003'))) { Copy-Item -LiteralPath $card -Destination ($card + '.bak-20261003') }
  $cardText = Get-Content -LiteralPath $card -Raw -Encoding UTF8
  foreach ($pair in @(
    @('|绝密|体验版|288r|238r|5滴|10滴|+2滴|','|绝密|体验版|288r|238r|5滴|10滴|+1滴|'),
    @('|绝密|基础版|488r|388r|10滴|20滴|+2滴|','|绝密|基础版|488r|388r|10滴|20滴|+1滴|'),
    @('|绝密|进阶版|888r|688r|20滴|40滴|+2滴|','|绝密|进阶版|888r|688r|20滴|40滴|+1滴|'),
    @('|绝密|豪华版|1688r|1288r|40滴|60滴|+3滴|','|绝密|豪华版|1688r|1288r|40滴|60滴|+2滴|'),
    @('|机密|体验版|188r|168r|5滴|10滴|+2滴|','|机密|体验版|188r|168r|5滴|10滴|+1滴|'),
    @('|机密|基础版|288r|238r|10滴|20滴|+2滴|','|机密|基础版|288r|238r|10滴|20滴|+1滴|'),
    @('|机密|进阶版|488r|388r|20滴|40滴|+2滴|','|机密|进阶版|488r|388r|20滴|40滴|+1滴|'),
    @('|机密|豪华版|1288r|1088r|40滴|60滴|+3滴|','|机密|豪华版|1288r|1088r|40滴|60滴|+2滴|'),
    @('|25分钟内|6滴|','|25分钟内|5滴|'), @('|21分钟内|7滴|','|21分钟内|6滴|'),
    @('|17分钟内|8滴|','|17分钟内|7滴|'), @('|13分钟内|9滴|','|13分钟内|8滴|'),
    @('|9分钟内|10滴|','|9分钟内|9滴|'), @('|5分钟内|12滴|','|5分钟内|11滴|'),
    @('免费吃不扣血；炸单按体验/基础/进阶/豪华分别加2/2/2/3滴。','免费吃不扣血；炸单按体验/基础/进阶/豪华分别加1/1/1/2滴。')
  )) { $cardText = Replace-First $cardText $pair[0] $pair[1] }
  $cardText += "`r`n`r`n## 2026-10-03 · 清图版小小巨人扣血调整`r`n`r`n- 撤离失败加血由体验/基础/进阶/豪华 `+2/+2/+2/+3` 调整为 `+1/+1/+1/+2`。`r`n- 25/21/17/13/9/5分钟扣血由6/7/8/9/10/12调整为5/6/7/8/9/11，每档下调1滴。`r`n- 价格、清图判定、第8人击杀计时、黑屋局流局和端手游范围保持不变。`r`n- 项目：$Project；新版本包：campaign-r5.zip。`r`n"
  Save-Text $card $cardText
}

$log = 'C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库\05_变更日志\变更日志.md'
if (Test-Path -LiteralPath $log) {
  if (-not (Test-Path -LiteralPath ($log + '.bak-20261003'))) { Copy-Item -LiteralPath $log -Destination ($log + '.bak-20261003') }
  $logText = Get-Content -LiteralPath $log -Raw -Encoding UTF8
  $logText += "`r`n`r`n## 2026-10-03 · 清图版小小巨人扣血调整`r`n`r`n- 撤离失败加血调整为1/1/1/2；限时清图扣血调整为5/6/7/8/9/11。`r`n- 影响范围：清图版小小巨人四张价目、广告、源数据与后续客服口径；常规版小小巨人与其他玩法不变。`r`n- 项目：$Project；成品包：campaign-r5.zip。`r`n"
  Save-Text $log $logText
}

Write-Output "已生成清图版小小巨人 r5：$Project"
