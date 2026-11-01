# 塔主大人 · 道具装饰贴图

本轮用户要求增加沙色保险、三幻神Q版贴图和装饰性插画。使用内置`image_gen`，不更换主角色。新道具按真实游戏截图核对轮廓，再转为既有兴安电竞二次元手绘风格。

## 参考图用途

1. `references/game-items/phantom-trio.png`：三幻神的游戏造型参考。天圆地方为金色浑天仪式环形摆件；万金泪冠为奶白金边、蓝宝石、可打开的穹顶冠盒；纵横为金边黑白棋盘与棋子。
2. `references/game-items/sand-safe.png`：沙色保险的游戏造型参考，为直立沙色长方形柜、门上中央深色竖条与上方密码面板。
3. 技能内`assets/material-v3/illustrations/01-medic-mouse-loot.png`：仅用作手绘线条、简化色块、奶白雾蓝海军蓝暖金配色的参考。

参考来源：[三幻神游戏截图](https://ol.3dmgame.com/gl/323829.html)、[天圆地方单品图](https://gl.ali213.net/html/2026-2/1742821.html)、[纵横游戏截图](https://gl.ali213.net/html/2026-2/1742793.html)。这些截图只作为造型依据，不把攻略内价格、刷新概率、地图规则等写入海报业务数据。

## atlas-v1 提示词

Use case: style-transfer. Asset: one coherent reusable transparent PNG sticker atlas for the Chinese Delta Force game themed 兴安电竞 塔主大人 price poster.

Inputs: image 1 is the exact visual identity reference for the three rare collectible props; image 2 is the actual in-world sand-colored safe silhouette reference; image 3 is the approved chibi hand-drawn series STYLE reference only. Output new prop-only illustrations, not a modified screenshot.

Create exactly SIX separate sticker illustrations on a genuinely transparent landscape canvas, arranged in an even 3-column by 2-row atlas. Each sticker is isolated, centered within its own equal cell, completely inside that cell, with generous transparent gutters and no overlaps. Entire prop silhouettes must remain intact. No cell borders, no ground plane, no collage scenery, no text, no labels, no letters or numbers anywhere.

Top row left to right:
1. 天圆地方: the reference's golden armillary-sphere collectible, nested circular gold rings around a small celestial ball, on a short ornate pedestal with a round base. Preserve the recognizable astronomical-ring silhouette, not a generic crystal ball or globe.
2. 万金泪冠: the reference's dome-shaped enamel crown/jewel casket, ivory enamel and ornate warm-gold bands, large blue gems, lifted hinged dome showing a dark royal-blue interior. Preserve this open rounded jeweled casket silhouette; do not replace it with a generic pointy crown.
3. 纵横: the reference's ornamental ivory-and-charcoal checker chessboard on a shallow square gold-edged raised plinth, with a few clearly visible chunky gold chess pieces. Three-quarter view and simplified readable checker pattern. Preserve chess set identity, not a treasure chest.

Bottom row left to right:
4. A CLOSED Delta Force sand-colored safe: tall narrow upright rectangular cream/sand-beige steel cabinet, softly rounded corners, a dark navy/charcoal vertical inset centered on the front door, a tiny cyan key pad at the upper end of that inset, side hinges. Three-quarter front view. Preserve the reference's standing cabinet form; not a suitcase and not a cube.
5. An OPEN version of the same sand-colored safe: identical tall cabinet design, door swinging open toward left, warm gold bars visible inside, a few tiny hand-drawn gold sparkles close to the safe. Complete open door and cabinet contour within its own cell.
6. A conventional large safe in dark mist-blue/navy-gray steel, a squat upright rectangular cabinet, round lock and compact three-spoke wheel handle, side hinges; a few warm-gold bars resting by its bottom corner. Clear visual distinction from the taller sand-colored safe.

Style: cute rounded exaggerated chibi PROP proportions, natural slightly varied navy ink contours, clean Japanese anime hand drawing, broad flat color regions, one gentle cel-shadow level, a restrained few gold star accents. Match image 3's line quality and warmth. No anthropomorphic faces, no characters, no photorealism, no 3D render appearance, no dramatic metallic specular lighting, no thick black outline. Palette: ivory, mist blue, navy, sand beige, restrained warm gold with blue gems. Real alpha transparency, no checkerboard drawn into the pixels. No watermark, no branding, no invented promise or price.

## sand-background-v1 提示词

用户要求：“底图背景颜色应该改成和巴克什的黄沙一致的颜色”。通过内置`image_gen`编辑已确认的`assets/backgrounds/poster-gold.png`；不重新生成价格文字或业务内容。

Use case: precise-object-edit / palette recolor. Edit target: the provided approved TEXT-FREE anime chibi poster background. Make a restrained COLOR-ONLY background revision for a Delta Force 巴克什 / Baksh desert sand themed price poster. Preserve the exact composition, aspect ratio, panel positions, character identity, face, pose, mouse, medical drone, all object silhouettes, and original hand-drawn outlines. Do not add, remove, shift, or redraw objects. Keep the medic and mouse navy clothes, mint medical accents, skin and fur colors, gold loot, and deep navy panel header bars unchanged. Change ONLY the environment and background palette: all mist-blue architecture, distant scenery, cranes, hangar facade, low-opacity cloud motifs, peripheral map-like scenery, footer airfield and sand ground should become warm Baksh desert tones of sand ochre, beige stone, pale dusty gold and light warm ivory. Footer aircraft and supply crates can retain subdued navy outlines with muted sandy-grey surfaces. The overall paper/background is warm pale sand, not cool blue or bright saturated lemon yellow. Replace the cool blue shaded background areas and pale blue panel surfaces with light sandy cream while preserving readability and every exact blank copy area. Keep fine navy ink line quality and warm soft cel shading. Flat clean digital artwork, same vertical framing as the source, no text, no letters, no numbers, no logos, no watermarks, no new sticker or props. This is a careful existing-art color edit; the main foreground character artwork must stay visually identical.

保存原图：`01-illustrations/tazhu-sand-background-v1.png`，原生809×1944，源输出来自`exec-737e7b06-2261-44d8-b267-61b3af6bd7de.png`。构图沿用，编辑输出的环境包含黄沙、沙色建筑和棕榈；重建页头与页尾时归一化为809×1942裁切坐标。正文区域另外采用确定性的浅沙色矢量卡片和原有排版。
