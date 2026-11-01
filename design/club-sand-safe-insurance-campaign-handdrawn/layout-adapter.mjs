import {createHash} from 'node:crypto';

export function makeLayout(c,m,glyphs={}) {
 const C={ink:'#183E50',muted:'#526570',gold:'#AD6E28',paper:'#FFFDF5',shade:'#D8B98B'};
 const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
 const requests=[],keys=new Map(),placements=[];
 const ready=Object.keys(glyphs).length>0;
 const rq=(text,font=CN,style=0)=>{text=String(text);const k=JSON.stringify([text,font,style]);if(!keys.has(k)){const id='t'+createHash('sha256').update(k).digest('hex').slice(0,20);keys.set(k,id);requests.push({id,text,font,style});}return keys.get(k);};
 const mt=(text,font=CN,style=0)=>glyphs[rq(text,font,style)]??{width:[...String(text)].length*91,height:95};
 const ww=(text,size,font=CN,style=0)=>mt(text,font,style).width*size/100;
 const hh=(text,size,font=CN,style=0)=>mt(text,font,style).height*size/100;
 const pg=(id,label,bg,w,h,ew,eh)=>({id,label,bg,w,h,ew,eh,elements:[]});
 const zone=(id,name)=>m[id].zones.find(z=>z.id===name).rect;
 const mid=b=>[b[0]+b[2]/2,b[1]+b[3]/2];
 const raw=(p,markup)=>p.elements.push({type:'raw',markup});
 function tx(p,text,x,cy,size,{font=CN,style=0,fill=C.ink,anchor='start',role='copy'}={}) {
  p.elements.push({type:'text',id:rq(text,font,style),text:String(text),x,cy,size,font,style,fill,anchor,role});
 }
 function fit(p,role,b,w,h,size) {
  if(ready&&(w>b[2]+.15||h>b[3]+.15)) throw Error(`${p.id} ${role} ${w.toFixed(1)}×${h.toFixed(1)} exceeds ${b[2]}×${b[3]}`);
  placements.push({page:p.id,role,container:b,visibleWidth:w,visibleHeight:h,fontSize:size,phoneSize:size*390/p.w,centerX:b[0]+b[2]/2,centerY:b[1]+b[3]/2});
 }
 function single(p,text,b,size,{font=CN,style=0,fill=C.ink,role='copy',align='center'}={}) {
  const [cx,cy]=mid(b),w=ww(text,size,font,style),h=hh(text,size,font,style);fit(p,role,b,w,h,size);
  tx(p,text,align==='left'?b[0]:cx,cy,size,{font,style,fill,role,anchor:align==='left'?'start':'middle'});
 }
 function inline(p,parts,b,{role='copy',gap=12,align='center'}={}) {
  parts=parts.map(s=>({size:35,font:CN,style:0,fill:C.ink,...s,id:rq(s.text,s.font??CN,s.style??0)}));
  const widths=parts.map(v=>ww(v.text,v.size,v.font,v.style));
  const width=widths.reduce((s,v,i)=>s+v+(i?parts[i].gap??gap:0),0),height=Math.max(...parts.map(v=>hh(v.text,v.size,v.font,v.style)));
  fit(p,role,b,width,height,Math.min(...parts.map(v=>v.size)));
  const [cx,cy]=mid(b);
  p.elements.push({type:'inline',role,x:align==='left'?b[0]:cx,cy,anchor:align==='left'?'start':'middle',gap,parts:parts.map((v,i)=>({...v,text:String(v.text),role:role+'-part-'+i}))});
 }
 function artExtent(text,size,{angles=true,outlineWidth=3}={}) {
  const a=[-2.8,1.4,-1.7,2.2,-1.4],bumps=[1,-2,1,-1,0],points=[];
  let x=0;
  for(const [i,ch] of [...text].entries()) {
   const g=mt(ch,ART,0),w=g.width*size/100,h=g.height*size/100,theta=(angles?a[i%5]:0)*Math.PI/180,cy=angles?bumps[i%5]*size/70:0;
   for(const [dx,dy,pad] of [[0,0,outlineWidth/2],[size*.035,size*.055,.3]])for(const xx of [-w/2-pad,w/2+pad])for(const yy of [-h/2-pad,h/2+pad])points.push([x+w/2+(xx+dx)*Math.cos(theta)-(yy+dy)*Math.sin(theta),cy+(xx+dx)*Math.sin(theta)+(yy+dy)*Math.cos(theta)]);
   x+=w+size*.065;
  }
  const xs=points.map(v=>v[0]),ys=points.map(v=>v[1]);
  return {minX:Math.min(...xs),maxX:Math.max(...xs),minY:Math.min(...ys),maxY:Math.max(...ys)};
 }
 function art(p,text,b,size,{fill=C.ink,shadow=C.shade,outline=C.paper,outlineWidth=3,angles=true,role='art-title'}={}) {
  const ids=[...text].map(ch=>rq(ch,ART,0));let e=artExtent(text,size,{angles,outlineWidth});
  if(ready) for(let i=0;i<3;i++) {const s=Math.min(1,b[2]/(e.maxX-e.minX),b[3]/(e.maxY-e.minY));if(s===1)break;size*=s;e=artExtent(text,size,{angles,outlineWidth});}
  const [cx,cy]=mid(b),w=e.maxX-e.minX,h=e.maxY-e.minY;
  fit(p,role,b,w,h,size);
  p.elements.push({type:'art',text,ids,x:cx-(e.minX+e.maxX)/2,cy:cy-(e.minY+e.maxY)/2,size,fill,shadow,outline,outlineWidth,angles,role,requestedCenter:[cx,cy],visibleBounds:[cx-w/2,cy-h/2,w,h]});
  return {width:w,height:h,size};
 }
 function brand(p,b,size) {
  const logo=size*1.12,gap=size*.32,textW=ww(c.brand,size,CN,1),width=logo+gap+textW,height=Math.max(logo,hh(c.brand,size,CN,1));
  fit(p,'brand',b,width,height,size);const [cx,cy]=mid(b),left=cx-width/2,k=logo/31;
  raw(p,`<g data-role="brand-logo" transform="translate(${left} ${cy-logo/2}) scale(${k})" fill="none" stroke="${C.ink}" stroke-width="2.2" stroke-linejoin="round"><path d="M0 29L15.5 1L31 29Z M7.5 24L15.5 10L23.5 24Z"/><path d="M12 22L17 14L15 21H21" stroke-width="1.8"/></g>`);
  tx(p,c.brand,left+logo+gap,cy,size,{style:1,role:'brand'});
 }
 const all=c.tiers.flatMap(t=>t.rows);
 if(c.mode!=='绝密'||c.tiers.length!==2||all.length!==6||c.tiers.some(t=>t.rows.length!==3))throw Error('本轮只做绝密，端手游各三档');
 const unitRatio=.70;
 function column(values,unit,left,right,preferred=70,gap=12) {
  const maxW=Math.max(...values.map(v=>mt(v,NUM,1).width))/100;
  const numH=mt(values[0],NUM,1).height/100,unitG=mt(unit,NUM,1);
  const unitSizePerPoint=numH/(unitG.height/100)*unitRatio;
  const size=ready?Math.min(preferred,(right-left-gap)/(maxW+unitG.width/100*unitSizePerPoint)):preferred;
  const us=size*unitSizePerPoint,nw=maxW*size,uw=unitG.width*us/100;
  const unitX=(left+right)/2+(nw+gap-uw)/2;
  return {left,right,size,unitSize:us,unitX,numberRight:unitX-gap,unitGap:gap,ratio:unitRatio,longestWidth:nw,unitWidth:uw};
 }
 function number(p,value,unit,col,cy,color,role) {
  tx(p,value,col.numberRight,cy,col.size,{font:NUM,style:1,fill:color,anchor:'end',role:role+'-number'});
  tx(p,unit,col.unitX,cy,col.unitSize,{font:NUM,style:1,fill:color,role:role+'-unit'});
 }
 const completionLabels=['沙色次数达标','常规次数达标','基础保底达标'];
 function reward(p,id) {
  const main=id==='poster',r=c.reward;
  if(main) {
   art(p,r.title,[170,898,756,41],37,{fill:C.gold,shadow:'#E9D2AB',outlineWidth:1.8,angles:false,role:'reward-art-title'});
   single(p,r.trigger,[170,943,756,32],30,{role:'reward-trigger'});
  } else {
   const b=zone(id,'reward-heading'),title='三幻神奖励',as=34;
   const e=artExtent(title,as,{angles:false,outlineWidth:1.8}),aw=e.maxX-e.minX,tw=ww(r.trigger,32),gap=24,total=aw+tw+gap,[cx,cy]=mid(b);
   fit(p,'reward-heading-group',b,total,Math.max(e.maxY-e.minY,hh(r.trigger,32)),32);
   art(p,title,[cx-total/2,cy-23,aw,46],as,{fill:C.gold,shadow:'#E9D2AB',angles:false,outlineWidth:1.8,role:'reward-art-title'});
   tx(p,r.trigger,cx-total/2+aw+gap,cy,32,{role:'reward-trigger'});
  }
  const eb=zone(id,'reward-extra'),gb=zone(id,'reward-gift');
  const ns=main?63:64,labelSize=main?30:32;
  const extra=String('+'+r.extraGuaranteeW),us=hh(extra,ns,NUM,1)/mt('w',NUM,1).height*100*unitRatio;
  inline(p,[{text:'额外保底',size:labelSize},{text:extra,font:NUM,style:1,size:ns,fill:C.gold,gap:16},{text:'w',font:NUM,style:1,size:us,fill:C.gold,gap:9}],eb,{role:'reward-extra',gap:12});
  inline(p,[{text:'赠沙色保险',size:labelSize},{text:r.gift.count,font:NUM,style:1,size:ns,fill:C.gold,gap:16},{text:r.gift.unit,size:main?39:40,style:1,fill:C.gold,gap:9}],gb,{role:'reward-gift'});
  const once=r.maximumTriggersPerOrder===1?'整单仅一次':`整单${r.maximumTriggersPerOrder}次`;
  inline(p,[{text:once,size:main?35:35,style:1,fill:C.gold},{text:r.gift.countsTowardPackage?'赠送计入套餐次数':'赠送不计套餐次数',size:main?34:35,gap:24}],zone(id,'reward-conditions'),{role:'reward-conditions'});
 }
 function poster(t) {
  const spec=m.poster,[w,h]=spec.native,p=pg(t.id,`${t.platform} · 绝密 · 沙色保险单`,'poster.png',w,h,3200,4800);
  brand(p,zone('poster','brand'),39);
  art(p,c.title,zone('poster','title'),88,{outlineWidth:3.2,role:'art-title'});
  single(p,c.subtitle,zone('poster','subtitle'),31,{role:'subtitle'});
  inline(p,[{text:t.platform+' · '+c.mode,style:1,size:32,fill:C.paper},{text:'三项同时达标才结单',style:0,size:31,fill:C.paper,gap:54}],zone('poster','platform'),{role:'platform-completion'});
  for(const [i,name] of ['goal-sand','goal-regular','goal-guarantee'].entries()) single(p,completionLabels[i],zone('poster',name),35,{style:1,fill:i===2?C.gold:C.ink,role:'completion-target-'+i});
  const headings=[['table-price-header','价格'],['table-sand-header','沙色保险'],['table-regular-header','常规大保险'],['table-guarantee-header','基础保底']];
  for(const [name,text] of headings) single(p,text,zone('poster',name),31,{fill:C.paper,role:'table-heading'});
  const tab=spec.table;
  const price=column(all.map(r=>r.priceR),'r',tab.price.left,tab.price.unitRight,68);
  const loot=column(all.map(r=>r.baseGuaranteeW),'w',tab.guarantee.left,tab.guarantee.unitRight,69);
  t.rows.forEach((r,i)=>{
   const cy=tab.rowCenters[i];
   number(p,r.priceR,'r',price,cy,C.ink,'price');
   number(p,r.baseGuaranteeW,'w',loot,cy,C.gold,'loot');
   for(const [name,v] of [['sand',r.sandSafeCount],['regular',r.regularLargeSafeCount]]) {
    const cell=tab[name];
    inline(p,[{text:v,font:NUM,style:1,size:69,fill:name==='sand'?C.gold:C.ink},{text:'个',size:34,gap:11}],[cell.left,cy-33,cell.right-cell.left,66],{role:name+'-count'});
   }
  });
  reward(p,'poster');
  art(p,'下单须知',[70,1159,310,34],31,{fill:C.paper,shadow:'#122F40',outline:C.ink,outlineWidth:.5,angles:false,role:'notes-art-title'});
  single(p,'r = 元 / w = 万',[713,1161,230,30],25,{fill:'#D7E4E9',role:'units-key',align:'center'});
  const rules=c.mechanism;
  inline(p,[{text:'计次',size:35,style:1},{text:'两类保险仅统计',size:35,gap:22},{text:'成功撤离',size:35,style:1,gap:0},{text:'的局',size:35,gap:0}],zone('poster','notes-count'),{role:'rule-count',align:'left',gap:0});
  inline(p,[{text:'代摸',size:35,style:1},{text:rules.proxyTouch.copy.replace(/。$/,''),size:35,gap:22}],zone('poster','notes-proxy'),{role:'rule-proxy',align:'left'});
  const compare=rules.lootCounting.operator==='>'?'＞':rules.lootCounting.operator;
  inline(p,[{text:'累计',size:34,style:1},{text:'成功撤离且板板全身物资',size:34,gap:20},{text:compare+rules.lootCounting.thresholdW+'w',size:34,style:1,fill:C.gold,gap:5},{text:'才计保底',size:34,gap:5}],zone('poster','notes-loot'),{role:'rule-loot',align:'left'});
  single(p,c.brand+' · '+c.footerInstruction,zone('poster','footer'),24,{role:'footer'});
  p.sourceRows=t.rows;
  p.alignment={price,loot,unitVisibleHeightRatio:unitRatio,rows:tab.rowBounds,bodyStyle:'Regular 400; selective Bold 700',ordinaryBodySizes:[35,35,34],minimumPhoneBodySize:34*390/w};
  return p;
 }
 function cover() {
  const spec=m.cover,[w,h]=spec.native,p=pg('cover',c.title+' · 横版封面','cover.png',w,h,3200,1800);
  brand(p,zone('cover','brand'),51);
  art(p,c.title,zone('cover','title'),145,{outlineWidth:5.5,role:'art-title'});
  single(p,c.subtitle,zone('cover','subtitle'),46,{fill:C.paper,role:'cover-subtitle'});
  single(p,c.mode+'专属 · 端游／手游',zone('cover','scope'),37,{role:'cover-scope'});
  p.alignment={centerX:spec.textCenterX,noOrderNotes:true,allGroupsHorizontallyCentered:true};
  return p;
 }
 function social() {
  const spec=m.social,[w,h]=spec.native,p=pg('social',c.title+' · 双平台简洁宣发','social.png',w,h,2400,3200);
  brand(p,zone('social','brand'),39);
  art(p,c.title,zone('social','title'),91,{outlineWidth:3.2,role:'art-title'});
  single(p,c.mode+'专属 · 成功撤离 · 累计摸箱',zone('social','subtitle'),30,{role:'social-subtitle'});
  const comp=zone('social','completion'),center=comp[0]+comp[2]/2;
  single(p,'沙色次数 ＋ 常规次数 ＋ 基础保底',[comp[0],comp[1],comp[2],34],33,{role:'social-completion-targets'});
  single(p,'三项同时达标才结单',[comp[0],comp[1]+42,comp[2],36],35,{style:1,fill:C.gold,role:'social-completion-relation'});
  const tab=spec.tables,price=column(all.map(r=>r.priceR),'r',tab.price.left,tab.price.unitRight,63,10),loot=column(all.map(r=>r.baseGuaranteeW),'w',tab.guarantee.left,tab.guarantee.unitRight,65,10);
  for(const [i,t] of c.tiers.entries()) {
   const prefix=i===0?'pc':'mobile',card=i===0?tab.desktop:tab.mobile;
   single(p,t.platform+' · '+c.mode,[74,i===0?412:757,938,37],32,{style:1,fill:C.paper,role:'social-platform'});
   const hb=zone('social',prefix+'-columns');
   for(const [text,x,width] of [['价格',77,148],['沙色保险',245,231],['常规大保险',513,235],['基础保底',791,219]])single(p,text,[x,hb[1],width,hb[3]],28,{role:'social-table-heading'});
   t.rows.forEach((r,j)=>{
    const cy=card.rowCenters[j];
    number(p,r.priceR,'r',price,cy,C.ink,'price');number(p,r.baseGuaranteeW,'w',loot,cy,C.gold,'loot');
    for(const [name,v] of [['sand',r.sandSafeCount],['regular',r.regularLargeSafeCount]]) {
     const cell=tab[name];inline(p,[{text:v,font:NUM,style:1,size:62,fill:name==='sand'?C.gold:C.ink},{text:'个',size:31,gap:10}],[cell.left,cy-26,cell.right-cell.left,52],{role:name+'-count'});
    }
   });
  }
  reward(p,'social');
  single(p,c.brand+' · r = 元 / w = 万',zone('social','footer'),28,{role:'social-footer'});
  p.sourceRows=all;p.alignment={price,loot,unitVisibleHeightRatio:unitRatio,noOrderNotes:true,rows:[...tab.desktop.rowBounds,...tab.mobile.rowBounds],rewardConditionsIncluded:true};
  return p;
 }
 return {version:'sand-safe-insurance-vectors-v1',requests,pages:[...c.tiers.map(poster),cover(),social()],placements,dataSource:'content.json',method:'真实字形测量、艺术字含描边阴影居中、独立r/w轴、横向权益组、正文常规与重点真实粗体、较少换行。'};
}
