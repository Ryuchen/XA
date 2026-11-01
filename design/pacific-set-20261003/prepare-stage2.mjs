import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const sharp=require(String.raw`C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp`);
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const statePath=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(statePath,'utf8'));
const contentPath=path.join(project,'content.json');
const content=JSON.parse(await fs.readFile(contentPath,'utf8'));
const targets=structuredClone(content.absoluteTargets);
content.confidentialTargets=targets;
for(const t of content.tiers)if(t.mode==='机密')t.targets=structuredClone(targets);
content.confirmations.confidentialTargetsAndRules=true;
content.pendingQuestions=[];
content.sources.push({type:'user-reply',date:'2026-10-03',basis:'用户明确答复：统一5点，沿用现有机密地图与规则；机密仍巴克什、航天基地、核电站及既有附加玩法/须知'});
state.artwork.approved=true;
state.approvalHistory.push({date:'2026-10-03',stage:'illustration',asset:state.artwork.illustration,userReply:'继续',basis:'用户在第一阶段新插画确认请求后明确继续，确认本轮探险插画进入底图阶段'});
state.pendingApprovals=[];
state.pendingBusinessQuestions=[];
state.contentVerified=true;
state.contentVerificationBasis='最新豪华5出生点绝密价目作为端游绝密基准；本轮三组价差与手游绝密-400w及机密原绝密保底已明确；用户另答复机密统一5点并沿用现有地图与规则。四版60售价及60保底映射核对通过。';
state.revision='stage2-r1';state.stage='background-production';
const hero=await fs.readFile(path.join(project,state.artwork.illustration));
const heroURL='data:image/png;base64,'+hero.toString('base64');
const navy='#23465b',ivory='#fbf8ee',blue='#d4e9f2';
const zones=[];
const rect=(x,y,w,h,fill,stroke='none',radius=18,sw=2)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const line=(x1,y1,x2,y2,color,sw=1)=>`<path d="M${x1} ${y1}H${x2}" fill="none" stroke="${color}" stroke-width="${sw}"/>`;
function svg(w,h,body){return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${rect(0,0,w,h,ivory,'none',0)}${body}</svg>`;}
async function save(id,w,h,markup,pageZones){
 await fs.writeFile(path.join(project,'source',id+'-layout-guide.svg'),markup);
 await sharp(Buffer.from(markup)).png().toFile(path.join(project,'source',id+'-layout-guide.png'));
 zones.push({id,w,h,zones:pageZones});
}
for(const mode of ['gold','teal']){
 const accent=mode==='gold'?'#d5a367':'#70b6b0';
 const tint=mode==='gold'?'#fcf0d9':'#e6f4ef';
 let body=`<path d="M0 282Q280 322 560 290T1000 300V333H0Z" fill="${blue}"/><image href="${heroURL}" x="492" y="24" width="482" height="321"/>`;
 body+=rect(62,265,362,52,navy,'none',12);
 body+=rect(44,352,912,77,tint,accent,28);
 const z=[{id:'header-copy',safe:[52,40,424,247],exclude:[492,24,974,345]},{id:'mode-tag',safe:[80,275,407,306]},{id:'objective',safe:[75,371,925,409]}];
 [455,880,1305].forEach((y,i)=>{
  body+=rect(44,y,912,400,'#fffdf7',accent,20);
  body+=rect(62,y+12,85,45,tint,'none',10);
  body+=rect(44,y+66,912,49,navy,'none',0);
  for(let row=0;row<5;row++){
   if(row===4)body+=rect(46,y+115+row*57,908,57,tint,'none',0);
   if(row>0)body+=line(65,y+115+row*57,935,y+115+row*57,'#dbe7e8',1.2);
   z.push({id:`map-${i+1}-row-${row+1}`,safe:[66,y+120+row*57,934,y+166+row*57],columns:[[66,256],[275,439],[459,682],[704,931]],centerY:y+143+row*57});
  }
  z.push({id:`map-${i+1}-title`,safe:[163,y+14,923,y+54],exclude:[61,y+9,148,y+60]},{id:`map-${i+1}-table-header`,safe:[66,y+76,934,y+105]});
 });
 body+=rect(44,1785,912,280,'#fffdf7',accent,20);
 body+=rect(65,1804,264,50,tint,'none',14)+rect(666,1804,261,50,tint,'none',14);
 body+=line(74,1920,925,1920,'#dbe7e8',1.2);
 body+=rect(44,2090,912,370,'#fffdf7',accent,20);
 body+=rect(65,2111,264,49,tint,'none',14);
 body+=rect(44,2485,912,410,'#fffdf7',accent,20);
 body+=rect(65,2506,280,48,tint,'none',14);
 z.push({id:'addons-section-title',safe:[68,1730,931,1773]},
  {id:'prophet-title',safe:[79,1815,314,1845]},{id:'prophet-fee',safe:[681,1815,911,1845]},
  {id:'prophet-body',safe:[75,1866,925,2044]},
  {id:'door-title',safe:[79,2120,314,2150]},{id:'door-body',safe:[75,2177,925,2437]},
  {id:'notice-title',safe:[80,2514,329,2545]},{id:'notice-body',safe:[75,2568,925,2871]},
  {id:'footer',safe:[125,2923,875,2965]});
 await save('poster-'+mode,1000,3000,svg(1000,3000,body),z);
}
{
 let body=`<path d="M0 310Q310 360 680 310T1086 323V353H0Z" fill="${blue}"/><image href="${heroURL}" x="537" y="37" width="517" height="345"/>`;
 body+=rect(45,360,996,114,'#eaf4f5','#98bfc9',24);
 const z=[{id:'header-copy',safe:[57,48,520,312],exclude:[537,37,1054,382]},{id:'objective',safe:[75,381,1014,453]}];
 [{x:45,y:510,tint:'#fcf0d9',accent:'#d5a367'},{x:560,y:510,tint:'#e6f4ef',accent:'#70b6b0'},{x:45,y:873,tint:'#fcf0d9',accent:'#d5a367'},{x:560,y:873,tint:'#e6f4ef',accent:'#70b6b0'}].forEach((c,i)=>{
  body+=rect(c.x,c.y,481,333,'#fffdf7',c.accent,20)+rect(c.x,c.y,481,64,navy,'none',12);
  body+=rect(c.x+25,c.y+96,431,131,c.tint,'none',18);
  z.push({id:`variant-${i+1}-header`,safe:[c.x+24,c.y+14,c.x+457,c.y+50]},{id:`variant-${i+1}-core-price`,safe:[c.x+46,c.y+113,c.x+435,c.y+211]},{id:`variant-${i+1}-guarantee`,safe:[c.x+38,c.y+251,c.x+443,c.y+307]});
 });
 body+=rect(45,1240,996,132,'#f4f7f4','#b3cacf',22);
 z.push({id:'maps-and-tier',safe:[73,1259,1013,1353]},{id:'footer',safe:[146,1395,940,1430]});
 await save('social',1086,1448,svg(1086,1448,body),z);
}
{
 let body=`<path d="M0 750Q490 630 830 754T1672 785V941H0Z" fill="${blue}"/><image href="${heroURL}" x="812" y="133" width="815" height="543"/>`;
 body+=rect(76,601,712,69,navy,'none',17)+rect(76,704,712,102,'#eaf4f5','#98bfc9',24);
 const z=[{id:'brand',safe:[104,116,761,176]},{id:'title',safe:[84,270,778,455]},{id:'subtitle',safe:[107,490,754,550]},{id:'versions',safe:[100,618,764,653]},{id:'selling-point',safe:[103,726,760,783]},{id:'hero-exclude',exclude:[812,133,1627,676]}];
 await save('cover',1672,941,svg(1672,941,body),z);
}
await fs.writeFile(path.join(project,'source/structure-zones-r1.json'),JSON.stringify(zones,null,2));
await fs.writeFile(contentPath,JSON.stringify(content,null,2));
await fs.writeFile(statePath,JSON.stringify(state,null,2));
const auditPath=path.join(project,'review/四版价目核对.md');
let audit=await fs.readFile(auditPath,'utf8');
audit=audit.replace('机密撤离目标与既有地图/规则仍待答复，未填正式海报文字。','机密已由用户确认统一2/3/4/5/全点，沿用巴克什/航天基地/核电站及既有机密规则。正式排字待无字底图确认。');
await fs.writeFile(auditPath,audit);
await fs.copyFile(new URL(import.meta.url),path.join(project,'source/prepare-stage2.mjs'));
console.log(JSON.stringify({stage:state.stage,contentVerified:state.contentVerified,guides:zones.map(z=>({id:z.id,size:[z.w,z.h],zones:z.zones.length})),businessPending:0},null,2));
