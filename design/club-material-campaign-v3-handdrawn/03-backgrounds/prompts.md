# 二次元手绘底图 · 生成提示词

使用内置 image_gen，将用户已确认的新画风插画与布局模板合成。所有底图均为无字底图，正式价格、规则和艺术字留到用户确认后再填充。

## poster-gold

```text
Use case: compositing.
Deliverable: finished TEXT-FREE background for the 兴安电竞 Delta Force material-supply price poster, tall portrait 5:12, target 1600 x 3840, high quality. This is the NEW JAPANESE 2D HAND-DRAWN ANIME edition (二次元手绘), not the older detailed semi-realistic illustration style.
INPUTS:
1 is the EDIT TARGET empty structural template. Keep its exact framing, panel geometry and empty copy areas.
2 is the USER-APPROVED new anime hero art. Preserve these character designs, simplified equipment and cel-shading.
3 is the USER-APPROVED new anime sticker atlas. Use selected individual elements, never the whole atlas.
ART DIRECTION applies to ALL ART AND BACKGROUND: clean naturally varying dark blue hand-inked lines, clear broad flat colors, only one soft cel-shadow level, small pencil-like blush and accent strokes. Buildings, helicopter and boxes must ALSO look like lightly hand-drawn anime scenery: simplified outlines, painted color blocks, airy pale blue watercolor washes at the horizon, no detailed realistic architecture. Ivory paper-color ground, misty blue, navy, restrained warm gold and tiny mint medical details. A gentle anime illustrated notebook atmosphere, polished and readable. No 3D material rendering, no realistic metal or fabric, no cinematic/photographic lighting, no gritty texture, no heavy gradients. No dense foliage framing.
LAYOUT, coordinates based on input 1's 1600x3840:
- Hero/header 0..640: x80..790 is a CLEAR empty ivory area for a later logo and large headline. Absolutely no objects in this left title area. Keep the navy pill at x80 y510 w465 h84 blank. Place the complete approved medic+mouse+open-loot-case illustration from input 2 ONLY at x850..1515 y80..605, preserving heads, ears, feet and case. A compact tiny drone may float above. Behind this right group draw a simplified pale-blue hangar, distant thin tower and a few loosely painted blue cloud shapes; blend quietly into the ivory. No dark rectangular transparency halo.
- Offer ticket x80 y680 w1440 h270: warm cream gold fill, thin hand-inked gold border, small side perforation notches. Keep left 77% completely empty. Draw the extra-gift mouse from input 3 in the small RIGHT ticket segment after the dashed separator, with an upward drawn gold arrow and 2 sparkles. No words or digits.
- Price table x80 y1020 w1440 h1120: clean navy header 112px high; EXACTLY SIX empty equal data rows 168px high below. Keep the long clean horizontal row rules. Alternating extremely pale blue and ivory; final sixth row pale gold. Keep 6 small central double chevrons, one per row, no other glyphs. Tiny 2D hand-drawn loot props may sit at x1410..1485 on the far right, outside future number areas. Keep all of x180..550 and x910..1370 clear.
- Note strip y2140..2220: empty.
- Protection block x80 y2230 w1440 h350: pale blue left zone with the new atlas's happy mouse and flat navy shield, leaving a little clear space beneath for a future label. White right body remains empty, with exactly TWO blank condition pills (first pale blue, second cream gold) as in the template, and enough open area below for the result statement.
- Future section heading y2630..2740: mostly blank; one small outlined clipboard at far left only.
- Terms/rules panel x80 y2760 w1440 h862: preserve EXACTLY SIX full-width EMPTY rows and template separators at y2950,3070,3210,3330,3490; first row is deliberately taller. Place small simple hand-drawn map-pin, backpack, keycards, burst, supply-safe and headset icons in left x115..200. Keep x220..1490 mostly blank for later category names and body copy, especially x475..1490.
- Bottom process strip x80 y3700 w1440 h82: pale blue, blank, at most two tiny linking arrows.
Decorate OUTER margins lightly with small doodled star glints, triangle waypoints, a simple broken route line. Very bottom behind footer may have a few faint blue outlined cargo crates and a simple pale helicopter silhouette. No dense environmental clutter.
Preserve exact template content count and proportions. Slight natural hand-line character on borders is okay, but do not distort the straight table grid, row height, alignment or copy whitespace.
ABSOLUTELY NO TEXT: no Chinese, no letters, no prices, no digits, no placeholder scribbles, no headline, no brand wordmark, no watermark. Pure illustrated background ready for later vector copy. Flat front-facing digital graphic, not a photographed poster.
```

