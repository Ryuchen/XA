import {createHash} from 'node:crypto';

export function makeLayout(content){
 const ink='#203F53',secondary='#55788B',gold='#A8651B',teal='#117D72',paper='#FFFDF5';
 const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
 const requests=[],ids=new Map();
 function request(text,font=CN,style=1){const key=JSON.stringify([String(text),font,style]);if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text:String(text),font,style});}return ids.get(key);}
 function page(id,label,bg,w,h,ew,eh){return {id,label,bg,w,h,ew,eh,platforms:id==='social'?['desktop','mobile']:[id.startsWith('mobile')?'mobile':'desktop'],elements:[]};}
 function tx(p,text,x,cy,size=16,{font=CN,style=1,fill=ink,anchor='start',maxWidth,role='copy',stroke,strokeWidth}={}){if(text===undefined||text===null)throw Error('Missing copy');p.elements.push({type:'text',id:request(text,font,style),text:String(text),x,cy,size,fill,anchor,maxWidth,role,stroke,strokeWidth});}
 function art(p,text,x,cy,size,{maxWidth,fill=ink,shadow='#A5BDD1',outline=paper,outlineWidth=3,angles=true}={}){p.elements.push({type:'art',text,x,cy,size,maxWidth,fill,shadow,outline,outlineWidth,angles,ids:[...text].map(c=>request(c,ART,0))});}
 function raw(p,markup){p.elements.push({type:'raw',markup});}
 function inline(p,parts,x,cy,{anchor='start',maxWidth,gap=8,role='inline-copy'}={}){p.elements.push({type:'rich',x,cy,anchor,maxWidth,size:20,role,group:`${role}:${x}:${cy}`,runs:parts.map(s=>({text:String(s.text),id:request(s.text,s.font??CN,s.style??1),size:s.size??20,fill:s.fill??ink,gap:s.gap??gap,role:s.role??role}))});}
 function band(p,x,y,w,h,color){raw(p,`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="9" fill="${color}" fill-opacity="0.07" stroke="${color}" stroke-opacity="0.14" stroke-width="0.8"/>`);}
 function brand(p,x,cy,size,halo=false){const k=size/28,d='M0 27L15 0L31 27Z M8 22L15 10L23 22Z';raw(p,`<g transform="translate(${x} ${cy-size*.52}) scale(${k})" fill="none" stroke-linejoin="round">${halo?`<path d="${d}" stroke="${paper}" stroke-width="7"/>`:''}<path d="${d}" stroke="${ink}" stroke-width="2.1"/></g>`);tx(p,content.brand,x+size*1.35,cy,size,{role:'brand',stroke:halo?paper:undefined,strokeWidth:halo?3:undefined});}
 function number(p,value,unit,unitX,cy,size,fill,unitSize=size*.955,role='number',gap=7,unitFont=NUM){tx(p,value,unitX-gap,cy,size,{font:NUM,fill,anchor:'end',role:role+'-number'});tx(p,unit,unitX,cy,unitSize,{font:unitFont,fill,role:role+'-unit'}); for(const e of p.elements.slice(-2))e.lineGroup=`${role}:${unitX}:${cy}`;}
 function lines(p,items,x,cy,size,spacing,{maxWidth,fill=ink,style=0,role='rule-body',anchor='start'}={}){items.forEach((s,j)=>tx(p,s,x,cy+(j-(items.length-1)/2)*spacing,size,{maxWidth,fill,style,role,anchor}));}
 const centers=edges=>edges.slice(0,-1).map((v,i)=>(v+edges[i+1])/2);
 function section(p,title,cy,condition){art(p,title,67,cy,25,{maxWidth:210,fill:paper,shadow:'#142E42',outline:ink,outlineWidth:.6,angles:false});if(condition)tx(p,condition,747,cy,17.2,{anchor:'end',fill:'#E5F0F5',maxWidth:420,role:'section-condition'});}
 if(content.tiers.length!==4||content.tiers.some(t=>t.rows.length!==6))throw Error('Timed clear needs four categories with six rows.');
 if(content.tiers.some(t=>t.rows[0].deadlineMinutes!==null||t.rows.slice(1).some((r,i)=>r.deadlineMinutes!==content.mechanism.timed.deadlinesMinutes[i])))throw Error('Tier deadlines must match the source.');
 const mechanism=content.mechanism,clear=mechanism.clearStandard,minimum=mechanism.minimumRequirement;

 function poster(t){
  const color=t.color==='gold'?gold:teal,rules=content.rules[t.mode];
  const p=page(t.id,`${t.platform} · ${t.mode}版 · ${content.title}`,t.color==='gold'?'poster-gold.png':'poster-teal.png',814,t.color==='gold'?1933:1932,3200,7600);
  brand(p,58,61,22,true);
  art(p,content.titleLines[0],60,128,78,{maxWidth:235,outlineWidth:3.8});
  art(p,content.titleLines[1],51,216,74,{maxWidth:255,outlineWidth:3.8});
  tx(p,`${t.platform} · ${t.mode}版`,190,293,24.3,{anchor:'middle',fill:paper,maxWidth:264,role:'platform-mode'});
  tx(p,t.mapLabel+'清图一把',222,362,18.8,{anchor:'middle',maxWidth:193,role:'service'});
  tx(p,'订单价格',260,423.5,21.3,{anchor:'middle',fill:paper,role:'table-heading'});
  tx(p,'清图时限',631,423.5,21.3,{anchor:'middle',fill:paper,role:'table-heading'});
  const edges=[447,503,559,615,670,727,782],rowCenters=centers(edges);
  t.rows.forEach((r,i)=>{
   const cy=rowCenters[i];number(p,r.priceR,'r',305,cy,48,ink,45.8,'price',8);
   if(r.tierType==='regular')tx(p,mechanism.regular.title,631,cy,28.5,{anchor:'middle',fill:secondary,role:'regular-tier'});
   else number(p,r.deadlineMinutes,'分钟内',621,cy,50,color,23.5,'deadline',9,CN);
  });
  p.alignment={rowEdges:edges,rowCenters,priceUnitColumn:305,deadlineUnitColumn:621,priceGap:8,deadlineGap:9,vertical:'visible glyph center',rightIcons:false};
  tx(p,'r = 元 / w = 万',759,791,9.8,{anchor:'end',fill:secondary,style:0,role:'units'});
  tx(p,'限时档保障',161,816,13.8,{fill:color,role:'guarantee-scope'});
  art(p,mechanism.timed.copy.replace('，',''),160,846,36,{maxWidth:581,fill:color,outlineWidth:1.6,angles:false});
  tx(p,mechanism.timed.successCondition,161,878,15.2,{maxWidth:580,role:'guarantee-condition'});

  section(p,'清图标准',934,'满足其一即可');
  tx(p,clear.methods[0].title,145,1013,25,{maxWidth:224,role:'method-title'});
  lines(p,clear.methods[0].conditions,222,1101,19.8,26,{maxWidth:300,role:'lever-conditions',anchor:'middle'});
  tx(p,'或',407,1076,24.5,{anchor:'middle',fill:color,role:'method-operator'});
  tx(p,clear.methods[1].title,507,1013,25,{maxWidth:235,role:'method-title'});
  tx(p,'规定时限内'+clear.methods[1].actor+'击杀',592,1059,18.8,{anchor:'middle',maxWidth:290,role:'kill-deadline'});
  band(p,469,1080,246,64,color);
  inline(p,[{text:'≥',size:30,fill:color,role:'kill-comparison'},{text:clear.methods[1].kills,font:NUM,size:53,fill:color,role:'kill-target-number',gap:10},{text:'人',size:28,fill:color,role:'kill-target-unit',gap:8}],592,1112,{anchor:'middle',role:'kill-goal'});

  tx(p,'最低要求 · 成功撤离时',155,1192,13.8,{role:'minimum-scope'});
  tx(p,'缺一不算清图成功',747,1192,13.8,{anchor:'end',fill:secondary,role:'minimum-failure'});
  inline(p,[{text:`陪玩击杀≥${minimum.successKillsAtLeast}人`,size:23,role:'minimum-kills'},{text:'且',size:22,fill:color,role:'minimum-operator',gap:22},{text:'撤离物资',size:21,role:'minimum-loot-label',gap:22},{text:'≥',size:26,fill:color,role:'minimum-loot-comparison',gap:9},{text:rules.minimumLootW,font:NUM,size:34,fill:color,role:'minimum-loot-number',gap:8},{text:'w',font:NUM,size:32.45,fill:color,role:'minimum-loot-unit',gap:7}],654,1225,{anchor:'end',maxWidth:592,role:'minimum-requirements'});

  section(p,'下单须知',1288,'地图 · 物资 · 房卡 · 速撤');
  const noteEdges=[1322,1395,1506,1639,1766],noteCenters=centers(noteEdges);
  ['地图选择','物资说明','房卡保障','打手保障'].forEach((label,i)=>tx(p,label,171,noteCenters[i],18.7,{maxWidth:110,role:'rule-label'}));
  tx(p,'本单仅限'+rules.map,293,noteCenters[0],21,{maxWidth:454,style:0,role:'map-restriction'});
  lines(p,['成功达标前，成功撤离带出物资计入累计物资；','若对应炸单保底大于累计物资保底，','则在打满结单条件后，再补保底。'],293,noteCenters[1],18.2,26,{maxWidth:454,role:'loot-compensation'});
  tx(p,'清图后开卡；未开仍按俱乐部规定补偿。',293,1532,17.4,{maxWidth:454,style:0,role:'card-timing'});
  tx(p,'含'+rules.cards.included+'，每少一张补偿：',293,1558,17.4,{maxWidth:454,style:0,role:'included-cards'});
  rules.cards.missingPerCard.forEach((v,i)=>{const x=i===0?293:529;band(p,x,1580,218,43,color);inline(p,[{text:v.label,size:20,role:'missing-card'},{text:'+'+v.amountW,font:NUM,size:28.8,fill:color,role:'missing-card-compensation-number',gap:14},{text:'w',font:NUM,size:27.5,fill:color,role:'missing-card-compensation-unit',gap:6}],x+178,1601.5,{anchor:'end',maxWidth:196,role:'card-compensation'});});
  tx(p,'规定时限内，打手'+mechanism.quickExtraction.killsRequirementCopy,520,1670,18.2,{anchor:'middle',maxWidth:454,style:0,role:'quick-extraction-condition'});
  band(p,293,1693,454,48,color);
  inline(p,[{text:'且物资',size:21,role:'quick-extraction-and'},{text:mechanism.quickExtraction.materialComparison,size:27,fill:color,role:'quick-extraction-comparison',gap:10},{text:rules.quickExtractionLootW,font:NUM,size:32,fill:color,role:'quick-extraction-loot-number',gap:8},{text:'w',font:NUM,size:30.55,fill:color,role:'quick-extraction-loot-unit',gap:6},{text:'→',size:22,fill:color,gap:16},{text:'允许速撤',size:23,fill:color,role:'quick-extraction-result',gap:16}],677,1717,{anchor:'end',maxWidth:422,role:'quick-extraction-summary'});
  tx(p,content.footerInstruction,407,1808,14.5,{anchor:'middle',maxWidth:385,role:'footer'});
  return p;
 }

 function cover(){
  const p=page('cover',`${content.title} · 横版封面`,'cover.png',1672,941,3200,1800);
  brand(p,96,110,37);
  art(p,content.titleLines[0],101,279,160,{maxWidth:543,outlineWidth:6});
  art(p,content.titleLines[1],80,493,174,{maxWidth:560,outlineWidth:7});
  tx(p,content.subtitle,419,706,35,{anchor:'middle',fill:paper,maxWidth:612,role:'service'});
  tx(p,'端游 / 手游 · 绝密 / 机密',94,766,22,{stroke:paper,strokeWidth:3,maxWidth:620,role:'versions'});
  return p;
 }

 function social(){
  const p=page('social',`${content.title} · 简洁宣发海报`,'social.png',1086,1448,2400,3200);
  brand(p,62,65,27,true);
  art(p,content.titleLines[0],68,166,101,{maxWidth:320,outlineWidth:4});
  art(p,content.titleLines[1],53,277,101,{maxWidth:348,outlineWidth:4.3});
  tx(p,'航天清图一把 · 常规 / 限时档',156,387,26,{maxWidth:846,role:'service'});
  const cards=[{mode:451.5,header:489,edges:[510,554,600,645,690,735,780],cols:[56,267,527]},{mode:451.5,header:489,edges:[510,554,600,645,690,735,780],cols:[559,765,1030]},{mode:813,header:850,edges:[870,916,961,1006,1051,1096,1142],cols:[56,267,527]},{mode:813,header:850,edges:[870,916,961,1006,1051,1096,1142],cols:[559,765,1030]}];
  p.alignment=[];
  content.tiers.forEach((t,ti)=>{
   const c=cards[ti],color=t.color==='gold'?gold:teal,rows=centers(c.edges),priceX=c.cols[0]+151,timeX=c.cols[1]+127;
   tx(p,`${t.platform} · ${t.mode}版`,(c.cols[0]+c.cols[2])/2,c.mode,22.8,{anchor:'middle',fill:color,role:'platform-mode'});
   tx(p,'订单价格',c.cols[0]+123,c.header,19.5,{anchor:'middle',fill:paper,role:'table-heading'});
   tx(p,'清图时限',(c.cols[1]+c.cols[2])/2,c.header,19.5,{anchor:'middle',fill:paper,role:'table-heading'});
   t.rows.forEach((r,ri)=>{number(p,r.priceR,'r',priceX,rows[ri],36.8,ink,35.15,'price',6);if(r.tierType==='regular')tx(p,mechanism.regular.title,(c.cols[1]+c.cols[2])/2,rows[ri],24,{anchor:'middle',fill:secondary,role:'regular-tier'});else number(p,r.deadlineMinutes,'分钟内',timeX,rows[ri],39,color,21,'deadline',7,CN);});
   p.alignment.push({id:t.id,rowEdges:c.edges,rowCenters:rows,colEdges:c.cols,priceUnitColumn:priceX,deadlineUnitColumn:timeX,priceGap:6,deadlineGap:7});
  });
  tx(p,'限时档保障',232,1179,15.2,{fill:secondary,role:'guarantee-scope'});
  art(p,mechanism.timed.copy.replace('，',''),231,1220,40,{maxWidth:774,outlineWidth:1.6,angles:false});
  tx(p,`拉闸清图 或 规定时限内陪玩击杀≥${clear.methods[1].kills}人`,232,1270,21.5,{maxWidth:774,role:'method-summary'});
  tx(p,`成功撤离仍需：陪玩击杀≥${minimum.successKillsAtLeast}人 且 物资达标`,543,1311,21.5,{anchor:'middle',maxWidth:915,role:'minimum-condition'});
  ['绝密','机密'].forEach((mode,i)=>{const color=i===0?gold:teal,labelX=i===0?216:719,unitX=i===0?354:857;tx(p,mode+' ≥',labelX,1347,22.5,{fill:color,role:'minimum-mode'});number(p,content.rules[mode].minimumLootW,'w',unitX,1347,29,color,27.7,'minimum-loot',6);});
  tx(p,'r = 元 / w = 万',543,1407,12.5,{anchor:'middle',fill:ink,style:0,stroke:paper,strokeWidth:2.5,role:'units'});
  return p;
 }
 return {version:'timed-clear-mobile-price-r3',requests,pages:[...content.tiers.filter(t=>content.deliveryPageIds.includes(t.id)).map(poster),social()]};
}
