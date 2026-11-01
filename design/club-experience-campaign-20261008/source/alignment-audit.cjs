const fs=require('fs'),path=require('path');const sharp=require(process.argv[2]);const root=path.resolve(__dirname,'..');const read=f=>JSON.parse(fs.readFileSync(path.join(root,f),'utf8').replace(/^\uFEFF/,''));
const bb=items=>{const x=Math.min(...items.map(b=>b.x)),y=Math.min(...items.map(b=>b.y)),r=Math.max(...items.map(b=>b.x+b.width)),b=Math.max(...items.map(b=>b.y+b.height));return{x,y,w:r-x,h:b-y,cx:(x+r)/2,cy:(y+b)/2};};
const round=n=>Math.round(n*100)/100;
(async()=>{const plan=read('layout.json'),generic=read('review/final-layout-audit.json'),report={pages:[],failures:[],policies:{free:'品牌和艺术标题按共同左锚点，不要求居中',center:'胶囊、平台行、标题栏、规则组、补偿和页脚按各自安全区居中',columns:'价目使用固定r/w列；短数字右对齐，数字可见中心不强求逐档相同',ruleBody:'多行正文内部左对齐，但按最长行宽度将段落组放在规则区中央',vertical:'文字语义组按真实卡片的安全区居中；普通价目保底行下移使上下留白平衡'}};
for(const p of plan.pages){const bounds=read('review/'+p.id+'-text-bounds.json'),old=read('review/alignment-before.json').pages.find(q=>q.id===p.id),a=generic.pages.find(q=>q.id===p.id),checks=[];let overlay='';
for(const c of p.containers){const items=bounds.filter(b=>b.group===c.id||b.group?.startsWith(c.id+'-'));if(!items.length)continue;const b=bb(items),[x,y,w,h]=c.safe;const before=old.checks.find(q=>q.container===c.id);let horizontal=['brand','headline'].includes(c.id)?'left':c.id.startsWith('card-')||c.id.startsWith('offer-')?'columns':p.id.includes('experience')&&c.id.startsWith('header-')?'split-left-right':'center';const dy=b.cy-y-h/2,dx=b.cx-x-w/2;
const failures=[];if(c.id!=='brand'&&Math.abs(dy)>3)failures.push('vertical-center');if(horizontal==='center'&&Math.abs(dx)>2)failures.push('horizontal-center');if(b.x<x-.5||b.y<y-.5||b.x+b.w>x+w+.5||b.y+b.h>y+h+.5)failures.push('containment');
const result={block:c.id,policy:horizontal,verticalOffset:round(dy),horizontalOffset:round(dx),beforeVerticalOffset:before?.verticalOffset,beforeHorizontalOffset:before?round((before.margins.left-before.margins.right)/2):null,passed:!failures.length,failures};checks.push(result);for(const f of failures)report.failures.push({page:p.id,block:c.id,type:f});
overlay+=`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="#149783" stroke-width="1.3" stroke-dasharray="6 4"/><rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" fill="none" stroke="#df7937" stroke-width="1.4"/><path d="M${x} ${y+h/2}h${w} M${x+w/2} ${y}v${h}" stroke="#366cb2" stroke-opacity=".55" stroke-width=".9"/>`;
}
const numberGroups=bounds.filter(b=>b.role==='price-unit'||b.role==='loot-unit');const numberChecks=numberGroups.map(u=>{const n=bounds.find(b=>b.group===u.group&&b.role?.endsWith('-number'));return{group:u.group,baselineDelta:round(u.baseline-n.baseline),visibleGap:round(u.x-n.x-n.width),passed:Math.abs(u.baseline-n.baseline)<.01&&u.x-n.x-n.width>=5.9};});
for(const q of numberChecks)if(!q.passed)report.failures.push({page:p.id,block:q.group,type:'numeric-baseline-gap'});
let columnChecks=[];if(p.id.includes('experience'))for(let j=0;j<3;j++)for(const suffix of ['price','loot']){let u=numberGroups.filter(b=>b.group===`card-0-${j}-${suffix}`||b.group===`card-1-${j}-${suffix}`);const delta=u[0].x-u[1].x;columnChecks.push({column:j,type:suffix,axisDelta:round(delta),passed:Math.abs(delta)<.01});}
if(p.id==='social')for(let i=0;i<4;i++)for(const suffix of ['price','loot']){const u=numberGroups.filter(b=>b.group.startsWith(`offer-${i}-`)&&b.group.endsWith('-'+suffix));const delta=Math.max(...u.map(b=>b.x))-Math.min(...u.map(b=>b.x));columnChecks.push({column:i,type:suffix,axisDelta:round(delta),passed:delta<.01});}
for(const q of columnChecks)if(!q.passed)report.failures.push({page:p.id,block:q.column,type:'column-axis'});
let compensation=[];if(p.id.includes('experience')){const labels=bounds.filter(b=>b.group==='compensation'&&['机密','绝密'].includes(b.text));for(let i=0;i<2;i++){const parts=bounds.filter(b=>b.group==='compensation-'+(i?'absolute':'secret')),b=bb(parts),label=labels.find(q=>q.text===(i?'绝密':'机密')),delta=b.cx-label.x-label.width/2;compensation.push({mode:label.text,centerDelta:round(delta),passed:Math.abs(delta)<.01});if(Math.abs(delta)>=.01)report.failures.push({page:p.id,block:'compensation-'+i,type:'label-number-center'});}}
const fonts=[...new Set(bounds.filter(b=>b.role!=='art-title').map(b=>b.font))];if(fonts.some(f=>!f.startsWith('MiSans')))report.failures.push({page:p.id,type:'body-font'});
const internalChecks=[];
const check=(block,test,delta,passed)=>{internalChecks.push({block,test,measured:round(delta),passed});if(!passed)report.failures.push({page:p.id,block,type:test});};
const sameLeft=(block,items)=>{const delta=Math.max(...items.map(b=>b.x))-Math.min(...items.map(b=>b.x));check(block,'consistent-left-edge',delta,delta<.02);};
if(p.id.includes('experience')){
 for(let i=0;i<2;i++)for(let j=0;j<3;j++){
  const g=`card-${i}-${j}`,labels=bounds.filter(b=>b.group===g),mode=labels.find(b=>b.text!=='保底'),caption=labels.find(b=>b.text==='保底'),price=bb(bounds.filter(b=>b.group===g+'-price')),loot=bb(bounds.filter(b=>b.group===g+'-loot'));
  const centerDelta=(mode.x+mode.width/2)-(caption.x+caption.width/2),captionGap=loot.y-caption.y-caption.height,priceGap=caption.y-price.y-price.h;
  check(g,'mode-caption-common-center',centerDelta,Math.abs(centerDelta)<.02);
  check(g,'guarantee-caption-group-gap',captionGap,captionGap>=5&&captionGap<=12);
  check(g,'price-guarantee-hierarchy',priceGap,priceGap>=5&&captionGap<=priceGap+1);
  check(g,'mode-price-gap',price.y-mode.y-mode.height,price.y-mode.y-mode.height>=5);
 }
 const equip=bounds.filter(b=>b.group==='equipment'||b.group.startsWith('equipment-'));
 sameLeft('equipment-labels',equip.filter(b=>b.role==='rule-label'));
 sameLeft('equipment-body',equip.filter(b=>b.role==='rule-body'));
 for(const g of ['equipment-room-secret','equipment-bag-secret','equipment-revolver']){const row=equip.filter(b=>b.group===g),delta=row[0].baseline-row[1].baseline;check(g,'label-body-baseline',delta,Math.abs(delta)<.01);}
 const body=equip.filter(b=>b.role==='rule-body').sort((a,b)=>a.y-b.y);
 for(let i=1;i<body.length;i++){const gap=body[i].y-body[i-1].y-body[i-1].height;check('equipment-line-'+i,'readable-line-gap',gap,gap>=3.5);}
 const map=bounds.filter(b=>b.group==='map');sameLeft('map-intro',map.filter(b=>['打手选图，下单可备注','但需要排队。','机密','绝密'].includes(b.text)));sameLeft('map-details',map.filter(b=>['核电站 / 巴克什','航天','航天 / 巴克什'].includes(b.text)));
 const restriction=bounds.filter(b=>b.group==='restriction'),centers=restriction.map(b=>b.x+b.width/2),centerDelta=Math.max(...centers)-Math.min(...centers);check('restriction','title-body-common-center',centerDelta,centerDelta<.02);
}else if(p.id==='social'){
 for(let i=0;i<4;i++){const modes=bounds.filter(b=>b.group?.startsWith(`offer-${i}-`)&&!b.role);sameLeft('offer-panel-'+i,modes.filter(b=>b.text!=='保'));sameLeft('offer-panel-'+i+'-guarantee-caption',modes.filter(b=>b.text==='保'));}
}
report.pages.push({id:p.id,checks,numberChecks,columnChecks,compensation,internalChecks,fonts});const bg=fs.readFileSync(path.join(root,'review',p.id+'.png')).toString('base64');const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${p.w}" height="${p.h}"><image width="${p.w}" height="${p.h}" href="data:image/png;base64,${bg}"/>${overlay}</svg>`;await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review',p.id+'-alignment-proof.png'));}
report.summary={pages:report.pages.length,blocks:report.pages.reduce((s,p)=>s+p.checks.length,0),numberPairs:report.pages.reduce((s,p)=>s+p.numberChecks.length,0),unitColumns:report.pages.reduce((s,p)=>s+p.columnChecks.length,0),internalChecks:report.pages.reduce((s,p)=>s+p.internalChecks.length,0),failures:report.failures.length};fs.writeFileSync(path.join(root,'review','alignment-audit-r2.json'),JSON.stringify(report,null,2));
const specs=[['price','价目卡：文案组与数值列',380,615],['rules','下单须知：段落及补偿标签',1090,510]];let composite=[];for(const [id,label,top,height]of specs){for(const [side,folder]of [['before','alignment-before-png'],['after','']]){let file=path.join(root,'review',folder,'mobile-experience.png');const crop=await sharp(file).extract({left:30,top,width:827,height}).png().toBuffer();composite.push({id,side,label,width:827,height,data:crop.toString('base64')});}}
let svg='<svg xmlns="http://www.w3.org/2000/svg" width="1720" height="1300"><rect width="1720" height="1300" fill="#edf2f4"/><text x="35" y="36" font-family="Microsoft YaHei" font-size="24" fill="#203f53">调整前</text><text x="885" y="36" font-family="Microsoft YaHei" font-size="24" fill="#203f53">对齐修正后</text>';
for(const c of composite){const x=c.side==='before'?24:870,y=c.id==='price'?60:735;svg+=`<image x="${x}" y="${y}" width="827" height="${c.height}" href="data:image/png;base64,${c.data}"/>`;}
svg+='</svg>';await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review','alignment-before-after-r2.png'));console.log(JSON.stringify(report.summary));if(report.failures.length){console.log(JSON.stringify(report.failures));process.exitCode=1;}
})();
