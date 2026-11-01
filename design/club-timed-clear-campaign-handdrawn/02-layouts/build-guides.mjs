import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.dirname(dir);
const c={ink:'#203F53',paper:'#FFFDF5',blue:'#EAF3F7',edge:'#BCCFDA',gold:'#D4A053',cream:'#FBEFD9',mint:'#DDF0E9'};
const rect=(x,y,w,h,fill,rx=22,stroke='none')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="3"/>`;
const hline=(x,y,w)=>`<path d="M${x} ${y}h${w}" stroke="${c.edge}" stroke-width="2"/>`;
const vline=(x,y,h)=>`<path d="M${x} ${y}v${h}" stroke="${c.edge}" stroke-width="2"/>`;
const text=(x,y,t,size=35,fill=c.ink)=>`<text x="${x}" y="${y}" font-family="Microsoft YaHei UI" font-size="${size}" fill="${fill}" dominant-baseline="central">${t.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}</text>`;
const icon=(x,y,r=38)=>`<circle cx="${x}" cy="${y}" r="${r}" fill="${c.blue}" stroke="${c.edge}" stroke-width="3"/>`;
async function save(name,w,h,b,annotations){for(const [suffix,extra]of[['template',''],['layout',annotations]]){const s=`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">${rect(0,0,w,h,c.paper,0)}${b}${extra}</svg>`;await fs.writeFile(path.join(dir,`${name}-${suffix}.svg`),s);await sharp(Buffer.from(s)).png().toFile(path.join(dir,`${name}-${suffix}.png`));}}

const rowEdges=[880,990,1100,1210,1320,1430,1540],noteEdges=[2670,2780,3040,3410,3650];
let b=rect(90,535,570,85,c.ink,28)+rect(90,665,1420,95,c.blue,24,c.edge)+icon(170,712,32);
b+=rect(90,790,1420,750,c.paper,26,c.edge)+rect(90,790,1420,90,c.ink,23);
for(let i=0;i<6;i++){b+=rect(93,rowEdges[i],1414,110,i===5?c.cream:i%2===0?c.blue:'#FFFFFF',0);if(i>0)b+=hline(93,rowEdges[i],1414);}
b+=rect(90,1580,1420,170,c.cream,26,c.gold)+icon(195,1665,65);
b+=rect(90,1810,1420,85,c.ink,24);
b+=rect(90,1915,690,405,'#FFFFFF',24,c.edge)+rect(820,1915,690,405,'#FFFFFF',24,c.edge);
b+=icon(182,2000,45)+icon(912,2000,45)+icon(800,2140,47);
b+=rect(90,2345,1420,165,c.blue,24,c.edge)+icon(173,2427,44);
b+=rect(90,2560,1420,85,c.ink,24)+rect(90,2670,1420,980,'#FFFFFF',24,c.edge);
for(let i=0;i<4;i++){b+=rect(96,noteEdges[i]+5,184,noteEdges[i+1]-noteEdges[i]-10,c.blue,14)+icon(188,(noteEdges[i]+noteEdges[i+1])/2,42);if(i>0)b+=hline(292,noteEdges[i],1185);}
b+=rect(390,3700,820,60,c.blue,25);
let a=text(90,140,'品牌 / 艺术标题',48)+text(90,265,'限时',96)+text(90,395,'清图单',96)+text(730,255,'蜂医冲刺 + 灰鼠 + 秒表',42)+text(730,325,'完整插画集中在右上',32)+text(120,578,'平台 · 模式 / 航天',36,'#FFFFFF');
a+=text(250,710,'共用服务提示：对应模式航天清图一把',32)+text(205,835,'订单价格',36,'#FFFFFF')+text(1000,835,'清图时限',36,'#FFFFFF');
const sample=[168,388,488,688,988,1688],minutes=['常规清图','16分钟内','14分钟内','12分钟内','10分钟内','8分钟内'];
rowEdges.slice(0,-1).forEach((y,i)=>{a+=text(390,y+55,sample[i]+' r',58)+text(1020,y+55,minutes[i],48);});
a+=text(330,1630,'仅限时档：',28)+text(330,1690,'超时未清图，继续打',52);
a+=text(128,1852,'清图标准 · 满足其一',42,'#FFFFFF')+text(262,2000,'拉闸清图',39)+text(992,2000,'击杀清图',39)+text(778,2140,'或',37);
a+=text(135,2110,'陪玩拉绝户闸撤离',31)+text(135,2172,'同时航天无人走飞升',31)+text(135,2234,'仅丢包可以有人撤离',31)+text(910,2120,'规定时限内',36)+text(960,2200,'击杀 ≥ 8人',54);
a+=text(260,2385,'成功撤离仍需同时满足：',29)+text(260,2455,'击杀 ≥ 4人  且  物资 ≥ 600w / 300w',44);
a+=text(130,2602,'下单须知',42,'#FFFFFF');
['地图选择','物资说明','房卡保障','打手保障'].forEach((t,i)=>a+=text(310,(noteEdges[i]+noteEdges[i+1])/2,t,35));
a+=text(570,2725,'仅限对应模式航天',34)+text(570,2860,'达标前成功撤离物资累计',33)+text(570,2920,'炸单保底高于累计保底时',33)+text(570,2980,'满足结单条件后再补保底',33);
a+=text(570,3110,'清图后开房卡；未开照常补偿',31)+text(570,3175,'包航天核心区金红卡',33)+text(570,3250,'金卡 +200w / +100w',39)+text(570,3320,'红卡 +300w / +150w',39);
a+=text(570,3475,'规定时限内满足击杀人头数',31)+text(570,3540,'且物资 >900w / >500w',39)+text(570,3600,'允许速撤',33)+text(445,3730,'下单前确认地图及规则',32);
await save('poster',1600,3800,b,a);

