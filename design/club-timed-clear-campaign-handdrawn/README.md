# 兴安电竞 · 限时清图单

六张成品已完成：四张六档价目、横版封面、简洁宣发海报。打开review/index.html逐张查看与下载。

- output/png：高清PNG
- output/svg-editable：正文及数字可改文字SVG，艺术标题保留路径
- output/svg-outlined：全部文字转曲SVG，无字体依赖
- review/final-overview.png：成品总览
- review/final-notes.md：业务口径、排版和分辨率说明
- review/alignment-record.json：单位列、字高与边距记录
- review/placement-record.json：标题、正文组与实际底图留白的定位记录
- content.json：本轮24档价目与规则的唯一业务数据源
- layout-adapter.mjs：六页矢量布局

## 修改与重建

使用本机Node及Sharp，从项目根目录运行（字体轮廓工具依赖Windows）：

```powershell
node source/workflow.mjs layout --project <项目绝对路径>
node source/workflow.mjs render --project <项目绝对路径>
node source/measure-placement.mjs
node source/build-overview.mjs
node source/workflow.mjs package --project <项目绝对路径> --revision timed-clear-final-r2
```

仅修改部分页面时可传--only desktop-absolute,social；价格变更应同步宣发。渲染器自动复用未修改页面及字体缓存。字体为Microsoft YaHei UI、Bahnschrift、Noto Sans SC Black，转曲版不依赖字体。缓存不随成品包交付，重新渲染将由本机字体生成。

## 确认记录

插画与无字底图均已获用户“继续”确认，依据见campaign-state.json。阶段查看页仅保留在原项目review/backgrounds.html、review/illustration.html；成品包以review/index.html查看最终稿。机密规则原文见references/secret-rules-user.md。正式文案与艺术字在本轮填入，无需重复确认已采用的底图。

背景为位图，文字为矢量层；PNG按目标尺寸渲染。原生图和生成提示词保留在03-backgrounds，assets/backgrounds为渲染副本。
