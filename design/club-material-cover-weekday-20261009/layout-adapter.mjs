import {createHash} from 'node:crypto';
export function makeLayout(c){
 const requests=[],ids=new Map();
 function request(text,font){const k=JSON.stringify([text,font]);if(!ids.has(k)){const id='t'+createHash('sha256').update(k).digest('hex').slice(0,20);ids.set(k,id);requests.push({id,text,font,style:0});}return ids.get(k);}
 const ink='#213F53',gold='#A96620',white='#FFFDF7';
 const p={id:'cover',label:'常规物资单 · 工作日专享封面',bg:'cover.png',w:1672,h:941,ew:3200,eh:1800,platforms:['desktop','mobile'],elements:[],containers:[]};
 function tx(text,x,cy,size,opt={}){const font=opt.font||'MiSans VF Semibold';p.elements.push({type:'text',id:request(text,font),text,x,cy,size,fill:ink,anchor:'start',...opt});}
 const raw=markup=>p.elements.push({type:'raw',markup});
 raw(`<g transform="translate(95 ${162-38*.52}) scale(${38/28})" fill="none" stroke="${ink}" stroke-width="2.1" stroke-linejoin="round"><path d="M0 27 L15 0 L31 27 Z M8 22 L15 10 L23 22 Z"/></g>`);
 tx(c.brand,95+38*1.35,162,38,{group:'brand',role:'brand'});
 p.elements.push({type:'art',text:c.title,x:87,cy:348,size:125,maxWidth:645,fill:ink,shadow:'#A4BFD5',outline:white,outlineWidth:7,angles:true,group:'headline',ids:[...c.title].map(t=>request(t,'Noto Sans SC Black'))});
 raw('<path d="M105 432 Q366 461 689 433" fill="none" stroke="#E4B474" stroke-width="11" stroke-linecap="round"/>');
 tx(c.subtitle,96,497,39,{group:'subtitle'});
 tx(c.platforms,366.5,578,32,{fill:white,anchor:'middle',group:'platform'});
 tx(c.coverOffer.when,366.5,638,36,{anchor:'middle',group:'activity'});
 p.elements.push({type:'rich',x:366.5,cy:681,size:38,anchor:'middle',fill:gold,group:'activity-benefit',role:'offer',runs:[{text:c.coverOffer.benefit,id:request(c.coverOffer.benefit,'MiSans VF Semibold'),size:38,fill:gold,bold:true},{text:c.coverOffer.increase,id:request(c.coverOffer.increase,'MiSans VF Bold'),size:48,fill:gold,bold:true,gap:12}]});
 p.containers=[{id:'brand',safe:[93,140,620,45]},{id:'headline',safe:[85,265,675,163]},{id:'subtitle',safe:[93,472,610,50]},{id:'platform',safe:[115,553,503,50]},{id:'activity',safe:[96,618,541,88]}];
 return{requests,pages:[p]};
}
