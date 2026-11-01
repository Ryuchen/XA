import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),sharp=loadSharp();
const content=JSON.parse(await fs.readFile(path.join(root,'content.json'),'utf8'));
const manifest=JSON.parse(await fs.readFile(path.join(root,'output/manifest.json'),'utf8'));
const img=async(id,x,y,w,h)=>`<image href="data:image/png;base64,${(await fs.readFile(path.join(root,'review',id+'.png'))).toString('base64')}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const tx=(x,y,t,size=28,fill='#203F53')=>`<text x="${x}" y="${y}" fill="${fill}" font-family="Microsoft YaHei UI" font-weight="600" font-size="${size}" dominant-baseline="central">${t}</text>`;
let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="2490"><rect width="2400" height="2490" fill="#F7F6EF"/>${tx(60,75,'兴安电竞 · 超额累计单 · 成品总览',47)}${tx(60,138,'四张价目 + 横版封面 + 简洁宣发海报 ｜ 艺术字、正文与数字保留矢量',27,'#6A8391')}`;
for(let i=0;i<content.tiers.length;i++){const t=content.tiers[i],x=60+i*585;svg+=tx(x,210,`${t.platform} · ${t.mode} · ${t.rows.length}档`,31)+await img(t.id,x,250,525,1182);}
svg+=tx(60,1495,'横版封面 · 3200 × 1800',31)+tx(1490,1495,'简洁宣发 · 2400 × 3200',31);
svg+=await img('cover',60,1545,1340,754)+await img('social',1490,1545,670,893.333);
svg+=tx(60,2370,'价目海报 3200 × 7200 ｜ PNG · 可改文字 SVG · 转曲 SVG',28)+tx(60,2426,'18 档新价目；成功撤离只计超额。文字按目标尺寸由矢量渲染，插画为位图。',25,'#6A8391')+'</svg>';
await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review/final-overview.png'));
const measurements=[];
for(const t of content.tiers){const b=JSON.parse(await fs.readFile(path.join(root,'review',t.id+'-text-bounds.json'),'utf8'));const first=role=>b.find(e=>e.role===role);const pn=first('price-number'),pu=first('price-unit'),gn=first('guarantee-number'),gu=first('guarantee-unit');measurements.push({id:t.id,rows:t.rows.length,priceUnitColumn:pu.x,guaranteeUnitColumn:gu.x,visibleUnitRatios:{r:pu.height/pn.height,w:gu.height/gn.height},verticalCenter:'数字与单位的可见字形分别居中于同一行中心',pricePairs:t.rows.map(r=>[r.priceR,r.excessGuaranteeW])});}
await fs.writeFile(path.join(root,'review/alignment-record.json'),JSON.stringify(measurements,null,2));
await fs.writeFile(path.join(root,'review/final-notes.md'),`# 超额累计单 · 最终排版记录\n\n## 内容\n\n- 本轮共18档：端游每模式五档，手游每模式四档；完整海报和广告共用 content.json。\n- 已统一更名“超额累计单”。端游保底128/388/688/1288/1888w；手游保底188/488/888/1288w。\n- 成功撤离为前提；绝密只累计超过1000w的部分，机密只累计超过600w的部分。\n- 未超过门槛不计订单进度，带出物资全部归板板；打满订单保底及适用炸单补偿后结单。\n- 计算示例明确标为举例；两模式的须知和补偿分别按已确认资料排入，端手游一致。\n\n## 排版\n\n- 关键标题采用逐字转曲艺术字、奶白描边和柔和错位阴影。\n- 主价目 r 固定列287，w固定列686（原生布局坐标）；数字向单位方向右对齐，组内8px间距。\n- r可见字高为数字的67.7%；w可见字高为数字的66.8%。\n- 行内数字与单位均按可见字形垂直居中；多行规则按整组居中，减少不必要换行。\n- 表头整条深蓝，数字区没有右侧装饰图标挤占空间。\n- 广告为3:4，保留四类价目、成功撤离与门槛条件、物资归属和结单条件，不含下单须知。\n\n## 交付\n\n${manifest.map(p=>`- ${p.label}：${p.width}×${p.height}；底图原生 ${p.nativeWidth}×${p.nativeHeight}`).join('\n')}\n\nPNG从矢量层直接在目标尺寸渲染。可改文字SVG保留正文/数字文字对象、标题保留路径；转曲SVG全部文字为路径，不依赖接收方字体。底图为内嵌位图，整张海报不是全矢量。\n\n## 后续修改\n\n仅修改 content.json 相关数据后执行 source/workflow.mjs layout，再render与package；版面修改写入layout-adapter.mjs。复用已确认底图，不重新生成角色。\n`);
const statePath=path.join(root,'campaign-state.json');let state=JSON.parse((await fs.readFile(statePath,'utf8')).replace(/^\uFEFF/,''));
state.vectorDelivery={status:'completed',overview:'review/final-overview.png',review:'review/index.html',notes:'review/final-notes.md',formats:['PNG','editable text SVG','outlined SVG'],rendering:'Final-size vector typography over approved raster backgrounds',tierCount:18};
state.nextStep='成品已完成；后续按需修改具体档位或文案，继续复用已确认底图';state.package='campaign-excess-final-r1.zip';
await fs.writeFile(statePath,JSON.stringify(state,null,2));
console.log('Created final overview, measured alignment record and production notes.');
