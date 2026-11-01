# 第二阶段：底图生成提示词

布局由 SVG 网格定义，PNG 底图由内置 image_gen 工具结合第一阶段已确认插画生成。工具输出尺寸与提示词期望尺寸不完全一致；原图保留，交付目录另有放大导出版本，未把放大尺寸描述为原生生成分辨率。

## poster-gold

```text
Use case: compositing.
Asset type: a FINISHED TEXT-FREE high-resolution poster background, portrait 1600 x 3840 pixels, exact 5:12 aspect ratio, for 兴安电竞 / Delta Force material supply menu. This is stage 2: illustrated layout only; real text is added as vectors later.
Input images: Image 1 is the EDIT TARGET, a carefully designed blank layout template; preserve its framing, aspect ratio and all panel positions. Image 2 is the APPROVED hero illustration to incorporate, preserving character identities. Image 3 is the APPROVED sticker atlas; use selected individual elements, do not insert the whole sheet.
Goal: transform the bare template into a charming, polished branded poster base while preserving ALL usable whitespace and ALL layout geometry. Match original campaign: warm ivory, misty steel blue, navy, champagne gold. Delicate anime linework and soft chibi cel-shading, clean commercial design.
Geometry in target's 1600x3840 coordinates:
- Top 0..650: leave x80..820 almost completely ivory and EMPTY for future logo and large artistic headline. Keep the navy blank capsule at x80 y510 w465 h84. Place the complete approved hero illustration from Image 2 at x850..1530 y80..600, medium size, preserving complete ears, heads, mouse, loot chest. Replace the pale circle behind it with barely visible airy extraction-base silhouettes: small hangar roof, distant radio tower, misty blue clouds. Do not spread dark details behind left headline area. A tiny support drone or two gold glints can float near the upper right. Softly blend the transparent art's edges onto the ivory ground with NO dark rectangular artifact.
- Promotion ticket x80 y680 w1440 h270: preserve pale warm-gold fill and delicate gold outline. Make it a subtle premium perforated ticket with small side notches and a dashed separator near x1190. Keep x120..1150 completely blank for later offer text. Put ONLY the extra-gift-hugging mouse and upward arrow from the atlas in the narrow right part x1230..1480. No text or numbers.
- Main price table x80 y1020 w1440 h1120: dark navy header exactly 112 high and EXACTLY SIX uniform empty data rows 168 high each, alternating ivory and pale blue, last row lightly gold. Keep their long straight horizontal dividers, NO vertical cell borders. Preserve the six central small double-arrow marks from the template, one in the vertical center of each row. Rightmost margin x1390..1470 may have one tiny simple gold/blue loot crate per row. Blank number regions x180..550 and x900..1370 must remain totally clear. Do not draw letters r or w, prices or numbers.
- Leave narrow strip y2140..2220 clear for later note.
- Protection panel x80 y2230 w1440 h350: preserve pale blue left vertical zone and white body. Place the grey mouse peeking over navy shield sticker in the left zone x115..305 y2260..2450; leave space under it for a future label. Keep body EMPTY, retain the TWO blank condition capsules and all white space below them. No ticks or words inside capsules.
- y2630..2740: mostly empty for a future section title. One tiny clipboard icon at far left is okay.
- Rules panel x80 y2760 w1440 h862: EXACTLY SIX full-width rows, with dividers at y2950,3070,3210,3330,3490. Keep entire content area x470..1490 blank. Within left x110..200 put small coherent icons: map pin, backpack, keycards, a subtle burst, safe box, support headset. These are decorative icons ONLY, no text. The rule rows remain empty and calm.
- Footer x80 y3700 w1440 h82: retain pale blue ribbon; at most 2 small centered linking arrows spaced across it. No words.
Decoration: carefully scatter tiny triangular route markers and 4-point star glints in OUTER MARGINS only; two understated misty blue cargo crates at the very bottom edge; a barely visible dashed evacuation path along an edge. Draw no decoration over text zones.
Non-negotiable: keep EXACTLY six table rows and six rules rows, single tall page, large negative spaces, no cropped content, no extra panels, no change to the original template proportions. Flat front-on digital artwork, NOT a photo/mockup, no paper curl, no perspective distortion.
Absolutely NO text, no Chinese characters, no pseudo-writing, no letters, no numerals, no titles, no logos, no watermarks. Do not output the annotated wireframe. Deliver one high-resolution PNG, desired 1600x3840.
```

## poster-teal

```text
Use case: precise-object-edit.
Asset: matching MINT-TEAL edition of the attached finished text-free Delta Force chibi price poster background.
Input image: the attached image is the EDIT TARGET and exact geometry/style reference.
Change ONLY the edition accent colors: the pale gold promotion ticket fill and outline become pale soft mint #E4F2ED with restrained teal #4F9F96 outline; the tiny central price-row chevrons become teal; the sixth / last price row tint becomes pale mint; the RIGHT condition capsule in the shield-protection panel becomes pale mint; the left segment of the top stripe becomes teal. Use cool teal accents on small margin triangle markers. KEEP actual gold loot, gold bars, gold chest hardware, golden shield check, red/gold keycards and warm character skin/fur exactly as in the target. The promotion gift mouse may keep its gold rising arrow to communicate added value.
Keep absolutely everything else unchanged: illustration identities and poses, background buildings and foliage, all SIX price rows and exact divider positions, all SIX rules rows, all panels, margins and header negative space, dark navy table header and platform pill, white copy areas, shield illustration, footer. Preserve the ORIGINAL 5:12 tall aspect ratio, complete full poster, no cropping, same framing. Keep empty text zones EMPTY.
No new text, no pseudo-text, no prices, no letters, no numbers, no logo, no watermark. Flat front-on digital artwork.
Please produce at the largest practical native resolution for clean high-resolution printing, desired 1600 x 3840 pixels, maintaining the target's full content and exact composition.
```

