import {createHash} from 'node:crypto';
export function makeLayout(content,m,glyphs={}){
 const C={ink:'#203F53',muted:'#526F80',gold:'#A96620',teal:'#117C72',paper:'#FFFDF5'};
 const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
 const requests=[],ids=new Map(),placements=[];
 const hasMetrics=Object.keys(glyphs).length>0;
 const request=(text,font=CN,style=1)=>{text=String(text);const key=JSON.stringify([text,font,style]);if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text,font,style});}return ids.get(key);};
 const metric=(text,font=CN,style=1)=>{const id=request(text,font,style);return glyphs[id]??{width:[...String(text)].length*90,height:98};};
 const width=(text,size,font=CN,style=1)=>metric(text,font,style).width*size/100;
 const height=(text,size,font=CN,style=1)=>metric(text,font,style).height*size/100;
 const page=(id,label,bg,w,h,ew,eh)=>({id,label,bg,w,h,ew,eh,platforms:id==='cover'?['desktop','mobile']:[id.includes('mobile')?'mobile':'desktop'],elements:[]});
 function tx(p,text,x,cy,size=27,{font=CN,style=1,fill=C.ink,anchor='start',role='copy'}={}){
  p.elements.push({type:'text',id:request(text,font,style),text:String(text),x,cy,size,font,style,fill,anchor,role});
 }
 function raw(p,markup){p.elements.push({type:'raw',markup});}
 function art(p,text,cx,cy,size,{maxWidth=1000,fill=C.ink,shadow='#B2C6D7',outline=C.paper,outlineWidth=2.5,angles=true,role='art-title',anchor='middle'}={}){
  const ids=[...text].map(t=>request(t,ART,0));
  const natural=[...text].reduce((s,t)=>s+metric(t,ART,0).width/100,0)+.065*(ids.length-1);
  const finalSize=Math.min(size,maxWidth/natural),actualWidth=natural*finalSize;
  let left=cx-actualWidth/2,centerY=cy;
  const rotations=[-2.8,1.4,-1.7,2.2,-1.4],bumps=[1,-2,1,-1,0],points=[];
  let charX=left;
  for(let i=0;i<ids.length;i++){
   const g=metric([...text][i],ART,0),w=g.width*finalSize/100,h=g.height*finalSize/100,a=(angles?rotations[i%5]:0)*Math.PI/180,dy=angles?bumps[i%5]*finalSize/70:0;
   for(const [ox,oy,pad] of [[0,0,outlineWidth/2],[finalSize*.035,finalSize*.055,.3]])for(const x of [-w/2-pad,w/2+pad])for(const y of [-h/2-pad,h/2+pad])points.push([charX+w/2+(x+ox)*Math.cos(a)-(y+oy)*Math.sin(a),cy+dy+(x+ox)*Math.sin(a)+(y+oy)*Math.cos(a)]);
   charX+=w+finalSize*.065;
  }
  const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  left+=cx-(Math.min(...xs)+Math.max(...xs))/2;centerY+=cy-(Math.min(...ys)+Math.max(...ys))/2;
  p.elements.push({type:'art',text,ids,x:cx,cy,size:finalSize,maxWidth,fill,shadow,outline,outlineWidth,angles,role,anchor});
 }
 function brand(p,cx,cy,size=23,anchor='middle'){
  const k=size/28,triW=31*k,gap=size*.38,textW=width(content.brand,size),left=anchor==='start'?cx:cx-(triW+gap+textW)/2;
  raw(p,'<g transform="translate('+left+' '+(cy-13.5*k)+') scale('+k+')" fill="none" stroke="'+C.ink+'" stroke-width="2.1" stroke-linejoin="round"><path d="M0 27L15 0L31 27Z M8 22L15 10L23 22Z"/></g>');
  tx(p,content.brand,left+triW+gap,cy,size,{role:'brand'});
 }
 function lines(p,items,box,{size=27,gap=6,fill=C.ink,role='rule-body',align='left',style=0}={}){
  const strings=items.map(s=>typeof s==='string'?s:s.text),styles=items.map(s=>typeof s==='string'?style:(s.style??style));
  const widths=strings.map((s,i)=>width(s,size,CN,styles[i])),heights=strings.map((s,i)=>height(s,size,CN,styles[i]));
  const max=Math.max(...widths),total=heights.reduce((a,b)=>a+b,0)+gap*(items.length-1);
  if(hasMetrics&&(max>box[2]+.25||total>box[3]+.25))throw Error(p.id+' '+role+' 超出文字区：'+max.toFixed(2)+'×'+total.toFixed(2)+' / '+box[2]+'×'+box[3]+'；'+items.join('|'));
  const cx=box[0]+box[2]/2,cy=box[1]+box[3]/2,left=cx-max/2;
  let top=cy-total/2;
  strings.forEach((s,i)=>{tx(p,s,align==='center'?cx:left,top+heights[i]/2,size,{fill,role,style:styles[i],anchor:align==='center'?'middle':'start'});top+=heights[i]+gap;});
  placements.push({page:p.id,role,container:box,visibleWidth:max,visibleHeight:total,centerX:cx,centerY:cy,fontSize:size,phoneSize:size*390/p.w});
 }
 function unitSize(value,unit,size,ratio=.75){
  return height(value,size,NUM)/metric(unit,NUM).height*100*ratio;
 }
 function unitColumn(values,unit,col,size,gap=8){
  const nwidth=Math.max(...values.map(n=>width(n,size,NUM))),us=unitSize(String(values[0]),unit,size),uw=width(unit,us,NUM);
  return {x:(col[0]+col[1])/2+(nwidth+gap-uw)/2,unitSize:us,gap,size};
 }
 function number(p,value,unit,x,cy,size,color,role,gap=8){
  const us=unitSize(value,unit,size);
  tx(p,value,x-gap,cy,size,{font:NUM,anchor:'end',fill:color,role:role+'-number'});
  tx(p,unit,x,cy,us,{font:NUM,fill:color,role:role+'-unit'});
 }
 function compensatedRow(p,a,b,cy,color){
  const labelSize=23,numberSize=27;
  for(const [v,labelX,unitX] of [[a,263,414],[b,477,640]]){
   tx(p,v.item,labelX,cy,labelSize,{role:'compensation-item'});
   number(p,'+'+v.amountW,'w',unitX,cy,numberSize,color,'compensation',5);
  }
 }
 const allTiers=content.tiers.flatMap(t=>t.maps.flatMap(g=>g.rows));
 const priceColumn=unitColumn(allTiers.map(r=>r.priceR),'r',[42,235],35);
 const lootColumn=unitColumn(allTiers.map(r=>r.baseGuaranteeW),'w',[235,488],35);
 const rowCenters=e=>e.slice(0,-1).map((v,i)=>(v+e[i+1])/2);
 if(content.tiers.length!==4||allTiers.length!==36||content.tiers.some(t=>t.maps.length!==3||t.maps.some(g=>g.rows.length!==3)))throw Error('累计撤离单需要四类、每类三地图×三档，共36档');
 function poster(t){
  const r=content.rules[t.mode],mode=t.mode,color=t.color==='gold'?C.gold:C.teal,pm=m.poster;
  const croppedHeight=pm.heightByMode[t.color]-96;
  const p=page(t.id,t.platform+' · '+mode+'版 · '+content.title,'poster-'+t.color+'.png',724,croppedHeight,3200,Math.round(croppedHeight*3200/724));
  p.backgroundSlices=[{top:0,height:1098,destinationY:0},{top:1194,height:pm.heightByMode[t.color]-1194,destinationY:1098}];
  brand(p,178,46,20);
  art(p,content.titleLines[0],178,105,65,{maxWidth:232,outlineWidth:3.3});
  art(p,content.titleLines[1],178,176,60,{maxWidth:232,outlineWidth:3.3});
  tx(p,t.platform+' · '+mode+'版',176,235,18,{fill:C.paper,anchor:'middle',role:'platform-mode'});
  lines(p,[{text:'结单条件 · 两项必须同时满足',style:1},{text:content.mechanism.completion.copy.replace('，才可结单',''),style:1}],[170,280,500,57],{size:21.5,gap:7,role:'completion',align:'center'});
  t.maps.forEach((g,i)=>{
   const b=pm.maps[i];art(p,g.mapLabel,360,b.header[1]+13.5,19.5,{maxWidth:370,fill:C.paper,shadow:'#102D40',outline:C.ink,outlineWidth:.6,angles:false,role:'map-title'});
   if(i===0)tx(p,'r=元 / w=万',667,b.header[1]+13.5,13,{anchor:'end',fill:'#D8E9F0',role:'units'});
   ['订单价格','基础保底','累计撤离'].forEach((s,j)=>tx(p,s,(pm.colEdges[j]+pm.colEdges[j+1])/2,b.tableHeader[1]+b.tableHeader[3]/2,16,{anchor:'middle',role:'table-heading'}));
   g.rows.forEach((v,j)=>{
    const cy=rowCenters(b.rowEdges)[j];
    number(p,v.priceR,'r',priceColumn.x,cy,35,C.ink,'price');
    number(p,v.baseGuaranteeW,'w',lootColumn.x,cy,35,color,'loot');
    tx(p,'累计'+v.cumulativeExtractions+'把',(488+681)/2,cy,22,{anchor:'middle',role:'cumulative'});
   });
  });
  art(p,'有效撤离 · 两项均满足',360,917,18.5,{maxWidth:580,fill:C.paper,shadow:'#102D40',outline:C.ink,outlineWidth:.65,angles:false,role:'mechanism-title'});
  const valid=content.mechanism.validExtraction;
  lines(p,['每把成功撤离，须同时满足：',{text:'陪玩击杀≥'+valid.killsAtLeast+'人 ＋ 带出≥'+valid.lootWByMode[mode]+'w',style:1},'低于门槛局不计累计，物资归板板。'],pm.validityBodySafe,{size:23.5,gap:9,align:'center',role:'valid-extraction'});
  const notesStart=p.elements.length;
  art(p,'下单须知',360,1212,20.5,{maxWidth:200,fill:C.paper,shadow:'#102D40',outline:C.ink,outlineWidth:.6,angles:false,role:'notes-title'});
  for(const n of pm.notes)tx(p,n.id,184,n.bounds[1]+n.bounds[3]/2,22.5,{anchor:'middle',role:'rule-label'});
  const boxes=Object.fromEntries(pm.notes.map(n=>[n.id,n.bodySafe]));
  const cards=r.mapAndCards.byMap;
  const cardLines=['客服确认地图；换图需协商。','航天：'+cards['航天']+'。','巴克什：塔内/博物馆金红卡。',mode==='绝密'?'监狱：金红卡+一级监狱卡。':'核电站：'+cards['核电站']+'。'];
  lines(p,cardLines,boxes['房卡保障'],{size:23,gap:7,role:'card-rules'});
  lines(p,['默认≥'+r.equipment.backpackSlotsAtLeast+'格背包+一把左轮。','进图前核对；板板主动免带除外。'],boxes['随行装备'],{size:23,gap:6,role:'equipment-rules'});
  const missingBox=boxes['缺项补偿'],missingCy=missingBox[1]+missingBox[3]/2;
  const headingHeights=r.missingItems.map(v=>height(v.cause+'：',22.5));
  const itemHeights=r.missingItems.map(v=>Math.max(...v.items.map(item=>height(item.item,23)),...v.items.map(item=>height('+'+item.amountW,27,NUM))));
  const heights=[headingHeights[0],itemHeights[0],headingHeights[1],itemHeights[1]],rows=[];
  let rowTop=missingCy-(heights.reduce((a,b)=>a+b,0)+7.3*3)/2;
  for(const h of heights){rows.push(rowTop+h/2);rowTop+=h+7.3;}
  r.missingItems.forEach((cause,i)=>{
   tx(p,cause.cause+'：',263,rows[i*2],22.5,{fill:C.muted,role:'compensation-cause'});
   compensatedRow(p,cause.items[0],cause.items[1],rows[i*2+1],color);
  });
  if(mode==='绝密'){
   const by=r.failedOrderCompensation.byMap;
   lines(p,['符合俱乐部炸单条件，增加保底：','航天/巴克什+'+by['航天']+'w；监狱+'+by['监狱']+'w'],boxes['炸单补偿'],{size:23,gap:8,role:'failed-order-compensation'});
  }else lines(p,['符合俱乐部炸单条件：','每局增加'+r.failedOrderCompensation.eachRoundW+'w保底。'],boxes['炸单补偿'],{size:23,gap:8,role:'failed-order-compensation'});
  const safe=r.safeBoxSettlement;
  lines(p,['符合炸单标准：安全箱物资不计；','>'+safe.noLossCompensationWhenSafeBoxLootGreaterThanW+'w免战损；否则补累计保底。'],boxes['结算口径'],{size:23,gap:8,role:'safe-box-settlement'});
  lines(p,['达标前，成功撤离物资累计；','炸单保底高于累计物资保底时，','打满结单条件后，再补保底。'],boxes['物资说明'],{size:22.5,gap:6,role:'loot-settlement'});
  const q=content.mechanism.quickExtraction;
  lines(p,['满足击杀要求，且物资'+q.materialComparison+q.lootWByMode[mode]+'w','允许速撤。'],boxes['打手保障'],{size:23.5,gap:7,role:'quick-extraction',align:'center',fill:color});
  const extraction=content.mechanism.extractionPointPolicy,defaults=extraction.allowedByDefault;
  lines(p,['默认允许：航天'+defaults['航天']+'、巴克什'+defaults['巴克什']+'；','若'+extraction.priceUpliftWhen+'，','对应单价上浮'+extraction.priceUpliftPercent+'%。'],boxes['撤离点'],{size:22.5,gap:4.5,role:'extraction-uplift'});
  tx(p,content.brand+'｜'+content.footerInstruction,360,2125,13.3,{anchor:'middle',role:'footer'});
  for(const e of p.elements.slice(notesStart)){if(e.cy!==undefined)e.cy-=96;}
  for(const pl of placements.filter(pl=>pl.page===p.id&&pl.container[1]>=1200)){pl.container=[pl.container[0],pl.container[1]-96,pl.container[2],pl.container[3]];pl.centerY-=96;}
  p.alignment={colEdges:pm.colEdges,priceUnitColumn:priceColumn.x,lootUnitColumn:lootColumn.x,numberSize:35,unitVisibleHeightRatio:.75,rows:pm.maps.map(g=>g.rowEdges),bodyMinSize:22.5,phoneBodyMinSize:22.5*390/724,centering:'Visible glyph bounds; multiline group centered in actual blank area',notesOffsetY:-96};
  p.sourceTiers=t.maps;
  return p;
 }
 function cover(){
  const p=page('cover',content.title+' · 横版封面','cover.png',1672,941,3200,1800);
  brand(p,145,115,40,'start');
  art(p,content.titleLines[0],145,310,180,{maxWidth:588,outlineWidth:8,anchor:'start'});
  art(p,content.titleLines[1],145,543,182,{maxWidth:588,outlineWidth:8.5,anchor:'start'});
  raw(p,'<path d="M145 668 Q395 686 688 658" fill="none" stroke="#DCAC65" stroke-width="7" stroke-linecap="round"/>');
  tx(p,content.subtitle,m.cover.textCenterX,737,37,{anchor:'middle',fill:C.paper,role:'cover-subtitle'});
  tx(p,'端游 / 手游 · 绝密 / 机密',m.cover.textCenterX,832,29,{anchor:'middle',role:'cover-platforms'});
  p.alignment={leftAnchor:145,capsuleCenterX:m.cover.textCenterX,noOrderNotes:true};
  return p;
 }
 function social(platform,id){
  const sm=m.social,p=page(id,platform+' · '+content.title+' · 简洁宣发','social.png',1086,1448,2400,3200);
  brand(p,296,43,25);
  art(p,content.titleLines[0],296,133,92,{maxWidth:356,outlineWidth:4.2});
  art(p,content.titleLines[1],296,247,91,{maxWidth:358,outlineWidth:4.2});
  tx(p,platform+'价目｜r = 元 / w = 万',543,346,25,{anchor:'middle',role:'platform-units'});
  const sourceRows=[];
  for(const [ci,mode] of ['绝密','机密'].entries()){
   const card=sm.cards[ci],center=(card.colEdges[0]+card.colEdges[3])/2,color=ci?C.teal:C.gold;
   const t=content.tiers.find(t=>t.platform===platform&&t.mode===mode);
   tx(p,mode+'版',center,418,27,{anchor:'middle',fill:color,role:'social-mode'});
   ['价格','基础保底','累计撤离'].forEach((s,j)=>tx(p,s,(card.colEdges[j]+card.colEdges[j+1])/2,464,22,{anchor:'middle',fill:C.paper,role:'social-column-heading'}));
   const price=unitColumn(allTiers.map(v=>v.priceR),'r',card.colEdges.slice(0,2),44,6);
   const loot=unitColumn(allTiers.map(v=>v.baseGuaranteeW),'w',card.colEdges.slice(1,3),44,6);
   t.maps.forEach((g,i)=>{
    const mg=sm.groups[i];
    tx(p,g.mapLabel,center,(mg.mapHeaderY[0]+mg.mapHeaderY[1])/2,26.4,{anchor:'middle',role:'social-map'});
    g.rows.forEach((r,j)=>{
     const cy=rowCenters(mg.rowEdges)[j];
     number(p,r.priceR,'r',price.x,cy,44,C.ink,'price',6);
     number(p,r.baseGuaranteeW,'w',loot.x,cy,44,color,'loot',6);
     const numW=width(r.cumulativeExtractions,37,NUM),labelW=width('把',26),gap=7,x=(card.colEdges[2]+card.colEdges[3])/2-(numW+labelW+gap)/2;
     tx(p,r.cumulativeExtractions,x,cy,37,{font:NUM,role:'cumulative-number'});
     tx(p,'把',x+numW+gap,cy,26,{role:'cumulative-unit'});
     sourceRows.push({platform,mode,map:g.map,priceR:r.priceR,baseGuaranteeW:r.baseGuaranteeW,cumulativeExtractions:r.cumulativeExtractions});
    });
   });
  }
  lines(p,['累计撤离达标＋打满基础保底','两项同时满足，才可结单'],sm.completionSafe,{size:31.5,gap:12,role:'social-completion',align:'center',style:1});
  tx(p,content.brand,543,1402,20,{anchor:'middle',role:'social-footer'});
  p.sourceTiers=sourceRows;p.alignment={priceAndLoot:'each mode uses fixed unit columns computed from longest source number',unitVisibleHeightRatio:.75,tiers:18,noOrderNotes:true};
  return p;
 }
 const pages=[...content.tiers.map(poster),cover(),social('端游','social-desktop'),social('手游','social-mobile')];
 return {version:'cumulative-vectors-r1',requests,pages,placements,dataSource:'content.json',method:'沿用确认美术和字号；按实际轮廓居中、共用baseline和独立单位列。SVG背景裁窗删除失败卡，须知整体上移96像素。'};
}
