import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const sharp=require('C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const root=path.dirname(fileURLToPath(import.meta.url));
const sources=path.join(root,'03-backgrounds');
const exported=path.join(root,'04-png-export');
const review=path.join(root,'05-review');
await fs.mkdir(exported,{recursive:true});
await fs.mkdir(review,{recursive:true});
const jobs=[
 {id:'desktop-absolute',label:'端游 · 绝密',source:'poster-gold.png',w:3200,h:7680},
 {id:'desktop-secret',label:'端游 · 机密',source:'poster-teal.png',w:3200,h:7680},
 {id:'mobile-absolute',label:'手游 · 绝密',source:'poster-gold.png',w:3200,h:7680},
 {id:'mobile-secret',label:'手游 · 机密',source:'poster-teal.png',w:3200,h:7680},
 {id:'cover',label:'横版封面',source:'cover.png',w:3200,h:1800},
 {id:'social',label:'简洁宣发版',source:'social.png',w:2400,h:3200}
];
const exportedMeta=[];
for(const job of jobs){
 const input=path.join(sources,job.source),out=path.join(exported,job.id+'-background.png');
 const original=await sharp(input).metadata();
 await sharp(input).resize(job.w,job.h,{fit:'fill',kernel:'lanczos3'}).withMetadata({density:300}).png({compressionLevel:9}).toFile(out);
 exportedMeta.push({...job,file:'04-png-export/'+job.id+'-background.png',nativeWidth:original.width,nativeHeight:original.height,exportMethod:'Lanczos3 放大导出；非原生高分辨率生成；未增加文字或改变画面内容'});
}
const uri=async(name)=>'data:image/png;base64,'+(await fs.readFile(path.join(sources,name))).toString('base64');
const refs={gold:await uri('poster-gold.png'),teal:await uri('poster-teal.png'),cover:await uri('cover.png'),social:await uri('social.png')};
const escape=(s)=>s.replaceAll('&','&amp;').replaceAll('<','&lt;');
const text=(x,y,s,size=26,color='#273F50')=>`<text x="${x}" y="${y}" dominant-baseline="central" font-family="Microsoft YaHei UI, Microsoft YaHei, sans-serif" font-size="${size}" font-weight="600" fill="${color}">${escape(s)}</text>`;
const image=(src,x,y,w,h)=>`<image href="${src}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
let sheet=`<svg xmlns="http://www.w3.org/2000/svg" width="2160" height="1980" viewBox="0 0 2160 1980"><rect width="2160" height="1980" fill="#F4F3EF"/>`;
sheet+=text(60,68,'兴安电竞 · 第二阶段底图确认',43)+text(60,126,'已完成布局与插画底图，正式标题、价格和规则将在确认后填入。',25,'#657C8B');
sheet+=text(60,198,'绝密版 · 暖金',29)+text(750,198,'机密版 · 薄荷青',29)+text(1440,198,'封面 · 横版',29);
sheet+=image(refs.gold,60,240,650,1560)+image(refs.teal,750,240,650,1560)+image(refs.cover,1440,240,660,371.25);
sheet+=text(1440,670,'宣发版 · 四类价目 / 无下单须知',27)+image(refs.social,1440,710,660,880);
sheet+=text(1440,1664,'预留区：艺术字标题、+20%活动、',24)+text(1440,1705,'六档价格及 r / w 固定对齐列。',24);
sheet+=text(60,1860,'端游与手游使用对应色系底图；下一阶段用平台标签区分。',26)+text(60,1915,'提供生成原图及放大导出 PNG，导出尺寸不代表原生生成分辨率。',24,'#718592')+'</svg>';
await fs.writeFile(path.join(review,'background-overview.svg'),sheet);
await sharp(Buffer.from(sheet)).png().toFile(path.join(review,'background-overview.png'));
const layouts=[['完整版布局','poster-layout.png',450,1080],['封面布局','cover-layout.png',580,326.25],['宣发布局','social-layout.png',580,773.3333]];
let ls=`<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1320"><rect width="1200" height="1320" fill="#F4F3EF"/>${text(45,57,'信息布局与对齐示意',36)}${text(45,110,'仅用于确认结构，标注不会出现在底图中。',22,'#657C8B')}`;
for(let i=0;i<layouts.length;i++){const [title,f,w,h]=layouts[i],x=i===0?45:555,y=i===0?180:(i===1?180:580),u='data:image/png;base64,'+(await fs.readFile(path.join(root,'02-layouts',f))).toString('base64');ls+=text(x,y-28,title,25)+image(u,x,y,w,h);}
ls+='</svg>';
await fs.writeFile(path.join(review,'layout-overview.svg'),ls);
await sharp(Buffer.from(ls)).png().toFile(path.join(review,'layout-overview.png'));
const placement={
 unit:'生成原图像素坐标，填字时按实际输出比例换算；文案组按行高垂直居中',
 poster:{width:809,height:1942,brand:{x:130,y:47,w:210,h:44},headline:{x:43,y:116,w:310,h:110},platform:{x:40,y:256,w:237,h:46},promotion:{x:63,y:363,w:485,h:99},priceHeaderY:542,priceRowsY:[614,699,784,869,954,1038],priceNumberRightX:244,priceUnitX:258,materialNumberRightX:637,materialUnitX:646,noteY:1099,protection:{x:41,y:1120,w:727,h:175},conditions:[{x:200,y:1182,w:208,h:38},{x:460,y:1182,w:270,h:38}],result:{x:197,y:1231,w:536,h:48},rulesHeading:{x:110,y:1320,w:340,h:47},rules:{x:41,y:1383,w:727,h:383,rowEdges:[1383,1447,1510,1573,1637,1700,1766],labelX:128,bodyX:251,bodyRight:743},footer:{x:41,y:1855,w:727,h:42}},
 cover:{width:1672,height:941,brand:{x:150,y:110,w:570,h:70},headline:{x:88,y:215,w:670,h:238},platform:{x:87,y:527,w:554,h:62},subtitle:{x:91,y:615,w:527,h:58}},
 social:{width:1086,height:1448,brand:{x:130,y:64,w:360,h:54},headline:{x:48,y:151,w:419,h:152},promotion:{x:75,y:371,w:748,h:83},cards:[{name:'端游·绝密',x:50,titleY:519,headingY:566,rowCenters:[609,657,703,749,795,842]},{name:'端游·机密',x:560,titleY:519,headingY:566,rowCenters:[609,657,703,749,795,842]},{name:'手游·绝密',x:50,titleY:916,headingY:963,rowCenters:[1009,1056,1102,1148,1194,1240]},{name:'手游·机密',x:560,titleY:916,headingY:963,rowCenters:[1009,1056,1102,1148,1194,1240]}],cardWidth:476,priceNumberRightOffset:159,priceUnitOffset:165,materialNumberRightOffset:418,materialUnitOffset:425,bottomNote:{x:260,y:1300,w:558,h:78}},
 note:'这是观察底图后记录的初始安全区，第三阶段仍需在实际排字后逐项视觉检查。'
};
await fs.writeFile(path.join(review,'text-placement-plan.json'),JSON.stringify(placement,null,2));
await fs.writeFile(path.join(review,'export-manifest.json'),JSON.stringify(exportedMeta,null,2));
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞 · 底图确认</title><style>*{box-sizing:border-box}body{margin:0;background:#f4f3ef;color:#233e50;font:16px/1.65 "Microsoft YaHei",sans-serif}main{max-width:1440px;padding:40px;margin:auto}h1{font-size:32px;margin:0}p{color:#607583}nav{display:flex;gap:12px;margin:22px 0}a{color:#25696e}button{border:1px solid #c5d4da;border-radius:8px;padding:10px 18px;background:white;color:#233e50;cursor:pointer;font:inherit}button[aria-pressed=true]{background:#233e50;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:30px}.card{min-width:0}.card h2{font-size:20px}.card img{width:100%;display:block;background:#fff;border:1px solid #d5dfe1}.cover{grid-column:1/-1}.layout{display:none}.layout img{max-width:1100px;width:100%}.show-layout .grid{display:none}.show-layout .layout{display:block}.meta{font-size:14px}footer{margin-top:35px;border-top:1px solid #c7d3d7;padding-top:20px}@media(max-width:700px){main{padding:20px}.grid{grid-template-columns:1fr}.cover{grid-column:auto}}</style><main id="view"><h1>兴安电竞 · 底图确认</h1><p>第二阶段已完成：布局、插画背景及无字价目容器。确认后再加入艺术字与矢量文案。</p><nav><button id="art" aria-pressed="true">查看底图</button><button id="layout" aria-pressed="false">查看布局标注</button></nav><div class="layout"><img src="layout-overview.png" alt="信息布局与对齐示意"></div><div class="grid"><section class="card"><h2>绝密版 · 暖金</h2><img src="../03-backgrounds/poster-gold.png" alt="绝密版无字底图"><p class="meta"><a href="../04-png-export/desktop-absolute-background.png">端游 PNG</a> · <a href="../04-png-export/mobile-absolute-background.png">手游 PNG</a> · 导出 3200 × 7680</p></section><section class="card"><h2>机密版 · 薄荷青</h2><img src="../03-backgrounds/poster-teal.png" alt="机密版无字底图"><p class="meta"><a href="../04-png-export/desktop-secret-background.png">端游 PNG</a> · <a href="../04-png-export/mobile-secret-background.png">手游 PNG</a> · 导出 3200 × 7680</p></section><section class="card cover"><h2>封面 · 横版</h2><img src="../03-backgrounds/cover.png" alt="封面无字底图"><p class="meta"><a href="../04-png-export/cover-background.png">封面 PNG</a> · 导出 3200 × 1800</p></section><section class="card"><h2>简洁宣发版 · 四类价目</h2><img src="../03-backgrounds/social.png" alt="宣发版无字底图"><p class="meta"><a href="../04-png-export/social-background.png">宣发 PNG</a> · 导出 2400 × 3200</p></section></div><footer>导出版由生成原图放大，原生尺寸：长版 809 × 1942，封面 1672 × 941，宣发版 1086 × 1448。价格与规则目前留空；端游和手游的对应色系底图相同，下一阶段通过平台标签区分。</footer></main><script>const v=document.getElementById('view'),a=document.getElementById('art'),l=document.getElementById('layout');a.onclick=()=>{v.classList.remove('show-layout');a.setAttribute('aria-pressed','true');l.setAttribute('aria-pressed','false')};l.onclick=()=>{v.classList.add('show-layout');a.setAttribute('aria-pressed','false');l.setAttribute('aria-pressed','true')};</script></html>`;
await fs.writeFile(path.join(review,'review-stage2.html'),html);
const md=`# 第二阶段底图确认\n\n第一阶段插画已获用户确认（用户要求继续布局和底图阶段）。当前状态：第二阶段底图已生成，等待确认。\n\n## 排版\n\n- 完整版：标题与主插画 → 工作日优惠票券 → 六档价目 → 猛攻保障 → 六项下单须知 → 流程条。\n- 绝密用暖金，机密用薄荷青；端游与手游在下一阶段通过标题标签区分。\n- 文案按所在行或区域垂直居中；价格预留固定 r 单位列，物资预留固定 w 单位列。\n- 封面为 16:9 横版，左侧品牌与艺术字，右侧满载撤离插画。\n- 宣发版为 3:4 竖版，含工作日优惠和四类六档价目，不含下单须知。\n\n## 文件\n\n- 02-layouts：SVG 网格、空白结构模板、布局标注。\n- 03-backgrounds：内置 image_gen 生成原图和完整提示词。\n- 04-png-export：六份放大导出 PNG。\n- 05-review：底图总览、布局总览、浏览页、原生分辨率记录和文字安全区。\n\n## 分辨率\n\n| 类型 | 生成原图 | PNG 导出 |\n| --- | --- | --- |\n| 完整版 | 809 × 1942 | 3200 × 7680 |\n| 封面 | 1672 × 941 | 3200 × 1800 |\n| 宣发版 | 1086 × 1448 | 2400 × 3200 |\n\n导出使用 Lanczos3 放大，不能视为原生高分辨率生成。原图也完整保留。下一阶段的正式正文将独立保留为矢量文字或路径；当前图片不含正式文案。\n\n## 下一阶段\n\n须先获得用户对底图的确认，再渲染艺术字并填入经过核对的价格与规则。不沿用旧版 build.mjs 的错误数据，仍以用户本次上传的四张原始价目表为准。\n`;
await fs.writeFile(path.join(root,'STAGE-2-README.md'),md);
const state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.currentStage='2 - 布局和底图已完成，待用户确认';
state.illustrationApproval='用户明确要求继续下一阶段，先布局再生成底图，视为第一阶段通过';
state.stage2={status:'awaiting-user-approval',layout:'02-layouts/layout-spec.json',review:'05-review/review-stage2.html',overview:'05-review/background-overview.png',exports:exportedMeta};
state.reminders=state.reminders.filter(x=>!x.includes('尚未获得插画批准'));
state.reminders.unshift('第二阶段底图待用户确认，未授权进入正式文案填充');
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({exports:exportedMeta.map(x=>({file:x.file,width:x.w,height:x.h})),review:'05-review/background-overview.png'}));
