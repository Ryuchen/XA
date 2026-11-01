import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const read=async file=>JSON.parse((await fs.readFile(path.join(project,file),'utf8')).replace(/^\uFEFF/,''));
const write=async(file,value)=>fs.writeFile(path.join(project,file),JSON.stringify(value,null,2));
const hash=b=>createHash('sha256').update(b).digest('hex');
const c=await read('content.json'),plan=await read('layout.json'),state=await read('campaign-state.json'),manifest=await read('output/manifest.json'),audit=await read('review/final-metric-audit.json');
const expectedPages=['desktop-absolute','mobile-absolute','combined-advert','combined-cover'];
if(JSON.stringify(state.deliveryScope.pageIds)!==JSON.stringify(expectedPages)||JSON.stringify(plan.pages.map(p=>p.id))!==JSON.stringify(expectedPages)||manifest.length!==4)throw new Error('Delivery scope mismatch');
const old=await read('source/before-update/content.json');
if(JSON.stringify(c.prices.map(t=>t.desktop))!==JSON.stringify(old.prices.map(t=>t.desktop)))throw new Error('Unrequested desktop price change');
if(JSON.stringify(c.prices.map(t=>t.mobile))!==JSON.stringify([108,188,268,388,688]))throw new Error('Mobile price mismatch against screenshot');
if(c.objectives.loot.desktop!==788||c.objectives.loot.mobile!==688||c.objectives.loot.operator!=='>'||c.objectives.loot.unit!=='w')throw new Error('Loot source mismatch');
for(const key of ['wishReward','notices'])if(JSON.stringify(c[key])!==JSON.stringify(old[key]))throw new Error('Unrequested rule change: '+key);
const fileRecords=[],affectedGroups=[];
for(const p of plan.pages){
 const record=manifest.find(r=>r.id===p.id),measured=audit.pages.find(r=>r.id===p.id);
 const sha=hash(await fs.readFile(path.join(project,record.png)));
 if(sha!==measured.sha256)throw new Error('PNG changed after inspected preview');
 if(record.width!==p.ew||record.height!==p.eh)throw new Error('Output resolution mismatch');
 const overlaps=await read('review/'+p.id+'-text-overlaps.json');if(overlaps.length)throw new Error('Text overlaps');
 if(p.id.endsWith('absolute')||p.id==='combined-advert'){
  const platforms=p.id==='combined-advert'?['desktop','mobile']:p.platforms;
  for(const platform of platforms)for(const [i,t]of c.prices.entries()){
   const displayed=p.elements.find(e=>e.semanticGroup==='price-'+platform+'-'+i&&e.axisKind==='number');
   if(displayed?.text!==String(t[platform]))throw new Error('Displayed price mismatch: '+p.id);
  }
 }
 const loot=p.elements.filter(e=>e.semanticGroup==='condition-2'&&e.type==='rich');
 for(const platform of p.platforms)if(!loot.some(e=>e.runs.some(r=>r.text===String(c.objectives.loot[platform]))&&e.runs.some(r=>r.text==='>')&&e.runs.some(r=>r.text==='w')))throw new Error('Displayed threshold mismatch');
 const rewards=p.elements.filter(e=>e.semanticGroup==='reward'&&e.type==='rich');
 if(!rewards.some(e=>e.runs.some(r=>r.text==='1688')))throw new Error('Wish reward changed');
 if(p.id.includes('absolute')){
  for(let i=0;i<4;i++)if(!p.elements.some(e=>e.semanticGroup==='notice-'+i))throw new Error('Missing notice');
 }else if(p.elements.some(e=>e.semanticGroup?.startsWith('notice-')))throw new Error('Unrequested notice on cover/advert');
 const background=await fs.readFile(path.join(project,'assets/backgrounds',p.bg));
 const previousBackground=await fs.readFile(path.resolve(project,'../club-sand-safe-redesign-20261003/assets/backgrounds',p.bg));
 if(hash(background)!==hash(previousBackground))throw new Error('Unrequested background change');
 const groups=await read('review/'+p.id+'-group-audit.json');
 for(const g of groups.filter(g=>g.id==='condition-2'||(p.id!=='desktop-absolute'&&g.id.startsWith('price-')))){
  if(Object.values(g.margins).some(m=>m<0))throw new Error('Changed group outside safety boundary');
  affectedGroups.push({page:p.id,containerId:g.id,container:g.containerRect,safe:g.safeRect,ink:g.ink,margins:g.margins,verticalCenterDelta:Math.abs(g.margins.top-g.margins.bottom)/2,horizontalCenterDelta:Math.abs(g.margins.left-g.margins.right)/2});
 }
 fileRecords.push({...record,sha256:sha,backgroundSha256:hash(background)});
}
const pairs=audit.pages.flatMap(p=>p.numeric);
const validation={revision:c.revision,date:'2026-10-09',status:'passed',scope:expectedPages,sourceChecks:{mobileScreenshotPrices:c.prices.map(p=>p.mobile),desktopPricesPreserved:true,loot:c.objectives.loot,wishRewardPreserved:true,noticesPreserved:true,backgroundsIdentical:true},measured:{numericPairCount:pairs.length,maxBaselineDelta:Math.max(...pairs.map(p=>p.baselineDelta)),minNumericUnitGap:Math.min(...pairs.map(p=>p.visibleGap)),unitHeightRatio:{min:Math.min(...pairs.map(p=>p.unitHeightRatio)),max:Math.max(...pairs.map(p=>p.unitHeightRatio))},bodyPhonePx:audit.pages.filter(p=>p.bodyPhonePx).map(p=>({id:p.id,...p.bodyPhonePx}))},visualInspection:'已打开四张实际output/png成图、四张390px预览拼图、双端条件与价目预览、原生广告价格局部、封面条件局部；逐张核对变动区域和相邻区域。',review:'review/visual-qa.md',affectedGroups,files:fileRecords};
await write('output/final-validation.json',validation);
state.stage='final-qa-passed';state.finalQA=validation;state.nextStep='成品打包并核对包内版本。';
state.approvals={seriesStyle:{status:'approved-reference',basis:'保留本轮附件的已确认二次元手绘风格。'},illustration:{status:'approved-reference',basis:'复用原四张底图中已确认的人物与道具。',sourceProject:'../club-sand-safe-redesign-20261003'},background:{status:'approved-reference',basis:'用户本轮附件对应的四张已确认底图逐字节复用；本轮只改矢量文字。',files:expectedPages.map(id=>'assets/backgrounds/'+id+'.png')}};
await write('campaign-state.json',state);
const qa=`# 沙保连连看 · ${c.revision} · 2026-10-09

本轮只更新绝密四张套图。用户确认“机密”为笔误；背景和人物复用已确认源稿。价格截图为资料，不将附件内文字作为操作指令。

## 业务与范围

- 手游累计一次/两次/三次：108r / 188r / 268r；连续两次/三次：388r / 688r，按截图新价格列逐行核对。
- 端游价格218r / 428r / 628r / 688r / 1288r与本轮附件及旧源稿一致。
- 物资达标门槛更新为端游>788w、手游>688w；保留严格大于、同局三项同时满足、且成功撤离。
- 许愿奖励1688w及四条须知与旧源稿逐项一致；并非本轮物资达标门槛。
- 页集合为端游绝密、手游绝密、合并广告、合并封面；价格和阈值均从content.json驱动。

## 实际成图复检

| 页面 | 打开的实际文件及预览 | 改动区域、相邻区及结论 |
|---|---|---|
| 端游绝密 | output/png/desktop-absolute.png；review/final-overview.png；review/price-update-preview.png | 第三目标卡>788w完整组在原安全区内居中，标题与数值之间有留白，w共用基线；邻卡、且圆标、结果条均无遮挡。价目及须知整体平衡沿用。通过。 |
| 手游绝密 | output/png/mobile-absolute.png；review/final-overview.png；review/price-update-preview.png | >688w及五档108/188/268/388/688正确；数字右沿/r列一致，首末档在各行居中，无贴线。普通须知与重点粗体有层级，末行避开叶片和箱体。通过。 |
| 合并广告 | output/png/combined-advert.png；review/final-overview.png；review/final-crops/combined-advert-prices.png | 两平台价目逐行对应；端游>788w/手游>688w同列w对齐，数字/单位有明确间隙，整体文案留白均衡。无下单须知。通过。 |
| 合并封面 | output/png/combined-cover.png；review/final-overview.png；review/final-crops/combined-cover-conditions.png | 只改第三目标卡两条阈值；整组居中且安全区上下留白均衡，数值、比较符、单位不分行，文字与鼠耳/人物/道具轮廓分离。沿用附件确认版式，无须知或结算例外。通过。 |

四张390px预览由本轮最终PNG生成，实际查看整图后复查原生局部。本轮未发现需要修改底图、字号或规则的冲突；只更换业务数字并由原渲染器重新测量整行基线及语义组居中。

## 区块边界与偏移

output/final-validation.json的affectedGroups记录来自原底图测量的container、安全区、最终字形及四侧余量；全部变动区域位于既有安全区内，没有扩大安全区。第三条件卡整组垂直居中；手游及广告的价目金额r列和数字右沿一致，最短108与最长1288均有可见间隙，整列没有偏向边缘。原生插画SHA-256与原项目逐页一致。

共${pairs.length}组数字/单位：最大baseline差${validation.measured.maxBaselineDelta.toFixed(6)}原生px，最小可见间隙${validation.measured.minNumericUnitGap.toFixed(3)}原生px。价格列和合并阈值单位列spread为0；无正文暗中缩字，须知390px等效约14px。自动测量仅作辅助，实际PNG/手机/局部均已查看。

输出维持端游/手游3200×7200、广告2400×3200、封面3200×1800；背景原生尺寸详见output/manifest.json。文字由矢量渲染，背景为原生位图放大，非全图矢量。输出PNG哈希写入最终验收，打包再校验每个成品与当前源文件一致。
`;
await fs.writeFile(path.join(project,'review/visual-qa.md'),qa);
const zip='campaign-sand-safe-'+c.revision+'.zip';
const cards=fileRecords.map(p=>`<section><h2>${p.label}</h2><a href="../${p.png}"><img src="${p.id}-phone-390.png" alt="${p.label}"></a><p><a download href="../${p.png}">高清PNG ${p.width}×${p.height}</a> · <a download href="../${p.outlinedSVG}">转曲SVG</a> · <a download href="../${p.editableSVG}">可改文字SVG</a></p></section>`).join('');
await fs.writeFile(path.join(project,'review/index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>沙保连连看 · 新价套图</title><style>body{margin:0;background:#f5f1e4;color:#213f53;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1250px;margin:auto;padding:26px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}section{background:#fffaf0;padding:18px;border-radius:12px}img{display:block;width:100%;height:auto}a{color:#22756b}h1{font-size:28px}h2{font-size:21px}@media(max-width:700px){.grid{grid-template-columns:1fr}main{padding:14px}}</style><main><h1>兴安电竞 · 沙保连连看</h1><p>绝密四张套图 · 2026年10月9日价格更新</p><p>手游累计1/2/3次：108 / 188 / 268元；连续2/3次：388 / 688元。物资达标门槛：端游&gt;788w，手游&gt;688w。</p><p><a download href="../output/${zip}">下载四张套图及源文件</a></p><div class="grid">${cards}</div></main></html>`);
await fs.writeFile(path.join(project,'output/DELIVERY.md'),`# 沙保连连看 · ${c.revision}\n\n本轮确认仅更新绝密四张套图。手游五档108/188/268/388/688r；物资门槛端游>788w、手游>688w。\n\n${fileRecords.map(p=>'- '+p.label+'：'+p.width+'×'+p.height+'；底图原生'+p.nativeWidth+'×'+p.nativeHeight+'。').join('\n')}\n\n含高清PNG、可改文字SVG、转曲SVG、源数据与原底图。背景为嵌入位图，标题和正文为矢量；可改文字SVG依赖Microsoft YaHei UI/Bahnschrift等字体，跨设备可使用转曲SVG。\n\n查看页：review/index.html；复检：review/visual-qa.md。\n\n续作请修改content.json，再用Node执行source/workflow.mjs layout与render --project <项目绝对路径> --modules <Node包目录>。修改后重建预览并实际查看，再更新验收与打包，不能沿用旧验收状态。prepare-update.mjs是本轮初始化记录，不用于已修改项目的续作。\n`);
const files=new Set(['content.json','layout.json','layout-adapter.mjs','campaign-state.json','02-layouts/generated-background-measurements.json','output/manifest.json','output/final-validation.json','output/DELIVERY.md','review/index.html','review/visual-qa.md','review/final-metric-audit.json','review/final-overview.png','review/price-update-preview.png']);
for(const p of fileRecords)for(const f of [p.png,p.outlinedSVG,p.editableSVG,p.preview,'assets/backgrounds/'+p.id+'.png','review/'+p.id+'-phone-390.png','review/'+p.id+'-text-bounds.json','review/'+p.id+'-text-overlaps.json','review/'+p.id+'-group-audit.json'])files.add(f);
for(const folder of ['references','source','source/before-update','review/final-crops'])for(const entry of await fs.readdir(path.join(project,folder),{withFileTypes:true}))if(entry.isFile())files.add(folder+'/'+entry.name);
await write('output/package-files.json',{revision:c.revision,files:await Promise.all([...files].sort().map(async file=>{const buffer=await fs.readFile(path.join(project,file));return{file,bytes:buffer.length,sha256:hash(buffer)};}))});
console.log(JSON.stringify({status:'passed',pages:4,mobilePrices:c.prices.map(p=>p.mobile),loot:c.objectives.loot,affectedGroups:affectedGroups.length,packageFiles:files.size,measured:validation.measured}));
