import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const [project]=process.argv.slice(2);
const c=JSON.parse(await fs.readFile(path.join(project,'content.json'),'utf8'));
const m=JSON.parse(await fs.readFile(path.join(project,'02-layouts/final-background-measurements.json'),'utf8'));
const {makeLayout}=await import(pathToFileURL(path.join(project,'layout-adapter.mjs')));
const initial=makeLayout(c,m,{});
const input=path.join(project,'02-layouts/final-font-requests.json'),output=path.join(project,'02-layouts/final-font-outlines.json');
let glyphs={};
try{glyphs=JSON.parse(await fs.readFile(output,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
const missing=initial.requests.filter(r=>!glyphs[r.id]||glyphs[r.id].text!==r.text||glyphs[r.id].font!==r.font||glyphs[r.id].style!==r.style);
if(missing.length){
  await fs.writeFile(input,JSON.stringify(missing));
  const partial=path.join(project,'02-layouts/final-font-new-outlines.json');
  console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(project,'source/outline-text.ps1'),'-InputFile',input,'-OutputFile',partial],{encoding:'utf8'}).trim());
  Object.assign(glyphs,JSON.parse(await fs.readFile(partial,'utf8')));
  await fs.writeFile(output,JSON.stringify(glyphs));
}else console.log('Reused all '+initial.requests.length+' text outlines.');
const plan=makeLayout(c,m,glyphs);
await fs.writeFile(path.join(project,'layout.json'),JSON.stringify(plan,null,2)+'\n');
await fs.writeFile(path.join(project,'review/final-placements.json'),JSON.stringify(plan.placements,null,2)+'\n');
await fs.mkdir(path.join(project,'.cache'),{recursive:true});
const seed={};
for(const r of plan.requests){const key=createHash('sha256').update(JSON.stringify([r.text,r.font,r.style])).digest('hex');seed[key]=glyphs[r.id];}
await fs.writeFile(path.join(project,'.cache/font-outlines.json'),JSON.stringify(seed));
console.log(JSON.stringify({pages:plan.pages.length,strings:plan.requests.length,groups:plan.placements.length,phoneBodyPx:31*390/836}));
