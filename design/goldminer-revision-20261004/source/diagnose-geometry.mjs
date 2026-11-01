import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const plan=JSON.parse(fs.readFileSync(path.join(root,'layout.json'),'utf8'));
const results=[];
for(const page of plan.pages){
 const bs=JSON.parse(fs.readFileSync(path.join(root,'review',page.id+'-text-bounds.json'),'utf8'));
 for(const g of page.auditGroups){
  const matched=bs.filter(b=>(b.group||'').startsWith(g.prefix));
  if(!matched.length){results.push({page:page.id,group:g.name,error:'missing bounds'});continue;}
  const x=Math.min(...matched.map(b=>b.x)),y=Math.min(...matched.map(b=>b.y)),r=Math.max(...matched.map(b=>b.x+b.width)),bt=Math.max(...matched.map(b=>b.y+b.height));
  const [gx,gy,gw,gh]=g.rect;
  results.push({page:page.id,group:g.name,prefix:g.prefix,box:[x,y,r-x,bt-y],safe:g.rect,dx:(x+r)/2-gx-gw/2,dy:(y+bt)/2-gy-gh/2,margins:[x-gx,y-gy,gx+gw-r,gy+gh-bt],outside:x<gx-.5||y<gy-.5||r>gx+gw+.5||bt>gy+gh+.5});
 }
}
fs.writeFileSync(path.join(root,'review','geometry-diagnostic.json'),JSON.stringify(results,null,2));
console.log(JSON.stringify(results.filter(r=>r.outside||Math.abs(r.dy)>2||r.prefix==='notices-'),null,2));

