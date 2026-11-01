import fs from 'node:fs/promises';
import path from 'node:path';
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const memory=String.raw`C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库`;
const verified=JSON.parse(await fs.readFile(path.join(project,'review/package-verification-r2.json'),'utf8'));
if(!verified.passed||verified.checks.length!==24)throw new Error('r2包未验证');
const marker='## 2026-10-03 · 环太平洋手游两版指定售价修订 r2';
const note=`\n\n${marker}\n\n- 用户修订手游机密航天基地、核电站5点豪华售价为968r、868r；巴克什、航天基地、核电站全点为1668r、1868r、1568r。用户已明确答复“是，修改豪华版·全点”，后三项不是5点档。\n- 用户追加手游绝密豪华版·全点：巴克什2488r、航天基地2688r、潮汐监狱2888r。合计八处，替代r1对应旧价。\n- 手游其余22档售价及30档保底、目标/地图/规则保留；机密仍原绝密保底，手游绝密仍原绝密保底-400w。r1全档-200/-220r仅作初始依据，新指定售价覆盖优先，后续不能自动套旧公式重置八处新价。\n- 复用已确认美术，仅渲染手游机密、手游绝密两图；实际3200×9600/9596 PNG、390px预览、六地图价格与规则局部逐项复查通过。数字/单位基线、固定r列、最短最长金额和行间留白正常。两张航天全点行因数字字形整行共用基线微调0.02637原生单位，保持居中，无可见异常；改价行外像素变化为0。两张端游、广告及封面12个PNG/SVG哈希与r1一致。\n- r2成品仍六张整套，包内6PNG/12SVG及PNG包6图与最后验收文件哈希均一致。最新版项目：C:/Users/chenh/Documents/ChatGPT/兴安电竞玩法/环太平洋-四版价目广告封面-20261003；review/index.html，PNG包“环太平洋-六张海报-PNG-r2.zip”、源稿campaign-r2.zip，旧r1包保留。\n- 原因：落实用户本轮两条指定改价及全点档含义确认；影响仅环太平洋普通玩法手游两版八处价格、对应两图和当前六图下载包，不修改端游、附加费用/奖励或其他玩法，未外部发布。大型文件只在项目，记录review/visual-qa.md、business-source-checks-r2.json及package-verification-r2.json。\n`;
const cardPath=path.join(memory,'01_玩法/环太平洋.md');let card=await fs.readFile(cardPath,'utf8');
if(!card.includes(marker)){
 card=card.replace('六张最终成品r1已完成、复查及打包。售价和保底按文末四版新价目表执行；','最新六图r2已完成、复查及打包，本轮只修订手游两图八处售价；r1保留历史。售价和保底按文末最新四版价目表及r2修订执行；');
 const updates=[
 ['| 手游机密 | 巴克什 | 168 / 368 / 568 / 968 / 2668 |','| 手游机密 | 巴克什 | 168 / 368 / 568 / 968 / 1668 |'],
 ['| 手游机密 | 航天基地 | 168 / 368 / 668 / 1168 / 3068 |','| 手游机密 | 航天基地 | 168 / 368 / 668 / 968 / 1868 |'],
 ['| 手游机密 | 核电站 | 168 / 368 / 668 / 1368 / 3268 |','| 手游机密 | 核电站 | 168 / 368 / 668 / 868 / 1568 |'],
 ['| 手游绝密 | 巴克什 | 188 / 388 / 588 / 988 / 2688 |','| 手游绝密 | 巴克什 | 188 / 388 / 588 / 988 / 2488 |'],
 ['| 手游绝密 | 航天基地 | 188 / 388 / 688 / 1188 / 3088 |','| 手游绝密 | 航天基地 | 188 / 388 / 688 / 1188 / 2688 |'],
 ['| 手游绝密 | 潮汐监狱 | 188 / 388 / 688 / 1388 / 3288 |','| 手游绝密 | 潮汐监狱 | 188 / 388 / 688 / 1388 / 2888 |']
 ];
 for(const [from,to]of updates){if(!card.includes(from))throw new Error('玩法表旧价不符');card=card.replace(from,to);}card+=note;await fs.writeFile(cardPath,card);
}
for(const rel of ['00_入口/当前共识.md','05_变更日志/变更日志.md','04_决策记录/2026-10-03-环太平洋四版价差与机密原绝密保底.md']){
 const file=path.join(memory,rel);let text=await fs.readFile(file,'utf8');if(!text.includes(marker)){text+=note;await fs.writeFile(file,text);}
}
console.log(JSON.stringify({memoryUpdated:true,currentPriceRows:6,revision:'r2'}));
