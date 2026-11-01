import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const root=path.dirname(fileURLToPath(import.meta.url));
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const metricsPath=path.join(root,'assets/typography/cover-centering.json');
try{
 const cache=JSON.parse(await fs.readFile(path.join(root,'.cache/font-outlines.json'),'utf8'));
 const content=JSON.parse(await fs.readFile(path.join(root,'content.json'),'utf8'));
 const entries=[[content.brand,'Microsoft YaHei UI',1],...[...content.title].map(c=>[c,'Noto Sans SC Black',0])];
 const metrics={};
 for(const [text,font,style] of entries){
  const key=createHash('sha256').update(JSON.stringify([text,font,style])).digest('hex');
  if(!cache[key])throw Error('Missing outline metrics: '+text);
  metrics[key]={text,font,style,width:cache[key].width};
 }
 await fs.mkdir(path.dirname(metricsPath),{recursive:true});
 await fs.writeFile(metricsPath,JSON.stringify(metrics,null,2));
}catch(error){
 if(error.code!=='ENOENT')throw error;
 await fs.access(metricsPath);
}
const original=await sharp(path.join(root,'01-illustrations/tazhu-sand-background-v1.png')).resize(809,1942,{fit:'fill'}).png().toBuffer();
const header=await sharp(original).extract({left:0,top:0,width:809,height:390}).png().toBuffer();
const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1672" height="941" viewBox="0 0 1672 941"><defs><linearGradient id="sand" x2="0" y2="1"><stop stop-color="#FFF0D3"/><stop offset="1" stop-color="#F8E1B6"/></linearGradient></defs><rect width="1672" height="941" fill="url(#sand)"/><image href="data:image/png;base64,${header.toString('base64')}" x="0" y="0" width="1672" height="806.03"/><path d="M80 767 Q310 762 570 767" fill="none" stroke="#C8AE7C" stroke-width="2"/><path d="M80 927H1592" fill="none" stroke="#C8AE7C" stroke-width="1.6" opacity=".55"/></svg>`;
await fs.writeFile(path.join(root,'02-layouts/cover-tazhu-sand-v1.svg'),svg);
await sharp(Buffer.from(svg)).png().toFile(path.join(root,'assets/backgrounds/cover-tazhu-sand-v1.png'));
console.log('Created 16:9 cover background from the existing sand artwork.');
