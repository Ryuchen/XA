const fs=require('fs'),path=require('path');const sharp=require(process.argv[2]);const root=path.resolve(__dirname,'..');
const read=f=>JSON.parse(fs.readFileSync(path.join(root,f),'utf8').replace(/^\uFEFF/,''));
const round=x=>Math.round(x*1000)/1000;
function bounds(items){if(!items.length)throw Error('Missing text group');const x=Math.min(...items.map(b=>b.x)),y=Math.min(...items.map(b=>b.y)),right=Math.max(...items.map(b=>b.x+b.width)),bottom=Math.max(...items.map(b=>b.y+b.height));return{x,y,right,bottom,cx:(x+right)/2,cy:(y+bottom)/2};}
(async()=>{
 const plan=read('layout.json'),content=read('content.json'),report={revision:'uiux-typography-r4',pages:[],failures:[]};
 for(const p of plan.pages){const text=read(`review/${p.id}-text-bounds.json`),page={id:p.id,blocks:[],checks:[],fonts:[...new Set(text.filter(b=>b.role!=='art-title').map(b=>b.font))]};
  const check=(id,type,value,passed)=>{page.checks.push({id,type,value,passed});if(!passed)report.failures.push({page:p.id,id,type,value});};
  let overlay='';
  for(const c of p.containers){const b=bounds(text.filter(t=>t.group===c.id||t.group?.startsWith(c.id+'-'))),[x,y,w,h]=c.safe;
   const policy=['brand','headline','game'].includes(c.id)?'left':c.id.startsWith('card-')||c.id.startsWith('offer-')?'numeric-columns':p.id.includes('experience')&&c.id.startsWith('header-')?'split':'center';
   const dx=b.cx-x-w/2,dy=b.cy-y-h/2,inside=b.x>=x-.5&&b.y>=y-.5&&b.right<=x+w+.5&&b.bottom<=y+h+.5;
   page.blocks.push({id:c.id,policy,dx:round(dx),dy:round(dy),margins:[b.x-x,x+w-b.right,b.y-y,y+h-b.bottom].map(round)});
   check(c.id,'inside-real-safe-area',null,inside);if(c.id!=='brand')check(c.id,'vertical-balance',round(dy),Math.abs(dy)<=3);
   if(policy==='center')check(c.id,'horizontal-balance',round(dx),Math.abs(dx)<=2);
   overlay+=`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="#149783" stroke-width="1.2" stroke-dasharray="6 4"/><rect x="${b.x}" y="${b.y}" width="${b.right-b.x}" height="${b.bottom-b.y}" fill="none" stroke="#df7937" stroke-width="1.2"/>`;
  }
  check('all-copy','no-text-intersections',null,read(`review/${p.id}-text-overlaps.json`).length===0);
  check('all-copy','MiSans-body-font',page.fonts,page.fonts.every(f=>f.startsWith('MiSans')));
  const units=text.filter(t=>/^(price|loot)-unit$/.test(t.role));page.numberPairs=units.length;
  for(const u of units){const n=text.find(t=>t.group===u.group&&/^(price|loot)-number$/.test(t.role));check(u.group,'common-number-unit-baseline',round(u.baseline-n.baseline),Math.abs(u.baseline-n.baseline)<.01);check(u.group,'visible-number-unit-gap',round(u.x-n.x-n.width),u.x-n.x-n.width>=5.9);}
  const sameAxis=(group,items)=>{const d=Math.max(...items.map(t=>t.x))-Math.min(...items.map(t=>t.x));check(group,'fixed-column-axis',round(d),d<.01);};
  if(p.id.includes('experience')){
   const tier=content.tiers.find(t=>t.id===p.id);for(let i=0;i<2;i++)for(let j=0;j<3;j++){
    const group=`card-${i}-${j}`,o=tier.sections[i].offers[j];for(const [suffix,value]of [['price',o.price],['loot',o.guarantee]])check(group+'-'+suffix,'displayed-offer-matches-source',value,text.find(t=>t.group===group+'-'+suffix&&t.role===suffix+'-number').text===String(value));
    const label=text.find(t=>t.group===group&&t.text==='保底'),mode=text.find(t=>t.group===group&&t.text.includes(o.mode)),price=bounds(text.filter(t=>t.group===group+'-price')),loot=bounds(text.filter(t=>t.group===group+'-loot'));
    const d=label.x+label.width/2-mode.x-mode.width/2,gap=loot.y-label.y-label.height;
    check(group,'mode-caption-common-center',round(d),Math.abs(d)<.02);check(group,'guarantee-label-value-proximity',round(gap),gap>=5&&gap<=12);check(group,'price-guarantee-separation',round(label.y-price.bottom),label.y-price.bottom>=5);
   }
   for(let j=0;j<3;j++)for(const s of ['price','loot'])sameAxis(`card-column-${j}-${s}`,units.filter(u=>[0,1].some(i=>u.group===`card-${i}-${j}-${s}`)));
   const eq=text.filter(t=>t.group==='equipment'||t.group.startsWith('equipment-')),labels=eq.filter(t=>t.role==='rule-label');sameAxis('equipment-labels',labels);
   const rows=new Map();for(const t of eq.filter(t=>t.role!=='rule-label'&&t.text!=='装备规范')){const key=t.lineGroup||t.group;if(!rows.has(key))rows.set(key,[]);rows.get(key).push(t);}const rowBoxes=[...rows].map(([id,ts])=>({id,ts,b:bounds(ts)})).sort((a,b)=>a.b.y-b.b.y);
   const starts=rowBoxes.map(r=>r.b.x),startDelta=Math.max(...starts)-Math.min(...starts);check('equipment-body','hanging-indent',round(startDelta),startDelta<.02);
   for(const label of labels){const row=rowBoxes.find(r=>r.id===label.lineGroup);const d=label.baseline-row.ts[0].baseline;check(label.group,'label-description-baseline',round(d),Math.abs(d)<.01);}
   for(let i=1;i<rowBoxes.length;i++){const gap=rowBoxes[i].b.y-rowBoxes[i-1].b.bottom;check('equipment-row-'+i,'line-spacing',round(gap),gap>=7.5);}
   const detail=text.filter(t=>t.group==='map'&&['核电站 / 巴克什','航天','航天 / 巴克什'].includes(t.text));sameAxis('map-detailed-lines',detail);
   for(const mode of ['secret','absolute']){const b=bounds(text.filter(t=>t.group==='compensation-'+mode)),label=text.find(t=>t.group==='compensation'&&t.text===(mode==='secret'?'机密':'绝密'));const d=b.cx-label.x-label.width/2;check('compensation-'+mode,'mode-number-common-center',round(d),Math.abs(d)<.01);}
   const normal=eq.filter(t=>t.role==='rule-body');check('equipment','normal-and-emphasis-weights',null,normal.some(t=>t.fontWeight===400)&&eq.some(t=>t.role==='key-condition'&&t.fontWeight>=600));
  }else if(p.id==='social'){
   for(let i=0;i<4;i++){const sec=content.tiers[i%2].sections[i<2?0:1];for(let j=0;j<3;j++)for(const [s,v]of [['price',sec.offers[j].price],['loot',sec.offers[j].guarantee]])check(`offer-${i}-${j}-${s}`,'displayed-offer-matches-source',v,text.find(t=>t.group===`offer-${i}-${j}-${s}`&&t.role===s+'-number').text===String(v));for(const s of ['price','loot'])sameAxis(`offer-column-${i}-${s}`,units.filter(t=>t.group.startsWith(`offer-${i}-`)&&t.group.endsWith('-'+s)));}
  }
  const bg=fs.readFileSync(path.join(root,`review/${p.id}.png`)).toString('base64');await sharp(Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${p.w}" height="${p.h}"><image width="${p.w}" height="${p.h}" href="data:image/png;base64,${bg}"/>${overlay}</svg>`)).png().toFile(path.join(root,`review/${p.id}-typography-proof-r4.png`));
  report.pages.push(page);
 }
 report.summary={pages:report.pages.length,blocks:report.pages.reduce((s,p)=>s+p.blocks.length,0),numberPairs:report.pages.reduce((s,p)=>s+p.numberPairs,0),checks:report.pages.reduce((s,p)=>s+p.checks.length,0),failures:report.failures.length};
 fs.writeFileSync(path.join(root,'review/typography-audit-r4.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report.summary));if(report.failures.length){console.log(JSON.stringify(report.failures));process.exitCode=1;}
 const before=await sharp(path.join(root,'review/typography-before-r4/mobile-experience.png')).extract({left:35,top:1090,width:814,height:510}).png().toBuffer(),after=await sharp(path.join(root,'review/mobile-experience.png')).extract({left:35,top:1090,width:814,height:510}).png().toBuffer();
 const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1680" height="595"><rect width="1680" height="595" fill="#edf2f4"/><text x="20" y="38" font-family="MiSans VF Semibold" font-size="24" fill="#203f53">上一版</text><text x="852" y="38" font-family="MiSans VF Semibold" font-size="24" fill="#203f53">本轮：重点与内容分组</text><image x="18" y="65" width="814" height="510" href="data:image/png;base64,${before.toString('base64')}"/><image x="848" y="65" width="814" height="510" href="data:image/png;base64,${after.toString('base64')}"/></svg>`;
 await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review/typography-before-after-r4.png'));
})();
