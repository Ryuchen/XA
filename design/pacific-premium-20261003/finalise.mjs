import fs from 'node:fs/promises';
import path from 'node:path';
const root='C:/Users/chenh/Documents/ChatGPT/兴安电竞玩法/环太平洋-五出生点豪华降价-20261003';
const state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.stage='delivered';state.finalVisualQa={status:'passed',date:'2026-10-03',report:'review/visual-qa.md',basis:'已查看实际完整PNG、390px手机预览、三地图价目与规则原尺寸局部；15档价格、目标、保底和全部附加玩法及下单须知逐項目视核对。'};
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
await fs.writeFile(path.join(root,'review/visual-qa.md'),`# 环太平洋附件修订 · 成图检查

2026-10-03；状态：通过。使用内置ImageGen按source/imagegen-prompt.txt局部改字。

实际检查文件：环太平洋-5出生点豪华降价.png、review/mobile390.png、review/tables-detail.png、review/rules-detail.png。

三处豪华档均为5个不同出生点：巴克什1188r/4888w、航天基地1388r/5688w、潮汐监狱1588r/6288w。对应价格各降100，保底正确。三处全点档保持2888/3288/3488r、8888w及全部出生点，其余12档价目正确。

预言家+58r、+100w/+250w；任意门三个大大红/房卡160w以上；航天基地/巴克什788w、潮汐监狱688w；监狱直升机限制；撤离失败超过6次清除该点成功次数，逐项目视核对一致。其中超过6次是失败计数，仍保留6次。

三张表的改变文字在各自单元格内横纵居中，价格与同行r贴合既有排版。原尺寸局部清楚，分隔线完整，无明显文字碰撞或修补色块。整图及390px预览整体平衡和角色结构保持，文字密度沿用原稿。

源图809×1942，模型返回809×1944，成品保留模型原生输出。插画和文字均为位图，没有可改文字SVG，不称全矢量或原生高清放大稿。生成式局部编辑可能存在细微图像变化，本记录为目视内容核验，不宣称修改区外逐像素相等。

本轮只交付一张用户附件修订；对应绝密普通趣味玩法价目。未推定修改机密或独立任务版。原图与旧项目保留。
`);
const cardPath='C:/Users/chenh/Documents/Obsidian Vault/兴安电竞玩法设计库/01_玩法/环太平洋.md';
let card=await fs.readFile(cardPath,'utf8');
const backup=path.join(root,'references/环太平洋-玩法卡修改前.md');
try{await fs.access(backup);}catch{await fs.writeFile(backup,card);}
if(!card.includes('## 2026-10-03 · 附件三地图豪华5点降价')){
const split=card.indexOf('## 机密版');
if(split<0)throw Error('Missing mode boundary');
let absolute=card.slice(0,split);
for(const [from,to]of [
['豪华版 | 1288r | 6个不同出生点 | 4888w','豪华版 | 1188r | 5个不同出生点 | 4888w'],
['豪华版 | 1488r | 6个不同出生点 | 5688w','豪华版 | 1388r | 5个不同出生点 | 5688w'],
['豪华版 | 1688r | 6个不同出生点 | 6288w','豪华版 | 1588r | 5个不同出生点 | 6288w']
]){if(!absolute.includes(from))throw Error('Missing old absolute row');absolute=absolute.replace(from,to);}
card=absolute+card.slice(split);
card=card.replace('date: 2026-09-19','date: 2026-10-03');
card=card.replace('- 当前物料：项目目录 `环太平洋-机密任意门80w修订-20260919/`。PNG、用户原图、完整文案、双版数据和生成提示词保存在项目，Obsidian仅保存规则与索引。','- 当前绝密三地图附件修订物料：`环太平洋-五出生点豪华降价-20261003/`，豪华为5点且各降100r；机密仍在`环太平洋-机密任意门80w修订-20260919/`。PNG、原图、业务数据和生成提示词保存在各自项目，Obsidian保存规则与索引。');
card=card.replace('- 机密版15个套餐售价均较对应绝密档位减100r；基础保底、不同出生点数量及全部出生点目标不变。','- 2026-09-19原机密价目由当时绝密15档各减100r得到。2026-10-03只改绝密附件的三处豪华为5点且各降100r；机密豪华仍为6点、1188/1388/1588r，不把旧价差规则套到这次修订。');
card+=`\n\n## 2026-10-03 · 附件三地图豪华5点降价\n\n- 用户要求所有6个不同出生点改为5个，对应售价各降100。针对本轮附图，巴克什1288→1188r、航天基地1488→1388r、潮汐监狱1688→1588r；三处基础保底4888/5688/6288w保持。\n- 三处全点豪华和其余12档、预言家、任意门、下单须知保持；超过6次失败条款仍为6次。仅本轮附图，不推定修改机密或独立任务版。\n- 内置ImageGen局部改字；实际809×1944 PNG、390px预览与价格/规则局部已逐项目视核对。位图成品，无虚构矢量文字层。\n- [最新PNG](<${root}/环太平洋-5出生点豪华降价.png>)；[查看页](<${root}/review/index.html>)；提示词source/imagegen-prompt.txt，检查review/visual-qa.md。\n- 原因：落实用户明确修订；影响对应绝密趣味图、当前价目表和后续客服口径。原稿和历史图保留，未外部发布。\n`;
await fs.writeFile(cardPath,card);
}
const logPath='C:/Users/chenh/Documents/Obsidian Vault/兴安电竞玩法设计库/05_变更日志/变更日志.md';
let log=await fs.readFile(logPath,'utf8');
if(!log.includes('## 2026-10-03 · 环太平洋附件豪华改5点并降100')){
log+=`\n\n## 2026-10-03 · 环太平洋附件豪华改5点并降100\n\n- 用户本轮一张三地图趣味玩法图：三处豪华6点→5点，巴克什1288→1188r、航天基地1488→1388r、潮汐监狱1688→1588r。基础保底4888/5688/6288w保持。\n- 影响仅对应绝密普通玩法附件及当前报价，机密和独立任务版不推定改变。全点档、其他价目和失败超过6次规则沿用。\n- 内置ImageGen局部文字编辑，809×1944；原图、成品、结构化数据、提示词和目视检查在项目“环太平洋-五出生点豪华降价-20261003/”。已检查实际PNG、390px预览与三表/规则局部，未外部发布。\n`;
await fs.writeFile(logPath,log);
}
console.log(JSON.stringify({root,status:'passed',memory:'updated'}));
