# 你的护不行 · 三版封面广告 r3

六张宣传图已完成。封面艺术字统一放大居中，广告统一蜂医灰鼠插画。价目沿用原21档。

- review/index.html：查看和下载。
- output/png：封面3200×2000、广告3000×4000。
- output/svg-editable：正文与数字可编辑，标题为轮廓。
- output/svg-outlined：所有文本转为路径，不依赖字体。
- content.json：业务源，包含左轮机密r6条件例外。
- layout-adapter.mjs及layout-social-base.mjs：可复用排版源。
- review/visual-qa.md：实际成图复查记录。

原封面底图1586×992，插画1536×1024。背景放大不代表原生超高清绘制。

## 重建

通过workspace dependency loader获取Node与Node包目录，然后使用source/workflow.mjs layout --project <绝对项目路径>及render --project <绝对项目路径> --modules <Node包目录>。改字号后重新运行source/review.mjs <Node包目录>并目视检查实际PNG。转曲依赖Windows字体Microsoft YaHei UI、Bahnschrift及Noto Sans SC Black。
