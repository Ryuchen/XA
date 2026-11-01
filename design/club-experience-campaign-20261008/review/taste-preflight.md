# design-taste-frontend：海报排版适用性审查

本任务为已确认手绘底图上的静态海报文字优化。设计判断：面向三角洲玩家的促销海报，采用二次元手绘优惠券美术与清晰的中文信息层级。DESIGN_VARIANCE=4，MOTION_INTENSITY=0，VISUAL_DENSITY=5。无前端框架或新设计系统。

已审计现有成图，优先改变字体、字重、列宽和间距。配色、三角色、卡片数量、艺术标题和底图延续前阶段确认。暖金与薄荷青是已确认的绝密/机密语义色，按品牌保留规则适用；不把单一强调色、三列禁令等网页默认值机械套用到已确认价目海报。品牌标志沿用确认稿，未增添新图标。

以下逐条检查技能第14节；网页交互专属项明确标为不适用。最终成图数据与空间检查见visual-qa.md和final-layout-audit.json。

- [x] 已审查并按本轮海报及已确认品牌约束落实：**Brief inference** declared (Section 0.B one-liner)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Dial values** explicit and reasoned from the brief, not silently using baseline?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Design system** chosen from Section 2 if applicable, or aesthetic labeled honestly?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Redesign mode** detected and audit performed (if applicable, Section 11)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**ZERO em-dashes (`—`) anywhere on the page.** Headlines, eyebrows, pills, body, quotes, attribution, captions, buttons, alt text. Zero. (Section 9.G - non-negotiable.)
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Page Theme Lock**: ONE theme (light, dark, or auto) for the whole page. No section flips to inverted mode mid-page (Section 4.11)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Color Consistency Lock**: one accent color used identically across all sections (Section 4.2)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Shape Consistency Lock**: one corner-radius system applied consistently (Section 4.4)?
- [x] 不适用或不存在于本静态海报：**Button Contrast Check**: every CTA text is readable against its background (no white-on-white, WCAG AA 4.5:1)?
- [x] 不适用或不存在于本静态海报：**CTA Button Wrap**: no CTA label wraps to 2+ lines at desktop?
- [x] 不适用或不存在于本静态海报：**Form Contrast Check**: form inputs, placeholders, focus rings, labels all pass WCAG AA against the section background?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Serif discipline**: if a serif is used, it is NOT Fraunces or Instrument_Serif (or it is, with explicit brand justification)? Different serif from your previous project?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Premium-consumer palette check**: if the brief is premium-consumer (cookware / wellness / artisan / luxury), the palette is NOT the AI-default beige+brass+oxblood+espresso family? Different family from your previous premium-consumer project?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Italic descender clearance**: every italic word with `y g j p q` has `leading-[1.1]` min + `pb-1` reserve?
- [x] 不适用或不存在于本静态海报：**Hero fits the viewport**: headline ≤ 2 lines, subtext ≤ 20 words AND ≤ 4 lines, CTA visible without scroll, font scale planned around image?
- [x] 不适用或不存在于本静态海报：**Hero top padding**: max `pt-24` at desktop, hero content does not float halfway down the viewport?
- [x] 不适用或不存在于本静态海报：**Hero stack discipline**: max 4 text elements in hero (eyebrow OR brand strip, headline, subtext, CTAs)? No tiny tagline below CTAs, no trust micro-strip in hero?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**EYEBROW COUNT (mechanical)**: count instances of `uppercase tracking` micro-labels above section headlines across all components. Count ≤ ceil(sectionCount / 3)? Hero counts as 1.
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Split-Header Ban**: no "left big headline + right small explainer paragraph" pattern as a section header (vertical stack instead)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Zigzag Alternation Cap**: no 3+ consecutive sections with the same image+text-split layout?
- [x] 不适用或不存在于本静态海报：**No Duplicate CTA Intent**: no two CTAs with the same intent ("Get in touch" + "Let's talk" both on page = Fail)?
- [x] 不适用或不存在于本静态海报：**Logo wall = logo only**: no industry / category labels printed below logos?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Bento Background Diversity**: at least 2-3 bento cells have real visual variation (image, gradient, pattern), not all white-on-white text cards?
- [x] 不适用或不存在于本静态海报：**"Used by / Trusted by" logo wall** lives UNDER the hero, not inside it, uses REAL SVG logos (Simple Icons / devicon) or generated SVG marks, NOT plain text wordmarks?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Copy Self-Audit**: every visible string re-read, no grammatically-broken or AI-hallucinated phrases ("free on its past" type) shipped?
- [x] 不适用或不存在于本静态海报：**Motion motivated**: every animation can be justified in one sentence (hierarchy / storytelling / feedback / state transition), no GSAP-for-show?
- [x] 不适用或不存在于本静态海报：**Marquee max-one-per-page**: no two horizontal marquees on the same page?
- [x] 不适用或不存在于本静态海报：**Navigation on ONE line** at desktop, height ≤ 80px?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Section-Layout-Repetition** check: no two sections share the same layout family (at least 4 different families across 8 sections)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Bento has rhythm AND exact cell count** (N items → N cells, no empty cells in middle or at end)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Long lists use the right UI component** (not default `<ul>` with `divide-y` for > 5 items - see Section 4.9 alternatives)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Real images used** (gen-tool first, then Picsum-seed, then explicit placeholder slots) - NO div-based fake screenshots, NO hand-rolled decorative SVGs, NO pure-text minimalism?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No pills/labels overlaid on images** (no `Plate · Brand`, no `Field notes - journal`)?
- [x] 不适用或不存在于本静态海报：**No photo-credit captions as decoration** (`Field study no. 12 · Ines Caetano`)?
- [x] 不适用或不存在于本静态海报：**No version footers** (`v1.4.2`, `Build 0048`) on marketing pages?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No micro-meta-sentences** under eyebrows ("Each of these is a feature we ship today...")?
- [x] 不适用或不存在于本静态海报：**No decoration text strip at hero bottom** (`BRAND. MOTION. SPATIAL.`)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No floating top-right sub-text** in section headings?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No scoring/progress bars with filled background tracks** as comparison visuals?
- [x] 不适用或不存在于本静态海报：**No locale / city-name / time / weather strips** unless brief is genuinely globally-distributed or place-focused?
- [x] 不适用或不存在于本静态海报：**No scroll cues** (`Scroll`, `↓ scroll`, `Scroll to explore`)?
- [x] 不适用或不存在于本静态海报：**No version labels in hero** (V0.6, BETA, INVITE-ONLY) unless the brief is a launch?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No section-numbering eyebrows** (`00 / INDEX`, `001 · Capabilities`, `06 · how it works`)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No decorative dots** (zero by default, only for real semantic state)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No `border-t` + `border-b` on every row** of long lists / spec tables?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Content density** sane: no 20-row data tables, no fake-precise specs without justification, ≤ 25-word sub-paragraphs by default?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Quotes ≤ 3 lines** of body, attribution clean (no em-dash)?
- [x] 不适用或不存在于本静态海报：**Motion claimed = motion shown**: if `MOTION_INTENSITY > 4`, page actually animates, not just claimed?
- [x] 不适用或不存在于本静态海报：**GSAP sticky-stack / horizontal-pan** implemented per Section 5.A / 5.B canonical skeleton (`start: "top top"`, `pin: true`, correct scrub)?
- [x] 不适用或不存在于本静态海报：**No `window.addEventListener('scroll')`** - using Motion `useScroll()` / ScrollTrigger / IntersectionObserver / CSS scroll-driven animations only?
- [x] 不适用或不存在于本静态海报：**Reduced motion** wrapped for everything `MOTION_INTENSITY > 3`?
- [x] 不适用或不存在于本静态海报：**Dark mode** tokens defined and tested in both modes?
- [x] 不适用或不存在于本静态海报：**Mobile collapse** explicit (`w-full`, `px-4`, `max-w-7xl mx-auto`) for high-variance layouts?
- [x] 不适用或不存在于本静态海报：**Viewport stability**: `min-h-[100dvh]`, never `h-screen`?
- [x] 不适用或不存在于本静态海报：**`useEffect` animations** have strict cleanup functions?
- [x] 不适用或不存在于本静态海报：**Empty / loading / error** states provided?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Cards omitted** in favor of spacing where possible?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**Icons** from an allowed library only (Phosphor / HugeIcons / Radix / Tabler), no hand-rolled SVG paths?
- [x] 不适用或不存在于本静态海报：**Motion** isolated in client-leaf components with `'use client'` at the top, memoized?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**No AI Tells** from Section 9 (Inter as default, AI-purple, three-equal cards, Jane Doe, Acme, "Quietly in use at")?
- [x] 不适用或不存在于本静态海报：**Core Web Vitals** plausibly hit (LCP < 2.5s, INP < 200ms, CLS < 0.1)?
- [x] 已审查并按本轮海报及已确认品牌约束落实：**One design system** per project (no Material + shadcn mixed)?

具体排版落实：正文全部使用MiSans命名字重；只有主价格/保底为Bold，解释Regular，分类标题Semibold。12个广告档位按模式、费用、保底列对齐；条件不省略；正文按房卡、背包、左轮、地图分组；无填充口号、虚构价格、装饰性英文或破折号。
成图保留浅色已确认主题。交付查看页只承载文件与预览，不作为另建营销站点，未新增网站框架或动态效果。
