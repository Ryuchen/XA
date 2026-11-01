# 兴安电竞 · 物资对赌单

共六张成品：四张9档价目、横版封面、36档简洁宣发图。打开review/index.html逐张查看和下载。

- output/png：高清PNG
- output/svg-editable：可改正文和数字的SVG，艺术字为路径
- output/svg-outlined：全部文字转曲的SVG，跨设备无字体依赖
- review/final-overview.png：成品总览
- review/final-notes.md：内容口径、尺寸和排版说明
- review/alignment-record.json：r/w对齐、可见字高及边距记录

## 后续修改

使用本机Node和Sharp运行，字体轮廓工具依赖Windows。运行命令时从项目根目录开始：

```powershell
node source/workflow.mjs layout --project <项目绝对路径>
node source/workflow.mjs render --project <项目绝对路径>
node source/build-overview.mjs
node source/workflow.mjs package --project <项目绝对路径> --revision challenge-final-r2
```

唯一业务数据源content.json；布局源layout-adapter.mjs。只导出部分页面可传--only desktop-absolute,social。改价也应更新宣发图；缓存会跳过未修改页面。正文Microsoft YaHei UI，数字Bahnschrift，艺术字Noto Sans SC Black。字体缓存不随成品包交付，重渲染时由本机字体重新建立。

插画、底图及玩法口径已确认，依据见campaign-state.json。插画与背景为内嵌位图；文字为矢量层，最终PNG按目标尺寸渲染。
