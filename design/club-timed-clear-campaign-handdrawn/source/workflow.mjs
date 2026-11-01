import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {renderProject} from './render.mjs';
const here=path.dirname(fileURLToPath(import.meta.url));
const readJSON=async file=>JSON.parse((await fs.readFile(file,'utf8')).replace(/^\uFEFF/,''));
const exists=async file=>fs.access(file).then(()=>true,()=>false);
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const [command,...rest]=process.argv.slice(2),args={};
for(let i=0;i<rest.length;i++){
  const key=rest[i];if(!key.startsWith('--'))throw new Error('未知参数 '+key);
  if(key==='--force')args.force=true;
  else {if(!rest[i+1]||rest[i+1].startsWith('--'))throw new Error('参数缺少值 '+key);args[key.slice(2)]=rest[++i];}
}
if(!command||command==='help'){
  console.log('workflow.mjs init|layout|render|package --project <absolute-directory> [--only id1,id2] [--modules node_modules] [--force] [--revision r2]');
  process.exit(0);
}
if(!args.project||!path.isAbsolute(args.project))throw new Error('--project 需要绝对目录');
const project=path.resolve(args.project);
if(command==='init'){
  if(await exists(project))throw new Error('项目已存在；请继续使用 content/layout/state，init 不覆盖旧目录。');
  const preset=path.resolve(here,'../assets/material-v3');
  await fs.mkdir(project,{recursive:true});
  await fs.mkdir(path.join(project,'assets'),{recursive:true});
  await fs.cp(path.join(preset,'backgrounds'),path.join(project,'assets/backgrounds'),{recursive:true});
  await fs.copyFile(path.join(preset,'content.example.json'),path.join(project,'content.json'));
  await fs.copyFile(path.join(here,'layout-material.mjs'),path.join(project,'layout-adapter.mjs'));
  await fs.mkdir(path.join(project,'source'),{recursive:true});
  for(const name of ['workflow.mjs','render.mjs','outline-text.ps1','pack.ps1'])await fs.copyFile(path.join(here,name),path.join(project,'source',name));
  await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify({skill:'xingan-gameplay-posters',preset:'material-v3-approved-r2',stage:'content-intake',contentVerified:false,contentVerificationBasis:'',approvals:{illustration:{status:'approved-reference',basis:'用户已确认的兴安电竞物资系列；新美术需记录本轮确认'},background:{status:'approved-reference',basis:'仅复用同一底图和结构时有效；更换底图后更新状态'}},revision:'draft'},null,2));
  console.log(JSON.stringify({project,next:'核对本轮内容并记录来源；复用历史样例需有用户依据。随后运行 layout，再 render。'}));
}else if(command==='layout'){
  const content=await readJSON(path.join(project,'content.json'));
  const adapter=await import(pathToFileURL(path.join(project,'layout-adapter.mjs')).href);
  const plan=adapter.makeLayout(content);
  await fs.writeFile(path.join(project,'layout.json'),JSON.stringify(plan,null,2));
  console.log('Prepared '+plan.pages.length+' pages; '+plan.requests.length+' distinct strings.');
}else if(command==='render'){
  await renderProject(project,args);
}else if(command==='package'){
  const revision=args.revision||'r1';
  if(!/^[a-zA-Z0-9._-]+$/.test(revision))throw new Error('revision 只能包含字母数字、点、下划线与短横线');
  // Refresh only pages whose fingerprints changed; prevent stale companion ads.
  await renderProject(project,{modules:args.modules});
  const manifest=await readJSON(path.join(project,'output/manifest.json'));
  const plan=await readJSON(path.join(project,'layout.json'));
  const missing=plan.pages.filter(p=>!manifest.some(r=>r.id===p.id));
  if(missing.length)throw new Error('尚未导出以下页面: '+missing.map(p=>p.id).join(', '));
  const cards=manifest.map(p=>`<section><h2>${esc(p.label)}</h2><a href="../${esc(p.png)}"><img src="${esc(p.id)}.png" alt="${esc(p.label)}"></a><p><a download href="../${esc(p.png)}">PNG ${p.width}×${p.height}</a> · <a download href="../${esc(p.outlinedSVG)}">转曲 SVG</a> · <a download href="../${esc(p.editableSVG)}">可改文字 SVG</a></p></section>`).join('');
  const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞 · 限时清图单</title><style>body{margin:0;background:#f8f7f0;color:#213f53;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1500px;padding:30px;margin:auto}.grid{display:grid;grid-template-columns:1fr 1fr;gap:28px}img{width:100%;height:auto;display:block}a{color:#187c78}h2{font-size:22px}@media(max-width:700px){.grid{grid-template-columns:1fr}}</style><main><h1>兴安电竞 · 限时清图单</h1><p><a href="../campaign-${esc(revision)}.zip">全部成品</a></p><div class="grid">${cards}</div><p>标题、正文、价格有矢量源文件；背景为嵌入位图。放大导出的背景不代表原生绘制分辨率。可改文字版本依赖所用字体，跨设备保持版式请用转曲版本。</p></main></html>`;
  await fs.writeFile(path.join(project,'review/index.html'),html);
  const state=await readJSON(path.join(project,'campaign-state.json'));state.revision=revision;state.stage='delivered';
  await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2));
  await fs.writeFile(path.join(project,'output/DELIVERY.md'),`# 兴安电竞海报 ${revision}\n\nPNG / 转曲 SVG / 可改文字 SVG 已保留。\n\n${manifest.map(p=>`- ${p.label}：${p.width}×${p.height}；底图原生 ${p.nativeWidth}×${p.nativeHeight}。`).join('\n')}\n\n文字在目标尺寸由矢量渲染；插画为位图。可改文字版本使用 Microsoft YaHei UI / Bahnschrift 等本机字体，标题保留路径。\n`);
  if(process.platform!=='win32')throw new Error('当前打包器使用 Windows PowerShell；文件与查看页已保留，可使用本机 ZIP 工具打包。');
  console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(here,'pack.ps1'),'-Project',project,'-Revision',revision],{encoding:'utf8'}).trim());
}else throw new Error('未知命令 '+command);
