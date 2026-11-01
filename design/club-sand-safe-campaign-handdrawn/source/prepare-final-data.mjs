import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const [project,modules]=process.argv.slice(2);
const sharp=createRequire(path.join(modules,'..','sand-safe-final-loader.cjs'))('sharp');
const contentFile=path.join(project,'content.json');
const c=JSON.parse(await fs.readFile(contentFile,'utf8'));
c.display={
  platformMode:'绝密 · 端游 / 手游',
  priceColumnLabels:['完成目标','订单价格'],
  rewardHeading:'特别奖励',
  wishWhen:'下单时许愿（二选一）',
  wishTrigger:'如果出了，赠送',
  wishGuaranteeLabel:'保底',
  objectiveHeading:'玩法说明',
  objectiveRelation:'同一局成功撤离，三项同时达成',
  lootObjectivePrefix:'单局带出物资超过',
  noticeHeading:'下单须知',
  noticeCompletion:[[{text:'成功撤离',bold:true},{text:'，且'},{text:'三项目标同局完成，',bold:true}],[{text:'才算完成目标。'}]],
  noticeBrokenOrderPrefix:'成功撤离且带出物资低于',
  noticeBrokenOrderSuffix:'，算炸单；',
  noticeBrokenOrderResult:[{text:'中断连续计数，',bold:true},{text:'不增加保底。',bold:true}],
  noticeSafeInteraction:[[{text:'战斗激烈时，陪玩会'},{text:'询问板板，',bold:true}],[{text:'是否可以由陪玩摸沙色及常规大保险。'}]],
  noticeExtraGuarantee:[{text:'额外赠送保底：陪玩自选'},{text:'绝密地图',bold:true},{text:'补打。'}]
};
c.decoration={requestedByUser:true,objects:['沙色保险','万金泪冠','纵横','天圆地方'],purpose:'仅装饰；许愿对象仍为万金泪冠与纵横二选一',references:'references/decor-references.md'};
await fs.writeFile(contentFile,JSON.stringify(c,null,2)+'\n');
const measurementFile=path.join(project,'02-layouts/generated-background-measurements.json');
const m=JSON.parse(await fs.readFile(measurementFile,'utf8'));
const native=await sharp(path.join(project,'03-backgrounds/poster-gold-v2.png')).metadata();
if(native.width!==836||native.height!==1881)throw Error('New background dimensions require a fresh measurement.');
m.version='decor-v2';
m.basis='人工查看实际decor-v2底图后更新安全区；新增珠宝盒/棋盘缩小奖励文字区，新增沙色保险缩小玩法标题区；下单须知底边约1670，最后一项使用单行。';
m.reward.copy={x:60,y:327,width:528,height:133};
m.reward.reservedAccent={x:610,y:310,width:177,height:168};
m.objectives.title={x:176,y:894,width:598,height:74};
m.objectives.rows[2]={x:76,y:1151,width:686,height:67};
m.notices.container={x:36,y:1262,width:764,height:408};
m.notices.groups[3]={x:64,y:1626,width:710,height:35};
m.decorationBounds={reward:{x:610,y:310,width:177,height:168},objectiveSafe:{x:28,y:894,width:136,height:86},footer:{x:25,y:1704,width:810,height:175}};
m.reviewStatus='remeasured-for-final-typesetting';
await fs.writeFile(path.join(project,'02-layouts/final-background-measurements.json'),JSON.stringify(m,null,2)+'\n');
const stateFile=path.join(project,'campaign-state.json');
const s=JSON.parse(await fs.readFile(stateFile,'utf8'));
s.stage='final-typesetting-in-progress';
s.background={...s.background,source:'C:/Users/chenh/.codex/generated_images/01a0ebcb-df5a-79a3-8de8-18f6993ec3d9/exec-74235d45-2efc-4cf7-a605-58a4869c6c61.png',file:'03-backgrounds/poster-gold-v2.png',measurements:'02-layouts/final-background-measurements.json',prompt:'03-backgrounds/decor-edit-prompt-v2.md',native:{width:native.width,height:native.height}};
s.typography={font:'Microsoft YaHei UI',regularBody:true,selectiveBold:true,bodySize:31,plannedPhoneEquivalentPx:31*390/836,numberFont:'Bahnschrift',titleFont:'Noto Sans SC Black'};
await fs.writeFile(stateFile,JSON.stringify(s,null,2)+'\n');
console.log(JSON.stringify({native:[native.width,native.height],rewardCopy:m.reward.copy,objectiveHeading:m.objectives.title,noticeLastRow:m.notices.groups[3]}));
