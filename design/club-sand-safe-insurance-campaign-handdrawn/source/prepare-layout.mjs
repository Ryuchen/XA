import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const hero=(await fs.readFile(path.join(root,'assets/illustrations/sand-safe-medic-mouse-v1.png'))).toString('base64');
const ink='#203F53',cream='#FBF8F0',gold='#D7B36F';
const rect=(b,fill=cream,stroke='#DCCAA6',rx=14)=>`<rect x="${b[0]}" y="${b[1]}" width="${b[2]}" height="${b[3]}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`;
const image=b=>`<image href="data:image/png;base64,${hero}" x="${b[0]}" y="${b[1]}" width="${b[2]}" height="${b[3]}" preserveAspectRatio="xMidYMid meet"/>`;
const safe=(x,y,s=50,sand=false)=>`<g transform="translate(${x} ${y})"><rect width="${s}" height="${s}" rx="7" fill="${sand?'#E0BF8F':'#A8B9C9'}" stroke="${ink}" stroke-width="2"/><rect x="6" y="6" width="${s-12}" height="${s-12}" rx="4" fill="none" stroke="${ink}" stroke-width="1.5"/><circle cx="${s*.53}" cy="${s*.47}" r="${s*.12}" fill="none" stroke="${ink}" stroke-width="2"/><path d="M${s*.28} ${s*.60}h${s*.14}" stroke="${ink}" stroke-width="2.5"/></g>`;
const layouts={
 poster:{w:1024,h:1536,hero:[550,22,436,258],brand:[52,40,475,58],title:[52,126,475,96],subtitle:[52,233,475,36],platform:[48,286,928,52],completion:[48,359,928,106],tableHeader:[48,489,928,58],colEdges:[48,233,480,735,976],rowEdges:[547,659,771,883],reward:[48,906,928,252],rewardTitle:[148,925,700,38],rewardTrigger:[100,976,824,36],rewardBenefits:[[94,1023,350,88],[580,1023,350,88]],rewardConditions:[94,1121,836,28],notesHeader:[48,1180,928,50],notes:[{id:'保险计次',bounds:[48,1242,928,64],icon:[64,1254,40,40],labelSafe:[121,1255,126,38],bodySafe:[270,1252,680,44]},{id:'陪玩代摸',bounds:[48,1314,928,64],icon:[64,1326,40,40],labelSafe:[121,1327,126,38],bodySafe:[270,1324,680,44]},{id:'物资累计',bounds:[48,1386,928,72],icon:[64,1402,40,40],labelSafe:[121,1402,126,40],bodySafe:[270,1398,680,48]}],footer:[90,1482,844,32]},
 cover:{w:1600,h:900,hero:[760,92,794,650],textCenterX:407,brand:[145,101,524,72],title:[55,268,704,159],subtitle:[87,489,640,88],versions:[115,621,584,54]},
 social:{w:1080,h:1440,hero:[611,23,433,258],brand:[48,36,504,58],title:[48,128,530,88],subtitle:[48,232,530,36],completion:[46,303,988,98],cards:[{id:'desktop-absolute',bounds:[46,423,988,326],platformHeader:[46,423,988,46],columnHeader:[46,469,988,40],rowEdges:[509,589,669,749]},{id:'mobile-absolute',bounds:[46,771,988,326],platformHeader:[46,771,988,46],columnHeader:[46,817,988,40],rowEdges:[857,937,1017,1097]}],colEdges:[46,231,490,763,1034],reward:[46,1120,988,242],rewardTitle:[144,1138,804,38],rewardTrigger:[93,1185,894,32],rewardBenefits:[[100,1229,365,79],[617,1229,365,79]],rewardConditions:[95,1321,890,30],footer:[130,1383,820,27]}
};
const bodies={};let p='';
p+=image(layouts.poster.hero)+rect(layouts.poster.platform,ink,ink,18)+rect(layouts.poster.completion,'#FFF2D7',gold,22);
for(const b of [[81,376,255,71],[383,376,255,71],[687,376,255,71]])p+=rect(b,'#FFFCF4','#DCCAA6',15);
p+=rect([48,489,928,394],'#FFFFFF','#DCCAA6',20)+rect(layouts.poster.tableHeader,ink,ink,16);
for(let i=0;i<3;i++){const y=547+i*112;p+=rect([54,y+7,916,98],i%2?'#FAF4E7':'#FFFFFF','#F0E4CD',13)+safe(253,y+30,52,true)+safe(500,y+30,52,false);}
p+=rect(layouts.poster.reward,'#FFF1D6',gold,24)+rect([66,919,67,61],'#E8BD64','#D4A458',15);
for(const b of layouts.poster.rewardBenefits)p+=rect(b,'#FFFCF5','#DCCAA6',14);
p+=rect(layouts.poster.notesHeader,ink,ink,16);
for(const n of layouts.poster.notes)p+=rect(n.bounds,'#FFFFFF','#D8E2E8',15)+rect(n.icon,'#EDF3F7','#DAE3E9',8);
bodies.poster=p;
bodies.cover=image(layouts.cover.hero)+rect(layouts.cover.subtitle,ink,ink,32);
let s=image(layouts.social.hero)+rect(layouts.social.completion,'#FFF2D7',gold,20);
for(const c of layouts.social.cards){s+=rect(c.bounds,'#FFFFFF','#DCCAA6',18)+rect(c.platformHeader,ink,ink,14)+rect(c.columnHeader,'#F4E8D2','#F4E8D2',0);for(let i=0;i<3;i++){const y=c.rowEdges[i];s+=rect([52,y+5,976,70],i%2?'#FBF5E9':'#FFFFFF','#F0E4CD',9)+safe(248,y+18,44,true)+safe(514,y+18,44,false);}}
s+=rect(layouts.social.reward,'#FFF1D6',gold,22)+rect([67,1135,62,58],'#E8BD64','#D4A458',14);for(const b of layouts.social.rewardBenefits)s+=rect(b,'#FFFCF5','#DCCAA6',13);
bodies.social=s;
await fs.mkdir(path.join(root,'assets/backgrounds'),{recursive:true});
await fs.mkdir(path.join(root,'04-png-export'),{recursive:true});
for(const [id,l] of Object.entries(layouts)){
 const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${l.w}" height="${l.h}" viewBox="0 0 ${l.w} ${l.h}"><rect width="${l.w}" height="${l.h}" fill="${cream}"/>${bodies[id]}</svg>`;
 await fs.writeFile(path.join(root,'02-layouts',id+'-guide.svg'),svg);
 await sharp(Buffer.from(svg)).png().toFile(path.join(root,'02-layouts',id+'-guide.png'));
}
await fs.writeFile(path.join(root,'02-layouts/proposed-background-layout.json'),JSON.stringify({version:'sand-safe-insurance-layout-v1',method:'生成前结构坐标；底图返回后须按实际原图重新测量文字区',...layouts},null,2));
const state=JSON.parse(await fs.readFile(path.join(root,'campaign-state.json'),'utf8'));
state.stage='background-production';state.revision='sand-safe-insurance-background-v1';state.approvals.illustration={status:'approved',basis:'用户在本轮插画确认稿交付后回复：继续',file:'01-illustrations/sand-safe-medic-mouse-v1.png'};state.nextStep='按三种结构生成完整价目、封面与宣发无字底图，实测文字区并交付确认。';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
console.log('已保存三种结构稿与插画确认；完整价目共用版式，随后分别填端游、手游价目。');
