import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const sharp=require(process.argv[2]+'/sharp');
const project=path.resolve(import.meta.dirname,'..');
const content=JSON.parse(fs.readFileSync(path.join(project,'content.json'),'utf8'));
for(const d of ['source/layout-guides','assets/environment','review/backgrounds','assets/backgrounds'])fs.mkdirSync(path.join(project,d),{recursive:true});
const b=(id,label,kind,h,extra={})=>({id,label,kind,h,...extra});
const table=(id,label,rows,columns=3,rowHeight=90,extra={})=>b(id,label,'table',80+rows*rowHeight,{rows,columns,rowHeight,headerHeight:80,...extra});
const notes=(id,label,sourceIndexes,minHeight=200)=>b(id,label,'notes',minHeight,{sourceIndexes,bodyFont:50,lineHeight:68});
const qualification=b('qualification','陪玩资质','cards',310,{cards:4,columns:2});
const common={canvasWidth:1200,margin:64,gap:28,headerHeight:440,footerHeight:180,minBodyFont:50,phoneWidth:390};
const full=[
[b('fixed-tiers','T0 / T1固定起价','cards',170,{cards:2,columns:2}),table('rank-price','段位 × T2 / T3',9,3,96),qualification,notes('settlement','结算、净胜补偿与新秀大师规则',[0,1,2],760)],
[b('fixed-tiers','T0 / T1匹配与组排同价','cards',170,{cards:2,columns:2}),table('match-price','匹配 / 组排 × T2 / T3',4,3,106),qualification,notes('service','售后口径',[0],240)],
[b('tiers','斗魂四档价目','cards',350,{cards:4,columns:2}),table('double-guarantee','双男三档名次要求',3,3,116),notes('mixed','混搭与一护二说明',[3,4],330),notes('arena-rules','斗魂3×6规则与赔付例外',[0,5,6,7,8],660)],
[b('qualification','新秀 / 招牌代练资质','cards',240,{cards:2,columns:2}),table('basic-ranks','黑铁到翡翠：新秀 / 招牌对照',6,3,94),table('diamond-ranks','钻石四段：新秀 / 招牌对照',4,3,94),table('packages','大师套餐：新秀 / 招牌对照',2,3,108),notes('compensation','账号保障与补分规则',[0,2,5],520),notes('designated','指定位置、英雄、选用率',[1,3,4],400),notes('hidden','高隐藏分条件与费用',[6],400)],
[b('qualification','T2性价比 / T1效率与资质','cards',280,{cards:2,columns:2}),table('lp-price','五个分数区间 × T2 / T1',5,3,112),b('minimum','大师 / 宗师 / 王者起接分数','cards',180,{cards:3,columns:3}),notes('designated','直播、位置、英雄指定',[2,3,4,5],410),notes('hidden','隐藏分、补分与第三赛段规则',[6,7,8,9],650),notes('region','一区与高分演员局费用',[0,10],240)],
[table('below-master','大师以下：大段 / 小段',3,3,116),b('diamond','钻石单段与钻四到大师','cards',390,{cards:4,columns:2}),table('above-master','六个分数区间每分报价',6,2,94),notes('payment','支付方式与账号保障',[0,1,2],380),notes('surcharge','补分加价',[3],300),notes('designated','指定位置与英雄条件',[4],400),notes('korea','韩服与800分以上咨询口径',[5,6],240)],
[table('below-diamond','陪玩性质 · 钻石以下',3,3,116),table('above-diamond','陪玩性质 · 钻石以上',3,3,116),table('master-low','代练性质 · 大师100–500分',2,3,116),table('master-high','代练性质 · 大师500–800分',2,3,116),notes('season','S15 / S16段位取高',[0],360),notes('companion','陪玩结算：死亡条件与补足净胜',[1],350),notes('boost','代练指定与结算规则',[2],350),notes('event','500分以上活动前提与客服口径',[3],380)],
[b('female-male','女陪五档 / 男陪四档','parallel-tables',640,{cards:2,columns:2,rowsByCard:[5,4],tableColumns:2,rowHeight:104,headerHeight:104}),notes('settlement','单陪 / 双陪：满足任一项即结算',[1,2,3],390),b('compensation','三陪 / 四陪赔付','cards',330,{cards:2,columns:2}),notes('exceptions','女陪与三陪例外',[0,4],220)],
[b('offer','同区可双排双号一起下单88折','offer',170),table('below-master','大师以下定位赛六档',6,3,106),table('above-master','大师以上定位赛四档',4,3,106),b('new-account','新号到大师：胜率套餐','cards',210,{cards:1,columns:1}),notes('pricing','上赛季段位 / 隐藏分与一区收费',[0,1,2],380),notes('designated','位置 / 闪现 / 英雄指定',[3],330),notes('compensation','胜场不足补偿：或关系',[4],220)],
[b('offers','后续维护 / 三个月维护优惠','cards',360,{cards:2,columns:1}),table('maintenance','28局维护：T1 / T2价格与净胜',4,3,154),b('high-lp','一千分以上T1咨询档','cards',190,{cards:1,columns:1}),notes('offer-terms','特惠赔付与按预算调整',[0,1],440),notes('hidden-region','隐藏分 / 一区与其他区浮动',[2,3],440),notes('designated','原价维护与600分以上指定条件',[4],360)]
];
const adverts=[
[b('tiers','铂金以下四档核心价目','cards',660,{cards:4,columns:2}),b('rank-note','其他段位提示与档位资质','strip',170)],
[b('fixed-tiers','T0 / T1同价档','cards',170,{cards:2,columns:2}),table('match-price','匹配与组排核心价目',4,3,112),b('tiers','陪玩资质简述','strip',140)],
[b('tiers','娱乐 / 技术 / 招牌 / 顶尖','cards',680,{cards:4,columns:2}),b('mode','3×6竞技场模式说明','strip',150)],
[table('core-prices','基础段位：新秀 / 招牌',6,3,95),b('packages','两个大师套餐','cards',220,{cards:2,columns:2})],
[table('core-prices','五个分数区间 × T2 / T1',5,3,114),b('conditions','起接分数与一区必要条件','strip',220)],
[b('core-prices','大师以下代练 / 大师以上打分','cards',560,{cards:2,columns:1}),b('server','适用外服 / 韩服浮动报价','strip',260)],
[b('core-prices','四类包天核心档位','cards',650,{cards:4,columns:2}),b('conditions','大师500分以上活动前提','strip',200)],
[b('female-male','女陪五档 / 男陪四档','parallel-tables',640,{cards:2,columns:2,rowsByCard:[5,4],tableColumns:2,rowHeight:104,headerHeight:104}),b('tiers','男陪 / 女陪档位信息','strip',190)],
[b('offer','同区可双排双号88折','offer',160),b('core-prices','四个代表段位价格与胜场','cards',590,{cards:4,columns:2}),b('pricing','按上赛季段位及隐藏分收费','strip',130)],
[b('offer','两项维护7折活动及必要条件','offer',190),table('maintenance','28局维护：四档核心价目',4,3,132)]
];
const titleLines=[['单双陪玩'],['组排陪玩'],['斗魂陪玩'],['段位代练'],['打分'],['外服代练','/ 打分'],['联盟包天'],['海斗陪玩'],['单双新赛季','定位赛'],['维护局']];
const coverCore=[['从新秀到顶尖','按段位匹配陪玩'],['匹配 / 胜率匹配 / 组排'],['3×6斗魂竞技场','娱乐 / 技术 / 招牌 / 顶尖'],['新秀 / 招牌','按段位选择代练'],['T1效率 · T2性价比'],['外服代练 / 打分'],['8小时包天','陪玩 / 代练'],['女陪 / 男陪','多档位可选'],['同区可双排双号','一起下单88折'],['28局维护','兴安打分满300分 · 后续维护7折']];
const pages=[];
for(let i=0;i<content.sets.length;i++){
 const s=content.sets[i],theme=[2,7].includes(i)?'arena':'rift',accent=[2,7].includes(i)?'#83b6c8':i%2?'#81b9af':'#d5b57a';
 let y=common.headerHeight;
 const blocks=full[i].map(o=>{const block={...o,x:64,y,w:1072,safeInset:28,containerId:s.id+'-price-'+o.id};y+=o.h+28;return block;});
 const h=y+common.footerHeight-28;
 pages.push({id:s.id+'-price',setId:s.id,label:s.name+' · 完整价目页',type:'price',platforms:['desktop'],w:1200,h,ew:3200,eh:Math.round(h*3200/1200),theme,accent,hero:i===0||i===1||i===2||i===7?'duo':'victory',header:{brandSafe:{x:64,y:54,w:580,h:60},titleSafe:{x:64,y:130,w:580,h:200},subtitleSafe:{x:64,y:344,w:580,h:68},heroBox:{x:670,y:76,w:470,h:314}},blocks,footer:{x:0,y:h-170,w:1200,h:170},textSafeAreas:blocks.map(o=>({containerId:o.containerId,x:o.x+28,y:o.y+24,w:o.w-56,h:o.h-48})),notes:['插画与图案不进入价格栏','表头统一整条海军蓝','须知正文50px起，按文案量调整空间','完整价目不省略业务条件与例外']});
 const adBlocks=adverts[i];const available=990;const total=adBlocks.reduce((a,o)=>a+o.h,0)+28*(adBlocks.length-1);const scale=total>available?available/total:1;
 y=430;
 const positioned=adBlocks.map(o=>{const h=Math.round(o.h*scale);const block={...o,x:64,y,w:1072,h,safeInset:28,containerId:s.id+'-advert-'+o.id};y+=h+28;return block;});
 pages.push({id:s.id+'-advert',setId:s.id,label:s.name+' · 广告页',type:'advert',platforms:['desktop'],w:1200,h:1600,ew:2400,eh:3200,theme,accent,hero:[2,7].includes(i)?'duo':'victory',header:{brandSafe:{x:64,y:52,w:580,h:60},titleSafe:{x:64,y:130,w:580,h:172},subtitleSafe:{x:64,y:314,w:580,h:70},heroBox:{x:670,y:66,w:470,h:314}},blocks:positioned,footer:{x:0,y:1480,w:1200,h:120},textSafeAreas:positioned.map(o=>({containerId:o.containerId,x:o.x+28,y:o.y+24,w:o.w-56,h:o.h-48})),notes:['仅核心价目与优惠必要条件，不附下单须知','内容可提炼但不得增加承诺','不把完整价目全量缩小塞进3:4广告']});
 pages.push({id:s.id+'-cover',setId:s.id,label:s.name+' · 封面',type:'cover',platforms:['desktop'],w:1600,h:900,ew:3200,eh:1800,theme,accent,hero:[0,1,2,7].includes(i)?'duo':'victory',titleLines:titleLines[i],coverCore:coverCore[i],copyZones:{brand:{x:74,y:80,w:710,h:70},title:{x:74,y:212,w:710,h:230},core:{x:74,y:476,w:710,h:140},version:{x:114,y:694,w:630,h:80}},heroBox:{x:810,y:270,w:730,h:487},blocks:[],textSafeAreas:[{containerId:s.id+'-cover-brand',x:74,y:80,w:710,h:70},{containerId:s.id+'-cover-title',x:74,y:212,w:710,h:230},{containerId:s.id+'-cover-core',x:74,y:476,w:710,h:140},{containerId:s.id+'-cover-version',x:114,y:694,w:630,h:80}],notes:['所有文案组在各自左侧净文字区横向居中','封面不写注意事项、赔付或结算例外','活动必须显示必要条件，源数据保留完整规则']});
}
const plan={schemaVersion:1,revision:'bg-r1',stage:'background-approval',basis:'十套30页；按已核实文案量制定差异化结构，人物确认后复用，背景待用户确认。',common,pages};
fs.writeFileSync(path.join(project,'layout-plan.json'),JSON.stringify(plan,null,2)+'\n','utf8');
const sceneGuide=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900"><rect width="1600" height="900" fill="#fffbf3"/><path d="M830 0H1600V900H800 Q930 710 830 540Z" fill="#dcebef"/><path d="M0 835Q350 780 640 842T1600 825V900H0Z" fill="#c1d8d2"/><rect x="55" y="55" width="725" height="725" rx="24" fill="#fffbf3"/></svg>');
fs.writeFileSync(path.join(project,'source/layout-guides/cover-scene-guide.png'),await sharp(sceneGuide).png().toBuffer());
const footerGuide=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1536" height="512"><rect width="1536" height="512" fill="#fffbf3"/><path d="M0 0Q320 30 470 230Q300 300 200 512H0Z" fill="#dcebef"/><path d="M1536 0Q1250 80 1150 210Q1300 390 1420 512H1536Z" fill="#dcebef"/><path d="M0 410Q450 325 770 360T1536 410V512H0Z" fill="#c1d8d2"/></svg>');
fs.writeFileSync(path.join(project,'source/layout-guides/footer-scene-guide.png'),await sharp(footerGuide).png().toBuffer());
console.log(JSON.stringify({pages:pages.length,priceHeights:pages.filter(x=>x.type==='price').map(x=>({id:x.id,h:x.h,blocks:x.blocks.length})),adverts:pages.filter(x=>x.type==='advert').map(x=>({id:x.id,blocks:x.blocks.length,bodyEnd:x.blocks.at(-1).y+x.blocks.at(-1).h}))}));
