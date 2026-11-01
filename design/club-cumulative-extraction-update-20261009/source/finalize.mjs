import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const manifest=await read('output/manifest.json'),state=await read('campaign-state.json'),audit=await read('review/data-and-geometry-audit.json');
if(audit.failures.length)throw Error('Resolve audit failures before finalizing');
const old='端游新价格已同步，保底沿用原表。',replacement='端游新价格已同步；保底按同价常规物资单线性折算后取七折，保留百位及更高位并将后两位改为88。';
for(const f of ['source/review.mjs','review/index.html'])await fs.writeFile(path.join(root,f),(await fs.readFile(path.join(root,f),'utf8')).replace(old,replacement));
const sha256=data=>createHash('sha256').update(data).digest('hex');
const records=[];
for(const p of manifest)records.push({id:p.id,path:p.png,sha256:sha256(await fs.readFile(path.join(root,p.png)))});
await fs.writeFile(path.join(root,'output/final-files.json'),JSON.stringify({revision:'cumulative-r2',pngs:records},null,2));
const qa=[
 '# 累计撤离单 · cumulative-r2 · 2026-10-09',
 '',
 '本轮7页：四张完整价目、端游/手游各一张广告页、横版封面。业务数据只取content.json。',
 '',
 '## 数据核对',
 '',
 '- 手游新价格取本轮截图J列、保底取E列。绝密巴克什/监狱共用三档；手游保底保持截图数值。',
 '- 端游绝密航天328/488/648，巴克什与监狱288/388/488；机密核电站188/288/388，巴克什248/348/448，航天268/368/468。',
 '- 端游保底引用references/regular-material-price-source.json对应模式的基础档位；无同价档先线性插值，乘0.7后保留百位及更高位、后两位改88。算法已得到用户明确答复。详细18档计算记录见desktop-guarantee-calculations.json。',
 '- 两张广告各18档均与对应完整价目一致。36档源数据、72处成图价目映射已复核。',
 '- 标题、列名、结单条件全部采用累计撤离。有效撤离保持成功撤离、陪玩击杀≥4人，且绝密带出≥600w/机密≥300w；低于门槛局不计累计、物资归板板。',
 '- 删除失败重计卡及也不打断连撤文案。房卡、随行装备、补偿、结算、物资累计、速撤和撤离点上浮规则沿用。',
 '',
 '## 实际PNG目检与修复',
 '',
 '| 区域 | 证据 | 结论 |',
 '| --- | --- | --- |',
 '| 封面 | review/cover.png、final-overview.png | 品牌/主标题左对齐；胶囊/平台行在左文字区居中；艺术字与人物有间距，无下单须知。 |',
 '| 四张价目与保底 | phone-390四页、details四页prices裁图、总览 | 新价和保底映射正确；行上下留白，三/四位数字不挤压固定r/w列；累计2/3/4把对应明确。 |',
 '| 有效撤离与删除区 | 实际desktop-absolute.png、四页手机预览、details validity裁图 | 条件保持且关系；删除失败说明的SVG裁窗在空白间隙衔接，下单须知整体上移96原生像素，无残留说明。 |',
 '| 缺项补偿 | details/mobile-secret-notes.png、端游/手游手机预览 | 初次右侧w比文字安全区超出0.16px，右侧物品/补偿整组左移7px；复查右边安全余量6.84px，数字与单位共用baseline。 |',
 '| 所有须知与页尾 | 四页实际手机预览、两端两模式notes裁图 | 普通说明常规字重，分类和金额选择性加粗；各组上下居中，图标/文字和分隔线间有留白。 |',
 '| 两张广告 | review/social-mobile.png、phone-390/social-desktop.png、final-overview.png | 每页18档、累计撤离列和结单条件同步，无完整下单须知，图文无挤压。 |',
 '',
 '## 几何辅助核对',
 '',
 '- data-and-geometry-audit.json未发现失败项；文字与单位baseline差小于0.000001px，价目行最小上下余量约3.99原生像素。',
 '- 规则整组中心最大偏差约0.065原生像素；缺项补偿组保持实际文字安全区。自动结果用于辅助，上述实际PNG已逐页查看。',
 '- 普通须知沿用本套用户确认的字号，手机390px预览有效字号约12–13px；本轮不以放大导出替代字号检查。',
 '',
 '最终PNG为矢量文字直接按目标尺寸渲染。插画仍为原已确认位图，原生尺寸见manifest；SVG裁窗没有重新绘制或提高背景原生分辨率。',
 '',
 '## 后两位88修订',
 '',
 '用户纠正888为笔误，最终只把后两位改为88。端游两张价目和端游广告已重新渲染，检查实际最终PNG的两种模式价格局部、端游广告和手机预览。端游绝密巴克什/监狱保底988/1388/1888，航天1188/1888/2488；机密核电站888/1488/1888，巴克什1288/1688/2188，航天1388/1788/2288。手游保底未改。最终版本cumulative-r2。'
 ].join('\n');
