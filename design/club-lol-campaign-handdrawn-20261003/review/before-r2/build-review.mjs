import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const root=path.resolve(import.meta.dirname,'..');
const sharp=createRequire(import.meta.url)(process.argv[2]+'/sharp');
const m=JSON.parse(fs.readFileSync(root+'/output/manifest.json','utf8'));
const c=JSON.parse(fs.readFileSync(root+'/content.json','utf8'));
const read=f=>fs.readFileSync(root+'/'+f),write=(f,b)=>fs.writeFileSync(root+'/'+f,b);
const esc=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const svg=(w,h,body)=>Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="${w}" height="${h}" fill="#eef3f1"/>${body}</svg>`);
for(let start=0;start<10;start+=2){
 const pages=m.filter(p=>p.id.endsWith('-price')).slice(start,start+2);
 const h=Math.max(...pages.map(p=>Math.round(p.height*390/p.width)))+82;
 let layers=[],labels='';
 for(let i=0;i<pages.length;i++){
  const p=pages[i],x=20+i*410;layers.push({input:await sharp(read(p.png),{limitInputPixels:false}).resize({width:390}).png().toBuffer(),left:x,top:60});
  labels+=`<text x="${x+195}" y="36" text-anchor="middle" font-family="Microsoft YaHei UI" font-size="20" fill="#17334b">${esc(p.label)} · final-r1</text>`;
 }
 write('review/final/price-board-'+(start+1)+'-'+(start+2)+'.png',await sharp(svg(840,h,labels)).composite(layers).png().toBuffer());
}
for(const type of ['advert','cover'])for(let start=0;start<10;start+=5){
 const pages=m.filter(p=>p.id.endsWith('-'+type)).slice(start,start+5),cw=type==='advert'?390:600,ch=type==='advert'?520:338,cols=type==='advert'?5:3;
 const w=cols*(cw+20)+20,h=Math.ceil(pages.length/cols)*(ch+70)+20;
 let layers=[],labels='';
 for(let i=0;i<pages.length;i++){
  const p=pages[i],x=20+(i%cols)*(cw+20),y=55+Math.floor(i/cols)*(ch+70);
  layers.push({input:await sharp(read(p.png),{limitInputPixels:false}).resize({width:cw}).png().toBuffer(),left:x,top:y});
  labels+=`<text x="${x+cw/2}" y="${y-18}" text-anchor="middle" font-family="Microsoft YaHei UI" font-size="20" fill="#17334b">${esc(p.label)}</text>`;
 }
 write('review/final/'+type+'-board-'+(start+1)+'-'+(start+5)+'.png',await sharp(svg(w,h,labels)).composite(layers).png().toBuffer());
}
const cropSpecs=[['01-solo-duo-price','numeric',{left:450,top:1900,width:2450,height:1250}],['05-lp-price','rules',{left:150,top:5050,width:2900,height:1900}],['06-overseas-price','rules',{left:150,top:7200,width:2900,height:1700}],['10-maintenance-price','numeric',{left:150,top:2350,width:2900,height:1600}],['09-placement-cover','title-art',{left:70,top:100,width:2990,height:1500}]];
for(const [id,label,rect] of cropSpecs)write('review/final/'+id+'-crop-'+label+'.png',await sharp(read('output/png/'+id+'.png'),{limitInputPixels:false}).extract(rect).png().toBuffer());
const links=p=>`<a href="../${p.png}" download>高清PNG</a> · <a href="../${p.editableSVG}" download>可改文字SVG</a> · <a href="../${p.outlinedSVG}" download>转曲SVG</a>`;
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>兴安电竞 · 英雄联盟 · 10套30张</title><style>body{margin:0;background:#fffbf3;color:#17334b;font-family:"Microsoft YaHei UI",sans-serif}main{max-width:1450px;padding:26px;margin:auto}h1{font-size:28px}p{color:#476c86;line-height:1.8}nav{display:flex;gap:10px;flex-wrap:wrap}a{color:#285b85}nav a{background:#e9f1f1;border-radius:12px;padding:9px 13px;text-decoration:none}section{border-top:1px solid #cadbdd;margin-top:28px;padding-top:10px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.card{background:#fff;border:1px solid #cadbdd;border-radius:18px;padding:12px}.card img{width:100%;height:auto;display:block}.card small{display:block;line-height:1.8;color:#476c86;margin:12px 2px}h3{margin:5px 0 12px}details{background:#e9f1f1;padding:16px;border-radius:12px;margin:20px 0}@media(max-width:850px){.grid{grid-template-columns:1fr}main{padding:18px}}</style><main><h1>兴安电竞 · 英雄联盟</h1><p>十套海报，完整价目页＋3:4广告页＋16:9封面，共30张。EZ（伊泽瑞尔）与拉克丝手绘系列，价格与规则按飞书文档及已确认更正制作。</p><nav>${c.sets.map(s=>`<a href="#${s.id}">${s.index}. ${esc(s.name)}</a>`).join('')}</nav><details><summary>文件与使用</summary><p>PNG可直接发布。可改文字SVG保留正文与金额文字对象，艺术标题已转曲；转曲SVG不依赖字体。使用Microsoft YaHei UI、Bahnschrift和Noto Sans SC Black。人物与环境是内嵌手绘位图。最终高清尺寸与美术原始尺寸分别记录于README及manifest。</p></details>${c.sets.map(s=>`<section id="${s.id}"><h2>${s.index}. ${esc(s.name)}</h2><div class="grid">${['price','advert','cover'].map(type=>{const p=m.find(p=>p.id===s.id+'-'+type);return `<article class="card"><h3>${({price:'完整价目页',advert:'广告页',cover:'封面'})[type]}</h3><a href="../${p.png}" target="_blank"><img loading="lazy" src="../${p.preview}" alt="${esc(p.label)}"></a><small>${p.width}×${p.height}px<br>${links(p)}</small></article>`;}).join('')}</div></section>`).join('')}<p><a href="../README.md">交付说明</a> · <a href="../content.json">唯一业务数据源</a> · <a href="visual-qa.md">成图检查记录</a></p></main></html>`;
write('review/index.html',html);
const checks=JSON.parse(read('review/final/layout-checks.json'));
const records=[];
for(const p of m){
 const png=read(p.png),meta=await sharp(png,{limitInputPixels:false}).metadata();
 const outlined=read(p.outlinedSVG).toString(),editable=read(p.editableSVG).toString();
 if(meta.width!==p.width||meta.height!==p.height||/<text\b/.test(outlined)||!/<text\b/.test(editable))throw Error('Export format mismatch: '+p.id);
 if(checks.find(v=>v.id===p.id)?.failures.length)throw Error('Unresolved layout error: '+p.id);
 records.push({id:p.id,width:meta.width,height:meta.height,pngBytes:png.length,outlinedTextPaths:true,editableTextObjects:true,hash:(await import('node:crypto')).createHash('sha256').update(png).digest('hex')});
}
if(m.length!==30||stateScope().join('|')!==m.map(p=>p.id).join('|'))throw Error('Delivery scope mismatch');
function stateScope(){return JSON.parse(read('campaign-state.json')).deliveryScope.pageIds;}
write('review/final/export-checks.json',JSON.stringify(records,null,2));
console.log('Created actual-PNG review boards, detail crops and 30-page viewer; all exports structurally verified.');
