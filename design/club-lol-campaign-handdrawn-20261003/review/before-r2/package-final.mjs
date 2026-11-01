import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const root=path.resolve(import.meta.dirname,'..');
const require=createRequire(import.meta.url),JSZip=require(process.argv[2]+'/jszip');
const read=f=>fs.readFileSync(root+'/'+f),write=(f,b)=>{fs.mkdirSync(path.dirname(root+'/'+f),{recursive:true});fs.writeFileSync(root+'/'+f,b);};
const c=JSON.parse(read('content.json')),m=JSON.parse(read('output/manifest.json')),e=JSON.parse(read('review/final/export-checks.json'));
const state=JSON.parse(read('campaign-state.json'));
const checks=JSON.parse(read('review/final/layout-checks.json'));
if(m.length!==30||checks.some(p=>p.failures.length)||e.length!==30)throw Error('Incomplete or failed exports');
for(const p of m)if(createHash('sha256').update(read(p.png)).digest('hex')!==e.find(x=>x.id===p.id).hash)throw Error('PNG changed after inspection: '+p.id);
let qa=read('review/visual-qa.md').toString().split('\n\n## 最终成图 final-r1')[0];
qa+='\n\n## 最终成图 final-r1 · 2026-10-03\n\n结论：10套、30页逐张完成实际PNG整图、390px预览与重点局部检查，可以交付。此结论结合图片目视检查与真实字形边界核验，不以自动检查代替视觉验收。\n\n### 来源及范围\n\n- 仅英雄联盟端游，十栏目各完整价目页、广告页、封面。没有手游价格或历史物资数字。\n- 黑铁40r/段、海外铂金4→翡翠4的588r/大段、0–200分6r/分、30%补分、800–1000维护区间、50%/70%胜率和一区加价均按用户答复。\n- 完整页保留结算任一项、包天联合前提“死亡≥12且尽力局/SVP”、英雄选用率≥70%、补分阈值、赔付与优惠例外。封面无注意事项；广告无下单须知。\n\n### 检查图及修正\n\n- 实际最终PNG：`output/png/`；390px预览：`review/final/*-390.png`。5张价目检查板、2张广告检查板、2张封面检查板全部由这些实际PNG生成。\n- 局部：`01-solo-duo-price-crop-numeric.png`、`10-maintenance-price-crop-numeric.png`检查最长范围金额/四位数与r正间隙、同基线；`05-lp-price-crop-rules.png`、`06-overseas-price-crop-rules.png`检查粗/常规混排与行距；`09-placement-cover-crop-title-art.png`检查人物与标题、各左侧组居中。局部位于`review/final/`。\n- 修正：句号孤行、资质中“韩服王者/峡谷前20”拆行、补分加价孤行、代练AP/物理规则拆词；组排广告固定档卡增高；外服与定位广告双行标题重新测量；代练广告套餐改为新秀/招牌各一行；包天广告缩短等义条件，避免末字孤行。\n- 原尺寸检查：数值与r可见间距8布局像素，二者共baseline；卡片标题/价格/说明完整组上下留白，表格按真实分隔行中心排字。须知体46px、卡片说明44px，对应390px宽长图预览约15.0px/14.3px。普通规则常规字重，核心条件与标题真Bold700。\n- 文字安全区始终避开EZ、拉克丝及主要道具。标题使用Noto Sans SC Black字形路径、奶白描边、模式色错位阴影与轻微字符起伏。人物与环境统一手绘。\n- 因正式正文量调整长图面板高度与须知留白，背景r2只复用已确认人物、场景及面板构图；未重新生成美术。\n\n### 逐页结论\n\n| 页面 | 实际PNG / 手机预览 | 版式与内容检查 |\n| --- | --- | --- |\n';
for(const p of m){const type=p.id.split('-').at(-1);qa+=`| ${p.id} · ${p.label} | ${p.png} / ${p.preview} | ${type==='cover'?'品牌、标题、卖点与平台组按左侧安全区居中；无规则；图文间距通过':type==='advert'?'核心价目、单位和必要条件明确；首末卡行居中；无下单须知':'价目对应、规则前提及例外完整；字重分层、行距、容器边界、页尾留白通过'} |\n`;}
qa+='\n### 格式与打包一致性\n\n30张PNG尺寸逐一核对；30张转曲SVG不含文字对象，30张可改文字SVG保留正文/数值text对象。范围与campaign-state相符，页面没有未解决文字重叠或越界。PNG SHA-256保存于`review/final/export-checks.json`，打包前再次校验，打包不重新渲染。\n';
write('review/visual-qa.md',qa);
let dims='| 栏目 | 完整价目PNG | 广告PNG | 封面PNG |\n| --- | --- | --- | --- |\n';
for(const s of c.sets){const pages=['price','advert','cover'].map(t=>m.find(p=>p.id===s.id+'-'+t));dims+=`| ${s.index}. ${s.name} | ${pages[0].width}×${pages[0].height} | ${pages[1].width}×${pages[1].height} | ${pages[2].width}×${pages[2].height} |\n`;}
const readme=`# 兴安电竞 · 英雄联盟海报\n\n版本final-r1，2026-10-03。十套，每套完整价目页＋广告页＋封面，共30张。人物按要求为EZ（伊泽瑞尔）与拉克丝。\n\n打开 'review/index.html' 查看全部成图与各格式下载链接。\n\n## 文件\n\n- 'output/png'：30张高清成图，可直接使用。\n- 'output/svg-editable'：30张正文及金额可编辑SVG；艺术标题已转曲。\n- 'output/svg-outlined'：30张所有文字转曲SVG，不依赖字体。\n- 'content.json'：本轮唯一业务数据源，包含原始转录、确认更正、最终展示数据。\n- 'layout.json'：本轮文字、容器及真实字体请求；'review/visual-qa.md'记录实际成图检查。\n\n编辑文字时使用Microsoft YaHei UI与Bahnschrift。标题Noto Sans SC Black已变为路径。PNG为300dpi标记，实际像素如下；发布时以像素尺寸为准。\n\n${dims}\n## 美术原始尺寸\n\n独立人物插画两幅均为1536×1024；峡谷场景与斗魂场景为1672×941；页尾环境为2172×724。完整底图按布局画布合成后用于高清导出。正文与标题从矢量按目标像素渲染；人物与环境是嵌入位图。最终3200宽/2400宽成图并不代表插画在该尺寸原生绘制，整张海报不称全矢量。\n\n## 依据\n\n飞书《兴安电竞-英雄联盟价位表》：${c.source?.url||'https://ccnyms208cox.feishu.cn/wiki/Zm3awBtAsiPkVhkfUdFcggZDnLb'}；黑铁、外服、胜率、维护区间、一区加价与结算口径采用用户2026-10-03明确答复。独立插画与无字底图分别经用户“继续”确认后进入矢量排版。\n\n完整价目保留必要规则，广告为核心价目提炼，封面为玩法与卖点。源数据保留全部条件，不因封面精简删除业务规则。\n`;
write('README.md',readme);
state.stage='delivered';state.revision='final-r1';state.nextStep='全部30张已完成、检查并打包；后续改价或文案仅修改受影响页面。';
state.approvals.finalCopy={status:'completed',date:'2026-10-03',basis:'业务疑点均经用户确认；最终等义精简与分组对照来源完成。'};
state.visualQA={stage:'final',status:'passed',revision:'final-r1',report:'review/visual-qa.md',checks:'review/final/export-checks.json',phonePreviewWidth:390,pageCount:30,actualPNGInspection:true};
state.delivery={revision:'final-r1',sets:10,pages:30,PNG:30,editableSVG:30,outlinedSVG:30,viewer:'review/index.html',package:'delivery/兴安电竞-英雄联盟-10套30张-完整文件-final-r1.zip',pngPackage:'delivery/兴安电竞-英雄联盟-10套30张-PNG-final-r1.zip'};
write('campaign-state.json',JSON.stringify(state,null,2));
const full=new JSZip(),pngs=new JSZip();
const add=(z,f)=>z.file(f,read(f),{compression:f.endsWith('.png')?'STORE':'DEFLATE'});
for(const p of m){add(full,p.png);add(full,p.editableSVG);add(full,p.outlinedSVG);add(full,p.preview);add(pngs,p.png);}
const other=['README.md','content.json','layout.json','layout-final-plan.json','campaign-state.json','output/manifest.json','review/index.html','review/visual-qa.md','review/final/export-checks.json','review/final/layout-checks.json'];
for(const f of other)add(full,f);add(pngs,'README.md');
const fullbuf=await full.generateAsync({type:'nodebuffer',compression:'DEFLATE',compressionOptions:{level:6}});write(state.delivery.package,fullbuf);
const pngbuf=await pngs.generateAsync({type:'nodebuffer'});write(state.delivery.pngPackage,pngbuf);
const reopened=await JSZip.loadAsync(fullbuf,{checkCRC32:true});
const files=Object.keys(reopened.files).filter(f=>!reopened.files[f].dir);
for(const folder of ['output/png/','output/svg-editable/','output/svg-outlined/'])if(files.filter(f=>f.startsWith(folder)).length!==30)throw Error('Archive count mismatch: '+folder);
write('delivery/package-checks.json',JSON.stringify({revision:'final-r1',PNG:30,editableSVG:30,outlinedSVG:30,CRC32:'passed',fullBytes:fullbuf.length,pngBytes:pngbuf.length,fullSHA256:createHash('sha256').update(fullbuf).digest('hex'),pngSHA256:createHash('sha256').update(pngbuf).digest('hex')},null,2));
console.log(JSON.stringify({full:state.delivery.package,fullMB:Math.round(fullbuf.length/1048576),png:state.delivery.pngPackage,pngMB:Math.round(pngbuf.length/1048576),verifiedPages:30}));
