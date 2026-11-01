import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.dirname(fileURLToPath(import.meta.url));
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const read=async file=>JSON.parse((await fs.readFile(path.join(root,file),'utf8')).replace(/^\uFEFF/,''));
const content=await read('content.json'),manifest=await read('output/manifest.json'),layout=await read('layout.json');
await fs.mkdir(path.join(root,'review/phone-390'),{recursive:true});
await fs.mkdir(path.join(root,'review/details'),{recursive:true});
const images=[];
for(const [i,m] of manifest.entries()){
 const output=path.join(root,m.png);
 await sharp(output).resize({width:390}).png().toFile(path.join(root,'review/phone-390',m.id+'.png'));
 const preview=await sharp(output).resize({width:m.id==='social'?450:390}).png().toBuffer();
 const md=await sharp(preview).metadata();
 const x=m.id==='desktop-absolute'?20:m.id==='mobile-absolute'?430:840;
 const y=m.id==='cover'?770:65;
 const label=m.id==='desktop-absolute'?'端游 · 绝密':m.id==='mobile-absolute'?'手游 · 绝密':m.id==='cover'?'塔主大人 · 黄沙版封面':'端游 / 手游 · 四档广告';
 images.push(`<text x="${x+md.width/2}" y="${y-30}" text-anchor="middle" fill="#213F53" font-family="Microsoft YaHei UI" font-size="24" font-weight="700">${label}</text><image href="data:image/png;base64,${preview.toString('base64')}" x="${x}" y="${y}" width="${md.width}" height="${md.height}"/>`);
 if(['desktop-absolute','mobile-absolute'].includes(m.id)){
  const scale=m.width/809;
  for(const [name,y,h] of [['prices',410,756],['rewards',1174,792],['rules',1970,583]]){
   await sharp(output).extract({left:Math.round(40*scale),top:Math.round(y*scale),width:Math.round(729*scale),height:Math.round(h*scale)}).resize({width:900}).png().toFile(path.join(root,'review/details',m.id+'-'+name+'.png'));
  }
 }
}
const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1310" height="1410"><rect width="1310" height="1410" fill="#F3E2C1"/>${images.join('')}</svg>`;
await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review/typesetting-overview.png'));
const bounds=await read('review/desktop-absolute-text-bounds.json');
const body=bounds.filter(b=>b.role==='rule-body');
const shrinking=bounds.filter(b=>b.effectiveSize<29&&['rule-body','phantom-frequency','red-nonstacking','entitlement-item'].includes(b.role));
const overlaps=[];
for(let i=0;i<bounds.length;i++)for(let j=i+1;j<bounds.length;j++){
 const a=bounds[i],b=bounds[j];
 const x=Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x),y=Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y);
 if(x>1&&y>1)overlaps.push({a:a.text,b:b.text,x,y});
}
const propCollisions=[];
const coverAudit={shrinking:[],overlaps:[],textCenters:[],forbiddenCopy:[]};
for(const page of layout.pages){
 const texts=await read(`review/${page.id}-text-bounds.json`);
 if(page.id==='cover'){
  coverAudit.shrinking=texts.filter(b=>b.effectiveSize<42&&b.role!=='brand');
  coverAudit.textCenters=texts.filter(b=>b.role.startsWith('cover-')).map(b=>({role:b.role,text:b.text,centerX:b.x+b.width/2,cy:b.cy}));
  coverAudit.forbiddenCopy=texts.filter(b=>/须知|注意|补偿|撤离失败|整单|不叠加/.test(b.text));
  for(let i=0;i<texts.length;i++)for(let j=i+1;j<texts.length;j++){
   const a=texts[i],b=texts[j];
   const x=Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x),y=Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y);
   if(x>1&&y>1)coverAudit.overlaps.push({a:a.text,b:b.text,x,y});
  }
 }
 for(const prop of page.illustrationBounds??[])for(const b of texts){
  const x=Math.min(prop.x+prop.width,b.x+b.width)-Math.max(prop.x,b.x),y=Math.min(prop.y+prop.height,b.y+b.height)-Math.max(prop.y,b.y);
  if(x>1&&y>1)propCollisions.push({page:page.id,prop:prop.id,text:b.text,x,y});
 }
}
const audit={source:'本轮用户文字、9格仅加188w及三幻神整单一次确认',prices:content.tiers.map(t=>({platform:t.platform,rows:t.rows.map(r=>({priceR:r.priceR,lootW:r.lootW,cardCount:r.cards.length,safes:r.safes}))})),minimumBodySize:Math.min(...body.map(b=>b.effectiveSize)),phoneEquivalentMinimumBodySize:Math.min(...body.map(b=>b.effectiveSize))*390/809,shrinking,overlaps,propCollisions,coverAudit,bodyMargins:body.map(b=>({text:b.text,left:b.x-40,right:769-(b.x+b.width),effectiveSize:b.effectiveSize})),frequencyScopeConfirmed:content.rewards.phantoms.confirmationStatus==='confirmed',propsPerPage:layout.pages.map(p=>({id:p.id,count:p.illustrationBounds?.length??0}))};
await fs.writeFile(path.join(root,'review/typesetting-audit.json'),JSON.stringify(audit,null,2));
console.log(JSON.stringify(audit,null,2));