b=rect(130,1090,1130,135,c.ink,45);
a=text(130,180,'品牌',65)+text(130,470,'限时',180)+text(130,780,'清图单',180)+text(1350,575,'已确认主题插画',70)+text(1370,700,'蜂医 + 灰鼠 + 大秒表',50)+text(180,1155,'对应模式航天 · 挑战时限',48,'#FFFFFF')+text(130,1310,'端游 / 手游 · 绝密 / 机密',38);
await save('cover',2560,1440,b,a);

const socialCards=[{x:120,y:920,mode:'端游 · 绝密',accent:c.cream},{x:1250,y:920,mode:'端游 · 机密',accent:c.mint},{x:120,y:1760,mode:'手游 · 绝密',accent:c.cream},{x:1250,y:1760,mode:'手游 · 机密',accent:c.mint}];
b=rect(120,720,2160,150,c.blue,30,c.edge)+icon(230,795,55);
a=text(120,170,'品牌',50)+text(120,355,'限时',132)+text(120,560,'清图单',132)+text(1140,270,'主题插画',75)+text(1130,405,'冲刺 + 秒表',60)+text(355,795,'航天清图一把 · 常规 / 限时档',42);
for(const [i,card]of socialCards.entries()){
 const {x,y,accent}=card;b+=rect(x,y,1030,780,'#FFFFFF',24,c.edge)+rect(x,y,1030,90,accent,24)+rect(x,y+90,1030,90,c.ink,0);
 for(let ri=0;ri<6;ri++){b+=rect(x+3,y+180+ri*100,1024,100,ri===5?accent:ri%2===0?c.blue:'#FFFFFF',0);if(ri>0)b+=hline(x+3,y+180+ri*100,1024);}
 b+=vline(x+470,y+90,690);
 a+=text(x+300,y+45,card.mode,42)+text(x+170,y+135,'价格',35,'#FFFFFF')+text(x+650,y+135,'清图时限',35,'#FFFFFF');
 const prices=[[168,388,488,688,988,1688],[108,288,388,588,888,1488],[108,288,388,588,888,1488],[88,228,328,428,828,1228]][i];
 prices.forEach((price,ri)=>{a+=text(x+155,y+230+ri*100,price+' r',50)+text(x+625,y+230+ri*100,minutes[ri],43);});
}
b+=rect(120,2580,2160,460,'#FFFFFF',30,c.edge)+icon(260,2690,85)+rect(660,3120,1080,48,c.blue,24);
a+=text(420,2660,'限时档：未清图，继续打',65)+text(420,2760,'拉闸清图 或 限时击杀≥8人',44)+text(200,2860,'成功撤离仍需：击杀≥4人 且 达到物资门槛',40)+text(200,2950,'绝密≥600w / 机密≥300w',45)+text(930,3144,'r=元 / w=万',28);
await save('social',2400,3200,b,a);
await fs.writeFile(path.join(dir,'layout-spec.json'),JSON.stringify({poster:{w:1600,h:3800,exportW:3200,exportH:7600,titleSafe:[90,65,600,435],hero:[710,20,840,620],platformPill:[90,535,570,85],service:[90,665,1420,95],price:{bounds:[90,790,1420,750],header:[90,790,1420,90],rowEdges,rows:6,columns:2,unitColumns:{priceR:575,timeMinutes:1240}},guarantee:[90,1580,1420,170],standardHeader:[90,1810,1420,85],methodCards:[[90,1915,690,405],[820,1915,690,405]],orBadge:[800,2140,47],minimum:[90,2345,1420,165],noteHeader:[90,2560,1420,85],notes:{bounds:[90,2670,1420,980],rowEdges:noteEdges},footer:[390,3700,820,60]},cover:{w:2560,h:1440,exportW:3200,exportH:1800,titleSafe:[120,100,1080,930],hero:[1270,80,1240,1300],subtitle:[130,1090,1130,135]},social:{w:2400,h:3200,exportW:2400,exportH:3200,service:[120,720,2160,150],cards:socialCards.map(c=>({...c,width:1030,height:780,modeStrip:90,navyHeader:90,rowEdges:Array.from({length:7},(_,i)=>c.y+180+i*100),columnEdges:[c.x,c.x+470,c.x+1030]})),guarantee:[120,2580,2160,460],noOrderNotes:true},note:'仅结构稿；图像生成后以实际面板边界重新定位矢量文字。'},null,2));
const file=path.join(root,'campaign-state.json'),state=JSON.parse(await fs.readFile(file,'utf8'));
state.approvals.illustration={status:'approved',basis:'用户在限时清图单主题插画交付后回复：继续',file:'01-illustrations/timed-clear-medic-mouse-v1.png'};
state.approvals.background={status:'in-progress',basis:'本轮进入结构布局及无字底图阶段：六档价目、两种清图方式、最低要求、四组须知'};
state.stage='background-in-progress';state.revision='timed-clear-backgrounds-v1';
state.layoutInvariants=state.layoutInvariants.map(s=>s.replace('附加规则等待修订','机密规则以用户补充文本为准，绝密沿用对应参考图'));
state.nextStep='交付四张价目、封面及简洁宣发无字底图，等待本轮确认后再加入矢量文案';
await fs.writeFile(file,JSON.stringify(state,null,2));
await fs.mkdir(path.join(root,'03-backgrounds'),{recursive:true});
await fs.copyFile(path.join(root,'review/index.html'),path.join(root,'review/illustration.html'));
console.log('Prepared 6-row price poster, 2-method standard, 4-rule layout, cover and 24-tier social layouts.');
