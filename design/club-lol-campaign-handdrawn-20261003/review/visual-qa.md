# 第一阶段插画检查 · art-r1

检查日期：2026-10-03。内置 image_gen 生成，原生尺寸均为1536×1024。对应PNG已复制进项目，完整提示词和生成来源保存在source/generation-prompts.json。

## 检查证据

- 原生生成图片：01-ez-lux-duo、02-ez-lux-victory均已打开查看。
- 奶白底合成总览：review/illustration-overview-r1.png，1480×650，已打开查看。
- 双栏缩略预览：review/illustration-overview-phone-r1.png，780px宽，每张插画约390px可用横向空间，已打开查看。此项是插画识别检查，不能替代后续完整海报的390px手机验收。
- 透明通道与真实原生尺寸：review/illustration-metadata-r1.json。
- 业务源数据结构检查：10栏目、30唯一pageId、contentVerified=true、全部10项原始歧义已按用户答复解决。黑铁40/段、外服铂金4到翡翠4为588/大段、外服第一档0到200分、维护800到1000分均已复核。

## 逐块结论

| 区块 | 结论 | 证据与后续要求 |
| --- | --- | --- |
| EZ角色 | 通过当前插画阶段 | 金发、蓝眼、额头护目镜、蓝色探险服、金色蓝宝石护手可辨识；Q版手绘与原系列一致。 |
| 拉克丝角色 | 通过当前插画阶段 | 金发、蓝眼、蓝白铠甲、双翼光杖可辨识；人物接近等体量。 |
| 01人物与道具边界 | 通过；保留布局安全边界要求 | 身体、双手、脚、发型与光杖完整。原图光杖顶端和右侧留白紧（alpha≥128 bounds：x69到1526，y6到1015）；合成总览已有充分外围留白。后续放置必须留边距，不能按原图边沿顶贴。 |
| 02人物与道具边界 | 通过；保留布局安全边界要求 | 人物全身和胜利水晶完整。alpha≥128 bounds：x91到1499，y15到1007；后续缩放后留外围空间。 |
| 透明边缘 | 通过 | 两张均RGBA，分别有573748/598931个alpha=0像素。奶白底合成无黑色底板或暗色矩形；蓝色魔法柔光保持透明，不影响角色边界。 |
| 手绘统一性 | 通过当前插画阶段 | 人物、护手、光杖、水晶与小光效均为同类动漫手绘，未混入摄影/3D背景。 |
| 旧玩法资产移除 | 通过 | 无蜂医、灰鼠、医疗无人机、战术物资、三角洲场景。 |
| 文字与业务承诺 | 通过 | 原生插画无文案、价格、品牌、联系方式或二维码。胜利水晶为美术意象，不添加胜利保障。 |
| 缩略图识别 | 通过当前插画阶段 | 两人身份与动作在每栏约390px宽时清楚可辨。 |

## 阶段状态

插画待用户确认，确认后才能进入结构稿与无字底图。当前无字底图与30张最终成图尚未制作；不将本次插画检查计为成图验收。后续须按技能逐张执行文字区、baseline、单位、上下居中、横向平衡和390px手机预览检查。

业务确认依据：用户答复“是，黑铁价格为40/段，铂金4～翡翠4，588r/大段，其余都是你理解的对的”。


# 第二阶段 · bg-r1

30页结构与无字底图已完成并内部复检。具体逐套结论、边界、修复与预览证据见 [background-qa-r1.md](background-qa-r1.md)。人物阶段已由用户继续确认；底图等待用户确认，未进行正式矢量填字。


## 最终成图 final-r1 · 2026-10-03

结论：10套、30页逐张完成实际PNG整图、390px预览与重点局部检查，可以交付。此结论结合图片目视检查与真实字形边界核验，不以自动检查代替视觉验收。

### 来源及范围

- 仅英雄联盟端游，十栏目各完整价目页、广告页、封面。没有手游价格或历史物资数字。
- 黑铁40r/段、海外铂金4→翡翠4的588r/大段、0–200分6r/分、30%补分、800–1000维护区间、50%/70%胜率和一区加价均按用户答复。
- 完整页保留结算任一项、包天联合前提“死亡≥12且尽力局/SVP”、英雄选用率≥70%、补分阈值、赔付与优惠例外。封面无注意事项；广告无下单须知。

