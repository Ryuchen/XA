import {createHash} from 'node:crypto';

// Approved material-series r2 adapter. Business values come from content.json.
// Copy this file into each campaign before adapting its layout.
export function makeLayout(content){
  for(const value of [content.brand,content.title,content.columns?.price,content.columns?.loot,content.offer?.when,content.offer?.scope,content.offer?.increase]){
    if(typeof value!=='string'||!value.trim())throw new Error('缺少品牌、标题、列名或优惠字段；不同信息结构应调整项目适配器。');
  }
  if(content.process?.length!==3)throw new Error('本例底部流程为三步；不同数量应调整布局。');
  if(content.tiers?.length!==4)throw new Error('此适配器仅对应四类价目；其他玩法请调整项目适配器/底图。');
  if(content.titleLines?.length!==2)throw new Error('本竖版标题需要两行 titleLines；长标题请调整标题安全区。');
  if(new Set(content.tiers.map(t=>t.id)).size!==4)throw new Error('页面 id 必须唯一');
  for(const t of content.tiers){
    if(!['gold','teal'].includes(t.color))throw new Error('本例底图色系为 gold/teal');
    if(t.prices?.length!==6||t.loot?.length!==6)throw new Error('每页需要六档；更改行数需要适配底图与布局');
    if([...t.prices,...t.loot].some(v=>!Number.isFinite(v)||v<0))throw new Error('价格/物资必须为非负数值');
    const groups=content.rules[t.mode];
    if(groups?.length!==6)throw new Error('本例为六组规则；其他结构需要调整布局');
    for(const r of groups){
      if(r.compensation){
        if(r.compensation.length!==2||r.compensation.some(row=>row.items?.length!==2))throw new Error('本例补偿区采用两行两列');
      }else if(!r.lines?.length||r.lines.length>3)throw new Error('规则正文应为1–3行；多于3行需要增加区域高度');
    }
    if(!Number.isFinite(content.guarantee.lootThreshold[t.mode]))throw new Error('缺少该模式保障门槛');
  }
const ink='#213F53', secondary='#567486', gold='#A96620', teal='#187C78', white='#FFFDF7';
const CN='Microsoft YaHei UI', NUM='Bahnschrift', ART='Noto Sans SC Black';
const requests=[], ids=new Map();
function fontRequest(text,font=CN,style=1){
  const key=JSON.stringify([String(text),font,style]);
  if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text:String(text),font,style});}
  return ids.get(key);
}
function page(id,label,bg,w,h,ew,eh){return {id,label,bg,w,h,ew,eh,elements:[]};}
function tx(p,text,x,cy,size=16,{font=CN,style=1,fill=ink,anchor='start',maxWidth,role='copy',stroke,strokeWidth}={}){
  if(text===undefined||text===null)throw new Error('缺少文案字段，请检查 content.json');
  if(!String(text).trim())return;
  p.elements.push({type:'text',id:fontRequest(text,font,style),text:String(text),x,cy,size,fill,anchor,maxWidth,role,stroke,strokeWidth});
}
function art(p,text,x,cy,size,{maxWidth,fill=ink,shadow='#A4BFD5',outline=white,outlineWidth=3.3,angles=true}={}){
  p.elements.push({type:'art',text,x,cy,size,maxWidth,fill,shadow,outline,outlineWidth,angles,ids:[...text].map(c=>fontRequest(c,ART,0))});
}
function raw(p,markup){p.elements.push({type:'raw',markup});}
function brand(p,x,cy,size=22){
  const k=size/28;
  raw(p,`<g transform="translate(${x} ${cy-size*.52}) scale(${k})" fill="none" stroke="${ink}" stroke-width="2.1" stroke-linejoin="round"><path d="M0 27 L15 0 L31 27 Z M8 22 L15 10 L23 22 Z"/></g>`);
  tx(p,content.brand,x+size*1.35,cy,size,{maxWidth:size*4.1,role:'brand'});
}
function number(p,value,unit,unitX,cy,size,fill,unitSize,role){
  // The unit's LEFT ink edge is a shared vertical alignment axis.
  // Number ink is right-aligned with a fixed gap; the renderer shares a baseline
  // for both pieces and then centers their combined visible ink on this row.
  tx(p,value,unitX-9,cy,size,{font:NUM,fill,anchor:'end',role:role+'-number'});
  tx(p,unit,unitX,cy,unitSize,{font:NUM,fill,role:role+'-unit'});
}
function offer(p,{x,y,w,h,color=gold,large=false}){
  if(large){
    art(p,content.offer.when+'专享',x+25,y+26,24,{maxWidth:180,fill:ink,outlineWidth:1.5,angles:false});
    tx(p,content.offer.scope,x+25,y+69,35,{fill:ink});
    tx(p,content.offer.increase,x+395,y+57,84,{font:NUM,fill:color,maxWidth:310,role:'offer'});
    tx(p,content.offer.exception,x+25,y+h-15,15,{fill:secondary,style:0});
  }else{
    art(p,content.offer.when+'专享',x+24,y+27,23,{maxWidth:172,fill:ink,outlineWidth:1.4,angles:false});
    tx(p,content.offer.scope,x+24,y+67,28,{fill:ink});
    tx(p,content.offer.increase,x+295,y+60,74,{font:NUM,fill:color,maxWidth:216,role:'offer'});
    tx(p,content.offer.exception,x+24,y+h-16,13,{fill:secondary,style:0});
  }
}
function poster(t){
  const color=t.color==='gold'?gold:teal;
  const p=page(t.id,`${t.platform} · ${t.mode}`,`poster-${t.color}.png`,809,1942,3200,7680);
  p.platforms=[t.platform==='手游'?'mobile':'desktop'];
  // A single vector navy header replaces the uneven two-color raster header.
  raw(p,`<defs><linearGradient id="price-header-navy" x1="0" y1="0" x2="1" y2=".55"><stop offset="0" stop-color="#1D4056"/><stop offset="1" stop-color="#294B60"/></linearGradient></defs><path data-role="price-header-background" d="M54 528.5 H755 Q768.5 528.5 768.5 542 V582 H40.5 V542 Q40.5 528.5 54 528.5 Z" fill="url(#price-header-navy)" stroke="#24475D" stroke-width=".6"/>`);
  brand(p,43,89,25);
  art(p,content.titleLines[0],47,153,55,{maxWidth:257,outlineWidth:3.8});
  art(p,content.titleLines[1],45,220,76,{maxWidth:258,outlineWidth:4.8});
  raw(p,'<path d="M48 258 Q157 264 270 254" stroke="#EDBC79" stroke-width="6" opacity=".72" fill="none" stroke-linecap="round"/>');
  tx(p,`${t.platform} · ${t.mode}版`,159,283,22,{fill:white,anchor:'middle',maxWidth:207,role:'platform-mode'});
  tx(p,content.columns.loot,153,332,17,{fill:secondary,anchor:'middle'});
  offer(p,{x:41,y:391,w:727,h:119,color});
  tx(p,content.columns.price,206,556,21,{fill:white,anchor:'middle'});
  tx(p,content.columns.loot,549,556,21,{fill:white,anchor:'middle'});
  const rows=[621,699.5,778.5,857.5,936.5,1017.5];
  rows.forEach((cy,i)=>{
    tx(p,String(i+1).padStart(2,'0'),67,cy,13,{font:NUM,fill:'#87A0AF'});
    number(p,t.prices[i],'r',260,cy,44,ink,42,'price');
    number(p,t.loot[i],'w',604,cy,50,color,48,'loot');
  });
  tx(p,content.tableNote,52,1070,10.8,{fill:secondary,style:0,maxWidth:536,role:'table-note'});
  tx(p,'r = 元 / w = 万',756,1070,10.8,{fill:secondary,anchor:'end',style:0,role:'units'});
  art(p,content.guarantee.title,201,1101,22,{maxWidth:149,fill:ink,outlineWidth:1.4,angles:false});
  tx(p,content.guarantee.prerequisite,743,1101,13,{fill:secondary,anchor:'end'});
  tx(p,content.guarantee.leftCondition,305,1143,15.5,{anchor:'middle',maxWidth:195});
  tx(p,content.guarantee.operator,435,1143,21,{fill:color,anchor:'middle'});
  tx(p,`${content.guarantee.lootLabel} ${content.guarantee.lootComparison}${content.guarantee.lootThreshold[t.mode]}w`,596,1143,16,{fill:color,anchor:'middle',maxWidth:238});
  tx(p,content.guarantee.effect,201,1189,21.4,{maxWidth:546,role:'guarantee-result'});
  tx(p,content.guarantee.explanation,201,1220,12.7,{fill:secondary,style:0});
  art(p,content.rulesTitle,49,1256,21,{maxWidth:145,outlineWidth:1.2,angles:false});
  tx(p,content.rulesSubtitle,759,1256,11.5,{fill:secondary,anchor:'end',style:0});
  const boundaries=[1272,1345,1410,1475,1542,1607,1685];
  content.rules[t.mode].forEach((r,i)=>{
    const cy=(boundaries[i]+boundaries[i+1])/2;
    tx(p,r.title,155,cy,15.5,{maxWidth:87,role:'rule-label'});
    const lineHeight=18.5;
    if(r.compensation){
      r.compensation.forEach((row,j)=>{
        const lineCy=cy+(j-.5)*lineHeight;
        tx(p,row.reason,256,lineCy,13.4,{style:0,role:'rule-body'});
        tx(p,row.items[0].label,362,lineCy,13.4,{style:0,role:'rule-body'});
        number(p,'+'+row.items[0].amount,'w',485,lineCy,16,color,12,'compensation');
        tx(p,row.items[1].label,543,lineCy,13.4,{style:0,role:'rule-body'});
        number(p,'+'+row.items[1].amount,'w',714,lineCy,16,color,12,'compensation');
      });
    }else{
      r.lines.forEach((line,j)=>tx(p,line,256,cy+(j-(r.lines.length-1)/2)*lineHeight,13.4,{style:0,maxWidth:491,fill:ink,role:'rule-body'}));
    }
  });
  [145,400,650].forEach((cx,i)=>tx(p,`${String(i+1).padStart(2,'0')}  ${content.process[i]}`,cx,1733,12.5,{anchor:'middle',maxWidth:198,fill:ink}));
  tx(p,`${content.brand}  ·  ${t.platform} · ${t.mode}版  |  ${content.footerInstruction}`,405,1780,11.8,{anchor:'middle',fill:ink,stroke:white,strokeWidth:2});
  p.alignment={priceUnitX:260,lootUnitX:604,priceUnitSize:42,lootUnitSize:48,lootRightEdge:639.02,iconSafeGapAtLeast:35,rowCenters:rows,unitAlignment:'left ink edge',verticalAlignment:'shared font baseline, then whole-line ink center / multiline group center'};
  return p;
}
function cover(){
  const p=page('cover','横版封面','cover.png',1672,941,3200,1800);
  p.platforms=[...new Set(content.tiers.map(t=>t.platform==='手游'?'mobile':'desktop'))];
  brand(p,95,162,38);
  art(p,content.title,87,348,125,{maxWidth:645,outlineWidth:7});
  raw(p,'<path d="M105 432 Q366 461 689 433" fill="none" stroke="#E4B474" stroke-width="11" stroke-linecap="round"/>');
  tx(p,content.columns.loot,96,497,39,{fill:ink});
  tx(p,content.coverVersions,366,578,31,{fill:white,anchor:'middle',maxWidth:511});
  tx(p,content.offer.when+'专享',99,670,26,{fill:ink});
  tx(p,content.offer.scope+' '+content.offer.increase,302,670,30,{fill:gold,maxWidth:414});
  tx(p,content.offer.exception,302,699,17,{fill:secondary,style:0});
  return p;
}
function social(){
  const p=page('social','简洁广告海报','social.png',1086,1448,2400,3200);
  p.platforms=[...new Set(content.tiers.map(t=>t.platform==='手游'?'mobile':'desktop'))];
  brand(p,52,91,30);
  art(p,content.title,46,170,73,{maxWidth:406,outlineWidth:5});
  raw(p,'<path d="M53 222 Q251 236 437 217" fill="none" stroke="#E9B578" stroke-width="7" stroke-linecap="round"/>');
  tx(p,content.advertisingSubtitle,225,263,23,{fill:ink,maxWidth:256,stroke:white,strokeWidth:3});
  offer(p,{x:46,y:366,w:994,h:112,color:gold,large:true});
  const cards=[{x:49,head:527,sub:572,rows:[610.5,653.5,698.5,743,787,832.5]},
    {x:559,head:527,sub:572,rows:[610.5,653.5,698.5,743,787,832.5]},
    {x:49,head:903,sub:947,rows:[987,1030,1074.5,1119.5,1164.5,1209.5]},
    {x:559,head:903,sub:947,rows:[987,1030,1074.5,1119.5,1164.5,1209.5]}];
  p.alignment=[];
  content.tiers.forEach((t,i)=>{
    const c=cards[i],color=t.color==='gold'?gold:teal;
    tx(p,`${t.platform} · ${t.mode}版`,c.x+286,c.head,27,{fill:white,anchor:'middle',maxWidth:337,role:'platform-mode'});
    tx(p,content.columns.price,c.x+115,c.sub,16,{fill:ink,anchor:'middle'});
    tx(p,content.columns.loot,c.x+344,c.sub,16,{fill:color,anchor:'middle'});
    c.rows.forEach((cy,j)=>{
      number(p,t.prices[j],'r',c.x+150,cy,33,ink,30,'price');
      number(p,t.loot[j],'w',c.x+390,cy,36,color,34,'loot');
      raw(p,`<path d="M${c.x+223} ${cy-3} l4 3 -4 3" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity=".65"/>`);
    });
    p.alignment.push({card:t.id,priceUnitX:c.x+150,lootUnitX:c.x+390,priceUnitSize:30,lootUnitSize:34,rowCenters:c.rows});
  });
  tx(p,content.advertisingNote,544,1264,17,{anchor:'middle',fill:ink,style:0,stroke:white,strokeWidth:2.5});
  tx(p,'r = 元   /   w = 万',544,1298,15,{anchor:'middle',fill:secondary,style:0,stroke:white,strokeWidth:2.5});
  return p;
}
const pages=[...content.tiers.map(poster),cover(),social()];

return {requests,pages};
}
