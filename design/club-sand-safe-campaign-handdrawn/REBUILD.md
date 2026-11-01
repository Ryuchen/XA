# 兴安电竞 · 沙保连连看

当前成品共四张：端游详细价格海报、手游详细价格海报、合并端游/手游的广告页、共用横版封面。详细海报保留全部下单须知；广告页按「完成目标／端游／手游」三列展示十档价格与奖励；封面按技能标准及历史参考完整重绘为16:9横版，左侧品牌、大标题、累计/连续挑战、平台/模式与许愿奖励，右侧蜂医、灰鼠开箱场景。广告与封面均不放下单须知。底图为巴克什黄沙的暖沙金色，保留沙色保险和三幻神Q版点缀。

最新须知排版：标题、四项正文及续行统一左对齐于原生x72；普通说明31字号、关键条件真实粗体，多行组在各自分区内垂直居中。广告两组价格数字/r列分别对齐。新版封面各组文字按左侧实际净文字区横向居中，主标题为单行矢量艺术字，普通说明62原生字号、390px宽等效14.46px；奖励数字与w分别定位。

## 文件

- `output/png/desktop-absolute.png`、`output/png/mobile-absolute.png`：两张3200×7200高清成品。
- `output/png/combined-advert.png`：一张合并广告页，3:4，2400×3200。
- `output/png/combined-cover.png`：一张完全重绘的共用横版封面，16:9，3200×1800。
- `output/svg-editable/desktop-absolute.svg`、`output/svg-editable/mobile-absolute.svg`：正文、价格和规则为可改文字，艺术标题为轮廓，插画为内嵌位图。
- `output/svg-outlined/desktop-absolute.svg`、`output/svg-outlined/mobile-absolute.svg`：全部文字已转曲，跨设备保留文字外观。
- 封面与广告同名的可改文字SVG、转曲SVG保存在上述两个SVG目录，全套共八个SVG。
- `content.json`：价格、单位、奖励和规则的唯一业务数据源，含本轮原始来源及展示文案。
- `layout-adapter.mjs`：当前海报的布局生成器，普通说明常规字重、关键条件真实粗体；价格与r单位分别定位。
- `02-layouts/final-background-measurements.json`：本轮丰富底图后的实际文字安全区。
- `03-backgrounds/bakhsh-sand-edit-prompt-v3.md`：内置image_gen本轮黄沙底图编辑完整提示词；v2点缀提示词也保留。
- `03-backgrounds/poster-bakhsh-sand-v3.png`：当前原生无字底图，已同步到`assets/backgrounds`。
- `03-backgrounds/cover-standard-horizontal-v2.png`：当前封面底图，原生1672×941；提示词`cover-standard-redraw-prompt-v2.md`。内置image_gen参考技能标准无字封面，完整重绘暖黄沙开箱场景，未沿用旧竖版面板结构。
- `references/standard-cover-references.json`：标准封面和历史物资/限时清图封面的参考记录；只参考美术和构图，历史价目、优惠与例外不沿用。
- `03-backgrounds/advert-bakhsh-v2.png`：当前广告底图，原生1086×1448；对应提示词同目录保存。广告v2扩大目标区域后重新实测，不沿用初稿坐标。
- `review/index.html`：查看及下载页。
- `review/suite-preview.png`：整套四张预览，上方新版横版封面，下方端游价目、手游价目和合并广告页。
- `review/visual-qa.md`：完整成图、手机预览、局部检查记录。

详细海报底图原生836×1881，文字按3200×7200由矢量渲染；广告文字按2400×3200渲染，横版封面按3200×1800渲染。放大底图不代表原生4K插画。端游价格218/428/628/588/988r，手游价格168/328/488/488/888r。单平台详细海报的物资目标一行显示「单局物资保底 >888w」或「单局物资保底 >688w」，不再重复平台；合并广告一行标明「保底 端游 >888w · 手游 >688w」，保持金额与平台对应。同局成功撤离且三项目标同时达成的前提保留。三幻神道具只作装饰；许愿范围仍严格为万金泪冠和纵横二选一。新版横版封面只保留玩法、平台及条件奖励，不放价格、三项详细目标、下单须知或结算例外；这些业务数据完整保留在源数据、两张价目海报和合并广告中。封面文案「下单时许愿（二选一）」「万金泪冠 / 纵横」「出了送1688w保底」保持奖励触发条件。

## 后续改价或改文案

先编辑`content.json`。价格改`prices`；正文和显示方式改相应业务字段及`display`，须同步保留原规则的前提、比较符和例外。修改版式时改项目的`layout-adapter.mjs`或安全区文件。

使用本机Node.js与Sharp；系统字体为Microsoft YaHei UI、Bahnschrift、Noto Sans SC Black。运行时路径和模块目录由workspace dependency loader提供，不依赖原项目盘符。

```text
node source/workflow.mjs layout --project <项目绝对路径>
node source/workflow.mjs render --project <项目绝对路径> --modules <Node包目录>
node source/review-final.mjs <项目绝对路径> <Node包目录>
```

只复查新版封面并保留其他页预览时，在核对程序末尾加`--cover-only`。更新封面底图必须重新测量文字区，不能将旧竖版坐标套入横版图。

检查实际成图和390px手机预览后，再用不同revision打包：

```text
node source/workflow.mjs package --project <项目绝对路径> --modules <Node包目录> --revision <新版本名>
```

本轮`source/review-final.mjs`是针对用户当前资料的核对程序；以后价格或规则改变时应同步更新其来源对照，不能沿用旧数字作为新版本断言。
