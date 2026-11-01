import {createHash} from 'node:crypto';

// Coordinates measured on the approved excess-campaign backgrounds.
export function makeLayout(content){
const ink='#203F53',secondary='#57768A',gold='#A5651E',teal='#147E77',paper='#FFFDF5';
const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
const requests=[],ids=new Map();
function request(text,font=CN,style=1){const key=JSON.stringify([String(text),font,style]);if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text:String(text),font,style});}return ids.get(key);}
function page(id,label,bg,w,h,ew,eh){return {id,label,bg,w,h,ew,eh,elements:[]};}
function tx(p,text,x,cy,size=16,{font=CN,style=1,fill=ink,anchor='start',maxWidth,role='copy',stroke,strokeWidth}={}){if(text===undefined||text===null)throw Error('Missing copy');p.elements.push({type:'text',id:request(text,font,style),text:String(text),x,cy,size,fill,anchor,maxWidth,role,stroke,strokeWidth});}
function art(p,text,x,cy,size,{maxWidth,fill=ink,shadow='#A5BDD1',outline=paper,outlineWidth=3,angles=true}={}){p.elements.push({type:'art',text,x,cy,size,maxWidth,fill,shadow,outline,outlineWidth,angles,ids:[...text].map(c=>request(c,ART,0))});}
function raw(p,markup){p.elements.push({type:'raw',markup});}
function brand(p,x,cy,size){const k=size/28;raw(p,`<g transform="translate(${x} ${cy-size*.52}) scale(${k})" fill="none" stroke="${ink}" stroke-width="2.1" stroke-linejoin="round"><path d="M0 27L15 0L31 27Z M8 22L15 10L23 22Z"/></g>`);tx(p,content.brand,x+size*1.35,cy,size,{role:'brand'});}
function number(p,value,unit,unitX,cy,size,fill,unitSize=size*.95,role='number',gap=7){tx(p,value,unitX-gap,cy,size,{font:NUM,fill,anchor:'end',role:role+'-number'});tx(p,unit,unitX,cy,unitSize,{font:NUM,fill,role:role+'-unit'});}
function lines(p,items,x,cy,size,spacing,{maxWidth,fill=ink,style=0,role='rule-body',anchor='start'}={}){items.forEach((s,j)=>tx(p,s,x,cy+(j-(items.length-1)/2)*spacing,size,{maxWidth,fill,style,role,anchor}));}
function centers(edges){return edges.slice(0,-1).map((v,i)=>(v+edges[i+1])/2);}
if(content.tiers.length!==4||content.tiers.some(t=>t.rows.length!==(t.platform==='端游'?5:4)))throw Error('Expected four categories, desktop 5 rows and mobile 4 rows.');
function ruleRows(mode){
 const r=content.rules[mode],e=r.equipment,s=r.settlement;
 const cardLines=r.cards.map(c=>`${c.map}含${c.included}`);
 const maps=[`地图：${r.maps.join(' / ')}；下单先与客服确认。`,'中途换图需协商。'+cardLines[0]+'；',cardLines.slice(1).join('；')+'。'];
 const fail=r.failedOrderCompensation;
 const failValue=fail.byMap?fail.byMap.map(v=>`${v.maps.join(' / ')}：+${v.amountW}w。`).join(' '):`每局增加 ${fail.perRoundW}w 保底。`;
 return [
  {title:'地图与房卡',lines:maps},
  {title:'随行装备',lines:[`默认 ${e.minimumBackpackSlots} 格以上背包 + ${e.revolverCount} 把左轮，进图前核对。`,e.exception+'。']},
  {title:'缺项补偿',compensation:r.missingEquipmentCompensation},
  {title:'炸单补偿',lines:[fail.condition+'：',failValue]},
  {title:'结算口径',lines:[s.condition+'，安全箱带出物资不计入；',`安全箱物资 ${s.noBattleLossCompensationWhenSafeBoxW.operator} ${s.noBattleLossCompensationWhenSafeBoxW.value}w 时无需补战损，`,`否则${s.otherwise}。`]},
  {title:'物资与售后',lines:['对局产出默认归板板；假账须提前征得板板同意，','按约定价值计入。全程录屏，争议交给客服核实。']}
 ];
}
function poster(t){
 const color=t.color==='gold'?gold:teal,threshold=content.mechanism.thresholdByModeW[t.mode],example=content.mechanism.examples[t.mode];
 const p=page(t.id,`${t.platform} · ${t.mode}版 · ${content.title}`,t.id+'.png',836,1882,3200,7200);
 brand(p,57,91,25);
 art(p,content.titleLines[0],61,166,88,{maxWidth:273,outlineWidth:3.5});
 art(p,content.titleLines[1],54,254,91,{maxWidth:282,outlineWidth:3.8});
 tx(p,`${t.platform} · ${t.mode}版`,201,325,25,{anchor:'middle',fill:paper,maxWidth:254,role:'platform-mode'});
 tx(p,'成功撤离 · 超额才计入',57,370,17.5,{maxWidth:278,fill:ink,stroke:paper,strokeWidth:3,role:'mechanism'});
 art(p,'只累计超过门槛的部分',68,437,30,{maxWidth:560,fill:color,outlineWidth:1.4,angles:false});
 tx(p,`${t.mode}单局门槛 ${threshold}w · 成功撤离后计入`,69,473,18.8,{maxWidth:558,role:'threshold-condition'});
 tx(p,content.columns.price,239,528,24,{anchor:'middle',fill:paper,role:'table-heading'});
 tx(p,content.columns.guarantee,648,528,24,{anchor:'middle',fill:paper,role:'table-heading'});
 const edges=t.platform==='端游'?[550,614,677,739,801,864]:t.id==='mobile-secret'?[550,626,703,780,864]:[550,628,708,786,864];
 const rows=centers(edges);
 rows.forEach((cy,i)=>{const r=t.rows[i];number(p,r.priceR,'r',287,cy,54,ink,51.5,'price',8);number(p,r.excessGuaranteeW,'w',686,cy,56,color,53.5,'guarantee',8);});
 tx(p,'表内为超额累计保底',49,873,10.2,{fill:secondary,style:0,role:'table-note'});
 tx(p,'r = 元 / w = 万',785,873,10.2,{anchor:'end',fill:secondary,style:0,role:'units'});
 art(p,'超额怎么算',66,910,24,{maxWidth:280,outlineWidth:1.3,angles:false});
 tx(p,'举例 · 成功撤离时',765,912,13.5,{anchor:'end',fill:secondary,style:0,role:'example-prerequisite'});
 for(const [cx,label]of[[175,'当局带出'],[420,'计入门槛'],[662,'本局计入']])tx(p,label,cx,960,16,{anchor:'middle',fill:secondary,role:'example-label'});
 number(p,example.extractedW,'w',198,998,36,ink,34.3,'example-extracted',5);
 number(p,example.thresholdW,'w',443,998,36,ink,34.3,'example-threshold',5);
 number(p,example.countedW,'w',680,998,38,color,36.2,'example-counted',5);
 tx(p,'−',298,994,30,{anchor:'middle',fill:color,font:NUM,role:'example-operator'});
 tx(p,'=',540,994,38,{anchor:'middle',fill:color,font:NUM,role:'example-operator'});
 tx(p,`单局未超过 ${threshold}w，本局不计订单进度。`,419,1056,16,{anchor:'middle',maxWidth:690,role:'zero-progress'});
 art(p,'带出物资全部归板板',282,1127,26,{maxWidth:481,fill:color,outlineWidth:1.2,angles:false});
 lines(p,['未超过门槛也归板板，订单只累计超额部分。',content.mechanism.closeCondition+'。'],282,1180,15.2,25,{maxWidth:479,role:'ownership-close'});
 art(p,'下单须知',68,1262,26,{maxWidth:170,fill:paper,shadow:'#142E42',outline:ink,outlineWidth:.6,angles:false});
 tx(p,'地图 · 装备 · 补偿 · 结算',767,1262,15,{anchor:'end',fill:'#DCEAF1',style:0,maxWidth:436});
 const ruleEdges=[1294,1351,1406,1464,1519,1582,1642];
 ruleRows(t.mode).forEach((r,i)=>{const cy=(ruleEdges[i]+ruleEdges[i+1])/2;
  tx(p,r.title,180,cy,17,{maxWidth:103,role:'rule-label'});
  if(r.compensation){r.compensation.forEach((v,j)=>{const y=cy+(j-.5)*23;tx(p,v.reason+'：',298,y,13.2,{style:0,role:'rule-body'});tx(p,v.items[0].label,414,y,14.2,{role:'rule-body'});number(p,'+'+v.items[0].amountW,'w',558,y,18,color,17.2,'compensation',4);tx(p,v.items[1].label,613,y,14.2,{role:'rule-body'});number(p,'+'+v.items[1].amountW,'w',749,y,18,color,17.2,'compensation',4);});}
  else lines(p,r.lines,298,cy,13.8,17,{maxWidth:479,role:'rule-body'});
 });
 brand(p,345,1776,24);
 tx(p,content.footerInstruction,418,1830,13,{anchor:'middle',maxWidth:350,role:'footer'});
 p.alignment={rowCenters:rows,rowEdges:edges,unitColumns:{priceR:287,guaranteeW:686},numberGap:8,vertical:'visible glyph center',tableColumns:[44,400,444,792],noRightIcons:true};
 return p;
}
function cover(){
 const p=page('cover',`${content.title} · 横版封面`,'cover.png',1672,941,3200,1800);
 brand(p,94,145,39);
 art(p,content.titleLines[0],102,279,156,{maxWidth:550,outlineWidth:6});
 art(p,content.titleLines[1],84,464,182,{maxWidth:594,outlineWidth:7});
 tx(p,content.subtitle,386,617,33.5,{anchor:'middle',fill:paper,maxWidth:583,role:'mechanism'});
 tx(p,'端游 / 手游 · 绝密 / 机密',91,716,27,{maxWidth:591,stroke:paper,strokeWidth:3,role:'versions'});
 return p;
}
function social(){
 const p=page('social',`${content.title} · 简洁宣发海报`,'social.png',1086,1448,2400,3200);
 brand(p,74,98,30);
 art(p,content.titleLines[0],79,181,91,{maxWidth:389,outlineWidth:4});
 art(p,content.titleLines[1],69,280,105,{maxWidth:403,outlineWidth:4.5});
 tx(p,'端游 / 手游 · 绝密 / 机密',76,343,21,{maxWidth:411,stroke:paper,strokeWidth:3.8,role:'versions'});
 art(p,'成功撤离，只计超额',78,441,30,{maxWidth:750,fill:ink,outlineWidth:1.4,angles:false});
 tx(p,`绝密超过 ${content.mechanism.thresholdByModeW['绝密']}w / 机密超过 ${content.mechanism.thresholdByModeW['机密']}w 的部分计入进度`,78,479,18.4,{maxWidth:754,role:'threshold-condition'});
 const cards=[{x:55,mode:540,header:580,edges:[598,648,698,750,801,853]},{x:557,mode:540,header:580,edges:[598,648,698,750,801,853]},{x:55,mode:894,header:933,edges:[951,1006,1061,1118,1177]},{x:557,mode:894,header:933,edges:[951,1006,1061,1118,1177]}];
 p.alignment=[];
 content.tiers.forEach((t,i)=>{const c=cards[i],dx=c.x-55,color=t.color==='gold'?gold:teal,rows=centers(c.edges);
  tx(p,`${t.platform} · ${t.mode}版`,c.x+237,c.mode,25,{anchor:'middle',fill:color,role:'platform-mode'});
  tx(p,'订单价格',178+dx,c.header,18,{anchor:'middle',fill:paper,role:'table-heading'});
  tx(p,'超额累计保底',442+dx,c.header,18,{anchor:'middle',fill:paper,role:'table-heading'});
  rows.forEach((cy,j)=>{const r=t.rows[j];number(p,r.priceR,'r',216+dx,cy,40,ink,38.2,'price',6);number(p,r.excessGuaranteeW,'w',471+dx,cy,42,color,40.1,'guarantee',6);});
  p.alignment.push({id:t.id,rowCenters:rows,rowEdges:c.edges,unitColumns:{priceR:216+dx,guaranteeW:471+dx},numberGap:6,noRightIcons:true});
 });
 tx(p,content.mechanism.allLootOwnership,543,1254,26,{anchor:'middle',maxWidth:792,role:'ownership'});
 tx(p,'未超过门槛不计进度 · '+content.mechanism.closeCondition,543,1290,17.2,{anchor:'middle',maxWidth:793,role:'zero-progress-close'});
 tx(p,'r = 元 / w = 万',543,1423,12,{anchor:'middle',fill:secondary,style:0,role:'units'});
 return p;
}
const allPages=[...content.tiers.map(poster),cover(),social()];
const selected=content.deliveryPageIds??allPages.map(p=>p.id);
const pages=allPages.filter(p=>selected.includes(p.id));
if(pages.length!==selected.length)throw Error('Unknown or duplicate delivery page ID.');
for(const p of pages)p.platforms=p.id.startsWith('desktop-')?['desktop']:p.id.startsWith('mobile-')?['mobile']:['desktop','mobile'];
return {version:'excess-price-r2',requests,pages};
}
