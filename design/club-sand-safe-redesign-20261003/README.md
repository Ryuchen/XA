# 兴安电竞 · 沙保连连看 · 第二步确认包

这是全新四张套图的无字底图与文字位置稿，正式成图尚未排字。

打开 review/backgrounds.html 查看四张底图、实际文字位置稿和下载链接。

本轮已确认数据：端游连续两次688r、三次1288r；手游连续两次588r、三次888r；手游带出物资严格＞788w，端游严格＞888w。其他累计价目和规则以content.json为准。

三项缺一不可：同一局摸2个沙色保险、摸4个常规大保险、带出物资门槛都满足，并成功撤离才算达标。

插画已由用户确认。当前等待四张新无字底图确认；确认后才进入矢量填字和正式成图验收。

原生与输出尺寸写在campaign-state.json和02-layouts/generated-background-measurements.json。04-png-export是放大的无字PNG，未声称为原生4K。

生成使用内置image_gen；03-backgrounds保存本轮生成和局部校正提示词，01-illustrations保存本轮已确认的透明插画。references中的道具图片只作原始造型参考，没有复用旧成品海报。

source程序可在Node.js和Sharp可用的Windows环境重建位置稿：

    node source/prepare-background-review.mjs <项目绝对路径> <Node包目录>

省略项目路径时使用程序所在项目目录；省略包目录时从当前Node运行时旁的node_modules解析。字体轮廓脚本在source/outline-guide.ps1，需本机已有Microsoft YaHei UI字体。