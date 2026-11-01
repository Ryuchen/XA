import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),sharp=loadSharp();
const readJSON=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const content=await readJSON('content.json'),manifest=await readJSON('output/manifest.json'),layout=await readJSON('layout.json'),placement=await readJSON('review/placement-record.json');
const img=async(id,x,y,w,h)=>`<image href="data:image/png;base64,${(await fs.readFile(path.join(root,'review',id+'.png'))).toString('base64')}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const tx=(x,y,t,size=28,fill='#203F53')=>`<text x="${x}" y="${y}" fill="${fill}" font-family="Microsoft YaHei UI" font-weight="600" font-size="${size}" dominant-baseline="central">${t}</text>`;
let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="2620"><rect width="2400" height="2620" fill="#F7F6EF"/>${tx(60,75,'兴安电竞 · 限时清图单 · 成品总览',47)}${tx(60,138,'四张价目 + 横版封面 + 简洁宣发海报 ｜ 艺术字、正文与数字保留矢量',27,'#6A8391')}`;
for(let i=0;i<content.tiers.length;i++){const t=content.tiers[i],x=60+i*585;svg+=tx(x,210,`${t.platform} · ${t.mode} · ${t.rows.length}档`,31)+await img(t.id,x,250,525,1246.875);}
svg+=tx(60,1570,'横版封面 · 3200 × 1800',31)+tx(1490,1570,'简洁宣发 · 2400 × 3200',31);
svg+=await img('cover',60,1620,1340,754)+await img('social',1490,1620,670,893.333);
svg+=tx(60,2440,'价目海报 3200 × 7600 ｜ PNG · 可改文字 SVG · 转曲 SVG',28)+tx(60,2500,'24档新价目；常规及16/14/12/10/8分钟，限时档未清图继续打。',25,'#6A8391')+tx(60,2560,'文字按目标尺寸从矢量渲染；人物与背景为内嵌位图。',24,'#6A8391')+'</svg>';
await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review/final-overview.png'));

