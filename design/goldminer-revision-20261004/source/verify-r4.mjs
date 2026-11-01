import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const sharp=createRequire(import.meta.url)(path.resolve(path.dirname(process.execPath),'../node_modules/sharp'));
const root=path.resolve(import.meta.dirname,'..'),read=f=>JSON.parse(fs.readFileSync(path.join(root,f),'utf8'));
const c=read('content.json'),plan=read('layout.json'),state=read('campaign-state.json'),manifest=read('output/manifest.json'),geometry=read('review/geometry-diagnostic.json');
const eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b),sha=f=>createHash('sha256').update(fs.readFileSync(f)).digest('hex'),checks=[];
function check(name,passed,detail){checks.push({name,passed:!!passed,detail});if(!passed)console.error('FAIL '+name);}
check('三张页面符合用户累计范围',eq(plan.pages.map(p=>p.id),state.deliveryScope.pageIds)&&eq(manifest.map(m=>m.id),['desktop','mobile','social']));
for(const id of ['desktop','mobile'])check(id+'已交付图保持',sha(path.join(root,manifest.find(m=>m.id===id).png))===state.previousPNGHashes[id]);
const page=plan.pages.find(p=>p.id==='social'),m=manifest.find(m=>m.id==='social'),png=path.join(root,m.png),bounds=read('review/social-text-bounds.json'),text=bounds.map(b=>b.text).join('|');
check('封面两处208格，无218',bounds.filter(b=>b.text==='208'&&b.group.endsWith('-slots')).length===2&&!text.includes('218'));
check('封面六档格数与共同内容一致',eq(page.elements.filter(e=>e.role==='slot-count').map(e=>Number(e.runs[0].text)),c.tiers.map(t=>t.totalGoldRedSlots)));
check('两端价格与原稿一致',eq(bounds.filter(b=>b.role==='price-number').map(b=>Number(b.text)),[388,288,488,388,688,588,988,788,1188,1088,1688,1488]));
check('原核心权益保留',text.includes('红每格 +80w')&&text.includes('金每格 +20w'));
check('无叠字或额外须知',read('review/social-text-overlaps.json').length===0&&!bounds.some(b=>b.role==='rule-body'||b.text==='注意事项'));
for(const g of geometry.filter(g=>g.page==='social')){
 const spec=page.auditGroups.find(a=>a.prefix===g.prefix);
 check(g.group+'在安全区并保持居中',!g.outside&&(!spec.centerY||Math.abs(g.dy)<=2)&&(!spec.centerX||Math.abs(g.dx)<=(spec.toleranceX||2)),{margins:g.margins,dx:g.dx,dy:g.dy});
}
for(const n of bounds.filter(b=>b.role==='price-number')){
 const u=bounds.find(b=>b.role==='price-unit'&&b.group===n.group),ratio=u.height/n.height,gap=u.x-n.x-n.width;
 check(n.group+'数字单位基线和间隙',Math.abs(n.baseline-u.baseline)<.01&&ratio>=.65&&ratio<=.75&&gap>=5,{ratio,gap});
}
const meta=await sharp(png).metadata();check('高清PNG3000×4000',meta.width===3000&&meta.height===4000);
await sharp(png).resize({width:390}).png().toFile(path.join(root,'review/social-final-390.png'));
await sharp(png).resize({width:1086}).png().toFile(path.join(root,'review/social-final-native.png'));
const s=meta.width/1086;
await sharp(png).extract({left:Math.round(48*s),top:Math.round(969*s),width:Math.round(992*s),height:Math.round(215*s)}).resize({width:1200}).png().toFile(path.join(root,'review/qa-cover-revised-208.png'));
if(process.argv[2]){
 const {data:old,info:oi}=await sharp(process.argv[2]).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 const {data:now,info:ni}=await sharp(png).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 check('与原封面对比尺寸一致',oi.width===ni.width&&oi.height===ni.height);
 let changed=0,outside=0;const allowed=[[231,986,361,1058],[231,1101,361,1165]].map(r=>r.map(v=>Math.round(v*s)));
 if(oi.width===ni.width&&oi.height===ni.height){
  for(let y=0;y<ni.height;y++)for(let x=0;x<ni.width;x++){
   const i=(y*ni.width+x)*4;if(old[i]===now[i]&&old[i+1]===now[i+1]&&old[i+2]===now[i+2]&&old[i+3]===now[i+3])continue;
   changed++;if(!allowed.some(([l,t,r,b])=>x>=l&&x<=r&&y>=t&&y<=b))outside++;
  }
  check('像素变化仅限两处格数区域',changed>0&&outside===0,{changedPixels:changed,unexpectedPixels:outside});
 }
}
const report={revision:'r4',date:'2026-10-05',counts:{total:checks.length,passed:checks.filter(c=>c.passed).length,failed:checks.filter(c=>!c.passed).length},checks,sha256:Object.fromEntries(manifest.map(m=>[m.id,sha(path.join(root,m.png))])),limitation:'自动检查配合实际成图和390px手机预览目视。'};
fs.writeFileSync(path.join(root,'review/final-verification.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({counts:report.counts,failed:checks.filter(c=>!c.passed)},null,2));
if(report.counts.failed)process.exitCode=1;

