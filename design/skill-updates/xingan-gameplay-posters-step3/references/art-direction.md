# 美术与分阶段确认

只在需要新插画、换画风或重新布置底图时读取本文件。只改价和文案时跳过图像生成。

## 默认美术

二次元手绘 Q 版：自然变化的深蓝线稿，明亮平涂，一层柔和赛璐璐阴影，轻微手绘腮红。奶白底、雾蓝环境、海军蓝服装；暖金与薄荷青作为适量模式色。建筑和载具也要手绘化，不能人物是动漫而背景是写实金属渲染。

本例：可爱男性蜂医、开心的灰鼠、开箱得吃与满载撤离。使用技能内 `illustrations/01-medic-mouse-loot.png`、`02-loaded-extraction.png` 和 `03-tactical-sticker-atlas.png`。贴纸图集选取单个元素，不能整张贴进海报。

## 生成流程

1. 先读用户本轮玩法的语义与卖点，再选择对应情景。没有必要的玩法关联时不要强加某种道具。
2. 新人物/情景先生成独立插画，可抠图资产用透明背景；用户确认后用于底图。
3. 先明确布局：画幅、扣除插画后的标题/正文安全区、表格行数、优惠位、规则组数。封面为横向居中的文案组预留空间；完整价目按实际内容量为常规说明、重点强调与必要图示分配足够宽高和上下留白，避免频繁换行或正文挤小而页尾闲空。用简单结构稿锁定区域，再生成无字底图。
4. 底图确认后进入技能第三步，先理解文案、确认实质疑点，再组织信息与填入矢量文字；只加粗重点，展示方式与手绘底图协调。图像模型生成的面板位置可能漂移，以生成结果实测为准。

当用户已授权沿用资产或进入后续阶段，不重新索取已给出的确认；只就缺失的决定提问。生成工具不可用时保留现有材料并如实说明，不悄悄换成收费 API 或重新安装工具。

## 可复用提示词骨架

### 插画

```text
Use case: illustration-story.
Asset: 兴安电竞 [玩法名称] poster illustration, reusable transparent PNG.
Scene: [与本轮玩法相关的单一情景].
Characters: [已确认角色设计、表情、动作].
Style: clean Japanese anime chibi hand drawing, varying navy ink contours,
broad flat colors, one soft cel-shadow level, warm cheek hatching.
Palette: ivory, mist blue, navy, restrained gold and mint accents.
Keep complete silhouettes and props. No text, digits, logos, watermarks.
No photographic/3D material lighting or dense hard-surface detail.
```

### 无字底图

```text
Use case: compositing.
Inputs: 1=layout guide/edit target, 2=approved hero, 3=approved series style.
Create [aspect ratio] TEXT-FREE poster background for 兴安电竞 [玩法].
Preserve [exact row count/card count] and copy-safe rectangles [coordinates].
All illustration AND scenery use the same hand-drawn anime style.
Header: left clear ivory copy area; approved character group at right.
Offer: separate pale accent ticket, clear primary copy area, small gift accent.
Price header: ONE CONTINUOUS NAVY BAR, same navy throughout its entire width.
Mode accents may color offer outlines, chevrons and final-row tint.
Keep numeric columns and icon safety margins empty; props only outside them.
For cover copy, reserve clear horizontal-centering space within each copy zone.
Keep character silhouettes and foreground props outside all copy-safe areas.
For full posters, reserve notice-body width and height for readable normal copy,
selectively bold key information and semantic diagrams, with vertical padding;
keep footer scenery modest rather than leaving a large empty logo-only tail.
[Full poster: guarantee and grouped rule areas / Social: omit rules area].
No Chinese, letters, numbers, percent signs, prices, pseudo-text or watermark.
No new contact CTA, QR code or unsolicited terms. Flat digital image, no mockup.
```

保持本轮明确约束，不把此骨架当作新增内容清单。使用可用的图像生成/编辑工具；手绘人物和环境不是 SVG 占位图替代品。表头、文字对齐、单位放大等现有矢量层修改直接改源文件。
