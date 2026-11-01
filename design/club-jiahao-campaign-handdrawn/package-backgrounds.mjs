import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const root=path.dirname(fileURLToPath(import.meta.url)),raw=path.join(root,'03-backgrounds'),out=path.join(root,'04-png-export'),review=path.join(root,'review');
await fs.mkdir(out,{recursive:true});await fs.mkdir(review,{recursive:true});
const jobs=[
 {id:'desktop-absolute',label:'端游 · 绝密',src:'poster-gold.png',width:3200,height:7200},
 {id:'desktop-secret',label:'端游 · 机密',src:'poster-teal.png',width:3200,height:7200},
 {id:'mobile-absolute',label:'手游 · 绝密',src:'poster-gold.png',width:3200,height:7200},
 {id:'mobile-secret',label:'手游 · 机密',src:'poster-teal.png',width:3200,height:7200},
 {id:'cover',label:'横版封面',src:'cover.png',width:3200,height:1800},
 {id:'social',label:'简洁广告海报',src:'social.png',width:2400,height:3200}
];
const exports=[];
for(const j of jobs){const m=await sharp(path.join(raw,j.src)).metadata();await sharp(path.join(raw,j.src)).resize(j.width,j.height,{fit:'fill',kernel:'lanczos3'}).withMetadata({density:300}).png({compressionLevel:9}).toFile(path.join(out,j.id+'-background.png'));exports.push({...j,file:'04-png-export/'+j.id+'-background.png',nativeWidth:m.width,nativeHeight:m.height,method:'内置图像生成；按目标尺寸 Lanczos3 放大导出；无正式文案'});}
await fs.writeFile(path.join(review,'export-manifest.json'),JSON.stringify(exports,null,2));
const data=async(file)=>'data:image/png;base64,'+(await fs.readFile(file)).toString('base64');
const img=(src,x,y,w,h)=>`<image href="${src}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const text=(x,y,s,size=26,color='#284351')=>`<text x="${x}" y="${y}" fill="${color}" font-family="Microsoft YaHei UI" font-size="${size}" font-weight="600" dominant-baseline="central">${s}</text>`;
const refs={};for(const[k,f]of Object.entries({gold:'poster-gold.png',teal:'poster-teal.png',cover:'cover.png',social:'social.png'}))refs[k]=await data(path.join(raw,f));
let overview=`<svg xmlns="http://www.w3.org/2000/svg" width="2200" height="1940"><rect width="2200" height="1940" fill="#f8f6ef"/>${text(60,65,'兴安电竞 · 嘉豪物资单 · 底图确认',44)}${text(60,128,'二次元手绘 ｜ 四档两列 ｜ 插画已确认，文案待下一阶段添加',27,'#728791')}`;
overview+=text(60,205,'绝密价目 · 暖金',30)+text(780,205,'机密价目 · 薄荷青',30)+text(1490,205,'横版封面 · 16:9',30);
overview+=img(refs.gold,60,255,665,1496.25)+img(refs.teal,780,255,665,1496.25)+img(refs.cover,1490,255,650,365.625);
overview+=text(1490,685,'简洁广告 · 3:4',30)+img(refs.social,1490,735,650,866.667);
overview+=text(1490,1670,'四类价目，各四档。',25)+text(1490,1720,'广告版无下单须知。',25);
overview+=text(60,1825,'端游、手游复用对应色系底图；后续按各自价格和平台标签区分。',27)+text(60,1883,'本页标题是确认标注；单张底图均无正式文案。',25,'#728791')+'</svg>';
await fs.writeFile(path.join(review,'background-overview.svg'),overview);await sharp(Buffer.from(overview)).png().toFile(path.join(review,'background-overview.png'));
let lo=`<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="2180"><rect width="1800" height="2180" fill="#f8f6ef"/>${text(55,65,'嘉豪物资单 · 排版规划',44)}${text(55,130,'以下为布局说明，正式文案将在底图确认后填入。',29)}`;
lo+=img(await data(path.join(root,'02-layouts/poster-layout.png')),55,200,790,1777.5)+img(await data(path.join(root,'02-layouts/cover-layout.png')),900,200,845,475.3)+img(await data(path.join(root,'02-layouts/social-layout.png')),900,750,845,1126.7)+text(900,1950,'r、w 各自固定锚点，单位放大。',27)+text(900,2010,'每组文案在所在区域垂直居中。',27)+text(55,2100,'填字时根据生成底图实测位置重新校准，避免插画与数字挤占空间。',29)+'</svg>';
await sharp(Buffer.from(lo)).png().toFile(path.join(review,'layout-overview.png'));
const sources=[{title:'绝密价目底图',src:'poster-gold.png',ids:['desktop-absolute','mobile-absolute']},{title:'机密价目底图',src:'poster-teal.png',ids:['desktop-secret','mobile-secret']},{title:'嘉豪主题封面',src:'cover.png',ids:['cover']},{title:'简洁宣发海报',src:'social.png',ids:['social']}];
const cards=sources.map(s=>`<section><h2>${s.title}</h2><a href="../03-backgrounds/${s.src}"><img loading="lazy" src="../03-backgrounds/${s.src}" alt="${s.title}"></a><p>${s.ids.map(id=>{const j=jobs.find(j=>j.id===id);return `<a download href="../04-png-export/${id}-background.png">${j.label} · ${j.width}×${j.height} PNG</a>`}).join('<br>')}</p></section>`).join('');
const sizes=exports.filter(j=>['desktop-absolute','desktop-secret','cover','social'].includes(j.id)).map(j=>`${j.label}原图 ${j.nativeWidth}×${j.nativeHeight}`).join('；');
await fs.writeFile(path.join(review,'index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞嘉豪单 · 底图确认</title><style>*{box-sizing:border-box}body{background:#f8f6ef;color:#244252;font:16px/1.8 "Microsoft YaHei",sans-serif;margin:0}main{max-width:1500px;margin:auto;padding:40px}h1{margin:0;font-size:34px}h2{font-size:24px}p{color:#657d89}a{color:#227b80}nav{display:flex;gap:14px;margin:25px 0}button{padding:12px 22px;background:white;border:1px solid #a7bcc6;border-radius:10px;font:inherit;cursor:pointer}button.active{background:#244252;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:35px}img{width:100%;display:block}#layout{display:none;max-width:1200px;margin:auto}footer{border-top:1px solid #bccdd4;margin-top:40px;padding-top:20px;font-size:14px}section{align-self:start}@media(max-width:700px){main{padding:20px}.grid{grid-template-columns:1fr}}</style><main><h1>嘉豪物资单 · 底图确认</h1><p>已沿用确认的蜂医、得吃灰鼠与航天清图插画。价目四档两列；保障、清图方式与炸单条件独立分区。确认底图后再填艺术字和矢量文案。</p><nav><button id="a" class="active">查看底图</button><button id="b">查看排版规划</button></nav><div id="art" class="grid">${cards}</div><div id="layout"><img src="layout-overview.png" alt="排版布局规划"></div><footer>${sizes}。高清导出为放大版本，原始生成图完整保留。<br>端游和手游在本阶段复用对应色系，下一阶段以各自价格与平台标签区分。<br><a href="../03-backgrounds/prompts.md">内置 image_gen 完整提示词</a> · <a href="export-manifest.json">分辨率清单</a></footer></main><script>let a=document.getElementById('a'),b=document.getElementById('b'),art=document.getElementById('art'),l=document.getElementById('layout');a.onclick=()=>{art.style.display='grid';l.style.display='none';a.className='active';b.className=''};b.onclick=()=>{art.style.display='none';l.style.display='block';b.className='active';a.className=''};</script></html>`);
await fs.writeFile(path.join(root,'STAGE-2-README.md'),`# 嘉豪物资单 · 无字底图确认\n\n本阶段完成四张价目底图、封面、简洁广告，共六张高清尺寸 PNG。端手游复用对应模式色系，填文案时区分价格。\n\n- 绝密暖金、机密薄荷青，价目表头整条深蓝。\n- 价目四档两列；主表无装饰图标挤占数字。\n- 保障、清图方式与炸单条件独立分区；须知三组。\n- 3:4 广告版四类价目各四档，不附加下单须知。\n- 插画与环境统一二次元手绘。\n\n## 文件\n\n- 03-backgrounds：生成原图和提示词。\n- 04-png-export：3200×7200 价目、3200×1800 封面、2400×3200 广告。\n- review/index.html：可切换底图与排版规划的本地查看页。\n- 02-layouts：空白结构稿、标注稿与布局坐标。\n\n${sizes}。高清导出为 Lanczos3 放大，不是原生高清生成。\n\n## 后续\n\n遵循 xingan-gameplay-posters 的分阶段流程及用户要求，等待本轮底图确认后，再添加矢量正文与艺术字。届时根据实际底图位置校准单位锚点、垂直居中和安全边距。业务数据唯一来源 content.json；清图、保障和须知已经用户确认端手游一致。\n`);
let state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));state.stage='background-awaiting-user-approval';state.approvals.background={status:'awaiting-user-approval',basis:'无字底图已生成，等待本轮用户确认',overview:'review/background-overview.png',review:'review/index.html'};state.backgrounds={gold:'03-backgrounds/poster-gold.png',teal:'03-backgrounds/poster-teal.png',cover:'03-backgrounds/cover.png',social:'03-backgrounds/social.png'};state.exports=exports;state.nextStep='用户确认底图后，按实际面板位置重新测量并填矢量艺术字及文案';await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({exports:exports.map(j=>({id:j.id,native:[j.nativeWidth,j.nativeHeight],export:[j.width,j.height]})),review:'review/index.html'}));
