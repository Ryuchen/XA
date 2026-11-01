$ErrorActionPreference='Stop'
$taskLibrary='C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库'
$taskMarker='## 2026-10-01 · 蜂医灰鼠三张封面填字交付 r2'
$taskNote=@'

## 2026-10-01 · 蜂医灰鼠三张封面填字交付 r2

- 用户在三张新封面无字底图展示后回复“填入文字”，底图确认通过。本轮完成狗牌版、枪械版、左轮版三张蜂医灰鼠封面；三张广告仍在后续范围。
- 已填矢量艺术标题“你的护不行”、兴安电竞品牌、版本名、两行核心卖点、“端游 / 手游同价”及“绝密 / 机密”。封面不加须知或结算例外，价格与全部规则源数据未修改。
- 三张 PNG 均3200×2000；场景底图原生1586×992。另交正文可改文字SVG和转曲SVG；艺术标题为矢量轮廓，其文字与样式参数保存在layout.json。未称整张全矢量。
- 逐张整图、390px手机图、原尺寸文字局部复查通过；枪械版窄文字区微调字号后复查。文字在实际安全区内，各组居中，无图文主体遮挡。旧六图留archive/r1和原r1包，本轮包仅三张新封面。
- 文件在“你的护不行-三版封面广告-20261001/”；查看review/covers-r2.html，成品output/png/*-cover.png，PNG包“你的护不行-蜂医灰鼠-三张封面-PNG-r2.zip”，源稿包“你的护不行-蜂医灰鼠-三张封面-源稿-r2.zip”。复检review/visual-qa.md与covers-layout-audit-r2.json。
- 原因：完成已确认封面的矢量填字和交付。影响三张封面及其源稿，不改业务规则、未外部发布，广告继续保留待制作状态。
'@
foreach($taskRelative in @('00_入口\当前共识.md','01_玩法\你的护不行.md','01_玩法\你的护不行-左轮版.md','05_变更日志\变更日志.md')){
 $taskPath=Join-Path $taskLibrary $taskRelative
 $taskExisting=Get-Content -LiteralPath $taskPath -Raw -Encoding utf8
 if(-not $taskExisting.Contains($taskMarker)){Add-Content -LiteralPath $taskPath -Value $taskNote -Encoding utf8}
}
$taskDecisionPath=Join-Path $taskLibrary '04_决策记录\2026-10-01-你的护不行三版统一蜂医灰鼠.md'
$taskDecision=Get-Content -LiteralPath $taskDecisionPath -Raw -Encoding utf8
$taskDecision=$taskDecision.Replace('status: illustration-approved-cover-background-pending','status: covers-delivered-ads-pending').Replace('当前三张封面底图待确认，广告后续继续。','三张封面底图已通过，并于2026-10-01完成矢量填字交付；广告后续继续。').Replace('- [ ] 六张无字底图生成、复查与用户确认。',"- [x] 三张封面底图生成、复查与用户确认。`r`n- [ ] 三张广告底图生成、复查与用户确认。").Replace('- [ ] 六张最终PNG及矢量源稿排版复检与交付。',"- [x] 三张最终封面PNG及矢量源稿复检与交付。`r`n- [ ] 三张最终广告PNG及矢量源稿复检与交付。")
Set-Content -LiteralPath $taskDecisionPath -Value $taskDecision -Encoding utf8
$taskProject=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
& powershell -ExecutionPolicy Bypass -File (Join-Path $taskProject 'sync-obsidian-memory.ps1')
if($LASTEXITCODE -ne 0){throw '素材索引同步失败'}
Write-Output '三张封面交付与广告后续状态已记录。'
