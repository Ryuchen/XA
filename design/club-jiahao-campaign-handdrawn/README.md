# 兴安电竞 · 嘉豪物资单

成品位于 output/png；矢量版位于 output/svg-editable 与 output/svg-outlined；打开review/index.html查看全部六张。

## 后续改价

使用本机Node及Sharp运行（Windows字体轮廓工具）：

```powershell
node source/workflow.mjs layout --project <项目绝对路径>
node source/workflow.mjs render --project <项目绝对路径>
node source/build-overview.mjs
node source/workflow.mjs package --project <项目绝对路径> --revision jiahao-final-r2
```

唯一业务数据源content.json；布局源layout-adapter.mjs。只导出部分页面可传--only desktop-absolute,social。缓存不随成品包交付，再次渲染会从本机字体建立缓存。正文使用Microsoft YaHei UI，数字Bahnschrift，艺术字Noto Sans SC Black。转曲SVG无需字体。

插画和底图已由用户确认；价格、保障、清图方式、须知端手游一致的口径已确认。见campaign-state.json。
