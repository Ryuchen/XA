import {createHash} from 'node:crypto';

function makePlatformLayout(c,m,glyphs={},platformKey='desktop',pageKind='price') {
  const C={ink:'#203F57',muted:'#766853',gold:'#A16523',paper:'#FFF7E4',soft:'#E7D2AD'};
  const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
  const requests=[],ids=new Map(),placements=[],hasMetrics=Object.keys(glyphs).length>0;
  const combined=platformKey==='combined',platformLabel=combined?'端游 / 手游':platformKey==='desktop'?'端游':'手游';
  const isCover=pageKind==='cover',isAdvert=pageKind==='advert',shortPage=isCover||isAdvert,canvas=isCover?m.cover.native:isAdvert?m.advert.native:m.native;
  const p={id:platformKey+(isCover?'-cover':isAdvert?'-advert':'-absolute'),label:c.brand+' · '+c.title+' · '+platformLabel+' · '+(isCover?'绝密封面':isAdvert?'绝密广告页':'绝密价格海报'),bg:isCover?m.cover.background:isAdvert?'advert-bakhsh-v2.png':'poster-bakhsh-sand-v3.png',w:canvas.width,h:canvas.height,ew:isCover?3200:isAdvert?2400:3200,eh:isCover?1800:isAdvert?3200:7200,elements:[],sourcePlatform:platformKey,pageKind};
  const request=(text,font=CN,style=0)=>{
    text=String(text);const key=JSON.stringify([text,font,style]);
    if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text,font,style});}
    return ids.get(key);
  };
  const metric=(text,font=CN,style=0)=>glyphs[request(text,font,style)]??{width:[...String(text)].length*90,height:96};
  const width=(s,size,font=CN,style=0)=>metric(s,font,style).width*size/100;
  const height=(s,size,font=CN,style=0)=>metric(s,font,style).height*size/100;
  function tx(text,x,cy,size=31,{font=CN,style=0,fill=C.ink,anchor='start',role='copy'}={}) {
    p.elements.push({type:'text',id:request(text,font,style),text:String(text),x,cy,size,font,style,fill,anchor,role});
  }
  const raw=markup=>p.elements.push({type:'raw',markup});
  const normalizeRun=(r,size=31)=>({text:String(r.text),size:r.size??size,font:r.font??CN,style:r.bold?1:(r.style??0),fill:r.fill??C.ink,gap:r.gap??0,role:r.role});
  function runMetrics(r,size=31){const a=normalizeRun(r,size);return {...a,width:width(a.text,a.size,a.font,a.style),height:height(a.text,a.size,a.font,a.style)};}
  function lineInfo(runs,size=31){const rs=runs.map(r=>runMetrics(r,size));return {runs:rs,width:rs.reduce((s,r)=>s+r.width+r.gap,0)-(rs.at(-1)?.gap??0),height:Math.max(...rs.map(r=>r.height))};}
  function drawLine(info,left,cy,role){let x=left;for(const r of info.runs){tx(r.text,x,cy,r.size,{font:r.font,style:r.style,fill:r.fill,role:r.role??role});x+=r.width+r.gap;}}
  function richLine(runs,box,{size=31,role='copy',align='center'}={}){
    const a=lineInfo(runs,size);
    if(hasMetrics&&(a.width>box.width+.05||a.height>box.height+.05))throw Error(`${role} exceeds ${box.width}×${box.height}: ${a.width.toFixed(2)}×${a.height.toFixed(2)}; ${runs.map(r=>r.text).join('')}`);
    const cx=box.x+box.width/2,cy=box.y+box.height/2;
    const left=align==='left'?box.x:cx-a.width/2;
    drawLine(a,left,cy,role);
    placements.push({role,box,visible:{x:left,y:cy-a.height/2,width:a.width,height:a.height},center:[left+a.width/2,cy],containerCenter:[cx,cy],size,phoneSize:size*390/p.w,align});
  }
  function richParagraph(lines,box,{size=31,gap=7,role='rule-body',align='left',blockAlign='center'}={}){
    const infos=lines.map(rs=>lineInfo(rs,size)),w=Math.max(...infos.map(i=>i.width)),h=infos.reduce((s,i)=>s+i.height,0)+gap*(infos.length-1);
    if(hasMetrics&&(w>box.width+.05||h>box.height+.05))throw Error(`${role} exceeds ${box.width}×${box.height}: ${w.toFixed(2)}×${h.toFixed(2)}; ${lines.map(rs=>rs.map(r=>r.text).join('')).join('|')}`);
    const cx=box.x+box.width/2,cy=box.y+box.height/2,left=blockAlign==='left'?box.x:cx-w/2;
    let top=cy-h/2;
    for(const a of infos){drawLine(a,align==='center'?left+(w-a.width)/2:left,top+a.height/2,role);top+=a.height+gap;}
    placements.push({role,box,visible:{x:left,y:cy-h/2,width:w,height:h},center:[left+w/2,cy],containerCenter:[cx,cy],size,phoneSize:size*390/p.w,lineCount:infos.length,align,blockAlign});
  }
  function unitRuns(value,unit,size=48,color=C.ink,role='price',gap=6){
    const numHeight=height(value,size,NUM,1),unitHeight=metric(unit,NUM,1).height;
    const us=numHeight*.72/unitHeight*100;
    return [{text:String(value),font:NUM,bold:true,size,fill:color,gap,role:role+'-number'},{text:unit,font:NUM,bold:true,size:us,fill:color,role:role+'-unit'}];
  }
  function combinedLootRuns(numberSize=56){
    const runs=[{text:c.display.combinedLootPrefix,gap:15}];
    for(const [i,key] of ['desktop','mobile'].entries()){
      if(i)runs.push({text:'·',gap:15});
      runs.push({text:(key==='desktop'?'端游':'手游')+' '+c.objectives.loot.operator,bold:true,gap:7},...unitRuns(c.objectives.loot[key],c.objectives.loot.unit,numberSize,C.gold,'loot-'+key,6));
      runs.at(-1).gap=15;
    }
    runs.at(-1).gap=0;return runs;
  }
  function art(text,cx,cy,size,{maxWidth=320,outlineWidth=2.7,box=m.header.copy}={}){
    const chars=[...text],artIds=chars.map(t=>request(t,ART,0));
    const natural=chars.reduce((s,t)=>s+metric(t,ART,0).width/100,0)+.065*(chars.length-1);
    const finalSize=Math.min(size,maxWidth/natural),w=natural*finalSize;
    let left=cx-w/2,centerY=cy,x=left;const points=[],rotations=[-2.8,1.4,-1.7,2.2,-1.4],bumps=[1,-2,1,-1,0];
    for(let i=0;i<chars.length;i++){
      const g=metric(chars[i],ART,0),cw=g.width*finalSize/100,ch=g.height*finalSize/100,a=rotations[i%5]*Math.PI/180,dy=bumps[i%5]*finalSize/70;
      for(const [ox,oy,pad] of [[0,0,outlineWidth/2],[finalSize*.035,finalSize*.055,.3]])for(const px of [-cw/2-pad,cw/2+pad])for(const py of [-ch/2-pad,ch/2+pad])points.push([x+cw/2+(px+ox)*Math.cos(a)-(py+oy)*Math.sin(a),cy+dy+(px+ox)*Math.sin(a)+(py+oy)*Math.cos(a)]);
      x+=cw+finalSize*.065;
    }
    const xs=points.map(v=>v[0]),ys=points.map(v=>v[1]),bw=Math.max(...xs)-Math.min(...xs),bh=Math.max(...ys)-Math.min(...ys);
    left+=cx-(Math.max(...xs)+Math.min(...xs))/2;centerY+=cy-(Math.max(...ys)+Math.min(...ys))/2;
    p.elements.push({type:'art',text,ids:artIds,x:left,cy:centerY,size:finalSize,maxWidth,fill:C.ink,shadow:'#D7BD91',outline:C.paper,outlineWidth,angles:true,visibleBounds:[cx-bw/2,cy-bh/2,bw,bh]});
    placements.push({role:'art-title',box,visible:{x:cx-bw/2,y:cy-bh/2,width:bw,height:bh},center:[cx,cy],size:finalSize});
  }
  if(c.prices.length!==5||c.prices.some(v=>!Number.isFinite(v.desktop)||!Number.isFinite(v.mobile)))throw Error('Five rows and ten prices are required.');

  if(isCover){
    const z=m.cover,b=z.headerCopy,hc=b.x+b.width/2,bs=z.type.brand;
    raw(`<defs><linearGradient id="cover-reward-paper" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFF7E4" stop-opacity="0"/><stop offset=".2" stop-color="#FFF7E4" stop-opacity=".92"/><stop offset=".85" stop-color="#FFF7E4" stop-opacity=".92"/><stop offset="1" stop-color="#FFF7E4" stop-opacity="0"/></linearGradient><linearGradient id="cover-paper-edge" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="white"/><stop offset=".88" stop-color="white"/><stop offset="1" stop-color="black"/></linearGradient><mask id="cover-paper-mask"><rect x="${z.rewardPaper.x}" y="${z.rewardPaper.y}" width="${z.rewardPaper.width}" height="${z.rewardPaper.height}" fill="url(#cover-paper-edge)"/></mask></defs><rect x="${z.rewardPaper.x}" y="${z.rewardPaper.y}" width="${z.rewardPaper.width}" height="${z.rewardPaper.height}" rx="24" fill="url(#cover-reward-paper)" mask="url(#cover-paper-mask)"/>`);
    const triW=bs*.84,gap=bs*.30,bw=width(c.brand,bs,CN,1),left=hc-(bw+triW+gap)/2;
    const triTop=z.brandY-triW*.43;
    raw(`<g transform="translate(${left} ${triTop})" fill="none" stroke="${C.ink}" stroke-width="2.6" stroke-linejoin="round"><path d="M0 ${triW*.86}L${triW/2} 0L${triW} ${triW*.86}Z M${triW*.25} ${triW*.71}L${triW/2} ${triW*.34}L${triW*.75} ${triW*.71}Z"/></g>`);
    tx(c.brand,left+triW+gap,z.brandY,bs,{style:1,role:'brand'});
    placements.push({role:'brand-group',box:z.brandBox,visible:{x:left,y:Math.min(triTop,z.brandY-height(c.brand,bs,CN,1)/2),width:bw+triW+gap,height:Math.max(triW*.86,height(c.brand,bs,CN,1))},center:[hc,z.brandY],size:bs});
    art(c.title,hc,z.titleY,z.type.title,{maxWidth:b.width-18,outlineWidth:3.6,box:z.titleBox});
    richLine([{text:[...new Set(c.prices.map(r=>r.type))].map(s=>s+'挑战').join(' · ')}],z.challengeBox,{size:z.type.body,role:'cover-challenge-types'});
    richLine([{text:c.display.platformMode,bold:true,fill:C.paper}],z.modeBox,{size:z.type.body,role:'platform-mode'});
    richLine([{text:c.display.wishWhen}],z.wishBoxes[0],{size:z.type.body,role:'wish-when'});
    richLine([{text:c.wishReward.choices.join(' / '),bold:true}],z.wishBoxes[1],{size:z.type.choices,role:'wish-choices'});
    const reward=[{text:c.cover.wishTriggerShort,gap:12},...unitRuns(c.wishReward.guarantee,c.wishReward.unit,z.type.reward,C.gold,'reward',8)];reward.at(-1).gap=12;reward.push({text:c.display.wishGuaranteeLabel,bold:true});
    richLine(reward,z.wishBoxes[2],{size:z.type.body,role:'wish-trigger-and-guarantee'});
    p.alignment={bodySize:z.type.body,phoneBodySize:z.type.body*390/p.w,unitVisibleHeightRatio:.72,coverCopyCentered:true,positionBasis:'new horizontal art measured after generation',coverFieldCenter:hc,format:'16:9'};
    p.coverSummary={showNotices:false,showPrice:false,showTargets:false,showConditionalWishReward:true};
    return {requests,pages:[p],placements:placements.map(v=>({...v,page:p.id}))};
  }

  if(isAdvert){
    const a=m.advert,hc=a.headerCopy.x+a.headerCopy.width/2,bs=38,tw=34,gap=12,bw=width(c.brand,bs,CN,1),left=hc-(bw+tw+gap)/2;
    raw(`<g transform="translate(${left} 154)" fill="none" stroke="${C.ink}" stroke-width="2.2" stroke-linejoin="round"><path d="M0 32L17 0L34 32Z M8 26L17 12L26 26Z"/></g>`);
    tx(c.brand,left+tw+gap,171,bs,{style:1,role:'brand'});
    art(c.title,hc,249,80,{maxWidth:418,outlineWidth:3.3,box:a.headerCopy});
    raw(`<rect x="108" y="309" width="372" height="47" rx="21" fill="${C.ink}"/>`);
    richLine([{text:c.mode+' · '+platformLabel,bold:true,fill:C.paper}],{x:116,y:312,width:356,height:41},{size:39,role:'platform-mode'});
    if(combined)raw(`<defs><linearGradient id="combined-table-paper" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FFF5D8"/><stop offset="1" stop-color="#FFF2D4"/></linearGradient><clipPath id="combined-table-body"><path d="M35 478H1050V796Q1050 821 1028 821H57Q35 821 35 799Z"/></clipPath></defs><g clip-path="url(#combined-table-body)"><rect x="35" y="478" width="1015" height="343" fill="url(#combined-table-paper)"/><rect x="35" y="680" width="1015" height="141" fill="#FBE8BD"/><path d="M371 478V821M708 478V821M43 545H1043M43 613H1043M43 680H1043M43 747H1043" fill="none" stroke="#C8B98F" stroke-width="1.45"/></g>`);
    const headings=combined?[[c.display.priceColumnLabels[0],210],['端游',539.5],['手游',879.5]]:[[c.display.priceColumnLabels[0],294],[c.display.priceColumnLabels[1],794]];
    for(const [s,cx] of headings)tx(s,cx,a.priceHeaderY,40,{style:1,anchor:'middle',fill:C.paper,role:'table-heading'});
    const columns=combined?[['desktop',[387,692]],['mobile',[727,1032]]]:[[platformKey,[564,1024]]],unitColumns=[];
    for(const [key,col] of columns){
      const values=c.prices.map(v=>v[key]),size=59,nw=Math.max(...values.map(v=>width(v,size,NUM,1))),u=unitRuns(values[0],'r',size)[1],uw=width('r',u.size,NUM,1),gx=8,ux=(col[0]+col[1])/2+(nw+gx-uw)/2;
      unitColumns.push({platform:key,unitX:ux,unitSize:u.size,gap:gx});
      for(let i=0;i<c.prices.length;i++){
        const color=i>=3?C.gold:C.ink,cy=a.priceRowCenters[i];
        tx(values[i],ux-gx,cy,size,{font:NUM,style:1,anchor:'end',fill:color,role:'price-number'});
        tx('r',ux,cy,u.size,{font:NUM,style:1,fill:color,role:'price-unit'});
      }
    }
    for(let i=0;i<c.prices.length;i++)tx(c.prices[i].label,combined?210:294,a.priceRowCenters[i],41,{style:i>=3?1:0,anchor:'middle',role:'tier-label'});
    const targets=[{text:c.objectives.sandSafes.count,font:NUM,size:54,bold:true,fill:C.gold,gap:6,role:'objective-count'},{text:'个'+c.objectives.sandSafes.name,bold:true,gap:16},{text:'·',gap:16},{text:c.objectives.regularSafes.count,font:NUM,size:54,bold:true,fill:C.gold,gap:6,role:'objective-count'},{text:'个'+c.objectives.regularSafes.name,bold:true}];
    richLine(targets,a.coreRows[0],{size:40,role:'advert-safe-targets'});
    richLine([{text:c.advert.coreRelation}],a.coreRows[1],{size:40,role:'advert-target-relation'});
    richLine(combined?combinedLootRuns(56):[{text:c.display.lootObjectivePrefix,gap:14},{text:c.objectives.loot.operator,bold:true,gap:8},...unitRuns(c.objectives.loot[platformKey],c.objectives.loot.unit,56,C.gold,'loot-'+platformKey,6)],a.coreRows[2],{size:40,role:'loot-target'});
    richLine([{text:c.display.wishWhen}],{x:76,y:1120,width:518,height:47},{size:40,role:'wish-when'});
    richLine([{text:c.wishReward.choices.join(' / '),bold:true}],{x:76,y:1189,width:518,height:58},{size:47,role:'wish-choices'});
    const reward=[{text:c.display.wishTriggerShort,gap:10},...unitRuns(c.wishReward.guarantee,c.wishReward.unit,76,C.gold,'reward',7)];reward.at(-1).gap=10;reward.push({text:c.display.wishGuaranteeLabel,bold:true});
    richLine(reward,{x:76,y:1254,width:518,height:70},{size:40,role:'wish-trigger-and-guarantee'});
    p.sourcePrices=columns.flatMap(([key])=>c.prices.map(v=>({platform:key,id:v.id,label:v.label,priceR:v[key],unit:v.unit})));
    p.alignment={priceUnitColumns:unitColumns,priceRowCenters:a.priceRowCenters,bodySize:40,phoneBodySize:40*390/p.w,unitVisibleHeightRatio:.72,positionBasis:'actual advert background measurements'};
    return {requests,pages:[p],placements:placements.map(v=>({...v,page:p.id}))};
  }

  // Header: all copy is measured within the left rectangle, clear of the medic.
  const hc=m.header.copy.x+m.header.copy.width/2;
  const brandSize=29,brandW=width(c.brand,brandSize,CN,1),triW=27,gap=10,brandLeft=hc-(brandW+triW+gap)/2;
  raw(`<g transform="translate(${brandLeft} 122)" fill="none" stroke="${C.ink}" stroke-width="1.9" stroke-linejoin="round"><path d="M0 25L13.5 0L27 25Z M7 21L13.5 10L20 21Z"/></g>`);
  tx(c.brand,brandLeft+triW+gap,135,brandSize,{style:1,role:'brand'});
  art(c.title,hc,190,67,{maxWidth:318,outlineWidth:2.7});
  raw(`<rect x="72" y="236" width="310" height="33" rx="15" fill="${C.ink}"/>`);
  richLine([{text:c.mode+' · '+platformLabel,size:25.5,bold:true,fill:C.paper}],{x:80,y:237,width:294,height:31},{role:'platform-mode',size:25.5});

  // Wish reward: when + either choice + the conditional reward are all visible.
  richLine([{text:c.display.rewardHeading,bold:true,gap:16},{text:c.display.wishWhen}],{x:60,y:328,width:528,height:32},{size:31,role:'wish-heading-and-when'});
  richLine([{text:c.wishReward.choices.join(' / '),bold:true}],{x:60,y:365,width:528,height:39},{size:39,role:'wish-choices'});
  const rewardRuns=[{text:c.display.wishTrigger,size:31,gap:9},...unitRuns(c.wishReward.guarantee,c.wishReward.unit,58,C.gold,'reward',5)];
  rewardRuns.at(-1).gap=9;rewardRuns.push({text:c.display.wishGuaranteeLabel,bold:true,size:31});
  richLine(rewardRuns,{x:60,y:405,width:528,height:47},{role:'wish-trigger-and-guarantee'});

  // Rebuild the table's simple vector lines into two columns. The approved art stays.
  raw(`<defs><linearGradient id="table-paper-${platformKey}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FFF4D6"/><stop offset="1" stop-color="#FFF0CD"/></linearGradient><clipPath id="table-body-${platformKey}"><path d="M39 550H797V842Q797 862 777 862H59Q39 862 39 842Z"/></clipPath></defs><g clip-path="url(#table-body-${platformKey})"><rect x="39" y="550" width="758" height="312" fill="url(#table-paper-${platformKey})"/><rect x="39" y="736" width="758" height="126" fill="#F8E8BE"/><path d="M418 550V862" fill="none" stroke="#C9B78C" stroke-width="1.25"/><path d="M51 612Q416 611.5 785 612M51 674Q416 674.5 785 674M51 736Q416 736 785 736M51 799Q416 798.5 785 799" fill="none" stroke="#CFBF99" stroke-width="1.25"/></g>`);
  for(const [s,cx] of [[c.display.priceColumnLabels[0],229],[c.display.priceColumnLabels[1],611]])tx(s,cx,524,31,{style:1,anchor:'middle',fill:C.paper,role:'table-heading'});
  const unitColumns=[];
  for(const [key,col] of [[platformKey,[436,786]]]){
    const values=c.prices.map(v=>v[key]),nw=Math.max(...values.map(v=>width(v,48,NUM,1))),u=unitRuns(values[0],'r',48)[1],uw=width('r',u.size,NUM,1),gx=8;
    const ux=(col[0]+col[1])/2+(nw+gx-uw)/2;unitColumns.push({platform:key,unitX:ux,unitSize:u.size,gap:gx});
    for(let i=0;i<c.prices.length;i++){
      const cy=m.prices.rowCenters[i],v=c.prices[i][key],color=i>=3?C.gold:C.ink;
      tx(v,ux-gx,cy,48,{font:NUM,style:1,anchor:'end',fill:color,role:'price-number'});
      tx('r',ux,cy,u.size,{font:NUM,style:1,fill:color,role:'price-unit'});
    }
  }
  for(let i=0;i<c.prices.length;i++)tx(c.prices[i].label,229,m.prices.rowCenters[i],33,{anchor:'middle',style:i>=3?1:0,role:'tier-label'});

  // Same successful extraction AND all three targets; large counts stay in context.
  const oc=m.objectives.title.x+m.objectives.title.width/2;
  tx(c.display.objectiveHeading,oc,913,37,{style:1,anchor:'middle',role:'objective-heading'});
  richLine([{text:c.display.objectiveRelation}],{x:m.objectives.title.x,y:938,width:m.objectives.title.width,height:32},{size:31,role:'objective-relation'});
  const safeTargets=[c.objectives.sandSafes,c.objectives.regularSafes];
  for(let i=0;i<safeTargets.length;i++){
    const t=safeTargets[i];richLine([{text:c.objectives.scope+t.verb,gap:10},{text:t.count,font:NUM,size:44,bold:true,fill:C.gold,gap:7,role:'objective-count'},{text:'个',gap:9},{text:t.name,bold:true}],m.objectives.rows[i],{size:31,role:'safe-target-'+i});
  }
  const lootRuns=[{text:c.display.lootObjectivePrefix,gap:12},{text:c.objectives.loot.operator,bold:true,gap:7},...unitRuns(c.objectives.loot[platformKey],c.objectives.loot.unit,44,C.gold,'loot-'+platformKey,5)];
  richLine(lootRuns,m.objectives.rows[2],{size:33,role:'loot-target'});

  // Necessary exceptions are ordinary-size copy with selective real bold emphasis.
  const ruleBox=i=>({x:m.notices.groups[i].x+8,y:m.notices.groups[i].y,width:m.notices.groups[i].width-16,height:m.notices.groups[i].height});
  const noticeLeft=ruleBox(0).x,noticeAlignment=c.display.noticeAlignment??'left';
  tx(c.display.noticeHeading,noticeLeft,1300,37,{style:1,role:'notice-heading'});
  richParagraph(c.display.noticeCompletion,ruleBox(0),{size:31,gap:7,role:'rule-completion',blockAlign:noticeAlignment});
  const broken=c.notices.find(v=>v.id==='broken-order');
  const brokenLine=[{text:c.display.noticeBrokenOrderPrefix},...unitRuns(broken.loot,broken.unit,31,C.gold,'broken-order-loot',3),{text:c.display.noticeBrokenOrderSuffix}];
  richParagraph([brokenLine,c.display.noticeBrokenOrderResult],ruleBox(1),{size:31,gap:7,role:'rule-broken-order',blockAlign:noticeAlignment});
  richParagraph(c.display.noticeSafeInteraction,ruleBox(2),{size:31,gap:7,role:'rule-safe-interaction',blockAlign:noticeAlignment});
  richLine(c.display.noticeExtraGuarantee,ruleBox(3),{size:31,role:'rule-extra-guarantee',align:noticeAlignment});

  p.alignment={priceUnitColumns:unitColumns,priceRowCenters:m.prices.rowCenters,unitVisibleHeightRatio:.72,bodyFont:CN,bodySize:31,phoneBodySize:31*390/p.w,selectiveBold:true,noticeAlignment,noticeLeft,positionBasis:'actual decorated native background measurements'};
  p.sourcePrices=c.prices.map(v=>({id:v.id,label:v.label,priceR:v[platformKey],unit:v.unit}));
  return {requests,pages:[p],placements:placements.map(v=>({...v,page:p.id}))};
}

export function makeLayout(c,m,glyphs={}) {
  const plans=[...['desktop','mobile'].map(key=>makePlatformLayout(c,m,glyphs,key,'price')),makePlatformLayout(c,m,glyphs,'combined','advert'),makePlatformLayout(c,m,glyphs,'combined','cover')];
  const requests=[...new Map(plans.flatMap(v=>v.requests).map(v=>[v.id,v])).values()];
  return {requests,pages:plans.flatMap(v=>v.pages),placements:plans.flatMap(v=>v.placements)};
}
