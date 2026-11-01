import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';

const [project,modules,generated]=process.argv.slice(2);
const require=createRequire(path.join(modules,'..','sand-safe-background-loader.cjs'));
const sharp=require('sharp');
for(const d of ['03-backgrounds','assets/backgrounds','04-png-export','review'])await fs.mkdir(path.join(project,d),{recursive:true});
const nativeFile=path.join(project,'03-backgrounds/poster-gold-v1.png');
await fs.copyFile(generated,nativeFile);
await fs.copyFile(generated,path.join(project,'assets/backgrounds/poster-gold.png'));
const m=await sharp(nativeFile).metadata();
if(m.width!==836||m.height!==1881)throw new Error('Native dimensions changed: remeasure before export.');
const measured={
  basis:'人工查看本轮生成的实际无字底图后，按原生836×1881坐标重测；不套用结构稿的1024×2304坐标。',
  native:{width:m.width,height:m.height},
  export:{width:3200,height:7200,method:'Lanczos3放大无字底图；正式文字将在下一阶段直接以目标尺寸矢量渲染。'},
  header:{container:{x:30,y:92,width:420,height:194},copy:{x:55,y:112,width:344,height:150},illustrationBounds:{x:421,y:51,width:389,height:249},safeGap:22},
  reward:{container:{x:34,y:309,width:767,height:170},copy:{x:60,y:327,width:646,height:131},reservedAccent:{x:727,y:341,width:64,height:126}},
  prices:{container:{x:36,y:491,width:765,height:374},header:{x:40,y:497,width:756,height:53},body:{x:51,y:555,width:735,height:300},rowBounds:[[550,612],[612,674],[674,736],[736,799],[799,863]],rowCenters:[581,643,705,767.5,831],columnBounds:[[52,272],[297,537],[562,785]],columns:[{label:'完成目标',center:162},{label:'端游',center:417},{label:'手游',center:674}],count:5},
  objectives:{container:{x:36,y:878,width:764,height:371},title:{x:62,y:897,width:711,height:71},rows:[{x:75,y:995,width:685,height:47},{x:75,y:1073,width:685,height:48},{x:75,y:1154,width:685,height:60}],stripBounds:[[983,1052],[1062,1133],[1143,1226]]},
  notices:{container:{x:36,y:1262,width:764,height:464},title:{x:62,y:1283,width:711,height:38},groups:[{x:64,y:1330,width:710,height:70},{x:64,y:1423,width:710,height:75},{x:64,y:1525,width:710,height:77},{x:64,y:1633,width:710,height:77}],separatorY:[1408,1510,1617]},
  plannedType:{body:31,bodyLineHeight:37,priceNumber:48,priceUnit:42,rulePhoneEquivalent:Number((31*390/m.width).toFixed(2)),pricePhoneEquivalent:Number((48*390/m.width).toFixed(2))},
  layoutCheck:{priceRows:5,priceColumns:3,continuousNavyHeader:true,objectiveStrips:3,noticeGroups:4,textFree:true,approvedCharacterStyle:true},
  reviewStatus:'stage-2-background-only; final typography and business-rule QA remain for stage 3'
};
await fs.writeFile(path.join(project,'02-layouts/generated-background-measurements.json'),JSON.stringify(measured,null,2)+'\n');
await sharp(nativeFile).resize(3200,7200,{kernel:'lanczos3'}).withMetadata({density:300}).png({compressionLevel:9}).toFile(path.join(project,'04-png-export/sand-safe-absolute-background.png'));
await sharp(nativeFile).resize({width:390}).png().toFile(path.join(project,'review/background-phone.png'));
await fs.copyFile(nativeFile,path.join(project,'review/background-native.png'));
const bg=await fs.readFile(nativeFile);
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const guideLabels=[];
const label=(s,cx,cy,size=29,fill='#24455f')=>{
  const id='guide-'+guideLabels.length;
  guideLabels.push({id,text:s,font:'Microsoft YaHei UI',style:0,cx,cy,size,fill});
  return `@@${id}@@`;
};
const outline=z=>`<rect x="${z.x}" y="${z.y}" width="${z.width}" height="${z.height}" rx="8" fill="#fffdf5" fill-opacity="0.18" stroke="#587e98" stroke-width="1.5" stroke-dasharray="6 5"/>`;
let overlay=outline(measured.header.copy)+label('品牌 / 主标题',227,162,36)+label('绝密标签',227,223,27);
overlay+=outline(measured.reward.copy)+label('许愿奖励',383,366,36)+label('二选一条件 / 赠送保底额度',383,420,29);
for(const c of measured.prices.columns)overlay+=label(c.label,c.center,524,31,'#fff9e9');
for(let i=0;i<5;i++)overlay+=label(`第 ${i+1} 档`,162,measured.prices.rowCenters[i],28)+label('价格',417,measured.prices.rowCenters[i],29)+label('价格',674,measured.prices.rowCenters[i],29);
overlay+=outline(measured.objectives.title)+label('玩法说明',417,920,36)+label('同局成功撤离 · 三项目标同时达成',417,953,26);
for(let i=0;i<3;i++){
  const z=measured.objectives.rows[i];overlay+=outline(z)+label(['沙色保险目标','常规大保险目标','端手游带出物资目标'][i],417,z.y+z.height/2,31);
}
overlay+=outline(measured.notices.title)+label('下单须知',417,1302,36);
for(let i=0;i<4;i++){
  const z=measured.notices.groups[i];overlay+=outline(z)+label(['达标口径','炸单与连续计数','摸保险说明','赠送保底补打'][i],417,z.y+z.height/2,31);
}
const inputFile=path.join(project,'02-layouts/guide-font-requests.json');
const outlineFile=path.join(project,'02-layouts/guide-font-outlines.json');
const outlineScript=path.join(project,'source/outline-text.ps1');
await fs.copyFile('C:/Users/chenh/.codex/skills/xingan-gameplay-posters/scripts/outline-text.ps1',outlineScript);
await fs.writeFile(inputFile,JSON.stringify(guideLabels));
execFileSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',outlineScript,'-InputFile',inputFile,'-OutputFile',outlineFile],{encoding:'utf8'});
const glyphs=JSON.parse(await fs.readFile(outlineFile,'utf8'));
const guideBounds=[];
for(const e of guideLabels){
  const g=glyphs[e.id],sc=e.size/100,w=g.width*sc,h=g.height*sc;
  const x=e.cx-w/2,y=e.cy-h/2,dx=x-g.x*sc,dy=y-g.y*sc;
  guideBounds.push({text:e.text,x,y,width:w,height:h});
  overlay=overlay.replace(`@@${e.id}@@`,`<g data-text="${esc(e.text)}" transform="translate(${dx} ${dy}) scale(${sc})" fill="${e.fill}"><path d="${g.d}"/></g>`);
}
await fs.writeFile(path.join(project,'review/background-guide-text-bounds.json'),JSON.stringify(guideBounds,null,2)+'\n');
const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="836" height="1881" viewBox="0 0 836 1881"><title>沙保连连看 · 文字位置稿（非正式填字）</title><image x="0" y="0" width="836" height="1881" href="data:image/png;base64,${bg.toString('base64')}"/>${overlay}</svg>`;
await fs.writeFile(path.join(project,'02-layouts/background-position-guide.svg'),svg);
await sharp(Buffer.from(svg)).png().toFile(path.join(project,'review/background-position-guide.png'));
await sharp(Buffer.from(svg)).resize({width:390}).png().toFile(path.join(project,'review/background-position-phone.png'));
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>沙保连连看 · 无字底图与文字位置稿</title><style>body{margin:0;background:#f8f5ec;color:#203f57;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1650px;margin:auto;padding:26px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}img{display:block;width:100%;height:auto}a{color:#326677}h1{font-size:27px}h2{font-size:21px}.links{margin:15px 0}@media(max-width:750px){.grid{grid-template-columns:1fr}}</style><main><h1>兴安电竞 · 沙保连连看</h1><p>第二步：无字底图与文字位置稿。正式价格、数值和规则将在底图确认后用矢量填入。位置稿中的区块名称仅说明排版占位。</p><div class="links"><a download href="../04-png-export/sand-safe-absolute-background.png">3200×7200无字PNG</a> · <a download href="../03-backgrounds/poster-gold-v1.png">836×1881原生底图</a> · <a download href="background-position-guide.png">文字位置稿PNG</a> · <a href="../03-backgrounds/prompts.md">完整生成提示词</a></div><div class="grid"><section><h2>无字底图</h2><img src="background-native.png" alt="沙保连连看无字绝密底图"></section><section><h2>文字位置稿</h2><img src="background-position-guide.png" alt="本轮底图的文字安全区与内容占位"></section></div><p>一张完整海报，五行端手游并列价目、许愿奖励、三项目标和四组须知。原生底图836×1881，放大导出不代表原生4K绘制。正式正文计划等效约14.5px手机字号，须知与数字仍需在实际最终成图检查。</p></main></html>';
await fs.writeFile(path.join(project,'review/backgrounds.html'),html);
const stateFile=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(stateFile,'utf8'));
state.stage='awaiting-background-approval';
state.revision='sand-safe-background-v1';
state.background={provider:'built-in image_gen',source:generated,file:'03-backgrounds/poster-gold-v1.png',native:{width:m.width,height:m.height},export:'04-png-export/sand-safe-absolute-background.png',exportSize:{width:3200,height:7200},measurements:'02-layouts/generated-background-measurements.json',positionGuide:'review/background-position-guide.png',review:'review/backgrounds.html',prompt:'03-backgrounds/prompts.md'};
state.nextStep='本轮无字底图与文字位置稿待用户确认；确认后按实际原生尺寸测量结果填入矢量文案并完成PNG、SVG与手机预览复检。';
await fs.writeFile(stateFile,JSON.stringify(state,null,2)+'\n');
console.log(JSON.stringify({native:[m.width,m.height],export:[3200,7200],stage:state.stage,plannedMobileRulePx:measured.plannedType.rulePhoneEquivalent,review:'review/backgrounds.html'}));
