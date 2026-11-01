import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),sharp=loadSharp();
const manifest=JSON.parse(await fs.readFile(path.join(root,'output/manifest.json'),'utf8'));
const label=(x,y,s,size=25,color='#203F53')=>`<text x="${x}" y="${y}" font-family="Microsoft YaHei UI" font-size="${size}" font-weight="700" fill="${color}">${s}</text>`;
let page=`<svg xmlns="http://www.w3.org/2000/svg" width="1560" height="920"><rect width="1560" height="920" fill="#F9F7F0"/>${label(40,52,'限时清图单 · 三处版式调整',36)}${label(40,94,'四张价目同步更新；下方展示绝密 / 机密两种规则。',22,'#607F8C')}`;
for(const [i,id] of ['desktop-absolute','desktop-secret'].entries()){
 const p=manifest.find(v=>v.id===id),x=40+i*760,sx=p.width/p.nativeWidth,sy=p.height/p.nativeHeight;
 page+=label(x,148,i===0?'绝密版':'机密版',28,i===0?'#A8651B':'#117D72');
 for(const region of [{x:43,y:909,w:728,h:347,outY:170},{x:43,y:1506,w:728,h:264,outY:605}]){
  const data=await sharp(path.join(root,p.png)).extract({left:Math.round(region.x*sx),top:Math.round(region.y*sy),width:Math.round(region.w*sx),height:Math.round(region.h*sy)}).resize(720).png().toBuffer();
  page+=`<image x="${x}" y="${region.outY}" width="720" height="${region.h*720/region.w}" href="data:image/png;base64,${data.toString('base64')}"/>`;
 }
 page+=label(x,580,'房卡补偿分组 · 速撤条件连贯展示',24);
}
page+=label(40,898,'数字与单位以可见字形垂直居中，r / w 保留固定对齐列。',21,'#607F8C')+'</svg>';
await sharp(Buffer.from(page)).png().toFile(path.join(root,'review/revised-sections.png'));
console.log('Created review/revised-sections.png');
