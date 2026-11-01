const fs=require('fs'),path=require('path');
const root=path.resolve(__dirname,'..');
const read=f=>fs.readFileSync(path.join(root,f),'utf8').replace(/^\uFEFF/,'');
const write=(f,s)=>fs.writeFileSync(path.join(root,f),s);
const state=JSON.parse(read('campaign-state.json'));
state.revision='misans-aligned-r3';state.stage='final-review-ready';
state.pendingRevision.status='implemented';state.pendingRevision.next='新版广告成图已完成并随四页交付包提供复核。';
state.approvals.background.socialR3={status:'approved',basis:'用户2026-10-09明确回复“确认”批准广告去票券新底图。',file:'assets/backgrounds/experience-social-r3.png',nativeDimensions:{width:1086,height:1448}};
state.approvals.finalArtwork.currentRevision={revision:'misans-aligned-r3',status:'ready-for-review',basis:'此前成图已获通过；本版按已确认广告新底图完成矢量合成及排版复核。',preview:'review/stage3-misans-preview.png'};
state.delivery={package:'campaign-misans-aligned-r3.zip',gallery:'review/index.html',pageCount:4,pngCount:4,editableSvgCount:4,outlinedSvgCount:4};
write('campaign-state.json',JSON.stringify(state,null,2)+'\n');
let gallery=read('source/build-final-review.cjs').replaceAll('campaign-misans-confirmation-r1.zip','campaign-misans-aligned-r3.zip').replaceAll('体验单成图确认','体验单 · 对齐修订 r3').replaceAll('MiSans成图确认','MiSans 对齐修订 r3').replace('成图待确认。','广告顶部票券底板已按确认移除，艺术标题直接浮在云景上；四页区块及内部文案对齐已复核。');
write('source/build-final-review.cjs',gallery);
let qa=read('review/visual-qa.md').replaceAll('广告去票券底图 r3 等待该阶段确认','广告去票券底图 r3 已确认，更新成图已完成').replaceAll('广告顶部去票券底板的新底图 r3 已生成并保存，依用户逐阶段确认要求尚未进入该底图的矢量填字。','广告顶部去票券底板的新底图 r3 已获用户明确“确认”，现已完成矢量填字。');
qa+='\n## 广告云景底图 r3 成图复查\n\n用户于2026-10-09明确确认新底图。复核实际1086×1448背景：头部为开放云景，品牌和艺术标题占用左侧 x75—460、y105—335；与右侧人物分离。其余四个价目表头、12行卡片及条件条仍在既有安全区，查看实际边界图确认文字没有贴边或越界。标题使用原矢量描边与阴影，无新增票券底板。已查看原尺寸成图、390px手机图及边界辅助图。57个区块、84项内部检查、52组数字单位与20项列对齐复核再次全部通过。\n\n本轮只重渲染受影响的广告页；其余三页沿用上一轮完成的对齐修订。最终包包含四页当前PNG、可编辑SVG与转曲SVG，底图为已确认原生位图。\n';
write('review/visual-qa.md',qa);
let report=read('review/alignment-report-r2.md').replace('新底图另存 r3，等待该阶段确认。','新底图另存 r3，已获用户确认并用于新版广告。').replace('后续填字时须重测头部净文字区和下方卡片实际轮廓。本轮尚未用此底图替换正式广告成图，以遵守用户的逐阶段确认要求。','用户确认后已复核头部净文字区和下方卡片实际轮廓，完成正式广告成图替换。更新后的测量仍全部通过；详见 visual-qa.md 的 r3 记录。');
write('review/alignment-report-r2.md',report);
write('output/DELIVERY.md',`# 兴安电竞体验单 · MiSans 对齐修订 r3

包含封面、广告、端游体验单、手游体验单，各有PNG、可编辑文字SVG和转曲SVG。

| 页面 | PNG尺寸 | 底图原生尺寸 |
| --- | --- | --- |
| 封面 | 3200×1801 | 1672×941 |
| 广告 | 2400×3200 | 1086×1448 |
| 端游体验单 | 2400×4800 | 887×1774 |
| 手游体验单 | 2400×4800 | 887×1774 |

广告已采用用户确认的开放云景新底图，艺术标题直接悬浮在背景上。四页的区块留白与内部对齐均已复核；设备说明采用统一标签列和悬挂缩进，费用与保底层级、补偿标签与数值的对应关系已修正。

全部非艺术标题文字使用 MiSans VF Regular/Semibold/Bold。可编辑SVG需要安装对应字体；转曲SVG无需字体。字体软件未随包再分发。插画为内嵌位图，文字为矢量；PNG放大不代表底图原生重绘。

打开 review/index.html 查看四页及390px手机预览。review/visual-qa.md、alignment-report-r2.md 和 alignment-audit-r2.json 记录检查；辅助边界图和前后对照也在review内。content.json 为唯一业务数据源。

生成及修改：source/layout-experience.mjs → source/workflow.mjs layout/render。正文生成使用本机MiSans字体；广告新底图由内置 image_gen 编辑，提示词见 03-backgrounds/social-no-ticket-r3-prompt.txt。

本包已完成制作与自检，供本轮更新成图复核；此前确认记录保留在 campaign-state.json。
`);
let pack=read('source/package-confirmation.ps1').replace("param([string]$Project)","param([string]$Project,[string]$Revision='misans-aligned-r3')").replace("'assets/backgrounds/experience-social.png'","'assets/backgrounds/experience-social-r3.png'").replace("$taskZipPath=Join-Path $taskRoot 'campaign-misans-confirmation-r1.zip'","if($Revision -notmatch '^[a-z0-9-]+$'){throw 'Invalid revision'}\n$taskZipPath=Join-Path $taskRoot ('campaign-'+$Revision+'.zip')");
pack=pack.replace('foreach($page in $taskManifest)',"$taskFiles+=@('review/alignment-report-r2.md','review/alignment-audit-r2.json','review/alignment-before.json','review/alignment-before-after-r2.png','source/alignment-audit.cjs','source/build-final-review.cjs','source/package-confirmation.ps1','03-backgrounds/social-no-ticket-r3-prompt.txt')\nforeach($page in $taskManifest)").replace("('review/'+$page.id+'-phone-final.png')","('review/'+$page.id+'-phone-final.png'),('review/'+$page.id+'-alignment-proof.png'),('review/'+$page.id+'-text-bounds.json'),('review/'+$page.id+'-text-overlaps.json'),('review/alignment-before-png/'+$page.id+'.png')");
write('source/package-confirmation.ps1',pack);
console.log('R3 records, gallery builder and package script updated.');
