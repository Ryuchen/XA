$ErrorActionPreference='Stop'
$taskLibrary='C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库'
$taskMarker='## 2026-10-01 · 三版蜂医灰鼠插画通过，封面底图待确认'
$taskNote=@'

## 2026-10-01 · 三版蜂医灰鼠插画通过，封面底图待确认

- 用户明确回复“通过，继续，做封面”，三张新情景插画已获确认；本阶段先推进狗牌版、枪械版、左轮版三张封面，三张广告仍保留在后续范围。
- 已用内置 ImageGen 完成三张约 8:5 的无字封面底图，原生均为 1586×992。采用左文右图、蜂医与灰鼠互动、手绘营地/仓库/塔楼/直升机与边角物资，三版以身份牌箱、满改枪箱、左轮箱区分。
- 枪械首稿过宽，已通过 ImageGen 重排为系列比例。三图完整图、角色两手结构、手机预览与净文字区已检查，底板坐标已根据实图重测；排字需使用本轮坐标，不能直接套用旧稿。
- 新底图当前待确认，尚未矢量填字，不把旧 output 或 campaign-r1.zip 当作这轮完成。下一步底图确认后制作三张最终封面，再推进广告。封面不加入下单须知或结算例外。
- 价格、保底、计分/枪数等仍沿用已确认数据，端手游同价同规则；content.json 未修改。
- 文件位于项目“你的护不行-三版封面广告-20261001/”：assets/backgrounds/*-cover-medic-mouse-r2.png；查看页 review/stage2-covers-r2.html；坐标 review/cover-background-stage-r2.json；检查记录 review/cover-background-qa-r2.md。生成提示词及修正提示词保存在 source/prompts/。大型源文件仅在项目目录。
- 原因：落实用户对插画的确认并推进封面阶段。影响三版封面视觉与生产状态，广告稍后继续，业务数据不变，未外部发布。
- [三版封面底图查看页](<C:/Users/chenh/Documents/ChatGPT/兴安电竞玩法/你的护不行-三版封面广告-20261001/review/stage2-covers-r2.html>)。
'@
foreach($taskRelative in @('00_入口\当前共识.md','01_玩法\你的护不行.md','01_玩法\你的护不行-左轮版.md','05_变更日志\变更日志.md')){
 $taskPath=Join-Path $taskLibrary $taskRelative
 $taskExisting=Get-Content -LiteralPath $taskPath -Raw -Encoding utf8
 if(-not $taskExisting.Contains($taskMarker)){Add-Content -LiteralPath $taskPath -Value $taskNote -Encoding utf8}
}
$taskDecisionPath=Join-Path $taskLibrary '04_决策记录\2026-10-01-你的护不行三版统一蜂医灰鼠.md'
$taskDecision=Get-Content -LiteralPath $taskDecisionPath -Raw -Encoding utf8
$taskDecision=$taskDecision.Replace('status: direction-confirmed-artwork-pending','status: illustration-approved-cover-background-pending').Replace('- [ ] 用户确认三张新插画。','- [x] 用户于2026-10-01回复“通过，继续，做封面”，确认三张新插画。').Replace('具体新插画仍待分阶段确认。','三张新插画已于2026-10-01获用户确认；当前三张封面底图待确认，广告后续继续。')
Set-Content -LiteralPath $taskDecisionPath -Value $taskDecision -Encoding utf8
$taskProject=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
& powershell -ExecutionPolicy Bypass -File (Join-Path $taskProject 'sync-obsidian-memory.ps1')
if($LASTEXITCODE -ne 0){throw '素材索引同步失败'}
Write-Output '插画确认与三张封面底图阶段已同步至 Obsidian。'
