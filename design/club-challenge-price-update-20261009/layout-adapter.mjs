import {createHash} from 'node:crypto';

// Native-image coordinates for the approved single-round challenge campaign.
export function makeLayout(content){
const ink='#203F53',secondary='#55788B',gold='#A8651B',teal='#117D72',paper='#FFFDF5';
const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
const requests=[],ids=new Map();
function request(text,font=CN,style=1){const key=JSON.stringify([String(text),font,style]);if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text:String(text),font,style});}return ids.get(key);}
function page(id,label,bg,w,h,ew,eh){return {id,label,bg,w,h,ew,eh,elements:[]};}
function tx(p,text,x,cy,size=16,{font=CN,style=1,fill=ink,anchor='start',maxWidth,role='copy',stroke,strokeWidth}={}){if(text===undefined||text===null)throw Error('Missing copy');p.elements.push({type:'text',id:request(text,font,style),text:String(text),x,cy,size,fill,anchor,maxWidth,role,stroke,strokeWidth});}
function art(p,text,x,cy,size,{maxWidth,fill=ink,shadow='#A5BDD1',outline=paper,outlineWidth=3,angles=true}={}){p.elements.push({type:'art',text,x,cy,size,maxWidth,fill,shadow,outline,outlineWidth,angles,ids:[...text].map(c=>request(c,ART,0))});}
function raw(p,markup){p.elements.push({type:'raw',markup});}
function numericGroup(p,runs,x,cy,size,fill,role,group,maxWidth){p.elements.push({type:'rich',runs:runs.map(([text,runSize,gap])=>({id:request(text,NUM,1),text:String(text),size:runSize??size,fill,gap})),x,cy,size,fill,anchor:'middle',maxWidth,role,group});}
function brand(p,x,cy,size,halo=false){const k=size/28,d='M0 27L15 0L31 27Z M8 22L15 10L23 22Z';raw(p,`<g transform="translate(${x} ${cy-size*.52}) scale(${k})" fill="none" stroke-linejoin="round">${halo?`<path d="${d}" stroke="${paper}" stroke-width="7"/>`:''}<path d="${d}" stroke="${ink}" stroke-width="2.1"/></g>`);tx(p,content.brand,x+size*1.35,cy,size,{role:'brand',stroke:halo?paper:undefined,strokeWidth:halo?3:undefined});}
function number(p,value,unit,unitX,cy,size,fill,unitSize=size*.955,role='number',gap=7){tx(p,value,unitX-gap,cy,size,{font:NUM,fill,anchor:'end',role:role+'-number'});tx(p,unit,unitX,cy,unitSize,{font:NUM,fill,role:role+'-unit'});}
function lines(p,items,x,cy,size,spacing,{maxWidth,fill=ink,style=0,role='rule-body',anchor='start'}={}){items.forEach((s,j)=>tx(p,s,x,cy+(j-(items.length-1)/2)*spacing,size,{maxWidth,fill,style,role,anchor}));}
function centers(edges){return edges.slice(0,-1).map((v,i)=>(v+edges[i+1])/2);}
function section(p,title,cy,rightText){art(p,title,68,cy,25,{maxWidth:190,fill:paper,shadow:'#142E42',outline:ink,outlineWidth:.6,angles:false});if(rightText)tx(p,rightText,765,cy,16.3,{anchor:'end',fill:'#E5F0F5',maxWidth:468,role:'section-condition'});}
if(content.tiers.length!==4||content.tiers.some(t=>t.maps.length!==3||t.maps.some(m=>m.rows.length!==3)))throw Error('This layout requires four categories, three maps and three tiers per map.');
if(content.mechanism.comparison!=='>'||content.tiers.some(t=>t.maps.some(m=>m.rows.some(r=>r.comparison!=='>'))))throw Error('The confirmed campaign requires strict greater-than.');
const mechanism=content.mechanism;
function poster(t){
 const color=t.color==='gold'?gold:teal,rules=content.rules[t.mode];
 const p=page(t.id,`${t.platform} · ${t.mode}版 · ${content.title}`,t.color==='gold'?'poster-gold.png':'poster-teal.png',836,t.color==='gold'?1881:1882,3200,7200);
 brand(p,60,65,24,true);
 art(p,content.titleLines[0],62,140,80,{maxWidth:245,outlineWidth:3.8});
 art(p,content.titleLines[1],51,227,78,{maxWidth:259,outlineWidth:3.8});
 tx(p,`${t.platform} · ${t.mode}版`,190,302,24.5,{anchor:'middle',fill:paper,maxWidth:260,role:'platform-mode'});
 art(p,mechanism.condition,159,367,25,{maxWidth:603,fill:color,outlineWidth:1,angles:false});
 tx(p,mechanism.equalClarification+' · 不跨局累计',160,395,18.6,{maxWidth:603,role:'strict-equality-and-accumulation'});
 const groups=[{header:460,edges:[481,533,583,634]},{header:669.5,edges:[691,742,792,844]},{header:880,edges:[901,953,1004,1054]}];
 p.alignment=[];
 t.maps.forEach((m,gi)=>{const g=groups[gi],rowCenters=centers(g.edges);
  tx(p,t.mode+m.name,69,g.header,23.5,{maxWidth:155,fill:paper,role:'map-title'});
  tx(p,content.columns.price,282,g.header,17.4,{anchor:'middle',fill:paper,role:'table-heading'});
  tx(p,content.columns.loot,633,g.header,18.5,{anchor:'middle',fill:paper,role:'table-heading'});
  m.rows.forEach((r,ri)=>{const cy=rowCenters[ri];number(p,r.priceR,'r',310,cy,48,ink,45.8,'price',8);tx(p,r.comparison,511,cy,56,{font:NUM,fill:color,anchor:'middle',role:'strict-comparison'});number(p,r.thresholdW,'w',698,cy,50,color,47.75,'threshold',8);});
  p.alignment.push({map:m.name,rowCenters,rowEdges:g.edges,unitColumns:{priceR:310,thresholdW:698},comparisonColumn:511,numberGap:8,vertical:'visible glyph center',noRightIcons:true});
 });
 tx(p,'r = 元 / w = 万',783,1065,10.2,{anchor:'end',fill:secondary,style:0,role:'units'});
 tx(p,mechanism.guaranteeTitle,205,1094,15.4,{fill:color,role:'guarantee-label'});
 art(p,mechanism.unmetAction.replace('，',''),205,1127,39,{maxWidth:558,fill:color,outlineWidth:1.6,angles:false});
 tx(p,'单局挑战 · 不跨局累计',206,1161,20.5,{maxWidth:552,role:'guarantee-condition'});
 section(p,'特别声明',1214.5,'物资归属 · 补偿结算 · 存储限制');
 const declarations=[['物资归属',mechanism.ownershipBeforeCompletion],['补偿结算',mechanism.failedOrderCompensation.condition],['存储限制',mechanism.storageRestriction]];
 declarations.forEach(([label,body],i)=>{const cy=centers([1246,1295,1345,1400])[i];tx(p,label,158,cy,18.5,{maxWidth:128,role:'declaration-label'});tx(p,body,307,cy,18,{maxWidth:461,style:0,role:'declaration-body'});});
 section(p,'下单须知',1439,'地图 · 房卡 · 理包 · 装备');
 tx(p,'地图选择',158,1499.5,18.5,{maxWidth:128,role:'rule-label'});
 lines(p,['仅限 '+rules.maps.map(m=>t.mode+m).join(' / '),`默认${t.mode}${rules.defaultMap}；可选地图按本版价目表。`],307,1499.5,16.3,23,{maxWidth:461,role:'map-restriction'});
 tx(p,'房卡保障',158,1562,18.5,{maxWidth:128,role:'rule-label'});
 tx(p,'含'+rules.cards.included+'；每少一张补偿：',307,1548,16.3,{maxWidth:461,style:0,role:'included-cards'});
 rules.cards.missingPerCard.forEach((v,i)=>{tx(p,v.label,i===0?307:554,1579,17.7,{role:'missing-card'});number(p,'+'+v.amountW,'w',i===0?494:736,1579,23,color,22,'missing-card-compensation',5);});
 tx(p,'理包与速撤',158,1627,18.5,{maxWidth:128,role:'rule-label'});
 lines(p,[mechanism.packing,mechanism.operatorQuickExtraction],307,1627,17.6,24,{maxWidth:461,role:'packing-quick-extraction'});
 tx(p,'装备例外',158,1691.5,18.5,{maxWidth:128,role:'rule-label'});
 const e=mechanism.specialEquipmentPolicy;
 lines(p,[e.items.slice(0,3).join('、')+'，',e.items[3]+'，'+e.treatment+'。'],307,1691.5,17.3,24,{maxWidth:461,role:'equipment-exception'});
 tx(p,content.footerInstruction,418,1829,15.2,{anchor:'middle',maxWidth:390,role:'footer'});
 return p;
}
function cover(){
 const p=page('cover',`${content.title} · 横版封面`,'cover.png',1672,941,3200,1800);
 brand(p,98,130,38);
 art(p,content.titleLines[0],103,280,156,{maxWidth:550,outlineWidth:6});
 art(p,content.titleLines[1],84,468,174,{maxWidth:560,outlineWidth:7});
 tx(p,content.subtitle,375,663,35,{anchor:'middle',fill:paper,maxWidth:601,role:'mechanism'});
 tx(p,'端游 / 手游 · 绝密 / 机密',95,740,26.8,{maxWidth:580,stroke:paper,strokeWidth:3,role:'versions'});
 tx(p,'单局严格大于目标 · 不跨局累计',95,786,22,{maxWidth:580,stroke:paper,strokeWidth:3,role:'strict-condition'});
 return p;
}
function social(){
 const p=page('social',`${content.title} · 简洁宣发海报`,'social.png',1086,1448,2400,3200);
 brand(p,71,77,29);
 art(p,content.titleLines[0],77,172,95,{maxWidth:349,outlineWidth:4});
 art(p,content.titleLines[1],64,278,102,{maxWidth:367,outlineWidth:4.4});
 art(p,mechanism.condition,159,416,29,{maxWidth:847,outlineWidth:1.3,angles:false});
 tx(p,mechanism.equalClarification+' · 不跨局累计',160,449,19,{maxWidth:847,role:'strict-equality-and-accumulation'});
 const cards=[{mode:502.5,header:542.5,edges:[565,628,697,763],cols:[53,157,279,400,525]},{mode:502.5,header:542.5,edges:[565,628,697,763],cols:[560,666,787,907,1033]},{mode:806,header:845.5,edges:[867,931,1000,1067],cols:[53,157,279,400,525]},{mode:806,header:845.5,edges:[867,931,1000,1067],cols:[560,666,787,907,1033]}];
 p.alignment=[];
 content.tiers.forEach((t,ti)=>{const c=cards[ti],color=t.color==='gold'?gold:teal,rows=centers(c.edges),priceUnitX=c.cols[1]-18;
  if(t.platform==='手游'){
   // Preserve the approved card outline; replace only the internal four-column grid.
   // Each map needs its own prices, so every cell shows a price above its target.
   const left=c.cols[0],right=c.cols[4],cols=[left,left+(right-left)/3,left+2*(right-left)/3,right];
   const clip=t.id+'-grid';
   raw(p,`<defs><clipPath id="${clip}"><rect x="${left}" y="788" width="${right-left}" height="279" rx="14"/></clipPath></defs><g clip-path="url(#${clip})"><rect x="${left}" y="824" width="${right-left}" height="43" fill="${ink}"/><rect x="${left}" y="867" width="${right-left}" height="64" fill="#EAF5FB"/><rect x="${left}" y="931" width="${right-left}" height="69" fill="#FFFFFF"/><rect x="${left}" y="1000" width="${right-left}" height="67" fill="${t.color==='gold'?'#FAF0DB':'#DEF6EF'}"/>${cols.slice(1,-1).map(x=>`<path d="M${x} 824V1067" fill="none" stroke="#A8CDDF" stroke-width="1.1"/>`).join('')}${[867,931,1000].map(y=>`<path d="M${left} ${y}H${right}" fill="none" stroke="#A8CDDF" stroke-width="1.1"/>`).join('')}</g>`);
   tx(p,`${t.platform} · ${t.mode}版`,left+139,c.mode,23.5,{anchor:'middle',fill:color,role:'platform-mode'});
   tx(p,'上：价格 / 下：目标',right-13,c.mode,14.5,{anchor:'end',fill:secondary,style:0,role:'price-target-legend'});
   const cells=[];
   t.maps.forEach((m,mi)=>{
    const x0=cols[mi],x1=cols[mi+1],cx=(x0+x1)/2;
    tx(p,m.name,cx,c.header,21,{anchor:'middle',fill:paper,maxWidth:x1-x0-16,role:'map-title'});
    m.rows.forEach((r,ri)=>{
     const cy=rows[ri],key=`${t.id}-${mi}-${ri}`;
     numericGroup(p,[[r.priceR,34],['r',32.5,4]],cx,cy-14.5,34,ink,'price',key+'-price',x1-x0-16);
     numericGroup(p,[[r.comparison,31],[r.thresholdW,29,8],['w',27.7,3]],cx,cy+14.5,29,color,'threshold',key+'-target',x1-x0-16);
     cells.push({map:m.name,row:ri,rowCenter:cy,container:[x0,c.edges[ri],x1,c.edges[ri+1]],groups:[key+'-price',key+'-target']});
    });
   });
   p.alignment.push({id:t.id,rowCenters:rows,rowEdges:c.edges,colEdges:cols,sharedPriceRows:false,cells,comparisons:'strict > per map',cellDisplay:'top price / bottom single-round target'});
   return;
  }
  if(t.maps.some(m=>m.rows.some((r,i)=>r.priceR!==t.maps[0].rows[i].priceR)))throw Error('The matrix requires shared price tiers across maps within a category.');
  tx(p,`${t.platform} · ${t.mode}版`,(c.cols[0]+c.cols[4])/2,c.mode,23.5,{anchor:'middle',fill:color,role:'platform-mode'});
  tx(p,'价格',(c.cols[0]+c.cols[1])/2,c.header,18.4,{anchor:'middle',fill:paper,role:'table-heading'});
  t.maps[0].rows.forEach((r,i)=>number(p,r.priceR,'r',priceUnitX,rows[i],31,ink,29.6,'price',4));
  const units=[];
  t.maps.forEach((m,mi)=>{const x0=c.cols[mi+1],x1=c.cols[mi+2],unitX=x1-32;
   tx(p,m.name,(x0+x1)/2,c.header,21,{anchor:'middle',fill:paper,maxWidth:x1-x0-10,role:'map-title'});
   m.rows.forEach((r,i)=>{tx(p,r.comparison,x0+12,rows[i],34.8,{anchor:'middle',font:NUM,fill:color,role:'strict-comparison'});number(p,r.thresholdW,'w',unitX,rows[i],31,color,29.6,'threshold',3);});
   units.push({map:m.name,x:unitX,cellLeft:x0,cellRight:x1});
  });
  p.alignment.push({id:t.id,rowCenters:rows,rowEdges:c.edges,colEdges:c.cols,priceUnitColumn:priceUnitX,thresholdUnitColumns:units,sharedPriceRows:true,comparisons:'strict > per target cell'});
 });
 tx(p,mechanism.guaranteeTitle,282,1128,18.8,{fill:secondary,role:'guarantee-label'});
 art(p,mechanism.unmetAction.replace('，',''),282,1178,49,{maxWidth:721,outlineWidth:1.7,angles:false});
 tx(p,'单局挑战 · 不跨局累计',283,1225,26,{maxWidth:723,role:'guarantee-condition'});
 lines(p,[mechanism.ownershipBeforeCompletion,mechanism.failedOrderCompensation.condition],283,1274,19.2,29,{maxWidth:723,role:'core-conditions'});
 tx(p,'r = 元 / w = 万',543,1383,12.5,{anchor:'middle',fill:secondary,style:0,role:'units'});
 return p;
}
const selected=content.deliveryPageIds??['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret','cover','social'];
const pages=[...content.tiers.filter(t=>selected.includes(t.id)).map(poster),...(selected.includes('cover')?[cover()]:[]),...(selected.includes('social')?[social()]:[])];
if(pages.length!==selected.length)throw Error('Unknown or duplicate delivery page ID.');
for(const p of pages)p.platforms=p.id.startsWith('desktop-')?['desktop']:p.id.startsWith('mobile-')?['mobile']:['desktop','mobile'];
return {version:'challenge-mobile-price-r2',requests,pages};
}
