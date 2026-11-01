import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const generated='C:/Users/chenh/.codex/generated_images/01a0f320-018a-7441-9bd5-883d38691610';
const variants=[
 {id:'dogtag',name:'狗牌版',source:'exec-36197dc8-7334-460c-b1a2-41edec7bed6c.png',panels:{version:[130,407,610,92],platform:[136,640,600,74],mode:[226,745,416,73]},safe:{brand:[154,122,560,76],title:[108,233,650,123],version:[160,422,550,60],tagline:[190,544,480,60],platform:[164,651,544,52],mode:[245,756,378,51]}},
 {id:'firearms',name:'枪械版',source:'exec-53e0d0a9-6c8f-46d7-b753-df3a248cdac1.png',panels:{version:[128,413,603,81],platform:[141,622,585,63],mode:[237,710,395,60]},safe:{brand:[154,111,552,70],title:[169,204,514,100],version:[160,426,539,55],tagline:[189,523,480,48],platform:[164,633,539,41],mode:[254,718,362,44]}},
 {id:'revolver',name:'左轮版',source:'exec-06d82f54-4edc-4a39-bbf5-6bdb25895f9a.png',panels:{version:[129,408,612,94],platform:[133,674,587,74],mode:[221,776,422,69]},safe:{brand:[155,126,560,74],title:[117,224,630,118],version:[163,424,544,62],tagline:[190,539,480,62],platform:[157,687,539,48],mode:[243,787,378,47]}}
];
await fs.mkdir(path.join(root,'assets/backgrounds'),{recursive:true});
const items=[],overview=[],phone=[];
for (const [index,v] of variants.entries()) {
 v.safe.brand=[v.safe.brand[0]+60,v.safe.brand[1],v.safe.brand[2]-120,60];
 v.safe.tagline=[235,v.safe.tagline[1],390,v.safe.tagline[3]];
 const file=`assets/backgrounds/${v.id}-cover-medic-mouse-r2.png`;
 await fs.copyFile(path.join(generated,v.source),path.join(root,file));
 const m=await sharp(path.join(root,file)).metadata();
 if(Math.abs(m.width/m.height-1.6)>0.015)throw Error(v.id+' aspect ratio mismatch');
 const preview=`review/cover-background-${v.id}-r2.png`;
 await sharp(path.join(root,file)).resize({width:1200}).png().toFile(path.join(root,preview));
 const small=await sharp(path.join(root,file)).resize({width:390}).png().toBuffer();
 await fs.writeFile(path.join(root,`review/cover-background-${v.id}-phone-r2.png`),small);
 const thumbnail=await sharp(path.join(root,file)).resize({width:520,height:325,fit:'contain',background:'#fbfaf7'}).png().toBuffer();
 overview.push({input:thumbnail,left:20+540*index,top:60});
 const label=Buffer.from(`<svg width="520" height="42"><text x="260" y="31" text-anchor="middle" font-family="Microsoft YaHei UI" font-size="25" font-weight="bold" fill="#20394a">${v.name}</text></svg>`);
 overview.push({input:label,left:20+540*index,top:10});
 phone.push({input:small,left:12+402*index,top:10});
 const labels={brand:'品牌',title:'主标题',version:'版本',tagline:'卖点',platform:'平台',mode:'模式'};
 const rectangles=Object.entries(v.safe).map(([key,[x,y,w,h]])=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="#0d7185" fill-opacity="0.08" stroke="#0d7185" stroke-width="2" stroke-dasharray="8 4"/><text x="${x+w/2}" y="${y+h/2+8}" fill="#0d7185" text-anchor="middle" font-family="Microsoft YaHei UI" font-size="22">${labels[key]}</text>`).join('');
 const overlay=Buffer.from(`<svg width="${m.width}" height="${m.height}" viewBox="0 0 1600 1000">${rectangles}</svg>`);
 await sharp(path.join(root,file)).composite([{input:overlay}]).png().toFile(path.join(root,`review/cover-background-${v.id}-zones-r2.png`));
 const nativeRect=r=>r.map((n,i)=>Math.round(n*(i%2===0?m.width/1600:m.height/1000)));
 items.push({id:v.id+'-cover',name:v.name,file,preview,nativeWidth:m.width,nativeHeight:m.height,source:path.join(generated,v.source),tool:'built-in image_gen',viewBox:[1600,1000],panelsInViewBox:v.panels,safeTextZonesInViewBox:v.safe,safeTextZonesNative:Object.fromEntries(Object.entries(v.safe).map(([k,r])=>[k,nativeRect(r)])),zoneBasis:'依据实际生成PNG重新量取；用于下一步文字排版，非最终字体边界。',status:'awaiting-user-approval',review:{characters:'蜂医与灰鼠身份一致，各自两只手，头耳脚及尾巴完整。',style:'角色、仓库、塔楼、直升机和箱体统一手绘；奶白雾蓝、海军蓝、薄荷与暖金。',copy:'主阅读区无生成的正文或数字；版本、平台、模式底板为空；文字仅在审核叠层标注。',composition:'左文右图，约8:5；各版本按实图分别定位。'},repair:v.id==='firearms'?'首次生成2.5:1长横幅，已通过ImageGen重排为8:5；初稿保留但不入选。':undefined});
}
await fs.copyFile(path.join(generated,'exec-75fd42c1-9aeb-4f28-bd3f-f811649827cd.png'),path.join(root,'assets/backgrounds/firearms-cover-medic-mouse-r2-wide-draft.png'));
await sharp({create:{width:1640,height:405,channels:4,background:'#fbfaf7'}}).composite(overview).png().toFile(path.join(root,'review/cover-background-overview-r2.png'));
await sharp({create:{width:1218,height:265,channels:4,background:'#fbfaf7'}}).composite(phone).png().toFile(path.join(root,'review/cover-background-phone-r2.png'));
await fs.writeFile(path.join(root,'review/cover-background-stage-r2.json'),JSON.stringify({stage:'awaiting-cover-background-approval',date:'2026-10-01',items},null,2));
const cards=items.map(p=>`<section><h2>${p.name}</h2><a href="../${p.file}"><img src="cover-background-${p.id.replace('-cover','')}-r2.png" alt="${p.name}无字封面底图"></a><p>原生尺寸 ${p.nativeWidth}×${p.nativeHeight} · <a href="../${p.file}">原图</a> · <a href="cover-background-${p.id.replace('-cover','')}-zones-r2.png">文字区审核图</a> · <a href="cover-background-${p.id.replace('-cover','')}-phone-r2.png">手机预览</a></p></section>`).join('');
await fs.writeFile(path.join(root,'review/stage2-covers-r2.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>你的护不行 · 三版封面底图</title><style>body{margin:0;background:#edf2f2;color:#20394a;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1500px;margin:auto;padding:28px}.grid{display:grid;grid-template-columns:1fr;gap:24px}section{background:#fbfaf7;padding:22px;border-radius:20px}img{width:100%;display:block}h1{font-size:30px}h2{font-size:23px}a{color:#437d76}p{max-width:1000px}</style><main><h1>你的护不行 · 蜂医与灰鼠封面</h1><p>第二阶段：三张新封面无字底图。三张角色插画已获用户确认；当前等待新底图确认后排入矢量标题、版本、卖点、端手游通用与机密/绝密信息。三张广告仍在后续范围内。</p><div class="grid">${cards}</div><p>内置 ImageGen 生成。<a href="../source/prompts/cover-backgrounds-r2.json">完整提示词</a> · <a href="../source/prompts/firearms-cover-ratio-repair-r2.txt">枪械比例修正提示词</a> · <a href="cover-background-qa-r2.md">复检记录</a>。这里展示的是新底图，旧版成品与旧包装仅保留历史。</p></main></html>`);
const state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.stage='awaiting-cover-background-approval';
state.approvals.background={status:'pending',scope:'three-covers',date:'2026-10-01',basis:'三张新封面无字底图已生成并复查；依技能新底图确认后进入矢量填字。',items:items.map(i=>i.file)};
state.pendingBackgrounds=items;
state.visualQA={status:'cover-backgrounds-reviewed-final-typography-pending',record:'review/cover-background-qa-r2.md',viewer:'review/stage2-covers-r2.html'};
state.memorySync={status:'pending',date:'2026-10-01'};
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log(JSON.stringify(items.map(({id,nativeWidth,nativeHeight,file})=>({id,nativeWidth,nativeHeight,file})),null,2));
