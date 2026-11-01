import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const sharp=require('C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.dirname(dir);
const c={ink:'#203f52',paper:'#fffdf5',blue:'#eaf3f7',edge:'#b9cdd7',gold:'#d6a252',cream:'#fbefd9',mint:'#ddf0e9'};
const rect=(x,y,w,h,fill,rx=20,stroke='none')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="3"/>`;
const line=(x1,y1,x2,y2,color=c.edge)=>`<path d="M${x1} ${y1}H${x2}" fill="none" stroke="${color}" stroke-width="2"/>`;
const label=(x,y,t,size=35)=>`<text x="${x}" y="${y}" font-family="Microsoft YaHei UI" font-size="${size}" fill="${c.ink}" dominant-baseline="central">${t}</text>`;
const svg=(w,h,b)=>`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${rect(0,0,w,h,c.paper,0)}${b}</svg>`;
const save=async(name,w,h,body,annotations='')=>{for(const [suffix,extra] of [['template',''],['layout',annotations]]){let s=svg(w,h,body+extra);await fs.writeFile(path.join(dir,`${name}-${suffix}.svg`),s);await sharp(Buffer.from(s)).png().toFile(path.join(dir,`${name}-${suffix}.png`));}};
let p=rect(0,0,1600,14,c.ink,0)+rect(90,510,620,85,c.ink);
p+=rect(90,665,1420,185,c.cream,30,c.gold);
p+=rect(90,895,1420,965,'#fff',25,c.edge)+rect(90,895,1420,95,c.ink,20)+rect(90,947,1420,43,c.ink,0);
for(let i=0;i<5;i++){const y=990+i*174;p+=rect(93,y,1414,174,i===4?c.cream:i%2===0?c.blue:'#fff',0);if(i>0)p+=line(110,y,1490,y);for(const x of[420,805,1040])p+=`<path d="M${x} ${y+30}v114" stroke="${c.edge}" stroke-width="2" opacity=".6"/>`;}
p+=rect(90,1915,1420,310,c.cream,25,c.gold)+rect(90,2260,1420,135,c.ink,25);
p+=rect(90,2510,1420,850,'#fff',25,c.edge)+rect(91,2511,145,848,c.blue,22);
let yy=2510;const rh=[190,115,140,120,155,130];for(let i=0;i<6;i++){if(i)p+=line(258,yy,1475,yy);yy+=rh[i];}
p+=rect(480,3420,640,60,c.blue,30);
let pa=label(90,110,'品牌标识',28)+label(90,290,'物资 / 银行单 · 艺术字',46)+label(90,450,'平台 + 模式',32)+label(860,300,'已确认银行主插画',42)+label(860,365,'金库 · 蜂医 · 得吃鼠',32)+label(145,755,'限局完成 → 超时追加保底 → 保底打满',42);
for(const[x,t]of[[160,'订单价格'],[535,'带出物资'],[840,'限定局数'],[1120,'超时追加']])pa+=label(x,945,t,29).replace(c.ink,'#ffffff');
for(let i=0;i<5;i++){const y=1077+174*i;for(const[x,t]of[[195,'1688r'],[545,'9888w'],[861,'48把'],[1120,'+6688w']])pa+=label(x,y,t,38);}
pa+=label(140,1990,'加仓模块：金额 → 追加保底',40)+label(140,2070,'条件与局数规则分区，避免误解',30)+label(200,2328,'玩法特色短句',40).replace(c.ink,'#ffffff')+label(100,2465,'下单须知 · 六组',43);
yy=2510;for(let i=0;i<6;i++){pa+=label(270,yy+rh[i]/2,['地图与房卡','随行装备','缺项补偿','炸单补偿','结算口径','物资与售后'][i],32)+label(570,yy+rh[i]/2,'正文组按可见高度垂直居中',29);yy+=rh[i];}
await save('poster',1600,3600,p,pa);
let cov=rect(145,1035,1150,145,c.ink,60);
let ca=label(150,220,'兴安电竞',65)+label(150,505,'物资',148)+label(150,730,'银行单',180)+label(205,1105,'限定局数 · 超时追加保底',63).replace(c.ink,'#fff')+label(1490,600,'已确认主插画',75)+label(1520,700,'金库 + 蜂医 + 灰鼠',48);
await save('cover',2560,1440,cov,ca);
let so=rect(120,735,2160,220,c.cream,35,c.gold),sa=label(120,220,'物资银行单 · 艺术字',92)+label(120,470,'端游 / 手游 · 绝密 / 机密',53)+label(1520,325,'银行主插画',65)+label(185,845,'限局完成，超时追加对应档位保底',65);
for(const[ci,x,y]of[[0,120,1020],[1,1250,1020],[2,120,2010],[3,1250,2010]]){so+=rect(x,y,1030,900,'#fff',25,c.edge)+rect(x,y,1030,120,ci%2?c.mint:c.cream,25)+rect(x,y+120,1030,100,c.ink,0);sa+=label(x+60,y+60,['端游 · 绝密','端游 · 机密','手游 · 绝密','手游 · 机密'][ci],57);for(let i=0;i<5;i++){const ry=y+220+i*136;so+=rect(x+3,ry,1024,136,i===4?(ci%2?c.mint:c.cream):i%2===0?c.blue:'#fff',0);if(i)so+=line(x+20,ry,x+1010,ry);sa+=label(x+35,ry+68,'价r    物资w    局数    追加w',43);}}
so+=rect(300,3005,1800,100,c.blue,45);sa+=label(380,3055,'超时追加整单一次 · 必要条件说明',50);
await save('social',2400,3200,so,sa);
await fs.writeFile(path.join(dir,'layout-spec.json'),JSON.stringify({version:1,poster:{width:1600,height:3600,header:{x:90,y:70,w:1420,h:550},mechanism:{x:90,y:665,w:1420,h:185},table:{x:90,y:895,w:1420,headerHeight:95,rowTop:990,rowHeight:174,rows:5,columns:[90,420,805,1040,1510]},topUp:{x:90,y:1915,w:1420,h:310},features:{x:90,y:2260,w:1420,h:135},rules:{x:90,y:2510,w:1420,rowHeights:rh},unitAnchors:{priceR:352,lootW:725,lateBonusW:1425}},cover:{width:2560,height:1440},social:{width:2400,height:3200,cards:4,rowsPerCard:5,columnsPerCard:4},note:'坐标为生成前结构规划；填字阶段应以实际生成底图重新测量。'},null,2));
let state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));state.stage='background-in-progress';state.approvals.bankIllustration={status:'approved',basis:'用户在插画展示后回复：继续',file:'01-illustrations/bank-medic-mouse-v1.png'};state.approvals.background={status:'in-progress',basis:'插画已确认，生成无字底图后提交用户确认'};state.nextStep='完成无字底图并提交确认；确认前不填矢量文案';state.revision='background-v1';await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log('Created blank templates and annotated layout plans.');
