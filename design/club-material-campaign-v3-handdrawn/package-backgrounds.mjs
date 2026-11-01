import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const sharp=require('C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const root=path.dirname(fileURLToPath(import.meta.url));
const raw=path.join(root,'03-backgrounds'),out=path.join(root,'04-png-export'),review=path.join(root,'05-review');
await fs.mkdir(out,{recursive:true});
await fs.mkdir(review,{recursive:true});
const jobs=[
 {id:'desktop-absolute',label:'端游 · 绝密',src:'poster-gold.png',width:3200,height:7680},
 {id:'desktop-secret',label:'端游 · 机密',src:'poster-teal.png',width:3200,height:7680},
 {id:'mobile-absolute',label:'手游 · 绝密',src:'poster-gold.png',width:3200,height:7680},
 {id:'mobile-secret',label:'手游 · 机密',src:'poster-teal.png',width:3200,height:7680},
 {id:'cover',label:'横版封面',src:'cover.png',width:3200,height:1800},
 {id:'social',label:'简洁广告海报',src:'social.png',width:2400,height:3200}
];
const exports=[];
for(const j of jobs){
 const m=await sharp(path.join(raw,j.src)).metadata();
 await sharp(path.join(raw,j.src)).resize(j.width,j.height,{fit:'fill',kernel:'lanczos3'}).withMetadata({density:300}).png({compressionLevel:9}).toFile(path.join(out,j.id+'-background.png'));
 exports.push({...j,file:'04-png-export/'+j.id+'-background.png',nativeWidth:m.width,nativeHeight:m.height,method:'生成原图经 Lanczos3 放大导出；非原生高分辨率生成，无文字叠加'});
}
await fs.writeFile(path.join(review,'export-manifest.json'),JSON.stringify(exports,null,2));
const data=async(file)=>'data:image/png;base64,'+(await fs.readFile(file)).toString('base64');
const refs={gold:await data(path.join(raw,'poster-gold.png')),teal:await data(path.join(raw,'poster-teal.png')),cover:await data(path.join(raw,'cover.png')),social:await data(path.join(raw,'social.png'))};
const text=(x,y,s,size=25,color='#294252')=>`<text x="${x}" y="${y}" fill="${color}" font-family="Microsoft YaHei UI, Microsoft YaHei, sans-serif" font-size="${size}" font-weight="600" dominant-baseline="central">${s}</text>`;
const img=(src,x,y,w,h)=>`<image href="${src}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="2160" height="1980" viewBox="0 0 2160 1980"><rect width="2160" height="1980" fill="#F7F6F0"/>`;
svg+=text(60,68,'兴安电竞 · 二次元手绘底图确认',43)+text(60,126,'人物、装备、建筑与场景已统一画风；标题、价格和规则待确认后填入。',25,'#66818E');
svg+=text(60,198,'价目海报 · 绝密暖金',29)+text(750,198,'价目海报 · 机密薄荷青',29)+text(1440,198,'封面 · 16:9 横版',29);
svg+=img(refs.gold,60,240,650,1560)+img(refs.teal,750,240,650,1560)+img(refs.cover,1440,240,660,371.25);
svg+=text(1440,670,'广告海报 · 3:4 / 四类价目',28)+img(refs.social,1440,710,660,880);
svg+=text(1440,1663,'广告版保留优惠区和六档价目，',24)+text(1440,1708,'不含下单须知。',24);
svg+=text(60,1860,'端游、手游使用对应色系底图，下一阶段通过平台标签和各自价格区分。',25)+text(60,1915,'原图与大尺寸放大导出 PNG 均保留；预览中的文字仅为确认标注。',24,'#718792')+'</svg>';
await fs.writeFile(path.join(review,'background-overview.svg'),svg);
await sharp(Buffer.from(svg)).png().toFile(path.join(review,'background-overview.png'));
let ls=`<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1410"><rect width="1200" height="1410" fill="#F7F6F0"/>${text(45,58,'信息布局与对齐规划',36)}${text(45,110,'确认后按实际底图边界排字，标注不属于成品。',22,'#66818E')}`;
const layouts=[{name:'完整版布局',file:'poster-layout.png',x:45,y:180,w:450,h:1080},{name:'封面布局',file:'cover-layout.png',x:555,y:180,w:580,h:326.25},{name:'广告布局',file:'social-layout.png',x:555,y:580,w:580,h:773.333}];
for(const l of layouts)ls+=text(l.x,l.y-28,l.name,25)+img(await data(path.join(root,'02-layouts',l.file)),l.x,l.y,l.w,l.h);
ls+='</svg>';
await fs.writeFile(path.join(review,'layout-overview.svg'),ls);
await sharp(Buffer.from(ls)).png().toFile(path.join(review,'layout-overview.png'));
const cards=[{title:'绝密价目 · 暖金',source:'poster-gold.png',links:jobs.filter(j=>j.id.endsWith('absolute'))},{title:'机密价目 · 薄荷青',source:'poster-teal.png',links:jobs.filter(j=>j.id.endsWith('secret'))},{title:'横版封面',source:'cover.png',links:[jobs[4]],wide:true},{title:'简洁广告海报',source:'social.png',links:[jobs[5]]}];
const cardHtml=cards.map(c=>`<section class="card ${c.wide?'wide':''}"><h2>${c.title}</h2><a href="../03-backgrounds/${c.source}"><img src="../03-backgrounds/${c.source}" alt="${c.title}无字底图"></a><p>${c.links.map(j=>`<a download href="../04-png-export/${j.id}-background.png">${j.label} PNG · ${j.width}×${j.height}</a>`).join(' · ')}</p></section>`).join('');
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞 · 手绘底图确认</title><style>*{box-sizing:border-box}body{margin:0;background:#f7f6f0;color:#294252;font:16px/1.7 "Microsoft YaHei",sans-serif}main{max-width:1440px;margin:auto;padding:36px}h1{font-size:32px;margin:0}p{color:#63808e}a{color:#257e80}nav{display:flex;gap:12px;margin:24px 0}button{font:inherit;padding:10px 20px;border:1px solid #bed2db;border-radius:8px;background:white;color:#294252;cursor:pointer}button[aria-pressed=true]{background:#294252;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:30px}.card h2{font-size:21px}.card img{width:100%;display:block;border:1px solid #cedfe5}.wide{grid-column:1/-1}.layout{display:none}.layout img{width:100%;max-width:1100px}.show-layout .grid{display:none}.show-layout .layout{display:block}footer{margin-top:30px;padding-top:20px;border-top:1px solid #cedfe5;font-size:14px;color:#63808e}@media(max-width:720px){main{padding:20px}.grid{grid-template-columns:1fr}.wide{grid-column:auto}}</style><main id="view"><h1>二次元手绘 · 底图确认</h1><p>已统一人物、道具和场景画风。本次仅确认底图，正式艺术字与矢量文案在确认后添加。</p><nav><button id="art" aria-pressed="true">查看底图</button><button id="layout" aria-pressed="false">查看布局规划</button></nav><div class="layout"><img src="layout-overview.png" alt="布局规划"></div><div class="grid">${cardHtml}</div><footer>生成方式：内置 image_gen。原图：价目 809×1942、封面 1672×941、广告 1086×1448；大尺寸 PNG 为 Lanczos3 放大导出，不代表原生生成分辨率。端游和手游的对应色系底图相同，下一阶段以平台标签与各自价格区分。<br><a href="../03-backgrounds/prompts.md">完整生成提示词</a> · <a href="../STAGE-2-README.md">交付说明</a></footer></main><script>const v=document.getElementById('view'),a=document.getElementById('art'),l=document.getElementById('layout');a.onclick=()=>{v.classList.remove('show-layout');a.setAttribute('aria-pressed','true');l.setAttribute('aria-pressed','false')};l.onclick=()=>{v.classList.add('show-layout');a.setAttribute('aria-pressed','false');l.setAttribute('aria-pressed','true')};</script></html>`;
await fs.writeFile(path.join(review,'review-backgrounds.html'),html);
const md=`# 二次元手绘底图 · 第二阶段交付\n\n当前状态：用户已确认新版手绘插画，整套底图已重制，等待用户确认结果。尚未进入艺术字与矢量文案填充。\n\n## 内容\n\n- 暖金与薄荷青价目底图，对应绝密与机密；各输出端游、手游文件，共四张。\n- 16:9 横版封面一张。\n- 3:4 简洁广告底图一张，包含四类六档价目位置，不含下单须知。\n- 六张均为无字底图；端游和手游目前使用对应色系相同底图，后续以标题标签与价格区分。\n\n## 已检查的布局\n\n- 完整价目表为六档，须知区为六行；优惠和保障有独立区域。\n- 广告版为两列两行的四类价目，各六档。\n- 插画、物资、建筑、直升机与场景使用统一二次元手绘线条及简化色块。\n- 文案留白已保留。填字时须以实际生成底图边界为准，不能直接照抄旧版位置。\n\n## 文件位置\n\n- 03-backgrounds：四种生成原图与 prompts.md（内置 image_gen 提示词）。\n- 04-png-export：四张价目底图、封面、广告，共六张大尺寸 PNG。\n- 05-review：底图总览、布局规划、浏览确认页和分辨率清单。\n\n## 分辨率\n\n| 类别 | 生成原图 | PNG 导出 |\n| --- | --- | --- |\n| 价目海报 | 809×1942 | 3200×7680 |\n| 封面 | 1672×941 | 3200×1800 |\n| 广告海报 | 1086×1448 | 2400×3200 |\n\n大尺寸 PNG 是 Lanczos3 放大导出，不代表原生高分辨率生成。生成原图完整保留。\n\n## 后续条件\n\n用户确认本轮底图后，再生成艺术字并填入矢量文案。仍以原始四张价目图为数据来源，遵守 r/w 单位对齐、文案垂直居中、少换行、优惠重点展示。工作日累计物资保底 +20%，补偿另计、不参与上浮。\n`;
await fs.writeFile(path.join(root,'STAGE-2-README.md'),md);
const state=JSON.parse((await fs.readFile(path.join(root,'campaign-state.json'),'utf8')).replace(/^\uFEFF/,''));
state.currentStage='2R - 二次元手绘底图已完成，待用户确认结果';
state.illustrationApproval='用户明确表示很好，要求继续重置价目海报、封面、广告底图；新版二次元手绘插画已确认';
state.stage2={status:'awaiting-user-approval',style:'二次元手绘',overview:'05-review/background-overview.png',review:'05-review/review-backgrounds.html',layout:'02-layouts/layout-spec.json',exports};
state.reminders=['新版二次元手绘底图待确认，不进入正式文案填充','以实际底图位置和边界重新规划文案坐标，不取旧版 text-placement-plan.json','r 与 w 分别固定单位列，文案组垂直居中，减少换行','仍以用户原始四张价目图核对数据，禁止从旧版 build.mjs 取数'];
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({exports:exports.map(j=>({file:j.file,size:`${j.width}x${j.height}`})),overview:'05-review/background-overview.png'}));