## poster-teal

```text
Use case: precise-object-edit.
Input: the single attached image is the EDIT TARGET: the newly redrawn JAPANESE 2D HAND-DRAWN ANIME gold price-poster background.
Create its exactly matching MINT-TEAL edition, preserving the ENTIRE layout, anime drawing style, characters, background, panel positions, dimensions, framing, blank text zones, and EXACTLY SIX price rows and SIX rules rows.
Change ONLY edition accents:
- the promotion ticket light gold fill becomes very pale mint #E4F2ED and its border/perforations muted teal #4F9F96;
- the leftmost short segment of the top horizontal color bar becomes teal;
- all six price row center chevrons become teal;
- the final sixth price-row tint becomes pale mint;
- the RIGHT condition capsule in the protection panel becomes pale mint;
- small standalone margin gold sparkles may become teal where useful.
Keep actual valuable loot GOLD: gold bars, little gold statuette, chest hardware, red/gold keycards, shield checkmark, promotion mouse's rising golden arrow. Keep the characters' hair, skin, fur and uniforms unchanged. Retain blue ink lines and hand-painted pale-blue scenery, no realistic or 3D rendering.
Keep all copy fields perfectly EMPTY; no text, numerals, r/w letters, Chinese, logos, watermark or pseudo-writing. Do not alter or add any scene objects. No cropping or reframing.
Output a finished text-free portrait PNG at largest practical native resolution, same 5:12 aspect ratio, desired 1600x3840.
```

## cover

```text
Use case: compositing.
Deliverable: NEW text-free landscape cover background for the 兴安电竞 material supply price series, JAPANESE 2D HAND-DRAWN ANIME / 二次元手绘 style, 16:9, desired 2560x1440.
INPUTS:
1 = EDIT TARGET empty cover layout template; retain full composition and blank navy capsule.
2 = user-approved new hand-drawn “满载撤离” character illustration. Preserve these character designs and smiling faces, pose, medical uniform, mouse and cargo cart.
3 = the NEW tall price poster, series STYLE REFERENCE. Match its fresh blue/ivory line-drawn anime base, drawn clouds, simple buildings and restrained gold glints.
Composition:
Keep LEFT half uncluttered, warm ivory, specifically x130..1240 y110..760 completely clear for later brand name and large ONE-LINE art headline. Keep empty dark navy rounded capsule at x135 y835 w850 h100. Keep a slim simple hand-inked warm-gold divider near y1090; leave subtitle space above it. No text.
In RIGHT half, x1340..2490 y190..1210, put the complete two-character group from image 2: excited grey mouse leads with a gold collectible, friendly male medic grins and gives thumbs up while pulling a cargo trolley with 3 blue/cream cases and rolled blanket. Keep complete feet, ears, tails and cart. A cute small medical drone flies near the upper right. Hand-drawn action accents and a few star glints.
Background must itself be visibly DRAWN: simplified pale-blue outlined hangars, radio tower, cargo crates, distant cartoon helicopter, a simple helipad with broad painted light blue shadow shapes. Use naturally varying thin blue ink strokes and broad flat color blocks. Quiet distant buildings only behind the RIGHT group and at outer bottom corners. Ivory negative space dominates the left. Three soft drawn cloud shapes; a little pale watercolor wash at horizon is okay. No dense leaves or foliage; no photorealistic sky or detailed hard-surface architecture.
STYLE: clean Japanese anime chibi artwork, navy hand-inked contours, broad FLAT colors and only one soft cel-shadow level, warm hand-drawn cheek hatching, simplified equipment and gold glints. Match new approved assets and tall poster exactly. Characters, crates, helicopter, towers all belong to the SAME 2D illustrated world. A few doodled triangular route markers and dashed blue extraction line along lower edge under y1160. Polished and playful, high legibility.
NO realistic/3D material lighting, no detailed metallic reflections, no complex grit, no realistic fur, no dramatic ambient occlusion, no overpainted photographic scenery.
Keep framing 16:9; no new table or panels. Absolutely no letters, words, Chinese, price numerals, logos or watermark, no fake writing. Pure base art for later vector text. Flat front-facing image, no physical mockup.
```

