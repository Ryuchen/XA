import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const layout=await read('layout.json'),cache=await read('.cache/font-outlines.json');
const glyphs=Object.fromEntries(layout.requests.map(r=>[r.id,cache[createHash('sha256').update(JSON.stringify([r.text,r.font,r.style])).digest('hex')]]));
const union=b=>({left:Math.min(...b.map(v=>v.left)),top:Math.min(...b.map(v=>v.top)),right:Math.max(...b.map(v=>v.right)),bottom:Math.max(...b.map(v=>v.bottom))});
function artBounds(e){
 const natural=e.ids.reduce((sum,id)=>sum+glyphs[id].width/100,0)+.065*(e.ids.length-1),size=Math.min(e.size,e.maxWidth?e.maxWidth/natural:Infinity),out=[];
 let x=e.x;
 e.ids.forEach((id,i)=>{const g=glyphs[id],s=size/100,w=g.width*s,h=g.height*s,angle=(e.angles?[-2.8,1.4,-1.7,2.2,-1.4][i%5]:0)*Math.PI/180,cy=e.cy+(e.angles?[1,-2,1,-1,0][i%5]*size/70:0);
  for(const layer of [{dx:0,dy:0,pad:e.outlineWidth/2},{dx:size*.035,dy:size*.055,pad:.3}]){
   const points=[[-w/2,-h/2],[w/2,-h/2],[w/2,h/2],[-w/2,h/2]].map(([px,py])=>[x+w/2+(px+layer.dx)*Math.cos(angle)-(py+layer.dy)*Math.sin(angle),cy+(px+layer.dx)*Math.sin(angle)+(py+layer.dy)*Math.cos(angle)]);
   out.push({left:Math.min(...points.map(v=>v[0]))-layer.pad,top:Math.min(...points.map(v=>v[1]))-layer.pad,right:Math.max(...points.map(v=>v[0]))+layer.pad,bottom:Math.max(...points.map(v=>v[1]))+layer.pad});
  }
  x+=w+size*.065;
 });return {text:e.text,...union(out)};
}
const report=[];
for(const p of layout.pages){
 const bounds=await read('review/'+p.id+'-text-bounds.json'),art=p.elements.filter(e=>e.type==='art').map(artBounds);
 const box=roles=>{const b=bounds.filter(v=>roles.includes(v.role)).map(v=>({left:v.x,top:v.y,right:v.x+v.width,bottom:v.y+v.height}));return b.length?union(b):null;};
 const record={id:p.id,art};
 if(p.id==='cover')record.versionLine={bounds:box(['versions']),safeBand:[749,782]};
 else if(p.id==='social')record.featureBox={bounds:box(['guarantee-scope','method-summary','minimum-condition','minimum-mode','minimum-loot-number','minimum-loot-unit']),panel:[56,1157,974,212]};
 else{
  record.titleToPillGap=271-Math.max(...art.slice(0,2).map(a=>a.bottom));
  record.leverCopy={bounds:box(['lever-conditions']),lastIconBottom:1054};
  record.cardRuleCopy={bounds:box(['card-timing','included-cards','missing-card','missing-card-compensation-number','missing-card-compensation-unit']),rowTop:1506,rowBottom:1639};
  record.cardRuleDisplay={left:293,right:747,top:record.cardRuleCopy.bounds.top,bottom:1623,rowCenter:1572.5,description:'开卡说明两行 + 两个缺卡补偿条'};
  record.quickExtraction={bounds:box(['quick-extraction-condition','quick-extraction-and','quick-extraction-comparison','quick-extraction-loot-number','quick-extraction-loot-unit','quick-extraction-result']),rowTop:1639,rowBottom:1766,band:[293,1693,454,48]};
  record.unitColumns=bounds.filter(v=>['missing-card-compensation-unit','quick-extraction-loot-unit','minimum-loot-unit'].includes(v.role)).map(v=>({role:v.role,x:v.x,cy:v.cy}));
 }
 report.push(record);
}
await fs.writeFile(path.join(root,'review/placement-record.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify(report.map(p=>({...p,art:p.art.slice(0,2)}))));
