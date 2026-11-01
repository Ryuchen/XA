import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const archive=path.join(root,'archive/r1');
await fs.mkdir(archive,{recursive:true});
for(const name of ['output','layout.json','layout-adapter.mjs','review/index.html','review/visual-qa.md']){
 const target=path.join(archive,name);
 try{await fs.access(target);}catch{
  try{await fs.mkdir(path.dirname(target),{recursive:true});await fs.cp(path.join(root,name),target,{recursive:true});}catch(e){if(e.code!=='ENOENT')throw e;}
 }
}
await fs.copyFile(path.join(root,'source/layout-covers-r2.mjs'),path.join(root,'layout-adapter.mjs'));
const state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.approvals.background={...state.approvals.background,status:'approved',date:'2026-10-01',basis:'用户在三张新封面无字底图展示后明确回复“填入文字”，授权进入矢量排字。'};
state.deliveryScope.basis='用户先确认蜂医灰鼠插画并要求做封面，后确认新底图并回复“填入文字”；本轮完成三张封面，广告保留后续范围。';
state.revision='r2-covers';state.stage='cover-vector-typesetting';
state.memorySync={status:'pending',date:'2026-10-01'};
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log('Approved three cover backgrounds; historical output archived.');
