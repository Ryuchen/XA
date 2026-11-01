import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSharp} from './render.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),sharp=loadSharp();
const img=async(id,x,y,w,h)=>`<image href="data:image/png;base64,${(await fs.readFile(path.join(root,'review',id+'.png'))).toString('base64')}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
const tx=(x,y,t,size=28,fill='#203F53')=>`<text x="${x}" y="${y}" fill="${fill}" font-family="Microsoft YaHei UI" font-weight="600" font-size="${size}" dominant-baseline="central">${t}</text>`;
let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="2490"><rect width="2400" height="2490" fill="#F7F6EF"/>${tx(60,75,'兴安电竞 · 物资银行单 · 成品总览',47)}${tx(60,138,'四张五档价目 + 横版封面 + 简洁宣发海报 ｜ 艺术字与正文均保留矢量',27,'#6A8391')}`;
const posters=[['desktop-absolute','端游 · 绝密'],['desktop-secret','端游 · 机密'],['mobile-absolute','手游 · 绝密'],['mobile-secret','手游 · 机密']];
for(let i=0;i<posters.length;i++){const x=60+i*585;svg+=tx(x,210,posters[i][1],31)+await img(posters[i][0],x,250,525,1182);}
svg+=tx(60,1495,'横版封面 · 3200 × 1800',31)+tx(1490,1495,'简洁广告 · 2400 × 3200',31);
svg+=await img('cover',60,1545,1340,754)+await img('social',1490,1545,670,893.333);
svg+=tx(60,2370,'价目海报 3200 × 7200 ｜ PNG · 可编辑文字 SVG · 转曲 SVG',28)+tx(60,2426,'20 档数据已按本轮资料排入；插画为位图，文案在目标尺寸由矢量渲染。',25,'#6A8391')+'</svg>';
await sharp(Buffer.from(svg)).png().toFile(path.join(root,'review/final-overview.png'));
const notes=`# 银行物资单 · 最终排版记录\n\n## 内容\n\n- 共20档，四份完整版与广告共用content.json。\n- 绝密端游价格288/488/688/888/1688r；手游188/388/588/788/1488r。\n- 机密端游及手游价格沿用对应平台五档；物资、局数、超时追加依据本轮文字。\n- 加仓：绝密128r追加1388w；机密88r追加1088w。两端一致。\n- 超时保底整单只增加一次；加仓不延长或重置原定总局数。\n\n## 排版\n\n- 标题逐字转曲，奶白描边、错位阴影、轻微角度变化。\n- 价格数字右对齐至r前；基础物资与追加物资分别固定w列。\n- 主表数字可见字高约32.6–33.3，r/w可见字高约22.1–22.3，约为数字的67%–68%。\n- 每组正文、数字及单位按可见字形高度居中。\n- 主表没有装饰图标，四列表头统一深蓝。\n- 广告仅含核心价目、限局条件、超时整单一次及结单条件，无下单须知。\n\n## 交付\n\nPNG：四张3200×7200价目，3200×1800封面，2400×3200广告。\nSVG：可编辑文字版、全部文字转曲版。背景内嵌位图，整张海报不是全矢量。\n原生底图：价目836×1882，封面1672×941，广告1086×1448。正文与艺术字从矢量直接渲染到目标尺寸。\n\n## 后续修改\n\n更改content.json后运行source/workflow.mjs layout，再render；布局调整写入layout-adapter.mjs。复用已确认底图，无需再次生成插画。转曲版不依赖接收方字体，可改文字版需相同字体。\n`;
await fs.writeFile(path.join(root,'review/final-notes.md'),notes);
const statePath=path.join(root,'campaign-state.json');let state=JSON.parse((await fs.readFile(statePath,'utf8')).replace(/^\uFEFF/,''));state.vectorDelivery={status:'completed',overview:'review/final-overview.png',review:'review/index.html',notes:'review/final-notes.md',formats:['PNG','editable text SVG','outlined SVG'],rendering:'Final-size vector typography over approved raster backgrounds'};state.nextStep='成品已生成，可按需修改具体档位或文案；继续复用已确认底图';await fs.writeFile(statePath,JSON.stringify(state,null,2));
console.log('Created final overview and production notes.');
