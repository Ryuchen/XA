import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const project=path.resolve(process.argv[2] || path.join(path.dirname(fileURLToPath(import.meta.url)),'..'));
const require=createRequire(path.join(path.resolve(process.argv[3] || path.join(path.dirname(process.execPath),'../node_modules')),'package.json'));
const sharp=require('sharp');
const stateFile=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(stateFile,'utf8'));
const content=JSON.parse(await fs.readFile(path.join(project,'content.json'),'utf8'));
state.approvals.illustration={status:'approved',basis:'2026-10-03 用户在本轮新独立插画确认稿后回复：继续。',file:state.illustration.file};
state.illustrationQA.status='passed-and-user-approved';
state.stage='background-layout-in-progress';
state.nextStep='按本轮四张范围完成结构稿和四张全新无字底图，交付底图确认。';
await fs.writeFile(stateFile,JSON.stringify(state,null,2)+'\n');
const expected=['desktop-absolute','mobile-absolute','combined-advert','combined-cover'];
if(JSON.stringify(expected)!==JSON.stringify(state.deliveryScope.pageIds))throw Error('Scope mismatch');
if(content.prices.length!==5)throw Error('Must plan all five tiers');
const hero=await fs.readFile(path.join(project,state.illustration.file));
const imageHref='data:image/png;base64,'+hero.toString('base64');
const box=(x,y,width,height)=>({x,y,width,height});
const plans=[];
for(const [id,label,accent] of [['desktop-absolute','端游绝密价目','#ead8b7'],['mobile-absolute','手游绝密价目','#d9e5e3']]){
plans.push({id,label,platforms:[id.startsWith('desktop')?'desktop':'mobile'],canvas:{width:1024,height:2304},export:{width:3200,height:7200},header:{container:box(48,44,928,292),copy:box(66,70,550,238),hero:box(626,72,360,240)},conditions:{container:box(48,370,928,528),heading:box(74,392,876,90),cards:[box(74,510,264,232),box(380,510,264,232),box(686,510,264,232)],conjunctions:[{cx:359,cy:626},{cx:665,cy:626}],connectorY:779,result:box(226,810,572,60),copySafe:[box(94,542,224,172),box(400,542,224,172),box(706,542,224,172)]},price:{container:box(48,932,928,492),header:box(56,940,912,64),rowBounds:Array.from({length:5},(_,i)=>box(64,1004+i*82,896,82)),columnFractions:[0.52,0.48],rows:5},reward:{container:box(48,1456,928,270),copy:box(78,1480,692,220),decor:box(806,1490,138,192)},notices:{container:box(48,1756,928,492),heading:box(80,1774,864,54),groups:[box(80,1842,864,60),box(80,1920,864,104),box(80,2040,864,104),box(80,2160,864,64)],align:'left',plannedBodySize:38,phoneEquivalentPx:14.47},footer:box(48,2256,928,34),accent});
}
plans.push({id:'combined-advert',label:'双平台合并广告',platforms:['desktop','mobile'],canvas:{width:1200,height:1600},export:{width:2400,height:3200},header:{container:box(48,32,1104,250),copy:box(68,52,698,216),hero:box(782,36,372,248)},conditions:{container:box(48,304,1104,508),heading:box(74,324,1052,78),cards:[box(72,430,300,228),box(450,430,300,228),box(828,430,300,228)],conjunctions:[{cx:411,cy:544},{cx:789,cy:544}],connectorY:695,result:box(264,730,672,60),copySafe:[box(92,452,260,186),box(470,452,260,186),box(848,452,260,186)]},price:{container:box(48,846,1104,484),header:box(56,854,1088,64),rowBounds:Array.from({length:5},(_,i)=>box(64,918+i*78,1072,78)),columnFractions:[0.40,0.30,0.30],rows:5},reward:{container:box(48,1360,1104,188),copy:box(166,1384,868,138),decorLeft:box(66,1390,80,126),decorRight:box(1050,1390,78,126)},accent:'#ead8b7',showNotices:false});
plans.push({id:'combined-cover',label:'16:9共用封面',platforms:['desktop','mobile'],canvas:{width:1600,height:900},export:{width:3200,height:1800},header:{container:box(52,36,944,264),copy:box(72,58,900,224),hero:box(1012,268,556,371)},conditions:{container:box(52,312,944,412),heading:box(72,330,904,54),cards:[box(68,406,270,202),box(388,406,270,202),box(708,406,270,202)],conjunctions:[{cx:363,cy:508},{cx:683,cy:508}],connectorY:637,result:box(224,665,600,42),copySafe:[box(88,426,230,162),box(408,426,230,162),box(728,426,230,162)]},reward:{container:box(52,744,944,110),copy:box(74,754,900,90)},accent:'#ead8b7',showNotices:false,showEntryPrice:false});
const requests=[],labelsByPage=new Map();
const label=(id,text,cx,cy,size=36,fill='#24455f')=>{const key='guide-'+requests.length;requests.push({id:key,text,font:'Microsoft YaHei UI',style:0});return {id:key,text,cx,cy,size,fill};};
const rect=(b,fill,stroke='#516373',radius=22,width=2)=>'<rect x="'+b.x+'" y="'+b.y+'" width="'+b.width+'" height="'+b.height+'" rx="'+radius+'" fill="'+fill+'" stroke="'+stroke+'" stroke-width="'+width+'"/>';
const line=(x1,y1,x2,y2,stroke='#6b9389',width=4)=>'<path d="M'+x1+' '+y1+'L'+x2+' '+y2+'" fill="none" stroke="'+stroke+'" stroke-width="'+width+'" stroke-linecap="round"/>';
for(const p of plans){
const w=p.canvas.width,h=p.canvas.height;
let body='<rect width="'+w+'" height="'+h+'" fill="#f3e7d0"/>';
body+=rect(p.header.container,'#fff8e9','#e7dbc5',24,1);
body+='<image x="'+p.header.hero.x+'" y="'+p.header.hero.y+'" width="'+p.header.hero.width+'" height="'+p.header.hero.height+'" href="'+imageHref+'"/>';
body+=rect(p.conditions.container,'#fffaf0','#c9b792',26,3);
for(const c of p.conditions.cards)body+=rect(c,'#edf3eb','#93b4a7',20,3);
for(const c of p.conditions.conjunctions)body+='<circle cx="'+c.cx+'" cy="'+c.cy+'" r="18" fill="#fffaf0" stroke="#93b4a7" stroke-width="2"/>';
const cy=p.conditions.connectorY;
const centers=p.conditions.cards.map(c=>c.x+c.width/2);
for(const c of p.conditions.cards)body+=line(c.x+c.width/2,c.y+c.height+8,c.x+c.width/2,cy);
body+=line(centers[0],cy,centers[2],cy)+line((centers[0]+centers[2])/2,cy,(centers[0]+centers[2])/2,p.conditions.result.y);
body+=rect(p.conditions.result,'#264b52','#264b52',20,1);
if(p.price){body+=rect(p.price.container,'#fffaf1','#c9b792',24,2);body+=rect(p.price.header,'#233e53','#233e53',10,1);for(let i=0;i<p.price.rows;i++){if(i>=3)body+=rect(p.price.rowBounds[i],i===4?'#f0e2bd':'#f8edcf','#f8edcf',0,0);if(i>0)body+=line(p.price.rowBounds[i].x+12,p.price.rowBounds[i].y,p.price.rowBounds[i].x+p.price.rowBounds[i].width-12,p.price.rowBounds[i].y,'#ded8cb',1);}const total=p.price.rowBounds[0].width;let cum=p.price.rowBounds[0].x;for(const f of p.price.columnFractions.slice(0,-1)){cum+=total*f;body+=line(cum,p.price.header.y+12,cum,p.price.container.y+p.price.container.height-14,'#cdd6d4',1);}}
body+=rect(p.reward.container,'#fff0ca','#c9aa67',24,2);
if(p.notices){body+=rect(p.notices.container,'#fffaf0','#c9b792',24,2);for(const g of p.notices.groups.slice(0,-1))body+=line(g.x,g.y+g.height+9,g.x+g.width,g.y+g.height+9,'#ddd4c5',1);}
const svg='<svg xmlns="http://www.w3.org/2000/svg" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'">'+body+'</svg>';
await fs.writeFile(path.join(project,'02-layouts',p.id+'-text-free-guide.svg'),svg);
await fs.writeFile(path.join(project,'02-layouts',p.id+'-text-free-guide.png'),await sharp(Buffer.from(svg)).png().toBuffer());
const labs=[];
const hc=p.header.copy;labs.push(label(p.id,'品牌 / 主标题 / 平台',hc.x+hc.width/2,hc.y+hc.height/2,p.id==='combined-cover'?54:40));
const ch=p.conditions.heading;labs.push(label(p.id,'三项缺一不可（重点）',ch.x+ch.width/2,ch.y+ch.height/2,p.id==='combined-cover'?44:42));
p.conditions.cards.forEach((c,i)=>labs.push(label(p.id,['沙色保险目标','常规大保险目标','带出物资目标'][i],c.x+c.width/2,c.y+c.height/2,p.id==='combined-cover'?30:32)));
p.conditions.conjunctions.forEach(c=>labs.push(label(p.id,'且',c.cx,c.cy,24)));
const rr=p.conditions.result;labs.push(label(p.id,'三项全满足 ＋ 成功撤离',rr.x+rr.width/2,rr.y+rr.height/2,p.id==='combined-cover'?28:34,'#fff9ea'));
if(p.price){const ph=p.price.header;labs.push(label(p.id,p.id==='combined-advert'?'五档价目 · 端游 / 手游':'五档价目 · '+(p.id.startsWith('desktop')?'端游':'手游'),ph.x+ph.width/2,ph.y+ph.height/2,38,'#fff9ea'));for(let i=0;i<p.price.rows;i++){const row=p.price.rowBounds[i];labs.push(label(p.id,'第'+(i+1)+'档价目',row.x+row.width/2,row.y+row.height/2,32));}}
const rc=p.reward.copy;labs.push(label(p.id,'许愿二选一 / 条件奖励',rc.x+rc.width/2,rc.y+rc.height/2,p.id==='combined-cover'?38:40));
if(p.notices){const nt=p.notices.heading;labs.push(label(p.id,'下单须知（左对齐）',nt.x+nt.width/2,nt.y+nt.height/2,40));p.notices.groups.forEach((g,i)=>labs.push(label(p.id,['达标口径','炸单与连续计数','代摸须先询问板板','赠送保底补打'][i],g.x+g.width/2,g.y+g.height/2,34)));}
labelsByPage.set(p.id,{svg,labs});
}
await fs.writeFile(path.join(project,'02-layouts/layout-zones.json'),JSON.stringify({revision:'redesign-r1',basis:'本轮真实文案量：价目每平台五档、三项AND条件、许愿二选一奖励、完整海报四组须知；广告及封面不放须知。',pages:plans},null,2)+'\n');
await fs.writeFile(path.join(project,'02-layouts/guide-font-requests.json'),JSON.stringify(requests));
const skillScript=path.join(process.env.CODEX_HOME || path.join(process.env.USERPROFILE,'.codex'),'skills/xingan-gameplay-posters/scripts/outline-text.ps1');
let outline=await fs.readFile(skillScript,'utf8');outline=outline.replace('Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8','[System.IO.File]::ReadAllText($InputFile)');
const localOutline=path.join(project,'source/outline-guide.ps1');await fs.writeFile(localOutline,outline);
const input=path.join(project,'02-layouts/guide-font-requests.json'),output=path.join(project,'02-layouts/guide-font-outlines.json');
const command="& ([scriptblock]::Create([System.IO.File]::ReadAllText('"+localOutline+"'))) -InputFile '"+input+"' -OutputFile '"+output+"'";
console.log(execFileSync('powershell.exe',['-NoProfile','-Command',command],{encoding:'utf8'}).trim());
const glyphs=JSON.parse(await fs.readFile(output,'utf8'));
for(const p of plans){
const entry=labelsByPage.get(p.id);let overlay='';
for(const e of entry.labs){const g=glyphs[e.id],sc=e.size/100;const dx=e.cx-g.width*sc/2-g.x*sc,dy=e.cy-g.height*sc/2-g.y*sc;overlay+='<g fill="'+e.fill+'" transform="translate('+dx+' '+dy+') scale('+sc+')"><path d="'+g.d+'"/></g>';}
const labelled=entry.svg.replace('</svg>',overlay+'</svg>');
await fs.writeFile(path.join(project,'02-layouts',p.id+'-structure.svg'),labelled);
await fs.writeFile(path.join(project,'02-layouts',p.id+'-structure.png'),await sharp(Buffer.from(labelled)).png().toBuffer());
}
state.layout={zones:'02-layouts/layout-zones.json',guideCount:4,guidePageIds:expected,phoneBodyPx:14.47};
state.stage='generating-backgrounds';
await fs.writeFile(stateFile,JSON.stringify(state,null,2)+'\n');
console.log(JSON.stringify({pages:plans.map(p=>({id:p.id,canvas:p.canvas,guide:'02-layouts/'+p.id+'-text-free-guide.png'})),stage:state.stage}));