## cover

```text
Use case: compositing.
Asset: finished TEXT-FREE illustrated landscape cover background for 兴安电竞 Delta Force material supply price series, 16:9, desired 2560x1440 high resolution.
Inputs: Image 1 is the EDIT TARGET layout template. Image 2 is the APPROVED “满载撤离” illustration; preserve the two character identities, colors, expressions and their cargo cart. Image 3 is the finished tall gold poster STYLE REFERENCE, to keep the whole set cohesive.
Keep the full 16:9 layout. Match warm ivory background, misty blue extraction base, navy outlines and restrained warm gold, gentle premium chibi anime commercial illustration.
Composition:
LEFT 5%..48% of canvas: largely empty clean ivory for future brand name and one-line large artistic title. Keep especially x130..1230,y115..750 free of objects and textured scenery. Keep the blank navy pill at x135 y835 w850 h100, same size and placement. Leave the line of future subtitle at x140 y1010 empty. Preserve the slim gold divider at y1090.
RIGHT 52%..97%, vertical y180..1220: incorporate the approved full group from Image 2, human medic, ecstatic mouse and fully loaded cargo cart, complete feet/ears/tail, no cropping. The mouse leads in front near x1400 and the medic is behind at x1870 with trolley farther right, like the reference. The group sits naturally on a subtle blue helipad. A tiny support drone flies above them. Bring the chibi characters into focus with clean crisp outlines and warm faces; no black alpha-edge halos.
Environment: softly painted low-contrast misty blue airfield/helipad, distant hangars/radio tower far upper right, tiny extraction helicopter silhouette upper right, graceful pale clouds. A few blue storage crates at lower corners, kept small on left. Two or three triangular golden waypoints and sparkling accents. One dashed evacuation route in the LOWER margin under y1140, not through title space. Delicate navy foliage only as very small edge accents, similar to the approved tall poster. Keep middle left blank.
Do not create rows or price tables on the cover. Do not add signs, maps with writing, ribbons beyond the one blank navy capsule.
NO TEXT of any kind: no headline, no brand, no Chinese, no letters or numerals, no watermark, no pseudo-text. This is only the underlying art and layout; text will be added later.
Flat front-on digital poster artwork, not a physical poster mockup, no perspective distortion. Keep the chosen character style intact, avoid photorealism or glossy 3D.
```

## social

```text
Use case: compositing.
Asset: finished TEXT-FREE compact promotional price-poster background for sharing on 小红书 / B站 / 抖音 / 微信朋友圈. Portrait 3:4, desired 2400x3200 pixels high resolution.
Inputs: Image 1 is the EDIT TARGET blank structural template. Preserve all four tables and their exact grid. Image 2 is the APPROVED two-character “满载撤离” hero art. Image 3 is the finished tall gold poster as STYLE REFERENCE. Image 4 is the approved sticker atlas for selected small accents.
Art direction: warm ivory and misty blue, navy, restrained warm gold and mint, soft clean chibi anime commercial illustration, consistent with the series. Rich small tactical collectible details on the outer margins, but clear broad copy spaces.
Geometry / exact composition on 2400x3200:
TOP header y0..600: leave x110..1310 y80..550 EMPTY for later brand and large artistic headline. Put a compact coherent hero illustration from image 2 only at x1460..2290 y70..575 (medic and mouse celebrating a successful haul beside one cart of crates); all characters fully inside header. The group must not intrude on left text zone. Behind the group use very pale clouds and an extraction hangar, with tiny drone. Upper left remains clean ivory.
PROMOTION ticket x110 y645 w2180 h290: light warm gold, thin gold stroke and subtle perforated side notches. Keep x150..1890 EMPTY for the future central offer. Place the gift-hugging mouse with upward arrow from the atlas only at x1980..2240 y675..910; retain separator.
FOUR price cards in EXACT 2 by 2 arrangement:
A x110 y1020 w1045 h915; B x1245 y1020 w1045 h915;
C x110 y2025 w1045 h915; D x1245 y2025 w1045 h915.
Each card has a blank top title strip height132, a blank secondary column heading strip height95, then EXACTLY SIX empty data rows height110. Preserve template geometry and straight separators. These six rows are NOT five or seven. All FOUR cards identical size and perfectly aligned. The TOP PAIR title strips navy. The BOTTOM PAIR title strips muted blue. LEFT-column secondary strips warm cream gold; RIGHT-column secondary strips pale mint. Rows alternate barely blue-tinted white and ivory. No text, no chevrons in table, no objects inside table data cells. Keep fields visually spacious for later prices and quantities. An optional tiny monitor icon at far left of A/B title strip and tiny phone icon at far left of C/D is okay, each very small.
Background and edges: softly misty airfield at the very outer margins, restrained little triangular waypoints and sparkles, a couple tiny blue loot cases outside card boundaries near bottom corners. Header hero is the only large group illustration. Outer margin details low contrast. Do not add shields or terms/conditions panels to this compact design.
BOTTOM y2990..3150: mostly blank for only a short unit/source note later; keep free of banners. Nothing else below.
Critical constraints: preserve exact 3:4 ratio and full framing; all 4 cards with SIX EMPTY ROWS each; remain a text-free base with no added title, no price numerals, no Chinese, no letters, no fake writing, no logo wordmark, no watermark. No lower notices panel, no CTA, no QR code, no contact information, no mockup or perspective. Do not draw the sticker sheet as a sheet. Output one flat high-resolution PNG.
```

