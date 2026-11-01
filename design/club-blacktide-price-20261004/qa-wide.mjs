import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const root=path.dirname(fileURLToPath(import.meta.url));
const sharp=createRequire(import.meta.url)(path.resolve(path.dirname(process.execPath),'../node_modules/sharp'));
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const c=await read('content.json'),old=await read('archive/r1/content.json'),plan=await read('layout.json'),bounds=await read('review/blacktide-text-bounds.json');
const failures=[];
if(JSON.stringify(c)!==JSON.stringify(old))failures.push('Business content changed');
for(const k of ['hourly','rounds'])c.prices[k].values.forEach((row,i)=>row.forEach((v,j)=>{const s=c.prices[k].sourceValues[i][j];if(v!==(s===null?null:s-10))failures.push('Price '+k+' '+i+' '+j);}));
if(plan.pages.length!==1||plan.pages[0].id!=='blacktide')failures.push('Page set');
if(plan.pages[0].w!==2160||plan.pages[0].ew!==4800)failures.push('Width not doubled');
for(const b of bounds){if(b.x<28||b.y<28||b.x+b.width>2132||b.y+b.height>3402)failures.push('Outside canvas '+b.text);}
const numeric=bounds.filter(b=>b.role==='price-number');if(numeric.length!==61)failures.push('Expected 61 priced cells, got '+numeric.length);
const regionChecks=plan.reviewRegions.map(r=>{
 const es=bounds.filter(b=>b.group===r.containerId||b.group?.startsWith(r.containerId+'-line-'));
 if(!es.length)return {id:r.containerId,empty:true};
 const left=Math.min(...es.map(b=>b.x)),right=Math.max(...es.map(b=>b.x+b.width)),top=Math.min(...es.map(b=>b.y)),bottom=Math.max(...es.map(b=>b.y+b.height));
 const bad=es.filter(b=>b.x<r.safe.x-1||b.x+b.width>r.safe.x+r.safe.w+1||b.y<r.safe.y-1||b.y+b.height>r.safe.y+r.safe.h+1);
 if(bad.length)failures.push('Container safe bounds '+r.containerId+': '+bad.map(b=>b.text).join('|'));
 return {id:r.containerId,ink:{left,right,top,bottom},container:r,safeFailures:bad.map(b=>b.text),topMargin:top-r.y,bottomMargin:r.y+r.h-bottom,centerOffset:(top+bottom)/2-(r.y+r.h/2)};
});
const minimumBody=Math.min(...bounds.filter(b=>b.role==='rule-body').map(b=>b.effectiveSize));
await fs.writeFile(path.join(root,'review/automated-qa.json'),JSON.stringify({failures,pricedCellCount:numeric.length,businessContentIdentical:JSON.stringify(c)===JSON.stringify(old),contentSha256:createHash('sha256').update(await fs.readFile(path.join(root,'content.json'))).digest('hex'),widthMultiplier:2,minimumRuleColumnPhoneSize:minimumBody*390/1080,minimumRuleFullPagePhoneSize:minimumBody*390/2160,regionChecks,scope:plan.pages.map(p=>p.id)},null,2));
const png=path.join(root,'output/png/blacktide.png');
const native=await sharp(png).resize({width:2160}).png().toBuffer();
await sharp(native).resize({width:1080}).png().toFile(path.join(root,'review/wide-preview.png'));
await sharp(native).resize({width:390}).png().toFile(path.join(root,'review/mobile-390.png'));
for(const [id,top,height]of [['header',0,572],['prices',566,898],['middle',1450,1160],['bottom',2620,810]])await sharp(native).extract({left:0,top,width:2160,height}).resize({width:1440}).png().toFile(path.join(root,'review/wide-'+id+'.png'));
for(const [id,left]of [['left',0],['right',1080]]){
 const col=await sharp(native).extract({left,top:566,width:1080,height:2864}).png().toBuffer();
 await sharp(col).resize({width:390}).png().toFile(path.join(root,'review/column-'+id+'-390.png'));
 for(const [part,top,height]of [['price',566,885],['rules',1450,1160],['bottom',2620,810]])await sharp(native).extract({left,top,width:1080,height}).png().toFile(path.join(root,'review/'+id+'-'+part+'.png'));
}
console.log(JSON.stringify({failures,pricedCells:numeric.length,minimumRuleColumnPhoneSize:minimumBody*390/1080,containerChecks:regionChecks.length}));
