$ErrorActionPreference='Stop'
$taskLibrary='C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库'
$taskMarker='## 2026-10-01 · 你的护不行三版改用蜂医灰鼠（插画待确认）'
$taskNote=@'

## 2026-10-01 · 你的护不行三版改用蜂医灰鼠（插画待确认）

- 用户明确要求三张封面和三张广告按xingan-gameplay-posters技能中的蜂医与灰鼠重新生成。当前视觉方向改为男性蜂医+得吃灰鼠，覆盖上一版盾牌/双人干员作为主角的选择；旧稿保留历史。
- 价格、保底、计分/枪数、枪型清单与端游手游同价同规则均沿用本轮已确认内容；只重做这六张宣传图，不增加完整价目页。
- 第一阶段已完成三张内置ImageGen独立插画：狗牌版展示身份牌与价值扫描器；枪械版收集满改枪械与枪箱；左轮版整理高价值左轮与收纳箱。统一浅棕发、护目镜男性蜂医和战术灰鼠，二次元手绘奶白雾蓝、海军蓝、金/薄荷点缀。
- 三图原生1536×1024，保留真实alpha。左轮初稿灰鼠多出一只手，已通过ImageGen局部编辑删除并复查，最终两手分别指箱和持标签。奶白预览与角色结构已检查。
- 当前状态为新情景插画待用户确认，尚未制作新无字底图或六张蜂医灰鼠最终成品；不能将旧output或campaign-r1.zip记作本轮完成。后续按插画确认→无字底图确认→矢量填字推进。
- 文件仍在项目“你的护不行-三版封面广告-20261001/”；插画assets/illustrations/medic-mouse-{dogtag,firearms,revolver}-r2.png，查看页review/stage1-r2.html，提示词source/prompts/medic-mouse-r2.json，审核review/illustration-qa-r2.md。大型文件不复制入Obsidian。
- 原因：落实用户指定的技能角色与系列手绘风格。影响仅三版封面/广告美术和当前制作阶段；不改任何业务规则，未外部发布。
- [插画查看页](<C:/Users/chenh/Documents/ChatGPT/兴安电竞玩法/你的护不行-三版封面广告-20261001/review/stage1-r2.html>)；[[../04_决策记录/2026-10-01-你的护不行三版统一蜂医灰鼠]]。
'@
foreach($taskRelative in @('00_入口\当前共识.md','01_玩法\你的护不行.md','01_玩法\你的护不行-左轮版.md','05_变更日志\变更日志.md')){
 $taskPath=Join-Path $taskLibrary $taskRelative
 $taskExisting=Get-Content -LiteralPath $taskPath -Raw -Encoding utf8
 if(-not $taskExisting.Contains($taskMarker)){Add-Content -LiteralPath $taskPath -Value $taskNote -Encoding utf8}
}
$taskDecisionPath=Join-Path $taskLibrary '04_决策记录\2026-10-01-你的护不行三版统一蜂医灰鼠.md'
if(-not (Test-Path -LiteralPath $taskDecisionPath)){
 Copy-Item -LiteralPath (Join-Path $taskLibrary '99_模板\设计决策模板.md') -Destination $taskDecisionPath
 $taskDecision=@'
---
type: decision
project: 兴安电竞玩法
status: direction-confirmed-artwork-pending
date: 2026-10-01
tags: [设计决策, 蜂医, 灰鼠, 你的护不行]
---
# 决策：你的护不行三版统一蜂医灰鼠

## 背景

首版六图复用了狗牌/左轮的盾牌及枪械双人干员。用户明确要求三版封面和广告按海报技能里的蜂医与灰鼠重新生成。

## 选择

沿用技能已有男性蜂医与得吃灰鼠角色身份，分别新绘狗牌、满改枪械、左轮三个情景，再制作对应封面与广告底图。角色方向已获用户明确指定，具体新插画仍待分阶段确认。

## 备选方案

- 复用原盾牌及双人干员：不符合用户最新要求，保留历史但不继续作为本轮主视觉。
- 同一泛用开箱图用于全部版本：不够明确表达三种玩法差异，采用同角色三个情景。

## 理由

统一品牌角色与手绘美术，同时用主题道具区分狗牌积分、满改收集、左轮价值计分。依用户调用的技能遵循插画确认、底图确认、矢量填字的阶段流程。

## 影响范围

- 玩法：你的护不行狗牌版、枪械版、左轮版。
- 定价/权益：保持全部现行价目与端手游同价同规则。
- 视觉/物料：3封面+3广告；不新增完整价目海报。
- 客服口径：不修改业务术语或结单条件。

## 后续验证

- [x] 三张插画原生尺寸、alpha、角色一致性与手部结构已复查。
- [ ] 用户确认三张新插画。
- [ ] 六张无字底图生成、复查与用户确认。
- [ ] 六张最终PNG及矢量源稿排版复检与交付。
'@
 Set-Content -LiteralPath $taskDecisionPath -Value $taskDecision -Encoding utf8
}
$taskProject=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
& powershell -ExecutionPolicy Bypass -File (Join-Path $taskProject 'sync-obsidian-memory.ps1')
if($LASTEXITCODE -ne 0){throw '素材索引同步失败'}
Write-Output '蜂医灰鼠重绘方向与插画待确认阶段已记录。'
