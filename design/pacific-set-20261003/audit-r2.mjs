import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const require=createRequire(import.meta.url),sharp=require(String.raw`C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp`);
const read=async rel=>JSON.parse(await fs.readFile(path.join(project,rel),'utf8'));
const old=await read('archive/r1/content.json'),c=await read('content.json'),s=await read('campaign-state.json'),audit=await read('review/layout-and-data-checks-r2.json');
if(audit.failures.length||audit.missingPages.length)throw new Error('排版核对未通过');
const expected=[['mobile-secret',1,3,968],['mobile-secret',2,3,868],['mobile-secret',0,4,1668],['mobile-secret',1,4,1868],['mobile-secret',2,4,1568],['mobile-absolute',0,4,2488],['mobile-absolute',1,4,2688],['mobile-absolute',2,4,2888]];
const checks=[],assert=(name,actual,expected)=>{const passed=JSON.stringify(actual)===JSON.stringify(expected);checks.push({name,actual,expected,passed});if(!passed)throw new Error(name);};
let changed=0;
for(const t of c.tiers){
 const prev=old.tiers.find(x=>x.id===t.id);
 assert(t.id+'目标',t.targets,prev.targets);assert(t.id+'规则',t.rules,prev.rules);
 t.maps.forEach((m,i)=>{
  assert(t.id+'/'+m.name+'地图名称',m.name,prev.maps[i].name);assert(t.id+'/'+m.name+'保底',m.guarantees,prev.maps[i].guarantees);
  m.prices.forEach((v,j)=>{const override=expected.find(x=>x[0]===t.id&&x[1]===i&&x[2]===j);assert(t.id+'/'+m.name+'/'+j+'售价',v,override?override[3]:prev.maps[i].prices[j]);if(v!==prev.maps[i].prices[j])changed++;});
 });
}
assert('改价数量',changed,8);assert('共用附加规则',c.commonRules,old.commonRules);assert('展示文案',c.copy,old.copy);
const oldHashes=await read('review/final-file-hashes-r1.json');const currentHashes=[];
for(const item of oldHashes.files){
 const sha256=createHash('sha256').update(await fs.readFile(path.join(project,item.file))).digest('hex');currentHashes.push({file:item.file,sha256});
 if(!/mobile-(secret|absolute)\./.test(item.file))assert(item.file+'保持原文件',sha256,item.sha256);
}
const pixelChecks=[];
for(const id of ['mobile-secret','mobile-absolute']){
 const prior=await sharp(path.join(project,'archive/r1/review',id+'.png')).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 const now=await sharp(path.join(project,'review',id+'.png')).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 assert(id+'预览尺寸',now.info,prior.info);
 const rows=[[448,652],[770,976],[1100,1306]];
 const zones=expected.filter(x=>x[0]===id).map(x=>{const r=rows[x[1]],rh=(r[1]-r[0])/5;return[230,r[0]+x[2]*rh,334,r[0]+(x[2]+1)*rh];});
 const oldBounds=await read('archive/r1/review/'+id+'-text-bounds.json'),newBounds=await read('review/'+id+'-text-bounds.json');
 const recenteredRows=[];
 for(const edit of expected.filter(x=>x[0]===id)){
  const lineGroup=`row-${edit[1]}-${edit[2]}`,before=oldBounds.filter(b=>b.lineGroup===lineGroup),after=newBounds.filter(b=>b.lineGroup===lineGroup);
  const delta=after[0].baseline-before[0].baseline;
  assert(id+'/'+lineGroup+'基线微调小于0.05',Math.abs(delta)<.05,true);
  if(Math.abs(delta)>1e-8){const r=rows[edit[1]],rh=(r[1]-r[0])/5;zones.push([52,r[0]+edit[2]*rh,674,r[0]+(edit[2]+1)*rh]);recenteredRows.push({lineGroup,baselineDelta:delta,basis:'字形可见边界改变，渲染器将整行微调到原行中心；对应价格/保底/单位/档位仍共用基线'});}
 }
 let changedPixels=0,outside=0;const outsideBounds=[Infinity,Infinity,-Infinity,-Infinity],outsideRows=new Map();
 for(let y=0;y<now.info.height;y++)for(let x=0;x<now.info.width;x++){
  const idx=(y*now.info.width+x)*4;
  if(now.data[idx]!==prior.data[idx]||now.data[idx+1]!==prior.data[idx+1]||now.data[idx+2]!==prior.data[idx+2]||now.data[idx+3]!==prior.data[idx+3]){changedPixels++;if(!zones.some(z=>x>=z[0]&&x<z[2]&&y>=Math.floor(z[1])&&y<=Math.ceil(z[3]))){outside++;outsideBounds[0]=Math.min(outsideBounds[0],x);outsideBounds[1]=Math.min(outsideBounds[1],y);outsideBounds[2]=Math.max(outsideBounds[2],x);outsideBounds[3]=Math.max(outsideBounds[3],y);outsideRows.set(Math.floor(y/40),1+(outsideRows.get(Math.floor(y/40))||0));}}
 }
 if(outside)console.log(JSON.stringify({id,info:now.info,changedPixels,outside,outsideBounds,outsideRows:[...outsideRows],zones}));
 assert(id+'改价区及同行微调外变化像素',outside,0);pixelChecks.push({page:id,resolution:[now.info.width,now.info.height],changedPixels,outsideAffectedRows:outside,zones,recenteredRows});
 for(let i=0;i<3;i++){
  const scale=3200/724,top=[351,665,989][i];
  await sharp(path.join(project,'output/png',id+'.png')).extract({left:Math.round(38*scale),top:Math.round(top*scale),width:Math.round(654*scale),height:Math.round(318*scale)}).png().toFile(path.join(project,'review',`${id}-map-${i}-prices-r2.png`));
 }
}
await fs.writeFile(path.join(project,'review/business-source-checks-r2.json'),JSON.stringify({revision:'r2',passed:true,exactUserPrices:expected,checks,pixelChecks,unchangedFileChecks:12},null,2));
await fs.writeFile(path.join(project,'review/final-file-hashes-r2.json'),JSON.stringify({revision:'r2',files:currentHashes},null,2));
const pieces=[];
for(const [i,id]of ['mobile-secret','mobile-absolute'].entries())for(let j=0;j<3;j++){
 const input=await sharp(path.join(project,'review',`${id}-map-${j}-prices-r2.png`)).resize({width:900}).png().toBuffer();pieces.push({input,left:i*920+10,top:j*458+10});
}
await sharp({create:{width:1840,height:1374,channels:3,background:'#f8f7f0'}}).composite(pieces).png().toFile(path.join(project,'review/mobile-prices-contact-r2.png'));
console.log(JSON.stringify({changedPrices:changed,checks:checks.length,unchangedOtherFinals:12,pixelChecks},null,2));
