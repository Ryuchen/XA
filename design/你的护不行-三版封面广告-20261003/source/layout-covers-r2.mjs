import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.basename(here)==='source'?path.dirname(here):here;
export function makeLayout(c){
 const source=JSON.parse(fs.readFileSync(path.join(root,'review/cover-background-stage-r2.json'),'utf8'));
 const requests=[],pages=[],regions={};let seq=0;
 const ink='#20394A',paper='#FBFAF7';
 for(const p of c.products){
  const id=p.id+'-cover',item=source.items.find(v=>v.id===id);
  if(!item)throw Error('Missing measured background '+id);
  const elements=[],r=regions[id]={};
  const safe=structuredClone(item.safeTextZonesInViewBox),panels=item.panelsInViewBox;
  // Re-measured clear interval between the version and platform bars.
  const top=panels.version[1]+panels.version[3]+15;
  const bottom=panels.platform[1]-15;
  safe.tagline=[235,top,390,bottom-top];
  for(const [key,bounds] of Object.entries(safe))r[key]={bounds:panels[key]||bounds,safe:bounds};
  function text(text,region,size,{bold=false,fill=ink,y}={}){
   const [x,t,w,h]=safe[region],tid='r2t'+seq++;
   requests.push({id:tid,text,font:'Microsoft YaHei UI',style:bold?1:0});
   elements.push({type:'text',id:tid,text,x:x+w/2,cy:y??t+h/2,size,fill,anchor:'middle',maxWidth:w,role:'cover-'+region,group:region,lineGroup:id+'-'+region+'-'+(y??'single')});
  }
  text(c.brand,'brand',46,{bold:true,fill:'#587484'});
  const [tx,ty,tw,th]=safe.title;
  const ids=[...c.title].map(ch=>{const gid='r2t'+seq++;requests.push({id:gid,text:ch,font:'Noto Sans SC Black',style:0});return gid;});
  elements.push({type:'art',text:c.title,ids,x:tx+tw/2,cy:ty+th/2,size:p.id==='firearms'?89:108,maxWidth:Math.min(480,tw-26),fill:ink,shadow:'#B8CEDA',outline:paper,outlineWidth:3,angles:true,anchor:'middle',group:'title'});
  text(p.name,'version',p.id==='firearms'?56:60,{bold:true,fill:paper});
  const lines=p.coverTagline.split(' · ');
  if(lines.length!==2)throw Error('Expected semantic two-part cover tagline '+p.id);
  const center=safe.tagline[1]+safe.tagline[3]/2;
  lines.forEach((line,i)=>text(line,'tagline',47,{y:center+(i-.5)*51}));
  text(c.platformPricePolicy,'platform',p.id==='firearms'?41:43);
  text(p.modes.map(m=>m.name).join(' / '),'mode',42);
  pages.push({id,w:1600,h:1000,ew:3200,eh:2000,label:`${c.title}·${p.name}｜蜂医灰鼠封面`,platforms:['desktop','mobile'],bg:path.basename(item.file),elements});
 }
 return{revision:'r2-covers',requests,pages,regions,notes:{title:'矢量轮廓艺术字；标题字形与文字参数可在源布局修改。',tagline:'按既有中点分成两行，保持原意。两行完整组在版本与平台底板之间居中。',background:'原生1586×992，最终3200×2000；背景位图嵌入，文字矢量渲染。'}};
}
