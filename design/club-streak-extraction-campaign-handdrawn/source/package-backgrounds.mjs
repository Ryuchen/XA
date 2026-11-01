import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const metrics=JSON.parse(await fs.readFile(path.join(root,'02-layouts/generated-background-measurements.json'),'utf8'));
const esc=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const txt=(x,y,s,size=24,color='#203F53',anchor='middle')=>'<text x="'+x+'" y="'+y+'" font-family="Microsoft YaHei UI" font-size="'+size+'" font-weight="700" text-anchor="'+anchor+'" dominant-baseline="central" fill="'+color+'">'+esc(s)+'</text>';
const rect=(b,color='#DA7048')=>'<rect x="'+b[0]+'" y="'+b[1]+'" width="'+b[2]+'" height="'+b[3]+'" rx="5" fill="#FFFFFF" fill-opacity=".66" stroke="'+color+'" stroke-width="1.5" stroke-dasharray="4 3"/>';
const center=b=>[b[0]+b[2]/2,b[1]+b[3]/2];
const nativeFiles=[
 ['poster-gold','exec-33d472c5-cea8-461a-b6b3-79406862d44b.png','prompt-poster-gold.md','绝密暖金完整价目'],
 ['poster-teal','exec-f5a1a14b-4191-475e-a0aa-03f01aa7736d.png','prompt-poster-teal.md','机密薄荷完整价目'],
 ['cover','exec-a090336d-155d-476a-8561-ae7c473665a4.png','prompt-cover.md','横版封面'],
 ['social','exec-75a7b527-3d50-462b-bcd1-a67dd02dca01.png','prompt-social.md','端手游共用双模式宣发结构']
];
const assets=[];
await fs.mkdir(path.join(root,'04-png-export'),{recursive:true});
await fs.mkdir(path.join(root,'assets/backgrounds'),{recursive:true});
for(const [id,original,prompt,label] of nativeFiles){
 const file=path.join(root,'03-backgrounds',id+'.png'),m=await sharp(file).metadata();
 assets.push({id,label,provider:'built-in image_gen',nativeWidth:m.width,nativeHeight:m.height,selectedFile:'03-backgrounds/'+id+'.png',original:'C:/Users/chenh/.codex/generated_images/01a0e71e-3e4a-7e03-838f-9134499c5526/'+original,prompt:'03-backgrounds/'+prompt});
 await fs.copyFile(file,path.join(root,'assets/backgrounds',id+'.png'));
}
await fs.writeFile(path.join(root,'03-backgrounds/image-metadata.json'),JSON.stringify({stage:'background-review',assets,processing:'保留原始生成图；交付PNG仅按目标画布尺寸放大。后续正文与艺术字以矢量添加。',goldRevision:'首版保留为poster-gold-v1-draft.png；选用加高第一/第三须知卡、缩减页尾的修订版。'},null,2));
const exports=[
 ['desktop-absolute','poster-gold',3200,9600,'端游 · 绝密价目底图'],
 ['desktop-secret','poster-teal',3200,9600,'端游 · 机密价目底图'],
 ['mobile-absolute','poster-gold',3200,9600,'手游 · 绝密价目底图'],
 ['mobile-secret','poster-teal',3200,9600,'手游 · 机密价目底图'],
 ['cover','cover',3200,1800,'横版封面底图'],
 ['desktop-social','social',2400,3200,'端游 · 18档宣发底图'],
 ['mobile-social','social',2400,3200,'手游 · 18档宣发底图']
];
const manifest=[];
for(const [id,asset,w,h,label] of exports){
 const dest=path.join(root,'04-png-export',id+'-background.png');
 await sharp(path.join(root,'03-backgrounds',asset+'.png')).resize(w,h,{fit:'fill',kernel:'lanczos3'}).png({compressionLevel:9}).toFile(dest);
 manifest.push({id,label,file:'04-png-export/'+id+'-background.png',asset,width:w,height:h,content:'无正式文字，等待底图确认'});
 console.log('导出：'+id+' '+w+'×'+h);
}
await fs.writeFile(path.join(root,'04-png-export/manifest.json'),JSON.stringify(manifest,null,2));

