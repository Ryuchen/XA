import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const sharp=require('C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const root=path.dirname(fileURLToPath(import.meta.url));
const dir=path.join(root,'02-layouts');
await fs.mkdir(dir,{recursive:true});
await fs.mkdir(path.join(root,'03-backgrounds'),{recursive:true});
const C={paper:'#FCFBF7',navy:'#233E50',blue:'#DFEBF0',line:'#BDD0D8',gold:'#CBA066',goldlight:'#F7EAD6',mint:'#4F9F96',mintlight:'#E2F2EC'};
const rect=(x,y,w,h,fill,rx=24,stroke='none',sw=2)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const line=(x1,y1,x2,y2,color=C.line,sw=2,extra='')=>`<path d="M${x1} ${y1}H${x2}" fill="none" stroke="${color}" stroke-width="${sw}" ${extra}/>`;
const txt=(x,y,s,size=30,fill=C.navy,anchor='middle')=>`<text x="${x}" y="${y}" fill="${fill}" font-family="Microsoft YaHei UI, Microsoft YaHei, sans-serif" font-size="${size}" font-weight="600" text-anchor="${anchor}" dominant-baseline="central">${s}</text>`;
const label=(x,y,w,h,s,size=32)=>rect(x,y,w,h,'#FFFFFF',16,'#84A9B8',2)+txt(x+w/2,y+h/2,s,size);
const frame=(w,h,body)=>`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${rect(0,0,w,h,C.paper,0)}${body}</svg>`;
const stripe=(x,y,w,h,fill)=>`<path d="M${x+24} ${y}H${x+w-24}Q${x+w} ${y} ${x+w} ${y+24}V${y+h}H${x}V${y+24}Q${x} ${y} ${x+24} ${y}Z" fill="${fill}"/>`;
function poster(annotate=false,tone='gold'){
 const accent=tone==='gold'?C.gold:C.mint,tint=tone==='gold'?C.goldlight:C.mintlight;
 let p=`<path d="M0 0H1600V12H0Z" fill="${C.navy}"/><path d="M0 0H390V12H0Z" fill="${accent}"/>`;
 p+=`<ellipse cx="1250" cy="295" rx="320" ry="260" fill="#EAF0F0"/>`;
 p+=rect(80,510,465,84,C.navy,20);
 p+=rect(80,680,1440,270,tint,34,accent,3);
 p+=`<path d="M1190 710V920" fill="none" stroke="${accent}" stroke-width="2" stroke-dasharray="8 9"/>`;
 p+=rect(80,1020,1440,1120,'#FFFFFF',28,C.line,2);
 p+=stripe(80,1020,1440,112,C.navy);
 for(let i=0;i<6;i++){
  const y=1132+i*168;
  if(i%2===0)p+=rect(82,y,1436,168,i===0?'#F4F8F9':'#F4F8F9',0);
  if(i===5)p+=rect(82,y,1436,166,tint,0);
  if(i<5)p+=line(110,y+168,1490,y+168,C.line,2);
  p+=`<path d="M719 ${y+69}l16 15-16 15m32-30 16 15-16 15" fill="none" stroke="${accent}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>`;
 }
 p+=rect(80,2230,1440,350,'#FFFFFF',30,C.line,2);
 p+=rect(80,2230,260,350,C.blue,28);
 p+=rect(395,2350,410,72,'#EBF1F4',14);
 p+=rect(910,2350,535,72,tint,14);
 p+=rect(80,2760,1440,862,'#FFFFFF',26,C.line,2);
 const edges=[2760,2950,3070,3210,3330,3490,3622];
 edges.slice(1,-1).forEach(y=>p+=line(105,y,1495,y,C.line,1.6,'stroke-dasharray="5 7"'));
 p+=rect(80,3700,1440,82,'#E8F0F3',20);
 if(annotate){
  p+=label(90,90,650,70,'品牌名称 / 标识',29);
  p+=label(90,225,720,225,'艺术字主标题',64);
  p+=label(850,100,655,510,'蜂医 × 鼠鼠主插画',37);
  p+=txt(312,552,'端游 / 手游 · 绝密 / 机密',28,'#FFFFFF');
  p+=txt(126,810,'工作日专享',35,C.navy,'start');
  p+=txt(670,800,'累计保底 +20%',75);
  p+=txt(670,884,'补偿另计，不参与上浮',26);
  p+=label(1230,712,248,202,'加赠插画',29);
  p+=txt(355,1076,'订单价格',37,'#FFFFFF');
  p+=txt(1130,1076,'累计物资保底',37,'#FFFFFF');
  for(let i=0;i<6;i++){let y=1216+i*168;p+=txt(486,y,'数值',62,C.navy,'end')+txt(514,y,'r',40,C.navy,'start')+txt(1290,y,'数值',70,accent,'end')+txt(1310,y,'w',40,accent,'start');}
  p+=`<path d="M516 1132V2140M1310 1132V2140" stroke="#E2846C" stroke-width="3" stroke-dasharray="9 9"/>`;
  p+=txt(80,2180,'基础保底说明',25,C.navy,'start')+txt(1520,2180,'r = 元 / w = 万',25,C.navy,'end');
  p+=label(110,2280,200,165,'保障插画',28)+txt(210,2500,'猛攻保障',31);
  p+=txt(396,2290,'成功撤离后，满足任意一项',32,C.navy,'start');
  p+=txt(600,2386,'击杀条件',32)+txt(856,2386,'或',32)+txt(1177,2386,'当局物资条件',32);
  p+=txt(396,2495,'当局物资不计入累计保底',42,C.navy,'start');
  p+=txt(80,2697,'下单须知',48,C.navy,'start');
  const titles=['地图和房卡','随行装备','缺项补偿','炸单补偿','结算口径','物资与售后'];
  titles.forEach((s,i)=>{let cy=(edges[i]+edges[i+1])/2;p+=txt(228,cy,s,32)+txt(485,cy,'完整说明区域 · 文案组垂直居中',29,C.navy,'start');});
  p+=txt(800,3741,'确认平台与版本 → 确认地图与档位 → 按规则累计带出',31);
 }
 return frame(1600,3840,p);
}
function cover(annotate=false){
 let p=`<ellipse cx="1810" cy="680" rx="720" ry="650" fill="#EAF1F3"/><path d="M0 1180Q780 920 1490 1140T2560 1090V1440H0Z" fill="#DCE8ED"/>`;
 p+=rect(135,835,850,100,C.navy,25);
 p+=line(140,1090,1130,1090,C.gold,5);
 if(annotate){p+=label(135,120,900,100,'品牌名称 / 标识',46)+label(135,350,1115,360,'艺术字主标题',104)+label(1370,170,1080,1000,'满载撤离主插画',68)+txt(560,885,'端游 · 手游 · 绝密 / 机密',48,'#FFFFFF')+txt(140,1020,'简短副标题 / 核心卖点',40,C.navy,'start')+label(140,1160,950,170,'物资箱 · 撤离路线 · Q版装饰',40);}
 return frame(2560,1440,p);
}
function social(annotate=false){
 let p=`<ellipse cx="1600" cy="350" rx="440" ry="320" fill="#E5EEF1"/>`;
 p+=rect(110,645,2180,290,C.goldlight,34,C.gold,4);
 p+=`<path d="M1930 670V910" stroke="${C.gold}" stroke-width="3" stroke-dasharray="10 10"/>`;
 const cards=[{x:110,y:1020,c:C.gold,t:C.goldlight},{x:1245,y:1020,c:C.mint,t:C.mintlight},{x:110,y:2025,c:C.gold,t:C.goldlight},{x:1245,y:2025,c:C.mint,t:C.mintlight}];
 cards.forEach((c,i)=>{
  p+=rect(c.x,c.y,1045,915,'#FFFFFF',28,C.line,2);
  p+=stripe(c.x,c.y,1045,132,i<2?C.navy:'#536788');
  p+=rect(c.x,c.y+132,1045,95,c.t,0);
  for(let n=0;n<6;n++){let y=c.y+227+n*110;if(n%2===0)p+=rect(c.x+2,y,1041,110,'#F4F8F9',0);if(n<5)p+=line(c.x+24,y+110,c.x+1021,y+110,C.line,2);}
  if(annotate){p+=txt(c.x+522,c.y+66,['端游 · 绝密','端游 · 机密','手游 · 绝密','手游 · 机密'][i],48,'#FFFFFF')+txt(c.x+265,c.y+180,'价格 r',34)+txt(c.x+748,c.y+180,'保底 w',34);for(let n=0;n<6;n++){let cy=c.y+282+n*110;p+=txt(c.x+290,cy,'数值',45,C.navy,'end')+txt(c.x+315,cy,'r',29,C.navy,'start')+txt(c.x+865,cy,'数值',50,c.c,'end')+txt(c.x+884,cy,'w',29,c.c,'start');}}
 });
 if(annotate){p+=label(110,85,1200,95,'品牌名称 / 标识',43)+label(110,245,1230,300,'艺术字标题',94)+label(1460,60,830,520,'满载撤离插画',48)+txt(1020,755,'工作日累计保底 +20%',91)+txt(1020,865,'补偿另计，不参与上浮',36)+label(1980,680,260,220,'加赠插画',34)+txt(1200,3080,'基础保底说明 · 单位说明（不含下单须知）',35);}
 return frame(2400,3200,p);
}
const jobs=[['poster-layout',poster(true)],['poster-template-gold',poster(false)],['poster-template-teal',poster(false,'teal')],['cover-layout',cover(true)],['cover-template',cover(false)],['social-layout',social(true)],['social-template',social(false)]];
for(const [name,svg] of jobs){await fs.writeFile(path.join(dir,name+'.svg'),svg);await sharp(Buffer.from(svg)).png().toFile(path.join(dir,name+'.png'));}
const meta={stage:2,status:'布局已确定，底图待生成及确认',poster:{width:1600,height:3840,title:{x:90,y:225,width:720,height:225},hero:{x:850,y:100,width:655,height:510},promotion:{x:80,y:680,width:1440,height:270},priceTable:{x:80,y:1020,width:1440,height:1120,headerHeight:112,rowHeight:168,rows:6,priceUnitX:514,materialUnitX:1310,rowCenters:[1216,1384,1552,1720,1888,2056]},guarantee:{x:80,y:2230,width:1440,height:350},rules:{x:80,y:2760,width:1440,height:862,rowEdges:[2760,2950,3070,3210,3330,3490,3622]},footer:{x:80,y:3700,width:1440,height:82}},cover:{width:2560,height:1440},social:{width:2400,height:3200,cards:[{x:110,y:1020},{x:1245,y:1020},{x:110,y:2025},{x:1245,y:2025}],cardWidth:1045,cardHeight:915,rowsPerCard:6},notes:['布局坐标为设计网格；正式文案需要在生成底图上重新核对实际边界。','绝密采用暖金，机密采用薄荷青；端游与手游在下一阶段通过平台标识区分。','宣发版含四类完整六档价格；不含下单须知。','阶段2不填正式文案，不制作最终矢量文字。']};
await fs.writeFile(path.join(dir,'layout-spec.json'),JSON.stringify(meta,null,2));
console.log(JSON.stringify({created:jobs.map(([n])=>n+'.png'),dir}));
