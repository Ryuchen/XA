import fs from'node:fs/promises';import path from'node:path';import {createHash}from'node:crypto';import {createRequire}from'node:module';import{fileURLToPath}from'node:url';
const project=path.resolve(process.argv[2]||path.join(path.dirname(fileURLToPath(import.meta.url)),'..'));
const modules=process.argv[3]||path.resolve(path.dirname(process.execPath),'../node_modules'),sharp=createRequire(import.meta.url)(path.join(modules,'sharp'));
const read=async p=>JSON.parse(await fs.readFile(path.join(project,p),'utf8'));
const content=await read('content.json'),plan=await read('layout.json'),state=await read('campaign-state.json'),manifest=await read('output/manifest.json'),metric=await read('review/final-metric-audit.json');
const wanted=state.deliveryScope.pageIds;
if(JSON.stringify(wanted)!==JSON.stringify(plan.pages.map(x=>x.id)))throw new Error('Scope mismatch');
if(state.approvals.illustration.status!=='approved'||state.approvals.background.status!=='approved')throw new Error('Phase approvals missing');
const finalRecords=[];
for(const p of plan.pages){
 const record=manifest.find(x=>x.id===p.id),checked=metric.pages.find(x=>x.id===p.id);if(!record||!checked)throw new Error('Missing output');
 const png=await fs.readFile(path.join(project,record.png)),sha256=createHash('sha256').update(png).digest('hex'),meta=await sharp(png,{limitInputPixels:false}).metadata();
 if(sha256!==checked.sha256||meta.width!==p.ew||meta.height!==p.eh)throw new Error('Changed PNG after visual QA');
 if(p.id.includes('absolute')){
  const platform=p.platforms[0];
  for(const [i,tier]of content.prices.entries()){const value=p.elements.find(e=>e.semanticGroup==='price-'+platform+'-'+i&&e.axisKind==='number');if(value?.text!==String(tier[platform]))throw new Error('Platform price mismatch');}
  if(!p.elements.some(e=>e.type==='rich'&&e.semanticGroup==='condition-2'&&e.runs.some(r=>r.text===String(content.objectives.loot[platform]))))throw new Error('Loot threshold mismatch');
  for(let i=0;i<4;i++)if(!p.elements.some(e=>e.semanticGroup==='notice-'+i))throw new Error('Notice missing');
 }else{
  if(p.elements.some(e=>e.semanticGroup?.startsWith('notice-')))throw new Error('Notice on advert/cover');
  for(const platform of p.platforms)if(!p.elements.some(e=>e.type==='rich'&&e.semanticGroup==='condition-2'&&e.runs.some(r=>r.text===String(content.objectives.loot[platform]))))throw new Error('Combined loot threshold mismatch');
 }
 if(p.id==='combined-cover'&&p.elements.some(e=>e.priceAxis))throw new Error('Unrequested cover price');
 finalRecords.push({...record,sha256});
}
const pairs=metric.pages.flatMap(p=>p.numeric),maxBaselineDelta=Math.max(...pairs.map(p=>p.baselineDelta)),minGap=Math.min(...pairs.map(p=>p.visibleGap)),minRatio=Math.min(...pairs.map(p=>p.unitHeightRatio)),maxRatio=Math.max(...pairs.map(p=>p.unitHeightRatio));
const validation={revision:'redesign-r1',status:'passed',date:'2026-10-03',scope:wanted,numericPairCount:pairs.length,maxBaselineDelta,minVisibleNumericUnitGap:minGap,unitVisibleHeightRatio:{min:minRatio,max:maxRatio},fontBodyPhonePx:390/836*30,record:'review/visual-qa.md',metrics:'review/final-metric-audit.json',visualInspection:'All 4 actual PNG-derived 390px views; full-page images; native header, conditions, reward, prices, notices crops.',files:finalRecords};
state.revision='redesign-r1';state.stage='final-qa-passed';state.nextStep='成图复检完成，整理成品包并核对包内版本。';state.finalQA=validation;state.backgroundQA.finalTypesettingQA='passed';state.layout.phoneBodyPx=390/836*30;
await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2));
await fs.writeFile(path.join(project,'output/final-validation.json'),JSON.stringify(validation,null,2));
const qa=`
## 第三步成图验收 · redesign-r1 · 2026-10-03

范围：端游绝密、手游绝密、双平台合并广告、16:9共用封面共四张。插画与无字底图的两次“继续”确认沿用。业务内容由content.json统一驱动，原始规则及等义精简对照保留在verification.typesetEquivalence。

### 来源与含义

- 连续两次/三次：端游688r/1288r，手游588r/888r。累计218/428/628与168/328/488沿用正确沙保连连看旧项目已确认内容，广告使用同一数据。
- 三卡：摸2个沙色保险、摸4个常规大保险、带出物资严格大于端游888w/手游788w。主句“三项缺一不可”，两个“且”连接并汇合，结果条明确同一局全部满足且成功撤离。
- 下单许愿万金泪冠或纵横，二选一；出了送1688w保底，未写成普通下单即有保底。
- 完整页保留四项须知。炸单以前提“成功撤离且带出<300w”触发；“炸单断连，不增保底”对应中断连续计数、不增加保底。“激战/两类保险/须询问能否代摸”对应战斗激烈、目标卡中两类保险及询问许可。额外保底由陪玩自选绝密地图补打。
- 广告和封面均无下单须知；封面无结算、补偿例外。没有添加撤离失败的计数规则、普通保底、联系方式等内容。

### 发现、修复与复查

1. 初排炸单规则两行仅约1–2px原生间隙，且手游净区域略超高；等义精简为一行，字号仍30，重新测量advance与粗体边界，手机约14px。
2. 封面第三卡初排标题与首个数值边界重叠0.74px；改为32号短标签、58号数字、49px两行间距，在原有138px净区域内重新居中。看final-crops/combined-cover-conditions.png及combined-cover.png，标题、两阈值和单位间有正间距。
3. 手游末条原左沿70处与底图叶片重叠；将标题及四条须知统一左沿124，安全区只向内收窄，保持原右边界。精简炸单句使其无需缩字。复查final-crops/mobile-absolute-notices.png及mobile-absolute-phone-390.png，叶片与文字已分离，右下补给箱未侵入正文。
4. 混排基线按完整行设置，语义组另作垂直居中；修正同cy但不同行的文字误并入同一基线组的问题。双平台第三卡的w固定列位。正文只强调核心条件，常规说明保持Regular。

### 实际图与区域验收

| 页面 | 实际检查文件 | 区块及结论 |
|---|---|---|
| 端游绝密 | desktop-absolute.png、desktop-absolute-phone-390.png、final-crops/desktop-absolute-*.png | 标题组居中于人物左侧净区；三卡按完整组居中；汇合条有内边距；五价目行和最长1288r完整；奖励避开皇冠棋盘；须知左对齐、分隔线和末行无贴边。通过。 |
| 手游绝密 | mobile-absolute.png、mobile-absolute-phone-390.png、final-crops/mobile-absolute-*.png | 门槛788w、连续588/888核对；中卡上方小保险与标题隔开；奖励组在皇冠左侧居中；须知统一左沿124，末条与叶片/箱体分离。通过。 |
| 合并广告 | combined-advert.png、combined-advert-phone-390.png、final-crops/combined-advert-*.png | 两平台五档映射正确；两条阈值w列齐；首末价格行按实际分隔线居中；奖励整组居中于左右道具之间，人物轮廓无遮挡。通过。 |
| 横版封面 | combined-cover.png、combined-cover-phone-390.png、final-crops/combined-cover-*.png | 品牌、标题、版本在左文净区共用中心；同局三项主句醒目；三卡/结果/许愿条各自在所属净区居中；正文与右侧人物、鼠耳、道具轮廓分离；缩略图主标题和三项关系可辨，详细数值另由长图和广告展示。通过。 |

所有语义组按实际字形（艺术字含描边/阴影）边界居中。见各页group-audit.json，safeRect来自本轮实际底图实测；手游须知收窄并向内移动，未通过扩大安全区掩盖问题。价格最长金额组在真实列中心，短金额共用右沿和r列，未推到边缘。

### 基线、字距、字重与文件一致性

共${pairs.length}组独立数字/单位校验。最大baseline差${maxBaselineDelta.toFixed(6)}原生px；数字单位最小可见间隙${minGap.toFixed(3)}原生px；r/w可见字高占数字${(minRatio*100).toFixed(2)}%–${(maxRatio*100).toFixed(2)}%。端手游及广告价格列单位x和数字右沿差均0；广告和封面阈值w列差0。须知为30号字，390px等效${(390/836*30).toFixed(2)}px；未发生maxWidth暗中缩字，Regular与真实Bold选择性混排，无可见字形相交。自动检查仅作辅助，上表实际成图已打开复查。

review/final-metric-audit.json记录最终PNG SHA-256，output/final-validation.json记录尺寸与检查版本。3200px输出文字直接由矢量渲染；底图原生836×1881、836×1882、1086×1448、1672×941，保留在assets/backgrounds。SVG为位图背景+矢量文字，非整图全矢量。转曲SVG不依赖字体；可改文字SVG依赖Microsoft YaHei UI、Bahnschrift，标题保留路径。
`;
await fs.appendFile(path.join(project,'review/visual-qa.md'),qa);
const cards=finalRecords.map(p=>'<section><h2>'+p.label+'</h2><a href="../'+p.png+'"><img src="'+p.id+'.png" alt="'+p.label+'"></a><p><a download href="../'+p.png+'">PNG '+p.width+'×'+p.height+'</a> · <a download href="../'+p.outlinedSVG+'">转曲 SVG</a> · <a download href="../'+p.editableSVG+'">可改文字 SVG</a></p></section>').join('');
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>兴安电竞 · 沙保连连看 · 全新套图</title><style>body{margin:0;background:#f5f1e4;color:#213f53;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1450px;margin:auto;padding:28px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:28px}section{background:#fffaf0;padding:18px;border-radius:12px}img{width:100%;display:block;height:auto}a{color:#22756b}h1{font-size:28px}h2{font-size:21px}@media(max-width:700px){.grid{grid-template-columns:1fr}main{padding:14px}}</style><main><h1>兴安电竞 · 沙保连连看</h1><p>全新插画与四张底图 · 三项缺一不可 · 同局全满足且成功撤离才达标</p><p>端游连续：688r / 1288r；手游连续：588r / 888r。带出物资严格大于端游888w / 手游788w。</p><p><a href="../output/campaign-sand-safe-redesign-r1.zip">下载全部成品与源文件</a> · <a href="visual-qa.md">查看排版复检记录</a></p><div class="grid">'+cards+'</div><p>包内含PNG、两种SVG、内容数据和排版源文件。文字与数字为矢量，插画背景为嵌入位图。跨设备保持版式可使用转曲SVG。</p></main></html>';
await fs.writeFile(path.join(project,'review/index.html'),html);
const delivery='# 兴安电竞 · 沙保连连看 · redesign-r1\n\n已完成插画确认、无字底图确认、矢量填字、实际成图与390px手机预览复检。\n\n'+finalRecords.map(p=>'- '+p.label+'：'+p.width+'×'+p.height+'；背景原生'+p.nativeWidth+'×'+p.nativeHeight+'。').join('\n')+'\n\n端游连续两次688r、三次1288r；手游588r、888r。门槛严格>888w/>788w。\n\nPNG用于发布；转曲SVG保留矢量文字且不依赖字体；可改文字SVG依赖Microsoft YaHei UI、Bahnschrift，艺术标题为路径。背景为位图，保留原始分辨率和来源记录。\n\n重排：使用Node和Sharp，运行source/workflow.mjs layout / render --project <项目绝对路径> --modules <node_modules>。content.json为唯一业务数据源，排版调整写入layout-adapter.mjs，最终必须重新进行实际成图检查。\n';
await fs.writeFile(path.join(project,'output/DELIVERY.md'),delivery);
const files=new Set(['content.json','layout.json','layout-adapter.mjs','campaign-state.json','output/manifest.json','output/final-validation.json','output/DELIVERY.md','review/index.html','review/visual-qa.md','review/final-metric-audit.json','review/final-overview.png','review/price-update-preview.png','02-layouts/generated-background-measurements.json','01-illustrations/sand-safe-medic-mouse-redesign-r1.png','01-illustrations/prompt.txt','01-illustrations/image-metadata.json']);
for(const p of finalRecords){for(const f of [p.png,p.outlinedSVG,p.editableSVG,p.preview,'assets/backgrounds/'+p.id+'.png','review/'+p.id+'-phone-390.png','review/'+p.id+'-text-bounds.json','review/'+p.id+'-text-overlaps.json','review/'+p.id+'-group-audit.json'])files.add(f);}
for(const f of await fs.readdir(path.join(project,'review/final-crops')))files.add('review/final-crops/'+f);
for(const f of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','prepare-final-review.mjs'])files.add('source/'+f);
for(const f of await fs.readdir(path.join(project,'03-backgrounds')))if(f.endsWith('.txt'))files.add('03-backgrounds/'+f);
const sorted=[...files].sort(),entries=[];
for(const name of sorted){const buffer=await fs.readFile(path.join(project,name));entries.push({file:name,bytes:buffer.length,sha256:createHash('sha256').update(buffer).digest('hex')});}
await fs.writeFile(path.join(project,'output/package-files.json'),JSON.stringify({revision:'redesign-r1',files:entries},null,2));
console.log(JSON.stringify({finalValidation:validation.numericPairCount,baseline:maxBaselineDelta,gap:minGap,ratio:[minRatio,maxRatio],packageFileCount:entries.length,bytes:entries.reduce((s,e)=>s+e.bytes,0)}));
