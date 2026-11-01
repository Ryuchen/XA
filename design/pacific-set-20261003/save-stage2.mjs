import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const sharp=require(String.raw`C:\Users\chenh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\sharp`);
const project=String.raw`C:\Users\chenh\Documents\ChatGPT\兴安电竞玩法\环太平洋-四版价目广告封面-20261003`;
const gen=String.raw`C:\Users\chenh\.codex\generated_images\01a0fdaf-d080-7360-8791-13de1e585bc8`;
const taskDir=path.dirname(new URL(import.meta.url).pathname.replace(/^\//,''));
const assets=[
 {id:'poster-gold',label:'绝密 · 暖金价目底图',revision:'r2',key:'gold_r2',file:'exec-e7a2e348-ba02-408f-b9a5-8f86cbd94f60.png',pages:['desktop-absolute','mobile-absolute']},
 {id:'poster-teal',label:'机密 · 薄荷价目底图',revision:'r2',key:'teal_r2',file:'exec-c7f02630-3e81-44f0-bc3f-6133a9d8b7f4.png',pages:['desktop-secret','mobile-secret']},
 {id:'social',label:'四版起步价广告底图',revision:'r2',key:'social_r2',file:'exec-5f259ab1-763a-4d45-b404-f9da55d4e18e.png',pages:['social']},
 {id:'cover',label:'横版封面底图',revision:'r1',key:'cover',file:'exec-45af2193-d1aa-4330-bf95-f41abc9999ea.png',pages:['cover']}
];
const archive=[{id:'poster-gold-r1',key:'gold',file:'exec-c3c401f0-a37c-4653-90fd-a58a0e550b33.png'},{id:'poster-teal-r1',key:'teal',file:'exec-28bb6591-3e32-45a5-85f0-ecaa10bb877b.png'},{id:'social-r1',key:'social',file:'exec-7f9bf05e-9508-4bfa-a63b-8e90e67bc3b9.png'}];
await fs.mkdir(path.join(project,'assets/backgrounds/archive'),{recursive:true});
for(const a of archive){
 await fs.copyFile(path.join(gen,a.file),path.join(project,'assets/backgrounds/archive',a.id+'.png'));
 await fs.copyFile(path.join(taskDir,'prompts',a.key+'.txt'),path.join(project,'source/prompts/background-'+a.id+'.txt'));
}
const records=[];
for(const a of assets){
 const relative=`assets/backgrounds/${a.id}-${a.revision}.png`;
 await fs.copyFile(path.join(gen,a.file),path.join(project,relative));
 await fs.copyFile(path.join(taskDir,'prompts',a.key+'.txt'),path.join(project,`source/prompts/background-${a.id}-${a.revision}.txt`));
 const m=await sharp(path.join(project,relative)).metadata();
 await sharp(path.join(project,relative)).resize({width:390}).png().toFile(path.join(project,`review/${a.id}-mobile390-${a.revision}.png`));
 const {data,info}=await sharp(path.join(project,relative)).removeAlpha().raw().toBuffer({resolveWithObject:true});
 let bands=[],start=null;
 for(let y=0;y<info.height;y++){
  const i=(y*info.width+Math.round(info.width/2))*info.channels;
  const isNavy=data[i]<85&&data[i+1]<135&&data[i+2]<150;
  if(isNavy&&start===null)start=y;
  if((!isNavy||y===info.height-1)&&start!==null){const end=isNavy?y:y-1;if(end-start>10)bands.push([start,end]);start=null;}
 }
 records.push({...a,path:relative,nativeWidth:m.width,nativeHeight:m.height,centerNavyBands:bands,approved:false});
}
await sharp(path.join(project,records[0].path)).extract({left:25,top:1320,width:674,height:710}).png().toFile(path.join(project,'review/poster-gold-rules-detail-r2.png'));
await sharp(path.join(project,records[1].path)).extract({left:25,top:1320,width:674,height:710}).png().toFile(path.join(project,'review/poster-teal-rules-detail-r2.png'));
await sharp(path.join(project,records[2].path)).extract({left:40,top:60,width:505,height:250}).png().toFile(path.join(project,'review/social-title-detail-r2.png'));
await sharp(path.join(project,records[3].path)).extract({left:58,top:91,width:753,height:461}).png().toFile(path.join(project,'review/cover-title-detail-r1.png'));
const sheetW=1240,sheetH=930;
const sheetBase=Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${sheetW}" height="${sheetH}"><rect width="100%" height="100%" fill="#fbf8ee"/><g fill="#23465b" font-family="Arial,sans-serif" font-size="22"><text x="26" y="34">ABSOLUTE · GOLD</text><text x="318" y="34">CONFIDENTIAL · MINT</text><text x="623" y="34">SOCIAL AD</text><text x="623" y="611">COVER</text></g></svg>`);
const composites=[];
for(const [idx,w,x,y] of [[0,274,26,50],[1,274,318,50],[2,390,623,50],[3,580,623,630]])composites.push({input:await sharp(path.join(project,records[idx].path)).resize({width:w}).png().toBuffer(),left:x,top:y});
await sharp(sheetBase).composite(composites).png().toFile(path.join(project,'review/stage2-backgrounds-contact-sheet-r2.png'));
const statePath=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(statePath,'utf8'));
state.stage='backgrounds-awaiting-confirmation';state.revision='stage2-r2';
state.artwork.backgrounds=records;
state.pendingApprovals=['绝密r2、机密r2、广告r2、封面r1四类无字底图'];
state.backgroundApprovalBasis=null;
state.completedPages=[];
await fs.writeFile(statePath,JSON.stringify(state,null,2));
await fs.writeFile(path.join(project,'review/backgrounds-native-metadata.json'),JSON.stringify(records,null,2));
await fs.copyFile(new URL(import.meta.url),path.join(project,'source/save-stage2.mjs'));
console.log(JSON.stringify(records.map(({id,nativeWidth,nativeHeight,centerNavyBands,pages})=>({id,nativeWidth,nativeHeight,centerNavyBands,pages})),null,2));
