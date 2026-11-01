import fs from'node:fs/promises';import path from'node:path';import {createRequire}from'node:module';import {createHash}from'node:crypto';import {fileURLToPath}from'node:url';
const project=path.resolve(process.argv[2]||path.join(path.dirname(fileURLToPath(import.meta.url)),'..'));
const modules=process.argv[3]||path.resolve(path.dirname(process.execPath),'../node_modules'),sharp=createRequire(import.meta.url)(path.join(modules,'sharp'));
const read=async p=>JSON.parse(await fs.readFile(path.join(project,p),'utf8'));
const plan=await read('layout.json'),content=await read('content.json'),geometry=await read('02-layouts/generated-background-measurements.json');
await fs.mkdir(path.join(project,'review/final-crops'),{recursive:true});
const audit=[];
for(const p of plan.pages){
 const g=geometry.pages.find(g=>g.id===p.id),png=await fs.readFile(path.join(project,'output/png',p.id+'.png'));
 const native=await sharp(png,{limitInputPixels:false}).resize(p.w,p.h).png().toBuffer();
 await fs.writeFile(path.join(project,'review',p.id+'-phone-390.png'),await sharp(png,{limitInputPixels:false}).resize({width:390}).png().toBuffer());
 for(const [label,r]of Object.entries({header:g.header.container,conditions:g.conditions.container,...(g.prices?{prices:g.prices.container}:{}),reward:g.reward.container,...(g.notices?{notices:g.notices.container}:{})})){
  const pad=8,left=Math.max(0,r.x-pad),top=Math.max(0,r.y-pad),width=Math.min(p.w-left,r.width+pad*2),height=Math.min(p.h-top,r.height+pad*2);
  await fs.writeFile(path.join(project,'review/final-crops',p.id+'-'+label+'.png'),await sharp(native).extract({left,top,width,height}).png().toBuffer());
 }
 const bounds=await read('review/'+p.id+'-text-bounds.json'),groups=await read('review/'+p.id+'-group-audit.json');
 const numeric=[];
 const byLine=new Map();
 for(const b of bounds.filter(b=>b.lineGroup)){if(!byLine.has(b.lineGroup))byLine.set(b.lineGroup,[]);byLine.get(b.lineGroup).push(b);}
 for(const [line,bs]of byLine){
  const units=bs.filter(b=>/^[rw]$/.test(b.text));
  for(const unit of units){const n=bs.filter(b=>/^\d+$/.test(b.text)&&b.x<unit.x).at(-1);if(!n)throw new Error('Unit without number '+line);
   const pair={line,number:n.text,unit:unit.text,numberFont:n.font,unitFont:unit.font,baselineDelta:Math.abs(n.baseline-unit.baseline),visibleGap:unit.x-n.x-n.width,unitHeightRatio:unit.height/n.height,unitX:unit.x,numberRight:n.x+n.width};
   if(pair.baselineDelta>.1||pair.visibleGap<5||pair.unitHeightRatio<.64||pair.unitHeightRatio>.77)throw new Error('Numeric baseline/gap/ratio violation '+JSON.stringify(pair));
   numeric.push(pair);
  }
 }
 const columnAudit=[];
 for(const platform of p.platforms){
  const rows=numeric.filter(x=>x.line.startsWith('price-'+platform+'-'));
  if(rows.length){const unitSpread=Math.max(...rows.map(x=>x.unitX))-Math.min(...rows.map(x=>x.unitX)),numberRightSpread=Math.max(...rows.map(x=>x.numberRight))-Math.min(...rows.map(x=>x.numberRight));if(unitSpread>.1||numberRightSpread>.1)throw new Error('Price axes mismatch');columnAudit.push({platform,unitSpread,numberRightSpread});}
 }
 const objUnits=numeric.filter(x=>x.unit==='w'&&x.line.startsWith('line')&&[String(content.objectives.loot.desktop),String(content.objectives.loot.mobile)].includes(x.number));
 if(objUnits.length===2){const spread=Math.abs(objUnits[0].unitX-objUnits[1].unitX);if(spread>.1)throw new Error('Loot axes mismatch');columnAudit.push({type:'combined-loot',unitSpread:spread});}
 const shrink=bounds.filter(b=>b.requestedSize!==undefined&&b.effectiveSize<b.requestedSize-.05).map(b=>({text:b.text,role:b.role,requested:b.requestedSize,effective:b.effectiveSize}));
 if(shrink.some(b=>b.role==='rule-body'||b.role==='key-condition'))throw new Error('Hidden shrinking in body '+JSON.stringify(shrink));
 const phones=bounds.filter(b=>b.semanticGroup?.startsWith('notice-')&&b.semanticGroup!=='notice-heading').map(b=>b.effectiveSize*390/p.w);
 if(phones.some(s=>s<13.9))throw new Error('Unreadable phone size');
 const groupResults=groups.map(z=>({id:z.id,margins:z.margins,verticalCenterDelta:Math.abs(z.margins.top-z.margins.bottom)/2,horizontalCenterDelta:Math.abs(z.margins.left-z.margins.right)/2}));
 audit.push({id:p.id,png:'output/png/'+p.id+'.png',sha256:createHash('sha256').update(png).digest('hex'),numeric,columnAudit,shrink,bodyPhonePx:phones.length?{min:Math.min(...phones),max:Math.max(...phones)}:null,groups:groupResults});
}
const priceBusiness=content.prices.map(x=>({label:x.label,desktop:x.desktop,mobile:x.mobile,unit:x.unit}));
const combined=plan.pages.find(p=>p.id==='combined-advert');
for(const tier of content.prices)for(const platform of ['desktop','mobile']){const id='price-'+platform+'-'+content.prices.indexOf(tier),value=combined.elements.find(e=>e.semanticGroup===id&&e.axisKind==='number');if(value.text!==String(tier[platform]))throw new Error('Advert data mismatch');}
await fs.writeFile(path.join(project,'review/final-metric-audit.json'),JSON.stringify({basis:'Final PNG hash + measured vector glyphs; supplements visual QA',prices:priceBusiness,objectives:content.objectives,pages:audit},null,2));
const phones=await Promise.all(plan.pages.map(async p=>await fs.readFile(path.join(project,'review',p.id+'-phone-390.png'))));
const overview=await sharp({create:{width:1220,height:950,channels:4,background:'#f3f0e5'}}).composite([{input:phones[0],left:15,top:40},{input:phones[1],left:415,top:40},{input:phones[2],left:815,top:40},{input:phones[3],left:815,top:605}]).png().toBuffer();
await fs.writeFile(path.join(project,'review/final-overview.png'),overview);
const snippets=[];
for(const id of ['desktop-absolute','mobile-absolute']){const p=plan.pages.find(p=>p.id===id),g=geometry.pages.find(p=>p.id===id),b=await fs.readFile(path.join(project,'output/png',id+'.png'));
 const n=await sharp(b,{limitInputPixels:false}).resize(p.w,p.h).png().toBuffer();
 const crop=await sharp(n).extract({left:25,top:g.conditions.container.y-10,width:786,height:g.prices.container.y+g.prices.container.height-g.conditions.container.y+20}).resize({width:390}).png().toBuffer();snippets.push(crop);
}
const heights=await Promise.all(snippets.map(async b=>(await sharp(b).metadata()).height));
await fs.writeFile(path.join(project,'review/price-update-preview.png'),await sharp({create:{width:805,height:Math.max(...heights)+20,channels:4,background:'#f3f0e5'}}).composite(snippets.map((input,i)=>({input,left:10+i*395,top:10}))).png().toBuffer());
console.log(JSON.stringify({pages:audit.map(p=>({id:p.id,pairs:p.numeric.length,phone:p.bodyPhonePx,axes:p.columnAudit,shrink:p.shrink})),generated:'390px previews, native PNG crops, suite overview, price preview'}));
