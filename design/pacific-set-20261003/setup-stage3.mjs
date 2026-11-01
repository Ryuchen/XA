import fs from 'node:fs/promises';
import path from 'node:path';
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const skill=String.raw`C:\Users\chenh\.codex\skills\xingan-gameplay-posters\scripts`;
const taskDir=path.dirname(new URL(import.meta.url).pathname.replace(/^\//,''));
const statePath=path.join(project,'campaign-state.json'),contentPath=path.join(project,'content.json');
const s=JSON.parse(await fs.readFile(statePath,'utf8')),c=JSON.parse(await fs.readFile(contentPath,'utf8'));
if(!s.contentVerified)throw new Error('业务未确认');
for(const a of s.artwork.backgrounds)a.approved=true;
if(!s.approvalHistory.some(a=>a.stage==='backgrounds'))s.approvalHistory.push({date:'2026-10-03',stage:'backgrounds',userReply:'继续',basis:'用户在四类无字底图确认页展示后明确继续，确认绝密r2、机密r2、广告r2、封面r1进入矢量填字',assets:s.artwork.backgrounds.map(a=>a.path)});
s.pendingApprovals=[];s.backgroundApprovalBasis=s.approvalHistory.at(-1).basis;s.stage='vector-typesetting';s.revision='r1';
c.commonRules={doorItemCount:3,failureLimit:6,failureComparison:'超过',failurePrerequisite:'在已成功解锁的复活点复活后',failureEffect:'清除该复活点的撤离成功次数',basis:'本轮附件及已确认沿用的原绝密/机密规则；不是新增门槛'};
c.copy={
 columns:['档位','售价','撤离目标','基础保底'],addonsTitle:'附加玩法',prophetTitle:'预言家',prophetIntro:'每局开始前可加购（2选1），猜中复活点1次：',
 prophetConditions:['成功或失败均加','成功撤离时才加'],baseGuarantee:'基础保底',
 doorTitle:'任意门',doorPrefix:'累计摸出',
 doorLines:[[{text:'打手局前可选本局复活点，无论实际复活在哪，'}],[{text:'都按所选点计算。'},{text:'成功撤离可抵扣所选点；',bold:true}],[{text:'撤离失败则清空，重新累计。',bold:true}]],
 noticeTitle:'下单须知',failureEffect:'清除该复活点的撤离成功次数。',unitLegend:'r=元 / w=万',
 adExperience:'体验档 · 2个不同出生点',adScope:'五档套餐 · 三张地图',
 coverBenefitLines:['累计不同出生点','成功撤离'],coverPlatforms:'端游 / 手游',coverModes:'绝密 / 机密'
};
c.presentationBasis={targets:'表中省略“不同”，上方目标条完整显示累计从不同出生点成功撤离；规范目标仍保留content.targets。',door:'局前等义于对局开始前；所选点和实际点的计入、成功可抵扣、失败清空重累计全部保留。',notices:'绝密监狱直升机限制保留；机密没有该限制。只算成功、低于门槛流局、已成功解锁点复活后失败超过6次清除次数均完整保留。',ad:'四版体验档在三地图价格和保底分别一致，因此广告显示体验档起步价与保底，并明确2个不同出生点和五档三地图；不把全部档位说成同价。',cover:'只显示品牌、玩法、累计不同出生点成功撤离卖点、两平台两模式，无须知或例外。'};
c.social.scope='四版体验档起步价与基础保底、累计不同出生点撤离；明确体验档2点和三地图五档，具体地图与完整档位见四張价目；不放下单须知';
await fs.writeFile(contentPath,JSON.stringify(c,null,2));await fs.writeFile(statePath,JSON.stringify(s,null,2));
await fs.copyFile(path.join(taskDir,'layout-adapter.mjs'),path.join(project,'layout-adapter.mjs'));
for(const name of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','pack.ps1'])await fs.copyFile(path.join(skill,name),path.join(project,'source',name));
await fs.copyFile(new URL(import.meta.url),path.join(project,'source/setup-stage3.mjs'));
const {makeLayout}=await import('./layout-adapter.mjs');
const plan=makeLayout(c);
await fs.writeFile(path.join(project,'layout.json'),JSON.stringify(plan,null,2));
console.log(JSON.stringify({stage:s.stage,approvedBackgrounds:s.artwork.backgrounds.length,pages:plan.pages.map(p=>({id:p.id,size:[p.ew,p.eh],groups:p.auditGroups.length})),strings:plan.requests.length},null,2));
