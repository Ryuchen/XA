import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {makeLayout} from '../layout-adapter.mjs';
import {renderProject} from './render.mjs';
import {validateDeliveryScope} from './scope.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const content=await read('content.json'),state=await read('campaign-state.json'),metrics=await read('source/background-measurements.json');
const draft=makeLayout(content,metrics);
const cache=await read('.cache/font-outlines.json').catch(()=>({})),glyphs={},missing=[];
const hash=v=>createHash('sha256').update(v).digest('hex');
for(const r of draft.requests){const key=hash(JSON.stringify([r.text,r.font,r.style]));if(cache[key]?.advance)glyphs[r.id]=cache[key];else missing.push(r);}
if(missing.length){
 await fs.writeFile(path.join(root,'.cache/all-text-requests.json'),JSON.stringify(missing));
 console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(root,'source/outline-text.ps1'),'-InputFile',path.join(root,'.cache/all-text-requests.json'),'-OutputFile',path.join(root,'.cache/all-text-outlines.json')],{encoding:'utf8'}).trim());
 Object.assign(glyphs,await read('.cache/all-text-outlines.json'));
}
const plan=makeLayout(content,metrics,glyphs);
validateDeliveryScope(state,content,plan,{final:true});
for(const r of plan.requests)cache[hash(JSON.stringify([r.text,r.font,r.style]))]=glyphs[r.id];
await fs.writeFile(path.join(root,'.cache/font-outlines.json'),JSON.stringify(cache));
await fs.writeFile(path.join(root,'layout.json'),JSON.stringify(plan,null,2));
await fs.writeFile(path.join(root,'review/typesetting-placements.json'),JSON.stringify(plan.placements,null,2));
await renderProject(root,{only:process.argv.find(v=>v.startsWith('--only='))?.slice(7),modules:process.argv.find(v=>v.startsWith('--modules='))?.slice(10)});
