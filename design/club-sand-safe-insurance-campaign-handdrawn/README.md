# 兴安电竞 · 沙色保险单

阶段：正式成品已完成，版本sand-safe-insurance-v1。用户已确认插画与底图；本次完成矢量文案与最终PNG视觉复查。

- 查看页：review/backgrounds.html
- 底图总览：review/background-overview.png
- 文字位置总览：review/layout-overview.png
- 两端完整价目底图：04-png-export/desktop-absolute-background.png、mobile-absolute-background.png（3200×4800）
- 封面底图：04-png-export/cover-background.png（3200×1800）
- 双平台宣发底图：04-png-export/social-background.png（2400×3200）
- 净文字区及r/w轴：02-layouts/generated-background-measurements.json
- 确认业务源：content.json、references/rules-confirmation.md
- 图片原生尺寸与生成来源：03-backgrounds/image-metadata.json
- 原始与修正版底图均保留。目标PNG为放大导出；正式文字将在确认后以矢量渲染。

使用 built-in image_gen 生成与编辑底图；提示词见03-backgrounds/prompt-*.md、reward-divider-edit-prompts.md。第三阶段未重新生成美术。两张完整价目共用美术，分别填端游168/388/628r与手游128/288/488r。

## 正式成品

- 查看及逐张下载：review/index.html；总览review/final-overview.png。
- 4张高清PNG：output/png/desktop-absolute.png、mobile-absolute.png（3200×4800），cover.png（3200×1800），social.png（2400×3200）。
- 可改文字SVG：output/svg-editable/；正文和数字是文字对象，艺术标题为轮廓。需安装Microsoft YaHei UI与Bahnschrift以保持编辑后字体一致。
- 转曲SVG：output/svg-outlined/；正文、数字和标题均为矢量路径，不依赖字体。背景插画为内嵌位图。
- PNG包：output/campaign-sand-safe-insurance-v1-png.zip。
- 成品与矢量源文件包：output/campaign-sand-safe-insurance-v1-source.zip。
- 业务源content.json；实际布局layout.json，续作适配器layout-adapter.mjs；字体轮廓缓存.cache/all-text-outlines.json。

后续改价或改字，使用workspace dependency loader提供的Node执行source/typeset.mjs，支持--only=desktop-absolute,social；普通修改复用底图、字体轮廓与未变化页面。源数据或文案变化后，须查看真正变动的成图；source/review-output.mjs创建准确390px预览、局部图和源数据辅助记录，不代替目检。
