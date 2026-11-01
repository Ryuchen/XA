import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
export function makeLayout(c){
 const requests=[],pages=[],regions={};let seq=0,els,pageId,regionId;
 const ink='#20394A',muted='#718897',paper='#FBFAF7',line='#D2DDE1';
 const assets=Object.fromEntries(c.products.map(p=>[p.id,fs.readFileSync(path.join(root,c.artwork[p.id].file)).toString('base64')]));
 const raw=markup=>els.push({type:'raw',markup});
 const box=(x,y,w,h,fill,stroke='none',r=22)=>raw(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`);
 const rule=(x,y,x2,col=line)=>raw(`<path d="M${x} ${y}H${x2}" stroke="${col}" stroke-width="2"/>`);
 function tx(text,x,cy,size=38,fill=ink,bold=false,anchor='start',maxWidth,role='copy',font='Microsoft YaHei UI',group){const id='t'+seq++;requests.push({id,text:String(text),font,style:bold?1:0});els.push({type:'text',id,text:String(text),x,cy,size,fill,anchor,maxWidth,role,group:regionId,lineGroup:group||`${pageId}-${regionId}-${cy}`});}
 function art(text,x,cy,size,maxWidth){const ids=[...text].map(ch=>{const id='t'+seq++;requests.push({id,text:ch,font:'Noto Sans SC Black',style:0});return id;});els.push({type:'art',text,ids,x,cy,size,maxWidth,fill:ink,shadow:'#B8CEDA',outline:paper,outlineWidth:1.3,angles:true,anchor:'middle'});}
 function region(id,bounds,safe=bounds){regionId=id;regions[pageId][id]={bounds,safe};}
 function hero(p,x,y,w,h=w){raw(`<image x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,${assets[p.id]}"/>`);regions[pageId].artwork={bounds:[x,y,w,h],kind:'image-exclusion'};}
 function star(x,y,s,col){raw(`<path d="M${x} ${y-s}Q${x+s*.18} ${y-s*.18} ${x+s} ${y}Q${x+s*.18} ${y+s*.18} ${x} ${y+s}Q${x-s*.18} ${y+s*.18} ${x-s} ${y}Q${x-s*.18} ${y-s*.18} ${x} ${y-s}Z" fill="${col}"/>`);}
 function chip(text,x,y,w,h,fill=ink,color='#FFFFFF',size=35){box(x,y,w,h,fill,'none',h/2);tx(text,x+w/2,y+h/2,size,color,true,'middle',w-36,'label');}
 function amount(n,x,cy,size,unit,col,role='amount'){const g=`${pageId}-${regionId}-${role}-${cy}-${x}`;tx(n,x,cy,size,col,true,'end',undefined,role+'-number','Bahnschrift',g);tx(unit,x+9,cy,size*.94,col,true,'start',undefined,role+'-unit','Bahnschrift',g);}
 function score(n,x,cy,size,col,unit='分'){const g=`${pageId}-${regionId}-score-${cy}-${x}`;tx(n,x,cy,size,col,true,'end',undefined,'score-number','Bahnschrift',g);tx(unit,x+8,cy,size*.66,col,true,'start',undefined,'score-unit','Microsoft YaHei UI',g);}
 function start(id,w,h,ew,eh,label){pageId=id;regions[id]={};els=[];pages.push({id,w,h,ew,eh,label,platforms:['desktop','mobile'],bg:'paper.png',elements:els});box(14,14,w-28,h-28,'none','#D7E1E5',32);}
 function tags(x,y){raw(`<g transform="translate(${x} ${y}) rotate(-12)" stroke="#6E8C9E" stroke-width="3"><path d="M0 12L13 0H64L77 12V104L64 116H13L0 104Z" fill="#E5EDF1"/><circle cx="38" cy="16" r="6" fill="${paper}"/><path d="M20 55H58M20 70H58M20 85H48"/><path d="M32 10C-52-88 145-85 100 51" fill="none"/><path d="M86 41L97 30H141L153 42V127L141 138H97L86 127Z" fill="#F3EDE1"/><circle cx="118" cy="45" r="5" fill="${paper}"/><path d="M103 80H137M103 94H137M103 109H128"/></g>`);}
 for(const p of c.products){
  start(p.id+'-cover',1600,1000,3200,2000,`${c.title}·${p.name}｜通用封面`);
  box(38,38,1524,924,'#F6F8F5','#D2DFE4',32);
  raw('<path d="M945 64Q1519 13 1540 676Q1514 860 1011 850" fill="#E4EDF1" opacity=".72"/>');
  hero(p,862,p.id==='firearms'?188:136,670,p.id==='firearms'?616:670);
  if(p.id==='dogtag')tags(1120,665);
  const cx=465;
  region('brand',[82,94,766,97]);tx(c.brand,cx,142,37,muted,true,'middle',708,'cover-brand');
  region('title',[58,211,806,164]);art(c.title,cx,291,150,760);
  region('version',[cx-177,406,354,98]);chip(p.name,cx-177,406,354,98,ink,'#FFFFFF',62);
  region('tagline',[73,544,785,84]);tx(p.coverTagline,cx,586,46,ink,false,'middle',741,'cover-tagline');
  region('platform',[cx-320,662,640,82]);chip(c.platformPricePolicy,cx-320,662,640,82,'#E1EBF0',ink,43);
  region('mode',[cx-238,778,476,73]);chip('绝密 / 机密',cx-238,778,476,73,'#ECF0E9',ink,42);
  star(859,131,20,'#C8AB77');star(1449,850,21,'#9CBCAF');
  start(p.id+'-social',1200,1600,3000,4000,`${c.title}·${p.name}｜双难度广告`);
  region('brand',[58,30,650,55]);tx(c.brand,65,61,30,muted,true);rule(230,61,690);
  region('title',[40,102,687,159]);art(c.title,383.5,181.5,130,640);
  region('version',[64,265,268,73]);chip(p.name,64,265,268,73,ink,'#FFFFFF',46);
  region('tagline',[355,269,786,64]);tx(p.adTagline,357,302,p.id==='dogtag'?40:43,ink,false,'start',767,'hero-tagline');
  hero(p,758,20,390,252);
  region('platform',[58,361,1084,65]);chip(c.platformPricePolicy,58,361,1084,65,'#E5EDF1',ink,40);
  p.modes.forEach((m,k)=>{
   const x=k===0?58:625,w=517,top=456,head=76;
   region('mode-'+m.id,[x,top,w,head]);box(x,top,w,head,ink,'none',20);box(x,top+36,w,head-36,ink,'none',0);tx(`${m.name}地图`,x+w/2,top+head/2,46,'#FFFFFF',true,'middle',455,'ad-mode');
   const y=top+head+4,total=832,row=total/m.tiers.length;
   m.tiers.forEach((t,i)=>{
    const tY=y+i*row,cy=tY+row/2;region(`price-${m.id}-${i}`,[x,tY,w,row-4],[x+24,tY+17,w-48,row-38]);
    box(x,tY,w,row-4,i%2?'#EEF3F4':m.tint,'none',i===m.tiers.length-1?18:0);
    const primary=p.id==='firearms'?tY+45:cy-60,secondary=p.id==='firearms'?tY+105:cy;
    tx(t.name,x+27,primary,43,ink,true,'start',170,'ad-tier');
    amount(t.price,x+417,primary,67,'r',m.accent,'price');
    if(p.id!=='firearms'){
     tx('物资保底',x+27,secondary,42,muted,false,'start',180,'guarantee-label');amount(t.guarantee,x+417,secondary,56,'w',ink,'guarantee');
     tx('基础分数',x+27,cy+60,42,muted,false,'start',180,'score-label');score(t.baseScore,x+417,cy+60,56,m.accent);
    }
    else {
     tx('保底',x+27,secondary,42,muted,false,'start',100,'guarantee-label');amount(t.guarantee,x+259,secondary,52,'w',ink,'guarantee');
     score(t.count,x+448,secondary,51,m.accent,'把');
     let lines=[],rem=[...t.guns],rows=Math.ceil(t.guns.length/3);while(rows){lines.push(rem.splice(0,Math.ceil(rem.length/rows)).join(' · '));rows--;}
     const mid=tY+(lines.length===3?201:218),gap=39;lines.forEach((l,j)=>tx(l,x+w/2,mid+(j-(lines.length-1)/2)*gap,42,ink,false,'middle',465,'gun-list','Bahnschrift'));
    }
   });
  });
  region('core',[58,1401,1084,137],[85,1415,1030,110]);box(58,1401,1084,137,'#E1EBF0','none',25);
  tx(p.core,600,1441,45,ink,true,'middle',980,'ad-core');
  tx(p.settlement,600,1500,36,ink,false,'middle',1008,'ad-settlement');
 }
 return {requests,pages,regions};
}