async function annotate(id,overlay,w,h){
 const bytes=await fs.readFile(path.join(root,'03-backgrounds',id+'.png'));
 const svg='<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'"><image href="data:image/png;base64,'+bytes.toString('base64')+'" width="'+w+'" height="'+h+'"/>'+overlay+'</svg>';
 await fs.writeFile(path.join(root,'02-layouts',id+'-actual-layout.svg'),svg);
 await sharp(Buffer.from(svg)).png().toFile(path.join(root,'02-layouts',id+'-actual-layout.png'));
}
let p='';
p+=rect(metrics.poster.titleSafe)+txt(178,87,'品牌 / 艺术标题',17)+txt(178,127,'所属文字区居中',15);
p+=txt(178,235,'平台 · 模式',18,'#FFFFFF');
p+=rect(metrics.poster.completionSafe)+txt(420,303,'结单条件：两项同时满足',19)+txt(420,328,'连撤达标 ＋ 打满基础保底',17);
for(const [i,g] of metrics.poster.maps.entries()){
 p+=txt(360,g.header[1]+14,'地图 '+(i+1),20,'#FFFFFF');
 for(let j=0;j<3;j++){const a=[42,235,488,681];p+=txt((a[j]+a[j+1])/2,g.tableHeader[1]+12,['价格','基础保底','连撤要求'][j],16);}
 for(let j=0;j<3;j++){const y=(g.rowEdges[j]+g.rowEdges[j+1])/2;p+=txt(135,y,'数字',24)+txt(205,y,'r',22)+txt(365,y,'数字',24)+txt(443,y,'w',22)+txt(580,y,(j+2)+'把',24);}
 for(const x of [205,443])p+='<path d="M'+x+' '+g.rowEdges[0]+'V'+g.rowEdges[3]+'" stroke="#DA7048" stroke-width="1.2" stroke-dasharray="3 4"/>';
}
p+=txt(360,917,'有效连撤 · 两项均满足',20,'#FFFFFF');
p+=rect(metrics.poster.validityBodySafe)+txt(423,994,'击杀条件 ＋ 物资门槛',22)+txt(423,1030,'低门槛局不计数、不打断',19)+txt(423,1060,'当局物资归板板',19);
p+=rect(metrics.poster.failureSafe)+txt(421,1132,'失败：重计连撤次数',20)+txt(421,1160,'已撤离物资继续累计',18);
p+=txt(360,1212,'下单须知',20,'#FFFFFF');
for(const n of metrics.poster.notes){p+=rect(n.labelSafe)+rect(n.bodySafe,'#1C8B86');const lc=center(n.labelSafe),bc=center(n.bodySafe);p+=txt(...lc,n.id,18)+txt(...bc,'粗体正文 · 整组垂直居中',19);}
p+=txt(360,2125,'页尾品牌 / 确认提示',15);
await annotate('poster-gold',p,724,2171);
await annotate('poster-teal',p,724,2172);
let c=rect(metrics.cover.titleSafe)+rect(metrics.cover.brandSafe);
c+=txt(417,113,'品牌组居中',31)+txt(417,300,'连续',95)+txt(417,490,'撤离单',94);
c+=rect(metrics.cover.subtitleSafe)+txt(417,737,'稳定连撤 · 物资保底',33);
c+=rect(metrics.cover.versionSafe)+txt(417,832,'端游 / 手游 · 绝密 / 机密',29);
c+='<path d="M417 75V865" stroke="#DA7048" stroke-width="2" stroke-dasharray="8 9"/>';
await annotate('cover',c,1672,941);
let s=rect(metrics.social.titleSafe)+txt(296,95,'品牌 / 艺术标题',25)+txt(296,185,'连续撤离单',43);
s+=rect(metrics.social.platformSafe)+txt(543,346,'平台专属 · 18档价目',25);
for(const [ci,card] of metrics.social.cards.entries()){
 const cx=ci?795:292;s+=txt(cx,418,ci?'机密版':'绝密版',27);
 ['价格','基础保底','连撤'].forEach((label,j)=>{const a=card.colEdges;s+=txt((a[j]+a[j+1])/2,464,label,22,'#FFFFFF');});
 for(const [gi,g] of metrics.social.groups.entries()){
  s+=txt(cx,(g.mapHeaderY[0]+g.mapHeaderY[1])/2,'地图 '+(gi+1),23);
  for(let ri=0;ri<3;ri++){const cy=(g.rowEdges[ri]+g.rowEdges[ri+1])/2,a=card.colEdges;s+=txt((a[0]+a[1])/2,cy,'价r',29)+txt((a[1]+a[2])/2,cy,'保底w',29)+txt((a[2]+a[3])/2,cy,(ri+2)+'把',29);}
 }
}
s+=rect(metrics.social.completionSafe)+txt(624,1219,'连撤次数达标 ＋ 打满基础保底',27)+txt(624,1254,'两项同时满足，才可结单',26);
await annotate('social',s,1086,1448);
async function board(file,annotated){
 const W=2000,H=1800,composites=[];
 const box=async(id,left,top,width,height)=>{const f=path.join(root,annotated?'02-layouts':'03-backgrounds',id+(annotated?'-actual-layout':'')+'.png');composites.push({input:await sharp(f).resize(width,height,{fit:'fill'}).png().toBuffer(),left,top});};
 await box('poster-gold',50,150,520,1559);await box('poster-teal',610,150,520,1560);
 await box('cover',1180,150,770,433);await box('social',1220,665,690,920);
 const labels=txt(45,42,annotated?'连续撤离单｜文字区域与对齐稿':'连续撤离单｜无字底图确认',42,'#203F53','start')+txt(45,89,'四张完整价目 + 横版封面 + 端游、手游各一张18档宣发',26,'#536B7C','start')+
 txt(310,128,'绝密底图 · 端手游共用版式',26)+txt(870,128,'机密底图 · 端手游共用版式',26)+txt(1565,128,'横版封面',26)+txt(1565,629,'宣发底图 · 两端分别填价',26)+
 txt(1565,1632,annotated?'橙框为文字区，绿色框为正文区':'正式文案将在底图确认后以矢量填入',25)+txt(1565,1672,'完整价目每张9档；宣发每张18档',23);
 const base=await sharp({create:{width:W,height:H,channels:3,background:'#F6F8F9'}}).composite(composites).png().toBuffer();
 await sharp(base).composite([{input:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="'+W+'" height="'+H+'">'+labels+'</svg>')}]).png().toFile(path.join(root,'review',file));
}
await board('background-overview.png',false);
await board('layout-overview.png',true);
const cards=manifest.map(m=>'<article><h2>'+m.label+'</h2><p>'+m.width+' × '+m.height+' · <a href="../'+m.file+'">打开PNG原图</a></p><a href="../'+m.file+'"><img loading="lazy" src="../'+m.file+'" alt="'+m.label+'"></a></article>').join('');
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>连续撤离单 · 底图确认</title><style>body{margin:0;background:#F5F8FA;color:#203F53;font:16px/1.6 "Microsoft YaHei UI",sans-serif}main{max-width:1500px;margin:auto;padding:28px}h1{margin:0}p{color:#536B7C}nav{display:flex;gap:20px;flex-wrap:wrap}a{color:#1A7785}.overview{width:100%;height:auto;border-radius:12px;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}article{padding:20px;background:white;border:1px solid #D8E4EB;border-radius:14px}article img{display:block;width:100%;height:auto}h2{font-size:21px}details{margin:24px 0}@media(max-width:750px){main{padding:14px}.grid{grid-template-columns:1fr}}</style><main><h1>兴安电竞 · 连续撤离单</h1><p>当前阶段：排版区域与无字底图确认。四张完整价目分别填入本轮9档；两张宣发各18档。正式标题、价格和规则尚未填入。</p><nav><a href="../output/campaign-streak-backgrounds-v1.zip">下载本阶段源文件与七张PNG</a><a href="background-overview.png">底图总览</a><a href="layout-overview.png">文字位置稿</a><a href="../02-layouts/generated-background-measurements.json">实际区域坐标</a></nav><img class="overview" src="background-overview.png" alt="四类底图总览"><details><summary>查看文字区域、固定r/w单位列与居中方式</summary><p>这是一份位置说明稿。封面各文字组沿同一条左侧中轴居中；正文在图标右侧的实际空白区内整组垂直居中。价目表头整条深蓝。</p><img class="overview" src="layout-overview.png" alt="文字位置稿"></details><div class="grid">'+cards+'</div><p>生成原图尺寸单独记录于03-backgrounds/image-metadata.json。高清PNG按目标尺寸放大，未来文案另以矢量添加；字形居中、单位字号与手机阅读效果须在填字后逐张检查。</p></main></html>';
await fs.writeFile(path.join(root,'review/backgrounds.html'),html);
await fs.writeFile(path.join(root,'review/index.html'),html);
const state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.stage='background-review';state.revision='streak-backgrounds-v1';state.approvals.background={status:'pending-user-confirmation',basis:'七张无字PNG与生成后文字位置稿已制作；确认后填入矢量文案',review:'review/backgrounds.html'};
state.backgrounds={metadata:'03-backgrounds/image-metadata.json',nativeMeasurement:'02-layouts/generated-background-measurements.json',manifest:'04-png-export/manifest.json',overview:'review/background-overview.png',layoutOverview:'review/layout-overview.png',package:'output/campaign-streak-backgrounds-v1.zip'};
state.nextStep='用户确认底图后，按实际安全区填入矢量艺术标题、36档价格、r/w单位列及完整粗体规则；复测所有字形居中和手机阅读效果。';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
const readme='# 兴安电竞 · 连续撤离单\n\n## 当前阶段\n\n插画已由用户“继续”确认。当前交付排版布局与无字底图，等待用户确认后进行正式矢量文字填充。\n\n## 本阶段文件\n\n- 04-png-export：七张PNG。完整价目4张，3200×9600；横版封面3200×1800；端游/手游宣发各2400×3200。\n- review/backgrounds.html：可点开原图的确认页面。\n- review/background-overview.png：底图总览。\n- review/layout-overview.png：实际文字位置说明稿。\n- 02-layouts：生成前结构稿，以及生成后重新测量的安全区与说明稿。坐标以实际生成图为准。\n- 03-backgrounds：四种已选无字原图、提示词与生成来源；gold-v1-draft为保留的未选草稿。\n- 01-illustrations：已确认的男性蜂医与灰鼠主题插画，透明PNG。\n- content.json：本轮36档和已确认业务规则的唯一文字数据源。\n- campaign-state.json：分阶段状态，底图确认尚待用户回复。\n\n端游和手游共用每个模式的无字版式，但后续填入各自新价格。每张完整价目有三地图×三档；宣发按平台分两张，每张绝密/机密共18档，不放完整下单须知。\n\n## 制作约束\n\n1. 二次元手绘风格；绝密暖金、机密薄荷青，价格表头整条深蓝。\n2. 连撤对应次数与打满基础保底必须同时满足。\n3. 数字向独立r/w单位列右对齐；单位可见高度约数字65–75%；每个文字组按可见字形垂直居中。\n4. 房卡、缺项补偿采用加高文字卡；正文真实粗体，按实际空白区排版，未来需逐张检查字号与手机阅读效果。\n5. 封面文字在左侧所属文字区沿同一中轴居中；人物和路线位于右侧。\n6. 保留原始生成图。导出PNG使用Lanczos3放大，生成原图分辨率请看image-metadata.json，未宣称原生3200px绘制。\n7. 本阶段还未填正式文字；不能把无字底图当作最终价目海报发布。\n';
await fs.writeFile(path.join(root,'README.md'),readme);
await fs.writeFile(path.join(root,'02-layouts/brief.md'),'# 连续撤离单布局\n\n七张交付：四张完整价目、横版封面、端游/手游各一张简洁宣发。\n\n生成前设计稿为poster/cover/social-layout。生成后以*-actual-layout及generated-background-measurements.json为准。\n\n完整价目每张三地图×三档，结单两项突出；有效连撤/重计规则独立；八组须知在图标右侧，第一与第三卡加高。正文真实粗体，不以压小字号填满。封面文字沿左侧文字区同一中轴居中。宣发每张18档，不附完整须知。\n');
let qa='';try{qa=await fs.readFile(path.join(root,'review/visual-qa.md'),'utf8');}catch{}
qa+='\n\n## 底图阶段（2026-09-29）\n\n- 已目检四种生成底图与房卡/缺项补偿局部，人物面部、灰鼠、撤离路线及背景均延续已确认手绘风格。\n- 三地图表各三档，八组须知、有效连撤和失败重计区均保留；房卡及缺项卡已加高。价目表头为整条深蓝。\n- 按实际原图重新标注文字区，图标排除在正文区外；r/w采用独立固定位置。标题和规则的真实字形居中待填字后复测。\n- 封面左侧各文字组同中轴；宣发双模式九档×2，各平台分别填入18档。无完整须知。\n- 宣发人物局部与平台条右上边缘相接，正式平台文字安排在标注的内侧空白区，不能顶着人物排。\n- 本阶段只完成底图与区域确认；正式文案字号、溢出、业务逐项核对和390px手机效果尚未进入验证阶段，不提前标为通过。\n';
await fs.writeFile(path.join(root,'review/visual-qa.md'),qa);
const staging=path.join(root,'output/background-package-v1');
await fs.mkdir(staging,{recursive:true});
for(const dir of ['01-illustrations','02-layouts','03-backgrounds','04-png-export','assets','references','review','source'])await fs.cp(path.join(root,dir),path.join(staging,dir),{recursive:true});
for(const file of ['README.md','content.json','campaign-state.json'])await fs.copyFile(path.join(root,file),path.join(staging,file));
console.log('预览、状态和源文件已整理：'+staging);

