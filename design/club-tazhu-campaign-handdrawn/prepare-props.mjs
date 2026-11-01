import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.dirname(fileURLToPath(import.meta.url));
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const atlas=path.join(root,'01-illustrations/tazhu-prop-atlas-v1.png');
const target=path.join(root,'assets/props');
await fs.mkdir(target,{recursive:true});
const names=['tianyuan-difang','wanjin-leiguan','zongheng','sand-safe-closed','sand-safe-open','ordinary-safe'];
const labels=['天圆地方','万金泪冠','纵横','沙色保险 · 关闭','沙色保险 · 开启','普通大保险'];
const metadata=[];
for(const [i,name] of names.entries()){
 const cell={left:(i%3)*512,top:Math.floor(i/3)*512,width:512,height:512};
 const file=path.join(target,name+'.png');
 const cellBuffer=await sharp(atlas).extract(cell).png().toBuffer();
 await sharp(cellBuffer).trim({threshold:10}).png().toFile(file);
 const md=await sharp(file).metadata();
 metadata.push({id:name,file:'assets/props/'+name+'.png',label:labels[i],width:md.width,height:md.height,hasAlpha:md.hasAlpha,atlasCell:cell,method:'从原生透明贴图集按固定单元裁出；不改动插画内容'});
}
const approvedAtlas=process.argv[3];
if(approvedAtlas){
 for(const [name,rect,label] of [['keycards',{left:33,top:445,width:367,height:357},'已确认系列钥匙卡'],['mouse-shield',{left:904,top:436,width:348,height:379},'已确认系列灰鼠与盾牌']]){
  const file=path.join(target,name+'.png');
  const cellBuffer=await sharp(approvedAtlas).extract(rect).png().toBuffer();
  await sharp(cellBuffer).trim({threshold:10}).png().toFile(file);
  const md=await sharp(file).metadata();
  metadata.push({id:name,file:'assets/props/'+name+'.png',label,width:md.width,height:md.height,hasAlpha:md.hasAlpha,source:approvedAtlas,method:'复用已确认系列独立贴纸；只裁取单个元素'});
 }
}
await fs.writeFile(path.join(root,'01-illustrations/prop-metadata.json'),JSON.stringify({provider:'built-in image_gen',atlas:'01-illustrations/tazhu-prop-atlas-v1.png',atlasNative:{width:1536,height:1024},items:metadata},null,2));
const parts=[];
for(const [i,m] of metadata.slice(0,6).entries()){
 const x=40+(i%3)*400,y=70+Math.floor(i/3)*470;
 const b=(await fs.readFile(path.join(root,m.file))).toString('base64');
 parts.push(`<rect x="${x}" y="${y}" width="380" height="432" rx="20" fill="#FFFDF6" stroke="#D7C7A9" stroke-width="2"/><image href="data:image/png;base64,${b}" x="${x+30}" y="${y+15}" width="320" height="350" preserveAspectRatio="xMidYMid meet"/><text x="${x+190}" y="${y+401}" text-anchor="middle" fill="#213F53" font-family="Microsoft YaHei UI" font-size="27" font-weight="700">${m.label}</text>`);
}
const overview=`<svg xmlns="http://www.w3.org/2000/svg" width="1240" height="1020"><rect width="1240" height="1020" fill="#EDF3F5"/><text x="620" y="43" text-anchor="middle" fill="#213F53" font-family="Microsoft YaHei UI" font-size="29" font-weight="700">塔主大人 · 三幻神与保险箱Q版贴图</text>${parts.join('')}</svg>`;
await sharp(Buffer.from(overview)).png().toFile(path.join(root,'review/prop-overview-v1.png'));
console.log(JSON.stringify(metadata.map(m=>({id:m.id,width:m.width,height:m.height,hasAlpha:m.hasAlpha})),null,2));
