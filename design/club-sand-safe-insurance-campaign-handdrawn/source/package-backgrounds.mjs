import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const sharp = createRequire(path.resolve(path.dirname(process.execPath), '..', 'runtime.cjs'))('sharp');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const inputs = JSON.parse(await fs.readFile(path.join(root,'source/background-inputs.json'),'utf8'));
const map = JSON.parse(await fs.readFile(path.join(root,'02-layouts/generated-background-measurements.json'),'utf8'));
const content = JSON.parse(await fs.readFile(path.join(root,'content.json'),'utf8'));
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
for (const d of ['assets/backgrounds','03-backgrounds','04-png-export','review/phone-390','review/details']) await fs.mkdir(path.join(root,d),{recursive:true});
const metadata = [];
for (const [id,v] of Object.entries(inputs)) {
  if (!v.selected) throw new Error('Select reviewed background first: '+id);
  const selected = `03-backgrounds/${id}-${id==='cover'?'v1':'v2'}.png`;
  await fs.copyFile(v.selected,path.join(root,selected));
  await fs.copyFile(v.selected,path.join(root,`assets/backgrounds/${id}.png`));
  const m = await sharp(path.join(root,selected)).metadata();
  if (m.width!==map[id].native[0] || m.height!==map[id].native[1]) throw new Error(`Actual dimensions changed: ${id} ${m.width}x${m.height}; remeasure.`);
  metadata.push({id,provider:'built-in image_gen',nativeWidth:m.width,nativeHeight:m.height,hasAlpha:m.hasAlpha,original:v.original,selectedSource:v.selected,file:selected,prompt:`03-backgrounds/prompt-${id}.md`,editPrompt:id==='cover'?null:'03-backgrounds/reward-divider-edit-prompts.md',measurement:'02-layouts/generated-background-measurements.json'});
}
await fs.writeFile(path.join(root,'03-backgrounds/image-metadata.json'),JSON.stringify(metadata,null,2)+'\n');
const deliveries = [
  {id:'desktop-absolute',art:'poster',label:'端游绝密价目底图',width:3200,height:4800},
  {id:'mobile-absolute',art:'poster',label:'手游绝密价目底图',width:3200,height:4800},
  {id:'cover',art:'cover',label:'横版封面底图',width:3200,height:1800},
  {id:'social',art:'social',label:'双平台宣发底图',width:2400,height:3200}
];
for (const d of deliveries) {
  const m=metadata.find(m=>m.id===d.art);
  d.native=[m.nativeWidth,m.nativeHeight];
  d.file=`04-png-export/${d.id}-background.png`;
  await sharp(path.join(root,`assets/backgrounds/${d.art}.png`)).resize(d.width,d.height,{fit:'fill',kernel:'lanczos3'}).png().toFile(path.join(root,d.file));
  await sharp(path.join(root,d.file)).resize({width:390}).png().toFile(path.join(root,`review/phone-390/${d.id}-background.png`));
}
await fs.writeFile(path.join(root,'04-png-export/export-metadata.json'),JSON.stringify({stage:'background-confirmation',note:'目标尺寸为高分辨率导出；底图由原生绘制放大，未填正式文案。',files:deliveries},null,2)+'\n');
const proofFiles = {};
for (const id of ['poster','cover','social']) {
  const spec=map[id]; const [w,h]=spec.native;
  const b64=(await fs.readFile(path.join(root,`assets/backgrounds/${id}.png`))).toString('base64');
  const parts=[`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><image width="${w}" height="${h}" href="data:image/png;base64,${b64}"/>`];
  const box=(rect,label,dark=false)=> {
    const [x,y,bw,bh]=rect, sz=Math.min(21,bh*.50,bw/(label.length+1));
    parts.push(`<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="7" fill="${dark?'#ffffff':'#fffdf7'}" fill-opacity=".82" stroke="#ad5768" stroke-width="1.5" stroke-dasharray="6 4"/><text x="${x+bw/2}" y="${y+bh/2}" text-anchor="middle" dominant-baseline="central" font-family="Microsoft YaHei, sans-serif" font-size="${sz}" font-weight="400" fill="#713845">${esc(label)}</text>`);
  };
  spec.zones.forEach(z=>box(z.rect,z.label,z.dark));
  const table = id==='poster'?spec.table:id==='social'?spec.tables:null;
  if (table) {
    const groups=id==='poster'?[table.rowCenters]:[table.desktop.rowCenters,table.mobile.rowCenters];
    for (const centers of groups) {
      for (const cy of centers) {
        const sh=table.textSafeHeight;
        box([table.price.left,cy-sh/2,table.price.unitRight-table.price.left,sh],'价格');
        box([table.sand.left,cy-sh/2,table.sand.right-table.sand.left,sh],'次数');
        box([table.regular.left,cy-sh/2,table.regular.right-table.regular.left,sh],'次数');
        box([table.guarantee.left,cy-sh/2,table.guarantee.unitRight-table.guarantee.left,sh],'保底');
      }
      for(const [key,label] of [['price','r'],['guarantee','w']]) {
        const x=table[key].unitX;
        parts.push(`<line x1="${x}" y1="${centers[0]-table.textSafeHeight/2}" x2="${x}" y2="${centers.at(-1)+table.textSafeHeight/2}" stroke="#357b96" stroke-width="2" stroke-dasharray="4 4"/><text x="${x+5}" y="${centers[0]-table.textSafeHeight/2+14}" font-family="Microsoft YaHei, sans-serif" font-size="13" fill="#357b96">${label}</text>`);
      }
    }
  }
  parts.push(`<rect x="${w-212}" y="${h-28}" width="199" height="21" rx="6" fill="#fffdf7" fill-opacity=".93"/><text x="${w-112}" y="${h-16}" font-family="Microsoft YaHei, sans-serif" font-size="12" text-anchor="middle" fill="#713845">布局位置稿 · 非正式填字</text></svg>`);
  const svg=parts.join('');
  const file=`02-layouts/${id}-actual-layout`;
  await fs.writeFile(path.join(root,file+'.svg'),svg);
  await sharp(Buffer.from(svg)).png().toFile(path.join(root,file+'.png'));
  await sharp(path.join(root,file+'.png')).resize({width:390}).png().toFile(path.join(root,`review/phone-390/${id}-layout.png`));
  proofFiles[id]=file+'.png';
}
const overview=async(file,proof=false)=> {
  const W=1480,H=730, cardXs=[30,398,766,1134];
  const children=[{input:Buffer.from(`<svg width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg"><rect width="${W}" height="${H}" fill="#f6f0e7"/><text x="38" y="45" font-family="Microsoft YaHei, sans-serif" font-size="29" font-weight="700" fill="#173e51">沙色保险单 · ${proof?'文字位置确认稿':'无字底图确认稿'}</text><text x="38" y="77" font-family="Microsoft YaHei, sans-serif" font-size="18" fill="#596a6f">仅绝密｜三档价目｜手绘蜂医与灰鼠｜本阶段未填正式文案</text></svg>`),left:0,top:0}];
  for (let i=0;i<deliveries.length;i++) {
    const d=deliveries[i], x=cardXs[i];
    const input=path.join(root,proof?proofFiles[d.art]:d.file);
    const thumb=await sharp(input).resize({width:316}).png().toBuffer();
    const tm=await sharp(thumb).metadata();
    children.push({input:thumb,left:x+16,top:150});
    const txt=`<svg width="348" height="631" xmlns="http://www.w3.org/2000/svg"><text x="174" y="32" font-family="Microsoft YaHei, sans-serif" text-anchor="middle" font-size="21" font-weight="700" fill="#173e51">${esc(d.label)}</text><text x="174" y="${tm.height+95}" font-family="Microsoft YaHei, sans-serif" text-anchor="middle" font-size="17" fill="#596a6f">${d.width} × ${d.height} PNG</text></svg>`;
    children.push({input:Buffer.from(txt),left:x,top:99});
  }
  await sharp({create:{width:W,height:H,channels:4,background:'#f6f0e7'}}).composite(children).png().toFile(path.join(root,file));
};
await overview('review/background-overview.png');
await overview('review/layout-overview.png',true);
const crops=[['poster','reward',{left:42,top:884,width:942,height:250}],['poster','notes',{left:44,top:1149,width:938,height:275}],['social','reward',{left:46,top:1097,width:996,height:240}]];
for(const [id,section,rect] of crops) {
  await sharp(path.join(root,`assets/backgrounds/${id}.png`)).extract(rect).png().toFile(path.join(root,`review/details/${id}-${section}-background.png`));
  await sharp(path.join(root,proofFiles[id])).extract(rect).png().toFile(path.join(root,`review/details/${id}-${section}-layout.png`));
}
const panelCards=deliveries.map(d=>`<article><h2>${esc(d.label)}</h2><p>${d.width}×${d.height}导出；绘制原图 ${d.native.join('×')}。${d.art==='poster'?'两端共用美术，下一阶段分别填入各自价格。':''}</p><a href="../${d.file}">打开高清 PNG</a><img loading="lazy" src="../${d.file}" alt="${esc(d.label)}"/><details><summary>查看文字位置稿（不是正式填字）</summary><a href="../${proofFiles[d.art]}">打开布局位置稿</a><img loading="lazy" src="../${proofFiles[d.art]}" alt="文字安全区"/></details></article>`).join('');
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>沙色保险单｜底图确认</title><style>body{margin:0;background:#f5f0e7;color:#173e51;font-family:system-ui,'Microsoft YaHei',sans-serif}main{max-width:1140px;margin:auto;padding:28px}h1{font-size:29px}p{line-height:1.7}a{color:#9b6437}article{padding:24px;background:#fffdf8;border:1px solid #dfd7c8;border-radius:18px;margin:24px 0}img{display:block;max-width:100%;height:auto;margin:18px auto}article img{max-width:700px;width:100%}summary{cursor:pointer;padding:12px;background:#eee7db;border-radius:9px}details img{border:1px solid #dcb8c3}</style><main><h1>兴安电竞 · 沙色保险单</h1><p>第二阶段：底图与排版布局确认。只有绝密，端手游各三档。正式艺术标题、价目与规则将在确认后矢量填入；布局稿中的框与标签仅用于说明位置。</p><p>完整价目按四列展示价格、沙色次数、常规次数与基础保底；核心结单区表现三项同时达标。奖励区独立安排触发、两项权益及次数限制。普通规则使用短句，保留上下间距；重点选择性加粗。</p><a href="../output/campaign-sand-safe-insurance-backgrounds-v1.zip">下载本阶段底图包</a><img src="background-overview.png" alt="底图总览">${panelCards}<h2>已确认的业务口径</h2><p>保险只统计成功撤离局；激烈战况先询问板板能否代摸；成功撤离且板板带出物资严格＞600w才累计保底。两类次数与基础保底均达标才能结单。三幻神奖励整单一次，赠送沙色不计套餐次数。封面不写细则，宣发不加完整须知。</p></main></html>`;
await fs.writeFile(path.join(root,'review/backgrounds.html'),html);
const state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.stage='awaiting-background-approval';
state.revision='sand-safe-insurance-background-v1';
state.approvals.background={status:'pending',basis:'插画确认后生成完整价目、封面与宣发底图，已按实际边界建立文字安全区；本轮交付等待用户确认。',review:'review/backgrounds.html'};
state.backgrounds={metadata:'03-backgrounds/image-metadata.json',measurements:'02-layouts/generated-background-measurements.json',proofs:Object.values(proofFiles),exports:deliveries.map(d=>d.file),visualReview:'review/visual-qa.md'};
state.nextStep='用户确认底图后，按 content.json 与实际安全区矢量填入艺术标题、各端价格、权益、规则；真实字形居中、r/w独立对齐、重点选择性加粗，再检查实际成图。';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
const readme=`# 兴安电竞 · 沙色保险单\n\n阶段：底图与布局位置稿待确认。本阶段没有正式价目、文案与SVG矢量成品。\n\n- 查看页：review/backgrounds.html\n- 底图总览：review/background-overview.png\n- 文字位置总览：review/layout-overview.png\n- 两端完整价目底图：04-png-export/desktop-absolute-background.png、mobile-absolute-background.png（3200×4800）\n- 封面底图：04-png-export/cover-background.png（3200×1800）\n- 双平台宣发底图：04-png-export/social-background.png（2400×3200）\n- 净文字区及r/w轴：02-layouts/generated-background-measurements.json\n- 确认业务源：content.json、references/rules-confirmation.md\n- 图片原生尺寸与生成来源：03-backgrounds/image-metadata.json\n- 原始与修正版底图均保留。目标PNG为放大导出；正式文字将在确认后以矢量渲染。\n\n使用 built-in image_gen 生成与编辑；提示词见03-backgrounds/prompt-*.md、reward-divider-edit-prompts.md。两张完整价目共用无字美术，后续分别填端游168/388/628r与手游128/288/488r。\n`;
await fs.writeFile(path.join(root,'README.md'),readme);
process.stdout.write(JSON.stringify({native:metadata.map(m=>({id:m.id,width:m.nativeWidth,height:m.nativeHeight})),deliveries,proofs:proofFiles,stage:state.stage},null,2)+'\n');
