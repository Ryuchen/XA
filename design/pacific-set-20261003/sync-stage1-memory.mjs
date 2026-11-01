import fs from 'node:fs/promises';
import path from 'node:path';
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const vault=String.raw`C:\Users\chenh\Documents\Obsidian Vault\兴安电竞玩法设计库`;
const c=JSON.parse(await fs.readFile(path.join(project,'content.json'),'utf8'));
const cardPath=path.join(vault,'01_玩法/环太平洋.md');
let card=await fs.readFile(cardPath,'utf8');
await fs.writeFile(path.join(project,'references/本轮重做前玩法卡.md'),card);
const key='## 2026-10-03 · 环太平洋四版新价目与探险插画待确认';
if(!card.includes(key)){
 const newSource='- 2026-10-03本轮四版重做项目：`环太平洋-四版价目广告封面-20261003/`。售价和保底按文末“环太平洋四版新价目”执行；目前第一阶段新插画待确认，机密目标与地图/规则沿用口径待答复，尚未完成六张成品。下方旧双版段落保留历史来源。\n';
 card=card.replace('## 当前版本与来源\n\n','## 当前版本与来源\n\n'+newSource);
 const rows=['',key,'','- 范围：端游绝密、手游绝密、端游机密、手游机密、广告、封面，共六张。价格基准是上一轮已修订豪华5点和豪华降100r的最新绝密图。','- 用户本轮价差：手游绝密各售价减200r、每档基础保底减400w；端游机密同手游绝密售价；手游机密再减20r。','- 用户单独明确：机密两版保底沿用原绝密，不随手游绝密减400w。套餐价差不改预言家加购58r、其奖励或流局门槛。','- 已核对四版60售价及60保底；下面价目为本轮已确认新价，替代旧双版报价。','','| 版本 | 地图 | 体验/基础/进阶/豪华/全点售价（r） | 同序基础保底（w） |','|---|---|---|---|'];
 for(const t of c.tiers)for(const m of t.maps)rows.push(`| ${t.platform}${t.mode} | ${m.name} | ${m.prices.join(' / ')} | ${m.guarantees.join(' / ')} |`);
 rows.push('','- 绝密目标仍2/3/4/5/全点。待确认：机密两版是否也统一5点；是否仍为巴克什/航天/核电站并沿用现有机密附加玩法与须知。未将未答复内容记为本轮已确认。','- 美术：蜂医与得吃灰鼠一起查看探索地图，灰鼠持红卡指向任意门，配医疗无人机；统一二次元手绘奶白、雾蓝、海军蓝及金/薄荷点缀。内置ImageGen独立透明插画1536×1024，alpha已验证，整图和390px预览已查看；新情景待用户确认。','- 阶段：`illustration-awaiting-confirmation`。尚未制作新底图或六张最终海报；不将旧位图成品充作本轮新成品。确认插画后制作无字底图，底图确认后矢量填字。','- 项目保存content.json、campaign-state.json、60项价目核对、原资料、提示词、独立插画及review/stage1.html；大型素材不复制入Obsidian。','- 影响：环太平洋普通趣味玩法的四版报价/保底及本轮六图；不外推到独立任务版或其他玩法，不外部发布。');
 await fs.writeFile(cardPath,card+'\n'+rows.join('\n')+'\n');
}
const event=`\n\n${key}\n\n- 本轮明确四版一套六张。基于最新豪华5点绝密价目：手游绝密售价-200r/保底-400w，端游机密同手游绝密售价，手游机密再-20r；用户答复机密两版保底沿用原绝密。60售价和60保底已核对并记录content.json。\n- 新蜂医灰鼠探险插画1536×1024透明PNG已生成和核查，待用户确认；机密目标/地图/既有规则口径待答复。尚未生成底图或最终六图，业务未核实部分不进入正式排字。\n- 原因：落实用户指定技能重做及新两端差价；影响仅环太平洋普通玩法四版价目与六图制作，独立任务版保持。\n- 项目：${project}；查看review/stage1.html，检查review/illustration-qa-r1.md，数字review/四版价目核对.md。\n`;
for(const file of ['00_入口/当前共识.md','05_变更日志/变更日志.md']){
 const p=path.join(vault,file);const text=await fs.readFile(p,'utf8');
 if(!text.includes(key))await fs.writeFile(p,text+event);
}
const decision=path.join(vault,'04_决策记录/2026-10-03-环太平洋四版价差与机密原绝密保底.md');
await fs.writeFile(decision,`---\ntype: decision\nproject: 兴安电竞玩法\nstatus: confirmed-pricing-visual-pending\ndate: 2026-10-03\ntags: [设计决策, 环太平洋, 端游, 手游]\n---\n# 决策：环太平洋四版价差与机密原绝密保底\n\n## 背景\n用户要求依兴安电竞海报技能重做一套图，并提供双端绝密/机密价格映射。\n\n## 选择\n最新5点绝密作为端游绝密基准。手游绝密每档售价-200r、保底-400w；端游机密售价同手游绝密；手游机密再-20r。机密两版保底沿用原绝密，为用户单独答复。六页范围明确。\n\n## 备选方案\n- 机密沿用原绝密保底：用户选择。\n- 机密与手游绝密一同减400w：未采用。\n\n## 理由\n“同价”只确认售价，不推定保底；已向用户具体核对并得到原绝密保底口径。\n\n## 影响范围\n- 玩法：普通环太平洋趣味玩法，独立任务版不变。\n- 定价/权益：四版60售价/60保底，数据保存在项目content.json。\n- 视觉/物料：四张价目、广告、封面，蜂医灰鼠探险新情景目前待确认。\n- 客服口径：确认的新价差与保底；机密目标、地图和附加规则仍待答复。\n\n## 后续验证\n- [x] 四版价差和保底映射核对。\n- [x] 独立插画透明alpha、角色与390px预览核查。\n- [ ] 用户确认新插画。\n- [ ] 用户确认机密目标与地图/规则。\n- [ ] 底图确认与最终六图矢量排版复检。\n`);
console.log('已同步本轮已确认价差、机密保底和待确认插画阶段；未记录最终交付。');
