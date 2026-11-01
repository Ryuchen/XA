import fs from'node:fs/promises';import path from'node:path';const project=path.resolve(process.argv[2]),file=path.join(project,'source/render.mjs');let s=await fs.readFile(file,'utf8');
s=s.replace('    const groupAudit=[];',`
    const richAxes=new Map();
    for(const e of p.elements.filter(e=>e.type==='rich'&&e.unitAxis)){if(!richAxes.has(e.unitAxis))richAxes.set(e.unitAxis,[]);richAxes.get(e.unitAxis).push(e);}
    for(const es of richAxes.values()){const entries=es.map(e=>{const b=[];richElement(e,false,b);const unit=b.find(z=>z.text==='w');return{e,x:unit.x};});const x=Math.max(...entries.map(z=>z.x));for(const z of entries)z.e.x+=x-z.x;}
    const groupAudit=[];
`);await fs.writeFile(file,s);console.log('Combined loot unit columns aligned.');
