# 兴安电竞 · 累计撤离单

版本cumulative-r2，2026-10-09。7张：封面、两端广告页、四张价格表。查看review/index.html。

唯一业务数据源：content.json。手游新价格与保底取用户截图，端游保底按已确认算法计算；参考和计算明细见references及review/desktop-guarantee-calculations.json。

## 后续修改

1. 修改content.json中的对应价格、保底、规则。
2. 使用Node.js运行source/typeset.mjs；Sharp可由--modules=<包目录>提供。
3. 运行source/review.mjs，打开实际PNG和phone-390、details做视觉复检。
4. 完成目检后更新review/visual-qa.md，再运行source/finalize.mjs。

source/prepare.mjs仅为首次项目创建记录，已加防覆盖门禁；后续不要用它重置项目。source/update-desktop-guarantees.mjs按项目内的常规物资单快照重算端游保底，并检查不改手游。

可改文字SVG保留正文和数字文字对象，艺术字为路径；字体为Microsoft YaHei UI、Bahnschrift、Noto Sans SC Black。转曲SVG不依赖字体。插画为内嵌位图。

完整价目版面724×2075/2076（从原背景删除96px说明区），PNG3200×9171/9176；封面3200×1800；广告2400×3200。背景原生724×2171/2172、1672×941、1086×1448，未重新生成。