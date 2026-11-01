import fs from 'node:fs/promises';
import path from 'node:path';
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const memory=String.raw`C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库`;
const verification=JSON.parse(await fs.readFile(path.join(project,'review/package-verification-r1.json'),'utf8'));
if(!verification.passed||verification.checks.length!==24)throw new Error('包未核验');
const marker='## 2026-10-03 · 环太平洋六张矢量排版成品交付 r1';
const note=`\n\n${marker}\n\n- 用户“继续”确认四类无字底图，完成端游绝密、端游机密、手游绝密、手游机密、四版体验价广告、横版封面六张。新蜂医与得吃灰鼠探险插画、四类背景均已获用户确认；当前六图已验收打包，不再是插画或底图待确认状态。\n- 最新5点绝密价目为基准：手游绝密15售价各减200r、15保底各减400w；端游机密同手游绝密售价，手游机密再减20r。用户明确两版机密保底沿用原绝密。四版均2/3/4/5/全点，机密巴克什/航天基地/核电站及现有规则，绝密潮汐监狱。\n- 预言家加购58r与奖励、任意门三件及160w/80w以上、低于788/688w及488/388w流局门槛、成功可抵扣所选点、失败清空重新累计均保留；已成功解锁复活点复活后失败超过6次条款仍为6次。规则双端按同模式相同，绝密监狱直升机限制保留，机密不增加该限制。\n- 价目各3地图5档，连续海军蓝表头，暖金/薄荷区分模式，价格/保底固定r/w列同baseline。普通规则常规字重、原生26px，在390px宽等效14.01px，关键条件选择性真粗体。广告仅体验价/保底与体验2点条件；封面全部左侧净区居中，不放须知或结算例外。\n- 已逐张打开六张实际PNG和390px手机图，以及四版价格/规则局部检查；修正广告窄标签、封面标题与平台条边距，未扩大测量净区或容差。248展示映射、381组边界/居中、120数字单位检查无失败；60售价/60保底按独立基准核对，同模式规则局部双端像素一致。自动结果仅辅助实际成图复查。\n- 四张价目3200×9596/9600，广告3000×4000，封面3200×1802；另有可改正文文字SVG和转曲SVG。底图原生724×2171/2172、1086×1448、1671×941；独立透明插画1536×1024。背景为位图，非全矢量，放大输出不代表原生绘制分辨率。\n- 成品位置：C:/Users/chenh/Documents/ChatGPT/兴安电竞玩法/环太平洋-四版价目广告封面-20261003。查看review/index.html，高清包“环太平洋-六张海报-PNG-r1.zip”，完整源稿包campaign-r1.zip。QA记录review/visual-qa.md、layout-and-data-checks-r1.json、business-source-checks-r1.json；包内6PNG/12SVG及PNG包6图与最后验收文件哈希均一致。大型源文件仍仅在项目目录。\n- 原因：完成用户本轮六图重做及确认的价差/保底口径；影响仅普通环太平洋四版价目、规则展示与本轮宣传物料，不外推独立任务版或其他玩法，未外部发布。\n`;
for(const rel of ['01_玩法/环太平洋.md','00_入口/当前共识.md','05_变更日志/变更日志.md']){
 const file=path.join(memory,rel);let text=await fs.readFile(file,'utf8');
 if(rel==='01_玩法/环太平洋.md'){
  const oldLine='- 2026-10-03本轮四版重做项目：`环太平洋-四版价目广告封面-20261003/`。售价和保底按文末“环太平洋四版新价目”执行；探险插画已确认，机密统一5点并沿用现有地图与规则；当前四类无字底图待确认，尚未完成六张成品。下方旧双版段落保留历史来源。';
  const newLine='- 2026-10-03本轮四版重做项目：`环太平洋-四版价目广告封面-20261003/`，六张最终成品r1已完成、复查及打包。售价和保底按文末四版新价目表执行；豪华统一5点，机密两版沿用原绝密保底及现有机密地图/规则。最新查看review/index.html和本项目PNG/源稿包；下方旧双版与阶段段落保留历史来源。';
  if(text.includes(oldLine))text=text.replace(oldLine,newLine);else if(!text.includes(newLine))throw new Error('当前版本头部发生变化，请人工核对');
 }
 if(!text.includes(marker))text+=note;
 await fs.writeFile(file,text);
}
const decision=path.join(memory,'04_决策记录/2026-10-03-环太平洋四版价差与机密原绝密保底.md');
let d=await fs.readFile(decision,'utf8');d=d.replace('status: confirmed-pricing-background-pending','status: confirmed-delivered');d=d.replace('四张价目、广告、封面，蜂医灰鼠探险新情景目前待确认。','四张价目、广告、封面，蜂医灰鼠探险新情景与四类底图均已确认，六图r1已完成。');d=d.replace('- [ ] 底图确认与最终六图矢量排版复检。','- [x] 底图确认与最终六图矢量排版复检。\n- [x] PNG/SVG源稿导出、实际成图检查、成品包哈希验证。');
if(!d.includes(marker))d+=note;await fs.writeFile(decision,d);
console.log(JSON.stringify({updated:['环太平洋玩法卡','当前共识','变更日志','四版价差决策'],deliveryVerified:true}));
