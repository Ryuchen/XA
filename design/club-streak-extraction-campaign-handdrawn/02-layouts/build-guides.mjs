import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.dirname(dir);
const C={paper:'#FFFCF3',ink:'#203F53',edge:'#ADC5D2',blue:'#EEF5F8',gold:'#D4A153',cream:'#FBF0DB',mint:'#E2F3ED'};
const R=(x,y,w,h,fill,rx=22,stroke='none')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="2.5"/>`;
const tx=(x,y,s,size=50,fill=C.ink,anchor='start')=>`<text x="${x}" y="${y}" font-family="Microsoft YaHei UI" font-weight="700" font-size="${size}" fill="${fill}" text-anchor="${anchor}" dominant-baseline="central">${s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}</text>`;
const ln=(x1,y1,x2,y2)=>`<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="${C.edge}" stroke-width="2"/>`;
const save=async(name,w,h,base,overlay)=>{for(const [suffix,o] of [['template',''],['layout',overlay]]){const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">${R(0,0,w,h,C.paper,0)}${base}${o}</svg>`;await fs.writeFile(path.join(dir,`${name}-${suffix}.svg`),svg);await sharp(Buffer.from(svg)).png().toFile(path.join(dir,`${name}-${suffix}.png`));}};
const artData=(await fs.readFile(path.join(root,'01-illustrations/streak-medic-mouse-v1.png'))).toString('base64');
const art=(x,y,w,h)=>`<image href="data:image/png;base64,${artData}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>`;
let p=R(90,480,590,82,C.ink)+R(90,605,1420,160,C.cream,26,C.gold),a=art(785,25,735,490);
a+=tx(385,98,'兴安电竞',48,C.ink,'middle')+tx(385,240,'连续',140,C.ink,'middle')+tx(385,394,'撤离单',135,C.ink,'middle')+tx(385,521,'平台 · 模式',49,'#FFFCF3','middle');
a+=tx(910,653,'结单两项必须同时满足',53,C.ink,'middle')+tx(910,717,'连撤次数达标 ＋ 打满基础保底',53,C.ink,'middle')+tx(210,685,'两项',38,C.ink,'middle');
const mapTops=[810,1200,1590],mapGroups=[];
for(const [i,y] of mapTops.entries()){
 p+=R(90,y,1420,360,'#FFFFFF',24,C.edge)+R(90,y,1420,64,C.ink,22)+R(90,y+32,1420,32,C.ink,0)+R(93,y+64,1414,56,C.blue,0);
 a+=tx(800,y+32,'地图 '+(i+1),52,'#FFFCF3','middle');
 const cols=[90,520,1080,1510],rows=[y+120,y+200,y+280,y+360];
 ['订单价格','基础保底','连撤要求'].forEach((v,j)=>a+=tx((cols[j]+cols[j+1])/2,y+92,v,44,C.ink,'middle'));
 for(let r=0;r<3;r++){p+=R(93,rows[r],1414,80,r===2?C.cream:r%2===0?C.blue:'#FFFFFF',0);if(r)p+=ln(100,rows[r],1500,rows[r]);a+=tx(405,rows[r]+40,'价格 r',67,C.ink,'middle')+tx(835,rows[r]+40,'保底 w',67,C.ink,'middle')+tx(1295,rows[r]+40,'连撤 '+(r+2)+' 把',59,C.ink,'middle');}
 for(const x of cols.slice(1,-1))p+=ln(x,y+64,x,y+360);
 mapGroups.push({header:[90,y,1420,64],tableHeader:[90,y+64,1420,56],rowEdges:rows,colEdges:cols,priceUnitX:405,lootUnitX:960});
}
p+=R(90,2000,1420,62,C.ink)+R(90,2095,1420,330,'#FFFFFF',25,C.edge)+R(93,2098,225,324,C.blue,20)+R(90,2450,1420,128,C.cream,24,C.gold);
a+=tx(800,2031,'有效连撤 · 两项均满足',48,'#FFFCF3','middle')+tx(205,2220,'有效',52,C.ink,'middle')+tx(205,2290,'连撤',52,C.ink,'middle');
['每把成功撤离时','击杀 ≥4 人 且 物资 ≥模式门槛','低于门槛：不计有效连撤','也不打断连撤；当局物资归板板'].forEach((s,i)=>a+=tx(920,2160+i*66,s,58,C.ink,'middle'));
a+=tx(885,2488,'撤离失败 → 连撤次数重新计算',57,C.ink,'middle')+tx(885,2550,'已撤离物资继续累计 · 保底无上限',55,C.ink,'middle');
p+=R(90,2610,1420,62,C.ink);a+=tx(800,2641,'下单须知 · 粗体大字号',49,'#FFFCF3','middle');
const noteLabels=['房卡保障','随行装备','缺项补偿','炸单补偿','结算口径','物资说明','打手保障','撤离点'],noteHeights=[310,170,310,240,240,240,170,240],notes=[];
let y=2700;
for(let i=0;i<noteLabels.length;i++){
 const h=noteHeights[i];p+=R(90,y,1420,h,'#FFFFFF',22,C.edge)+R(93,y+3,110,h-6,C.blue,18)+ln(435,y+20,435,y+h-20);
 a+=tx(317,y+h/2,noteLabels[i],50,C.ink,'middle');
 const count=[4,2,4,3,3,3,2,3][i],cy=y+h/2;
 for(let j=0;j<count;j++)a+=tx(465,cy+(j-(count-1)/2)*70,j===0?'完整规则等义精简，正文60':'可用文字区内整组垂直居中',60);
 notes.push({id:noteLabels[i],bounds:[90,y,1420,h],iconSafe:[103,y+15,90,h-30],labelSafe:[210,y+20,215,h-40],bodySafe:[465,y+15,1025,h-30],lines:count,minBodySize:60});y+=h+10;
}
p+=R(350,4710,900,65,C.blue,30);a+=tx(800,4742,'下单前确认地图及订单规则',36,C.ink,'middle');
await save('poster',1600,4800,p,a);

