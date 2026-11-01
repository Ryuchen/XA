import {createHash} from 'node:crypto';

// Bank-specific adapter measured against the approved generated backgrounds.
// Five rows, four columns; business numbers come only from content.json.
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
function number(p,value,unit,unitX,cy,size,fill,unitSize=size*.95,role='number',gap=7){tx(p,value,unitX-gap,cy,size,{font:NUM,fill,anchor:'end',role:role+'-number'});tx(p,unit,unitX,cy,unitSize,{font:unit==='把'?CN:NUM,fill,role:role+'-unit'});}
function arrow(p,x,y,color,scale=1){raw(p,`<path d="M${x} ${y}h${25*scale}m${-9*scale} ${-9*scale}l${9*scale} ${9*scale}l${-9*scale} ${9*scale}" fill="none" stroke="${color}" stroke-width="${3*scale}" stroke-linecap="round" stroke-linejoin="round"/>`);}
if(content.tiers.length!==4||content.tiers.some(t=>t.rows.length!==5))throw Error('Bank template requires four categories, five tiers each');
function poster(t){
 const color=t.color==='gold'?gold:teal;
 const p=page(t.id,`${t.platform} · ${t.mode}银行物资单`,`poster-${t.color}.png`,836,1882,3200,7200);
 brand(p,46,63,24);
 art(p,content.titleLines[0],48,136,77,{maxWidth:257,outlineWidth:3.5});
 art(p,content.titleLines[1],45,225,88,{maxWidth:262,outlineWidth:4.2});
 raw(p,`<path d="M48 271Q173 279 296 267" fill="none" stroke="${t.color==='gold'?'#D8AD71':'#77C0B2'}" stroke-width="5" stroke-linecap="round"/>`);
 tx(p,`${t.platform} · ${t.mode}版`,177,305,23,{anchor:'middle',fill:paper,maxWidth:238,role:'platform-mode'});
 tx(p,'限定局数 · 超时加保底',48,354,17,{fill:secondary,maxWidth:262});
 art(p,'限局完成 · 超时加保底',70,424,30,{maxWidth:565,fill:color,outlineWidth:1.7,angles:false});
 tx(p,'未按表内局数完成，追加对应档位保底',70,455,20,{maxWidth:566,role:'late-condition'});
 tx(p,`${content.mechanism.bonusFrequency} · ${content.mechanism.closeCondition}`,70,482,13.5,{fill:secondary,style:0,maxWidth:565,role:'late-frequency'});
 for(const [x,label] of [[139,'订单价格'],[324,'带出物资'],[480,'限局完成'],[670,'超时追加保底']])tx(p,label,x,538,19.5,{anchor:'middle',fill:paper,role:'table-heading'});
 const rows=[603,682,760,838,918];
 rows.forEach((cy,i)=>{const r=t.rows[i];number(p,r.priceR,'r',202,cy,45,ink,43,'price');number(p,r.lootW,'w',374,cy,46,color,44,'loot');number(p,r.roundLimit,'把',494,cy,38,ink,24,'rounds');number(p,'+'+r.lateBonusW,'w',742,cy,46,color,44,'late-bonus');});
 tx(p,'表内物资为基础保底，超时按右列追加',49,967,10.5,{fill:secondary,style:0,role:'table-note'});
 tx(p,'r = 元 / w = 万',786,967,10.5,{anchor:'end',fill:secondary,style:0,role:'units'});
 const top=content.topUp.byPlatform[t.platform==='手游'?'mobile':'desktop'][t.mode];
 art(p,content.topUp.title,70,997,22,{maxWidth:544,fill:ink,outlineWidth:1.2,angles:false});
 tx(p,'自愿加仓',81,1025,13,{fill:secondary});tx(p,'追加保底',256,1025,13,{fill:secondary});
 number(p,top.priceR,'r',159,1058,36,color,34,'topup-price');arrow(p,190,1058,color,.8);number(p,'+'+top.additionalLootW,'w',345,1058,38,color,36,'topup-loot');
 const topLines=content.topUp.displayLines;
 topLines.forEach((s,i)=>tx(p,s,407,1034+i*18,12.6,{maxWidth:241,style:0,role:'topup-condition'}));
 art(p,'下单须知',70,1144,29,{maxWidth:200,fill:paper,shadow:'#163346',outline:ink,outlineWidth:.6,angles:false});
 tx(p,'物资归属 · 装备权益 · 补偿结算',765,1144,15,{anchor:'end',fill:'#DCEAF1',style:0,maxWidth:404});
 const bounds=[1189,1269,1332,1406,1474,1559,1635];
 content.rules[t.mode].forEach((r,i)=>{
  const cy=(bounds[i]+bounds[i+1])/2;
  tx(p,r.title,185,cy,17,{maxWidth:109,role:'rule-label'});
  if(r.compensation){r.compensation.forEach((row,j)=>{const y=cy+(j-.5)*23;tx(p,row.reason,310,y,13.8,{style:0,role:'rule-body'});tx(p,row.items[0].label,435,y,14,{role:'rule-body'});number(p,'+'+row.items[0].amountW,'w',571,y,17,color,16,'compensation',4);tx(p,row.items[1].label,626,y,14,{role:'rule-body'});number(p,'+'+row.items[1].amountW,'w',750,y,17,color,16,'compensation',4);});}
  else r.lines.forEach((s,j)=>tx(p,s,310,cy+(j-(r.lines.length-1)/2)*22,14.1,{style:0,maxWidth:463,role:'rule-body'}));
 });
 tx(p,content.featureCopy[0],418,1668,21,{anchor:'middle',maxWidth:590,stroke:paper,strokeWidth:3,role:'feature'});
 tx(p,content.featureCopy[1],418,1701,25,{anchor:'middle',maxWidth:590,stroke:paper,strokeWidth:3,role:'feature'});
 tx(p,content.footerInstruction,418,1752,13,{anchor:'middle',stroke:paper,strokeWidth:2.5,fill:ink,role:'footer'});
 p.alignment={rowCenters:rows,unitColumns:{priceR:202,lootW:374,lateBonusW:742},numberGap:7,vertical:'visible glyph center',tableColumns:[45,234,416,544,791],noTableIcons:true};
 return p;
}
function cover(){const p=page('cover','银行物资单 · 横版封面','cover.png',1672,941,3200,1800);brand(p,96,175,39);art(p,content.titleLines[0],102,325,129,{maxWidth:533,outlineWidth:6});art(p,content.titleLines[1],96,490,174,{maxWidth:545,outlineWidth:7});raw(p,'<path d="M107 602 Q353 623 618 595" fill="none" stroke="#DCAF70" stroke-width="10" stroke-linecap="round"/>');tx(p,content.subtitle,392,721,35,{anchor:'middle',fill:paper,maxWidth:526,role:'mechanism'});tx(p,'端游 / 手游 · 绝密 / 机密',393,815,27,{anchor:'middle',maxWidth:545,stroke:paper,strokeWidth:3,role:'versions'});return p;}
function social(){
 const p=page('social','银行物资单 · 简洁宣发海报','social.png',1086,1448,2400,3200);
 brand(p,59,95,30);art(p,content.titleLines[0],61,182,88,{maxWidth:372,outlineWidth:4});art(p,content.titleLines[1],56,280,103,{maxWidth:380,outlineWidth:5});
 tx(p,'端游 / 手游 · 绝密 / 机密',62,355,20.5,{maxWidth:380,stroke:paper,strokeWidth:2.5,role:'versions'});
 art(p,'限局完成 · 超时加保底',160,424,35,{maxWidth:819,fill:gold,outlineWidth:1.8,angles:false});
 tx(p,'未按表内局数完成，追加对应档位保底；保底打满结单',160,461,18.5,{maxWidth:820,role:'late-condition'});
 const order=['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret'];
 const cards=[{x:56,head:522,sub:562,rows:[609,663,715,768,822]},{x:558,head:522,sub:562,rows:[609,663,715,768,822]},{x:56,head:886,sub:926,rows:[973,1025,1078,1131,1184]},{x:558,head:886,sub:926,rows:[973,1025,1078,1131,1184]}];p.alignment=[];
 order.forEach((id,ci)=>{const t=content.tiers.find(t=>t.id===id),c=cards[ci],dx=c.x-56,color=t.color==='gold'?gold:teal;
  tx(p,`${t.platform} · ${t.mode}版`,c.x+236,c.head,26,{anchor:'middle',fill:color,role:'platform-mode'});
  for(const[x,s]of[[115,'价格'],[237,'带出物资'],[345,'限局'],[462,'超时追加']])tx(p,s,x+dx,c.sub,17,{anchor:'middle',fill:paper,role:'table-heading'});
  c.rows.forEach((cy,j)=>{const r=t.rows[j];number(p,r.priceR,'r',152+dx,cy,30,ink,28.5,'price',5);number(p,r.lootW,'w',262+dx,cy,31,color,29.5,'loot',5);number(p,r.roundLimit,'把',361+dx,cy,28,ink,17,'rounds',5);number(p,'+'+r.lateBonusW,'w',493+dx,cy,30,color,28.5,'late-bonus',5);});
  p.alignment.push({id,unitColumns:{priceR:152+dx,lootW:262+dx,lateBonusW:493+dx},rowCenters:c.rows,tableColumns:[56+dx,176+dx,296+dx,394+dx,528+dx],noTableIcons:true});
 });
 tx(p,`${content.mechanism.bonusFrequency} · ${content.mechanism.closeCondition}`,544,1273,18,{anchor:'middle',maxWidth:674,role:'late-frequency'});
 tx(p,'r = 元 / w = 万',544,1298,13,{anchor:'middle',fill:secondary,style:0,role:'units'});
 return p;
}
const pages=[...content.tiers.filter(t=>t.platform==='手游').map(poster),social()];
for(const p of pages)p.platforms=p.id==='social'?['desktop','mobile']:['mobile'];
return {version:'bank-mobile-price-20261009',requests,pages};
}
