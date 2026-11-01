import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const sharp = require(process.argv[2] + '/sharp');
const project = path.resolve(import.meta.dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(project,'source/generation-prompts.json'),'utf8'));
for(const folder of ['assets/illustrations','assets/backgrounds','review','output']) fs.mkdirSync(path.join(project,folder),{recursive:true});
const stats = [];
const composites = [];
for (let i=0; i<manifest.assets.length; i++) {
 const asset = manifest.assets[i];
 const target = path.join(project,asset.file);
 fs.copyFileSync(asset.sourceFile,target);
 const input = fs.readFileSync(target);
 const metadata = await sharp(input).metadata();
 const {data,info} = await sharp(input).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 let transparent=0, semi=0, solid=0, minX=info.width,minY=info.height,maxX=-1,maxY=-1;
 for(let y=0;y<info.height;y++) for(let x=0;x<info.width;x++){
   const a=data[(y*info.width+x)*info.channels+3];
   if(a===0)transparent++;else if(a<240)semi++;else solid++;
   if(a>=128){minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);}
 }
 stats.push({id:asset.id,width:metadata.width,height:metadata.height,hasAlpha:metadata.hasAlpha,alphaPixelCounts:{transparent,semi,solid},visibleBoundsAlpha128:{minX,minY,maxX,maxY}});
 const preview=await sharp(input).resize({width:680,height:460,fit:'contain',background:{r:255,g:251,b:243,alpha:1}}).png().toBuffer();
 composites.push({input:preview,left:40+i*720,top:110});
}
const labels = Buffer.from('<svg width="1480" height="650" xmlns="http://www.w3.org/2000/svg"><rect width="1480" height="650" fill="#fffbf3"/><text x="40" y="48" font-family="Microsoft YaHei UI, sans-serif" font-size="30" font-weight="700" fill="#17334b">兴安电竞 · 英雄联盟｜第一阶段：独立插画</text><text x="40" y="82" font-family="Microsoft YaHei UI, sans-serif" font-size="18" fill="#5b7489">10套 × 完整价目页 / 广告页 / 封面 = 30张 · 当前仅确认人物资产</text><line x1="740" y1="110" x2="740" y2="590" stroke="#dae6eb"/><text x="380" y="605" text-anchor="middle" font-family="Microsoft YaHei UI, sans-serif" font-size="25" font-weight="700" fill="#17334b">01 双人开黑</text><text x="1100" y="605" text-anchor="middle" font-family="Microsoft YaHei UI, sans-serif" font-size="25" font-weight="700" fill="#17334b">02 胜利庆祝</text><text x="740" y="638" text-anchor="middle" font-family="Microsoft YaHei UI, sans-serif" font-size="15" fill="#6c8090">原生 1536 × 1024 透明PNG · 奶白背景用于透明边缘复检</text></svg>');
const overview=await sharp(labels).composite(composites).png().toBuffer();
fs.writeFileSync(path.join(project,'review/illustration-overview-r1.png'),overview);
fs.writeFileSync(path.join(project,'review/illustration-overview-phone-r1.png'),await sharp(overview).resize({width:780}).png().toBuffer());
fs.writeFileSync(path.join(project,'review/illustration-metadata-r1.json'),JSON.stringify(stats,null,2)+'\n','utf8');
console.log(JSON.stringify({project,stats,previews:['review/illustration-overview-r1.png','review/illustration-overview-phone-r1.png']}));
