# 兴安电竞 · 连续撤离单

## 当前阶段

最终版本：streak-r3-route。七张成品已完成矢量文字填充、字号与上下留白修订，以及撤离点规则修正。

## 本阶段文件

- output/png：七张最终PNG。完整价目4张，3200×9600；横版封面3200×1800；端游/手游宣发各2400×3200。
- output/svg-editable：可改正文与价目文字对象的SVG；艺术标题为路径。
- output/svg-outlined：所有正式文案转曲，不依赖系统字体；插画仍为内嵌位图。
- output/campaign-streak-r3-png.zip：七张高清PNG。
- output/campaign-streak-r3-source.zip：成品、矢量SVG、底图、数据、制作程序和检查记录。
- review/index.html：可浏览成品、SVG与390px预览的查看页。
- review/final-overview.png：成品总览。
- 02-layouts：生成前结构稿，以及生成后重新测量的安全区与说明稿。坐标以实际生成图为准。
- 03-backgrounds：四种已选无字原图、提示词与生成来源；gold-v1-draft为保留的未选草稿。
- 01-illustrations：已确认的男性蜂医与灰鼠主题插画，透明PNG。
- content.json：本轮36档和已确认业务规则的唯一文字数据源。
- campaign-state.json：阶段确认与最终版本记录。

端游和手游共用每个模式的版式，各自使用本轮新价格。每张完整价目有三地图×三档；宣发按平台分两张，每张绝密/机密共18档，不放完整下单须知。历史04-png-export、review/backgrounds.html属于无字底图阶段，不能代替正式成品。

## 制作约束

1. 二次元手绘风格；绝密暖金、机密薄荷青，价格表头整条深蓝。
2. 连撤对应次数与打满基础保底必须同时满足。
3. 数字向独立r/w单位列右对齐；单位可见高度约数字65–75%；每个文字组按可见字形垂直居中。
4. 房卡、缺项补偿采用加高文字卡；正文真实粗体。用户要求缩小文字并保留上下间距，已调整七张成品：完整价目主数字43→35，宣发数字52→44，须知正文约26/27→22.5/23；重新测量可见字形并整组居中。
5. 封面文字在左侧所属文字区沿同一中轴居中；人物和路线位于右侧。
6. 生成底图原生尺寸：绝密724×2171、机密724×2172、封面1672×941、宣发1086×1448。按目标尺寸直接渲染矢量文字并放大背景，未宣称底图原生3200px。原图尺寸与来源见03-backgrounds/image-metadata.json。
7. 最新撤离点规则：默认允许航天飞升、巴克什飞鼠；禁止对应撤离点，对应单价上浮25%。四张完整价目已同步，基础价格不提前上浮；确认原文见references/rules-confirmation.md。

## 检查与复用

逐张实际成图、390px手机预览与须知局部的检查记录见review/visual-qa.md；字形边距、r/w对齐与数值核对见review/typesetting-audit.json。

后续修改content.json或项目layout-adapter.mjs，使用依赖加载工具提供的Node运行时：

```text
node source/typeset.mjs --only=desktop-absolute,desktop-secret,mobile-absolute,mobile-secret
node source/review-output.mjs
```

检查实际更新PNG、390px预览和重点局部后，再更新状态与检查记录并打包。source/package-final.mjs只打包完成目检的文件，不自动改字或重渲染。不要对现有成品重新运行初始化或历史底图阶段打包程序。
