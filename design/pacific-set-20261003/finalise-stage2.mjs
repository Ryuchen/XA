import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const sharp=require(String.raw`C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp`);
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const vault=String.raw`C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库`;
const taskDir=path.dirname(new URL(import.meta.url).pathname.replace(/^\//,''));
const state=JSON.parse(await fs.readFile(path.join(project,'campaign-state.json'),'utf8'));
const assets=state.artwork.backgrounds;
const measured=[];
const zone=(id,safe,extra={})=>({id,safe,...extra});
for(const a of assets){
 let zones=[];
 if(a.id.startsWith('poster')){
  zones=[zone('brand',[84,80,299,107],{container:[32,66,311,184],exclude:[35,67,78,110],centerX:191.5}),zone('title',[51,111,299,172],{container:[32,66,311,184],centerX:175}),zone('mode',[62,226,291,255],{container:[46,219,305,262]}),zone('objective',[52,292,674,326],{container:[32,280,693,337]}),zone('hero',null,{exclude:[315,48,693,276]})];
  const maps=[{titleTop:352,header:[412,447],rows:[448,652]},{titleTop:666,header:[734,769],rows:[770,976]},{titleTop:990,header:[1064,1099],rows:[1100,1306]}];
  maps.forEach((m,i)=>{
   zones.push(zone(`map-${i+1}-title`,[185,m.titleTop+15,670,m.header[0]-10],{container:[32,m.titleTop,693,m.header[0]],exclude:[37,m.titleTop+5,183,m.header[0]],centerX:427.5}));
   zones.push(zone(`map-${i+1}-header`,[52,m.header[0]+6,674,m.header[1]-5],{container:[33,m.header[0],691,m.header[1]],columns:[[52,218],[233,357],[370,506],[522,674]]}));
   const rowHeight=(m.rows[1]-m.rows[0])/5;
   for(let j=0;j<5;j++)zones.push(zone(`map-${i+1}-row-${j+1}`,[52,m.rows[0]+j*rowHeight+4,674,m.rows[0]+(j+1)*rowHeight-4],{container:[33,m.rows[0]+j*rowHeight,692,m.rows[0]+(j+1)*rowHeight],centerY:m.rows[0]+(j+.5)*rowHeight,columns:[[52,218],[233,357],[370,506],[522,674]],nativeBodyFont:26,phoneEquivalent:26*390/724}));
  });
  zones.push(zone('addons-heading',[180,1310,615,1331],{note:'细窄间隙，只放短标题；不能加入普通正文'}),
   zone('prophet-title',[60,1360,224,1385],{container:[49,1352,238,1390]}),zone('prophet-fee',[494,1360,654,1385],{container:[480,1352,670,1390]}),
   zone('prophet-intro',[53,1396,674,1424],{container:[32,1337,692,1538]}),
   zone('prophet-options',[53,1450,674,1518],{container:[32,1337,692,1538],columns:[[53,348],[379,674]],nativeBodyFont:26,phoneEquivalent:26*390/724}),
   zone('door-title',[60,1574,224,1599],{container:[49,1566,239,1603]}),
   zone('door-body',[53,1618,674,1772],{container:[32,1550,692,1790],nativeBodyFont:26,phoneEquivalent:26*390/724,capacity:'4行等义完整说明；每行约40px间距，净字高约26px'}),
   zone('notice-title',[61,1828,234,1853],{container:[50,1820,248,1858]}),
   zone('notice-body',[53,1870,674,2012],{container:[32,1803,692,2025],nativeBodyFont:26,phoneEquivalent:26*390/724,capacity:'绝密4行/机密4行；上下边距与段内/段间距在填字后按实际字形复查'}),
   zone('footer',[197,2042,617,2077],{exclude:[0,2040,170,a.nativeHeight],note:'边角灯与沙滩不进入中心品牌区；不把大段规则塞到海景上'}));
 }else if(a.id==='social'){
  zones=[zone('brand',[85,105,480,144],{container:[50,86,514,251]}),zone('title',[78,164,486,237],{container:[50,86,514,251]}),zone('hero',null,{exclude:[524,43,1054,359]}),zone('objective',[82,381,1008,449],{container:[46,359,1039,474]})];
  [{x:47,y:503},{x:560,y:503},{x:47,y:807},{x:560,y:807}].forEach((c,i)=>zones.push(zone(`variant-${i+1}-header`,[c.x+21,c.y+14,c.x+455,c.y+48],{container:[c.x,c.y,c.x+479,c.y+60]}),zone(`variant-${i+1}-price`,[c.x+45,c.y+104,c.x+437,c.y+188],{container:[c.x+23,c.y+87,c.x+454,c.y+204]}),zone(`variant-${i+1}-guarantee`,[c.x+31,c.y+227,c.x+448,c.y+260],{container:[c.x,c.y+205,c.x+479,c.y+276]})));
  zones.push(zone('maps-and-experience-condition',[78,1120,1008,1188],{container:[46,1101,1039,1210],note:'广告核心为体验档起步价；明确2个不同出生点，不把起步价冒充全部档位价格'}),zone('footer',[253,1231,895,1271],{exclude:[0,1240,222,1448]}));
 }else{
  zones=[zone('brand',[166,151,706,221],{container:[72,108,785,528],centerX:436,exclude:[72,108,334,154]}),zone('title',[106,246,750,372],{container:[72,108,785,528],centerX:428}),zone('subtitle',[125,386,733,507],{container:[72,108,785,528],centerX:429}),zone('platforms',[116,577,741,616],{container:[74,562,787,631],centerX:428.5}),zone('modes',[116,674,741,721],{container:[74,649,787,749],centerX:428.5}),zone('hero',null,{exclude:[802,139,1627,659]})];
 }
 const record={id:a.id,path:a.path,w:a.nativeWidth,h:a.nativeHeight,method:'按实际PNG底板边界、色线检测、局部预览与插画轮廓测量；不是从已排文字反推容器',zones};
 measured.push(record);
 const imageData=await fs.readFile(path.join(project,a.path));
 const markup=`<svg xmlns="http://www.w3.org/2000/svg" width="${a.nativeWidth}" height="${a.nativeHeight}"><image href="data:image/png;base64,${imageData.toString('base64')}" width="100%" height="100%"/>${zones.filter(z=>z.safe).map(z=>`<rect x="${z.safe[0]}" y="${z.safe[1]}" width="${z.safe[2]-z.safe[0]}" height="${z.safe[3]-z.safe[1]}" fill="#12a879" fill-opacity=".035" stroke="#159a73" stroke-width="1.5" stroke-dasharray="5 4"/>`).join('')}</svg>`;
 await sharp(Buffer.from(markup)).png().toFile(path.join(project,`review/${a.id}-copy-safe-r2.png`));
}
await fs.writeFile(path.join(project,'review/background-measured-zones-r2.json'),JSON.stringify(measured,null,2));
const outlineData=JSON.parse(await fs.readFile(path.join(taskDir,'capacity-outlines.json'),'utf8'));
const capacity=Object.values(outlineData).map(t=>({text:t.text,font:t.font,nativeSize:26,width:+(t.width*.26).toFixed(3),height:+(t.height*.26).toFixed(3),advance:+(t.advance*.26).toFixed(3),phoneEquivalent:+(26*390/724).toFixed(3)}));
const ruleRows=capacity.filter(t=>t.font==='Microsoft YaHei UI'&&t.text.length>8);
if(ruleRows.some(t=>t.width>621))throw new Error('规则结构容量不足：示例等义说明超出真实净宽');
await fs.writeFile(path.join(project,'review/rule-capacity-measurements-r2.json'),JSON.stringify({basis:'已核对业务含义的等义说明，仅作底图容量测量，尚未填正式成图。字体实际轮廓，不是字符数估算。正式混合字重和版面仍需第三步复查。',safeWidth:621,safeNoticeHeight:142,nativeBodyFont:26,phoneEquivalent:26*390/724,maxRuleWidth:Math.max(...ruleRows.map(r=>r.width)),rows:capacity},null,2));
await fs.copyFile(path.join(taskDir,'capacity-requests.json'),path.join(project,'source/capacity-requests.json'));
await fs.copyFile(new URL(import.meta.url),path.join(project,'source/finalise-stage2.mjs'));
const qa=`# 环太平洋 · 无字底图检查 stage2-r2\n\n当前阶段：四类新底图待用户确认。插画已由用户“继续”确认；业务已由用户明确机密统一5点、沿用现有机密地图与规则。未矢量填字，未完成六张正式海报，不标记最终交付。\n\n## 实际检查版本\n\n| 底图 | 原生尺寸 | 映射页面 | 检查证据 |\n|---|---|---|---|\n${assets.map(a=>`| ${a.label} ${a.revision} | ${a.nativeWidth}×${a.nativeHeight} | ${a.pages.join(' / ')} | ${a.path}；review/${a.id}-mobile390-${a.revision}.png |`).join('\n')}\n\n- 整体已查看实际ImageGen输出、已保存的四图拼版、四张390px手机预览；规则、广告标题、封面标题查看原尺寸局部。\n- 绝密/机密各3张地图表，每表5个完整空行；标题条整段深蓝，不做半绿色表头。主体数据行无道具、花纹侵入。\n- 机密第三图为核电站；绝密第三图为潮汐监狱。前两图为巴克什古城与航天基地发射设施。\n- 角色、建筑、门和海景统一二次元手绘，人物各两手，无明显额外肢体；头、耳、尾、地图、卡片与门主体完整。\n- 三块附加玩法/须知卡保持空白，面板边界与图文邻接安全区已记录review/background-measured-zones-r2.json，共${measured.reduce((n,r)=>n+r.zones.length,0)}块审核区域。辅助框是review专用，不写入正式背景。\n- 初稿任意门区偏紧、页尾风景偏大，已用ImageGen加高任意门卡并压缩页尾；广告标题区云、鸟与海景干扰，已局部编辑为干净奶白标题底板。r1原稿保存在assets/backgrounds/archive。\n- 修订后任意门净区621×154px、须知621×142px；System.Drawing实际字体测量：26px正文手机等效${(26*390/724).toFixed(2)}px，等义规则最长可见宽${Math.max(...ruleRows.map(r=>r.width)).toFixed(2)}px，小于621px净宽。仅验证结构容量，混合字重、居中、baseline、字距及正式PNG可读性必须填字后另查。\n- 封面文字区按实际左侧奶白面板、深蓝平台条及浅蓝模式条分别居中；顶部叶片排除在品牌安全区之外，右侧角色不可侵入左栏。封面不写须知或结算例外。\n- 广告只安排四版体验档起步价与保底，须显示2个不同出生点及对应地图，避免将体验价称为所有档位同价；没有下单须知。\n- 无文字、价格、伪文字、二维码或联系方式；没有把未填字底图称为正式海报。\n- 所有原生图和生成提示词保留。后续放大输出只代表导出分辨率，不描述为原生3200绘制。\n\n## 放行状态\n\n结构与底图视觉检查通过，等待用户确认四类新底图后进入矢量填字。业务contentVerified=true来自用户资料与明确答复，不代表最终视觉验收已通过。\n`;
await fs.writeFile(path.join(project,'review/visual-qa.md'),qa);
await fs.writeFile(path.join(project,'review/stage2.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>环太平洋 · 无字底图确认</title><style>body{margin:0;background:#fbf8ee;color:#23465b;font:17px/1.65 system-ui}main{max-width:1200px;margin:auto;padding:28px}h1{font-size:30px}h2{font-size:23px;margin-top:0}p{max-width:980px}a{color:#187c78}figure{margin:0}img{max-width:100%;height:auto;display:block;border-radius:16px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.card{background:#fffdf7;border:1px solid #d4e4e7;border-radius:24px;padding:20px;margin-bottom:24px}.long{max-width:390px;margin:auto}.wide{margin-top:26px}@media(max-width:720px){.grid{grid-template-columns:1fr}main{padding:16px}}</style><main><h1>环太平洋 · 四类无字底图</h1><p>插画已确认。四版价目、目标、地图及规则全部核实：机密也统一5点并沿用原地图与规则；机密保底沿用原绝密，仅手游绝密各档保底减400w。当前是底图确认阶段，还没有填入价格或正式文案。</p><p>最终六张：端游绝密、端游机密、手游绝密、手游机密、广告、封面。绝密与机密各共用一张对应底图。</p><img src="stage2-backgrounds-contact-sheet-r2.png" alt="四类底图概览"><div class="grid wide">${assets.map(a=>`<section class="card"><h2>${a.label} ${a.revision}</h2><p>原生 ${a.nativeWidth}×${a.nativeHeight}；${a.pages.join('、')}</p><figure><img class="${a.id.startsWith('poster')?'long':''}" src="../${a.path}" alt="${a.label}"></figure><p><a href="../${a.path}" download>下载原生底图</a> · <a href="${a.id}-copy-safe-r2.png">查看文字安全区</a></p></section>`).join('')}</div><p><a href="四版价目核对.md">已核对四版价目</a> · <a href="visual-qa.md">底图检查记录</a></p><p>请确认四类底图。确认后进入矢量填字、六图成品检查和打包。</p></main></html>`);
state.stage='backgrounds-awaiting-confirmation';
state.backgroundVisualQA={version:'stage2-r2',passed:true,basis:'实际整图、390px预览、原尺寸规则/标题局部、原生面板测量和规则字体容量核查',report:'review/visual-qa.md',zones:'review/background-measured-zones-r2.json'};
state.reviewPage='review/stage2.html';
await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2));
const cardPath=path.join(vault,'01_玩法/环太平洋.md');
let card=await fs.readFile(cardPath,'utf8');
card=card.replace('目前第一阶段新插画待确认，机密目标与地图/规则沿用口径待答复，尚未完成六张成品。','探险插画已确认，机密统一5点并沿用现有地图与规则；当前四类无字底图待确认，尚未完成六张成品。');
card=card.replace('- 绝密目标仍2/3/4/5/全点。待确认：机密两版是否也统一5点；是否仍为巴克什/航天/核电站并沿用现有机密附加玩法与须知。未将未答复内容记为本轮已确认。','- 本轮已由用户明确答复：机密两版统一2/3/4/5/全点，沿用巴克什/航天基地/核电站及现有机密附加玩法与须知；与绝密一样豪华为5个不同出生点。');
const key='## 2026-10-03 · 环太平洋插画通过、四类无字底图待确认';
const event=`\n\n${key}\n\n- 用户“继续”确认蜂医灰鼠探险插画；另明确机密统一5点、沿用现有机密地图与规则。四版内容全部核实，目标统一2/3/4/5/全点。\n- 完成四类无字底图：绝密暖金r2原生724×2171，机密薄荷r2原生724×2172，四版起步价广告r2原生1086×1448，横版封面r1原生1671×941。映射四张平台/模式价目＋广告＋封面，共六张。\n- 价目各3地图×5行，连续深蓝表头；机密第三图核电站，绝密潮汐监狱。预言家、任意门、须知三卡，任意门加高、页尾压缩；广告左标题环境细节已清为奶白底板。二次元手绘角色、门、场景统一，所有正式文字留空。\n- 已查看实际整图、四张390px预览、规则和标题局部；${measured.reduce((n,r)=>n+r.zones.length,0)}块真实净区测量，规则26px在390px宽手机等效14.01px。只核查结构容量，填字后仍需baseline、字距、居中和完整六图复检。\n- 当前为四类新底图待确认，未填正式矢量文字、未完成六图成品，不将旧图充作本轮交付。下一步底图确认后填字。\n- 来源与记录：${project}；review/stage2.html，review/background-measured-zones-r2.json，review/visual-qa.md。大型PNG、源稿和提示词只保存在项目；Obsidian保存说明和索引。\n- 原因：按用户确认进入第二阶段并完成业务歧义核实；影响仅环太平洋普通玩法六图物料，价格和保底仍按本轮已确认映射，不外推到任务版或其他玩法，未外部发布。\n`;
if(!card.includes(key))card+=event;
await fs.writeFile(cardPath,card);
for(const file of ['00_入口/当前共识.md','05_变更日志/变更日志.md']){
 const p=path.join(vault,file);const text=await fs.readFile(p,'utf8');if(!text.includes(key))await fs.writeFile(p,text+event);
}
const dPath=path.join(vault,'04_决策记录/2026-10-03-环太平洋四版价差与机密原绝密保底.md');
let d=await fs.readFile(dPath,'utf8');
d=d.replace('status: confirmed-pricing-visual-pending','status: confirmed-pricing-background-pending').replace('机密目标、地图和附加规则仍待答复。','机密目标、地图和附加规则已由用户确认统一5点并沿用现有机密口径。').replace('- [ ] 用户确认新插画。','- [x] 用户确认新插画。').replace('- [ ] 用户确认机密目标与地图/规则。','- [x] 用户确认机密统一5点与现有地图/规则。');
d+='\n\n2026-10-03第二阶段：四类无字底图r2/r1制作和结构检查完成，待用户确认；六张正式矢量成品尚未输出。\n';
await fs.writeFile(dPath,d);
console.log(JSON.stringify({stage:state.stage,contentVerified:state.contentVerified,assets:assets.length,mappedPages:assets.flatMap(a=>a.pages),measuredZones:measured.reduce((n,r)=>n+r.zones.length,0),maxRuleWidth:Math.max(...ruleRows.map(r=>r.width)),phoneBody:26*390/724,reviewPage:state.reviewPage},null,2));
