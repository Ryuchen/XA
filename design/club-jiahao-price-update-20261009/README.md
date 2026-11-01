# 兴安电竞 · 嘉豪物资单 · 2026-10-09 价目核对版

本轮交付三张：手游绝密、手游机密、广告页。查看 `review/index.html`，高清图在 `output/png`，矢量文件在 `output/svg-editable` 和 `output/svg-outlined`。

八档手游新价表与旧源稿已一致；本轮重新记录新价表为最新依据，重新输出并检查。广告端游两卡按本轮附图沿用。数值与规则仅由 `content.json` 提供，来源截图保存在 `references`，具体核对见 `review/visual-qa.md`。

后续修改 `content.json`，在Windows环境使用已安装Node与Sharp执行：

```powershell
node source/workflow.mjs layout --project <本项目绝对路径>
node source/workflow.mjs render --project <本项目绝对路径> --modules <Node包目录>
node source/verify-price-update.mjs <Node包目录>
node source/workflow.mjs package --project <本项目绝对路径> --modules <Node包目录> --revision <新版本号>
```

只导出点名的三页，适配器已设置准确的平台范围。字体为Microsoft YaHei UI、Bahnschrift、Noto Sans SC Black；跨设备保持外观使用转曲SVG。插画为内嵌位图。`source/prepare-price-update.mjs`仅记录本轮初始化过程，继续改稿不要再次执行。
