import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const here=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(import.meta.url),sharp=require(String.raw`C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp`);
const read=async rel=>JSON.parse(await fs.readFile(path.join(project,rel),'utf8'));
const write=async(rel,value)=>fs.writeFile(path.join(project,rel),JSON.stringify(value,null,2));
const c=await read('content.json'),s=await read('campaign-state.json'),audit=await read('review/layout-and-data-checks-r1.json'),plan=await read('layout.json');
if(audit.failures.length||audit.missingPages.length||audit.pages.length!==6)throw new Error('六页排版检查未通过');
const expectedIds=s.deliveryScope.pageIds;
const manifest=(await read('output/manifest.json')).sort((a,b)=>expectedIds.indexOf(a.id)-expectedIds.indexOf(b.id));
if(JSON.stringify(manifest.map(p=>p.id))!==JSON.stringify(expectedIds))throw new Error('页面范围不一致');
await write('output/manifest.json',manifest);
const baseline=await read('references/绝密5出生点当前价目.json'),old=await read('references/现有绝密机密规则.json');
const checks=[],assert=(name,actual,expected)=>{const passed=JSON.stringify(actual)===JSON.stringify(expected);checks.push({name,actual,expected,passed});if(!passed)throw new Error(name+' 不一致');};
for(const t of c.tiers){
 const priceDelta=t.id==='desktop-absolute'?0:t.id==='mobile-secret'?220:200,lootDelta=t.id==='mobile-absolute'?400:0;
 assert(t.id+'目标',t.targets,baseline.targets);
 assert(t.id+'地图',t.maps.map(m=>m.name),t.mode==='绝密'?baseline.rules.maps.map(m=>m.name):old.confidential.maps.map(m=>m.name));
 t.maps.forEach((m,i)=>m.prices.forEach((v,j)=>{assert(`${t.id}/${m.name}/${j}/售价`,v,baseline.rules.maps[i].prices[j]-priceDelta);assert(`${t.id}/${m.name}/${j}/保底`,m.guarantees[j],baseline.rules.maps[i].guarantees[j]-lootDelta);}));
 const rules=t.mode==='绝密'?baseline.rules:old.confidential;
 for(const key of ['prophetFee','prophetRewards','doorItem','doorThreshold','minimum'])assert(t.id+'/'+key,t.rules[key],rules[key]);
 assert(t.id+'/直升机限制',Boolean(t.rules.forbidPrisonHelicopter),t.mode==='绝密');
 const p=plan.pages.find(p=>p.id===t.id),b=await read('review/'+t.id+'-text-bounds.json');
 const groupText=prefix=>b.filter(x=>x.group?.startsWith(prefix)).map(x=>x.text).join('');
 const door=groupText('door-'),notice=groupText('notice-');
 for(const text of ['打手局前可选本局复活点','无论实际复活在哪','都按所选点计算','成功撤离可抵扣所选点','撤离失败则清空，重新累计'])assert(t.id+'/任意门语义/'+text,door.includes(text),true);
 for(const text of ['只计成功撤离','在已成功解锁的复活点复活后','超过6次','清除该复活点的撤离成功次数'])assert(t.id+'/须知语义/'+text,notice.includes(text),true);
 assert(t.id+'/失败原门槛',c.commonRules.failureLimit,6);
 assert(t.id+'/普通说明字号',audit.results.find(r=>r.page===p.id).minRule,26);
}
assert('所有文件无自动缩字',audit.results.flatMap(r=>r.shrunk).length,0);
const coverText=plan.pages.find(p=>p.id==='cover').elements.map(e=>e.text||'').join('');
assert('封面无须知及结算例外',/须知|补偿|失败|流局|另计/.test(coverText),false);
assert('背景均经用户确认',s.artwork.backgrounds.every(b=>b.approved),true);
const sameModePixels=[];
for(const mode of ['absolute','secret']){
 const a=await sharp(path.join(project,`review/desktop-${mode}-rules-detail.png`)).raw().toBuffer(),b=await sharp(path.join(project,`review/mobile-${mode}-rules-detail.png`)).raw().toBuffer();
 assert(mode+'双端规则像素一致',a.equals(b),true);sameModePixels.push({mode,identical:true});
}
await write('review/business-source-checks-r1.json',{revision:'r1',passed:true,checks,sameModePixels});
const hashFiles=[];
for(const p of manifest)for(const file of [p.png,p.outlinedSVG,p.editableSVG])hashFiles.push({file,sha256:createHash('sha256').update(await fs.readFile(path.join(project,file))).digest('hex')});
await write('review/final-file-hashes-r1.json',{revision:'r1',files:hashFiles});
const stage2=path.join(project,'review/visual-qa-stage2-r2.md');
try{await fs.access(stage2);}catch{await fs.copyFile(path.join(project,'review/visual-qa.md'),stage2);}
const qa=`# 环太平洋六图最终成图检查 r1\n\n日期：2026-10-03。结论：六张实际最终PNG、全部390px手机图与四版价格/规则局部已逐张打开复查，通过。下列记录为成图验收，前两阶段历史另存visual-qa-stage2-r2.md与illustration-qa-r1.md。\n\n## 内容与用户确认\n\n- 用户分别以“继续”确认独立插画和四类无字底图；机密两版明确沿用原绝密保底，统一2/3/4/5/全点并沿用现有机密地图与规则。无需重复视觉确认。\n- 最新5点、豪华降100r的绝密价目为端游绝密基准。手游绝密15售价各-200r/15保底各-400w；端游机密同手游绝密售价、保底为原绝密；手游机密在端游机密15售价各-20r，保底为原绝密。参考JSON和本轮确认独立核对60售价、60保底，见business-source-checks-r1.json。\n- 四版目标均2/3/4/5/全点；表内省略“不同”，上方完整目标明确累计从不同出生点成功撤离。绝密潮汐监狱，机密核电站。\n- 预言家每局+58r、2选1，猜中1次的前提保留；绝密+100w成功或失败均加/+250w只成功，机密+150w/+300w。任意门三件与160w/80w以上、可预选复活点、实际点不限、成功可抵扣所选点、失败清空重计均保留。\n- 须知保留成功撤离前提、低于门槛算流局、绝密监狱直升机禁止、已成功解锁点复活后失败超过6次清除该点成功次数。6次不是5点目标，未更改。机密不添加直升机限制。\n- 广告四张体验价卡388/188/188/168r起，保底1288/1288/888/1288w，明确体验档2点与三地图五档；封面只品牌、玩法、卖点、平台与模式，无须知或例外。\n\n## 实际PNG与逐页目视结论\n\n| 页ID | 完整图/手机图/局部 | 复查结论 |\n|---|---|---|\n${manifest.map(p=>`| ${p.id} | ${p.png}；review/${p.id}-mobile390.png${/^(desktop|mobile)-/.test(p.id)?`；review/${p.id}-price-detail.png、${p.id}-rules-detail.png`:''} | 主次、模式与数字正确；实际容器留白、居中、图文交界通过 |`).join('\n')}\n\n- 四张长图：主标题在左标题纸牌、平台模式在独立深蓝条内；目标条清楚。地图名称居中于扣除左插画的右侧文字区；连续深蓝表头，三张五行表逐行对齐。首末行和最长四位售价、8888/8488w均有可见留白。\n- 价格数字右对齐固定r列、保底右对齐固定w列；全部同行数字/单位同baseline，单位约主数字可见高度70.7%、间隙7原生布局单位。普通档名/目标常规，关键金额与全点档真Bold，未用伪描边。\n- 预言家条件和奖励作为完整两行组居中；任意门与须知正文左对齐，整体按真实净区纵向居中。普通解释常规字重，数值和决定条件选择性粗体。规则26原生字号，在390px宽等效14.01px，effectiveSize没有缩小；行距和分隔线均无相交。\n- 须知尾部四行留足空间，页脚与规则框保持间隔；不是把条款缩入页脚。手机上普通说明无需放大可读；“可抵扣”“低于”“以上”“超过6次”和前提可见。双端同模式规则局部像素一致，降低漏改风险。\n- 广告：四个模式标识、起价、保底三层清楚，各在实际卡片净区居中；起价对应体验2点，未写成全档同价，不附须知。封面：品牌、标题、两行卖点、平台、模式全部按左侧净文字区中线对齐，未向角色移动。\n- 六图二次元手绘风格统一；蜂医脸/手、灰鼠耳/持卡手、地图、任意门与医疗无人机未被文字遮挡，环境、叶片、道具只在边角，正文对比度正常。\n\n## 发现与修正闭环\n\n- 初批桌面绝密每局费字形略越窄标题净区0.39；调整标签和数字字号后复查。普通规则字号仍26。\n- 初批广告标题装饰越净区4.82、模式/保底窄标签稍紧；分别调整标题、标签字号。广告底部第二行上移2，整组上下留白恢复均衡。\n- 初批封面艺术标题偏上、平台窄条文字稍高；标题缩为110并下移7，卖点两行微调、平台字号50并重测。\n- 上述修正未改变任何安全区或检查容差，未更改底图或业务数字。重渲染六张后实际PNG/手机/局部均复查通过。\n\n## 几何辅助检查与源稿\n\n- layout-and-data-checks-r1.json：页面无缺失，248个展示/源数据映射、381个容器文字组、120组数字单位检查无失败；全部文字都有所属容器，未发生maxWidth自动缩字。自动检查只辅助，上面的实际成图目视结论独立成立。\n- 原生底图：绝密724×2171，机密724×2172，广告1086×1448，封面1671×941；独立插画1536×1024透明PNG。输出为四张3200宽长图、广告3000×4000、封面3200×1802。背景放大不代表原生3200绘制。\n- 各页保留PNG、可改正文文字SVG及转曲SVG。艺术标题为轮廓路径，文字/样式参数在layout.json；背景内嵌位图，整张不是全矢量。可改文字字体为Microsoft YaHei UI、Bahnschrift、Noto Sans SC Black；跨设备版式建议使用转曲版。\n- final-file-hashes-r1.json记录已验收的6PNG与12SVG哈希，打包后按包内文件逐项核验。\n`;
await fs.writeFile(path.join(project,'review/visual-qa.md'),qa);
const composites=[];
for(const [i,p]of manifest.entries()){
 const tile=await sharp(path.join(project,p.png)).resize({width:390,height:1175,fit:'contain',background:'#f8f7f0'}).png().toBuffer();
 composites.push({input:tile,left:(i%3)*410+10,top:Math.floor(i/3)*1195+10});
}
await sharp({create:{width:1230,height:2390,channels:3,background:'#f8f7f0'}}).composite(composites).png().toFile(path.join(project,'review/final-contact-sheet-r1.png'));
s.finalVisualQA={version:'r1',passed:true,basis:'六张实际最终PNG、全部390px手机图、四版原输出价格和规则局部逐页目视复查；业务独立来源映射及容器/字形检查辅助',report:'review/visual-qa.md',geometry:'review/layout-and-data-checks-r1.json',business:'review/business-source-checks-r1.json',hashes:'review/final-file-hashes-r1.json'};
s.backgroundVisualQA.report='review/visual-qa-stage2-r2.md';s.reviewPage='review/index.html';s.completedPages=expectedIds;s.stage='final-qa-passed';
s.deliveryFiles={pngZip:'环太平洋-六张海报-PNG-r1.zip',sourceZip:'campaign-r1.zip',pngEntryNames:{'desktop-absolute':'01-端游绝密价目.png','desktop-secret':'02-端游机密价目.png','mobile-absolute':'03-手游绝密价目.png','mobile-secret':'04-手游机密价目.png',social:'05-四版体验价广告.png',cover:'06-横版封面.png'}};
await write('campaign-state.json',s);
// Remove stale wording in the provenance entry while retaining its history basis.
c.sources.find(x=>x.type==='confirmed-rules').basis='机密既有核电站、预言家150/300w、任意门80w及488/388w流局规则；本轮用户已确认目标统一5点并沿用现有机密地图与规则';
c.social.scope=c.social.scope.replace('四張','四张');await write('content.json',c);
await fs.copyFile(path.join(here,'pack-final.ps1'),path.join(project,'source/pack.ps1'));
const qaScript=(await fs.readFile(path.join(here,'check-layout.mjs'),'utf8')).replace("const project=String.raw`C:\\Users\\chenh\\Documents\\ChatGPT\\兴安电竞玩法\\环太平洋-四版价目广告封面-20261003`;","const project=path.resolve(process.argv[2]||path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\\//,'')),'..')); ");
await fs.writeFile(path.join(project,'source/check-layout.mjs'),qaScript);
await fs.writeFile(path.join(project,'source/README.md'),'# 环太平洋 r1 源稿\n\ncontent.json为已确认业务源；layout-adapter.mjs为三地图五档布局，layout.json为本轮渲染计划。PNG、正文可改SVG、转曲SVG都在output。active背景和独立插画在assets，references含资料，source/prompts保存内置ImageGen提示词。\n\n用本机Node+Sharp与Windows系统字体：\n\n```powershell\nnode source/workflow.mjs layout --project <项目绝对路径>\nnode source/workflow.mjs render --project <项目绝对路径> --modules <Sharp所在node_modules>\n```\n\n只改文字时复用已确认底图，先修改content/adapter，再重建layout和渲染受影响页，重新检查实际PNG。确认记录和最终QA保存于campaign-state/review。可改文字SVG依赖字体，转曲SVG不依赖字体；标题艺术字已经是路径，文本仍保存于layout。所有背景为位图。\n');
console.log(JSON.stringify({businessChecks:checks.length,layoutChecks:audit.groupsChecked,finalFiles:hashFiles.length,visualQA:true,readyForPackaging:true}));