## social

```text
Use case: compositing.
Deliverable: TEXT-FREE compact advertising price-poster base for 兴安电竞, for 小红书 / B站 / 抖音 / 微信朋友圈. Portrait 3:4, desired 2400x3200 high resolution. New JAPANESE 2D HAND-DRAWN ANIME style, clean and cute.
INPUTS:
Image 1 is the EDIT TARGET 4-table blank layout template. Preserve the 2x2 card arrangement and exact row counts.
Image 2 is the user-approved NEW anime hero group, source for chibi character identities.
Image 3 is the new hand-drawn tall gold poster, the exact SERIES STYLE reference for scenery, lines, color blocks and decoration.
Image 4 is the new simplified hand-drawn sticker atlas, selected tiny accents only.
STYLE throughout: naturally varying dark blue ink contours, broad flat blue/ivory colors, a single restrained cel-shadow level, soft cheek hatching, simplified navy tactical clothes, gold bars as simple drawn yellow shapes, no 3D or realistic material lighting. Background buildings, helicopter and crates ALSO use light blue hand-drawn outlines and flat washed colors, airy cloud shapes, low contrast. Do not use photorealistic or densely detailed scenery.
EXACT COMPOSITION based on 2400x3200:
1 HEADER y0..600. LEFT x110..1290 y80..550 is empty warm ivory, reserved for brand and one-line art headline. RIGHT x1450..2300 y60..600 has a compact cheerful chibi male medic and grey mouse beside a blue supply case, matching image 2; tiny medical drone and 2 golden doodled glints. Keep entire figures in header, not on left text area. A very pale drawn hangar and small radio tower behind group; sparse drawn clouds.
2 OFFER TICKET x110 y645 w2180 h290. Pale cream gold, thin hand-drawn gold contour and small side ticket notches. Keep x155..1890 entirely empty for later prominent offer; separated small RIGHT segment contains the gift-hugging mouse and an upward arrow from atlas.
3 EXACTLY FOUR price cards in 2x2 layout:
top left x110 y1020 w1045 h915; top right x1245 y1020 w1045 h915;
bottom left x110 y2025 w1045 h915; bottom right x1245 y2025 w1045 h915.
Every card: blank title strip height132, blank secondary heading strip height95, then EXACTLY SIX equal EMPTY price rows (110 each) with five horizontal dividers. Maintain identical aligned card dimensions. NO icons or objects in the price row fields. No vertical dividers. Gentle alternating ivory and pale-blue rows.
TOP PAIR title bars NAVY, bottom pair muted BLUE. Left-column secondary strips pale GOLD, right-column secondary strips pale MINT. A tiny simple hand-drawn monitor outline at far left of top title bars and phone outline at far left of bottom title bars, each small enough for later centered title.
4 Below y2980: small clear note area near center, with only tiny pale blue outlined cargo boxes at very bottom outside cards.
Decorations: restrained small hand-doodled triangular waypoints, 4-point gold/blue stars, light broken evacuation route around OUTER margins. No leafy frame, no heavy corner vignette. Bright ivory remains dominant.
NONNEGOTIABLE: preserve the template's four aligned cards with SIX data rows EACH, no cropping, no extra rows or cards, same 3:4 aspect. No order instructions, no safety/terms panel, no QR, no contact CTA. This is a simplified ad.
NO WRITING: no titles, Chinese, letters, digits, prices, percentages, pseudo-writing, logo wordmark or watermark. All text will be added as vector typography in a later stage. Flat front-facing digital art, not a mockup.
```

