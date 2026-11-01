import {createHash} from 'node:crypto';

export function makeLayout(c){
 const ink='#243f55', muted='#647d91', accent='#8170b1', soft='#f3f0fa', blue='#e9f2f8', white='#fffefa';
 const CN='Microsoft YaHei UI', NUM='Bahnschrift', ART='Noto Sans SC Black';
 const requests=[],ids=new Map(),regions=[];
 function request(text,font=CN,style=0){const k=JSON.stringify([String(text),font,style]);if(!ids.has(k)){const id='t'+createHash('sha256').update(k).digest('hex').slice(0,20);ids.set(k,id);requests.push({id,text:String(text),font,style});}return ids.get(k);}
 const pages=[];
 function page(id,label,w,h,ew,eh){const p={id,label,bg:id+'.png',w,h,ew,eh,platforms:['desktop'],elements:[]};pages.push(p);return p;}
 const raw=(p,markup)=>p.elements.push({type:'raw',markup});
 function tx(p,text,x,cy,size=44,o={}){if(text===null||text===undefined)throw Error('Missing text');const {font=CN,style=0,fill=ink,anchor='start',role='copy',maxWidth}=o;p.elements.push({type:'text',id:request(text,font,style),text:String(text),x,cy,size,fill,anchor,role,maxWidth});}
 function art(p,text,x,cy,size,maxWidth){p.elements.push({type:'art',text,ids:[...text].map(t=>request(t,ART,0)),x,cy,size,maxWidth,anchor:'middle',fill:ink,shadow:'#b7c6e0',outline:white,outlineWidth:4,angles:true});}
 function rich(p,runs,x,cy,size=44,o={}){const group=p.id+'-line-'+p.elements.length;p.elements.push({type:'rich',runs:runs.map(r=>{if(typeof r==='string')r={text:r};return {...r,id:request(r.text,r.font||CN,r.bold?1:0),fill:r.fill||ink};}),x,cy,size,anchor:o.anchor||'start',maxWidth:o.maxWidth,role:o.role||'rule-body',group});}
 function rect(p,x,y,w,h,fill=white,stroke='#c9d5e0',r=22){raw(p,`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`);}
 function line(p,x1,y1,x2,y2,color='#dce4ed'){raw(p,`<path d="M${x1} ${y1}H${x2}" fill="none" stroke="${color}" stroke-width="2"/>`);}
 function region(p,id,x,y,w,h){regions.push({page:p.id,containerId:id,x,y,w,h,safe:{x:x+24,y:y+20,w:w-48,h:h-40}});}
 function icon(p,x,y){raw(p,`<g transform="translate(${x} ${y})" fill="none" stroke="${accent}" stroke-width="3" stroke-linecap="round"><circle r="17"/><circle r="6"/><path d="M-26 0h10M16 0h10M0-26v10M0 16v10"/></g>`);}
 function hero(p,subtitle){
  rect(p,61,59,254,47,'#ffffff','#b9cbd9',24);tx(p,'XINGAN CLUB',188,83,28,{font:NUM,style:1,anchor:'middle',fill:muted});
  tx(p,c.brand,278,145,50,{anchor:'middle',style:1});
  art(p,c.title,279,250,104,441);tx(p,subtitle,279,349,50,{anchor:'middle',style:1});
  tx(p,c.game+' · DELTA FORCE',279,418,32,{anchor:'middle',fill:muted});
  region(p,'hero-copy',53,110,452,345);
 }
 function footer(p,y,label){line(p,62,y-50,p.w-62,y-50);tx(p,c.brand+' · '+c.title+' · '+label,p.w/2,y,36,{anchor:'middle',fill:muted});}
 function heading(p,n,title,y,sub){rect(p,54,y-39,58,67,ink,ink,12);tx(p,n,83,y-5,34,{font:NUM,style:1,fill:white,anchor:'middle'});tx(p,title,132,y-5,54,{style:1});if(sub)tx(p,sub,1025,y-5,36,{anchor:'end',fill:muted});}
 function table(p,values,y,hourly){
  const x=54,w=972,rw=168,cw=(w-rw)/6,hh=88,rh=98,h=hh+6*rh;
  rect(p,x,y,w,h,'#ffffff','#b8cbd9',20);raw(p,`<path d="M${x+20} ${y}H${x+w-20}Q${x+w} ${y} ${x+w} ${y+20}V${y+hh}H${x}V${y+20}Q${x} ${y} ${x+20} ${y}Z" fill="${ink}"/>`);
  tx(p,'段位',x+rw/2,y+hh/2,40,{style:1,fill:white,anchor:'middle'});
  c.tierNames.forEach((t,j)=>tx(p,t,x+rw+(j+.5)*cw,y+hh/2,40,{style:1,fill:white,anchor:'middle'}));
  values.forEach((row,i)=>{
   const top=y+hh+i*rh,cy=top+rh/2;
   raw(p,`<rect x="${x+2}" y="${top}" width="${w-4}" height="${rh}" fill="${i%2===0?blue:'#ffffff'}" opacity=".66"/>`);
   if(i===0){tx(p,hourly?'下三':'下三',x+rw/2,cy-(hourly?20:0),38,{style:1,anchor:'middle'});if(hourly)tx(p,'／匹配',x+rw/2,cy+23,30,{anchor:'middle',fill:muted});}
   else tx(p,c.rankNames[i],x+rw/2,cy,38,{style:1,anchor:'middle'});
   row.forEach((v,j)=>{
    const cx=x+rw+(j+.5)*cw;
    if(v===null)tx(p,c.prices.unavailableLabel,cx,cy,44,{fill:'#9eb0bd',anchor:'middle'});
    else {const unitX=cx+29;tx(p,v,unitX-8,cy,44,{font:NUM,style:1,fill:j===0?muted:ink,anchor:'end',role:'price-number'});tx(p,'r',unitX,cy,30,{font:NUM,fill:accent,role:'price-unit'});}
   });
   if(i<5)line(p,x+17,top+rh,x+w-17,top+rh,'#dfe7ef');
   region(p,(hourly?'hourly':'rounds')+'-row-'+i,x+2,top,w-4,rh);
  });
  for(let j=0;j<6;j++)raw(p,`<path d="M${x+rw+j*cw} ${y+hh}V${y+h-10}" stroke="#d7e2ec" stroke-width="1.5"/>`);
  region(p,(hourly?'hourly':'rounds')+'-table',x,y,w,h);
 }
 const price=page('prices','01 · 兴安电竞黑潮爆破陪玩价目表',1080,2480,2400,5511);
 hero(price,c.copy.pricePage);
 rect(price,54,487,972,69,soft,'#d8d1e9',18);tx(price,c.copy.priceSubtitle,540,522,42,{style:1,anchor:'middle',fill:accent});
 heading(price,'01',c.copy.hourlyTitle,618,c.copy.hourlyCaption);
 table(price,c.prices.hourly.values,677,true);
 rect(price,54,1375,972,80,'#fff','#d7dfe9',18);tx(price,'女陪／娱乐陪无赔付',540,1415,42,{style:1,anchor:'middle',fill:accent});
 heading(price,'02',c.copy.roundsTitle,1542,c.copy.roundsCaption);
 table(price,c.prices.rounds.values,1601,false);
 tx(price,c.copy.unavailable,540,2327,38,{anchor:'middle',fill:muted});

 const notice=page('notice','02 · 兴安电竞黑潮爆破点单须知',1080,4390,2400,9756);
 hero(notice,c.copy.noticePage);
 const noticeStart=notice.elements.length;
 rect(notice,54,481,972,117,ink,ink,22);
 tx(notice,'女陪／娱乐陪无赔付',540,520,46,{style:1,fill:white,anchor:'middle'});
 tx(notice,'有赔付订单按以下标准结算',540,566,38,{fill:'#cddce9',anchor:'middle'});
 heading(notice,'03','赔付标准',664);
 function partyCard(label,y,h,lines){
  rect(notice,54,y,972,h,'#ffffff','#c8d6e3',22);rect(notice,74,y+20,124,h-40,soft,'#e0daed',14);tx(notice,label,136,y+h/2,43,{style:1,anchor:'middle',fill:accent});
  lines.forEach(l=>{if(l.runs)rich(notice,l.runs,229,y+l.dy,l.size||44);else tx(notice,l.text,229,y+l.dy,l.size||44,{style:l.bold?1:0,fill:l.color||ink});});
  region(notice,'compensation-'+label,218,y,792,h);
 }
 const criteria=[{text:'赢局',bold:true},{text:'或',fill:muted},{text:'SVP',bold:true,font:NUM},{text:'或',fill:muted},{text:'对局伤害最高',bold:true}];
 partyCard('一陪',717,222,[{runs:criteria,dy:51,size:44},{text:c.compensation.one.result,dy:111,size:42},{text:c.compensation.one.exception,dy:171,size:42,bold:true,color:accent}]);
 partyCard('双陪',959,232,[{runs:criteria,dy:50,size:44},{text:c.compensation.two.who+'达成任意一项，',dy:115,size:42},{text:'即正常结算',dy:175,size:42}]);
 partyCard('三陪',1211,174,[{runs:[{text:c.compensation.three.cause},{text:'→',fill:muted},{text:c.compensation.three.result,bold:true,fill:accent}],dy:51,size:48},{text:c.compensation.three.exception,dy:119,size:42}]);
 partyCard('四陪',1405,194,[{runs:[{text:c.compensation.four[0].cause},{text:'→',fill:muted},{text:c.compensation.four[0].result,bold:true,fill:accent}],dy:60,size:48},{runs:[{text:c.compensation.four[1].cause},{text:'→',fill:muted},{text:c.compensation.four[1].result,bold:true,fill:accent}],dy:132,size:48}]);
 partyCard('五陪',1619,117,[{runs:[{text:c.compensation.five.result,bold:true},{text:'（'+c.compensation.five.exception+'）',fill:muted}],dy:58,size:44}]);

 heading(notice,'04','加价说明',1816);
 rect(notice,54,1869,972,391,'#ffffff','#c8d6e3',22);
 tx(notice,c.additions.hourly.condition,540,1921,46,{style:1,anchor:'middle'});
 const rankAdd=c.additions.hourly.byRank;
 rankAdd.forEach((r,i)=>{
  const y=1990+i*71;
  tx(notice,r.label,92,y,42);
  rich(notice,[{text:'+'+r.amount,bold:true,font:NUM,fill:accent,size:48},{text:c.additions.hourly.unit,size:35,fill:accent}],986,y,44,{anchor:'end',role:'price-unit'});
  if(i<2)line(notice,92,y+36,986,y+36);
 });
 tx(notice,c.additions.hourly.exemption,540,2210,40,{anchor:'middle',fill:muted});
 region(notice,'hourly-additions',54,1869,972,391);
 c.additions.extras.forEach((r,i)=>{
  const x=54+i*496;rect(notice,x,2280,476,150,soft,'#d7d1e7',20);
  tx(notice,r.label,x+238,2323,42,{anchor:'middle',style:1});
  rich(notice,[{text:'+'+r.amount,bold:true,font:NUM,fill:accent,size:48},{text:r.unit,fill:accent,size:35}],x+238,2387,44,{anchor:'middle',role:'price-unit'});
  region(notice,'extra-'+i,x,2280,476,150);
 });
 rect(notice,54,2450,972,290,'#ffffff','#c8d6e3',22);
 tx(notice,'按把结算',90,2494,42,{style:1});
 c.additions.rounds.forEach((r,i)=>{
  const y=2561+i*69;tx(notice,r.label,90,y,42);
  if(r.amount===0)tx(notice,r.result,982,y,42,{anchor:'end',style:1,fill:accent});
  else rich(notice,[{text:(r.amount>0?'+':'−')+Math.abs(r.amount),font:NUM,bold:true,size:48,fill:accent},{text:r.unit,size:35,fill:accent}],982,y,44,{anchor:'end',role:'price-unit'});
 });
 region(notice,'round-additions',54,2450,972,290);

 heading(notice,'05','红屏计时',2819);
 rect(notice,54,2872,972,371,'#ffffff','#c8d6e3',22);
 tx(notice,c.redScreen.scope+' · '+c.redScreen.definition,540,2919,40,{anchor:'middle',fill:muted});
 const red=[['未满'+c.redScreen.early.round+'回合',c.redScreen.early.result],[c.redScreen.late.round+'回合及以上',c.redScreen.late.result],['赛点及加时','计整局']];
 red.forEach(([a,b],i)=>{const y=2991+i*78;tx(notice,a,93,y,46,{style:1});tx(notice,'→',666,y,40,{anchor:'middle',fill:muted});tx(notice,b,985,y,46,{anchor:'end',style:1,fill:accent});if(i<2)line(notice,93,y+40,985,y+40);});
 tx(notice,c.redScreen.entertainment,540,3203,40,{anchor:'middle',fill:muted});
 region(notice,'red-screen',54,2872,972,371);
 rect(notice,54,3263,972,180,soft,'#d7d1e7',22);
 rich(notice,[{text:'默认 ',fill:muted},{text:c.redScreen.conversion.hours+'小时',bold:true},{text:' = ',fill:muted},{text:c.redScreen.conversion.rounds+'局',bold:true}],540,3316,46,{anchor:'middle'});
 rich(notice,[{text:'免1局',bold:true},{text:' = '+c.redScreen.conversion.freeRoundHours+'小时',fill:muted}],299,3390,42,{anchor:'middle'});
 rich(notice,[{text:'免半局',bold:true},{text:' = '+c.redScreen.conversion.freeHalfRoundHours+'小时',fill:muted}],784,3390,42,{anchor:'middle'});
 region(notice,'conversion',54,3263,972,180);

 heading(notice,'06','结算与补充',3522);
 rect(notice,54,3575,972,639,'#ffffff','#c8d6e3',22);
 const ruleRows=[
  {y:3650,h:134,lines:[c.additions.hourly.mixed.split('，')[0]+'，','按少陪多计算'],bold:[false,true]},
  {y:3790,h:122,lines:[c.settlement.draws,c.settlement.applies],bold:[true,false]},
  {y:3925,h:133,lines:['无对应段位价格时，','按该陪玩最高单价接单，且无赔付'],bold:[false,true]},
  {y:4075,h:90,lines:[c.settlement.p0],bold:[false]}
 ];
 ruleRows.forEach((r,i)=>{
  r.lines.forEach((s,j)=>tx(notice,s,91,r.y+37+(j-(r.lines.length-1)/2)*57,42,{style:r.bold[j]?1:0,fill:r.bold[j]?ink:muted}));
  if(i<ruleRows.length-1)line(notice,91,r.y+37+r.h/2+8,988,r.y+37+r.h/2+8);
 });
 region(notice,'settlement',54,3575,972,639);
 footer(notice,4320,'点单须知');

 const shift=1969;
 const merged={...price,id:'blacktide',label:'兴安电竞 · 黑潮爆破陪玩价目表与点单须知',bg:'blacktide.png',h:6360,eh:14133,elements:[...price.elements]};
 merged.elements.push(...notice.elements.slice(noticeStart).map(e=>e.type==='raw'?{...e,markup:`<g transform="translate(0 ${shift})">${e.markup}</g>`}:{...e,cy:e.cy+shift}));
 const mergedRegions=regions.filter(r=>r.page==='prices'||(r.page==='notice'&&r.y>=481)).map(r=>({...r,page:'blacktide',y:r.y+(r.page==='notice'?shift:0),safe:{...r.safe,y:r.safe.y+(r.page==='notice'?shift:0)}}));
 return {requests,pages:[merged],reviewRegions:mergedRegions};
}
