import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const project=path.dirname(fileURLToPath(import.meta.url));
const geometry=JSON.parse(fs.readFileSync(path.join(project,'02-layouts/generated-background-measurements.json'),'utf8'));
const navy='#213f53',teal='#22756b',gold='#a16f21',cream='#fff9e8';
export function makeLayout(c){
 const requests=[],requestMap=new Map();
 function req(text,font='Microsoft YaHei UI',style=0){text=String(text);const key=JSON.stringify([text,font,style]);if(requestMap.has(key))return requestMap.get(key);const id='t'+requests.length;requests.push({id,text,font,style});requestMap.set(key,id);return id;}
 const pages=geometry.pages.map(g=>{
  const ad=g.id==='combined-advert',cover=g.id==='combined-cover',mobile=g.id==='mobile-absolute',full=!ad&&!cover;
  const p={id:g.id,label:g.label,bg:g.id+'.png',w:g.w,h:g.h,ew:g.ew,eh:g.eh,platforms:g.platforms,elements:[],textGroups:[]};
  const cx=r=>r.x+r.width/2,cy=r=>r.y+r.height/2;
  function group(id,safe,container,horizontal='center'){p.textGroups.push({id,safeRect:safe,containerRect:container,horizontal});return id;}
  function text(s,x,y,size,o={}){const {bold=false,font='Microsoft YaHei UI',...rest}=o;p.elements.push({type:'text',id:req(s,font,bold?1:0),text:String(s),x,cy:y,size,fill:navy,anchor:'middle',...rest});}
  function rich(runs,x,y,size,o={}){p.elements.push({type:'rich',runs:runs.map(r=>{if(typeof r==='string')r={text:r};return {...r,text:String(r.text),id:req(r.text,r.font||'Microsoft YaHei UI',r.bold?1:0)};}),x,cy:y,size,fill:navy,anchor:'middle',group:'line'+p.elements.length,...o});}
  function art(s,x,y,size,o={}){p.elements.push({type:'art',text:s,ids:[...s].map(a=>req(a,'Noto Sans SC Black',0)),x,cy:y,size,fill:navy,shadow:'#8ba5a8',outline:cream,outlineWidth:cover?5:3.2,angles:true,anchor:'middle',...o});}
  function raw(markup){p.elements.push({type:'raw',markup});}
  const head=group('header',g.header.copy,g.header.container);
  const hx=cx(g.header.copy);
  text(c.brand,hx,cover?93:ad?96:130,cover?42:ad?34:32,{bold:true,semanticGroup:head});
  art(c.title,hx,cover?174:ad?166:192,cover?112:ad?86:72,{maxWidth:g.header.copy.width-24,semanticGroup:head});
  text(cover||ad?c.display.platformMode:(mobile?'手游':'端游')+' · '+c.mode,hx,cover?263:ad?235:260,cover?34:32,{semanticGroup:head});
  const heading=group('condition-title',g.conditions.heading,g.conditions.container);
  text(cover?c.display.typeset.objectiveHeadingCover:c.display.objectiveHeading,cx(g.conditions.heading),cy(g.conditions.heading),cover?62:ad||mobile?40:50,{bold:true,fill:teal,semanticGroup:heading});
  const cardFont=cover?42:ad?40:32;
  g.conditions.copy.forEach((r,i)=>{
   const sg=group('condition-'+i,r,g.conditions.cards[i]),x=cx(r);
   if(i<2){
    const objective=i===0?c.objectives.sandSafes:c.objectives.regularSafes;
    text(objective.name,x,r.y+22,cardFont,{semanticGroup:sg});
    rich([{text:objective.verb,size:cover?38:ad?36:30},{text:objective.count,font:'Bahnschrift',bold:true,size:cover?72:ad?70:64},{text:'个',size:cover?40:ad?38:34}],x,r.y+(cover||ad?104:92),32,{maxWidth:r.width,semanticGroup:sg,role:'objective'});
   }else{
    text(c.display.combinedLootPrefix,x,r.y+18,cover?32:cardFont,{semanticGroup:sg});
    const nums=(platform,size)=>[{text:c.objectives.loot.operator,size:cover?40:ad?34:38},{text:c.objectives.loot[platform],font:'Bahnschrift',bold:true,size},{text:c.objectives.loot.unit,font:'Bahnschrift',bold:true,size:size*.94}];
    if(full)rich(nums(mobile?'mobile':'desktop',58),x,r.y+92,32,{maxWidth:r.width,semanticGroup:sg,role:'objective'});
    else {
      for(const [index,platform]of ['desktop','mobile'].entries()){
        rich([{text:platform==='desktop'?'端游':'手游',size:cover?30:32},...nums(platform,cover?58:54)],x,r.y+(cover?61:65)+index*(cover?49:53),30,{maxWidth:r.width,semanticGroup:sg,unitAxis:'loot-threshold',role:'objective'});
      }
    }
   }
  });
  for(const z of g.conditions.conjunctions){
   const radius=ad||cover?28:22;
   raw('<circle cx="'+z.cx+'" cy="'+z.cy+'" r="'+radius+'" fill="'+teal+'" stroke="'+cream+'" stroke-width="3"/>');
   text('且',z.cx,z.cy,ad||cover?40:29,{bold:true,fill:cream,role:'conjunction'});
  }
  const result=cover?{x:220,y:651,width:638,height:58}:ad?{x:161,y:652,width:764,height:60}:{x:mobile?129:128,y:mobile?665:680,width:580,height:56};
  raw('<rect x="'+result.x+'" y="'+result.y+'" width="'+result.width+'" height="'+result.height+'" rx="'+result.height/2+'" fill="'+teal+'" stroke="'+cream+'" stroke-width="2"/>');
  const resultSafe={x:result.x+12,y:result.y+5,width:result.width-24,height:result.height-10};
  const resultGroup=group('result',resultSafe,g.conditions.container);
  text(cover?c.display.typeset.objectiveResultCover:c.display.typeset.objectiveResultFull,cx(resultSafe),cy(resultSafe),cover?36:ad?40:31,{bold:true,fill:cream,semanticGroup:resultGroup,role:'core-condition'});
  if(!cover){
   const ps=g.prices;
   const labels=ad?[c.display.priceColumnLabels[0],'端游 / '+c.mode,'手游 / '+c.mode]:c.display.priceColumnLabels;
   ps.columns.forEach((r,i)=>{const sg=group('price-heading-'+i,{x:r.x,y:r.y+6,width:r.width,height:r.height-12},ps.header);text(labels[i],cx(r),cy(r),ad?36:32,{bold:true,fill:cream,semanticGroup:sg});});
   c.prices.forEach((tier,row)=>{
    const top=ps.rowEdges[row],bottom=ps.rowEdges[row+1],label=ps.columns[0],r={x:label.x,y:top+5,width:label.width,height:bottom-top-10};
    const sg=group('price-label-'+row,r,ps.container);
    text(tier.label,cx(r),cy(r),ad?40:38,{bold:tier.type==='连续',fill:tier.type==='连续'?gold:navy,semanticGroup:sg});
    const platforms=ad?['desktop','mobile']:[mobile?'mobile':'desktop'];
    platforms.forEach((platform,i)=>{
      const col=ps.columns[i+1],safe={x:col.x,y:top+5,width:col.width,height:bottom-top-10},sg=group('price-'+platform+'-'+row,safe,ps.container,'fixed');
      const numSize=ad?55:60,unitSize=ad?52:57,fill=tier.type==='连续'?gold:navy,axis='price-axis-'+platform;
      // Renderer measures longest number, then fixes the r column for all rows.
      text(tier[platform],cx(safe),cy(safe),numSize,{font:'Bahnschrift',bold:true,anchor:'end',fill,semanticGroup:sg,lineGroup:sg,priceAxis:axis,axisKind:'number',axisRect:col,role:'price'});
      text(tier.unit,cx(safe),cy(safe),unitSize,{font:'Bahnschrift',bold:true,anchor:'start',fill,semanticGroup:sg,lineGroup:sg,priceAxis:axis,axisKind:'unit',axisRect:col,role:'price-unit'});
    });
   });
  }
  const rewardGroup=group('reward',g.reward.copy,g.reward.container);
  const rx=cx(g.reward.copy),ry=g.reward.copy.y,choices=c.wishReward.choices.join(' / ');
  if(cover){
    rich([{text:c.display.typeset.coverWishWhen},{text:choices,bold:true}],rx,ry+15,32,{maxWidth:g.reward.copy.width,semanticGroup:rewardGroup,role:'reward'});
    rich([{text:c.cover.wishTriggerShort,size:32},{text:c.wishReward.guarantee,font:'Bahnschrift',bold:true,size:52,fill:gold},{text:c.wishReward.unit,font:'Bahnschrift',bold:true,size:49,fill:gold},{text:c.display.wishGuaranteeLabel,size:32}],rx,ry+61,32,{semanticGroup:rewardGroup,role:'reward'});
  }else{
    const ys=full?(mobile?[16,53,90,135]:[16,58,106,160]):[22,70,122];
    if(full)text(c.display.rewardHeading,rx,ry+ys[0],mobile?32:36,{bold:true,fill:gold,semanticGroup:rewardGroup});
    text(c.display.wishWhen,rx,ry+(full?ys[1]:ys[0]),ad?40:30,{semanticGroup:rewardGroup});
    text(choices,rx,ry+(full?ys[2]:ys[1]),ad?40:mobile?33:34,{bold:true,semanticGroup:rewardGroup});
    const n=ad?58:mobile?50:56;
    rich([{text:c.display.wishTriggerShort,size:ad?36:30},{text:c.wishReward.guarantee,font:'Bahnschrift',bold:true,size:n,fill:gold},{text:c.wishReward.unit,font:'Bahnschrift',bold:true,size:n*.94,fill:gold},{text:c.display.wishGuaranteeLabel,size:ad?36:30}],rx,ry+(full?ys[3]:ys[2]),30,{maxWidth:g.reward.copy.width,semanticGroup:rewardGroup,role:'reward'});
  }
  if(full){
   const nn=g.notices,left=mobile?124:65;
   const title=group('notice-heading',{...nn.heading,x:left,width:nn.heading.width-(left-nn.heading.x)},nn.container,'start');
   text(c.display.noticeHeading,left,cy(nn.heading),mobile?40:36,{bold:true,anchor:'start',semanticGroup:title,role:'rule-heading'});
   const completion=c.display.typeset.noticeCompletion;
   const broken=c.notices.find(n=>n.id==='broken-order');
   const proxy=c.display.typeset.noticeSafeInteraction;
   const lines=[
    [completion],
    [[{text:c.display.typeset.noticeBrokenOrderPrefix},{text:'＜'+broken.loot+broken.unit,bold:true},{text:c.display.typeset.noticeBrokenOrderSeparator},...c.display.typeset.noticeBrokenOrderResult]],
    [proxy],
    [c.display.noticeExtraGuarantee]
   ];
   nn.groups.forEach((r,i)=>{
    const safe={...r,x:left,width:r.width-(left-r.x)},sg=group('notice-'+i,safe,nn.container,'start');
    const linePitch=31;
    lines[i].forEach((runs,j)=>rich(runs,left,cy(safe)+(j-(lines[i].length-1)/2)*linePitch,30,{anchor:'start',semanticGroup:sg,role:'rule-body',maxWidth:safe.width}));
   });
  }
  return p;
 });
 return {campaign:c.campaign,revision:c.revision,requests,pages};
}
