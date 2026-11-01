import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const read=async p=>JSON.parse((await fs.readFile(path.join(root,p),'utf8')).replace(/^\uFEFF/,''));
const state=await read('campaign-state.json'),audit=await read('review/typesetting-audit.json');
if(!audit.frequencyScopeConfirmed||audit.shrinking.length||audit.overlaps.length||audit.propCollisions.length)throw Error('Final geometry audit did not pass.');
if(!audit.coverAudit||audit.coverAudit.shrinking.length||audit.coverAudit.overlaps.length||audit.coverAudit.forbiddenCopy.length)throw Error('Cover audit did not pass.');
if(state.approvals.decorativeProps.status!=='approved')throw Error('New artwork not approved.');
state.stage='visual-qa-passed';
state.revision='tazhu-sand-r2';
state.nextStep='已完成：补齐16:9封面；封面、端游、手游及四档广告共四张，正式输出通过复检并重新打包。';
state.backgroundMethod='按用户要求，通过内置image_gen将原雾蓝环境编辑为巴克什黄沙色。保留新编辑原图809×1944，构图归一化为809×1942后裁取页头、边景和页尾，合成809×2740详细页及1086×1448广告背景；正文区用浅沙色矢量卡片。道具作为独立原生透明PNG嵌入SVG，文字按目标尺寸由矢量渲染。';
state.backgrounds={poster:'assets/backgrounds/poster-tazhu-sand-v1.png',social:'assets/backgrounds/social-tazhu-sand-v1.png',cover:'assets/backgrounds/cover-tazhu-sand-v1.png'};
state.approvals.cover={status:'approved-reference',basis:'用户指出缺少封面；直接复用本轮已确认黄沙美术与三幻神/保险贴图，只新增封面矢量排版。'};
state.plannedDeliverables=['塔主大人 · 16:9黄沙封面','端游 · 绝密完整价目与规则','手游 · 绝密完整价目与规则','端游 / 手游 · 3:4 四档价目广告'];
state.cover={source:'01-illustrations/tazhu-sand-background-v1.png',nativeHeroCrop:{width:809,height:390},backgroundCanvas:{width:1672,height:941},textCenterX:326,content:'品牌、玩法、端游/手游、绝密、刷卡开保及累计物资保底'};
state.approvals.seriesStyle.paletteRevision={status:'user-directed',basis:'用户要求：底图背景颜色应该改成和巴克什的黄沙一致的颜色；按明确修改指令完成环境与底纹配色。'};
state.approvals.background.paletteRevision={status:'user-directed',basis:'本轮仅调整色彩及环境色调，沿用已确认分区和排版；用户已明确授权此修改。',overview:'review/background-overview-sand-v1.png'};
state.backgroundReview='review/background-overview-sand-v1.png';
state.sandBackground={provider:'built-in image_gen',file:'01-illustrations/tazhu-sand-background-v1.png',nativeWidth:809,nativeHeight:1944,compositionCoordinates:{width:809,height:1942},prompt:'01-illustrations/prompts.md',editTarget:'assets/backgrounds/poster-gold.png'};
state.layoutInvariants.push('三幻神特别奖励整单合计只计算一次；同一红物资不叠加6格与9格奖励');
state.layoutInvariants=[...new Set(state.layoutInvariants)];
state.visualQAStatus='passed';
state.visualQABasis='新增封面实际PNG和390px手机预览已检查：品牌、标题、模式与卖点在左侧安全文字区居中，主角色及道具完整，正文没有须知或结算例外；标题与模式条、卖点票签留白修正后重新检查。原三张黄沙海报沿用已通过版本，缩字、文本重叠和文图碰撞均为0。';
await fs.copyFile(path.join(root,'review/typesetting-overview.png'),path.join(root,'review/final-overview-sand-r2.png'));
state.finalOverview='review/final-overview-sand-r2.png';
state.exportDimensions={cover:{width:3200,height:1800},detailed:{width:3200,height:10838},social:{width:2400,height:3200}};
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
const qaPath=path.join(root,'review/visual-qa.md');
let qa=await fs.readFile(qaPath,'utf8');
if(!qa.includes('## tazhu-decorated-r1 · 最终通过'))qa+=`
## tazhu-decorated-r1 · 最终通过

- 用户确认依据：9格红只加188w；三幻神“整单一次”；原底图“确认，继续填字”；本轮新贴图“确认，加入海报”。以下为最终版检查记录，上方条目为历史草稿。
- 实际最终PNG：\`output/png/desktop-absolute.png\`、\`output/png/mobile-absolute.png\`、\`output/png/social.png\`，均已打开检查整图。
- 手机检查：三张\`review/phone-390/*.png\`来自当前实际输出；详细页正文有效字号29，390px宽时约13.98px，无暗中缩字。普通说明保持常规字重，标题、主金额和决定性条件为真实粗体。
- 重点局部：\`review/details/*-prices.png\`、\`*-rewards.png\`、\`*-rules.png\`；核查最长金库卡名称与贴图、最大3288w、+1088w、9格红188w、击杀≥4且收益＞588w及末条规则。金额数字与r/w分列定位，单位未拆行。
- 四档价格、保底、卡片/保险次数与源数据逐项一致；摸到三幻神增加对应保底，整单合计只计算一次。红成功带出增加保底，不限制撤离结果；同一9格红不叠加6格奖励。
- 套餐权益旁加入钥匙卡、沙色保险和普通大保险；三幻神位于对应奖励行；须知侧加入灰鼠盾牌。道具仅辅助理解，套餐数量均由矢量文字显示。广告扣除右侧保险插画后居中排金额，底部展示三幻神组合。
- 图文检查：人物脸、鼠耳、手和道具轮廓完整；标题和正文未侵入主体；插画与文案有可见间隙。辅助边界检查：缩字0、文本重叠0、贴图/文字碰撞0；人工整图和局部检查通过。
- 本轮修正：奖励面板增高190px，三幻神每行120px，规则与页尾下移；整单一次说明CY=1648、红奖励标题CY=1701，拉开两个语义组间距。修正后仅重渲染两张详细页，并重新查看受影响PNG和局部。
- 分辨率：详细页3200×10838，广告2400×3200；复用原始人物/场景底图809×1942，新透明道具图集1536×1024。背景与道具为内嵌位图，正文和艺术字为矢量；不将导出尺寸称为插画原生分辨率。
- 最终文件：PNG、保留正文文字的SVG、转曲SVG、业务数据、贴图原图/独立贴图、生成提示词及查看页。打包仅复用本次已检查页面；若页面指纹变化须重新查看。
`;
await fs.writeFile(qaPath,qa);
if(!qa.includes('## tazhu-sand-r1 · 最终通过')){
 qa+=`
## tazhu-sand-r1 · 最终通过

- 修改依据：用户要求底图颜色与巴克什黄沙一致。色彩修改已有明确授权，原价格、权益、规则及三幻神整单一次的确认继续有效。
- 内置image_gen将原空白手绘美术编辑为沙金、土黄、暖米色环境；原图保存在\`01-illustrations/tazhu-sand-background-v1.png\`，原生809×1944。主角色的身份、姿势与前景道具沿用，环境呈现沙色建筑、黄沙与棕榈；没有新增业务文字。
- 重建三张背景并渲染三张PNG，逐张查看\`output/png/desktop-absolute.png\`、\`output/png/mobile-absolute.png\`、\`output/png/social.png\`和三张\`review/phone-390/*.png\`。深蓝标题栏、文字和金色金额在浅沙色卡片上保持清晰；奖励与须知局部图也采用当前PNG检查。
- 颜色：纸底#FFF0D3至#F1D19A，卡片#FFF8E8，暖砂边框#C8AE7C，深蓝正文#213F53，奖励金额#A96D26。边景、页头和页尾同属黄沙色系，不再使用雾蓝环境。
- 本轮复检修正：页尾单位解释落在暖色环境上时对比不足，因此加浅沙色圆角底签；修改后重新渲染全部三张并查看手机图，单位文字可辨、没有遮住主体。
- 字号、间距和数字列沿用已通过的装饰版；四档价目、卡片与保险次数、≥/＞/且以及红物资和三幻神奖励口径不变。辅助检测仍为缩字0、文本重叠0、贴图/文字碰撞0。
- 最终尺寸：两张详细页3200×10838，3:4广告2400×3200。正文矢量渲染、场景与道具内嵌位图；导出尺寸不等于原图绘制尺寸。源图、透明贴图、提示词、数据和可编辑/转曲SVG随包保留。
`;
 await fs.writeFile(qaPath,qa);
}
console.log('Recorded final visual QA and approval state.');
if(!qa.includes('## tazhu-sand-r2 · 补齐封面，最终通过')){
 qa+=`
## tazhu-sand-r2 · 补齐封面，最终通过

- 修改依据：用户指出“缺少封面”。补齐16:9封面，复用本轮已确认的黄沙色人物、环境、三幻神和沙色保险贴图；没有新生成美术。
- 封面实际文件：\`output/png/cover.png\`，3200×1800；同时查看\`review/phone-390/cover.png\`，390×219。已检查整幅画面及标题、模式条、卖点票签和道具相邻区。
- 所属文字区x=80..572，统一中线x=326；品牌图标与名称按实际可见宽度整体居中，艺术标题按字形轮廓宽度居中，模式与两条卖点也沿同一中线。右侧人物脸、鼠耳、手、医疗无人机和物资箱完整，文案不侵入主体。
- 复检修正：两行标题上移20px，拉开与模式条的间距；模式和卖点有效字号增至52；卖点增加浅沙色票签，最终卡片y=636、高139，文字顶/底内边距约14px，组间自然换行。修正后只重渲染封面，再查看实际PNG与手机图。
- 封面仅展示兴安电竞、塔主大人、端游/手游、绝密、刷卡开保与累计物资保底；没有下单须知、注意事项、补偿或结算例外。所有完整规则仍保留在原两张详细页及\`content.json\`。
- 辅助检查：封面文本重叠0、图文碰撞0、暗中缩字0；三个正文组可见中心均为326。艺术标题与背景主体、描边/阴影的间隙另经实际成图人工检查通过。
- 分辨率：封面背景画布1672×941，页头插画来自809×390原始裁片；文字直接以矢量在3200×1800输出。插画仍为内嵌位图，未称原生3200绘制。
- 最终交付补齐四张：封面、端游、手游和3:4广告；新增封面的PNG、可改文字SVG和转曲SVG、背景及构图脚本随新版包保留。其他三张内容未变，打包采用其已有已检查缓存。
`;
 await fs.writeFile(qaPath,qa);
}
