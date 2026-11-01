import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.dirname(dir);
const c={ink:'#203f52',paper:'#fffdf5',blue:'#eaf3f7',edge:'#b9cdd7',gold:'#d6a252',cream:'#fbefd9',mint:'#ddf0e9'};
const rect=(x,y,w,h,fill,rx=22,stroke='none')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="3"/>`;
const line=(x1,y,x2)=>`<path d="M${x1} ${y}H${x2}" fill="none" stroke="${c.edge}" stroke-width="2"/>`;
const tx=(x,y,t,size=35,fill=c.ink)=>`<text x="${x}" y="${y}" font-family="Microsoft YaHei UI" font-size="${size}" fill="${fill}" dominant-baseline="central">${t}</text>`;
const chev=(x,y,color=c.gold)=>`<path d="M${x-13} ${y-12}l13 12 -13 12m28 -24l13 12 -13 12" fill="none" stroke="${color}" stroke-width="4" stroke-linecap="round"/>`;
async function save(name,w,h,b,a){for(const[suffix,extra]of[['template',''],['layout',a]]){const s=`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">${rect(0,0,w,h,c.paper,0)}${b}${extra}</svg>`;await fs.writeFile(path.join(dir,`${name}-${suffix}.svg`),s);await sharp(Buffer.from(s)).png().toFile(path.join(dir,`${name}-${suffix}.png`));}}
let p=rect(90,520,550,88,c.ink)+rect(90,660,1420,100,c.blue,30);
p+=rect(90,800,1420,650,'#fff',25,c.edge)+rect(90,800,1420,90,c.ink,20)+rect(90,840,1420,50,c.ink,0);
for(let i=0;i<4;i++){const y=890+i*140;p+=rect(93,y,1414,140,i===3?c.cream:i%2===0?c.blue:'#fff',0)+chev(786,y+70);if(i)p+=line(110,y,1490);}
p+=rect(90,1520,1420,280,c.cream,30,c.gold);
p+=rect(90,1860,1420,84,c.ink)+rect(90,1960,675,420,'#fff',25,c.edge)+rect(835,1960,675,420,'#fff',25,c.edge);
p+=rect(90,2430,1420,84,c.ink);
for(const x of [90,455,820,1185])p+=rect(x,2535,325,350,c.blue,25,c.edge);
p+=rect(90,2935,1420,84,c.ink)+rect(90,3040,1420,420,'#fff',25,c.edge)+rect(92,3042,148,416,c.blue,22)+line(258,3140,1480)+line(258,3300,1480);
p+=rect(390,3510,820,60,c.blue,30);
let a=tx(95,110,'兴安电竞',40)+tx(95,280,'嘉豪 / 物资单',62)+tx(95,430,'航天专属 · 高标准护航',34)+tx(915,300,'已确认的航天清图插画',39)+tx(150,565,'平台 · 模式',38,'#fff')+tx(235,710,'进图 → 清图 → 装包 → 撤离',37);
a+=tx(315,845,'订单价格',36,'#fff')+tx(1010,845,'带出物资',36,'#fff');
for(let i=0;i<4;i++)a+=tx(310,960+i*140,'价格 r',58)+tx(1080,960+i*140,'物资 w',58);
a+=tx(130,1610,'护盾',40)+tx(350,1600,'嘉豪保障 · 每炸一把',48)+tx(350,1680,'保底 +100w / +60w',63)+tx(350,1750,'当局物资全送，不计订单累计',35);
a+=tx(145,1902,'清图说明',40,'#fff')+tx(900,1902,'20分钟内，任一方式完成',32,'#fff');
a+=tx(140,2040,'拉闸清图',43)+tx(140,2180,'保留拉闸附加条件，正文3行',32)+tx(784,2170,'或',36)+tx(885,2040,'击杀清图',43)+tx(885,2180,'陪玩单局合计击杀 8 名敌方干员',30);
a+=tx(145,2472,'嘉豪标准',40,'#fff')+tx(920,2472,'任一条件成立即炸单',32,'#fff');
for(const [i,x]of[90,455,820,1185].entries()){a+=tx(x+95,2630,['秒表','队友','背包','警示'][i],36)+tx(x+35,2770,['20分钟内清图','丢包 / 双倒兜底','40格 / 30格','撤离失败'][i],27);}
a+=tx(145,2977,'下单须知',40,'#fff');
for(const [y,t]of[[3090,'地图选择'],[3220,'房卡保障'],[3380,'物资与售后']])a+=tx(260,y,t,34)+tx(575,y,'按规则分组，文字垂直居中',34);
await save('poster',1600,3600,p,a);
await save('cover',2560,1440,rect(130,1090,1130,135,c.ink,60),tx(145,210,'兴安电竞',62)+tx(145,505,'嘉豪',165)+tx(145,780,'物资单',185)+tx(188,1155,'20分钟清图 · 高标准护航',59,'#fff')+tx(1460,550,'已确认主题插画',73));
let s=rect(120,650,2160,180,c.blue,30,c.edge),sa=tx(125,190,'嘉豪 / 物资单',108)+tx(1330,265,'蜂医 · 灰鼠 · 清图路线',57)+tx(205,717,'航天专属 · 20分钟清图 · 高标准护航',55)+tx(600,788,'进图 → 清图 → 装包 → 撤离',36);
for(const [ci,x,y]of[[0,120,880],[1,1250,880],[2,120,1650],[3,1250,1650]]){const mode=ci%2?c.mint:c.cream;s+=rect(x,y,1030,720,'#fff',25,c.edge)+rect(x,y,1030,100,mode,25)+rect(x,y+100,1030,80,c.ink,0);sa+=tx(x+80,y+50,['端游 · 绝密','端游 · 机密','手游 · 绝密','手游 · 机密'][ci],48);for(let i=0;i<4;i++){const ry=y+180+i*135;s+=rect(x+3,ry,1024,135,i===3?mode:i%2===0?c.blue:'#fff',0)+chev(x+500,ry+67.5);if(i)s+=line(x+20,ry,x+1010);sa+=tx(x+140,ry+67.5,'价格 r',52)+tx(x+660,ry+67.5,'物资 w',52);}}
s+=rect(120,2420,2160,630,'#fff',35,c.edge)+rect(190,2570,900,180,c.cream)+rect(1310,2570,900,180,c.mint)+rect(660,3110,1080,46,c.blue,20);
sa+=tx(235,2495,'嘉豪保障 · 每炸一把保底加码',66)+tx(270,2660,'绝密 +100w',76)+tx(1380,2660,'机密 +60w',76)+tx(205,2830,'四项炸单条件的简洁说明',40)+tx(205,2910,'背包要求随模式区分；条件与保障就近',37)+tx(205,2990,'当局物资全送，不计订单累计',44);
await save('social',2400,3200,s,sa);
await fs.writeFile(path.join(dir,'layout-spec.json'),JSON.stringify({stage:'pre-generation-guide',poster:{w:1600,h:3600,exportW:3200,exportH:7200,rows:4,columns:2,header:{titleSafe:[90,80,550,420],hero:[750,30,765,610],platform:[90,520,550,88]},flow:[90,660,1420,100],table:{header:[90,800,1420,90],rowEdges:[890,1030,1170,1310,1450]},guarantee:[90,1520,1420,280],clearance:{header:[90,1860,1420,84],cards:[[90,1960,675,420],[835,1960,675,420]],relation:'or'},failure:{header:[90,2430,1420,84],cards:[90,455,820,1185].map(x=>[x,2535,325,350]),relation:'or'},notes:{header:[90,2935,1420,84],bounds:[90,3040,1420,420],rowEdges:[3040,3140,3300,3460]},footer:[390,3510,820,60]},cover:{w:2560,h:1440,exportW:3200,exportH:1800},social:{w:2400,h:3200,rowsPerCard:4,cardTops:[880,1650],cards:[120,1250],guarantee:[120,2420,2160,630],noOrderNotes:true},note:'正式文字待底图确认后添加，届时按实图边界重新定位。'},null,2));
const sf=path.join(root,'campaign-state.json'),state=JSON.parse(await fs.readFile(sf,'utf8'));
state.approvals.illustration={...state.approvals.illustration,status:'approved',basis:'用户在嘉豪主题插画交付后回复：继续'};
state.approvals.background={status:'in-progress',basis:'按四档价目、保障、两种清图方式、四项炸单条件及三组须知规划底图'};
state.stage='background-in-progress';state.revision='background-v1';state.nextStep='提交四张价目、封面和简洁宣发的无字底图确认，确认后填矢量文案';await fs.writeFile(sf,JSON.stringify(state,null,2));
console.log('Created poster, cover and social guides; illustration approval recorded.');
