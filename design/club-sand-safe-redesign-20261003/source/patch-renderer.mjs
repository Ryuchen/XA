import fs from 'node:fs/promises';import path from 'node:path';import {fileURLToPath}from'node:url';
const project=path.resolve(process.argv[2]);const file=path.join(project,'source/render.mjs');let s=await fs.readFile(file,'utf8');
s=s.replace("console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(here,'outline-text.ps1'),'-InputFile',input,'-OutputFile',output],{encoding:'utf8'}).trim());","const quote=x=>\"'\"+x.replaceAll(\"'\",\"''\")+\"'\"; const command='& ([scriptblock]::Create([System.IO.File]::ReadAllText('+quote(path.join(here,'outline-text.ps1'))+'))) -InputFile '+quote(input)+' -OutputFile '+quote(output); console.log(execFileSync('powershell.exe',['-NoProfile','-Command',command],{encoding:'utf8'}).trim());");
s=s.replace("bounds.push({text:e.text,role:e.role,group:e.group,lineGroup:e.lineGroup,","bounds.push({elementId:e.id,semanticGroup:e.semanticGroup,priceAxis:e.priceAxis,axisKind:e.axisKind,text:e.text,role:e.role,group:e.group,lineGroup:e.lineGroup,");
s=s.replace("role:r.bold?'key-condition':e.role,group:e.group,lineGroup:e.group","fill:r.fill||e.fill,semanticGroup:e.semanticGroup,role:r.bold?'key-condition':e.role,group:e.group,lineGroup:e.group");
s=s.replace("bounds.push({text:e.text,role:'art-title'","bounds.push({semanticGroup:e.semanticGroup,text:e.text,role:'art-title'");
s=s.replace("await sharp(Buffer.from(svg),{limitInputPixels:false}).withMetadata({density:300}).png({compressionLevel:9}).toFile(path.join(project,files.png));","await fs.writeFile(path.join(project,files.png), await sharp(Buffer.from(svg),{limitInputPixels:false}).withMetadata({density:300}).png({compressionLevel:9}).toBuffer());");
s=s.replace("await sharp(Buffer.from(svg),{limitInputPixels:false}).resize({width:Math.min(p.w,1280)}).png().toFile(path.join(project,files.preview));","await fs.writeFile(path.join(project,files.preview), await sharp(Buffer.from(svg),{limitInputPixels:false}).resize({width:Math.min(p.w,1280)}).png().toBuffer());");
const before="    // Compose each line on one font baseline, then centre the complete line's ink.";
const insert=`
    const axes=new Map();
    for(const e of p.elements.filter(e=>e.priceAxis)){
      if(!axes.has(e.priceAxis))axes.set(e.priceAxis,[]);
      axes.get(e.priceAxis).push(e);
    }
    for(const es of axes.values()){
      const ns=es.filter(e=>e.axisKind==='number'),u=es.find(e=>e.axisKind==='unit');
      const numWidth=Math.max(...ns.map(e=>glyphs[e.id].width*e.size/100)),unitWidth=glyphs[u.id].width*u.size/100,gap=9;
      const center=u.axisRect.x+u.axisRect.width/2,unitX=center+(numWidth+gap-unitWidth)/2;
      es.forEach(e=>e.x=e.axisKind==='unit'?unitX:unitX-gap);
    }
`;
s=s.replace(before,insert+before);
const marker="    const bgRoot=path.resolve(project,'assets/backgrounds')";
const groupCode=`
    function measure(){const b=[];for(const e of p.elements){if(e.type==='text')textElement(e,false,b);else if(e.type==='rich')richElement(e,false,b);else if(e.type==='art')artElement(e,b);}return b;}
    const groupAudit=[];
    for(const group of p.textGroups||[]){
      let bs=measure().filter(b=>b.semanticGroup===group.id);
      if(!bs.length)throw new Error('Empty semantic group '+group.id);
      const box=bs=>({x:Math.min(...bs.map(b=>b.x)),y:Math.min(...bs.map(b=>b.y)),right:Math.max(...bs.map(b=>b.x+b.width)),bottom:Math.max(...bs.map(b=>b.y+b.height))});
      const ink=box(bs),r=group.safeRect;
      const dy=r.y+r.height/2-(ink.y+ink.bottom)/2;
      const dx=group.horizontal==='center'?r.x+r.width/2-(ink.x+ink.right)/2:0;
      for(const e of p.elements.filter(e=>e.semanticGroup===group.id)){e.x+=dx;e.cy+=dy;if(e.baseline!==undefined)e.baseline+=dy;}
      bs=measure().filter(b=>b.semanticGroup===group.id);const after=box(bs);
      const margins={left:after.x-r.x,right:r.x+r.width-after.right,top:after.y-r.y,bottom:r.y+r.height-after.bottom};
      groupAudit.push({id:group.id,safeRect:r,containerRect:group.containerRect,ink:after,margins,dy,dx});
      if(Object.values(margins).some(v=>v<-.1))throw new Error(p.id+' '+group.id+' exceeds measured safe region '+JSON.stringify(margins));
    }
    await fs.writeFile(path.join(project,'review',p.id+'-group-audit.json'),JSON.stringify(groupAudit,null,2));
`;
s=s.replace(marker,groupCode+marker);
await fs.writeFile(file,s);console.log('Portable rendering, fixed unit axes and measured group centering enabled.');
