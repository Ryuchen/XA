import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.dirname(dir);
const c={ink:'#203f52',paper:'#fffdf5',blue:'#eaf3f7',edge:'#b9cdd7',gold:'#d6a252',cream:'#fbefd9',mint:'#ddf0e9'};
const rect=(x,y,w,h,fill,rx=22,stroke='none')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="3"/>`;
const line=(x1,y,x2)=>`<path d="M${x1} ${y}H${x2}" fill="none" stroke="${c.edge}" stroke-width="2"/>`;
const vline=(x,y1,y2)=>`<path d="M${x} ${y1}V${y2}" fill="none" stroke="${c.edge}" stroke-width="2"/>`;
const tx=(x,y,t,size=35,fill=c.ink)=>`<text x="${x}" y="${y}" font-family="Microsoft YaHei UI" font-size="${size}" fill="${fill}" dominant-baseline="central">${t.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}</text>`;
async function save(name,w,h,b,a){for(const[suffix,extra]of[['template',''],['layout',a]]){const s=`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">${rect(0,0,w,h,c.paper,0)}${b}${extra}</svg>`;await fs.writeFile(path.join(dir,`${name}-${suffix}.svg`),s);await sharp(Buffer.from(s)).png().toFile(path.join(dir,`${name}-${suffix}.png`));}}
let p=rect(90,535,550,85,c.ink)+rect(90,660,1420,135,c.cream,30,c.gold);
let a=tx(95,110,'兴安电竞',40)+tx(95,275,'物资 / 对赌单',64)+tx(925,300,'已确认的靶心挑战插画',38)+tx(150,577,'平台 · 模式',38,'#fff')+tx(310,702,'单局撤离必须严格大于目标',42)+tx(310,760,'等于也未达标 · 不跨局累计',34);
const groupTops=[850,1250,1650];
for(const [g,y]of groupTops.entries()){
 p+=rect(90,y,1420,370,'#fff',25,c.edge)+rect(90,y,1420,80,c.ink,22)+rect(90,y+40,1420,40,c.ink,0);
 a+=tx(135,y+40,'地图 '+(g+1),42,'#fff')+tx(840,y+40,'单局撤离物资 > 目标',33,'#fff');
 for(let i=0;i<3;i++){const ry=y+80+i*(290/3);p+=rect(93,ry,1414,290/3,i===2?c.cream:i%2===0?c.blue:'#fff',0);if(i)p+=line(110,ry,1490);a+=tx(310,ry+145/3,'价格 r',54)+tx(990,ry+145/3,'> 目标 w',54);}
}
p+=rect(90,2070,1420,225,c.cream,30,c.gold);
a+=tx(135,2160,'靶心',38)+tx(350,2140,'未达标，就继续打',64)+tx(350,2225,'单局挑战 · 不跨局累计',40);
p+=rect(90,2345,1420,80,c.ink)+rect(90,2445,1420,285,'#fff',25,c.edge)+rect(93,2448,147,279,c.blue,20);
a+=tx(140,2385,'特别声明',40,'#fff');
for(const [i,text]of['达标前物资归板板','对应炸单保底达标后另补','禁止自带空白存储洗给板板'].entries()){if(i)p+=line(258,2445+i*95,1480);a+=tx(290,2492.5+i*95,text,34);}
p+=rect(90,2780,1420,80,c.ink)+rect(90,2880,1420,560,'#fff',25,c.edge)+rect(93,2883,147,554,c.blue,20);
a+=tx(140,2820,'下单规则',40,'#fff');
const ruleEdges=[2880,2990,3150,3290,3440];
for(let i=0;i<4;i++){if(i)p+=line(258,ruleEdges[i],1480);const cy=(ruleEdges[i]+ruleEdges[i+1])/2;a+=tx(275,cy,['地图选择','房卡保障','理包与速撤','装备例外'][i],32)+tx(600,cy,'完整规则，按整组垂直居中',31);}
p+=rect(390,3490,820,60,c.blue,30);
await save('poster',1600,3600,p,a);
await save('cover',2560,1440,rect(130,1090,1130,135,c.ink,60),tx(145,210,'兴安电竞',62)+tx(145,505,'物资',165)+tx(145,780,'对赌单',185)+tx(230,1155,'单局达标 · 未达继续',60,'#fff')+tx(1420,550,'已确认靶心挑战插画',68));
let s=rect(120,670,2160,200,c.blue,30,c.edge),sa=tx(125,180,'物资 / 对赌单',108)+tx(1340,285,'蜂医 · 灰鼠 · 靶心挑战',55)+tx(250,730,'单局撤离必须严格大于目标',63)+tx(520,818,'等于仍未达标 · 不跨局累计',39);
const socialCards=[];
for(const [ci,x,y]of[[0,120,925],[1,1250,925],[2,120,1685],[3,1250,1685]]){
 const mode=ci%2?c.mint:c.cream,header=190,rowH=500/3,colEdges=[x,x+230,x+496.667,x+763.333,x+1030];
 s+=rect(x,y,1030,690,'#fff',25,c.edge)+rect(x,y,1030,90,mode,25)+rect(x,y+90,1030,100,c.ink,0);
 sa+=tx(x+85,y+45,['端游 · 绝密','端游 · 机密','手游 · 绝密','手游 · 机密'][ci],48);
 for(let i=0;i<3;i++){const ry=y+header+i*rowH;s+=rect(x+3,ry,1024,rowH,i===2?mode:i%2===0?c.blue:'#fff',0);if(i)s+=line(x+12,ry,x+1018);sa+=tx(x+23,ry+rowH/2,'价格r',38);for(let j=0;j<3;j++)sa+=tx(colEdges[j+1]+22,ry+rowH/2,'>目标w',35);}
 for(const edge of colEdges.slice(1,-1))s+=vline(edge,y+90,y+690);
 sa+=tx(x+28,y+140,'价格',35,'#fff');for(let j=0;j<3;j++)sa+=tx(colEdges[j+1]+45,y+140,'地图'+(j+1),35,'#fff');
 socialCards.push({x,y,w:1030,h:690,modeHeader:[x,y,1030,90],tableHeader:[x,y+90,1030,100],rowEdges:[y+190,y+190+rowH,y+190+rowH*2,y+690],colEdges});
}
s+=rect(120,2435,2160,580,'#fff',35,c.edge)+rect(660,3110,1080,46,c.blue,20);
sa+=tx(170,2570,'靶心',55)+tx(505,2545,'未达标，就继续打',83)+tx(505,2660,'单局挑战 · 不跨局累计',57)+tx(240,2820,'达标前全部物资归板板',47)+tx(240,2910,'对应炸单保底在满足结单条件后另补',44);
await save('social',2400,3200,s,sa);
await fs.writeFile(path.join(dir,'layout-spec.json'),JSON.stringify({stage:'pre-generation-guide',poster:{w:1600,h:3600,exportW:3200,exportH:7200,header:{titleSafe:[90,65,570,440],hero:[745,25,770,590],platform:[90,535,550,85]},mechanism:[90,660,1420,135],mapGroups:groupTops.map(y=>({header:[90,y,1420,80],rows:3,rowEdges:[y+80,y+80+290/3,y+80+580/3,y+370],columns:2})),guarantee:[90,2070,1420,225],declarations:{header:[90,2345,1420,80],bounds:[90,2445,1420,285],rowEdges:[2445,2540,2635,2730]},notes:{header:[90,2780,1420,80],bounds:[90,2880,1420,560],rowEdges:ruleEdges},footer:[390,3490,820,60]},cover:{w:2560,h:1440,exportW:3200,exportH:1800,titleSafe:[120,100,1060,930],subtitle:[130,1090,1130,135]},social:{w:2400,h:3200,mechanism:[120,670,2160,200],cards:socialCards,rowsPerCard:3,columnsPerCard:4,guarantee:[120,2435,2160,580],noOrderNotes:true},note:'正式文字及严格大于符号待底图确认后以矢量添加，根据实图边界重测。'},null,2));
const sf=path.join(root,'campaign-state.json'),state=JSON.parse(await fs.readFile(sf,'utf8'));
state.approvals.illustration={...state.approvals.illustration,status:'approved',basis:'用户在物资对赌单主题插画交付后回复：继续'};
state.approvals.background={status:'in-progress',basis:'三地图各三档；独立玩法保障、三项声明、四组下单规则；广告为四卡矩阵'};
state.stage='background-in-progress';state.revision='background-v1';state.nextStep='提交四张价目、封面和简洁宣发的无字底图确认，确认后填矢量文案';await fs.writeFile(sf,JSON.stringify(state,null,2));
await fs.mkdir(path.join(root,'03-backgrounds'),{recursive:true});
console.log('Prepared three-map poster, cover, and 36-tier matrix social layout guides.');
