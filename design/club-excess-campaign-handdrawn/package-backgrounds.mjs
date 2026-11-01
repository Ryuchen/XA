import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const sharp=require('C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const root=path.dirname(fileURLToPath(import.meta.url)),raw=path.join(root,'03-backgrounds'),out=path.join(root,'04-png-export'),review=path.join(root,'review');
await fs.mkdir(out,{recursive:true});await fs.mkdir(review,{recursive:true});
const jobs=[
 {id:'desktop-absolute',label:'端游 · 绝密 · 五档',width:3200,height:7200,rows:5},
 {id:'desktop-secret',label:'端游 · 机密 · 五档',width:3200,height:7200,rows:5},
 {id:'mobile-absolute',label:'手游 · 绝密 · 四档',width:3200,height:7200,rows:4},
 {id:'mobile-secret',label:'手游 · 机密 · 四档',width:3200,height:7200,rows:4},
 {id:'cover',label:'横版封面 · 16:9',width:3200,height:1800},
 {id:'social',label:'简洁宣发 · 3:4',width:2400,height:3200}
];
const exports=[];
for(const j of jobs){
 const file=path.join(raw,j.id+'.png'),m=await sharp(file).metadata();
 await sharp(file).resize(j.width,j.height,{fit:'fill',kernel:'lanczos3'}).withMetadata({density:300}).png({compressionLevel:9}).toFile(path.join(out,j.id+'-background.png'));
 exports.push({...j,source:'03-backgrounds/'+j.id+'.png',file:'04-png-export/'+j.id+'-background.png',nativeWidth:m.width,nativeHeight:m.height,method:'内置 image_gen；按目标尺寸 Lanczos3 放大导出；无正式文案'});
}
await fs.writeFile(path.join(review,'export-manifest.json'),JSON.stringify(exports,null,2));
const data=async file=>'data:image/png;base64,'+(await fs.readFile(file)).toString('base64');
const img=(src,x,y,w,h)=>`<image href="${src}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const tx=(x,y,t,size=26,color='#254252')=>`<text x="${x}" y="${y}" fill="${color}" font-family="Microsoft YaHei UI" font-size="${size}" font-weight="600" dominant-baseline="central">${t}</text>`;
const refs={};for(const j of jobs)refs[j.id]=await data(path.join(raw,j.id+'.png'));
let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="2600" height="1660"><rect width="2600" height="1660" fill="#f8f6ef"/>${tx(60,65,'兴安电竞 · 超额累计单',44)}${tx(60,128,'第二阶段：无字底图确认  /  二次元手绘系列',28,'#68818f')}`;
for(let i=0;i<4;i++){const j=jobs[i],x=60+i*475;svg+=tx(x,202,j.label,27)+img(refs[j.id],x,250,440,990);}
svg+=tx(1990,202,'横版封面',27)+img(refs.cover,1990,250,550,309.4)+tx(1990,623,'简洁宣发海报',27)+img(refs.social,1990,665,550,733.3);
svg+=tx(60,1320,'价目区  →  三格超额计算示例  →  物资归属  →  六组下单须知',30);
svg+=tx(60,1380,'端游五档 / 手游四档；整条深蓝表头，暖金与薄荷青区分模式。',28);
svg+=tx(60,1440,'广告只留四类价目和计入条件；价格、门槛及全部正文待下一阶段添加。',28);
svg+=tx(60,1545,'确认后添加艺术字与矢量文案，按实际面板定位 r / w 固定列和垂直居中。',28,'#68818f');
svg+=tx(60,1600,'本页外侧文字仅为确认标注；六张独立底图均不含正式文案。',24,'#68818f')+'</svg>';
await fs.writeFile(path.join(review,'background-overview.svg'),svg);
await sharp(Buffer.from(svg)).png().toFile(path.join(review,'background-overview.png'));
let layout=`<svg xmlns="http://www.w3.org/2000/svg" width="2300" height="2300"><rect width="2300" height="2300" fill="#f8f6ef"/>${tx(55,70,'超额累计单 · 排版规划',44)}${tx(55,140,'原始结构稿；最终填字将按生成底图的实际边界校准。',28)}`;
layout+=tx(55,212,'端游五档',28)+img(await data(path.join(root,'02-layouts/poster-desktop-layout.png')),55,265,650,1462.5);
layout+=tx(790,212,'手游四档',28)+img(await data(path.join(root,'02-layouts/poster-mobile-layout.png')),790,265,650,1462.5);
layout+=tx(1510,212,'封面',28)+img(await data(path.join(root,'02-layouts/cover-layout.png')),1510,265,735,413.4);
layout+=tx(1510,750,'宣发：上排五档，下排四档',28)+img(await data(path.join(root,'02-layouts/social-layout.png')),1510,800,735,980);
layout+=tx(55,1860,'玩法表达',35)+tx(55,1920,'成功撤离后，仅将单局超过门槛的部分计入进度；带出物资全部归板板。',30);
layout+=tx(55,1980,'绝密案例：1200w − 1000w = 200w   /   机密案例：800w − 600w = 200w',30);
layout+=tx(55,2075,'排字原则',35)+tx(55,2135,'价格以 r 为固定列，物资以 w 为固定列；单位字高约为数字的 65%–75%。',30);
layout+=tx(55,2195,'文字组在容器内垂直居中，减少换行，并为价格与装饰留足安全距离。',30)+'</svg>';
await sharp(Buffer.from(layout)).png().toFile(path.join(review,'layout-overview.png'));
const cards=jobs.map(j=>`<section id="${j.id}"><h2>${j.label}</h2><a href="../04-png-export/${j.id}-background.png"><img loading="lazy" src="../03-backgrounds/${j.id}.png" alt="${j.label}"></a><p><a download href="../04-png-export/${j.id}-background.png">下载 ${j.width}×${j.height} PNG</a></p></section>`).join('');
const sizes=exports.map(j=>`${j.label}：原图 ${j.nativeWidth}×${j.nativeHeight} → 导出 ${j.width}×${j.height}`).join('<br>');
await fs.writeFile(path.join(review,'index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>超额累计单 · 底图确认</title><style>*{box-sizing:border-box}body{background:#f8f6ef;color:#254252;font:16px/1.8 "Microsoft YaHei",sans-serif;margin:0}main{max-width:1600px;margin:auto;padding:40px}h1{margin:0;font-size:34px}h2{font-size:23px}p{color:#657d89}a{color:#167e80}nav{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}button{padding:11px 22px;background:white;border:1px solid #a7bcc6;border-radius:10px;font:inherit;cursor:pointer}button.active{background:#254252;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:36px}img{width:100%;display:block}#layout{display:none;max-width:1250px;margin:auto}footer{border-top:1px solid #bccdd4;margin-top:40px;padding-top:22px;font-size:14px}section{align-self:start}article{padding:18px 25px;border-left:4px solid #74b7ad;background:#fff}small{color:#78909b}@media(max-width:750px){main{padding:20px}.grid{grid-template-columns:1fr}}</style><main><h1>超额累计单 · 底图确认</h1><p>蜂医与得吃灰鼠满载撤离，插画和环境统一二次元手绘。以下是六张无字底图，正式标题、价目和规则将在确认后以矢量方式填入。</p><article>端游五档，手游四档；每张价目包含三格超额计算示例、物资归属和六组须知留白。<br>简洁宣发版保留四类价目与必要计入条件，不含下单须知。</article><nav><button id="a" class="active">查看六张底图</button><button id="b">查看排版规划</button><a href="../campaign-excess-backgrounds-v1.zip">下载本阶段文件包</a></nav><div id="art" class="grid">${cards}</div><div id="layout"><img src="layout-overview.png" alt="布局说明"></div><footer>${sizes}<p>高清尺寸版本为 Lanczos3 放大导出，原始生成图完整保留；正式文案将在目标尺寸从矢量直接渲染。<br><a href="../03-backgrounds/prompts.md">生成提示词</a> · <a href="export-manifest.json">分辨率清单</a></p></footer></main><script>const a=document.getElementById('a'),b=document.getElementById('b'),art=document.getElementById('art'),l=document.getElementById('layout');a.onclick=()=>{art.style.display='grid';l.style.display='none';a.className='active';b.className=''};b.onclick=()=>{art.style.display='none';l.style.display='block';b.className='active';a.className=''};</script></html>`);
await fs.writeFile(path.join(root,'STAGE-2-README.md'),`# 超额累计单 · 无字底图确认\n\n第二阶段共六张底图：端游绝密/机密各五档，手游绝密/机密各四档，横版封面与简洁宣发海报。正式文案待底图确认后添加。\n\n## 版面\n\n- 标题更名为“超额累计单”。\n- 每张完整价目采用两列，整条深蓝表头；绝密暖金，机密薄荷青。\n- 独立机制横幅、三格减法案例、物资归属、六组须知。\n- 广告四卡，上排端游五档、下排手游四档，无下单须知。\n- 价格与物资单位下一阶段用固定 r / w 列，文字组垂直居中。\n\n## 文件\n\n- 03-backgrounds：六张原生生成图与提示词。\n- 04-png-export：四张 3200×7200、封面 3200×1800、广告 2400×3200。\n- 02-layouts：布局稿与坐标；actual-background-bounds.json 为实图初步边界。\n- review/index.html：底图及布局查看页。\n- content.json：本轮用户18档数据及已确认规则的唯一业务源。\n\n## 分辨率\n\n${exports.map(j=>`- ${j.label}：原图 ${j.nativeWidth}×${j.nativeHeight}；导出 ${j.width}×${j.height}`).join('\n')}\n\n高清导出为 Lanczos3 放大，并非原生高清生成。插画保持位图，最终标题与正文另外以矢量制作。\n\n## 后续\n\n依据用户要求与 xingan-gameplay-posters 的“插画→确认→底图→确认→矢量文案”流程，等待本轮底图确认后再填正式文案。\n`);
let state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.stage='background-awaiting-user-approval';state.approvals.background={status:'awaiting-user-approval',basis:'六张无字底图已生成，本轮提交用户确认',overview:'review/background-overview.png',review:'review/index.html'};
state.backgrounds=Object.fromEntries(jobs.map(j=>[j.id,'03-backgrounds/'+j.id+'.png']));state.exports=exports;
state.nextStep='用户确认底图后，根据实图边界填充艺术字和矢量文案；PC五档、手游四档，r/w固定列。';
state.package='campaign-excess-backgrounds-v1.zip';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({exports:exports.map(j=>({id:j.id,native:[j.nativeWidth,j.nativeHeight],export:[j.width,j.height]})),review:'review/index.html'}));
