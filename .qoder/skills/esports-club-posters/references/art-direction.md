# 美术与分阶段确认

只在需要新插画、换画风或重新布置底图时读取本文件。只改价和文案时跳过图像生成。

## 默认美术

二次元手绘 Q 版：自然变化的深蓝线稿，明亮平涂，一层柔和赛璐璐阴影，轻微手绘腮红。奶白底、雾蓝环境、海军蓝服装；暖金与薄荷青作为适量模式色。建筑和载具也要手绘化，不能人物是动漫而背景是写实金属渲染。

角色身份优先使用当前项目已确认的原型；技能自带的 [三角色示例稿](../assets/sample-club/characters/character-trio-prototype-r1.png) 提供医疗兵、电竞女孩与鼠形吉祥物三种选择，可整套替换为俱乐部自有角色。角色细节与封面要求见 [示例角色与封面规范](sample-visuals.md)。示例 `assets/sample-club/illustrations/01-medic-mouse-loot.png`、`02-loaded-extraction.png` 和 `03-tactical-sticker-atlas.png` 可继续提供开箱、满载撤离与道具参考；贴纸选取单个元素，不能整张贴进海报。

## 生成流程

1. 先读用户本轮玩法的语义与卖点，再选择对应情景。没有必要的玩法关联时不要强加某种道具。
2. 新人物/情景先生成独立插画，可抠图资产用透明背景；用户确认后用于底图。
3. 先明确布局与密度：画幅、标题安全区、表格行数、优惠位、规则组数。封面/广告页按社媒卡构图留大留白，只安排主标题与核心卖点；价目海报按信息图分层，预留数据列与规则区。用简单结构稿锁定区域。
4. 封面/广告页可由模型直接绘制风格化标题（图文一体），需要精确数据时再叠矢量层；价目海报先生成无字底图，确认后加矢量数据文字。图像模型生成的面板位置可能漂移；以生成结果实测为准。

当用户已授权沿用资产或进入后续阶段，不重新索取已给出的确认；只就缺失的决定提问。生成工具不可用时保留现有材料并如实说明，不悄悄换成收费 API 或重新安装工具。

## 生成时锁定尺寸与比例

尺寸和比例写进**生成指令**，不靠生成后压缩或缩放补救。

| 目标比例 | 生成尺寸 |
| --- | --- |
| 16:9 横版封面 | 1792x1024 |
| 3:4 广告 / 小红书 | 1024x1536 |
| 1:1 朋友圈 | 1024x1024 |
| 9:16 故事 / 竖版 | 1024x1792 |

多比例输出必须每个比例独立生成，AI 直出无法靠裁切或缩放复用。底图原生尺寸应不小于目标导出尺寸；价目海报是竖版长图（示例约 1:2.4），须显式写明画幅、行数与留空区域。文字字数上限、数字混排风险与强制二次校验见 [文字防错与生成时约束](text-accuracy.md)。

## 可复用提示词骨架

新生成美术时先读 [手绘风格库集成](handdraw-style-library.md) 决定风格、主题色与版式族：封面/广告页用社媒卡 `SC-*`（低/中密度），价目海报用信息图 `IG-*`（高密度）。风格库未安装时用默认骨架继续，不编造编号。提示词正文只写自然语言名称，不带任何风格/色彩/图型编号。

文字按类型分工：**标题、卖点、装饰短语可以是风格化文字**，由模型图文一体绘制或用矢量艺术字；**价格、物资、单位、门槛、补偿数额与规则正文一律矢量渲染**，底图必须为这些区域留空。因此只有封面/广告页追加上游图文一体后缀，价目海报底图保持无字。

### 插画

```text
Use case: illustration-story.
Asset: [俱乐部名称] [玩法名称] poster illustration, reusable transparent PNG.
Size: [生成尺寸，如 1024x1536；透明插画按主体留足边距].
Scene: [与本轮玩法相关的单一情景].
Characters: [按当前项目已确认原型选用角色，保留身份细节，变化表情与动作].
Style: [示例画风，或选定风格名 + 参考作者/风格名称 + 过滤后的正向核心特征].
Palette: [示例配色，或选定主题色的自然语言名称与主色/点缀色角色].
Keep complete silhouettes and props. No text, digits, logos, watermarks.
No photographic/3D material lighting or dense hard-surface detail.
```

默认示例画风与配色（未选用风格库时）：

```text
Style: clean Japanese anime chibi hand drawing, varying navy ink contours,
broad flat colors, one soft cel-shadow level, warm cheek hatching.
Palette: ivory, mist blue, navy, restrained gold and mint accents.
```

### 封面 / 广告页（社媒卡，低/中密度）

```text
Use case: social-card.
Asset: [俱乐部名称] [玩法] cover / ad card, [aspect ratio].
Size: [按上表锁定的生成尺寸，如 16:9 → 1792x1024].
Headline: [≤8 字主标题原文]；副标题 ≤12 字。含数字时先做风险告知。
Layout: [社媒卡构图，如 文案主导底部辅助图 / 极简大字小图点缀 / 一大一组非对称].
Density: [极低 / 低 / 中] — only headline and core selling points, no rules or terms.
Style: [选定风格名 + 参考作者/风格名称 + 过滤后的正向核心特征].
Palette: [选定主题色的自然语言名称与主色/点缀色角色].
Stylized headline and short selling points may be drawn into the image.
Keep prices, loot figures and thresholds OUT of the image; leave clear space for them.
No contact CTA, QR code or unsolicited terms. Flat digital image, no mockup.
[原样追加上游图文一体后缀]
```

封面沿用示例参考时仍按左文右图：品牌与错落艺术主标题左对齐，卖点胶囊与平台行在左侧安全文字区居中，右侧角色与手绘环境。详见 [示例角色与封面规范](sample-visuals.md)。

### 价目海报底图（信息图，高密度，无字）

```text
Use case: compositing.
Inputs: 1=layout guide/edit target, 2=approved hero, 3=approved series style.
Create [aspect ratio] TEXT-FREE poster background for [俱乐部名称] [玩法].
Size: [显式写明竖版长图画幅与原生像素，不小于目标导出尺寸].
Layout: [信息图分层，如 多行左文右图 / 双列多行对比 / 讲义式高密分析].
Preserve [exact row count/card count] and copy-safe rectangles [coordinates].
All illustration AND scenery use the same hand-drawn style [本轮选定画风].
Header: left clear ivory copy area; approved character group at right.
Offer: separate pale accent ticket, clear primary copy area, small gift accent.
Price header: ONE CONTINUOUS NAVY BAR, same navy throughout its entire width.
Mode accents may color offer outlines, chevrons and final-row tint.
Keep numeric columns and icon safety margins empty; props only outside them.
Reserve guarantee and grouped rule areas; keep them text-free.
No Chinese, letters, numbers, percent signs, prices, pseudo-text or watermark.
No new contact CTA, QR code or unsolicited terms. Flat digital image, no mockup.
```

保持本轮明确约束，不把此骨架当作新增内容清单。使用可用的图像生成/编辑工具；手绘人物和环境不是 SVG 占位图替代品。表头、文字对齐、单位放大等现有矢量层修改直接改源文件。
