import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),sharp=loadSharp();
const readJSON=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const content=await readJSON('content.json'),manifest=await readJSON('output/manifest.json');
const img=async(id,x,y,w,h)=>`<image href="data:image/png;base64,${(await fs.readFile(path.join(root,'review',id+'.png'))).toString('base64')}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const tx=(x,y,t,size=28,fill='#203F53')=>`<text x="${x}" y="${y}" fill="${fill}" font-family="Microsoft YaHei UI" font-weight="600" font-size="${size}" dominant-baseline="central">${t}</text>`;
let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="2490"><rect width="2400" height="2490" fill="#F7F6EF"/>${tx(60,75,'兴安电竞 · 嘉豪物资单 · 成品总览',47)}${tx(60,138,'四张价目 + 横版封面 + 简洁宣发海报 ｜ 艺术字、正文与数字保留矢量',27,'#6A8391')}`;
for(let i=0;i<content.tiers.length;i++){const t=content.tiers[i],x=60+i*585;svg+=tx(x,210,`${t.platform} · ${t.mode} · ${t.rows.length}档`,31)+await img(t.id,x,250,525,1182);}
svg+=tx(60,1495,'横版封面 · 3200 × 1800',31)+tx(1490,1495,'简洁宣发 · 2400 × 3200',31);
svg+=await img('cover',60,1545,1340,754)+await img('social',1490,1545,670,893.333);
svg+=tx(60,2370,'价目海报 3200 × 7200 ｜ PNG · 可改文字 SVG · 转曲 SVG',28)+tx(60,2426,'16 档新价目；20分钟清图，两种方式任选。文字由矢量渲染，插画为位图。',25,'#6A8391')+'</svg>';
await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review/final-overview.png'));
const measurements=[];
for(const t of content.tiers){
 const b=await readJSON('review/'+t.id+'-text-bounds.json'),first=role=>b.find(e=>e.role===role);
 const pn=first('price-number'),pu=first('price-unit'),ln=first('loot-number'),lu=first('loot-unit');
 const labels=b.filter(v=>v.role==='price-number'||v.role==='loot-number');
 measurements.push({id:t.id,rows:t.rows.length,priceUnitColumn:pu.x,lootUnitColumn:lu.x,visibleUnitRatios:{r:pu.height/pn.height,w:lu.height/ln.height},verticalCenter:'数字与单位的可见字形分别居中于同一行中心',pricePairs:t.rows.map(r=>[r.priceR,r.lootW]),renderedNumbers:labels.map(v=>({text:v.text,cy:v.cy})),unitRightMargin:788-(lu.x+lu.width)});
}
await fs.writeFile(path.join(root,'review/alignment-record.json'),JSON.stringify(measurements,null,2));
await fs.writeFile(path.join(root,'review/final-notes.md'),`# 嘉豪物资单 · 最终排版记录\n\n## 内容\n\n- 共16档：端游与手游、绝密与机密各四档；完整版和广告共用 content.json。\n- 端游绝密末档为1299r / 4888w，按本轮用户文字。\n- 仅限对应模式航天；20分钟内完成拉闸清图或陪玩单局合计击杀8名敌方干员。\n- 拉闸附加条件保留用户确认的术语和关系。\n- 四项炸单条件按“任一成立”呈现；绝密每炸一把保底+100w，机密+60w。\n- 当局物资全送、不计订单累计，紧邻保障数字显示。\n- 背包门槛40格/30格；缺金卡补100w/60w、缺红卡补150w/90w。端手游规则一致。\n- 广告包含四类价目与保障必要条件，不增加下单须知。\n\n## 排版\n\n- 关键标题采用逐字转曲艺术字、奶白描边、柔和错位阴影及轻微角度变化。\n- 完整版r固定列287、w固定列698；数字右对齐，单位分开定位，间隙8px（原生底图坐标）。\n- r可见字高约${(measurements[0].visibleUnitRatios.r*100).toFixed(1)}%，w约${(measurements[0].visibleUnitRatios.w*100).toFixed(1)}%；物资单位距表格右边约${measurements[0].unitRightMargin.toFixed(1)}px。\n- 文案按可见字形边界在所属行或区域内垂直居中；正文优先减少换行。\n- 价目表头整条深蓝；数字右侧不放装饰图标。\n- 两种清图方式之间标“或”；四项条件独立卡片。\n\n## 交付\n\n${manifest.map(p=>`- ${p.label}：${p.width}×${p.height}；底图原生 ${p.nativeWidth}×${p.nativeHeight}`).join('\n')}\n\nPNG文字由矢量直接按目标尺寸渲染。可改文字SVG保留正文/数字文字对象，艺术字为路径；转曲SVG所有文字为路径。插画为内嵌位图，整张海报不是全矢量，放大背景不是原生高清绘制。\n\n## 修改与重建\n\n修改content.json相关数据，运行source/workflow.mjs layout，再render与package。版面调整写入layout-adapter.mjs；改价时复用底图与字体缓存。完整命令见README.md。\n`);
await fs.writeFile(path.join(root,'README.md'),`# 兴安电竞 · 嘉豪物资单\n\n成品位于 output/png；矢量版位于 output/svg-editable 与 output/svg-outlined；打开review/index.html查看全部六张。\n\n## 后续改价\n\n使用本机Node及Sharp运行（Windows字体轮廓工具）：\n\n\`\`\`powershell\nnode source/workflow.mjs layout --project <项目绝对路径>\nnode source/workflow.mjs render --project <项目绝对路径>\nnode source/build-overview.mjs\nnode source/workflow.mjs package --project <项目绝对路径> --revision jiahao-final-r2\n\`\`\`\n\n唯一业务数据源content.json；布局源layout-adapter.mjs。只导出部分页面可传--only desktop-absolute,social。缓存不随成品包交付，再次渲染会从本机字体建立缓存。正文使用Microsoft YaHei UI，数字Bahnschrift，艺术字Noto Sans SC Black。转曲SVG无需字体。\n\n插画和底图已由用户确认；价格、保障、清图方式、须知端手游一致的口径已确认。见campaign-state.json。\n`);
const state=await readJSON('campaign-state.json');
state.vectorDelivery={status:'completed',overview:'review/final-overview.png',review:'review/index.html',notes:'review/final-notes.md',formats:['PNG','editable text SVG','outlined SVG'],rendering:'Final-size vector typography over approved raster backgrounds',tierCount:16};
state.nextStep='成品已完成；后续按需修改具体档位或文案，继续复用已确认底图';state.package='campaign-'+state.revision+'.zip';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify({overview:'review/final-overview.png',alignment:measurements.map(({id,visibleUnitRatios,unitRightMargin})=>({id,visibleUnitRatios,unitRightMargin}))}));
