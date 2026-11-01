import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {makeLayout} from '../layout-adapter.mjs';
import {renderProject} from './render.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const hash=s=>createHash('sha256').update(s).digest('hex');
const state=await read(path.join(root,'campaign-state.json'));
state.approvals.background={...state.approvals.background,status:'approved',basis:'用户在四张无字底图、布局位置稿及查看页交付后回复：继续'};
state.stage='vector-production';state.revision='sand-safe-insurance-v1';
state.typographyPreference={basis:'用户要求第三步先理解文案，重点选择性加粗，PPT式组织权益与条件，减少换行，保留上下间距',bodyFont:'Microsoft YaHei UI Regular',keyFont:'Microsoft YaHei UI Bold',alignment:'真实字形及多行组居中，独立r/w轴，单位可见字高70%'};
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
const c=await read(path.join(root,'content.json')),m=await read(path.join(root,'02-layouts/generated-background-measurements.json'));
const initial=makeLayout(c,m);
await fs.mkdir(path.join(root,'.cache'),{recursive:true});
const input=path.join(root,'.cache','all-text-requests.json'),output=path.join(root,'.cache','all-text-outlines.json');
await fs.writeFile(input,JSON.stringify(initial.requests));
let glyphs={};try{glyphs=await read(output);}catch(e){if(e.code!=='ENOENT')throw e;}
const missing=initial.requests.filter(r=>!glyphs[r.id]||glyphs[r.id].text!==r.text||glyphs[r.id].font!==r.font||glyphs[r.id].style!==r.style);
if(missing.length) {
 const inp=path.join(root,'.cache','missing-text.json'),out=path.join(root,'.cache','missing-outlines.json');
 await fs.writeFile(inp,JSON.stringify(missing));
 console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(root,'source/outline-text.ps1'),'-InputFile',inp,'-OutputFile',out],{encoding:'utf8'}).trim());
 Object.assign(glyphs,await read(out));await fs.writeFile(output,JSON.stringify(glyphs));
}
const plan=makeLayout(c,m,glyphs),seed={};
for(const r of plan.requests)seed[hash(JSON.stringify([r.text,r.font,r.style]))]=glyphs[r.id];
await fs.writeFile(path.join(root,'.cache/font-outlines.json'),JSON.stringify(seed));
await fs.writeFile(path.join(root,'layout.json'),JSON.stringify(plan,null,2)+'\n');
await fs.writeFile(path.join(root,'review/typesetting-placements.json'),JSON.stringify(plan.placements,null,2)+'\n');
await renderProject(root,{only:process.argv.find(v=>v.startsWith('--only='))?.slice(7)});
console.log(JSON.stringify(plan.pages.map(p=>({id:p.id,priceSize:p.alignment.price?.size,priceUnitX:p.alignment.price?.unitX,lootSize:p.alignment.loot?.size,lootUnitX:p.alignment.loot?.unitX})),null,2));
