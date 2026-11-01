import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';

const [project, modules, generated] = process.argv.slice(2);
if (!project || !modules || !generated) throw new Error('Usage: node prepare-illustration.mjs project modules generated');
const require = createRequire(path.join(modules, '..', 'sand-safe-art-loader.cjs'));
const sharp = require('sharp');
const file = path.join(project, '01-illustrations/sand-safe-medic-mouse-v1.png');
await fs.mkdir(path.dirname(file), {recursive:true});
await fs.mkdir(path.join(project, 'review'), {recursive:true});
await fs.copyFile(generated, file);
const metadata = await sharp(file).metadata();
const {data,info} = await sharp(file).ensureAlpha().raw().toBuffer({resolveWithObject:true});
let min = 255, max = 0, transparent = 0, nearOpaque = 0;
for (let i=3; i<data.length; i+=4) {
  const a=data[i]; min=Math.min(min,a); max=Math.max(max,a);
  if(a===0)transparent++;
  if(a>=240)nearOpaque++;
}
const alpha={min,max,transparentPixels:transparent,nearOpaquePixels:nearOpaque,totalPixels:info.width*info.height,corners:[data[3],data[(info.width-1)*4+3],data[(info.height-1)*info.width*4+3],data[data.length-1]]};
if (!metadata.hasAlpha || min!==0 || alpha.corners.some(v=>v!==0)) throw new Error('Transparent background verification failed.');
await sharp(file).flatten({background:'#f8f5ec'}).resize({width:1200}).png().toFile(path.join(project,'review/illustration-overview.png'));
await sharp(file).flatten({background:'#f8f5ec'}).resize({width:390}).png().toFile(path.join(project,'review/illustration-phone.png'));
const content=JSON.parse(await fs.readFile(path.join(project,'content.json'),'utf8'));
const expected=[[218,168],[428,328],[628,488],[588,488],[988,888]];
if (content.prices.length!==5 || content.prices.some((r,i)=>r.desktop!==expected[i][0]||r.mobile!==expected[i][1])) throw new Error('Price transcription check failed.');
if(content.objectives.loot.operator!=='>' || content.objectives.loot.desktop!==888 || content.objectives.loot.mobile!==688 || content.wishReward.guarantee!==1688 || content.notices[1].loot!==300 || content.notices[1].operator!=='<') throw new Error('Condition transcription check failed.');
const stateFile=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(stateFile,'utf8'));
state.stage='awaiting-illustration-approval';
state.illustration={provider:'built-in image_gen',source:generated,file:'01-illustrations/sand-safe-medic-mouse-v1.png',width:metadata.width,height:metadata.height,hasAlpha:metadata.hasAlpha,alpha,prompt:'01-illustrations/prompts.md',review:'review/illustration-overview.png'};
state.dataAudit={prices:'10/10 checked against original user text',reward:'1688w checked',successConditions:'single successful extraction AND all 3 objectives; >888w desktop, >688w mobile',brokenOrder:'successful extraction AND <300w; consecutive count interrupted, no added guarantee'};
await fs.writeFile(stateFile,JSON.stringify(state,null,2)+'\n');
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>沙保连连看 · 第一步主题插画</title><style>body{margin:0;background:#f8f5ec;color:#203f57;font:17px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1150px;margin:auto;padding:32px}img{display:block;width:100%;height:auto;border-radius:20px}h1{font-size:27px}a{color:#326677}.meta{padding:18px 0}p{max-width:850px}</style><main><h1>兴安电竞 · 沙保连连看</h1><p>第一步：主题插画。沿用已确认的男性蜂医与灰鼠，新场景为开沙色保险和常规大保险。本页为插画阶段预览，还未填入正式海报文案。</p><img src="illustration-overview.png" alt="蜂医与灰鼠开沙色保险，后方为四个常规大保险"><div class="meta"><a download href="../01-illustrations/sand-safe-medic-mouse-v1.png">透明PNG原图</a> · <a href="../01-illustrations/prompts.md">完整提示词</a> · <a href="../content.json">本轮价格与玩法数据</a> · <a href="../02-layouts/layout-brief.md">海报结构稿</a></div><p>下一阶段计划：一张完整竖版绝密价格海报，端游、手游五档并列表，下方展示许愿奖励、单局三项目标和四项下单须知。</p></main></html>';
await fs.writeFile(path.join(project,'review/illustration.html'),html);
console.log(JSON.stringify({file,width:metadata.width,height:metadata.height,hasAlpha:metadata.hasAlpha,alpha,stage:state.stage},null,2));
