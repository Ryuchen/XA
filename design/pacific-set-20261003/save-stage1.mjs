import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';

const root=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法`;
const project=path.join(root,'环太平洋-四版价目广告封面-20261003');
const baselineDir=path.join(root,'环太平洋-五出生点豪华降价-20261003');
const legacyDir=path.join(root,'环太平洋-机密任意门80w修订-20260919');
const baseline=JSON.parse(await fs.readFile(path.join(baselineDir,'content.json'),'utf8'));
const legacy=JSON.parse(await fs.readFile(path.join(legacyDir,'双版数据.json'),'utf8'));
const generated=String.raw`C:\Users\chenh\.codex\generated_images\01a0fdaf-d080-7360-8791-13de1e585bc8\exec-5f569f7a-896b-45e5-883d-38b5fd96e41b.png`;
const require=createRequire(import.meta.url);
const sharp=require(String.raw`C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp`);
const clone=v=>structuredClone(v);
const variants=[
  {id:'desktop-absolute',platform:'端游',mode:'绝密',color:'gold',priceReduction:0,guaranteeReduction:0},
  {id:'desktop-secret',platform:'端游',mode:'机密',color:'teal',priceReduction:200,guaranteeReduction:0},
  {id:'mobile-absolute',platform:'手游',mode:'绝密',color:'gold',priceReduction:200,guaranteeReduction:400},
  {id:'mobile-secret',platform:'手游',mode:'机密',color:'teal',priceReduction:220,guaranteeReduction:0}
];
const pendingQuestion='机密两版是否统一2/3/4/5/全点目标，地图仍巴克什/航天基地/核电站，沿用现有机密附加玩法与须知？';
const rulesSecret=clone(legacy.confidential);
delete rulesSecret.maps;
const content={
  brand:baseline.brand,title:baseline.title,date:'2026-10-03',
  subtitle:'累计从不同出生点成功撤离',
  tierNames:clone(baseline.tiers),
  absoluteTargets:clone(baseline.targets),
  confidentialTargets:null,
  tiers:variants.map(v=>({
    ...v,
    targets:v.mode==='绝密'?clone(baseline.targets):null,
    maps:baseline.rules.maps.map((m,i)=>({
      name:v.mode==='机密'?legacy.confidential.maps[i].name:m.name,
      prices:m.prices.map(n=>n-v.priceReduction),
      guarantees:m.guarantees.map(n=>n-v.guaranteeReduction),
      slogan:v.mode==='机密'?legacy.confidential.maps[i].slogan:m.slogan
    })),
    rules:v.mode==='绝密'?Object.fromEntries(Object.entries(clone(baseline.rules)).filter(([k])=>k!=='maps')):clone(rulesSecret)
  })),
  sources:[
    {type:'confirmed-baseline',path:'references/绝密5出生点当前价目.json',basis:'前轮用户已修订三地图豪华为5个不同出生点及豪华售价各减100r；作为端游绝密基准'},
    {type:'confirmed-rules',path:'references/现有绝密机密规则.json',basis:'机密既有核电站、预言家150/300w、任意门80w及488/388w流局规则；等待本轮目标/地图/规则确认'},
    {type:'user',basis:'本轮明确原图绝密档；手游绝密各售价减200r及各保底减400w；端游机密与手游绝密同价；手游机密在此再每档减20r'},
    {type:'user-reply',basis:'本轮明确机密两版基础保底沿用原绝密保底，仅手游绝密减400w'}
  ],
  confirmations:{prices:true,guarantees:true,absoluteTargets:true,confidentialTargetsAndRules:false},
  pendingQuestions:[pendingQuestion],
  social:{platforms:['desktop','mobile'],scope:'四版核心价格、三地图与累计不同出生点撤离卖点，不放下单须知'},
  cover:{platforms:['desktop','mobile'],scope:'品牌、玩法、累计不同出生点撤离卖点、端手游及绝密/机密版本，不放须知或结算例外'}
};
const state={
  projectTitle:'环太平洋 · 四版价目、广告与封面',revision:'stage1-r1',stage:'illustration-awaiting-confirmation',
  deliveryScope:{pageIds:['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret','social','cover'],platforms:['desktop','mobile'],basis:'用户明确要求一套图，并分别给出端游/手游绝密和机密价格映射；技能默认六页范围'},
  verifiedPricePlatforms:['desktop','mobile'],
  priceSourceByPlatform:{desktop:'最新5出生点绝密附件修订价目；端游机密减200r；机密保底由用户本轮明确沿用原绝密',mobile:'本轮手游绝密每档减200r和保底减400w；手游机密减220r，机密保底由本轮明确沿用原绝密'},
  contentVerified:false,
  contentVerificationBasis:'四版售价与保底已核对；机密目标/地图/规则仍待本轮答复，未填正式海报价格',
  approvalHistory:[],
  pendingApprovals:['新蜂医灰鼠探险插画'],
  pendingBusinessQuestions:[pendingQuestion],
  artwork:{illustration:'assets/illustrations/medic-mouse-exploration-r1.png',basis:'技能默认蜂医灰鼠角色与原有环太平洋探索不同出生点、任意门玩法关联',tool:'built-in image_gen',approved:false},
  completedPages:[],externalPublication:false
};
for(const dir of ['assets/illustrations','assets/backgrounds','references','source/prompts','review','output/png','output/svg-outlined','output/svg-editable'])await fs.mkdir(path.join(project,dir),{recursive:true});
await fs.copyFile(generated,path.join(project,state.artwork.illustration));
await fs.copyFile(path.join(baselineDir,'content.json'),path.join(project,'references/绝密5出生点当前价目.json'));
await fs.copyFile(path.join(legacyDir,'双版数据.json'),path.join(project,'references/现有绝密机密规则.json'));
await fs.copyFile(path.join(baselineDir,'references/用户原图.png'),path.join(project,'references/用户原图.png'));
await fs.copyFile(String.raw`C:\Users\chenh\.codex\skills\xingan-gameplay-posters\assets\material-v3\illustrations\01-medic-mouse-loot.png`,path.join(project,'references/已确认蜂医灰鼠角色.png'));
await fs.copyFile(path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\//,'')),'imagegen-prompt.txt'),path.join(project,'source/prompts/illustration-r1.txt'));
await fs.copyFile(new URL(import.meta.url),path.join(project,'source/save-stage1.mjs'));
const metadata=await sharp(path.join(project,state.artwork.illustration)).metadata();
const alpha=await sharp(path.join(project,state.artwork.illustration)).ensureAlpha().extractChannel(3).stats();
state.artwork.nativeSize={width:metadata.width,height:metadata.height,hasAlpha:metadata.hasAlpha,alphaMin:alpha.channels[0].min,alphaMax:alpha.channels[0].max};
if(!metadata.hasAlpha||alpha.channels[0].min!==0)throw new Error('透明alpha核查未通过');
await sharp(path.join(project,state.artwork.illustration)).flatten({background:'#fbf8ef'}).resize({width:1200}).png().toFile(path.join(project,'review/illustration-ivory-r1.png'));
await sharp(path.join(project,state.artwork.illustration)).flatten({background:'#fbf8ef'}).resize({width:390}).png().toFile(path.join(project,'review/illustration-mobile390-r1.png'));
await fs.writeFile(path.join(project,'content.json'),JSON.stringify(content,null,2));
await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2));
const lines=['# 环太平洋 · 四版价目核对','', '售价基准：最新5出生点绝密图。预言家加购费与奖励、流局门槛没有随套餐价差改变。','', '机密两版保底已由用户确认沿用原绝密；仅手游绝密每档减400w。机密撤离目标与既有地图/规则仍待答复，未填正式海报文字。','', '| 地图对应 | 档位 | 端游绝密售价 | 手游绝密售价 | 端游机密售价 | 手游机密售价 | 原绝密/两版机密保底 | 手游绝密保底 |','|---|---|---:|---:|---:|---:|---:|---:|'];
for(let i=0;i<3;i++)for(let j=0;j<5;j++){
 const [da,ds,ma,ms]=content.tiers;
 const orig=baseline.rules.maps[i];
 if(ma.maps[i].prices[j]!==orig.prices[j]-200||ds.maps[i].prices[j]!==ma.maps[i].prices[j]||ms.maps[i].prices[j]!==ds.maps[i].prices[j]-20||ma.maps[i].guarantees[j]!==orig.guarantees[j]-400||ds.maps[i].guarantees[j]!==orig.guarantees[j]||ms.maps[i].guarantees[j]!==orig.guarantees[j])throw new Error('价目映射错误');
 lines.push(`| ${orig.name}${i===2?' / 机密核电站':''} | ${content.tierNames[j]} | ${da.maps[i].prices[j]}r | ${ma.maps[i].prices[j]}r | ${ds.maps[i].prices[j]}r | ${ms.maps[i].prices[j]}r | ${orig.guarantees[j]}w | ${ma.maps[i].guarantees[j]}w |`);
}
await fs.writeFile(path.join(project,'review/四版价目核对.md'),lines.join('\n'));
await fs.writeFile(path.join(project,'review/illustration-qa-r1.md'),`# 第一阶段插画核查\n\n- 版本：stage1-r1，内置ImageGen；原生${metadata.width}×${metadata.height}，透明alpha已验证，原始输出保留。\n- 已查看实际插画：男性蜂医与灰鼠探索地图、定位标记、任意门和医疗无人机与玩法对应；统一二次元手绘，非3D。\n- 两名角色各两只手：蜂医托地图与指向路线；灰鼠持红卡与指门。耳朵、尾巴、背包与门轮廓完整，无明显额外肢体。\n- 无文案、价格、数字、联系方式；地图定位符不代表已确认出生点总量。\n- 已生成奶白底原尺寸比例预览与390px预览；手机可辨认主角、地图与门。\n- 当前待用户确认新插画；未生成无字底图或六张最终价目/宣传成品。\n`);
await fs.writeFile(path.join(project,'review/stage1.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>环太平洋 · 插画确认</title><style>body{max-width:1200px;margin:40px auto;padding:20px;background:#fbf8ef;color:#213f53;font:18px system-ui}img{width:100%;border-radius:24px}a{color:#187c78}h1{font-size:32px}</style><h1>环太平洋 · 第一阶段插画</h1><p>计划六张：端游绝密、端游机密、手游绝密、手游机密、广告、封面。</p><img src="illustration-ivory-r1.png" alt="蜂医与灰鼠探索地图、任意门"><p>新插画待确认；其后制作无字底图并确认，再填入矢量文字。</p><p>机密保底沿用原绝密；仅手游绝密各档保底减400w。机密目标与既有地图规则待答复。</p><a href="四版价目核对.md">查看四版价目核对</a> · <a href="../assets/illustrations/medic-mouse-exploration-r1.png">透明插画原图</a></html>`);
console.log(JSON.stringify({project,stage:state.stage,illustration:state.artwork.nativeSize,priceRows:15,variants:4,pricesChecked:60,guaranteesChecked:60,pendingBusinessQuestions:state.pendingBusinessQuestions},null,2));
