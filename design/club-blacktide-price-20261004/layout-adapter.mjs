import {createHash} from 'node:crypto';

export function makeLayout(c){
 const ink='#243f55', muted='#647d91', accent='#8170b1', soft='#f3f0fa', blue='#e9f2f8', white='#fffefa';
 const CN='Microsoft YaHei UI', NUM='Bahnschrift', ART='Noto Sans SC Black';
 const requests=[],ids=new Map(),regions=[];
 const p={id:'blacktide',label:'兴安电竞 · 黑潮爆破陪玩价目表 · 双倍宽版',bg:'blacktide.png',w:2160,h:3430,ew:4800,eh:7622,platforms:['desktop'],elements:[]};
 let container='hero';
 function req(text,font=CN,style=0){const key=JSON.stringify([String(text),font,style]);if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text:String(text),font,style});}return ids.get(key);}
 const raw=markup=>p.elements.push({type:'raw',markup});
 function tx(text,x,cy,size=44,o={}){if(text===null||text===undefined)throw Error('Missing text');const {font=CN,style=0,fill=ink,anchor='start',role='copy'}=o;p.elements.push({type:'text',id:req(text,font,style),text:String(text),x,cy,size,fill,anchor,role,group:container});}
 function rich(runs,x,cy,size=44,o={}){p.elements.push({type:'rich',runs:runs.map(r=>{if(typeof r==='string')r={text:r};return {...r,id:req(r.text,r.font||CN,r.bold?1:0),fill:r.fill||ink};}),x,cy,size,anchor:o.anchor||'start',role:o.role||'rule-body',group:container+'-line-'+p.elements.length});}
 function rect(x,y,w,h,fill=white,stroke='#c9d5e0',r=22){raw(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`);}
 const line=(x1,y,x2,color='#dce4ed')=>raw(`<path d="M${x1} ${y}H${x2}" fill="none" stroke="${color}" stroke-width="2"/>`);
 function region(id,x,y,w,h,padX=24,padY=20){regions.push({page:p.id,containerId:id,x,y,w,h,safe:{x:x+padX,y:y+padY,w:w-2*padX,h:h-2*padY}});container=id;}
 function box(id,x,y,w,h,fill=white,stroke='#c9d5e0'){rect(x,y,w,h,fill,stroke);region(id,x,y,w,h);}
 function heading(id,n,title,y,dx=0,sub){region(id,54+dx,y-46,972,84,0,0);rect(54+dx,y-39,58,67,ink,ink,12);tx(n,83+dx,y-5,34,{font:NUM,style:1,fill:white,anchor:'middle'});tx(title,132+dx,y-5,54,{style:1});if(sub)tx(sub,1025+dx,y-5,36,{anchor:'end',fill:muted});}
 function art(text,x,cy,size,maxWidth){p.elements.push({type:'art',text,ids:[...text].map(t=>req(t,ART,0)),x,cy,size,maxWidth,anchor:'middle',fill:ink,shadow:'#b7c6e0',outline:white,outlineWidth:5.4,angles:true});}

 region('hero',68,50,1140,490,12,12);
 rect(427,66,438,61,'#fff','#b9cbd9',30);tx('XINGAN CLUB',646,96,40,{font:NUM,style:1,anchor:'middle',fill:muted});
 tx(c.brand,646,174,66,{style:1,anchor:'middle'});
 art(c.title,646,304,166,1100);
 tx(c.copy.pricePage,646,432,66,{style:1,anchor:'middle'});
 tx(c.game+' · DELTA FORCE',646,506,40,{anchor:'middle',fill:muted});

 function table(id,values,y,dx,hourly){
  const x=54+dx,w=972,rw=168,cw=(w-rw)/6,hh=88,rh=98,h=hh+6*rh;
  rect(x,y,w,h,'#fff','#b8cbd9',20);
  raw(`<path d="M${x+20} ${y}H${x+w-20}Q${x+w} ${y} ${x+w} ${y+20}V${y+hh}H${x}V${y+20}Q${x} ${y} ${x+20} ${y}Z" fill="${ink}"/>`);
  region(id+'-header',x,y,w,hh,12,12);
  tx('段位',x+rw/2,y+hh/2,40,{style:1,fill:white,anchor:'middle'});
  c.tierNames.forEach((t,j)=>tx(t,x+rw+(j+.5)*cw,y+hh/2,40,{style:1,fill:white,anchor:'middle'}));
  values.forEach((row,i)=>{
   const top=y+hh+i*rh,cy=top+rh/2;
   raw(`<rect x="${x+2}" y="${top}" width="${w-4}" height="${rh}" fill="${i%2===0?blue:'#ffffff'}" opacity=".66"/>`);
   region(id+'-row-'+i,x+2,top,w-4,rh,12,8);
   if(i===0){tx('下三',x+rw/2,cy-(hourly?20:0),38,{style:1,anchor:'middle'});if(hourly)tx('／匹配',x+rw/2,cy+23,30,{anchor:'middle',fill:muted});}
   else tx(c.rankNames[i],x+rw/2,cy,38,{style:1,anchor:'middle'});
   row.forEach((v,j)=>{const cx=x+rw+(j+.5)*cw;if(v===null)tx(c.prices.unavailableLabel,cx,cy,44,{fill:'#9eb0bd',anchor:'middle'});else {const ux=cx+29;tx(v,ux-8,cy,44,{font:NUM,style:1,fill:j===0?muted:ink,anchor:'end',role:'price-number'});tx('r',ux,cy,30,{font:NUM,fill:accent,role:'price-unit'});}});
   if(i<5)line(x+17,top+rh,x+w-17,'#dfe7ef');
  });
  for(let j=0;j<6;j++)raw(`<path d="M${x+rw+j*cw} ${y+hh}V${y+h-10}" stroke="#d7e2ec" stroke-width="1.5"/>`);
 }
 heading('heading-hourly','01',c.copy.hourlyTitle,618,0,c.copy.hourlyCaption);
 heading('heading-rounds','02',c.copy.roundsTitle,618,1080,c.copy.roundsCaption);
 table('hourly',c.prices.hourly.values,677,0,true);
 table('rounds',c.prices.rounds.values,677,1080,false);
 rect(54,1375,2052,75,soft,'#d8d1e9');region('price-note',54,1375,2052,75,24,14);
 rich([{text:c.compensation.exclusions.map(t=>t.replace('无赔付','')).join('／')+'无赔付',bold:true,fill:accent,size:44},{text:'  ·  ',fill:muted,size:38},{text:c.copy.unavailable,size:38,fill:muted}],1080,1412.5,44,{anchor:'middle',role:'table-note'});

 heading('heading-compensation','03','赔付标准',1520);
 function partyCard(label,y,h,lines){
  box('compensation-'+label,54,y,972,h);
  rect(74,y+20,124,h-40,soft,'#e0daed',14);tx(label,136,y+h/2,43,{style:1,anchor:'middle',fill:accent});
  lines.forEach(l=>{if(l.runs)rich(l.runs,229,y+l.dy,l.size||44);else tx(l.text,229,y+l.dy,l.size||44,{style:l.bold?1:0,fill:l.color||ink,role:'rule-body'});});
 }
 const criteria=c.compensation.one.anyOf.flatMap((s,i)=>[...(i?[{text:'或',fill:muted}]:[]),{text:s,bold:true,...(s==='SVP'?{font:NUM}:{})}]);
 partyCard('一陪',1573,222,[{runs:criteria,dy:51},{text:c.compensation.one.result,dy:111,size:42},{text:c.compensation.one.exception,dy:171,size:42,bold:true,color:accent}]);
 const duo=c.compensation.two.anyOf.flatMap((s,i)=>[...(i?[{text:'或',fill:muted}]:[]),{text:s,bold:true,...(s==='SVP'?{font:NUM}:{})}]);
 partyCard('双陪',1815,232,[{runs:duo,dy:50},{text:c.compensation.two.who+'达成任意一项，',dy:115,size:42},{text:'即正常结算',dy:175,size:42}]);
 partyCard('三陪',2067,174,[{runs:[{text:c.compensation.three.cause},{text:'→',fill:muted},{text:c.compensation.three.result,bold:true,fill:accent}],dy:51,size:48},{text:c.compensation.three.exception,dy:119,size:42}]);
 partyCard('四陪',2261,194,c.compensation.four.map((r,i)=>({runs:[{text:r.cause},{text:'→',fill:muted},{text:r.result,bold:true,fill:accent}],dy:i===0?60:132,size:48})));
 partyCard('五陪',2475,117,[{runs:[{text:c.compensation.five.result,bold:true},{text:'（'+c.compensation.five.exception+'）',fill:muted}],dy:58}]);

 const dx=1080;
 heading('heading-additions','04','加价说明',1520,dx);
 box('hourly-additions',54+dx,1573,972,391);
 tx(c.additions.hourly.condition,540+dx,1625,46,{style:1,anchor:'middle'});
 c.additions.hourly.byRank.forEach((r,i)=>{const y=1694+i*71;tx(r.label,92+dx,y,42,{role:'rule-body'});rich([{text:'+'+r.amount,bold:true,font:NUM,fill:accent,size:48},{text:c.additions.hourly.unit,size:35,fill:accent}],986+dx,y,44,{anchor:'end',role:'price-unit'});if(i<2)line(92+dx,y+36,986+dx);});
 tx(c.additions.hourly.exemption,540+dx,1914,40,{anchor:'middle',fill:muted,role:'rule-body'});
 c.additions.extras.forEach((r,i)=>{const x=54+dx+i*496;box('extra-'+i,x,1984,476,150,soft,'#d7d1e7');tx(r.label,x+238,2027,42,{anchor:'middle',style:1});rich([{text:'+'+r.amount,bold:true,font:NUM,fill:accent,size:48},{text:r.unit,fill:accent,size:35}],x+238,2091,44,{anchor:'middle',role:'price-unit'});});
 box('round-additions',54+dx,2154,972,290);
 tx('按把结算',90+dx,2198,42,{style:1});
 c.additions.rounds.forEach((r,i)=>{const y=2265+i*69;tx(r.label,90+dx,y,42,{role:'rule-body'});if(r.amount===0)tx(r.result,982+dx,y,42,{anchor:'end',style:1,fill:accent});else rich([{text:(r.amount>0?'+':'−')+Math.abs(r.amount),font:NUM,bold:true,size:48,fill:accent},{text:r.unit,size:35,fill:accent}],982+dx,y,44,{anchor:'end',role:'price-unit'});});
 box('mixed-party',54+dx,2464,972,128,soft,'#d7d1e7');
 tx(c.additions.hourly.mixed.split('，')[0]+'，',92+dx,2504,42,{fill:muted,role:'rule-body'});tx(c.additions.hourly.mixed.split('，')[1],92+dx,2552,42,{style:1,role:'rule-body'});

 heading('heading-red','05','红屏计时',2660);
 box('red-screen',54,2713,972,371);
 tx(c.redScreen.scope+' · '+c.redScreen.definition,540,2760,40,{anchor:'middle',fill:muted,role:'rule-body'});
 const red=[['未满'+c.redScreen.early.round+'回合',c.redScreen.early.result],[c.redScreen.late.round+'回合及以上',c.redScreen.late.result],['赛点及加时','计整局']];
 red.forEach(([a,b],i)=>{const y=2832+i*78;tx(a,93,y,46,{style:1});tx('→',666,y,40,{anchor:'middle',fill:muted});tx(b,985,y,46,{anchor:'end',style:1,fill:accent});if(i<2)line(93,y+40,985);});
 tx(c.redScreen.entertainment,540,3044,40,{anchor:'middle',fill:muted,role:'rule-body'});
 box('conversion',54,3104,972,180,soft,'#d7d1e7');
 rich([{text:'默认 ',fill:muted},{text:c.redScreen.conversion.hours+'小时',bold:true},{text:' = ',fill:muted},{text:c.redScreen.conversion.rounds+'局',bold:true}],540,3157,46,{anchor:'middle'});
 rich([{text:'免1局',bold:true},{text:' = '+c.redScreen.conversion.freeRoundHours+'小时',fill:muted}],299,3231,42,{anchor:'middle'});
 rich([{text:'免半局',bold:true},{text:' = '+c.redScreen.conversion.freeHalfRoundHours+'小时',fill:muted}],784,3231,42,{anchor:'middle'});

 heading('heading-settlement','06','结算与补充',2660,dx);
 box('settlement',54+dx,2713,972,571);
 tx(c.settlement.draws,91+dx,2808,42,{style:1,role:'rule-body'});
 tx(c.settlement.applies,91+dx,2865,42,{fill:muted,role:'rule-body'});line(91+dx,2937,988+dx);
 const missing=c.settlement.missingRank.split('，');
 tx(missing[0]+'，',91+dx,2990,42,{fill:muted,role:'rule-body'});tx(missing.slice(1).join('，'),91+dx,3047,42,{style:1,role:'rule-body'});line(91+dx,3128,988+dx);
 tx(c.settlement.p0,91+dx,3190,42,{fill:muted,role:'rule-body'});

 region('footer',54,3334,2052,69,0,0);line(62,3327,2098);
 tx(c.brand+' · '+c.title+' · 陪玩价目表与点单须知',1080,3374,36,{anchor:'middle',fill:muted});
 return {requests,pages:[p],reviewRegions:regions};
}
