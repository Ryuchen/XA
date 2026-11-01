import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const read=async name=>JSON.parse(await fs.readFile(path.join(root,name),'utf8'));
const plan=await read('layout.json'),state=await read('campaign-state.json'),content=await read('content.json');
const expected=state.deliveryScope.pageIds;
if(plan.pages.length!==3||expected.length!==3||plan.pages.some(p=>!expected.includes(p.id)))throw Error('Cover page set mismatch');
const sha=b=>createHash('sha256').update(b).digest('hex');
const original=sha(await fs.readFile(path.join(root,'archive/r1/content.json'))),current=sha(await fs.readFile(path.join(root,'content.json')));
if(original!==current)throw Error('Business source unexpectedly changed');
const summaries=[],contact=[],phones=[];
for(const [i,p] of plan.pages.entries()){
 const bounds=await read(`review/${p.id}-text-bounds.json`),regions=plan.regions[p.id],groups={};
 for(const b of bounds){const key=b.role==='art-title'?'title':b.group;(groups[key]??=[]).push(b);}
 const audit=[];
 for(const [key,bs] of Object.entries(groups)){
  const [x,y,w,h]=regions[key].safe;
  const l=Math.min(...bs.map(b=>b.x)),r=Math.max(...bs.map(b=>b.x+b.width)),t=Math.min(...bs.map(b=>b.y)),bottom=Math.max(...bs.map(b=>b.y+b.height));
  const a={region:key,text:bs.map(b=>b.text).join(' / '),margins:{left:l-x,right:x+w-r,top:t-y,bottom:y+h-bottom},horizontalOffset:(l+r)/2-(x+w/2),verticalOffset:(t+bottom)/2-(y+h/2),effectiveSizes:bs.map(b=>b.effectiveSize)};
  a.pass=Object.values(a.margins).every(v=>v>=-0.1)&&Math.abs(a.horizontalOffset)<.2&&Math.abs(a.verticalOffset)<1;
  audit.push(a);
 }
 const png=`output/png/${p.id}.png`,buffer=await fs.readFile(path.join(root,png)),m=await sharp(buffer).metadata();
 if(m.width!==3200||m.height!==2000)throw Error('Wrong exported dimensions');
 const phone=await sharp(buffer).resize({width:390}).png().toBuffer();
 await fs.writeFile(path.join(root,`review/${p.id}-phone-r2.png`),phone);
 phones.push({input:phone,left:10+i*400,top:10});
 const small=await sharp(buffer).resize({width:520}).png().toBuffer();
 contact.push({input:small,left:10+i*530,top:10});
 await sharp(buffer).extract({left:160,top:180,width:1450,height:1500}).png().toFile(path.join(root,`review/${p.id}-text-detail-r2.png`));
 summaries.push({id:p.id,png,sha256:sha(buffer),dimensions:[m.width,m.height],backgroundNative:[1586,992],audit,pass:audit.every(a=>a.pass)});
}
await sharp({create:{width:1600,height:345,channels:4,background:'#fbfaf7'}}).composite(contact).png().toFile(path.join(root,'review/covers-overview-r2.png'));
await sharp({create:{width:1210,height:264,channels:4,background:'#fbfaf7'}}).composite(phones).png().toFile(path.join(root,'review/covers-phone-r2.png'));
await fs.writeFile(path.join(root,'review/covers-layout-audit-r2.json'),JSON.stringify({businessContentUnchanged:original===current,summaries},null,2));
console.log(JSON.stringify(summaries.map(s=>({id:s.id,pass:s.pass,issues:s.audit.filter(a=>!a.pass)})),null,2));
if(summaries.some(s=>!s.pass))process.exitCode=1;
