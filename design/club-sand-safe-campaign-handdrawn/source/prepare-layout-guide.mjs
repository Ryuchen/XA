import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';

const [project,modules]=process.argv.slice(2);
const require=createRequire(path.join(modules,'..','sand-safe-layout-loader.cjs'));
const sharp=require('sharp');
const stateFile=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(stateFile,'utf8'));
state.approvals.illustration={status:'approved',basis:'用户在主题插画交付后回复「继续」；2026-09-29',file:state.illustration.file};
state.stage='background-in-progress';
state.revision='sand-safe-background-v1';
state.nextStep='制作一张完整绝密无字底图与文字位置稿，交用户确认后进行矢量填字。';
const zones={
  canvas:{width:1024,height:2304,ratio:'4:9',reason:'十档价目、奖励条件、三项目标及四组须知采用约390px手机宽可读字号；延长原3:5草案，避免缩小正文。'},
  header:{x:44,y:34,width:936,height:294,copy:{x:58,y:50,width:496,height:250},illustration:{x:570,y:50,width:418,height:279}},
  reward:{x:44,y:350,width:936,height:240,copy:{x:76,y:376,width:820,height:184},accent:{x:910,y:380,width:50,height:130}},
  prices:{x:44,y:612,width:936,height:505,header:{x:46,y:614,width:932,height:76},rows:[{y:691,height:84},{y:775,height:84},{y:859,height:84},{y:943,height:84},{y:1027,height:88}],columns:[{x:72,width:270},{x:373,width:268},{x:679,width:268}],rowCount:5},
  objectives:{x:44,y:1140,width:936,height:500,title:{x:76,y:1170,width:864,height:108},rows:[{x:76,y:1300,width:864,height:92},{x:76,y:1405,width:864,height:92},{x:76,y:1510,width:864,height:102}]},
  notices:{x:44,y:1664,width:936,height:588,title:{x:76,y:1690,width:860,height:55},groups:[{x:76,y:1770,width:860,height:75},{x:76,y:1858,width:860,height:112},{x:76,y:1984,width:860,height:116},{x:76,y:2113,width:860,height:109}]},
  footer:{x:44,y:2268,width:936,height:22},
  plannedType:{body:38,bodyLineHeight:52,priceNumber:58,priceUnit:50,rulePhoneEquivalent:14.47,noticeRowLabel:35},
  rules:['无字底图中不写任何中文字母或数字','标题文案在左侧实际安全区，人物放右侧','价目表五行三列，表头整条海军蓝','数值与单位位置不放图标或道具','须知四组预留清楚的上下内边距','人物与医疗无人机保持完整轮廓','不要增加联系方式、二维码或其他模式']
};
await fs.mkdir(path.join(project,'03-backgrounds'),{recursive:true});
await fs.writeFile(path.join(project,'02-layouts/layout-zones.json'),JSON.stringify(zones,null,2)+'\n');
const stroke='#263f58';
const rect=(x,y,w,h,fill,rx=24,sw=2)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
let markup=rect(0,0,1024,2304,'#f7f4e9',0,0);
markup+=`<path d="M 990 330 Q 1010 720 993 1090 T 994 1775 L 994 2240" fill="none" stroke="#d7e3e8" stroke-width="9"/>`;
markup+=rect(44,350,936,240,'#fbefd0',26);
markup+=rect(44,612,936,505,'#fffdf4',24);
markup+=`<path d="M68 614 H956 Q978 614 978 638 V690 H46 V638 Q46 614 68 614 Z" fill="#223d56"/>`;
markup+=`<path d="M46 943 H978 V1091 Q978 1115 954 1115 H70 Q46 1115 46 1091 Z" fill="#f8f0d8"/>`;
for(const y of [775,859,943,1027])markup+=`<path d="M63 ${y} H961" fill="none" stroke="#d9dfdb" stroke-width="2"/>`;
markup+=rect(44,1140,936,500,'#fffdf4',26);
for(const r of zones.objectives.rows)markup+=rect(r.x,r.y,r.width,r.height,'#eef3f4',18,1);
markup+=rect(44,1664,936,588,'#fffdf4',26);
for(const y of [1848,1975,2105])markup+=`<path d="M77 ${y} H947" stroke="#dae2e2" stroke-width="2" stroke-dasharray="5 9"/>`;
const hero=await sharp(path.join(project,state.illustration.file)).resize({width:418,height:279,fit:'contain'}).png().toBuffer();
markup+=`<image x="570" y="50" width="418" height="279" href="data:image/png;base64,${hero.toString('base64')}"/>`;
const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="2304" viewBox="0 0 1024 2304">${markup}</svg>`;
await fs.writeFile(path.join(project,'02-layouts/text-free-layout-guide.svg'),svg);
await sharp(Buffer.from(svg)).png().toFile(path.join(project,'02-layouts/text-free-layout-guide.png'));
state.layout={zones:'02-layouts/layout-zones.json',guide:'02-layouts/text-free-layout-guide.png',canvas:zones.canvas};
await fs.writeFile(stateFile,JSON.stringify(state,null,2)+'\n');
console.log(JSON.stringify({guide:'02-layouts/text-free-layout-guide.png',zones:zones.canvas,stage:state.stage}));
