import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const modules=process.argv[2];
if(!modules)throw Error('Pass the workspace Node package directory.');
const decorated=process.argv.includes('--decorated');
const sand=process.argv.includes('--sand');
if(decorated){
 const state=JSON.parse((await fs.readFile(path.join(root,'campaign-state.json'),'utf8')).replace(/^\uFEFF/,''));
 if(state.approvals.decorativeProps?.status!=='approved')throw Error('Approve the new prop illustrations first.');
}
const sharp=createRequire(import.meta.url)(path.join(modules,'sharp'));
const source=path.join(root,sand?'01-illustrations/tazhu-sand-background-v1.png':'assets/backgrounds/poster-gold.png');
const sourceMetadata=await sharp(source).metadata();
const sourceBuffer=await sharp(source).resize(809,1942,{fit:'fill'}).png().toBuffer();
const b64=async rect=>(await sharp(sourceBuffer).extract(rect).png().toBuffer()).toString('base64');
const header=await b64({left:0,top:0,width:809,height:390});
const left=await b64({left:0,top:510,width:38,height:1180});
const right=await b64({left:771,top:510,width:38,height:1180});
const footer=await b64({left:0,top:1758,width:809,height:184});
const ink='#213F53',gold='#B47C36',border=sand?'#C8AE7C':'#B8D3DE';
const img=(data,x,y,w,h)=>`<image href="data:image/png;base64,${data}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="none"/>`;
const panel=(x,y,w,h,fill=sand?'#FFF8E8':'#FFFDF7',stroke=border)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="18" fill="${fill}" stroke="${stroke}" stroke-width="1.8"/>`;
const bar=(x,y,w,h)=>`<path d="M${x+18},${y} H${x+w-18} Q${x+w},${y} ${x+w},${y+18} V${y+h} H${x} V${y+18} Q${x},${y} ${x+18},${y}Z" fill="${ink}"/>`;
const star=(x,y,s)=>`<path d="M${x},${y-s} Q${x+2},${y-2} ${x+s},${y} Q${x+2},${y+2} ${x},${y+s} Q${x-2},${y+2} ${x-s},${y} Q${x-2},${y-2} ${x},${y-s}Z" fill="#F4C46B" stroke="${gold}" stroke-width="1.2"/>`;
function detailed(){
 const extra=decorated?190:0,H=2550+extra;
 let s=`<svg xmlns="http://www.w3.org/2000/svg" width="809" height="${H}" viewBox="0 0 809 ${H}"><defs><linearGradient id="paper" x2="0" y2="1"><stop stop-color="${sand?'#FFF0D3':'#FFFDF5'}"/><stop offset="1" stop-color="${sand?'#F1D19A':'#F5FAFC'}"/></linearGradient><linearGradient id="gold" x2="1" y2="1"><stop stop-color="${sand?'#FFF2D8':'#FFFBF0'}"/><stop offset="1" stop-color="${sand?'#F6DDA9':'#F7E9CC'}"/></linearGradient></defs><rect width="809" height="${H}" fill="url(#paper)"/>`;
 s+=img(header,0,0,809,390)+img(left,0,390,38,1980+extra)+img(right,771,390,38,1980+extra)+img(footer,0,2366+extra,809,184);
 // Separate entitlement cards with uninterrupted navy price rows.
 for(const [y,h] of [[420,326],[768,388]]){s+=panel(40,y,729,h)+bar(40,y,729,76);s+=`<path d="M430 ${y+21}v34" stroke="#8CA6B2" stroke-width="1.2" opacity=".65"/>`;}
 // Reward panel: three phantom rows and two red item rows.
 s+=panel(40,1183,729,584+extra,'url(#gold)',gold)+bar(40,1183,729,61);
 s+=`<path d="${decorated?'M66 1380H743 M66 1500H743 M66 1620H743':'M66 1328H743 M66 1390H743 M66 1538H743'}" stroke="${gold}" stroke-width="1.3" opacity=".3"/>`;
 s+=panel(64,decorated?1732:1552,681,137,sand?'#FFF8E8':'#FFFCF4','#DEC79F');
 s+=`<path d="M82 ${decorated?1800:1620}H727" stroke="${gold}" stroke-width="1.1" opacity=".27"/>`;
 // Rules as three readable semantic groups; once-only scope belongs to rewards.
 s+=panel(40,1791+extra,729,563)+bar(40,1791+extra,729,61);
 s+=`<path d="M67 ${1990+extra}H742 M67 ${2210+extra}H742" stroke="${border}" stroke-width="1.2" stroke-dasharray="3 6"/>`;
 for(const [x,y,siz] of [[18,400,10],[793,751,10],[22,1172,12],[790,1780+extra,11]])s+=star(x,y,siz);
 s+='</svg>';return s;
}
function social(){
 let s=`<svg xmlns="http://www.w3.org/2000/svg" width="1086" height="1448" viewBox="0 0 1086 1448"><rect width="1086" height="1448" fill="${sand?'#FFF0D3':'#FFFDF5'}"/>`;
 s+=img(header,0,0,1086,524)+img(left,0,524,38,740)+img(right,1048,524,38,740)+img(footer,0,1220,1086,247);
 for(const [x,y] of [[54,554],[557,554],[54,867],[557,867]]){s+=panel(x,y,475,287,sand?'#FFF8E8':'#FFFDF7',border)+bar(x,y,475,59);s+=`<path d="M${x+27},${y+140}H${x+448}" stroke="${border}" stroke-width="1.2"/>`;}
 s+=panel(54,1200,978,105,sand?'#FFF2D8':'#FFFBF0',gold);
 s+='</svg>';return s;
}
await fs.mkdir(path.join(root,'02-layouts'),{recursive:true});
await fs.mkdir(path.join(root,'review'),{recursive:true});
const posterName=sand?'poster-tazhu-sand-v1':decorated?'poster-tazhu-gold-v2':'poster-tazhu-gold';
const socialName=sand?'social-tazhu-sand-v1':'social-tazhu-gold';
for(const [name,svg] of [[posterName,detailed()],[socialName,social()]]){
 await fs.writeFile(path.join(root,'02-layouts',name+'.svg'),svg);
 await sharp(Buffer.from(svg)).png().toFile(path.join(root,'assets/backgrounds',name+'.png'));
 await sharp(Buffer.from(svg)).resize({width:name.startsWith('poster')?600:600}).png().toFile(path.join(root,'review',name+'-blank.png'));
}
const previewFiles=[posterName,socialName];
const cards=[];
for(const [i,name] of previewFiles.entries()){
 const data=(await fs.readFile(path.join(root,'review',name+'-blank.png'))).toString('base64');
 const h=name.startsWith('poster')?(decorated?2032:1891):800;
 cards.push(`<image href="data:image/png;base64,${data}" x="${i===0?30:660}" y="80" width="600" height="${h}"/><text x="${i===0?330:960}" y="44" text-anchor="middle" fill="${ink}" font-family="Microsoft YaHei UI" font-size="25" font-weight="700">${i===0?'端游 / 手游 · 共用完整价目底图':'端游 + 手游 · 3:4 价目广告底图'}</text>`);
}
const overviewH=decorated?2150:1990;
const overview=`<svg xmlns="http://www.w3.org/2000/svg" width="1290" height="${overviewH}"><rect width="1290" height="${overviewH}" fill="${sand?'#F3E2C1':'#EAF1F5'}"/>${cards.join('')}</svg>`;
await sharp(Buffer.from(overview)).png().toFile(path.join(root,'review',sand?'background-overview-sand-v1.png':decorated?'background-overview-v2.png':'background-overview.png'));
console.log(JSON.stringify({created:[posterName,socialName],source,sourceNative:{width:sourceMetadata.width,height:sourceMetadata.height},sand}));
