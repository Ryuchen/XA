import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const root=path.dirname(fileURLToPath(import.meta.url)),raw=path.join(root,'03-backgrounds'),out=path.join(root,'04-png-export'),review=path.join(root,'review');
await fs.mkdir(out,{recursive:true});await fs.mkdir(review,{recursive:true});
const jobs=[
 {id:'desktop-absolute',label:'端游 · 绝密',src:'poster-gold.png',width:3200,height:7600},
 {id:'desktop-secret',label:'端游 · 机密',src:'poster-teal.png',width:3200,height:7600},
 {id:'mobile-absolute',label:'手游 · 绝密',src:'poster-gold.png',width:3200,height:7600},
 {id:'mobile-secret',label:'手游 · 机密',src:'poster-teal.png',width:3200,height:7600},
 {id:'cover',label:'横版封面',src:'cover.png',width:3200,height:1800},
 {id:'social',label:'简洁宣发海报',src:'social.png',width:2400,height:3200}
];
const exports=[],rendered=new Map();
for(const j of jobs){
 const m=await sharp(path.join(raw,j.src)).metadata(),key=j.src+'@'+j.width+'x'+j.height;
 const dest=path.join(out,j.id+'-background.png');
 if(rendered.has(key))await fs.copyFile(rendered.get(key),dest);
 else{await sharp(path.join(raw,j.src)).resize(j.width,j.height,{fit:'fill',kernel:'lanczos3'}).withMetadata({density:300}).png({compressionLevel:9}).toFile(dest);rendered.set(key,dest);}
 exports.push({...j,file:'04-png-export/'+j.id+'-background.png',nativeWidth:m.width,nativeHeight:m.height,method:'内置图像生成；按目标尺寸 Lanczos3 放大导出；无正式文案'});
}
await fs.writeFile(path.join(review,'export-manifest.json'),JSON.stringify(exports,null,2));
const data=async file=>'data:image/png;base64,'+(await fs.readFile(file)).toString('base64');
const img=(src,x,y,w,h)=>`<image href="${src}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const tx=(x,y,s,size=26,color='#284351')=>`<text x="${x}" y="${y}" fill="${color}" font-family="Microsoft YaHei UI" font-size="${size}" font-weight="600" dominant-baseline="central">${s}</text>`;
const refs={};for(const[k,f]of Object.entries({gold:'poster-gold.png',teal:'poster-teal.png',cover:'cover.png',social:'social.png'}))refs[k]=await data(path.join(raw,f));
let overview=`<svg xmlns="http://www.w3.org/2000/svg" width="2200" height="2010"><rect width="2200" height="2010" fill="#F8F6EF"/>${tx(60,65,'兴安电竞 · 限时清图单 · 底图确认',44)}${tx(60,128,'二次元手绘 ｜ 六档价目 ｜ 清图方式与最低要求分区呈现',27,'#728791')}`;
overview+=tx(60,205,'绝密价目 · 暖金',30)+tx(780,205,'机密价目 · 薄荷青',30)+tx(1490,205,'横版封面 · 16:9',30);
overview+=img(refs.gold,60,255,665,1579.375)+img(refs.teal,780,255,665,1579.375)+img(refs.cover,1490,255,650,365.625);
overview+=tx(1490,685,'简洁宣发 · 3:4',30)+img(refs.social,1490,735,650,866.667);
overview+=tx(1490,1670,'四类价目，共24档。',25)+tx(1490,1720,'宣发版省去完整下单须知。',25);
overview+=tx(60,1900,'端游、手游复用对应色系底图；填字时区分平台标签与价格。',27)+tx(60,1960,'本页标题为确认标注；单张底图均无正式文案。',25,'#728791')+'</svg>';
await fs.writeFile(path.join(review,'background-overview.svg'),overview);await sharp(Buffer.from(overview)).png().toFile(path.join(review,'background-overview.png'));
let lo=`<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="2260"><rect width="1800" height="2260" fill="#F8F6EF"/>${tx(55,65,'限时清图单 · 排版规划',44)}${tx(55,130,'以下为布局说明；正式艺术字与文案在底图确认后填入。',29)}`;
lo+=img(await data(path.join(root,'02-layouts/poster-layout.png')),55,200,790,1876.25)+img(await data(path.join(root,'02-layouts/cover-layout.png')),900,200,845,475.3)+img(await data(path.join(root,'02-layouts/social-layout.png')),900,750,845,1126.7);
lo+=tx(900,1950,'r、分钟、w均使用固定单位列。',27)+tx(900,2010,'数字、单位和文案在行内垂直居中。',27)+tx(55,2180,'填字时按生成底图实测边界重新定位，保证清晰和安全间距。',29)+'</svg>';
await sharp(Buffer.from(lo)).png().toFile(path.join(review,'layout-overview.png'));
const sources=[{title:'绝密价目底图',src:'poster-gold.png',ids:['desktop-absolute','mobile-absolute']},{title:'机密价目底图',src:'poster-teal.png',ids:['desktop-secret','mobile-secret']},{title:'限时清图单封面',src:'cover.png',ids:['cover']},{title:'简洁宣发海报',src:'social.png',ids:['social']}];
const cards=sources.map(s=>`<section><h2>${s.title}</h2><a href="../03-backgrounds/${s.src}"><img loading="lazy" src="../03-backgrounds/${s.src}" alt="${s.title}"></a><p>${s.ids.map(id=>{const j=jobs.find(j=>j.id===id);return `<a download href="../04-png-export/${id}-background.png">${j.label} · ${j.width}×${j.height} PNG</a>`}).join('<br>')}</p></section>`).join('');
const sizes=exports.filter(j=>['desktop-absolute','desktop-secret','cover','social'].includes(j.id)).map(j=>`${j.label}原图 ${j.nativeWidth}×${j.nativeHeight}`).join('；');
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞限时清图单 · 底图确认</title><style>*{box-sizing:border-box}body{background:#F8F6EF;color:#244252;font:16px/1.8 'Microsoft YaHei',sans-serif;margin:0}main{max-width:1500px;margin:auto;padding:40px}h1{margin:0;font-size:34px}h2{font-size:24px}p{color:#657D89}a{color:#227B80}nav{display:flex;gap:14px;margin:25px 0;flex-wrap:wrap}button{padding:12px 22px;background:white;border:1px solid #A7BCC6;border-radius:10px;font:inherit;cursor:pointer}button.active{background:#244252;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:35px}img{width:100%;display:block}#layout{display:none;max-width:1200px;margin:auto}footer{border-top:1px solid #BCCDD4;margin-top:40px;padding-top:20px;font-size:14px}section{align-self:start}@media(max-width:700px){main{padding:20px}.grid{grid-template-columns:1fr}}</style><main><h1>限时清图单 · 底图确认</h1><p>沿用已确认的冲刺蜂医、灰鼠通关旗、大秒表和医疗无人机。六档价目、两种清图方式、最低要求及四组须知分别预留文字区；确认后再加入艺术字与矢量文案。</p><p><a download href="../campaign-timed-clear-backgrounds-v1.zip">下载本阶段全部底图与布局文件</a></p><nav><button id="a" class="active">查看底图</button><button id="b">查看排版规划</button></nav><div id="art" class="grid">${cards}</div><div id="layout"><img src="layout-overview.png" alt="排版布局规划"></div><footer>${sizes}。高清尺寸导出为放大版本，原始生成图完整保留。<br>端游、手游本阶段复用对应模式色系，下一阶段通过各自价格及标签区分。<br><a href="../03-backgrounds/prompts.md">内置 image_gen 提示词</a> · <a href="../03-backgrounds/prompt-teal-variant.md">机密配色提示词</a> · <a href="export-manifest.json">分辨率清单</a></footer></main><script>const a=document.getElementById('a'),b=document.getElementById('b'),art=document.getElementById('art'),l=document.getElementById('layout');a.onclick=()=>{art.style.display='grid';l.style.display='none';a.className='active';b.className=''};b.onclick=()=>{art.style.display='none';l.style.display='block';b.className='active';a.className=''};</script></html>`;
await fs.writeFile(path.join(review,'index.html'),html);await fs.writeFile(path.join(review,'backgrounds.html'),html);
await fs.writeFile(path.join(root,'STAGE-2-README.md'),`# 限时清图单 · 无字底图确认

本阶段完成四张价目底图、封面、简洁宣发，共六张高清尺寸PNG。端手游复用对应模式色系，填字时区分平台和价格。

- 绝密暖金、机密薄荷青，所有价目及分区表头整条深蓝。
- 完整版价目六档，常规与16/14/12/10/8分钟；重复服务说明放在共用提示条。
- 两种清图方式使用左右两卡，中央圆章预留“或”；最低要求用独立横条。
- 四组须知预留不同高度，以容纳地图、累计补偿、清图后开房卡及缺卡补偿、速撤条件。
- 宣发四卡六行，共24档，省去完整下单须知。
- 人物、秒表、火箭发射场与边缘道具均为二次元手绘风格。

## 文件

- 03-backgrounds：生成原图与完整提示词。
- 04-png-export：3200×7600价目、3200×1800封面、2400×3200宣发。
- review/index.html：底图与布局查看页。
- 02-layouts：结构稿、标注稿、规划和坐标。
- content.json：唯一业务数据源，24档价目及本轮规则。

${sizes}。最终尺寸PNG由Lanczos3放大导出，不能称为原生高清绘制。

## 后续

按 xingan-gameplay-posters 的分阶段流程，等待本轮底图确认后再添加矢量艺术字与正文。机密规则采用用户补充文本，绝密沿用本轮参考图。填字以实际生成面板位置为准，数字和单位分开定位，按可见字形垂直居中。
`);
const stateFile=path.join(root,'campaign-state.json'),state=JSON.parse(await fs.readFile(stateFile,'utf8'));
state.stage='background-awaiting-user-approval';state.revision='timed-clear-backgrounds-v1';
state.approvals.background={status:'awaiting-user-approval',basis:'无字底图生成并完成面板行数和留白检查，等待本轮用户确认',overview:'review/background-overview.png',review:'review/backgrounds.html'};
state.backgrounds={gold:'03-backgrounds/poster-gold.png',teal:'03-backgrounds/poster-teal.png',cover:'03-backgrounds/cover.png',social:'03-backgrounds/social.png'};
state.exports=exports;state.backgroundPackage='campaign-timed-clear-backgrounds-v1.zip';
state.nextStep='用户确认本轮底图后，按实际面板边界定位艺术字、24档价格、时限及清图规则，交付PNG与可编辑/转曲SVG';
await fs.writeFile(stateFile,JSON.stringify(state,null,2));
console.log(JSON.stringify({exports:exports.map(j=>({id:j.id,native:[j.nativeWidth,j.nativeHeight],export:[j.width,j.height]})),review:'review/index.html'}));