await fs.writeFile(path.join(root,'review/visual-qa.md'),qa);
await fs.writeFile(path.join(root,'README.md'),[
 '# 兴安电竞 · 累计撤离单',
 '',
 '版本cumulative-r2，2026-10-09。7张：封面、两端广告页、四张价格表。查看review/index.html。',
 '',
 '唯一业务数据源：content.json。手游新价格与保底取用户截图，端游保底按已确认算法计算；参考和计算明细见references及review/desktop-guarantee-calculations.json。',
 '',
 '## 后续修改',
 '',
 '1. 修改content.json中的对应价格、保底、规则。',
 '2. 使用Node.js运行source/typeset.mjs；Sharp可由--modules=<包目录>提供。',
 '3. 运行source/review.mjs，打开实际PNG和phone-390、details做视觉复检。',
 '4. 完成目检后更新review/visual-qa.md，再运行source/finalize.mjs。',
 '',
 'source/prepare.mjs仅为首次项目创建记录，已加防覆盖门禁；后续不要用它重置项目。source/update-desktop-guarantees.mjs按项目内的常规物资单快照重算端游保底，并检查不改手游。',
 '',
 '可改文字SVG保留正文和数字文字对象，艺术字为路径；字体为Microsoft YaHei UI、Bahnschrift、Noto Sans SC Black。转曲SVG不依赖字体。插画为内嵌位图。',
 '',
 '完整价目版面724×2075/2076（从原背景删除96px说明区），PNG3200×9171/9176；封面3200×1800；广告2400×3200。背景原生724×2171/2172、1672×941、1086×1448，未重新生成。'
 ].join('\n'));
state.revision='cumulative-r2';state.stage='delivered';
state.visualQA={status:'complete',revision:'cumulative-r2',record:'review/visual-qa.md',dataAndGeometryAudit:'review/data-and-geometry-audit.json',basis:'实际最终PNG、7页手机预览和价目/须知局部已目检；修复缺项补偿右侧安全间距后复查。'};
state.finalDeliverables={manifest:'output/manifest.json',completePackage:'campaign-cumulative-r2.zip',pngPackage:'output/campaign-cumulative-r2-png.zip',review:'review/index.html',overview:'review/final-overview.png'};
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
await fs.writeFile(path.join(root,'output/DELIVERY.md'),[
 '# 累计撤离单 · 成品',
 '',
 '已更新7张：封面、两端广告页、四张价格表；累计撤离规则、有效撤离要求及最新价格/保底已同步。',
 '端游保底采用已确认的线性折算×70%，保留百位及更高位且后两位改88；手游保底保持截图数值。',
 '',
 ...manifest.map(p=>'- '+p.label+'：'+p.width+'×'+p.height+'；背景原生'+p.nativeWidth+'×'+p.nativeHeight+'。'),
 '',
 '提供PNG、转曲SVG、可改文字SVG、业务源数据和检查记录。插画为内嵌位图，文字为矢量。'
 ].join('\n'));
console.log(execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(root,'source/pack.ps1'),'-Project',root,'-Revision','cumulative-r2'],{encoding:'utf8'}).trim());
const command=[
 "Add-Type -AssemblyName System.IO.Compression",
 "Add-Type -AssemblyName System.IO.Compression.FileSystem",
 "$taskManifest=Get-Content -LiteralPath '"+path.join(root,'output/manifest.json').replaceAll("'","''")+"' -Raw -Encoding UTF8 | ConvertFrom-Json",
 "$taskRoot='"+root.replaceAll("'","''")+"'",
 "$taskPath=Join-Path $taskRoot 'output/campaign-cumulative-r2-png.zip'",
 "$taskStream=[System.IO.File]::Open($taskPath,[System.IO.FileMode]::Create)",
 "$taskZip=[System.IO.Compression.ZipArchive]::new($taskStream,[System.IO.Compression.ZipArchiveMode]::Create)",
 "try {foreach($taskPage in $taskManifest){[System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip,(Join-Path $taskRoot $taskPage.png),([System.IO.Path]::GetFileName($taskPage.png)),[System.IO.Compression.CompressionLevel]::Optimal) | Out-Null}}finally{$taskZip.Dispose();$taskStream.Dispose()}",
 "Write-Output $taskPath"
].join('; ');
console.log(execFileSync('powershell.exe',['-NoProfile','-Command',command],{encoding:'utf8'}).trim());
console.log(JSON.stringify(records.map(r=>({id:r.id,sha256:r.sha256}))));
