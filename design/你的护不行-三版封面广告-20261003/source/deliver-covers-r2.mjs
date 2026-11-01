import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const read=async n=>JSON.parse(await fs.readFile(path.join(root,n),'utf8'));
const manifest=await read('output/manifest.json'),state=await read('campaign-state.json'),audit=await read('review/covers-layout-audit-r2.json');
if(manifest.length!==3||audit.summaries.some(s=>!s.pass))throw Error('Final covers have not passed layout checks');
const cards=manifest.map(p=>`<section><h2>${p.label}</h2><a href="../${p.png}"><img src="${p.id}.png" alt="${p.label}"></a><p><a href="../${p.png}" download>高清 PNG · 3200×2000</a> · <a href="../${p.outlinedSVG}" download>转曲 SVG</a> · <a href="../${p.editableSVG}" download>可改文字 SVG</a></p></section>`).join('');
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>你的护不行 · 三版封面成品</title><style>body{margin:0;background:#edf2f2;color:#20394a;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1450px;margin:auto;padding:28px}section{background:#fbfaf7;padding:22px;border-radius:20px;margin:24px 0}img{width:100%;display:block}h1{font-size:30px}h2{font-size:23px}a{color:#437d76}</style><main><h1>你的护不行 · 蜂医与灰鼠</h1><p>狗牌版、枪械版、左轮版三张最终封面 · 端游 / 手游同价 · 绝密 / 机密</p><p><a href="../你的护不行-蜂医灰鼠-三张封面-PNG-r2.zip">下载三张高清 PNG</a> · <a href="../你的护不行-蜂医灰鼠-三张封面-源稿-r2.zip">下载封面及源稿包</a></p>${cards}<p>正文为矢量文字；艺术标题为可调整的矢量轮廓，完整文字和样式参数保存在 layout.json。插画与场景为内嵌位图，原生 1586×992，最终 PNG 为 3200×2000。跨设备保持版式请用转曲 SVG。<a href="visual-qa.md">成图复检记录</a>。</p><p>本轮完成三张封面；三张广告仍在整体任务的后续制作范围。</p></main></html>`;
await fs.writeFile(path.join(root,'review/covers-r2.html'),html);
await fs.writeFile(path.join(root,'review/index.html'),html);
const maxX=Math.max(...audit.summaries.flatMap(s=>s.audit.map(a=>Math.abs(a.horizontalOffset)))),maxY=Math.max(...audit.summaries.flatMap(s=>s.audit.map(a=>Math.abs(a.verticalOffset))));
await fs.writeFile(path.join(root,'review/visual-qa.md'),`# 蜂医灰鼠三版封面 r2 · 成图复检

日期：2026-10-01。用户在底图展示后回复“填入文字”，已授权填字；插画与底图确认均延续有效。

## 成品与范围

- dogtag-cover、firearms-cover、revolver-cover，共三张 3200×2000 PNG，以及正文可改文字 SVG、转曲 SVG。广告仍待后续制作，未当作本轮完成。
- 原底图 1586×992。文字从矢量按最终尺寸渲染，不将位图放大称为原生高清绘制。
- content.json 与 r1 归档哈希一致，价格、保底、计分/枪数等业务规则未改。封面只展示品牌、玩法名、版本、核心卖点、平台同价与绝密/机密。

## 完整图与手机预览

- 逐张查看本次成图：review/dogtag-cover.png、firearms-cover.png、revolver-cover.png。
- 从最终 PNG 派生 390px 预览：review/*-cover-phone-r2.png，合览 covers-phone-r2.png。原尺寸文字局部：review/*-cover-text-detail-r2.png。
- 三张均以玩法艺术字为主标题，版本条加粗，品牌为次级强调，卖点/平台/模式为常规字重。卖点按原有分隔号等义分成两行，整组居中，无新增承诺。
- 主体完整，文字不遮挡蜂医、灰鼠、手部和主题道具；无须知、结算例外或未经提供的优惠。手绘环境与角色风格一致。
- 手机图中玩法与版本优先辨识，短卖点及平台/模式可读，无不自然拆字或叠行；最终文字局部线条清晰、留白正常。

## 区块边界与修正

- 按底图原生底板分别定位六个文字区。卖点采用版本条底部至平台条顶部之间的连续净空，左右扣除景物，给两行语义组留出空间；没有移动或覆盖原人物。
- 初次复检枪械版标题及版本/平台文字略超预先定义的净安全区，已分别调整字号，保持既定安全边界不扩大；仅重渲染枪械版后复查。
- 全部文字可见轮廓（艺术标题含描边/阴影近似边界）均位于所属安全区；全部文字区横向中心最大偏差 ${maxX.toFixed(3)}，整组纵向中心最大偏差 ${maxY.toFixed(3)} 布局单位。
- 普通文字无可见相交；两行卖点上下分离，版本、平台、模式在各自实际底板内居中。三版因底板位置不同而单独定位，未生搬旧坐标。
- 详细数字和最终 PNG SHA-256：review/covers-layout-audit-r2.json。原始文字边界：review/*-text-bounds.json；相交记录：review/*-text-overlaps.json。

## 结论

三张封面已完成并通过本轮成图复检。旧 r1 六张成品在 archive/r1/output 与原 r1 包中保留；本轮打包只含这三张蜂医灰鼠封面及对应源文件。
`);
state.stage='covers-delivered-ads-pending';state.revision='r2-covers';
state.completedPageIds=manifest.map(p=>p.id);
state.approvedBackgrounds=(state.pendingBackgrounds||state.approvedBackgrounds).map(i=>({...i,status:'approved',approvalBasis:'用户回复“填入文字”。'}));delete state.pendingBackgrounds;
if(state.pendingArtwork){state.artwork=Object.fromEntries(state.pendingArtwork.map(p=>[p.id,{...p,status:'approved'}]));delete state.pendingArtwork;}
state.visualQA={status:'passed-three-covers',date:'2026-10-01',record:'review/visual-qa.md',measurements:'review/covers-layout-audit-r2.json',viewer:'review/covers-r2.html'};
state.delivery={revision:'r2-covers',pageIds:state.completedPageIds,viewer:'review/covers-r2.html',pngPackage:'你的护不行-蜂医灰鼠-三张封面-PNG-r2.zip',sourcePackage:'你的护不行-蜂医灰鼠-三张封面-源稿-r2.zip',dimensions:[3200,2000]};
state.memorySync={status:'pending',date:'2026-10-01'};
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
await fs.writeFile(path.join(root,'output/DELIVERY-r2.md'),`# 你的护不行 · 蜂医灰鼠三张封面

狗牌版、枪械版、左轮版，PNG 均为 3200×2000。查看 review/covers-r2.html。

- output/png：最终图片。
- output/svg-outlined：全部文字为轮廓，跨设备稳定。
- output/svg-editable：正文文字对象可改；艺术标题仍为轮廓，标题文字/字号/字距等从 layout.json 或 layout-adapter.mjs 修改再渲染。正文使用 Microsoft YaHei UI；标题转曲使用 Noto Sans SC Black。
- 背景为嵌入位图，原生1586×992，并非整张全矢量。
- content.json 保留全部已确认业务数据，旧 artwork 字段为历史来源；本轮美术以 campaign-state.json 和 layout-adapter.mjs 引用的已确认底图为准。
- 本轮仅三张封面，广告在后续范围，不用旧广告冒充新成品。

重新渲染：使用 source/workflow.mjs 的 layout、render 命令，传本项目绝对路径与环境的 Sharp 模块目录。source/prepare-* 与 source/review-cover-backgrounds-r2.mjs 是阶段留档脚本，不作为最终渲染入口重复运行。
`);
console.log('Final cover viewer, QA and delivery state saved.');
