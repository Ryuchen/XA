import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(path.join(path.resolve(process.argv[3] || path.join(path.dirname(process.execPath),'../node_modules')),'package.json'));
const sharp=require('sharp');
const project=path.resolve(process.argv[2] || path.join(path.dirname(fileURLToPath(import.meta.url)),'..'));
const hero=path.join(project,'01-illustrations/sand-safe-medic-mouse-redesign-r1.png');
const heroBuffer=await fs.readFile(hero);
const metadata=await sharp(heroBuffer).metadata();
const {data,info}=await sharp(heroBuffer).ensureAlpha().raw().toBuffer({resolveWithObject:true});
let min=255,max=0,transparentPixels=0,nearOpaquePixels=0;
const occupied={left:info.width,top:info.height,right:0,bottom:0};
for(let y=0;y<info.height;y++) for(let x=0;x<info.width;x++){
const a=data[(y*info.width+x)*4+3];min=Math.min(min,a);max=Math.max(max,a);
if(a===0)transparentPixels++;
if(a>240)nearOpaquePixels++;
if(a>128){occupied.left=Math.min(occupied.left,x);occupied.top=Math.min(occupied.top,y);occupied.right=Math.max(occupied.right,x);occupied.bottom=Math.max(occupied.bottom,y);}
}
const corners=[[0,0],[info.width-1,0],[0,info.height-1],[info.width-1,info.height-1]].map(([x,y])=>data[(y*info.width+x)*4+3]);
const imageMeta={width:metadata.width,height:metadata.height,format:metadata.format,hasAlpha:metadata.hasAlpha,alpha:{min,max,transparentPixels,nearOpaquePixels,totalPixels:info.width*info.height,corners},opaqueBounds:occupied};
await fs.writeFile(path.join(project,'01-illustrations/image-metadata.json'),JSON.stringify(imageMeta,null,2)+'\n');
await fs.writeFile(path.join(project,'review/illustration-ivory-preview.png'),await sharp(heroBuffer).flatten({background:'#fff8e9'}).resize({width:1200}).png().toBuffer());
await fs.writeFile(path.join(project,'review/illustration-phone.png'),await sharp(heroBuffer).flatten({background:'#e7ceb0'}).resize({width:390}).png().toBuffer());
const state=JSON.parse(await fs.readFile(path.join(project,'campaign-state.json'),'utf8'));
state.stage='awaiting-illustration-approval';
state.illustration={provider:'built-in image_gen',source:'C:/Users/chenh/.codex/generated_images/01a100c5-2566-7302-9896-ceb05845e9bf/exec-757bb785-6566-4dd7-b51b-4c0200436601.png',file:'01-illustrations/sand-safe-medic-mouse-redesign-r1.png',...imageMeta,prompt:'01-illustrations/prompt.txt',review:'review/illustration.html',reusedPriorRaster:false};
state.approvals.illustration.file=state.illustration.file;
state.nextStep='等待用户确认本轮新独立插画；随后按四张范围制作结构稿和全新无字底图，确认底图后再矢量填字。';
await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2)+'\n');
const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>沙保连连看 · 第一步插画确认</title><style>body{margin:0;background:#f1e6d4;color:#20344a;font:16px/1.7 "Microsoft YaHei UI",sans-serif}main{max-width:1080px;margin:40px auto;padding:32px;background:#fffaf0;border-radius:24px}h1{font-size:30px;margin:0 0 12px}p{margin:12px 0}img{width:100%;height:auto;border-radius:18px}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{border-bottom:1px solid #dfd6c7;padding:12px;text-align:left}th{background:#20344a;color:white}a{color:#286b71}small{color:#566b76}.tag{display:inline-block;background:#d6eee5;border-radius:30px;padding:5px 16px}</style><main><span class="tag">第一步 · 新插画待确认</span><h1>兴安电竞 · 沙保连连看</h1><p>全新蜂医与灰鼠情景；两类保险区分，三枚相连的勾选呼应“三项缺一不可”。正式条件与数字会在后续步骤用矢量文字呈现。</p><img src="illustration-ivory-preview.png" alt="全新蜂医灰鼠插画在奶白底上的预览"><p><a href="../01-illustrations/sand-safe-medic-mouse-redesign-r1.png">查看透明原图</a> · <a href="illustration-phone.png">390px小尺寸预览</a> · <a href="../01-illustrations/prompt.txt">本轮生成提示词</a></p><table><tr><th>本轮修改</th><th>端游</th><th>手游</th></tr><tr><td>连续两次</td><td>688r</td><td>588r</td></tr><tr><td>连续三次</td><td>1288r</td><td>888r</td></tr><tr><td>单局带出物资</td><td>＞888w</td><td>＞788w</td></tr></table><p><strong>同一局：摸2个沙色保险 ＋ 摸4个常规大保险 ＋ 物资门槛，三项全部满足，并成功撤离才算达标。</strong></p><p>本轮四张范围：端游价目、手游价目、合并广告页、16:9共用封面。插画确认后再制作结构稿和四页无字底图；底图确认后进行矢量填字与逐张验收。</p><small>插画原生 '+metadata.width+'×'+metadata.height+'，透明PNG。此页仅为插画阶段确认稿，正式海报尚未制作。</small></main></html>';
await fs.writeFile(path.join(project,'review/illustration.html'),html);
await fs.writeFile(path.join(project,'review/illustration-qa.md'),'# 第一步插画验收 · redesign-r1\n\n- 输入：本轮新生成独立插画，未复用旧海报画面。\n- 原生尺寸：'+metadata.width+'×'+metadata.height+'；alpha最小'+min+'，最大'+max+'，四角'+corners.join('/')+'。\n- 已生成奶白底整图预览与暖沙底390px预览，须打开实际预览检查角色、道具及轮廓。\n- 插画仅提供视觉情景，正式次数、物资阈值及且关系以后续矢量文字为准。\n- 下一门禁：用户确认本轮新插画后，才能进入无字底图阶段。\n');
console.log(JSON.stringify({project,metadata:imageMeta,review:'review/illustration.html',stage:state.stage}));