### 检查图及修正

- 实际最终PNG：`output/png/`；390px预览：`review/final/*-390.png`。5张价目检查板、2张广告检查板、2张封面检查板全部由这些实际PNG生成。
- 局部：`01-solo-duo-price-crop-numeric.png`、`10-maintenance-price-crop-numeric.png`检查最长范围金额/四位数与r正间隙、同基线；`05-lp-price-crop-rules.png`、`06-overseas-price-crop-rules.png`检查粗/常规混排与行距；`09-placement-cover-crop-title-art.png`检查人物与标题、各左侧组居中。局部位于`review/final/`。
- 修正：句号孤行、资质中“韩服王者/峡谷前20”拆行、补分加价孤行、代练AP/物理规则拆词；组排广告固定档卡增高；外服与定位广告双行标题重新测量；代练广告套餐改为新秀/招牌各一行；包天广告缩短等义条件，避免末字孤行。
- 原尺寸检查：数值与r可见间距8布局像素，二者共baseline；卡片标题/价格/说明完整组上下留白，表格按真实分隔行中心排字。须知体46px、卡片说明44px，对应390px宽长图预览约15.0px/14.3px。普通规则常规字重，核心条件与标题真Bold700。
- 文字安全区始终避开EZ、拉克丝及主要道具。标题使用Noto Sans SC Black字形路径、奶白描边、模式色错位阴影与轻微字符起伏。人物与环境统一手绘。
- 因正式正文量调整长图面板高度与须知留白，背景r2只复用已确认人物、场景及面板构图；未重新生成美术。

### 逐页结论

| 页面 | 实际PNG / 手机预览 | 版式与内容检查 |
| --- | --- | --- |
| 01-solo-duo-price · 单双 · 完整价目页 | output/png/01-solo-duo-price.png / review/final/01-solo-duo-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 01-solo-duo-advert · 单双 · 广告页 | output/png/01-solo-duo-advert.png / review/final/01-solo-duo-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 01-solo-duo-cover · 单双 · 封面 | output/png/01-solo-duo-cover.png / review/final/01-solo-duo-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 02-flex-price · 组排 · 完整价目页 | output/png/02-flex-price.png / review/final/02-flex-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 02-flex-advert · 组排 · 广告页 | output/png/02-flex-advert.png / review/final/02-flex-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 02-flex-cover · 组排 · 封面 | output/png/02-flex-cover.png / review/final/02-flex-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 03-arena-price · 斗魂 · 完整价目页 | output/png/03-arena-price.png / review/final/03-arena-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 03-arena-advert · 斗魂 · 广告页 | output/png/03-arena-advert.png / review/final/03-arena-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 03-arena-cover · 斗魂 · 封面 | output/png/03-arena-cover.png / review/final/03-arena-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 04-boost-price · 代练 · 完整价目页 | output/png/04-boost-price.png / review/final/04-boost-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 04-boost-advert · 代练 · 广告页 | output/png/04-boost-advert.png / review/final/04-boost-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 04-boost-cover · 代练 · 封面 | output/png/04-boost-cover.png / review/final/04-boost-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 05-lp-price · 打分 · 完整价目页 | output/png/05-lp-price.png / review/final/05-lp-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 05-lp-advert · 打分 · 广告页 | output/png/05-lp-advert.png / review/final/05-lp-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 05-lp-cover · 打分 · 封面 | output/png/05-lp-cover.png / review/final/05-lp-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 06-overseas-price · 外服代练/打分 · 完整价目页 | output/png/06-overseas-price.png / review/final/06-overseas-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 06-overseas-advert · 外服代练/打分 · 广告页 | output/png/06-overseas-advert.png / review/final/06-overseas-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 06-overseas-cover · 外服代练/打分 · 封面 | output/png/06-overseas-cover.png / review/final/06-overseas-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 07-day-price · 联盟包天 · 完整价目页 | output/png/07-day-price.png / review/final/07-day-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 07-day-advert · 联盟包天 · 广告页 | output/png/07-day-advert.png / review/final/07-day-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 07-day-cover · 联盟包天 · 封面 | output/png/07-day-cover.png / review/final/07-day-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 08-haidou-price · 海斗 · 完整价目页 | output/png/08-haidou-price.png / review/final/08-haidou-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 08-haidou-advert · 海斗 · 广告页 | output/png/08-haidou-advert.png / review/final/08-haidou-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 08-haidou-cover · 海斗 · 封面 | output/png/08-haidou-cover.png / review/final/08-haidou-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 09-placement-price · 单双新赛季定位赛 · 完整价目页 | output/png/09-placement-price.png / review/final/09-placement-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 09-placement-advert · 单双新赛季定位赛 · 广告页 | output/png/09-placement-advert.png / review/final/09-placement-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 09-placement-cover · 单双新赛季定位赛 · 封面 | output/png/09-placement-cover.png / review/final/09-placement-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |
| 10-maintenance-price · 维护局 · 完整价目页 | output/png/10-maintenance-price.png / review/final/10-maintenance-price-390.png | 价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过 |
| 10-maintenance-advert · 维护局 · 广告页 | output/png/10-maintenance-advert.png / review/final/10-maintenance-advert-390.png | 核心价目、单位和必要条件明确；首末卡行居中；无下单须知 |
| 10-maintenance-cover · 维护局 · 封面 | output/png/10-maintenance-cover.png / review/final/10-maintenance-cover-390.png | 品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过 |

