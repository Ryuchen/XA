import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),sharp=require('C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.dirname(dir);
const c={ink:'#203f52',paper:'#fffdf5',blue:'#eaf3f7',edge:'#b9cdd7',gold:'#d6a252',cream:'#fbefd9',mint:'#ddf0e9'};
const rect=(x,y,w,h,fill,rx=22,stroke='none')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="3"/>`;
const line=(x1,y,x2,color=c.edge)=>`<path d="M${x1} ${y}H${x2}" fill="none" stroke="${color}" stroke-width="2"/>`;
const label=(x,y,t,size=35,fill=c.ink)=>`<text x="${x}" y="${y}" font-family="Microsoft YaHei UI" font-size="${size}" fill="${fill}" dominant-baseline="central">${t}</text>`;
const svg=(w,h,b)=>`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${rect(0,0,w,h,c.paper,0)}${b}</svg>`;
async function save(name,w,h,b,a=''){for(const[suffix,extra]of[['template',''],['layout',a]]){const s=svg(w,h,b+extra);await fs.writeFile(path.join(dir,`${name}-${suffix}.svg`),s);await sharp(Buffer.from(s)).png().toFile(path.join(dir,`${name}-${suffix}.png`));}}
const ruleHeights=[160,120,140,120,145,125];
for(const [platform,count]of [['desktop',5],['mobile',4]]){
 let b=rect(0,0,1600,14,c.ink,0)+rect(90,505,570,83,c.ink)+rect(90,660,1420,160,c.cream,30,c.gold);
 b+=rect(90,860,1420,790,'#fff',25,c.edge)+rect(90,860,1420,90,c.ink,20)+rect(90,910,1420,40,c.ink,0);
 const rowH=700/count;
 for(let i=0;i<count;i++){const y=950+i*rowH;b+=rect(93,y,1414,rowH,i===count-1?c.cream:i%2===0?c.blue:'#fff',0);if(i)b+=line(112,y,1488);b+=`<path d="M786 ${y+rowH/2-12}l13 12 -13 12m28 -24l13 12 -13 12" fill="none" stroke="${c.gold}" stroke-width="4" stroke-linecap="round"/>`;}
 b+=rect(90,1700,1420,460,'#fff',25,c.edge);
 for(const [x,fill]of[[140,c.blue],[630,c.blue],[1120,c.cream]])b+=rect(x,1860,340,180,fill,23);
 b+=rect(90,2200,1420,240,c.cream,25,c.gold)+rect(90,2200,240,240,c.blue,25);
 b+=rect(90,2490,1420,90,c.ink,25)+rect(90,2600,1420,810,'#fff',25,c.edge)+rect(91,2601,145,808,c.blue,22);
 let y=2600;for(let i=0;i<6;i++){if(i)b+=line(258,y,1475);y+=ruleHeights[i];}
 b+=rect(390,3470,820,62,c.blue,30);
 let a=label(95,100,'品牌标识',30)+label(95,260,'超额 / 累计单 · 艺术字',45)+label(95,445,'成功撤离 · 超额累计',32)+label(950,305,'已确认主插画',44)+label(950,375,'满载物资车 · 蜂医 · 灰鼠',29)+label(165,741,'仅累计超过门槛的部分',43);
 a+=label(350,907,'订单价格',33,'#fff')+label(1040,907,'超额累计保底',33,'#fff');
 for(let i=0;i<count;i++){const cy=950+(i+.5)*rowH;a+=label(365,cy,'价格 r',52)+label(1080,cy,'保底 w',52);}
 a+=label(140,1765,'超额怎么算 · 以成功撤离为前提',38)+label(190,1910,'当局带出',32)+label(690,1910,'计入门槛',32)+label(1180,1910,'本局计入',32)+label(260,1980,'物资 w',46)+label(755,1980,'门槛 w',46)+label(1230,1980,'结果 w',46)+label(535,1960,'−',70)+label(1030,1960,'=',60)+label(150,2110,'示例说明：仅超额部分计进度，未超过门槛不计入',29);
 a+=label(130,2320,'物资图标',29)+label(380,2270,'全部物资归板板',39)+label(380,2340,'累计进度与物资归属分开说明',29)+label(145,2535,'下单须知',40,'#fff');
 y=2600;for(let i=0;i<6;i++){a+=label(265,y+ruleHeights[i]/2,['地图和房卡','随行装备','缺项补偿','炸单补偿','结算口径','物资与售后'][i],31)+label(575,y+ruleHeights[i]/2,'正文组按实际字形高度垂直居中',28);y+=ruleHeights[i];}
 await save(`poster-${platform}`,1600,3600,b,a);
}
await save('cover',2560,1440,rect(145,1035,1150,145,c.ink,60),label(150,220,'兴安电竞',60)+label(150,500,'超额',150)+label(150,740,'累计单',170)+label(195,1105,'成功撤离 · 超额累计 · 打满结单',54,'#fff')+label(1490,650,'已确认主插画',78));
let s=rect(120,735,2160,325,c.blue,32,c.edge),a=label(120,215,'超额 / 累计单 · 艺术字',92)+label(130,555,'成功撤离 · 超额累计',47)+label(1500,330,'满载撤离插画',57)+label(300,820,'成功撤离，仅超过门槛的部分计入',57)+label(300,922,'绝密门槛 / 机密门槛',62);
for(const [ci,x,y,count,h]of[[0,120,1110,5,850],[1,1250,1110,5,850],[2,120,2010,4,790],[3,1250,2010,4,790]]){const mode=ci%2?c.mint:c.cream;let rh=(h-190)/count;s+=rect(x,y,1030,h,'#fff',25,c.edge)+rect(x,y,1030,110,mode,25)+rect(x,y+110,1030,80,c.ink,0);a+=label(x+70,y+55,['端游 · 绝密','端游 · 机密','手游 · 绝密','手游 · 机密'][ci],50);for(let i=0;i<count;i++){let ry=y+190+i*rh;s+=rect(x+3,ry,1024,rh,i===count-1?mode:i%2===0?c.blue:'#fff',0);if(i)s+=line(x+20,ry,x+1010);a+=label(x+125,ry+rh/2,'价格 r',48)+label(x+630,ry+rh/2,'累计 w',48);}}
s+=rect(210,2890,1980,220,c.blue,45);a+=label(360,2960,'全部物资归板板 · 超额部分计入进度',46)+label(360,3045,'打满订单保底及适用补偿后结单',40);
await save('social',2400,3200,s,a);
await fs.writeFile(path.join(dir,'layout-spec.json'),JSON.stringify({stage:'pre-generation-guide',poster:{w:1600,h:3600,exportW:3200,exportH:7200,header:{x:90,y:60,w:1420,h:550},mechanism:{x:90,y:660,w:1420,h:160},table:{x:90,y:860,w:1420,headerHeight:90,rowsTop:950,rowsHeight:700,desktopRows:5,mobileRows:4,columns:2},example:{x:90,y:1700,w:1420,h:460,tiles:[[140,1860,340,180],[630,1860,340,180],[1120,1860,340,180]]},ownership:{x:90,y:2200,w:1420,h:240},rules:{x:90,y:2600,w:1420,rowHeights:ruleHeights}},cover:{w:2560,h:1440,exportW:3200,exportH:1800},social:{w:2400,h:3200,cards:[{id:'desktop-absolute',rows:5},{id:'desktop-secret',rows:5},{id:'mobile-absolute',rows:4},{id:'mobile-secret',rows:4}]},note:'所有正式文字等待底图确认后再填；以实际生成面板重新测量。'},null,2));
const stateFile=path.join(root,'campaign-state.json');let state=JSON.parse(await fs.readFile(stateFile,'utf8'));state.approvals.illustration={...state.approvals.illustration,status:'approved',basis:'用户在插画预览交付后回复：继续'};state.approvals.background={status:'in-progress',basis:'插画已确认，按端游5档、手游4档及计算示例排版生成底图'};state.stage='background-in-progress';state.revision='background-v1';state.nextStep='提交四张价目、封面与广告的无字底图供确认，确认后再填矢量文案';await fs.writeFile(stateFile,JSON.stringify(state,null,2));
console.log('Built PC five-row, mobile four-row, cover and social layout guides.');
