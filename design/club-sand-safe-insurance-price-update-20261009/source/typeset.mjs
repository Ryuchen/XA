import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {makeLayout} from '../layout-adapter.mjs';
import {renderProject} from './render.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const c=await read('content.json'),m=await read('02-layouts/generated-background-measurements.json');
let glyphs=await read('.cache/all-text-outlines.json');
const initial=makeLayout(c,m),missing=initial.requests.filter(r=>!glyphs[r.id]?.advance);
if(missing.length) {
 const inp=path.join(root,'.cache/missing-text.json'),out=path.join(root,'.cache/missing-outlines.json');
 await fs.writeFile(inp,JSON.stringify(missing));
 console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(root,'source/outline-text.ps1'),'-InputFile',inp,'-OutputFile',out],{encoding:'utf8'}).trim());
 Object.assign(glyphs,JSON.parse(await fs.readFile(out,'utf8')));
 await fs.writeFile(path.join(root,'.cache/all-text-outlines.json'),JSON.stringify(glyphs));
}
const plan=makeLayout(c,m,glyphs),seed={};
for(const r of plan.requests)seed[createHash('sha256').update(JSON.stringify([r.text,r.font,r.style])).digest('hex')]=glyphs[r.id];
await fs.writeFile(path.join(root,'.cache/font-outlines.json'),JSON.stringify(seed));
await fs.writeFile(path.join(root,'layout.json'),JSON.stringify(plan,null,2)+'\n');
await fs.writeFile(path.join(root,'review/typesetting-placements.json'),JSON.stringify(plan.placements,null,2)+'\n');
const state=await read('campaign-state.json');state.stage='vector-production';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
await renderProject(root,{modules:process.argv.find(v=>v.startsWith('--modules='))?.slice(10)});
console.log(JSON.stringify(plan.pages.map(p=>({id:p.id,size:[p.ew,p.eh],price:p.sourceRows.map(r=>r.priceR)})),null,2));
