import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const project=path.resolve(process.argv[2] || path.join(path.dirname(fileURLToPath(import.meta.url)),'..'));
const require=createRequire(path.join(path.resolve(process.argv[3] || path.join(path.dirname(process.execPath),'../node_modules')),'package.json'));
const sharp=require('sharp');
const B=(x,y,width,height)=>({x,y,width,height});
const pages=[
{id:'desktop-absolute',label:'端游绝密价目',version:'r1',w:836,h:1881,ew:3200,eh:7200,platforms:['desktop'],source:'exec-58f4790e-901e-4373-a98a-bee3b0013573.png',header:{container:B(39,87,758,239),copy:B(64,110,374,174),hero:B(466,92,327,208)},conditions:{container:B(39,327,759,429),heading:B(105,355,626,70),cards:[B(62,440,215,188),B(311,440,211,188),B(560,440,215,188)],copy:[B(82,465,175,140),B(329,465,175,140),B(580,465,175,140)],conjunctions:[{cx:294,cy:534},{cx:543,cy:534}],result:B(185,682,470,50),resultCopy:B(200,693,440,29)},prices:{container:B(39,773,759,385),header:B(47,780,742,52),columns:[B(65,780,352,52),B(447,780,325,52)],rowEdges:[832,896,960,1023,1087,1150]},reward:{container:B(39,1178,759,240),copy:B(70,1200,402,195),exclude:B(496,1191,301,227)},notices:{container:B(39,1436,759,354),heading:B(65,1458,700,42),groups:[B(65,1500,700,38),B(65,1553,700,62),B(65,1629,700,60),B(65,1709,700,64)],dividerY:[1547,1622,1696],bodyFont:30,lineHeight:32,phoneBodyPx:14.0}},
{id:'mobile-absolute',label:'手游绝密价目',version:'r2',w:836,h:1882,ew:3200,eh:7200,platforms:['mobile'],source:'exec-b8653170-a8f7-4d40-8fbd-37026a8414b1.png',header:{container:B(39,84,759,233),copy:B(64,108,353,180),hero:B(442,67,356,246)},conditions:{container:B(31,349,775,386),heading:B(163,354,510,45),cards:[B(54,450,220,168),B(308,450,220,168),B(562,450,220,168)],copy:[B(74,474,180,120),B(328,474,180,120),B(582,474,180,120)],conjunctions:[{cx:291,cy:534},{cx:546,cy:534}],result:B(182,666,474,53),resultCopy:B(196,677,446,31)},prices:{container:B(35,759,766,393),header:B(43,766,750,52),columns:[B(64,766,352,52),B(450,766,325,52)],rowEdges:[818,884,951,1016,1081,1144]},reward:{container:B(31,1174,777,208),copy:B(66,1195,392,165),exclude:B(492,1194,314,188)},notices:{container:B(31,1410,775,394),heading:B(62,1440,710,53),groups:[B(62,1520,710,57),B(62,1594,710,60),B(62,1669,710,60),B(70,1743,632,47)],dividerY:[1509,1588,1661,1735],bodyFont:30,lineHeight:32,phoneBodyPx:14.0}},
{id:'combined-advert',label:'双平台合并广告',version:'r2',w:1086,h:1448,ew:2400,eh:3200,platforms:['desktop','mobile'],source:'exec-b6ef1f66-513e-4015-a6a1-af0ea084f7a0.png',header:{container:B(44,58,1000,207),copy:B(140,77,518,174),hero:B(693,35,353,220)},conditions:{container:B(44,282,1001,451),heading:B(204,293,678,42),cards:[B(65,382,272,209),B(407,382,271,209),B(748,382,272,209)],copy:[B(85,414,232,148),B(427,414,232,148),B(768,414,232,148)],conjunctions:[{cx:372,cy:489},{cx:713,cy:489}],result:B(239,654,609,58),resultCopy:B(258,665,570,36)},prices:{container:B(44,750,999,406),header:B(50,756,987,59),columns:[B(64,756,367,59),B(455,756,270,59),B(746,756,277,59)],rowEdges:[815,878,944,1012,1080,1142]},reward:{container:B(44,1175,1000,199),copy:B(306,1194,474,150),exclude:[B(31,1181,263,237),B(800,1194,286,254)]},showNotices:false},
{id:'combined-cover',label:'16:9共用封面',version:'r1',w:1672,h:941,ew:3200,eh:1800,platforms:['desktop','mobile'],source:'exec-96ad3df7-dce2-4779-a613-fbb3c93df645.png',header:{container:B(54,40,976,281),copy:B(260,72,702,212),hero:B(1029,210,611,492)},conditions:{container:B(54,324,976,402),heading:B(113,344,852,63),cards:[B(72,423,279,183),B(398,423,281,183),B(729,423,279,183)],copy:[B(92,447,240,138),B(418,447,240,138),B(749,447,240,138)],conjunctions:[{cx:374,cy:519},{cx:704,cy:519}],result:B(238,658,600,44),resultCopy:B(257,665,562,30)},reward:{container:B(54,739,976,112),copy:B(234,750,723,86)},showNotices:false,showEntryPrice:false}
];
await fs.mkdir(path.join(project,'04-png-export'),{recursive:true});
const requests=[],items=new Map();
function req(text,size,cx,cy,fill='#24475c'){const id='actual-'+requests.length;requests.push({id,text,font:'Microsoft YaHei UI',style:0});return {id,text,size,cx,cy,fill};}
function label(text,b,size=30,fill='#24475c'){return req(text,size,b.x+b.width/2,b.y+b.height/2,fill);}
for(const p of pages){
const src=path.join(project,'03-backgrounds',p.id+'-background-'+p.version+'.png'),bg=await fs.readFile(src);
const meta=await sharp(bg).metadata();
if(meta.width!==p.w||meta.height!==p.h)throw Error('Actual coordinates must be remeasured: '+p.id);
p.native={width:meta.width,height:meta.height};
p.background='assets/backgrounds/'+p.id+'.png';p.highResolution='04-png-export/'+p.id+'-background.png';
p.upscaleMethod='Lanczos3; background enlarged from preserved native raster, not native 4K drawing';
await fs.writeFile(path.join(project,p.background),bg);
await fs.writeFile(path.join(project,p.highResolution),await sharp(bg).resize(p.ew,p.eh,{fit:'fill',kernel:'lanczos3'}).withMetadata({density:300}).png({compressionLevel:9}).toBuffer());
await fs.writeFile(path.join(project,'review',p.id+'-background-phone.png'),await sharp(bg).resize({width:390}).png().toBuffer());
await fs.writeFile(path.join(project,'review',p.id+'-background-preview.png'),await sharp(bg).resize({width:Math.min(p.w,1100)}).png().toBuffer());
const labs=[];
const hc=p.header.copy;
labs.push(req('品牌 / 主标题',p.id==='combined-cover'?58:36,hc.x+hc.width/2,hc.y+hc.height*.38));
labs.push(req('平台 / 绝密',p.id==='combined-cover'?40:30,hc.x+hc.width/2,hc.y+hc.height*.78));
labs.push(label('三项缺一不可',p.conditions.heading,p.id==='combined-cover'?54:32));
p.conditions.copy.forEach((b,i)=>{labs.push(req(['沙色保险','常规大保险','带出物资'][i],p.id==='combined-cover'?36:30,b.x+b.width/2,b.y+b.height*.25));labs.push(req(i===2?'平台门槛':'次数目标',p.id==='combined-cover'?36:32,b.x+b.width/2,b.y+b.height*.70));});
p.conditions.conjunctions.forEach(c=>labs.push(req('且',22,c.cx,c.cy)));
labs.push(label('同局全满足 ＋ 成功撤离',p.conditions.resultCopy,p.id==='combined-cover'?24:26,'#fff7e9'));
if(p.prices){p.prices.columns.forEach((c,i)=>labs.push(label(i===0?'完成目标':p.prices.columns.length===3?['','端游','手游'][i]:'订单价格',c,31,'#fff7e9')));for(let i=0;i<5;i++)labs.push(req('第'+(i+1)+'档',30,p.prices.columns[0].x+p.prices.columns[0].width/2,(p.prices.rowEdges[i]+p.prices.rowEdges[i+1])/2));}
labs.push(label('许愿 / 条件奖励',p.reward.copy,p.id==='combined-cover'?38:32));
if(p.notices){labs.push(label('下单须知',p.notices.heading,32));p.notices.groups.forEach((g,i)=>labs.push(label(['达标口径','炸单与连续计数','代摸须先询问板板','赠送保底补打'][i],g,26)));}
items.set(p.id,{bg,labs});
}
const input=path.join(project,'02-layouts/actual-guide-font-requests.json'),output=path.join(project,'02-layouts/actual-guide-font-outlines.json');
await fs.writeFile(input,JSON.stringify(requests));
const script=path.join(project,'source/outline-guide.ps1');
const command="& ([scriptblock]::Create([System.IO.File]::ReadAllText('"+script+"'))) -InputFile '"+input+"' -OutputFile '"+output+"'";
console.log(execFileSync('powershell.exe',['-NoProfile','-Command',command],{encoding:'utf8'}).trim());
const glyphs=JSON.parse(await fs.readFile(output,'utf8'));
const dash=b=>'<rect x="'+b.x+'" y="'+b.y+'" width="'+b.width+'" height="'+b.height+'" rx="5" fill="#fff9ea" fill-opacity=".13" stroke="#577f93" stroke-width="1.2" stroke-dasharray="6 5"/>';
for(const p of pages){
const {bg,labs}=items.get(p.id);
let overlay=dash(p.header.copy)+dash(p.conditions.heading)+p.conditions.copy.map(dash).join('')+dash(p.conditions.resultCopy)+dash(p.reward.copy);
if(p.notices)overlay+=dash(p.notices.heading)+p.notices.groups.map(dash).join('');
const bounds=[];
for(const e of labs){const g=glyphs[e.id];let scale=e.size/100;
const dx=e.cx-g.width*scale/2-g.x*scale,dy=e.cy-g.height*scale/2-g.y*scale;
bounds.push({text:e.text,x:e.cx-g.width*scale/2,y:e.cy-g.height*scale/2,width:g.width*scale,height:g.height*scale,size:e.size});
overlay+='<g fill="'+e.fill+'" transform="translate('+dx+' '+dy+') scale('+scale+')"><path d="'+g.d+'"/></g>';
}
const svg='<svg xmlns="http://www.w3.org/2000/svg" width="'+p.w+'" height="'+p.h+'" viewBox="0 0 '+p.w+' '+p.h+'"><title>文字位置稿，非正式成图</title><image width="'+p.w+'" height="'+p.h+'" href="data:image/png;base64,'+bg.toString('base64')+'"/>'+overlay+'</svg>';
await fs.writeFile(path.join(project,'02-layouts',p.id+'-actual-position.svg'),svg);
await fs.writeFile(path.join(project,'review',p.id+'-position.png'),await sharp(Buffer.from(svg)).png().toBuffer());
await fs.writeFile(path.join(project,'review',p.id+'-position-phone.png'),await sharp(Buffer.from(svg)).resize({width:390}).png().toBuffer());
await fs.writeFile(path.join(project,'review',p.id+'-position-text-bounds.json'),JSON.stringify(bounds,null,2)+'\n');
}
const measures={revision:'background-r1',coordinateBasis:'逐张查看本轮实际生成底图，记录各原生尺寸面板、人物、图标与净文字区；不沿用1024/1200/1600结构稿坐标。',meaning:'位置稿仅为区域说明；正式价格、目标数值、单位基线和规则正文将在底图确认后测量并矢量排版。',pages};
await fs.writeFile(path.join(project,'02-layouts/generated-background-measurements.json'),JSON.stringify(measures,null,2)+'\n');
const thumbCards=await Promise.all(pages.map(async p=>{const img=await sharp(items.get(p.id).bg).resize({width:350,height:500,fit:'contain',background:'#fff9ee'}).png().toBuffer();return img;}));
const montageSvg='<svg xmlns="http://www.w3.org/2000/svg" width="1480" height="540"><rect width="1480" height="540" fill="#f2e7d3"/>'+thumbCards.map((b,i)=>'<image x="'+(20+i*370)+'" y="20" width="350" height="500" href="data:image/png;base64,'+b.toString('base64')+'"/>').join('')+'</svg>';
await fs.writeFile(path.join(project,'review/background-overview.png'),await sharp(Buffer.from(montageSvg)).png().toBuffer());
const positionThumbs=[];
for(const p of pages)positionThumbs.push(await sharp(await fs.readFile(path.join(project,'review',p.id+'-position.png'))).resize({width:350,height:500,fit:'contain',background:'#fff9ee'}).png().toBuffer());
const positionMontage='<svg xmlns="http://www.w3.org/2000/svg" width="1480" height="540"><rect width="1480" height="540" fill="#f2e7d3"/>'+positionThumbs.map((b,i)=>'<image x="'+(20+i*370)+'" y="20" width="350" height="500" href="data:image/png;base64,'+b.toString('base64')+'"/>').join('')+'</svg>';
await fs.writeFile(path.join(project,'review/position-overview.png'),await sharp(Buffer.from(positionMontage)).png().toBuffer());
const sections=pages.map(p=>'<section><h2>'+p.label+'</h2><p>原生 '+p.w+'×'+p.h+' · 无字PNG导出 '+p.ew+'×'+p.eh+'，底图经放大；正式文字后续按目标尺寸矢量渲染。</p><p><a href="../'+p.background+'">原生底图</a> · <a href="../'+p.highResolution+'">高清无字PNG</a> · <a href="'+p.id+'-position.png">实际文字位置稿</a> · <a href="../02-layouts/'+p.id+'-structure.png">初始结构稿</a></p><div class="pair"><figure><img src="'+p.id+'-background-preview.png" alt="'+p.label+'无字底图"><figcaption>本轮新生成无字底图</figcaption></figure><figure><img src="'+p.id+'-position.png" alt="'+p.label+'实际文字位置稿"><figcaption>按实际原生坐标标示净文字区；非正式填字</figcaption></figure></div></section>').join('');
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>沙保连连看 · 第二步底图确认</title><style>body{margin:0;background:#eee2ce;color:#203c50;font:16px/1.75 "Microsoft YaHei UI",sans-serif}main{max-width:1480px;margin:auto;padding:28px}h1{font-size:30px}h2{font-size:25px;margin-bottom:8px}p{margin:8px 0 16px}.tag{display:inline-block;background:#d8ece2;border-radius:24px;padding:4px 16px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}figure{margin:0}img{width:100%;height:auto;display:block;border-radius:12px}figcaption{padding:8px 0;color:#536b75}section{background:#fff9ed;border-radius:20px;padding:24px;margin:24px 0}a{color:#276b70}.summary{background:#fff9ed;border-radius:20px;padding:24px}@media(max-width:760px){.pair{grid-template-columns:1fr}main{padding:16px}}</style><main><span class="tag">第二步 · 四张无字底图待确认</span><h1>兴安电竞 · 沙保连连看 · 全新套图</h1><div class="summary"><p><strong>三项缺一不可：</strong>同局摸2个沙色保险、摸4个常规大保险、带出物资严格＞端游888w / 手游788w，并成功撤离才达标。</p><p>三张等权条件卡通过两个“且”和汇合线连接到唯一结果条；四张底图全新生成，继续使用本轮已确认的新蜂医与灰鼠插画。</p><p>连续价：端游两次688r / 三次1288r；手游两次588r / 三次888r。累计档位与已确认奖励规则沿用。正式业务文字将在底图确认后矢量填入。</p><p><a href="background-overview.png">四张底图总览</a> · <a href="position-overview.png">四张文字位置稿总览</a> · <a href="../02-layouts/generated-background-measurements.json">实际区域记录</a> · <a href="../output/campaign-sand-safe-redesign-backgrounds-r1.zip">第二步确认包</a></p></div>'+sections+'<p>下一步：底图确认后进行正式矢量文案、字形测量、逐张整图与390px手机预览验收，最后交付PNG与两种SVG。</p></main></html>';
await fs.writeFile(path.join(project,'review/backgrounds.html'),html);
const stateFile=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(stateFile,'utf8'));
state.stage='awaiting-background-approval';
state.approvals.background={status:'pending',basis:'本轮四页无字底图均重新生成，待用户确认后进入矢量填字。',review:'review/backgrounds.html'};
state.backgrounds={provider:'built-in image_gen',revision:'background-r1',pages:pages.map(p=>({id:p.id,label:p.label,file:p.background,generatedFile:'03-backgrounds/'+p.id+'-background-'+p.version+'.png',source:'C:/Users/chenh/.codex/generated_images/01a100c5-2566-7302-9896-ceb05845e9bf/'+p.source,native:{width:p.w,height:p.h},export:{file:p.highResolution,width:p.ew,height:p.eh},upscaleMethod:p.upscaleMethod,prompt:'03-backgrounds/'+p.id+'-prompt.txt',correctionPrompt:p.version==='r2'?'03-backgrounds/'+p.id+'-correction-prompt.txt':null})),measurements:'02-layouts/generated-background-measurements.json',overview:'review/background-overview.png',positionOverview:'review/position-overview.png',review:'review/backgrounds.html',package:'output/campaign-sand-safe-redesign-backgrounds-r1.zip'};
state.nextStep='等待用户确认第二步四张新无字底图与实际文字位置稿；确认后进行第三步正式矢量填字、字形测量、成图复检与成品打包。';
await fs.writeFile(stateFile,JSON.stringify(state,null,2)+'\n');
console.log(JSON.stringify({stage:state.stage,pages:pages.map(p=>({id:p.id,native:p.native,export:[p.ew,p.eh]})),review:'review/backgrounds.html'}));