### 格式与打包一致性

30张PNG尺寸逐一核对；30张转曲SVG不含文字对象，30张可改文字SVG保留正文/数值text对象。范围与campaign-state相符，页面没有未解决文字重叠或越界。PNG SHA-256保存于`review/final/export-checks.json`，打包前再次校验，打包不重新渲染。


## 文案展示增强版 final-r3 · 2026-10-03

结论：本轮10套30张实际PNG、390px手机预览及代表性原尺寸局部检查通过，可以交付。无未解决重叠、容器越界或图文挤压。自动检查与图片目视检查均完成，自动结果不替代视觉结论。

### 两个技能的使用顺序

先按design-taste-frontend的保留品牌重设计流程审视既有页面，记录设计解读、6/1/4（完整价目密度5）配置和信息层级，制作review/presentation-design-r3.html展示稿；随后按xingan-gameplay-posters改成真实字体测量的矢量海报。

浏览器工具不允许访问file协议，展示稿已保存，但未宣称完成浏览器实机截图验收；最终验版采用实际导出的PNG和手机预览。没有用绕过策略的浏览器或间接执行方式。

### 展示改动

- 主价格字号从76增至92布局像素，金额与r共用baseline，可见单位高约72%，清楚间隙8；浅模式色只强调价格和权益。表格仍保持整条深蓝表头与固定r列，不制造假原价或推荐档。
- 资质标题与普通说明分级；代练广告套餐改为新秀/招牌对应金额两行，金额来自content.json的套餐表。
- 定位赛88折、维护7折成为主权益，所需条件保持同卡清晰可读；移除旧票签中穿过文字的虚线。
- 规则按关键条件与支撑说明分组。任一项采用并列标签；且/或采用连接词明确的关系展示；补分条件与百分比成组。图示全部取自已确认文案，不添加新的承诺。
- 封面在左侧实际净文字区居中，主要卖点加模式色底签；定位优惠由条件、下单动作和88折构成三层。定位卖点区使用原底图中476至686的空白区域，平台底板从694开始；按真实空白重分区后复查。封面不展示规则与赔付例外。
- 说明46px、卡片普通正文44px，390px长图预览等效15.0px/14.3px；真实Bold只用于标题、金额、权益及关键条件。

### 检查证据与复查

