import {createHash} from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const project=path.dirname(fileURLToPath(import.meta.url));

export function makeLayout(content){
 const ink='#213F53',gold='#A96D26',cream='#FFFCF4',secondary='#746548';
 const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
 const requests=[],seen=new Map();
 const glyphCache=JSON.parse(fs.readFileSync(path.join(project,'assets/typography/cover-centering.json'),'utf8'));
 const glyphWidth=(text,font,style,size)=>{
  const key=createHash('sha256').update(JSON.stringify([text,font,style])).digest('hex');
  const glyph=glyphCache[key];
  if(!glyph)throw Error('Missing approved glyph metrics for centered cover: '+text);
  return glyph.width*size/100;
 };
 const propMetadata=JSON.parse(fs.readFileSync(path.join(project,'01-illustrations/prop-metadata.json'),'utf8')).items;
 const state=JSON.parse(fs.readFileSync(path.join(project,'campaign-state.json'),'utf8').replace(/^\uFEFF/,''));
 if(state.approvals.decorativeProps?.status!=='approved')throw Error('Confirm the new decorative prop illustrations before composing production posters.');
 function req(text,font=CN,style=0){
  const key=JSON.stringify([String(text),font,style]);
  if(!seen.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);seen.set(key,id);requests.push({id,text:String(text),font,style});}
  return seen.get(key);
 }
 function page(id,label,bg,w,h,ew,eh){return {id,label,bg,w,h,ew,eh,elements:[]};}
 function tx(p,text,x,cy,size,{font=CN,style=0,fill=ink,anchor='start',maxWidth,role='copy'}={}){
  p.elements.push({type:'text',id:req(text,font,style),text:String(text),x,cy,size,font,fill,anchor,maxWidth,role});
 }
 function raw(p,markup){p.elements.push({type:'raw',markup});}
 function prop(p,id,x,y,w,h,role='decorative-prop'){
  const m=propMetadata.find(item=>item.id===id);
  if(!m)throw Error('Missing prop: '+id);
  const scale=Math.min(w/m.width,h/m.height),width=m.width*scale,height=m.height*scale;
  const bx=x+(w-width)/2,by=y+(h-height)/2;
  const png=fs.readFileSync(path.join(project,m.file)).toString('base64');
  raw(p,`<g data-prop="${id}" data-role="${role}"><image href="data:image/png;base64,${png}" x="${bx}" y="${by}" width="${width}" height="${height}"/></g>`);
  (p.illustrationBounds??=[]).push({id,role,x:bx,y:by,width,height,source:m.file,nativeWidth:m.width,nativeHeight:m.height});
 }
 function art(p,text,x,cy,size,maxWidth){p.elements.push({type:'art',text,x,cy,size,maxWidth,fill:ink,shadow:'#AFC4D4',outline:cream,outlineWidth:3.1,angles:true,ids:[...text].map(c=>req(c,ART,0))});}
 function num(p,value,unit,unitX,cy,size,{fill=ink,unitSize=size*.96,role='number',gap=11}={}){
  tx(p,value,unitX-gap,cy,size,{font:NUM,style:1,fill,anchor:'end',role:role+'-number'});
  tx(p,unit,unitX,cy,unitSize,{font:NUM,style:1,fill,role:role+'-unit'});
 }
 function brand(p,x,cy,size){
  const k=size/28;raw(p,`<g transform="translate(${x} ${cy-size*.53}) scale(${k})" fill="none" stroke="${ink}" stroke-width="2.1" stroke-linejoin="round"><path d="M0 27L15 0L31 27Z M8 22L15 10L23 22Z"/></g>`);
  tx(p,content.brand,x+size*1.34,cy,size,{style:1,role:'brand'});
 }
 function heading(p,text,cy){tx(p,text,405,cy,31,{style:1,fill:cream,anchor:'middle',role:'section-title'});}
 if(content.mode!=='绝密'||content.tiers.length!==2||content.tiers.some(t=>t.rows.length!==2))throw Error('This layout is for two platforms with two absolute-mode packages each.');
 const reward=content.rewards;
 if(reward.phantoms.confirmationStatus==='confirmed'&&!['order-total-once','each-type-once'].includes(reward.phantoms.frequencyScope))throw Error('Confirmed phantom reward frequency must include its scope.');
 if(reward.red.sameItemStacks!==false)throw Error('This layout requires the confirmed non-stacking red reward rule.');
 const phantomScope=reward.phantoms.confirmationStatus!=='confirmed'?null:reward.phantoms.frequencyScope==='order-total-once'?'三幻神特别奖励：整单合计只计算一次':'三幻神特别奖励：每种各计算一次';

 function detailed(t){
  const p=page(t.id,`${content.brand} · ${content.title} · ${t.platform} · ${content.mode}`,'poster-tazhu-sand-v1.png',809,2740,3200,10838);
  brand(p,43,78,23);
  art(p,content.titleLines[0],47,137,83,222);
  art(p,content.titleLines[1],47,215,83,222);
  tx(p,`${t.platform} · ${content.mode}`,159,283,24,{style:1,fill:cream,anchor:'middle',maxWidth:216,role:'platform-mode'});
  const cards=[{y:420,amount:458,rows:[552,616,680]},{y:768,amount:806,rows:[894,958,1022,1086]}];
  t.rows.forEach((r,i)=>{
   const c=cards[i];
   num(p,r.priceR,'r',283,c.amount,64,{fill:cream,unitSize:61.5,role:'price'});
   tx(p,'保底',459,c.amount,27,{fill:'#F3D39A',style:1,role:'loot-label'});
   num(p,r.lootW,'w',681,c.amount,64,{fill:'#F4D391',unitSize:61.5,role:'loot'});
   const items=[...r.cards.map(x=>({action:x.action,count:x.count,unit:'张',name:x.name})),...r.safes.map(x=>({action:'开启',count:x.count,unit:'次',name:x.name}))];
   if(items.length!==c.rows.length)throw Error('Entitlement count does not match the approved card structure.');
   items.forEach((item,j)=>{
    const cy=c.rows[j];
    const icon=item.unit==='张'?'keycards':item.name==='沙色保险'?'sand-safe-closed':'ordinary-safe';
    prop(p,icon,633,cy-32.5,106,65,'package-entitlement-prop');
    tx(p,item.action,82,cy,30,{role:'entitlement-action'});
    tx(p,item.count,198,cy,36,{font:NUM,style:1,fill:gold,anchor:'end',role:'entitlement-count'});
    tx(p,item.unit,212,cy,29,{role:'entitlement-unit'});
    tx(p,item.name,263,cy,31,{maxWidth:458,role:'entitlement-item'});
   });
  });
  heading(p,'特别奖励 · 加保底',1213.5);
  reward.phantoms.items.forEach((item,i)=>{
   const cy=[1320,1440,1560][i];
   prop(p,['tianyuan-difang','wanjin-leiguan','zongheng'][i],76,cy-54,126,108,'phantom-collectible');
   tx(p,reward.phantoms.trigger+item.name,230,cy,32,{maxWidth:279,role:'phantom-trigger'});
   num(p,'+'+item.bonusW,'w',679,cy,49,{unitSize:47.1,fill:gold,role:'phantom-bonus'});
  });
  if(phantomScope)tx(p,phantomScope,405,1648,29,{anchor:'middle',maxWidth:666,role:'phantom-frequency'});
  tx(p,'成功带出一个红物资',405,1701,31,{style:1,anchor:'middle',role:'red-reward-trigger'});
  reward.red.tiers.forEach((tier,i)=>{
   const cy=[1768,1835][i];
   tx(p,`${tier.minSlots}格及以上`,88,cy,31,{role:'red-slots'});
   num(p,'+'+tier.bonusW,'w',679,cy,47,{unitSize:45.1,fill:gold,role:'red-bonus'});
  });
  tx(p,'同一红物资按对应档位奖励，不叠加',405,1907,29,{anchor:'middle',maxWidth:666,role:'red-nonstacking'});

  heading(p,'下单须知',2011.5);
  tx(p,'① 有效次数',68,2075,31,{style:1,role:'rule-title'});
  tx(p,'仅成功撤离的对局计次；撤离失败时，',68,2120,29,{maxWidth:674,role:'rule-body'});
  tx(p,'刷卡、沙保和普通大保险均不计次数。',68,2157,29,{maxWidth:674,role:'rule-body'});
  tx(p,'② 累计保底｜成功撤离后',68,2214,31,{style:1,role:'rule-title'});
  const rule=content.rules.totalGuarantee;
  tx(p,`击杀${rule.killsComparison}${rule.kills}人  ${rule.operator}  撤离收益${rule.lootComparison}${rule.lootW}w`,405,2265,32,{style:1,fill:gold,anchor:'middle',maxWidth:674,role:'guarantee-condition'});
  tx(p,'达标才计入总保底；否则物资白吃，',68,2313,29,{maxWidth:674,role:'rule-body'});
  tx(p,'但已开的卡、沙保及普通大保险仍计次数。',68,2357,29,{maxWidth:674,role:'rule-body'});
  prop(p,'mouse-shield',626,2409,113,123,'rule-mascot');
  tx(p,'③ 红物资奖励',68,2431,31,{style:1,role:'rule-title'});
  tx(p,'红只要成功带出就加保底，',68,2474,29,{maxWidth:542,role:'rule-body'});
  tx(p,'无论撤离成功或失败。',68,2515,29,{maxWidth:542,role:'rule-body'});
  raw(p,'<rect x="280" y="2570" width="250" height="40" rx="20" fill="#FFF5DF" stroke="#C8AE7C" stroke-width="1"/>');
  tx(p,'r = 元 / w = 万',405,2590,23,{fill:secondary,anchor:'middle',role:'units'});
  p.alignment={priceUnitX:283,lootUnitX:681,rewardUnitX:679,entitlementCountRightX:198,entitlementUnitX:212,minimumBodySize:29,phoneWidth:390,copySafeBounds:[68,2060,674,470],bodyStyle:0,emphasisStyle:1,propsAreSeparateEmbeddedPNGs:true};
  return p;
 }
 function social(){
  const p=page('social',`${content.brand} · ${content.title} · 端游 / 手游 · 绝密四档价目`,'social-tazhu-sand-v1.png',1086,1448,2400,3200);
  brand(p,59,101,31);
  art(p,content.titleLines[0],64,184,111,298);
  art(p,content.titleLines[1],64,289,111,298);
  tx(p,`端游 / 手游 · ${content.mode}`,213,379,31,{style:1,fill:cream,anchor:'middle',maxWidth:295,role:'platform-mode'});
  const cards=[{x:54,y:554,platform:content.tiers[0],row:0},{x:557,y:554,platform:content.tiers[0],row:1},{x:54,y:867,platform:content.tiers[1],row:0},{x:557,y:867,platform:content.tiers[1],row:1}];
  cards.forEach(({x,y,platform,row})=>{
   const r=platform.rows[row];
   tx(p,`${platform.platform} · ${content.mode}`,x+237.5,y+29.5,29,{style:1,fill:cream,anchor:'middle',role:'platform-mode'});
   prop(p,row===0?'sand-safe-closed':'sand-safe-open',x+342,y+86,122,176,'social-package-safe');
   num(p,r.priceR,'r',x+257,y+100,80,{unitSize:76.8,role:'price'});
   tx(p,'累计物资保底',x+195.5,y+165,28,{anchor:'middle',role:'loot-label'});
   num(p,r.lootW,'w',x+254,y+224,68,{fill:gold,unitSize:65.3,role:'loot'});
  });
  ['tianyuan-difang','wanjin-leiguan','zongheng'].forEach((id,i)=>prop(p,id,[68,179,293][i],1207,102,91,'social-phantom-decoration'));
  tx(p,'刷卡 · 开保 · 物资保底',727,1252,34,{style:1,anchor:'middle',maxWidth:558,role:'service-summary'});
  raw(p,'<rect x="404" y="1326" width="278" height="48" rx="24" fill="#FFF5DF" stroke="#C8AE7C" stroke-width="1"/>');
  tx(p,'r = 元 / w = 万',543,1350,27,{anchor:'middle',fill:secondary,role:'units'});
  p.alignment={cards:cards.map(c=>({x:c.x,y:c.y,priceUnitX:c.x+257,lootUnitX:c.x+254,textCenterX:c.x+195.5})),rulesOmitted:true,fullRulesPreservedInDetailed:true};
  return p;
 }
 function cover(){
  const p=page('cover',`${content.brand} · ${content.title} · 黄沙版封面`,'cover-tazhu-sand-v1.png',1672,941,3200,1800);
  const cx=326,size=42;
  const brandWidth=size*1.34+glyphWidth(content.brand,CN,1,size);
  brand(p,cx-brandWidth/2,132,size);
  for(const [i,line] of content.titleLines.entries()){
   const natural=[...line].reduce((sum,char)=>sum+glyphWidth(char,ART,0,1),0)+.065*(line.length-1);
   const titleSize=Math.min(164,490/natural),width=natural*titleSize;
   art(p,line,cx-width/2,[274,434][i],164,490);
  }
  tx(p,`端游 / 手游 · ${content.mode}`,cx,585,52,{style:1,fill:cream,anchor:'middle',maxWidth:460,role:'cover-platform'});
  raw(p,'<rect x="125" y="636" width="402" height="139" rx="20" fill="#FFF3D6" stroke="#C8AE7C" stroke-width="1.8"/>');
  tx(p,'刷卡 · 开保',cx,675,52,{style:1,anchor:'middle',maxWidth:372,role:'cover-service'});
  tx(p,'累计物资保底',cx,735,52,{style:1,fill:gold,anchor:'middle',maxWidth:372,role:'cover-guarantee'});
  ['tianyuan-difang','wanjin-leiguan','zongheng'].forEach((id,i)=>prop(p,id,[97,258,419][i],793,138,124,'cover-phantom-decoration'));
  prop(p,'sand-safe-open',1441,793,141,128,'cover-sand-safe');
  p.alignment={coverTextCenterX:cx,copySafeBounds:[80,85,492,677],brandGroupWidth:brandWidth,titleCenterX:cx,platformCenterX:cx,serviceCenterX:cx,rulesOmitted:true,pricesOmitted:true,fullRulesPreservedInDetailed:true,heroSource:'01-illustrations/tazhu-sand-background-v1.png',heroNativeCrop:[0,0,809,390]};
  return p;
 }
 return {version:'tazhu-v4-sand-with-cover',requests,pages:[cover(),...content.tiers.map(detailed),social()]};
}
