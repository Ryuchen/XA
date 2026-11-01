import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),sharp=loadSharp();
const readJSON=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const content=await readJSON('content.json'),manifest=await readJSON('output/manifest.json'),layout=await readJSON('layout.json');
const img=async(id,x,y,w,h)=>`<image href="data:image/png;base64,${(await fs.readFile(path.join(root,'review',id+'.png'))).toString('base64')}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const tx=(x,y,t,size=28,fill='#203F53')=>`<text x="${x}" y="${y}" fill="${fill}" font-family="Microsoft YaHei UI" font-weight="600" font-size="${size}" dominant-baseline="central">${t}</text>`;
let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="2490"><rect width="2400" height="2490" fill="#F7F6EF"/>${tx(60,75,'兴安电竞 · 物资对赌单 · 成品总览',47)}${tx(60,138,'四张价目 + 横版封面 + 简洁宣发海报 ｜ 艺术字、正文与数字保留矢量',27,'#6A8391')}`;
for(let i=0;i<content.tiers.length;i++){
 const t=content.tiers[i],x=60+i*585;
 svg+=tx(x,210,`${t.platform} · ${t.mode} · 9档`,31)+await img(t.id,x,250,525,1182);
}
svg+=tx(60,1495,'横版封面 · 3200 × 1800',31)+tx(1490,1495,'简洁宣发 · 2400 × 3200',31);
svg+=await img('cover',60,1545,1340,754)+await img('social',1490,1545,670,893.333);
svg+=tx(60,2370,'价目海报 3200 × 7200 ｜ PNG · 可改文字 SVG · 转曲 SVG',28)+tx(60,2426,'36档新价目；单局严格大于目标，未达继续。文字由矢量渲染，插画为位图。',25,'#6A8391')+'</svg>';
await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review/final-overview.png'));

