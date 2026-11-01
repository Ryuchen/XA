# 美术与分阶段确认

当前流程优先：先直出完整海报作为视觉基准，再分别制作并依次叠加底图、透明区块、透明插画及可编辑文案四层。下方旧的先插画后底图顺序仅保留美术参考，不作为当前生产步骤；角色、画风和已核对数据要求继续适用。

只在需要新插画、换画风或重新布置底图时读取本文件。只改价和文案时跳过图像生成。

## 默认美术

二次元手绘 Q 版：自然变化的深蓝线稿，明亮平涂，一层柔和赛璐璐阴影，轻微手绘腮红。奶白底、雾蓝环境、海军蓝服装；暖金与薄荷青作为适量模式色。建筑和载具也要手绘化，不能人物是动漫而背景是写实金属渲染。

角色身份优先使用 2026-10-08 已确认的 [三角色 r1 原型](../assets/approved-visuals-20261008/character-trio-prototype-r1.png)，按玩法选用蜂医、灰鼠及粉发电竞女孩。角色细节与新封面要求见 [角色原型与封面规范](approved-characters-and-cover.md)。历史 `assets/material-v3/illustrations/01-medic-mouse-loot.png`、`02-loaded-extraction.png` 和 `03-tactical-sticker-atlas.png` 可继续提供开箱、满载撤离与道具参考；贴纸选取单个元素，不能整张贴进海报。

## 生成流程

1. 先读用户本轮玩法的语义与卖点，再选择对应情景。没有必要的玩法关联时不要强加某种道具。
2. 新人物/情景先生成独立插画，可抠图资产用透明背景；用户确认后用于底图。
3. 先明确布局：画幅、标题安全区、表格行数、优惠位、规则组数。封面按确认参考预留左侧品牌/艺术主标题左对齐区及居中卖点胶囊/平台行，右侧安排角色和手绘环境。用简单结构稿锁定区域，再生成无字底图。
4. 底图确认后加矢量文字。图像模型生成的面板位置可能漂移；以生成结果实测为准。

当用户已授权沿用资产或进入后续阶段，不重新索取已给出的确认；只就缺失的决定提问。生成工具不可用时保留现有材料并如实说明，不悄悄换成收费 API 或重新安装工具。

## 可复用提示词骨架

### 插画

```text
Use case: illustration-story.
Asset: 兴安电竞 [玩法名称] poster illustration, reusable transparent PNG.
Scene: [与本轮玩法相关的单一情景].
Characters: [按已确认三角色r1原型选用角色，保留身份细节，变化表情与动作].
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
For cover copy, follow the approved 2026-10-08 reference: ivory left copy zone,
right hand-drawn character scene; leave separate zones for left-aligned brand
and staggered bold title, a thin warm-gold curved underline, a navy selling-point
pill with centered copy, and a centered platform line. Keep all zones text-free.
[Full poster: guarantee and grouped rule areas / Social: omit rules area].
No Chinese, letters, numbers, percent signs, prices, pseudo-text or watermark.
No new contact CTA, QR code or unsolicited terms. Flat digital image, no mockup.
```

保持本轮明确约束，不把此骨架当作新增内容清单。使用可用的图像生成/编辑工具；手绘人物和环境不是 SVG 占位图替代品。表头、文字对齐、单位放大等现有矢量层修改直接改源文件。
