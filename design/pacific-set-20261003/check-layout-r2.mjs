import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),sharp=require(String.raw`C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp`);
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const plan=JSON.parse(await fs.readFile(path.join(project,'layout.json'),'utf8'));
const content=JSON.parse(await fs.readFile(path.join(project,'content.json'),'utf8'));
const manifest=JSON.parse(await fs.readFile(path.join(project,'output/manifest.json'),'utf8'));
const revision=process.argv[2]||'r2'; const results=[],failures=[];let bindingCount=0,amountPairs=0;
const eq=(actual,expected,msg)=>{bindingCount++;if(actual!==expected)failures.push({type:'data',msg,actual,expected});};
for(const p of plan.pages){
 if(!manifest.some(r=>r.id===p.id))continue;
 const bounds=JSON.parse(await fs.readFile(path.join(project,'review',p.id+'-text-bounds.json'),'utf8'));
 const glyphs=JSON.parse(await fs.readFile(path.join(project,'.cache/font-outlines.json'),'utf8'));
 const rows=[];
 for(const g of p.auditGroups){
  const matched=bounds.filter(b=>g.roles?.includes(b.role)||(g.groupName&&b.group===g.groupName)||(g.groupPrefix&&b.group?.startsWith(g.groupPrefix)));
  if(!matched.length){failures.push({page:p.id,group:g.id,type:'missing-audit-group'});continue;}
  const left=Math.min(...matched.map(b=>b.x)),right=Math.max(...matched.map(b=>b.x+b.width)),top=Math.min(...matched.map(b=>b.y)),bottom=Math.max(...matched.map(b=>b.y+b.height)),cx=(left+right)/2,cy=(top+bottom)/2;
  const overflow={left:Math.max(0,g.safe[0]-left),right:Math.max(0,right-g.safe[2]),top:Math.max(0,g.safe[1]-top),bottom:Math.max(0,bottom-g.safe[3])};
  const errorX=g.centerX===undefined?null:cx-g.centerX,errorY=g.centerY===undefined?null:cy-g.centerY;
  const row={group:g.id,safe:g.safe,ink:[left,top,right,bottom],center:[cx,cy],errorX,errorY,overflow,topPadding:top-g.safe[1],bottomPadding:g.safe[3]-bottom};rows.push(row);
  if(Object.values(overflow).some(v=>v>.15))failures.push({page:p.id,type:'containment',...row});
  if(errorX!==null&&Math.abs(errorX)>g.toleranceX+.05)failures.push({page:p.id,type:'horizontal-center',...row});
  if(errorY!==null&&Math.abs(errorY)>g.toleranceY+.05)failures.push({page:p.id,type:'vertical-center',...row});
 }
 for(const binding of p.businessBindings){
  if(binding.type==='map-tier'){
   const t=content.tiers.find(t=>t.id===p.id),b=role=>bounds.find(b=>b.role===role);
   eq(Number(b(binding.roles.price)?.text),t.maps[binding.map].prices[binding.tier],p.id+'售价');
   eq(Number(b(binding.roles.guarantee)?.text),t.maps[binding.map].guarantees[binding.tier],p.id+'保底');
   eq(b(binding.roles.target)?.text,t.targets[binding.tier].replace('不同',''),p.id+'撤离目标');
   eq(b(binding.roles.tier)?.text,content.tierNames[binding.tier],p.id+'档位');
  }else{
   const t=content.tiers.find(t=>t.id===binding.variant);
   const price=bounds.filter(b=>b.group===binding.priceGroup),loot=bounds.filter(b=>b.group===binding.lootGroup);
   eq(Number(price[0]?.text),t.maps[0].prices[0],binding.variant+'广告体验价');
   eq(Number(loot.find(b=>/^\d+$/.test(b.text))?.text),t.maps[0].guarantees[0],binding.variant+'广告保底');
  }
 }
 for(const b of bounds.filter(b=>/-number$/.test(b.role))){
  const unit=bounds.find(u=>u.role===b.role.replace('-number','-unit'));
  if(!unit){failures.push({page:p.id,type:'missing-unit',role:b.role});continue;}
  amountPairs++;
  const gap=unit.x-(b.x+b.width),ratio=unit.height/b.height;
  if(Math.abs(b.baseline-unit.baseline)>.1||gap<6.9||ratio<.65||ratio>.76)failures.push({page:p.id,type:'number-unit',role:b.role,baselineDelta:b.baseline-unit.baseline,gap,ratio});
 }
 const body=bounds.filter(b=>b.group?.startsWith('door-')||b.group?.startsWith('notice-')||b.role==='prophet-intro');
 const minRule=body.filter(b=>b.font==='Microsoft YaHei UI').reduce((n,b)=>Math.min(n,b.effectiveSize),Infinity);
 const shrunk=bounds.filter(b=>b.effectiveSize+.05<b.requestedSize).map(b=>({text:b.text,group:b.group,role:b.role,requested:b.requestedSize,effective:b.effectiveSize}));
 if(Number.isFinite(minRule)&&minRule<25.95)failures.push({page:p.id,type:'rule-font-shrunk',minRule});
 const unreviewed=bounds.filter(b=>!p.auditGroups.some(g=>g.roles?.includes(b.role)||(g.groupName&&b.group===g.groupName)||(g.groupPrefix&&b.group?.startsWith(g.groupPrefix))));
 if(unreviewed.length)failures.push({page:p.id,type:'unreviewed-text',text:unreviewed.map(b=>b.text)});
 results.push({page:p.id,groups:rows,shrunk,minRule:Number.isFinite(minRule)?minRule:null,bodyPhone:Number.isFinite(minRule)?minRule*390/p.w:null,boundsCount:bounds.length});
 if(['mobile-secret','mobile-absolute'].includes(p.id)){
 await sharp(path.join(project,'output/png',p.id+'.png')).resize({width:390}).png().toFile(path.join(project,'review',p.id+'-mobile390.png'));
 if(p.id.startsWith('desktop')||p.id.startsWith('mobile')){
  const scale=p.ew/p.w;
  await sharp(path.join(project,'output/png',p.id+'.png')).extract({left:Math.round(40*scale),top:Math.round(1328*scale),width:Math.round(645*scale),height:Math.round(704*scale)}).png().toFile(path.join(project,'review',p.id+'-rules-detail.png'));
  await sharp(path.join(project,'output/png',p.id+'.png')).extract({left:Math.round(38*scale),top:Math.round(351*scale),width:Math.round(654*scale),height:Math.round(308*scale)}).png().toFile(path.join(project,'review',p.id+'-price-detail.png'));
 }
}
}
const report={version:revision,pages:results.map(r=>r.page),missingPages:plan.pages.filter(p=>!results.some(r=>r.page===p.id)).map(p=>p.id),dataChecks:bindingCount,numberUnitPairs:amountPairs,groupsChecked:results.reduce((n,r)=>n+r.groups.length,0),failures,results};
await fs.writeFile(path.join(project,'review/layout-and-data-checks-'+revision+'.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({pages:report.pages,missing:report.missingPages,dataChecks:bindingCount,groups:report.groupsChecked,amountPairs,failures,fonts:results.map(({page,minRule,bodyPhone,shrunk})=>({page,minRule,bodyPhone,shrunk}))},null,2));
