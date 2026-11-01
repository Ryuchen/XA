import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const parent=path.dirname(root);
const skill=path.join(process.env.USERPROFILE,'.codex/skills/xingan-gameplay-posters');
const modules=process.argv[2];
const sharp=createRequire(import.meta.url)(path.join(modules,'sharp'));
const read=async p=>JSON.parse((await fs.readFile(path.join(parent,p),'utf8')).replace(/^\uFEFF/,''));
for(const dir of ['assets/backgrounds','source','review','output'])await fs.mkdir(path.join(root,dir),{recursive:true});
for(const name of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','pack.ps1'])await fs.copyFile(path.join(skill,'scripts',name),path.join(root,'source',name));
const artSources={dogtag:'你的护不行-绝密计分四档-20260929/assets/盾牌插画.png',firearms:'你的护不行-枪械版通用封面-20260925/枪械收集双人插画.png',revolver:'你的护不行-左轮版-20260929/assets/盾牌左轮插画.png'};
const artwork={};
for(const [id,p]of Object.entries(artSources)){await fs.copyFile(path.join(parent,p),path.join(root,`assets/${id}.png`));const m=await sharp(path.join(root,`assets/${id}.png`)).metadata();artwork[id]={file:`assets/${id}.png`,source:p,nativeWidth:m.width,nativeHeight:m.height,hasAlpha:m.hasAlpha};}
await fs.copyFile(path.join(parent,'你的护不行-左轮版-20260929/assets/backgrounds/paper.png'),path.join(root,'assets/backgrounds/paper.png'));
const dog=await read('你的护不行-绝密计分四档-20260929/content.json');
const gunS=await read('你的护不行-枪械版绝密出红拆枪补充-20260926/玩法数据.json');
const gunC=await read('你的护不行-枪械版机密出红拆枪补充-20260926/玩法数据.json');
const rev=await read('你的护不行-左轮版-20260929/content.json');
const mode=(id,name,tiers,extra={})=>({id,name,accent:id==='secret'?'#987442':'#437D76',tint:id==='secret'?'#F5EEE1':'#E6F0EB',tiers,...extra});
const products=[
 {id:'dogtag',name:'狗牌版',tagline:'行不行？分数说了算！',coverTagline:'狗牌装备价值 · 累计计分',adTagline:'按狗牌显示的装备价值计分',core:'物资保底 ＋ 狗牌积分',settlement:'保底与当前所需分数均达标，才可结单',platforms:['desktop','mobile'],source:'绝密：你的护不行-绝密计分四档-20260929/content.json；机密：Obsidian 01_玩法/你的护不行.md 的2026-09-19现行套餐；本轮确认手游同价同规则。',modes:[
  mode('secret','绝密',dog.tiers,{scoring:dog.scoring,failureScoring:dog.failureScoring,notices:dog.notices}),
  mode('confidential','机密',[{name:'基础版',price:188,guarantee:1088,baseScore:10,failureIncrease:2},{name:'高级版',price:288,guarantee:1688,baseScore:20,failureIncrease:2},{name:'豪华版',price:488,guarantee:2888,baseScore:30,failureIncrease:2}],{scoring:{basis:'打手击杀的干员身份牌显示的装备价值',bands:[{min:60,max:100,points:1},{min:100,max:140,points:2},{min:140,max:null,points:3}],boundary:'左闭右开'},notices:['必须是本单打手击杀的狗牌才计算；打手双倒前，老板击杀狗牌的计入打手。','打手必须提供截图为证。','装备价值分数仅计算撤离成功对局。','撤离失败对局，打手每击杀一人计0.5分，单局最多计2分。该细则与价目行撤离失败分数+2均按原图保留，不新增解释。','只计算老板撤出的对局，包括飞鼠、飞升。']})]},
 {id:'firearms',name:'枪械版',tagline:'满改收集 · 累计带出',coverTagline:'满改收集 · 累计带出',adTagline:'累计带出指定满改枪械',core:'物资保底 ＋ 枪械收集',settlement:'按所选档位累计带出指定满改枪械',platforms:['desktop','mobile'],source:'2026-09-26两难度出红拆枪补充稿的玩法数据.json；本轮确认手游同价同规则。',modes:[gunS,gunC].map((g,i)=>mode(i===0?'secret':'confidential',i===0?'绝密':'机密',g.tiers.map(({name,price,guarantee,count,guns,failureAdd})=>({name,price:parseInt(price),guarantee:parseInt(guarantee),count,guns,failureAdd})),{sharedGameplay:g.sharedGameplay,notices:g.notices}))},
 {id:'revolver',name:'左轮版',tagline:'缴获带出 · 按价值计分',coverTagline:'缴获带出 · 按价值计分',adTagline:'缴获左轮 · 带出计分',core:'物资保底 ＋ 左轮积分',settlement:'保底与当前所需分数均达标，才可结单',platforms:['desktop','mobile'],source:'你的护不行-左轮版-20260929/content.json 与 campaign-state.json r5；本轮沿用双平台同价。',modes:rev.modes.map(m=>mode(m.id,m.name,rev.tiers.map((t,i)=>({...t,price:m.prices[i]})),{bandPoints:m.bandPoints})),scoring:rev.scoring,failureRule:rev.failureRule,notices:rev.notices}
];
const c={brand:'兴安电竞',title:'你的护不行',version:'2026-10-01-r1',platformPricePolicy:'端游 / 手游同价',platforms:[{id:'desktop',name:'端游'},{id:'mobile',name:'手游'}],products,artwork,displayPolicy:{cover:'品牌、玩法、版本、核心卖点、平台与模式；不展示须知或结算例外。',social:'价目与核心玩法摘要；完整现行规则保存在content.json及来源海报，不新增或改写。',gunNames:'保留各档全部指定枪名，避免合并不同档位范围。'}};
await fs.writeFile(path.join(root,'content.json'),JSON.stringify(c,null,2));
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify({skill:'xingan-gameplay-posters',revision:'r1',stage:'layout',contentVerified:true,contentVerificationBasis:'用户点名狗牌、枪械、左轮各封面和广告；本轮确认手游与端游同价，保底、计分/枪数等规则相同。现行数据来自各版最新本地已确认记录。',deliveryScope:{pageIds:products.flatMap(p=>[p.id+'-cover',p.id+'-social']),platforms:['desktop','mobile'],basis:'用户点名三版封面与广告，共六张；三版均端手游通用。'},verifiedPricePlatforms:['desktop','mobile'],priceSourceByPlatform:{desktop:products.map(p=>p.source).join('\n'),mobile:'本轮用户明确：手游也是和端游一样的价格；随后确认同价、保底计分/枪数规则相同。'},approvals:{illustration:{status:'reuse-approved',basis:'复用各版已确认的盾牌、枪械双人、盾牌左轮插画，无新画风或新角色。'},background:{status:'reuse-approved-composition',basis:'沿用左轮r5奶白雾蓝底板、左文右图封面与双难度广告结构；按不同档数和枪名量调整矢量文字区。'}},artwork},null,2));
console.log(JSON.stringify({root,artwork,pages:products.flatMap(p=>[p.id+'-cover',p.id+'-social'])},null,2));
