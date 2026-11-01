import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const sharp=createRequire(import.meta.url)(path.resolve(path.dirname(process.execPath),'../node_modules/sharp'));
const root=path.resolve(import.meta.dirname,'..'),read=f=>JSON.parse(fs.readFileSync(path.join(root,f),'utf8'));
const c=read('content.json'),plan=read('layout.json'),state=read('campaign-state.json'),manifest=read('output/manifest.json'),geometry=read('review/geometry-diagnostic.json');
const checks=[],eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b),sha=f=>createHash('sha256').update(fs.readFileSync(f)).digest('hex');
function check(name,ok,detail){checks.push({name,passed:!!ok,detail});if(!ok)console.error('FAIL '+name);}
check('端手游同步修订范围',eq(plan.pages.map(p=>p.id),['desktop','mobile'])&&eq(manifest.map(p=>p.id),state.deliveryScope.pageIds));
check('原手游价格逐档保持',eq(c.tiers.map(t=>t.mobilePrice),[288,388,588,788,1088,1488]));
check('豪华终极208格，其他档位保持',eq(c.tiers.map(t=>t.totalGoldRedSlots),[50,50,108,108,208,208]));
check('原计入关系保持',eq(c.tiers.map(t=>t.processLabel),['计入','不计入','计入','不计入','计入','不计入']));
check('新例外适用范围准确',eq(c.notices.find(n=>n.id==='accumulation-excludes-destruction-loss').appliesToTierIds,['standard','advanced','ultimate']));
const md=manifest.find(m=>m.id==='desktop'),m=manifest.find(m=>m.id==='mobile'),desktop=path.join(root,md.png),png=path.join(root,m.png),page=plan.pages.find(p=>p.id==='mobile'),b=read('review/mobile-text-bounds.json'),allText=b.map(b=>b.text).join('|');
check('端游已交付图像保持',sha(desktop)===state.previousDesktopSHA256);
check('手游价目逐档显示准确',eq(b.filter(b=>b.role==='price-number').map(b=>Number(b.text)),[288,388,588,788,1088,1488]));
check('手游两处208无218',b.filter(b=>b.group.endsWith('-slots')&&b.text==='208').length===2&&!allText.includes('218'));
check('平台标题显示手游',allText.includes('手游 · 六档挖矿套餐')&&!allText.includes('端游'));
check('原五项规则和新增规则全部呈现',[
 '标准版、进阶版、终极版：','不计入总保底','累计金红格数时，','不计炸单战损','撤离成功','失败不计算','行动收获截图','吃不下的金和红，打手帮带也计入。','高价值物品','最后一把金红超过格数时','优先计算红'
].every(k=>allText.includes(k)));
check('手游手机须知字号保持',Math.min(...b.filter(b=>b.role==='rule-body').map(b=>b.effectiveSize))*390/836>=14);
check('手游无文字重叠',read('review/mobile-text-overlaps.json').length===0);
for(const g of geometry.filter(g=>g.page==='mobile')){
 const spec=page.auditGroups.find(s=>s.prefix===g.prefix);
 check(g.group+'在安全区内',!g.outside,g.margins);
 if(spec.centerY)check(g.group+'整组垂直居中',Math.abs(g.dy)<=2,g.dy);
 if(spec.centerX)check(g.group+'整组水平居中',Math.abs(g.dx)<=(spec.toleranceX||2),g.dx);
 if(g.prefix.includes('price'))check(g.group+'价格平衡',Math.abs(g.dx)<=(spec.toleranceX||15),g.dx);
}
for(const n of b.filter(b=>b.role==='price-number')){
 const u=b.find(b=>b.role==='price-unit'&&b.group===n.group),ratio=u.height/n.height,gap=u.x-n.x-n.width;
 check(n.group+'数字单位共用基线且间距合理',Math.abs(n.baseline-u.baseline)<.01&&ratio>=.65&&ratio<=.75&&gap>=5,{ratio,gap});
}
const meta=await sharp(png).metadata();
check('手游最终PNG实际尺寸',meta.width===m.width&&meta.height===m.height,{width:meta.width,height:meta.height,native:[m.nativeWidth,m.nativeHeight]});
await sharp(png).resize({width:390}).png().toFile(path.join(root,'review/mobile-final-390.png'));
await sharp(png).resize({width:836}).png().toFile(path.join(root,'review/mobile-final-native.png'));
const crop=async(name,x,y,w,h,width)=>{
 const s=meta.width/page.w;
 await sharp(png).extract({left:Math.round(x*s),top:Math.round(y*s),width:Math.round(w*s),height:Math.round(h*s)}).resize({width}).png().toFile(path.join(root,'review',name+'.png'));
};
await crop('qa-mobile-revised-208',29,745,777,174,1000);
await crop('qa-mobile-notices',29,1318,779,443,1100);
const top=Math.ceil(930*meta.width/836),rect={left:0,top,width:meta.width,height:meta.height-top};
const sharedDesktop=await sharp(desktop).extract(rect).raw().toBuffer(),sharedMobile=await sharp(png).extract(rect).raw().toBuffer();
check('两端玩法规则和注意事项像素完全一致',sharedDesktop.equals(sharedMobile));
if(process.argv[2]){
 const {data:old,info:oi}=await sharp(process.argv[2]).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 const {data:now,info:ni}=await sharp(png).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 check('旧手游原稿对比尺寸一致',oi.width===ni.width&&oi.height===ni.height);
 let changed=0,outside=0;const s=meta.width/836,allowed=[[420,750,590,921],[110,1403,773,1716]].map(r=>r.map(x=>Math.round(x*s)));
 if(oi.width===ni.width&&oi.height===ni.height){
  for(let y=0;y<ni.height;y++)for(let x=0;x<ni.width;x++){
   const i=(y*ni.width+x)*4;if(old[i]===now[i]&&old[i+1]===now[i+1]&&old[i+2]===now[i+2]&&old[i+3]===now[i+3])continue;
   changed++;if(!allowed.some(([l,t,r,bt])=>x>=l&&x<=r&&y>=t&&y<=bt))outside++;
  }
  check('手游只改变格数和须知区域',outside===0,{changedPixels:changed,unexpectedPixels:outside});
 }
}
const report={revision:'r3',counts:{total:checks.length,passed:checks.filter(c=>c.passed).length,failed:checks.filter(c=>!c.passed).length},checks,sha256:{desktop:sha(desktop),mobile:sha(png)},limitation:'自动来源、几何和像素检查配合实际PNG及手机预览目视。'};
fs.writeFileSync(path.join(root,'review/final-verification.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({counts:report.counts,failed:checks.filter(c=>!c.passed)},null,2));
if(report.counts.failed)process.exitCode=1;