const measurements=[];
for(const t of content.tiers){
 const b=await readJSON('review/'+t.id+'-text-bounds.json'),first=role=>b.find(e=>e.role===role);
 const pn=first('price-number'),pu=first('price-unit'),dn=first('deadline-number'),du=first('deadline-unit'),wn=first('minimum-loot-number'),wu=first('minimum-loot-unit');
 measurements.push({id:t.id,rows:t.rows.length,priceUnitColumn:pu.x,deadlineUnitColumn:du.x,
  visibleUnitRatios:{r:pu.height/pn.height,minutes:du.height/dn.height,w:wu.height/wn.height},
  verticalCenter:'每行数字与单位按可见字形居中于同一cy；正文按单行或多行组居中',
  tiers:t.rows.map(r=>({priceR:r.priceR,deadlineMinutes:r.deadlineMinutes,tierType:r.tierType})),
  renderedValues:b.filter(v=>['price-number','deadline-number','regular-tier'].includes(v.role)).map(v=>({role:v.role,text:v.text,cy:v.cy})),
  deadlineUnitToRightBorder:767-(du.x+du.width),layout:layout.pages.find(p=>p.id===t.id).alignment});
}
const socialBounds=await readJSON('review/social-text-bounds.json'),socialLayout=layout.pages.find(p=>p.id==='social');
const socialMeasurements=socialLayout.alignment.map(c=>({id:c.id,...c,rows:c.rowCenters.map(cy=>{
 const price=socialBounds.find(v=>v.role==='price-number'&&v.cy===cy&&v.x>=c.colEdges[0]&&v.x<c.colEdges[1]);
 const unit=socialBounds.find(v=>v.role==='price-unit'&&v.cy===cy&&v.x===c.priceUnitColumn);
 const time=socialBounds.find(v=>v.role==='deadline-number'&&v.cy===cy&&v.x>=c.colEdges[1]&&v.x<c.colEdges[2]);
 const minute=socialBounds.find(v=>v.role==='deadline-unit'&&v.cy===cy&&v.x===c.deadlineUnitColumn);
 return {cy,priceR:Number(price.text),rVisibleRatio:unit.height/price.height,priceToUnitGap:unit.x-price.x-price.width,
  deadlineMinutes:time?Number(time.text):null,minuteUnitToBorder:minute?c.colEdges[2]-minute.x-minute.width:null};
})}));
await fs.writeFile(path.join(root,'review/alignment-record.json'),JSON.stringify({fullPosters:measurements,social:socialMeasurements},null,2));
const minSocialMargin=Math.min(...socialMeasurements.flatMap(c=>c.rows.map(r=>r.minuteUnitToBorder).filter(v=>v!==null)));
await fs.writeFile(path.join(root,'review/final-notes.md'),`# 限时清图单 · 最终排版记录

## 本轮内容

- 共24档：端游/手游 × 绝密/机密，各六档；四张完整价目及宣发共用content.json。
- 常规档不额外添加分钟限制。限时档为16、14、12、10、8分钟，使用本轮文字替换旧参考的价格和7分钟档。
- 机密统一写机密航天；仅限对应模式航天。绝密规则沿用本轮参考图，机密按用户补充完整文本。
- 清图方式满足其一：陪玩拉绝户闸撤离（同时航天无人走飞升，仅丢包可以有人撤离）；或规定时限内陪玩击杀≥8人。
- 最低要求改为等价的正向展示：成功撤离时，陪玩击杀≥4人“且”撤离物资绝密≥600w / 机密≥300w；缺一不算清图成功。
- 成功达标前，成功撤离带出物资计入累计；对应炸单保底高于累计物资保底时，在打满结单条件后再补保底。未补写未知的炸单保底数额。
- 房卡在清图后开启；未开仍按俱乐部规定补偿。包含航天核心区金红卡；绝密缺金卡每张+200w、红卡+300w，机密+100w/+150w。
- 速撤需在规定时限内满足击杀人头数，且物资严格>900w（绝密）或>500w（机密）；保留严格大于，未改为≥。
- 简洁宣发包含全部24档、限时档保障及清图/最低要求概要，省去完整下单须知。

## 对齐与视觉

- 标题采用逐字轮廓艺术字，奶白描边、柔和错位阴影与轻微角度变化。四张表头保持整条深蓝。
- 完整版r固定x=305；分钟内固定x=621。数字分别右对齐于297及612，按可见字形中心与单位同cy。
- r可见字高约${(measurements[0].visibleUnitRatios.r*100).toFixed(1)}%，w约${(measurements[0].visibleUnitRatios.w*100).toFixed(1)}%。时间单位距表格右边约${measurements[0].deadlineUnitToRightBorder.toFixed(1)}px（原生底图坐标）。
- 宣发卡片r及分钟各自固定列；时间单位距单元格右边最少${minSocialMargin.toFixed(1)}px。
- w用于最低物资门槛、缺卡补偿及速撤门槛，数字与w分开定位。
- 最长价格1688r、1228r与两位分钟值均已查看；拉闸说明与图标留白、宣发物资门槛间距已调整。
- 按用户“需要额外注意下文字在底图上面呈现的位置”逐区修正：四张主标题与版本条间距由3.5px增加至${placement[0].titleToPillGap.toFixed(1)}px；两行艺术字之间保留约6.1px；所有数值均为原生底图坐标。
- 清图标准：拉闸正文在左卡片居中，三条条件完整保留；右卡片将“规定时限内陪玩击杀”和≥8人独立成组，最低要求仍保留“且”。
- 房卡保障：两行开卡/包含范围说明，下接金卡与红卡两个补偿条；标签与金额紧密关联，按整组视觉范围居中；两侧补偿w固定在各自列。
- 打手保障：上方显示规定时限与击杀前提，下方将“且物资 > 门槛 → 允许速撤”放入同一信息条，严格大于和前提均保留；整组在行内居中。
- 封面版本说明置于y=749–782的干净留白带，实际字形y=755.2–776.8，避开下方前景箱子。宣发底部文字保留在实际卡片边界内。

## 导出尺寸

${manifest.map(p=>`- ${p.label}：${p.width}×${p.height}；底图原生 ${p.nativeWidth}×${p.nativeHeight}`).join('\n')}

PNG文字由矢量直接按目标尺寸渲染。可改文字SVG保留正文/数字文字对象，艺术字为路径；转曲SVG全部文字为路径。人物及背景为内嵌位图；放大背景不代表原生高清绘制。
`);
await fs.writeFile(path.join(root,'README.md'),`# 兴安电竞 · 限时清图单

六张成品已完成：四张六档价目、横版封面、简洁宣发海报。打开review/index.html逐张查看与下载。

- output/png：高清PNG
- output/svg-editable：正文及数字可改文字SVG，艺术标题保留路径
- output/svg-outlined：全部文字转曲SVG，无字体依赖
- review/final-overview.png：成品总览
- review/final-notes.md：业务口径、排版和分辨率说明
- review/alignment-record.json：单位列、字高与边距记录
- review/placement-record.json：标题、正文组与实际底图留白的定位记录
- content.json：本轮24档价目与规则的唯一业务数据源
- layout-adapter.mjs：六页矢量布局

## 修改与重建

使用本机Node及Sharp，从项目根目录运行（字体轮廓工具依赖Windows）：

\`\`\`powershell
node source/workflow.mjs layout --project <项目绝对路径>
node source/workflow.mjs render --project <项目绝对路径>
node source/measure-placement.mjs
node source/build-overview.mjs
node source/workflow.mjs package --project <项目绝对路径> --revision timed-clear-final-r2
\`\`\`

仅修改部分页面时可传--only desktop-absolute,social；价格变更应同步宣发。渲染器自动复用未修改页面及字体缓存。字体为Microsoft YaHei UI、Bahnschrift、Noto Sans SC Black，转曲版不依赖字体。缓存不随成品包交付，重新渲染将由本机字体生成。

## 确认记录

插画与无字底图均已获用户“继续”确认，依据见campaign-state.json。阶段查看页仅保留在原项目review/backgrounds.html、review/illustration.html；成品包以review/index.html查看最终稿。机密规则原文见references/secret-rules-user.md。正式文案与艺术字在本轮填入，无需重复确认已采用的底图。

背景为位图，文字为矢量层；PNG按目标尺寸渲染。原生图和生成提示词保留在03-backgrounds，assets/backgrounds为渲染副本。
`);
const state=await readJSON('campaign-state.json');
state.vectorDelivery={status:'completed',overview:'review/final-overview.png',review:'review/index.html',notes:'review/final-notes.md',formats:['PNG','editable text SVG','outlined SVG'],tierCount:24,rendering:'Final-size vector typography over approved raster backgrounds'};
state.textPlacementRefinement={revision:'timed-clear-final-r2',basis:'用户要求注意文字在底图上位置，并指出清图标准、房卡保障与打手保障展示效果需改善',record:'review/placement-record.json',detailPreview:'review/revised-sections.png',changes:['四张主标题与版本条保持约12.8px间距，艺术字两行留白约6.1px','清图两种方式在卡片内分组居中，击杀目标独立突出','房卡说明与金卡/红卡补偿条分组，金额和标签紧密关联','速撤击杀条件置于上方，物资门槛与允许速撤放入同一信息条','封面版本说明避开前景箱子','保留固定r/w列与数字、单位的可见字形垂直居中'],reviewedPages:['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret','cover','social']};
state.nextStep='本轮六张成品已完成；后续按需改价或调整文字，继续复用已确认底图与矢量布局';
state.package='campaign-'+state.revision+'.zip';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({overview:'review/final-overview.png',alignment:measurements.map(({id,visibleUnitRatios,deadlineUnitToRightBorder})=>({id,visibleUnitRatios,deadlineUnitToRightBorder})),socialMinimumRightMargin:minSocialMargin}));
