import {createHash} from 'node:crypto';

// Native pixel coordinates measured on the approved Jiahao backgrounds.
export function makeLayout(content){
const ink='#203F53',secondary='#55788B',gold='#A8651B',teal='#117D72',paper='#FFFDF5';
const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
const requests=[],ids=new Map();
function request(text,font=CN,style=1){const key=JSON.stringify([String(text),font,style]);if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text:String(text),font,style});}return ids.get(key);}
function page(id,label,bg,w,h,ew,eh){return {id,label,bg,w,h,ew,eh,elements:[]};}
function tx(p,text,x,cy,size=16,{font=CN,style=1,fill=ink,anchor='start',maxWidth,role='copy',stroke,strokeWidth}={}){if(text===undefined||text===null)throw Error('Missing copy');p.elements.push({type:'text',id:request(text,font,style),text:String(text),x,cy,size,fill,anchor,maxWidth,role,stroke,strokeWidth});}
function art(p,text,x,cy,size,{maxWidth,fill=ink,shadow='#A5BDD1',outline=paper,outlineWidth=3,angles=true}={}){p.elements.push({type:'art',text,x,cy,size,maxWidth,fill,shadow,outline,outlineWidth,angles,ids:[...text].map(c=>request(c,ART,0))});}
function raw(p,markup){p.elements.push({type:'raw',markup});}
function brand(p,x,cy,size){const k=size/28;raw(p,`<g transform="translate(${x} ${cy-size*.52}) scale(${k})" fill="none" stroke="${ink}" stroke-width="2.1" stroke-linejoin="round"><path d="M0 27L15 0L31 27Z M8 22L15 10L23 22Z"/></g>`);tx(p,content.brand,x+size*1.35,cy,size,{role:'brand'});}
function number(p,value,unit,unitX,cy,size,fill,unitSize=size*.955,role='number',gap=7){tx(p,value,unitX-gap,cy,size,{font:NUM,fill,anchor:'end',role:role+'-number'});tx(p,unit,unitX,cy,unitSize,{font:NUM,fill,role:role+'-unit'});}
function lines(p,items,x,cy,size,spacing,{maxWidth,fill=ink,style=0,role='rule-body',anchor='start'}={}){items.forEach((s,j)=>tx(p,s,x,cy+(j-(items.length-1)/2)*spacing,size,{maxWidth,fill,style,role,anchor}));}
function centers(edges){return edges.slice(0,-1).map((v,i)=>(v+edges[i+1])/2);}
if(content.tiers.length!==4||content.tiers.some(t=>t.rows.length!==4))throw Error('Jiahao requires four categories with four rows each.');
const mechanism=content.mechanism,guarantee=mechanism.guarantee,failures=mechanism.failureConditions;
const gate=mechanism.clearance.methods.find(m=>m.id==='gate'),kills=mechanism.clearance.methods.find(m=>m.id==='eliminations');
const slotRule=failures.items.find(c=>c.id==='backpack');
const fallback=failures.items.find(c=>c.id==='fallback');
const extractionFailure=failures.items.find(c=>c.id==='extraction');
function section(p,title,cy,rightText){art(p,title,68,cy,25,{maxWidth:190,fill:paper,shadow:'#142E42',outline:ink,outlineWidth:.6,angles:false});if(rightText)tx(p,rightText,766,cy,17.2,{anchor:'end',fill:'#E5F0F5',maxWidth:460,role:'section-condition'});}
function poster(t){
 const color=t.color==='gold'?gold:teal,rules=content.rules[t.mode],bonus=guarantee.additionalGuaranteeWByMode[t.mode],slots=slotRule.minimumSlotsByMode[t.mode];
 const p=page(t.id,`${t.platform} · ${t.mode}版 · ${content.title}`,t.color==='gold'?'poster-gold.png':'poster-teal.png',836,t.color==='gold'?1881:1882,3200,7200);
 brand(p,57,73,25);
 art(p,content.titleLines[0],62,135,84,{maxWidth:270,outlineWidth:3.6});
 art(p,content.titleLines[1],52,224,83,{maxWidth:282,outlineWidth:3.8});
 tx(p,`${t.platform} · ${t.mode}版`,192,293,24.5,{anchor:'middle',fill:paper,maxWidth:260,role:'platform-mode'});
 tx(p,content.subtitle,64,359,16.5,{maxWidth:279,role:'mechanism'});
 tx(p,mechanism.flow.join(' → '),64,384,15.4,{maxWidth:279,fill:secondary,role:'flow'});
 tx(p,content.columns.price,232,441,24,{anchor:'middle',fill:paper,role:'table-heading'});
 tx(p,content.columns.loot,635,441,24,{anchor:'middle',fill:paper,role:'table-heading'});
 const edges=[465,538,611,685,759],rows=centers(edges);
 rows.forEach((cy,i)=>{const r=t.rows[i];number(p,r.priceR,'r',287,cy,54,ink,51.5,'price',8);number(p,r.lootW,'w',698,cy,56,color,53.5,'loot',8);});
 tx(p,'r = 元 / w = 万',783,769,10.2,{anchor:'end',fill:secondary,style:0,role:'units'});
 art(p,guarantee.title,211,810,29,{maxWidth:290,fill:color,outlineWidth:1.3,angles:false});
 tx(p,guarantee.trigger+'，保底',211,853,23,{maxWidth:267,role:'guarantee-trigger'});
 number(p,'+'+bonus,'w',633,853,54,color,51.6,'failed-round-bonus',8);
 tx(p,guarantee.failedRoundLoot.replace('，',' · '),211,902,19.6,{maxWidth:552,role:'guarantee-condition'});
 section(p,'清图说明',971,`${mechanism.clearance.deadlineMinutes}分钟内 · 任一方式完成`);
 tx(p,gate.title,170,1044,25,{maxWidth:205,role:'clearance-method'});
 lines(p,[gate.requirements[0]+'；',gate.requirements[1]+'，',gate.requirements[2]+'。'],74,1133,18.7,28,{maxWidth:304,role:'gate-conditions'});
 tx(p,kills.title,554,1044,25,{maxWidth:216,role:'clearance-method'});
 tx(p,kills.aggregate+'击杀',612,1102,18.8,{anchor:'middle',maxWidth:316,role:'kill-scope'});
 tx(p,kills.operatorCount,560,1150,49,{font:NUM,anchor:'end',fill:color,role:'kill-count'});
 tx(p,'名'+kills.target,574,1150,22,{maxWidth:184,role:'kill-target'});
 raw(p,`<circle cx="418" cy="1105" r="16.5" fill="${paper}" stroke="#B5CDDA" stroke-width="1"/>`);
 tx(p,'或',418,1105,19,{anchor:'middle',fill:color,role:'clearance-relation'});
 section(p,'嘉豪标准',1229,failures.intro);
 const standardX=[133.5,323.5,513.5,703];
 const conditionLines=[
  [`未在${mechanism.timeLimitMinutes}分钟内`,'清图'],
  [fallback.text.split(' / ')[0]+' /',fallback.text.split(' / ')[1]],
  [slotRule.when,`背包不足${slots}格`],
  [extractionFailure.text]
 ];
 conditionLines.forEach((ls,i)=>lines(p,ls,standardX[i],1386,i===3?23:18.4,27,{anchor:'middle',maxWidth:151,style:1,role:'failure-condition'}));
 section(p,'下单须知',1475,'航天专属 · 房卡保障 · 售后');
 tx(p,'地图选择',175,1537,19,{maxWidth:122,role:'rule-label'});
 tx(p,rules.mapRestriction+'。',322,1537,18.2,{maxWidth:448,style:0,role:'map-restriction'});
 tx(p,'房卡保障',175,1605,19,{maxWidth:122,role:'rule-label'});
 tx(p,'含'+rules.cards.included+'；每少一张补偿：',322,1587,16.1,{maxWidth:449,style:0,role:'included-cards'});
 rules.cards.missingPerCard.forEach((v,i)=>{tx(p,v.label,i===0?322:558,1620,17.7,{role:'missing-card'});number(p,'+'+v.amountW,'w',i===0?484:734,1620,23,color,22,'missing-card-compensation',5);});
 tx(p,'物资与售后',175,1680,18.8,{maxWidth:122,role:'rule-label'});
 const ownership=rules.ownershipAndAfterSales;
 lines(p,[ownership[0].split('，按约定')[0]+'，','按约定价值计入。'+ownership[1]],322,1680,16.5,25,{maxWidth:448,role:'ownership-aftersales'});
 tx(p,content.footerInstruction,418,1761,15.5,{anchor:'middle',maxWidth:390,role:'footer'});
 p.alignment={rowCenters:rows,rowEdges:edges,unitColumns:{priceR:287,lootW:698},numberGap:8,vertical:'visible glyph center',noRightIcons:true};
 return p;
}
function cover(){
 const p=page('cover',`${content.title} · 横版封面`,'cover.png',1672,941,3200,1800);
 brand(p,98,133,38);
 art(p,content.titleLines[0],102,281,153,{maxWidth:551,outlineWidth:6});
 art(p,content.titleLines[1],84,467,180,{maxWidth:594,outlineWidth:7});
 tx(p,content.subtitle,368,660,32,{anchor:'middle',fill:paper,maxWidth:602,role:'mechanism'});
 tx(p,'端游 / 手游 · 绝密 / 机密',95,740,27,{maxWidth:585,stroke:paper,strokeWidth:3,role:'versions'});
 return p;
}
function social(){
 const p=page('social',`${content.title} · 简洁宣发海报`,'social.png',1086,1448,2400,3200);
 brand(p,70,66,27);
 art(p,content.titleLines[0],76,134,87,{maxWidth:359,outlineWidth:4});
 art(p,content.titleLines[1],63,237,102,{maxWidth:382,outlineWidth:4.4});
 tx(p,content.subtitle,148,344,28,{maxWidth:857,role:'mechanism'});
 tx(p,mechanism.scope+' · '+mechanism.flow.join(' → '),148,371,16.5,{maxWidth:860,role:'scope-flow'});
 const cards=[{x:53,mode:418,header:459,edges:[480,539,601,662,725]},{x:562,mode:418,header:459,edges:[480,539,601,662,725]},{x:53,mode:761,header:803,edges:[823,884,945,1007,1068]},{x:562,mode:761,header:803,edges:[823,884,945,1007,1068]}];
 p.alignment=[];
 content.tiers.forEach((t,i)=>{const c=cards[i],dx=c.x-53,color=t.color==='gold'?gold:teal,rows=centers(c.edges);
  tx(p,`${t.platform} · ${t.mode}版`,c.x+235.5,c.mode,25,{anchor:'middle',fill:color,role:'platform-mode'});
  tx(p,content.columns.price,178+dx,c.header,18,{anchor:'middle',fill:paper,role:'table-heading'});
  tx(p,content.columns.loot,435+dx,c.header,18,{anchor:'middle',fill:paper,role:'table-heading'});
  rows.forEach((cy,j)=>{const r=t.rows[j];number(p,r.priceR,'r',218+dx,cy,44,ink,42,'price',6);number(p,r.lootW,'w',472+dx,cy,44,color,42,'loot',6);});
  p.alignment.push({id:t.id,rowCenters:rows,rowEdges:c.edges,unitColumns:{priceR:218+dx,lootW:472+dx},numberGap:6,noRightIcons:true});
 });
 art(p,guarantee.title,150,1116,28,{maxWidth:300,outlineWidth:1.4,angles:false});
 tx(p,guarantee.trigger+'，追加对应保底',1000,1116,18.8,{anchor:'end',maxWidth:495,role:'guarantee-trigger'});
 ['绝密','机密'].forEach((mode,i)=>{const dx=i?488:0,color=i?teal:gold;tx(p,mode,108+dx,1182,24,{fill:color,role:'guarantee-mode'});tx(p,'保底',187+dx,1182,19,{fill:color,role:'guarantee-label'});number(p,'+'+guarantee.additionalGuaranteeWByMode[mode],'w',434+dx,1182,47,color,44.9,'failed-round-bonus',6);});
 tx(p,guarantee.failedRoundLoot.replace('，',' · '),543,1244,22,{anchor:'middle',maxWidth:890,role:'guarantee-condition'});
 lines(p,[`任一即炸单：${mechanism.timeLimitMinutes}分钟内未清图；${fallback.text}；`,`${slotRule.when}背包不足（绝密${slotRule.minimumSlotsByMode['绝密']}格 / 机密${slotRule.minimumSlotsByMode['机密']}格）；${extractionFailure.text}。`],543,1296,16.2,28,{anchor:'middle',maxWidth:887,role:'failure-condition'});
 tx(p,'r = 元 / w = 万',543,1386,12.5,{anchor:'middle',fill:secondary,style:0,role:'units'});
 return p;
}
return {version:'jiahao-v1',requests,pages:[...content.tiers.map(poster),cover(),social()]};
}
