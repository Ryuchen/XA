import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
const root=process.argv[2];
if(!root||!path.isAbsolute(root))throw Error('Absolute project path required');
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const write=async(f,t)=>fs.writeFile(path.join(root,f),t);
const state=await read('campaign-state.json'),qa=await read('review/automated-qa.json'),manifest=await read('output/manifest.json');
if(qa.revision!=='r5'||qa.status!=='passed')throw Error('r5 verification required');
const hashes={};
for(const p of manifest)hashes[p.png]=createHash('sha256').update(await fs.readFile(path.join(root,p.png))).digest('hex');
await write('review/final-png-hashes.json',JSON.stringify(hashes,null,2));
state.revision='r5';state.stage='verified-ready-to-package';
state.finalVisualQa={status:'passed',revision:'r5',date:'2026-10-03',report:'review/visual-qa.md',automated:'review/automated-qa.json',basis:'四张实际最终PNG的390px整图及绝密/机密价格和扣血局部已查看，失败加血与六档扣血正确，字形、间距、图文关系和整体平衡通过；315项数据、边界、baseline及导出检查通过。'};
state.finalOverview='review/final-overview-r5.png';state.package='campaign-r5.zip';state.pngPackage='成品PNG-r5.zip';
await write('campaign-state.json',JSON.stringify(state,null,2));
await write('review/visual-qa.md',`# 成图复检 · r5

日期：2026-10-03。状态：通过。

## 数值与来源

用户指定体验/基础/进阶/豪华撤离失败加血为+1/+1/+1/+2滴。25/21/17/13/9/5分钟扣血各下调1滴，由6/7/8/9/10/12改为5/6/7/8/9/11。两端绝密/机密共四张完整价目同步。

## 实际成图检查

- 已打开四张review/*-mobile390.png，这些均从本轮实际PNG生成。档位对应的失败加血为+1/+1/+1/+2，六档时间和扣血分别为25→5、21→6、17→7、13→8、9→9、5→11。
- 已查看desktop-secret、desktop-confidential的prices-detail和deductions-detail局部；金额与r保持同行基线和间隙，失败加血完整，血量列与末行底边有留白。
- 时间、扣血整组位于所属卡片内并保持居中；绝密秒表和无人机与9滴/11滴的字形之间有清楚间隔，机密六卡一致。字号与单/双位数字之间的关系正常。
- 四张整图显示标题、价目、扣血、清图判定和八条规则的顺序与层级清楚。末两条时限和黑屋失败流局条件保留完整，规则区无相交或贴边。
- social和cover没有本轮变更的业务字段，直接沿用r4通过成图。没有重新生成美术。

## 程序核对与交付

${qa.total}项检查通过，包括四张失败加血/扣血来源、面板边界、整组垂直中心、字体baseline、单位间隙、输出尺寸和300DPI；两端同难度规则区域像素一致。程序核对作为上述实际图片复检的补充。

四张完整价目重新导出；PNG、可改文字SVG、转曲SVG、content.json、完整文案和Obsidian当前玩法表已同步。最终PNG哈希见review/final-png-hashes.json。旧campaign-r4.zip和成品PNG-r4.zip保留，修改前核心源文件位于archive/r4-before-r5。

最终范围为六张：四张完整价目、广告、封面。背景内嵌位图，文字矢量；沿用原尺寸，不将放大背景描述为原生高清绘制。
`);
await write('交付说明.md',`# 清图版小小巨人 · r5

2026-10-03已完成失败加血1/1/1/2及限时扣血5/6/7/8/9/11调整，四张完整价目同步。

- 查看页：review/index.html
- 高清图：成品PNG-r5.zip
- PNG、可改文字SVG、转曲SVG及业务与布局源稿：campaign-r5.zip
- 本轮检查：review/visual-qa.md；315项数据和布局检查通过，四张390px实际成图及改动区已复查。

两种压缩包均包含四张更新的价目、沿用的广告与封面，共六张。旧r4包和修改前源文件备份保留。
`);
await write('续作说明.md',`# 清图版小小巨人 · 续作入口

当前版本r5，2026-10-03。失败加血1/1/1/2；25/21/17/13/9/5分钟扣血5/6/7/8/9/11。

content.json为唯一业务数据源；layout-adapter.mjs为布局入口；campaign-state.json记录已确认范围和本轮变更。修改内容后使用source/workflow.mjs layout、render，运行source/verify-final.mjs并打开实际PNG和手机预览，再保存复检记录和package。本轮finalise-r5.mjs仅用于r5交付记录，未来版本应按实际内容更新。

查看review/index.html；最新版包成品PNG-r5.zip、campaign-r5.zip。archive/r4-before-r5和旧r4包保留历史。
`);
let adapter=await fs.readFile(path.join(root,'layout-adapter.mjs'),'utf8');
adapter=adapter.replace("revision:'r4'","revision:'r5'");
await write('layout-adapter.mjs',adapter);
const plan=await read('layout.json');plan.revision='r5';await write('layout.json',JSON.stringify(plan,null,2));
const packSource=await fs.readFile(path.join(root,'source/pack.ps1'),'utf8');
await write('source/pack.ps1','\uFEFF'+packSource.replace(/^\uFEFF/,''));
const cardPath='C:/Users/chenh/Documents/Obsidian Vault/兴安电竞玩法设计库/01_玩法/清图版小小巨人.md';
let card=await fs.readFile(cardPath,'utf8');
card=card.replace('date: 2026-09-30','date: 2026-10-03').replace('日期：2026-09-30。品牌：','日期：2026-10-03。品牌：').replace('- 用户确认：保留5分钟档，改扣12滴。','- 2026-10-03用户追加：失败加血1/1/1/2；六档扣血各减1，5分钟档为11滴。');
if(!card.includes('r5成图检查完成'))card+=`\n\n## 2026-10-03 · r5成图检查完成\n\n- 四张完整价目已更新并查看实际390px整图及价格/扣血局部，315项来源、面板边界、整组居中和baseline核对通过。\n- 当前包：成品PNG-r5.zip、campaign-r5.zip；查看页review/index.html。旧r4保留。\n`;
await fs.writeFile(cardPath,card);
const own=await fs.readFile(import.meta.filename,'utf8');
await write('source/finalise-r5.mjs',own);
console.log(JSON.stringify({revision:'r5',checks:qa.total,pages:manifest.length,visualQA:'passed'}));