let cover=R(100,1080,1150,140,C.ink,65),ca=art(1330,70,1150,1130);
ca+=tx(680,175,'兴安电竞',61,C.ink,'middle')+tx(680,440,'连续',190,C.ink,'middle')+tx(680,745,'撤离单',187,C.ink,'middle')+tx(675,1150,'稳定连撤 · 物资保底',57,'#FFFCF3','middle')+tx(680,1280,'端游 / 手游 · 绝密 / 机密',41,C.ink,'middle');
await save('cover',2560,1440,cover,ca);

let s=R(90,450,1420,86,C.blue,24,C.edge),sa=art(790,5,720,455);
sa+=tx(370,70,'兴安电竞',42,C.ink,'middle')+tx(370,210,'连续',114,C.ink,'middle')+tx(370,356,'撤离单',110,C.ink,'middle')+tx(800,493,'平台专属 · 两种模式共18档',48,C.ink,'middle');
const socialCards=[];
for(const [ci,x] of [[0,90],[1,830]]){
 const mode=ci===0?C.cream:C.mint,w=680,top=580,col=[x,x+220,x+495,x+w];
 s+=R(x,top,w,1236,'#FFFFFF',24,C.edge)+R(x,top,w,72,mode,22)+R(x,top+72,w,64,C.ink,0);
 sa+=tx(x+w/2,top+36,ci===0?'绝密版':'机密版',45,C.ink,'middle');
 ['价格','基础保底','连撤'].forEach((v,j)=>sa+=tx((col[j]+col[j+1])/2,top+104,v,35,'#FFFCF3','middle'));
 const groups=[];
 for(let gi=0;gi<3;gi++){
  const gy=716+gi*366;s+=R(x+2,gy,w-4,54,C.blue,0);sa+=tx(x+w/2,gy+27,'地图 '+(gi+1),37,C.ink,'middle');const edges=[gy+54,gy+158,gy+262,gy+366];
  for(let ri=0;ri<3;ri++){s+=R(x+2,edges[ri],w-4,104,ri===2?mode:ri%2===0?C.blue:'#FFFFFF',0);if(ri)s+=ln(x+6,edges[ri],x+w-6,edges[ri]);sa+=tx(x+120,edges[ri]+52,'价格r',57,C.ink,'middle')+tx(x+370,edges[ri]+52,'物资w',57,C.ink,'middle')+tx(x+588,edges[ri]+52,(ri+2)+'把',52,C.ink,'middle');}
  for(const cx of col.slice(1,-1))s+=ln(cx,gy+54,cx,gy+366);groups.push({mapHeader:[x,gy,w,54],rowEdges:edges,colEdges:col,priceUnitX:x+167,lootUnitX:x+431});
 }
 socialCards.push({modeHeader:[x,580,680,72],tableHeader:[x,652,680,64],groups});
}
s+=R(90,1855,1420,190,C.cream,26,C.gold);sa+=tx(900,1910,'结单两项同时满足',53,C.ink,'middle')+tx(900,1985,'连撤次数达标 ＋ 打满基础保底',46,C.ink,'middle');
await save('social',1600,2133,s,sa);
await fs.writeFile(path.join(dir,'layout-spec.json'),JSON.stringify({stage:'pre-generation-guide',poster:{w:1600,h:4800,exportW:3200,exportH:9600,titleSafe:[90,50,610,410],hero:[785,25,735,490],platform:[90,480,590,82],completion:[90,605,1420,160],mapGroups,mechanismHeader:[90,2000,1420,62],validStreak:[90,2095,1420,330],failure:[90,2450,1420,128],notesHeader:[90,2610,1420,62],notes,footer:[350,4710,900,65],phoneBodySize:60*390/1600},cover:{w:2560,h:1440,exportW:3200,exportH:1800,textSafe:[100,90,1150,1240],centerX:675,hero:[1330,70,1150,1130],subtitle:[100,1080,1150,140],noOrderNotes:true},social:{w:1600,h:2133,exportW:2400,exportH:3200,cards:socialCards,completion:[90,1855,1420,190],perPlatform:true,tiersPerPage:18,noOrderNotes:true},note:'布局为底图生成前结构稿；正式字号、字形居中、r/w单位列根据生成实图重新测量，不能套用旧坐标。'},null,2));
const stateFile=path.join(root,'campaign-state.json'),state=JSON.parse(await fs.readFile(stateFile,'utf8'));state.approvals.illustration={status:'approved',basis:'用户在连续撤离主题插画交付后回复：继续',file:'01-illustrations/streak-medic-mouse-v1.png'};state.approvals.background={status:'in-progress',basis:'四张三地图×三档价目、横版封面、端游与手游各18档的简洁宣发'};state.stage='background-production';state.revision='streak-backgrounds-v1';state.plannedDeliverables=['端游绝密三地图×三档价目','端游机密三地图×三档价目','手游绝密三地图×三档价目','手游机密三地图×三档价目','横版封面','端游简洁宣发18档','手游简洁宣发18档'];state.nextStep='提交布局与七张无字底图供确认；确认后以实际底图边界填入矢量艺术字、价格与粗体正文';await fs.writeFile(stateFile,JSON.stringify(state,null,2));
console.log(JSON.stringify({poster:'1600×4800',maps:3,rowsPerMap:3,notes:8,phoneBodySize:60*390/1600,social:'端游/手游各一张，两种模式共18档'}));