const measurements=[];
for(const t of content.tiers){
 const b=await readJSON('review/'+t.id+'-text-bounds.json'),first=role=>b.find(e=>e.role===role);
 const pn=first('price-number'),pu=first('price-unit'),tn=first('threshold-number'),tu=first('threshold-unit');
 const labels=b.filter(v=>['price-number','threshold-number','strict-comparison'].includes(v.role));
 measurements.push({id:t.id,rows:9,priceUnitColumn:pu.x,thresholdUnitColumn:tu.x,
  visibleUnitRatios:{r:pu.height/pn.height,w:tu.height/tn.height},
  verticalCenter:'数字、单位与比较符按可见字形分别居中于同一行中心',
  maps:t.maps.map(m=>({name:m.name,tiers:m.rows.map(r=>({priceR:r.priceR,comparison:r.comparison,thresholdW:r.thresholdW}))})),
  renderedLabels:labels.map(v=>({role:v.role,text:v.text,cy:v.cy})),
  unitRightMargin:787-(tu.x+tu.width),layout:layout.pages.find(p=>p.id===t.id).alignment});
}
const socialBounds=await readJSON('review/social-text-bounds.json'),socialLayout=layout.pages.find(p=>p.id==='social');
const socialMeasurements=socialLayout.alignment.map(c=>({
 id:c.id,priceUnitColumn:c.priceUnitColumn,
 columns:c.thresholdUnitColumns.map(col=>({map:col.map,unitColumn:col.x,rows:c.rowCenters.map(cy=>{
  const within=v=>v.cy===cy&&v.x>=col.cellLeft&&v.x<col.cellRight;
  const num=socialBounds.find(v=>v.role==='threshold-number'&&within(v));
  const unit=socialBounds.find(v=>v.role==='threshold-unit'&&within(v));
  const operator=socialBounds.find(v=>v.role==='strict-comparison'&&within(v));
  return {cy,number:num.text,comparison:operator.text,unitVisibleRatio:unit.height/num.height,
   comparisonToNumberGap:num.x-(operator.x+operator.width),numberToUnitGap:unit.x-(num.x+num.width),unitToBorderGap:col.cellRight-(unit.x+unit.width)};
 })}))
}));
await fs.writeFile(path.join(root,'review/alignment-record.json'),JSON.stringify({fullPosters:measurements,social:socialMeasurements},null,2));
const minSocialBorder=Math.min(...socialMeasurements.flatMap(c=>c.columns.flatMap(col=>col.rows.map(r=>r.unitToBorderGap))));
await fs.writeFile(path.join(root,'review/final-notes.md'),`# 物资对赌单 · 最终排版记录

## 内容

- 36档：端游与手游、绝密与机密各三张地图，每张地图三档。完整版和宣发矩阵均来自 content.json。
- 所有目标均为严格大于（>）；等于目标仍未达标。不跨局累计，未达继续打。
- 绝密包含航天、巴克什、监狱；机密包含核电站、巴克什、航天。按本轮清单排除大坝。
- 达标前全部物资归板板；对应炸单保底满足结单条件后另补。未提供的炸单补偿金额未新增。
- 必须由陪玩理包；陪玩不允许自带空白存储洗给板板；达到结单要求后打手允许速撤。
- 航天核心区金红卡：绝密缺金卡每张+200w、红卡+300w；机密缺金卡+100w、红卡+150w。
- “125以上的六套、龟包、金胸挂、满红头修/甲修，不算假账”沿用确认口径；端手游规则一致。
- 宣发图保留四类全部36档、严格大于条件、保障及必要前提，不添加下单须知。

## 排版

- 关键标题逐字转曲，采用奶白描边、柔和错位阴影及轻微角度变化。
- 完整版 r 固定列310、w固定列698；数字右对齐，单位分开定位，数字与单位间隙8px（原生底图坐标）。
- r可见字高约${(measurements[0].visibleUnitRatios.r*100).toFixed(1)}%，w约${(measurements[0].visibleUnitRatios.w*100).toFixed(1)}%；w距表格右边约${measurements[0].unitRightMargin.toFixed(1)}px。
- 宣发矩阵每卡共用价格列，三地图分别固定w列；目标单元格均标>。w距单元格右边最少${minSocialBorder.toFixed(1)}px。
- 文案按可见字形或多行组边界在所属区域内垂直居中；精简重复语句、减少换行。
- 价目表头整条深蓝；暖金/薄荷青用于目标数字及浅底，无半幅绿色标题栏，数字右侧无装饰图标。
- 已查看四张完整价目、封面和宣发图，核对本轮改价、地图、比较符、规则与最长数字行。

## 交付尺寸

${manifest.map(p=>`- ${p.label}：${p.width}×${p.height}；底图原生 ${p.nativeWidth}×${p.nativeHeight}`).join('\n')}

PNG文字由矢量直接按目标尺寸渲染。可改文字SVG保留正文和数字文字对象，艺术字为路径；转曲SVG文字均为路径。插画为内嵌位图，背景放大不代表原生高清绘制。

## 修改与重建

修改content.json后运行source/workflow.mjs layout，再render、build-overview与package。版式调整写入layout-adapter.mjs；改价复用底图和字体缓存。命令见README.md。
`);
await fs.writeFile(path.join(root,'README.md'),`# 兴安电竞 · 物资对赌单

共六张成品：四张9档价目、横版封面、36档简洁宣发图。打开review/index.html逐张查看和下载。

- output/png：高清PNG
- output/svg-editable：可改正文和数字的SVG，艺术字为路径
- output/svg-outlined：全部文字转曲的SVG，跨设备无字体依赖
- review/final-overview.png：成品总览
- review/final-notes.md：内容口径、尺寸和排版说明
- review/alignment-record.json：r/w对齐、可见字高及边距记录

## 后续修改

使用本机Node和Sharp运行，字体轮廓工具依赖Windows。运行命令时从项目根目录开始：

\`\`\`powershell
node source/workflow.mjs layout --project <项目绝对路径>
node source/workflow.mjs render --project <项目绝对路径>
node source/build-overview.mjs
node source/workflow.mjs package --project <项目绝对路径> --revision challenge-final-r2
\`\`\`

唯一业务数据源content.json；布局源layout-adapter.mjs。只导出部分页面可传--only desktop-absolute,social。改价也应更新宣发图；缓存会跳过未修改页面。正文Microsoft YaHei UI，数字Bahnschrift，艺术字Noto Sans SC Black。字体缓存不随成品包交付，重渲染时由本机字体重新建立。

插画、底图及玩法口径已确认，依据见campaign-state.json。插画与背景为内嵌位图；文字为矢量层，最终PNG按目标尺寸渲染。
`);
const state=await readJSON('campaign-state.json');
state.vectorDelivery={status:'completed',overview:'review/final-overview.png',review:'review/index.html',notes:'review/final-notes.md',formats:['PNG','editable text SVG','outlined SVG'],rendering:'Final-size vector typography over approved raster backgrounds',tierCount:36,completedAt:'2026-09-29'};
state.nextStep='本轮六张成品已完成；后续改价或修改文案时复用已确认底图和矢量布局';
state.package='campaign-'+state.revision+'.zip';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({overview:'review/final-overview.png',fullPosterAlignment:measurements.map(({id,visibleUnitRatios,unitRightMargin})=>({id,visibleUnitRatios,unitRightMargin})),socialMinimumUnitToBorderGap:minSocialBorder}));
