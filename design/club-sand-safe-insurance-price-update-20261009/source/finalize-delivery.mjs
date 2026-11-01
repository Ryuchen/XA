import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=async f=>JSON.parse(await fs.readFile(path.join(root,f),'utf8'));
const c=await read('content.json'),state=await read('campaign-state.json'),manifest=await read('output/manifest.json'),audit=await read('review/price-and-layout-audit.json');
const revision=c.revision,archive='campaign-sand-safe-insurance-mobile-price-r2-20261009.zip';
const finalRows=c.tiers.find(t=>t.id==='mobile-absolute').rows;
if(JSON.stringify(finalRows.map(r=>r.priceR))!==JSON.stringify([108,288,488]))throw Error('Latest user prices not applied');
for(const r of manifest) {
 const actual=createHash('sha256').update(await fs.readFile(path.join(root,r.png))).digest('hex');
 if(actual!==audit.records.find(v=>v.id===r.id).sha256)throw Error('PNG changed since inspected previews');
}
const qa=`# 成图复检 · ${revision}\n\n日期：2026-10-09。只交付手游绝密价目与双平台广告，共两张。\n\n用户最新文字“手机端沙保的价格改成108，288和488”覆盖截图新价格88/238/428。本轮最终手游档位：108r / 688w / 2沙保 / 3大保险；288r / 1688w / 6沙保 / 8大保险；488r / 3288w / 10沙保 / 15大保险。价目表与广告均由content.json同一组档位驱动。\n\n沿用当前确认项目的插画、无字底图、计次与累计规则、奖励口径。仅绝密，无机密页。广告端游沿用现有源数据168/388/628，保险次数2/6/10及3/8/15；用户附旧广告端游第二档写3，当前源数据、完整端游表与当前广告均为8，本次不改当前项目端游数据。\n\n## 实际最终图复检\n\n| 页面 | 实際检查证据 | 结论 |\n|---|---|---|\n| 手游绝密价目 | review/update-overview.png；review/phone-390/mobile-absolute.png；review/details/mobile-absolute-prices.png | 108/288/488与保底、次数对应准确；首末档完整；r/w共用基线，整行字形在行内居中；图标与数字有间距；左右列平衡。通过。 |\n| 双平台广告 | review/update-overview.png；review/phone-390/social.png；review/details/social-mobile-prices.png | 手游三档与完整页一致；端游沿用当前确认源稿；两表首末行与保险图标无遮挡；无完整下单须知。通过。 |\n| 相邻奖励与须知 | review/details/social-reward.png；review/details/mobile-absolute-notes.png | 三幻神触发、+800w、赠1次、整单仅一次及赠送不计套餐次数保留；规则仅计成功撤离、代摸须先询问、严格>600w均保留；普通说明与重点粗体分层，无贴线或叠字。通过。 |\n\n最终改价后再次打开从实际高清PNG生成的整图总览、准确390px手机预览及两张价目局部。奖励与须知排版在最终改价中未变，初次渲染时实际PNG局部已经打开检查；最终整图同时复查。\n\n## 排版修正和测量\n\n复用旧源稿时发现r/w及混排片段原为各自字形居中，未共用baseline。本轮源稿修正为同行共用baseline，再用完整字形组居中；中文混排按字体advance推进，数字单位保留明确可见间隙。不重生成或修改美术。两张页的18组价格/保底数字单位baseline差均为0，数字单位最小可见间隙10原生px，单位可见字高为主数字70%。手游表行最低留白21.02原生px，广告表10.68原生px。\n\n逐块对照现有底图实测安全区核查价目行、表头、奖励、规则及页尾：数据对应正确、字形均在容器内，整组居中，实际手机图可读。未扩大安全区使失败项通过。检查脚本按具体位置区分同名表头；无可见文本框交叠，数值与来源一致。自动检测为辅助，实际图复检结论如上。\n\nPNG手游3200×4800，广告2400×3200；对应底图原生1024×1536、1086×1448。文字按目标尺寸矢量渲染，底图为沿用原尺寸位图并放大，未声称插画原生高清。转曲SVG不依赖字体；可改文字SVG正文数字保留文字对象，艺术标题是路径，背景均为内嵌位图。\n\n最终PNG SHA-256记录在price-and-layout-audit.json，全部交付文件摘要记录在output/package-files.json；打包后逐项核对ZIP中的文件与这些已检查版本一致。\n`;
await fs.writeFile(path.join(root,'review/visual-qa.md'),qa);
const caption='手游108 / 288 / 488元；基础保底688 / 1688 / 3288万；沙色保险2 / 6 / 10，常规大保险3 / 8 / 15。';
const cards=manifest.map(r=>`<article><h2>${r.label}</h2><nav><a download href="../${r.png}">高清PNG ${r.width}×${r.height}</a><a download href="../${r.outlinedSVG}">转曲SVG</a><a download href="../${r.editableSVG}">可改文字SVG</a><a href="phone-390/${r.id}.png">手机预览</a></nav><img src="${r.id}.png" alt="${r.label}"></article>`).join('');
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>常规沙保单 · 手游价格更新</title><style>body{margin:0;background:#F5EFE4;color:#183E50;font:16px/1.7 'Microsoft YaHei UI',sans-serif}main{max-width:1360px;margin:auto;padding:24px}h1{font-size:28px}h2{font-size:21px}nav{display:flex;gap:14px;flex-wrap:wrap}a{color:#925A22}img{width:100%;height:auto;display:block;margin-top:18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{background:#fffdf7;padding:18px;border-radius:14px}@media(max-width:740px){main{padding:14px}.grid{grid-template-columns:1fr}}</style><main><h1>常规沙保单 · 手游绝密价格更新</h1><p>${caption}</p><p>以2026-10-09用户最新价格修订为准。广告与手游价目共用数据；沿用原确认美术、规则与奖励。</p><nav><a href="../${archive}">下载全部高清PNG和SVG源文件</a><a href="visual-qa.md">查看成图复检记录</a></nav><div class="grid">${cards}</div></main></html>`;
await fs.writeFile(path.join(root,'review/index.html'),html);
await fs.writeFile(path.join(root,'output/DELIVERY.md'),'# 常规沙保单 · 手游绝密价格更新\n\n版本：'+revision+'。\n\n'+caption+'\n\n本次交付两张：手游绝密完整价目3200×4800、双平台广告2400×3200。PNG直接用于发布；转曲SVG不依赖字体；可改文字SVG保留正文数字文本对象（Microsoft YaHei UI / Bahnschrift），艺术标题为路径。背景为内嵌位图。\n\n以最新用户文字价格为准，保底与次数以本轮价格截图为准。规则及奖励沿用原确认稿。\n');
state.stage='delivered';state.revision=revision;
state.finalDeliverables={pages:manifest.map(r=>r.id),manifest:'output/manifest.json',package:archive,review:'review/index.html',overview:'review/update-overview.png'};
state.visualQA={status:'passed',revision,record:'review/visual-qa.md',dataAndGeometryAudit:'review/price-and-layout-audit.json',basis:'最新108/288/488改价后打开实际PNG生成的整图、390px手机预览、手游表及广告价格局部；奖励和规则相邻区检查通过。'};
state.nextStep='本轮两张已完成；后续改价以content.json统一修改并重新渲染、复检。';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
let readme=await fs.readFile(path.join(root,'README.md'),'utf8');
readme+='\n## 成品与重建\n\n- review/index.html：查看和下载两张成图、两类SVG。\n- '+archive+'：本轮成品与必要源文件。\n- 先运行source/typeset.mjs --modules=<Node包目录>，再运行source/review-output.mjs同参数；检查实际成图后重新打包。\n- 原插画及底图确认继续有效，无需因改价重新生成。\n';
await fs.writeFile(path.join(root,'README.md'),readme);
const files=new Set(['README.md','content.json','campaign-state.json','layout.json','layout-adapter.mjs','02-layouts/generated-background-measurements.json','assets/backgrounds/poster.png','assets/backgrounds/social.png','references/new-mobile-prices.png','references/user-original-advert.png','references/user-original-mobile.png','output/manifest.json','output/DELIVERY.md','review/index.html','review/visual-qa.md','review/price-and-layout-audit.json','review/typesetting-placements.json','review/update-overview.png','source/typeset.mjs','source/render.mjs','source/outline-text.ps1','source/text-metrics.mjs','source/review-output.mjs','source/finalize-delivery.mjs','source/package-delivery.py','.cache/all-text-outlines.json']);
for(const r of manifest)for(const name of [r.png,r.outlinedSVG,r.editableSVG,r.preview,`review/${r.id}-text-bounds.json`,`review/phone-390/${r.id}.png`])files.add(name);
for(const f of await fs.readdir(path.join(root,'review/details')))if(f.endsWith('.png'))files.add('review/details/'+f);
const entries=[];
for(const file of [...files].sort()){const data=await fs.readFile(path.join(root,file));entries.push({file,bytes:data.length,sha256:createHash('sha256').update(data).digest('hex')});}
await fs.writeFile(path.join(root,'output/package-files.json'),JSON.stringify({revision,archive,files:entries},null,2)+'\n');
console.log(JSON.stringify({revision,pages:manifest.map(r=>r.id),files:entries.length,archive},null,2));
