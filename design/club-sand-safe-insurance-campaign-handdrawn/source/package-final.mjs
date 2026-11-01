import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=async f=>JSON.parse(await fs.readFile(path.join(root,f),'utf8'));
const revision='sand-safe-insurance-v1',prefix='campaign-'+revision;
const manifest=await read('output/manifest.json'),state=await read('campaign-state.json');
state.stage='delivered';state.revision=revision;
state.finalDeliverables={manifest:'output/manifest.json',pngPackage:'output/'+prefix+'-png.zip',sourcePackage:'output/'+prefix+'-source.zip',review:'review/index.html',overview:'review/final-overview.png',pages:manifest.map(v=>v.id)};
state.visualQA={status:'complete',revision,record:'review/visual-qa.md',dataAndGeometryAudit:'review/typesetting-audit.json',basis:'逐张查看实际PNG、准确390px预览和价目、奖励、规则局部；最新累计规则改为明确物资主体后复查两张完整页。'};
state.nextStep='后续改价或文字复用当前美术、真实字体轮廓和布局，只重渲染受影响页面与宣发；每次检查实际改动成图。';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
const hashes={revision,files:[]};
for(const p of manifest)for(const key of ['png','outlinedSVG','editableSVG'])hashes.files.push({id:p.id,type:key,path:p[key],sha256:createHash('sha256').update(await fs.readFile(path.join(root,p[key]))).digest('hex')});
await fs.writeFile(path.join(root,'output/final-files.json'),JSON.stringify(hashes,null,2)+'\n');
await fs.writeFile(path.join(root,'output/DELIVERY.md'),'# 兴安电竞 · 沙色保险单\n\n版本：'+revision+'。只有绝密，端手游各三档。三项同时达标结单，三幻神奖励整单一次，赠送不计套餐次数。\n\n'+manifest.map(p=>'- '+p.label+'：'+path.basename(p.png)+'，'+p.width+'×'+p.height).join('\n')+'\n\n价格与规则已经矢量填入；重点真实粗体，普通说明常规字重。可改文字SVG保留正文与数字对象，艺术标题为路径；转曲SVG不依赖字体，背景插画为内嵌位图。文字按目标尺寸直接渲染，背景原生分辨率另行记录。\n');
const nonce=Date.now().toString(36),pngStage=path.join(root,'output','delivery-'+nonce+'-png'),srcStage=path.join(root,'output','delivery-'+nonce+'-source');
const copy=async(src,dst)=>{await fs.mkdir(path.dirname(dst),{recursive:true});await fs.cp(path.join(root,src),dst,{recursive:true});};
await fs.mkdir(pngStage,{recursive:true});await fs.mkdir(srcStage,{recursive:true});
for(const p of manifest)await copy(p.png,path.join(pngStage,path.basename(p.png)));
await copy('output/DELIVERY.md',path.join(pngStage,'DELIVERY.md'));
const files=['README.md','content.json','campaign-state.json','layout.json','layout-adapter.mjs','references/rules-confirmation.md','02-layouts/generated-background-measurements.json','03-backgrounds/image-metadata.json','03-backgrounds/prompt-poster.md','03-backgrounds/prompt-cover.md','03-backgrounds/prompt-social.md','03-backgrounds/reward-divider-edit-prompts.md','assets/backgrounds','output/png','output/svg-editable','output/svg-outlined','output/manifest.json','output/final-files.json','output/DELIVERY.md','source/typeset.mjs','source/render.mjs','source/outline-text.ps1','source/review-output.mjs','source/package-final.mjs','.cache/all-text-outlines.json','.cache/font-outlines.json','review/index.html','review/final-overview.png','review/typesetting-audit.json','review/typesetting-placements.json','review/visual-qa.md'];
for(const p of manifest)files.push(p.preview,`review/${p.id}-text-bounds.json`,`review/phone-390/${p.id}.png`);
for(const name of await fs.readdir(path.join(root,'review/details')))if(name.endsWith('.png')&&manifest.some(v=>name.startsWith(v.id+'-')))files.push('review/details/'+name);
for(const f of files)await copy(f,path.join(srcStage,f));
let html=await fs.readFile(path.join(srcStage,'review/index.html'),'utf8');
html=html.replace(/<a href="\.\.\/output\/campaign-sand-safe-insurance-v1-(?:png|source)\.zip">[^<]+<\/a>/g,'');
await fs.writeFile(path.join(srcStage,'review/index.html'),html);
const quote=s=>"'"+s.replace(/'/g,"''")+"'";
const packages=[];
for(const [stage,type] of [[pngStage,'png'],[srcStage,'source']]) {
 const out=path.join(root,'output',prefix+'-'+type+'.zip');
 try{await fs.access(out);throw Error('Target already exists; use a new revision: '+out);}catch(e){if(e.code!=='ENOENT')throw e;}
 execFileSync('powershell.exe',['-NoProfile','-Command','Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('+quote(stage)+','+quote(out)+',[System.IO.Compression.CompressionLevel]::Optimal,$false)'],{encoding:'utf8'});
 packages.push({path:out,bytes:(await fs.stat(out)).size});
}
console.log(JSON.stringify({revision,pages:manifest.length,packages},null,2));