9张检查板由output/png中的本轮真实PNG生成，覆盖30页；review/final/*-390.png为实际成图手机预览。原尺寸局部覆盖条件签、包天且关系、退款或关系、补分比例、优惠与最长金额。

修正并复查：外服ADC除外、指定物理的断词；封面三行卖点放大后的上下边界；优惠票签穿字虚线。确认标题和副标题左轴、数字与r共baseline、价格整组居中、多行正文左边界、字体混排间距、分隔线距离、首末行与页尾留白通过。人物轮廓和主要道具不被遮挡。

content.json的SHA-256与改版前完全一致：a5ef5469d9ab4c0b31dbfa114a7b000fc4d6d4894f68487e68afa34c9eec8928。范围、价格、单位、比较符、且/或、结算条件及例外保持原确认含义。

### 逐页结论

| 页ID | 检查结论 |
| --- | --- |
| 01-solo-duo-price | 任一项结算用四个并列条件签；资质双行；金额整体居中。 字形边界、手机可读性与整组留白通过。 |
| 01-solo-duo-advert | 任一项结算用四个并列条件签；资质双行；金额整体居中。 字形边界、手机可读性与整组留白通过。 |
| 01-solo-duo-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 02-flex-price | 匹配、胜率匹配、组排对应固定r列；档位和资质分级。 字形边界、手机可读性与整组留白通过。 |
| 02-flex-advert | 匹配、胜率匹配、组排对应固定r列；档位和资质分级。 字形边界、手机可读性与整组留白通过。 |
| 02-flex-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 03-arena-price | 娱乐至顶尖价格卡放大；双男名次与达成频次保留；例外分组。 字形边界、手机可读性与整组留白通过。 |
| 03-arena-advert | 娱乐至顶尖价格卡放大；双男名次与达成频次保留；例外分组。 字形边界、手机可读性与整组留白通过。 |
| 03-arena-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 04-boost-price | 新秀与招牌并列价格；补分条件与30%/60%成组；套餐金额从源表读取。 字形边界、手机可读性与整组留白通过。 |
| 04-boost-advert | 新秀与招牌并列价格；补分条件与30%/60%成组；套餐金额从源表读取。 字形边界、手机可读性与整组留白通过。 |
| 04-boost-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 05-lp-price | T1/T2按分价目；未列价明确；补分条件与费用独立展示。 字形边界、手机可读性与整组留白通过。 |
| 05-lp-advert | T1/T2按分价目；未列价明确；补分条件与费用独立展示。 字形边界、手机可读性与整组留白通过。 |
| 05-lp-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 06-overseas-price | 区服与单位清楚；补分三档条件与30%/60%/100%对应；韩服除外保留。 字形边界、手机可读性与整组留白通过。 |
| 06-overseas-advert | 区服与单位清楚；补分三档条件与30%/60%/100%对应；韩服除外保留。 字形边界、手机可读性与整组留白通过。 |
| 06-overseas-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 07-day-price | 8小时与净胜成组；死亡条件与尽力局/SVP之间的且单独显示；活动限制保留。 字形边界、手机可读性与整组留白通过。 |
| 07-day-advert | 8小时与净胜成组；死亡条件与尽力局/SVP之间的且单独显示；活动限制保留。 字形边界、手机可读性与整组留白通过。 |
| 07-day-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 08-haidou-price | 男女档价目与r列对应；单陪/双陪结算前提、三陪/四陪赔付区分。 字形边界、手机可读性与整组留白通过。 |
| 08-haidou-advert | 男女档价目与r列对应；单陪/双陪结算前提、三陪/四陪赔付区分。 字形边界、手机可读性与整组留白通过。 |
| 08-haidou-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 09-placement-price | 88折与同区可双排双号一起下单的条件独立对应；补净胜或退款的或清楚显示。 字形边界、手机可读性与整组留白通过。 |
| 09-placement-advert | 88折与同区可双排双号一起下单的条件独立对应；补净胜或退款的或清楚显示。 字形边界、手机可读性与整组留白通过。 |
| 09-placement-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |
| 10-maintenance-price | 两种7折前提分别展示；28局价格与保障双行；例外与区服浮动仍可读。 字形边界、手机可读性与整组留白通过。 |
| 10-maintenance-advert | 两种7折前提分别展示；28局价格与保障双行；例外与区服浮动仍可读。 字形边界、手机可读性与整组留白通过。 |
| 10-maintenance-cover | 品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。 |

### 格式与打包

30张PNG尺寸与最终布局一致，30张可编辑SVG保留正文文字对象，30张转曲SVG无text对象。PNG SHA-256见review/final/export-checks.json；打包使用最后检查过的文件，不补渲染。ZIP CRC32、文件数量与包内PNG SHA-256再次核对。
