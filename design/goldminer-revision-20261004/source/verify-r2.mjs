import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const sharp=createRequire(import.meta.url)(path.resolve(path.dirname(process.execPath),'../node_modules/sharp'));
const root=path.resolve(import.meta.dirname,'..'),read=f=>JSON.parse(fs.readFileSync(path.join(root,f),'utf8'));
const c=read('content.json'),plan=read('layout.json'),state=read('campaign-state.json'),manifest=read('output/manifest.json'),geometry=read('review/geometry-diagnostic.json');
const checks=[],eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
function check(name,ok,detail){checks.push({name,passed:!!ok,detail});if(!ok)console.error('FAIL '+name);}
check('附件端游单页范围',eq(plan.pages.map(p=>p.id),['desktop'])&&eq(manifest.map(p=>p.id),state.deliveryScope.pageIds));
check('六档原价保持',eq(c.tiers.map(t=>t.desktopPrice),[388,488,688,988,1188,1688]));
check('豪华与终极均为208格',eq(c.tiers.map(t=>t.totalGoldRedSlots),[50,50,108,108,208,208]));
check('原带出过程计入关系保持',eq(c.tiers.map(t=>t.processLabel),['计入','不计入','计入','不计入','计入','不计入']));
check('红80w金20w与示例保持',c.mechanics.red.perSlotGuaranteeW===80&&c.mechanics.gold.perSlotGuaranteeW===20&&c.mechanics.example.guaranteeAddedW===1000&&c.mechanics.example.extractedValueW===800&&c.mechanics.example.remainingW===200);
const added=c.notices.find(n=>n.id==='accumulation-excludes-destruction-loss');
check('新增规则适用三个版本且只改变累计格数口径',c.notices.length===6&&eq(added.appliesToTierIds,['standard','advanced','ultimate'])&&added.metric==='totalGoldRedSlots'&&added.excluded==='炸单战损');
const page=plan.pages[0],bounds=read('review/desktop-text-bounds.json'),allText=bounds.map(b=>b.text).join('|');
check('成图两处208格，无218',bounds.filter(b=>b.group.endsWith('-slots')&&b.text==='208').length===2&&!allText.includes('218'));
check('原五项须知全部保留',[
 '标准版、进阶版、终极版：','不计入总保底','撤离成功','失败不计算','行动收获截图','吃不下的金和红，打手帮带也计入。','打手负责理包','高价值物品','最后一把金红超过格数时','优先计算红'
].every(k=>allText.includes(k)));
check('成图新增累计金红格数不计炸单战损',allText.includes('累计金红格数时，')&&allText.includes('不计炸单战损'));
check('须知字号保持手机可读',Math.min(...bounds.filter(b=>b.role==='rule-body').map(b=>b.effectiveSize))*390/836>=14);
check('无文字轮廓重叠',read('review/desktop-text-overlaps.json').length===0);
for(const g of geometry){
 const spec=page.auditGroups.find(s=>s.prefix===g.prefix);
 check(g.group+'在安全区内',!g.outside,g.margins);
 if(spec.centerY)check(g.group+'整组垂直居中',Math.abs(g.dy)<=2,g.dy);
 if(spec.centerX)check(g.group+'整组横向居中',Math.abs(g.dx)<=(spec.toleranceX||2),g.dx);
 if(g.prefix.includes('price'))check(g.group+'价格横向平衡',Math.abs(g.dx)<=(spec.toleranceX||15),g.dx);
}
for(const n of bounds.filter(b=>b.role==='price-number')){
 const u=bounds.find(b=>b.role==='price-unit'&&b.group===n.group),ratio=u.height/n.height,gap=u.x-n.x-n.width;
 check(n.group+'数字单位基线与间隙',Math.abs(n.baseline-u.baseline)<.01&&ratio>=.65&&ratio<=.75&&gap>=5,{ratio,gap});
}
const m=manifest[0],png=path.join(root,m.png),meta=await sharp(png).metadata();
check('PNG实际尺寸',meta.width===m.width&&meta.height===m.height,{width:meta.width,height:meta.height,native:[m.nativeWidth,m.nativeHeight]});
await sharp(png).resize({width:390}).png().toFile(path.join(root,'review/desktop-final-390.png'));
await sharp(png).resize({width:836}).png().toFile(path.join(root,'review/desktop-final-native.png'));
const crop=async(name,x,y,w,h,width)=>{
 const scale=meta.width/page.w;
 await sharp(png).extract({left:Math.round(x*scale),top:Math.round(y*scale),width:Math.round(w*scale),height:Math.round(h*scale)}).resize({width}).png().toFile(path.join(root,'review',name+'.png'));
};
await crop('qa-revised-208',29,745,777,174,1000);
await crop('qa-notices',29,1318,779,443,1100);
const baseline=process.argv[2];
if(baseline){
 const {data:old,info:oi}=await sharp(baseline).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 const {data:now,info:ni}=await sharp(png).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 check('原稿对比尺寸一致',oi.width===ni.width&&oi.height===ni.height);
 let changed=0,outside=0;const s=meta.width/836;
 const allowed=[[420,750,590,921],[110,1403,773,1716]].map(r=>r.map(x=>Math.round(x*s)));
 if(oi.width===ni.width&&oi.height===ni.height){
  for(let y=0;y<ni.height;y++)for(let x=0;x<ni.width;x++){
   const i=(y*ni.width+x)*4;if(old[i]===now[i]&&old[i+1]===now[i+1]&&old[i+2]===now[i+2]&&old[i+3]===now[i+3])continue;
   changed++;if(!allowed.some(([l,t,r,b])=>x>=l&&x<=r&&y>=t&&y<=b))outside++;
  }
  check('格数和须知区域之外像素保持原稿',outside===0,{changedPixels:changed,unexpectedPixels:outside});
 }
}
const report={revision:'r2',counts:{total:checks.length,passed:checks.filter(c=>c.passed).length,failed:checks.filter(c=>!c.passed).length},checks,sha256:createHash('sha256').update(fs.readFileSync(png)).digest('hex'),limitation:'几何和数据检查配合最终PNG目视。'};
fs.writeFileSync(path.join(root,'review/final-verification.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({counts:report.counts,failed:checks.filter(c=>!c.passed)},null,2));
if(report.counts.failed)process.exitCode=1;
