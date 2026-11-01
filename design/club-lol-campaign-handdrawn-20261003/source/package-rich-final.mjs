import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const root=path.resolve(import.meta.dirname,'..'),JSZip=createRequire(import.meta.url)(process.argv[2]+'/jszip');
const read=f=>fs.readFileSync(root+'/'+f),write=(f,b)=>{fs.mkdirSync(path.dirname(root+'/'+f),{recursive:true});fs.writeFileSync(root+'/'+f,b);};
const digest=b=>createHash('sha256').update(b).digest('hex');
const c=JSON.parse(read('content.json')),m=JSON.parse(read('output/manifest.json')),e=JSON.parse(read('review/final/export-checks.json')),checks=JSON.parse(read('review/final/layout-checks.json')),layout=JSON.parse(read('layout.json')),state=JSON.parse(read('campaign-state.json'));
const contentHash=digest(read('content.json'));
if(contentHash!==JSON.parse(read('review/before-r2/content-sha256.json')).contentSHA256)throw Error('Business source changed');
if(m.length!==30||e.length!==30||checks.length!==30||checks.some(x=>x.failures.length))throw Error('Unresolved export checks');
if(m.map(x=>x.id).join('|')!==state.deliveryScope.pageIds.join('|'))throw Error('Scope mismatch');
for(const p of m){if(digest(read(p.png))!==e.find(x=>x.id===p.id).hash)throw Error('PNG changed after QA');const q=layout.pages.find(q=>q.id===p.id);if(q.ew!==p.width||q.eh!==p.height)throw Error('Layout/export mismatch');}
const visible=layout.pages.flatMap(p=>p.elements).filter(x=>x.type!=='markup').map(x=>x.text||x.runs?.map(r=>r.text).join('')).join('');
if(/[—–]/.test(visible))throw Error('Visible prohibited dash');
const findings={
 '01-solo-duo':'任一项结算用四个并列条件签；资质双行；金额整体居中。',
 '02-flex':'匹配、胜率匹配、组排对应固定r列；档位和资质分级。',
 '03-arena':'娱乐至顶尖价格卡放大；双男名次与达成频次保留；例外分组。',
 '04-boost':'新秀与招牌并列价格；补分条件与30%/60%成组；套餐金额从源表读取。',
 '05-lp':'T1/T2按分价目；未列价明确；补分条件与费用独立展示。',
 '06-overseas':'区服与单位清楚；补分三档条件与30%/60%/100%对应；韩服除外保留。',
 '07-day':'8小时与净胜成组；死亡条件与尽力局/SVP之间的且单独显示；活动限制保留。',
 '08-haidou':'男女档价目与r列对应；单陪/双陪结算前提、三陪/四陪赔付区分。',
 '09-placement':'88折与同区可双排双号一起下单的条件独立对应；补净胜或退款的或清楚显示。',
 '10-maintenance':'两种7折前提分别展示；28局价格与保障双行；例外与区服浮动仍可读。'
};
let qa=read('review/visual-qa.md').toString().split('\n\n## 文案展示增强版 final-r3')[0];
qa+='\n\n## 文案展示增强版 final-r3 · 2026-10-03\n\n结论：本轮10套30张实际PNG、390px手机预览及代表性原尺寸局部检查通过，可以交付。无未解决重叠、容器越界或图文挤压。自动检查与图片目视检查均完成，自动结果不替代视觉结论。\n\n### 两个技能的使用顺序\n\n先按design-taste-frontend的保留品牌重设计流程审视既有页面，记录设计解读、6/1/4（完整价目密度5）配置和信息层级，制作review/presentation-design-r3.html展示稿；随后按xingan-gameplay-posters改成真实字体测量的矢量海报。\n\n浏览器工具不允许访问file协议，展示稿已保存，但未宣称完成浏览器实机截图验收；最终验版采用实际导出的PNG和手机预览。没有用绕过策略的浏览器或间接执行方式。\n\n### 展示改动\n\n- 主价格字号从76增至92布局像素，金额与r共用baseline，可见单位高约72%，清楚间隙8；浅模式色只强调价格和权益。表格仍保持整条深蓝表头与固定r列，不制造假原价或推荐档。\n- 资质标题与普通说明分级；代练广告套餐改为新秀/招牌对应金额两行，金额来自content.json的套餐表。\n- 定位赛88折、维护7折成为主权益，所需条件保持同卡清晰可读；移除旧票签中穿过文字的虚线。\n- 规则按关键条件与支撑说明分组。任一项采用并列标签；且/或采用连接词明确的关系展示；补分条件与百分比成组。图示全部取自已确认文案，不添加新的承诺。\n- 封面在左侧实际净文字区居中，主要卖点加模式色底签；定位优惠由条件、下单动作和88折构成三层。定位卖点区使用原底图中476至686的空白区域，平台底板从694开始；按真实空白重分区后复查。封面不展示规则与赔付例外。\n- 说明46px、卡片普通正文44px，390px长图预览等效15.0px/14.3px；真实Bold只用于标题、金额、权益及关键条件。\n\n### 检查证据与复查\n\n9张检查板由output/png中的本轮真实PNG生成，覆盖30页；review/final/*-390.png为实际成图手机预览。原尺寸局部覆盖条件签、包天且关系、退款或关系、补分比例、优惠与最长金额。\n\n修正并复查：外服ADC除外、指定物理的断词；封面三行卖点放大后的上下边界；优惠票签穿字虚线。确认标题和副标题左轴、数字与r共baseline、价格整组居中、多行正文左边界、字体混排间距、分隔线距离、首末行与页尾留白通过。人物轮廓和主要道具不被遮挡。\n\ncontent.json的SHA-256与改版前完全一致：'+contentHash+'。范围、价格、单位、比较符、且/或、结算条件及例外保持原确认含义。\n\n### 逐页结论\n\n| 页ID | 检查结论 |\n| --- | --- |\n';
for(const p of m){const set=c.sets.find(s=>p.id.startsWith(s.id)),type=p.id.split('-').at(-1);qa+=`| ${p.id} | ${type==='cover'?'品牌、艺术标题、卖点与平台均按左侧净文字区居中；无规则；与人物、底板间距通过。':findings[set.id]+' 字形边界、手机可读性与整组留白通过。'} |\n`;}
qa+='\n### 格式与打包\n\n30张PNG尺寸与最终布局一致，30张可编辑SVG保留正文文字对象，30张转曲SVG无text对象。PNG SHA-256见review/final/export-checks.json；打包使用最后检查过的文件，不补渲染。ZIP CRC32、文件数量与包内PNG SHA-256再次核对。\n';
write('review/visual-qa.md',qa);
let dims='| 栏目 | 完整价目 | 广告 | 封面 |\n| --- | --- | --- | --- |\n';
for(const s of c.sets){const pp=['price','advert','cover'].map(t=>m.find(x=>x.id===s.id+'-'+t));dims+=`| ${s.name} | ${pp[0].width}×${pp[0].height} | ${pp[1].width}×${pp[1].height} | ${pp[2].width}×${pp[2].height} |\n`;}
write('README.md',`# 兴安电竞 · 英雄联盟海报\n\n文案展示增强版final-r3，2026-10-03。十套，每套完整价目页、广告页与封面，共30张。角色为EZ（伊泽瑞尔）与拉克丝。\n\n打开review/index-r3.html查看全部海报；review/presentation-design-r3.html保存前端展示设计稿。设计稿与最终海报分别展示两个阶段的工作。\n\n主价格突出，资质分级；折扣与适用条件独立展示；结算的任一项、且/或、补分条件以图示表达。业务内容和确认价格未改。\n\n## 文件\n\n- output/png：30张高清PNG。\n- output/svg-editable：30张正文和数字可改文字SVG，艺术标题已转曲。\n- output/svg-outlined：30张所有文字转曲SVG。\n- content.json：唯一业务数据源；layout.json为本轮矢量布局。\n- review/visual-qa.md：实际PNG、390px与原尺寸局部检查记录。\n\n正文使用Microsoft YaHei UI，金额使用Bahnschrift；艺术标题为Noto Sans SC Black路径。PNG标记300dpi，发布以实际像素为准：\n\n${dims}\n## 美术尺寸与依据\n\n人物原图各1536×1024；峡谷与斗魂场景1672×941；页尾2172×724。高清文字从矢量渲染，人物与场景为内嵌手绘位图；成图不是全矢量，最终像素不代表插画原生绘制尺寸。\n\n飞书资料：https://ccnyms208cox.feishu.cn/wiki/Zm3awBtAsiPkVhkfUdFcggZDnLb，沿用用户2026-10-03确认的更正、结算与活动口径。\n`);
state.stage='delivered';state.revision='final-r3';state.nextStep='十套30张展示增强版已验收并打包。';
state.visualQA={stage:'final',status:'passed',revision:'final-r3',report:'review/visual-qa.md',checks:'review/final/export-checks.json',phonePreviewWidth:390,pageCount:30,actualPNGInspection:true,contentSHA256:contentHash};
state.delivery={revision:'final-r3',sets:10,pages:30,PNG:30,editableSVG:30,outlinedSVG:30,viewer:'review/index-r3.html',designPrototype:'review/presentation-design-r3.html',package:'delivery/兴安电竞-英雄联盟-10套30张-完整文件-final-r3.zip',pngPackage:'delivery/兴安电竞-英雄联盟-10套30张-PNG-final-r3.zip'};
write('campaign-state.json',JSON.stringify(state,null,2));
const full=new JSZip(),pngs=new JSZip();
const add=(z,f)=>z.file(f,read(f),{compression:f.endsWith('.png')?'STORE':'DEFLATE'});
for(const p of m){add(full,p.png);add(full,p.editableSVG);add(full,p.outlinedSVG);add(full,p.preview);add(pngs,p.png);}
for(const f of ['README.md','content.json','layout.json','layout-final-plan.json','campaign-state.json','output/manifest.json','review/index.html','review/index-r3.html','review/visual-qa.md','review/final/export-checks.json','review/final/layout-checks.json','review/presentation-design-r3.md','review/presentation-design-r3.html','review/presentation-comparison-r3.png','assets/environment/rift-scene-r1.png','assets/environment/arena-scene-r1.png','assets/environment/footer-scene-r1.png','assets/illustrations/01-ez-lux-duo.png','assets/illustrations/02-ez-lux-victory.png'])add(full,f);
add(pngs,'README.md');
const fullbuf=await full.generateAsync({type:'nodebuffer',compression:'DEFLATE',compressionOptions:{level:6}});write(state.delivery.package,fullbuf);console.log('Full package written.');
const pngbuf=await pngs.generateAsync({type:'nodebuffer'});write(state.delivery.pngPackage,pngbuf);
const reopened=await JSZip.loadAsync(fullbuf,{checkCRC32:true}),reopenedPNG=await JSZip.loadAsync(pngbuf,{checkCRC32:true});
const files=Object.keys(reopened.files).filter(f=>!reopened.files[f].dir);
for(const folder of ['output/png/','output/svg-editable/','output/svg-outlined/'])if(files.filter(f=>f.startsWith(folder)).length!==30)throw Error('Archive count mismatch');
for(const p of m){const bytes=await reopened.file(p.png).async('nodebuffer'),pb=await reopenedPNG.file(p.png).async('nodebuffer');if(digest(bytes)!==e.find(v=>v.id===p.id).hash||digest(pb)!==digest(bytes))throw Error('Packaged PNG mismatch');}
write('delivery/package-checks-r3.json',JSON.stringify({revision:'final-r3',PNG:30,editableSVG:30,outlinedSVG:30,CRC32:'passed',packagePNGHashes:'passed',contentSHA256:contentHash,fullBytes:fullbuf.length,pngBytes:pngbuf.length,fullSHA256:digest(fullbuf),pngSHA256:digest(pngbuf)},null,2));
console.log(JSON.stringify({revision:'final-r3',fullMB:Math.round(fullbuf.length/1048576),pngMB:Math.round(pngbuf.length/1048576),verifiedPages:30,sourceUnchanged:true}));
