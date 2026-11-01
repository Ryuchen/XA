import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharp=createRequire(import.meta.url)(path.join(process.argv[2],'sharp'));
const read=async p=>JSON.parse(await fs.readFile(path.join(root,p),'utf8'));
const state=await read('campaign-state.json');
await fs.mkdir(path.join(root,'assets/layout-guides'),{recursive:true});
state.overallDeliveryScope??=structuredClone(state.deliveryScope);
state.deliveryScope={pageIds:['dogtag-cover','firearms-cover','revolver-cover'],platforms:['desktop','mobile'],basis:'用户已批准三张蜂医灰鼠插画，并回复“通过，继续，做封面”；本阶段先制作三张封面，广告仍属整体任务后续部分。'};
state.queuedPageIds=['dogtag-social','firearms-social','revolver-social'];
state.approvals.illustration={status:'approved',date:'2026-10-01',basis:'用户明确回复：通过，继续，做封面。',items:state.pendingArtwork.map(x=>x.file)};
state.approvals.background={status:'in-progress',scope:'three-covers',basis:'按用户本轮要求先做三张封面的新手绘无字底图；新底图确认后矢量填字。'};
state.stage='cover-background-production';state.revision='r2-cover-backgrounds';
await fs.writeFile(path.join(root,'campaign-state.json'),JSON.stringify(state,null,2));
const regions={canvas:[1600,1000],copyColumn:[80,100,710,755],brand:[125,108,620,80],title:[82,211,706,157],version:{panel:[130,408,610,92],safe:[160,422,550,65]},tagline:[110,546,650,91],platform:{panel:[135,674,600,77],safe:[160,687,550,52]},mode:{panel:[224,785,420,72],safe:[244,794,380,51]},illustration:[810,195,760,595],marginalScenery:'场景和细节主要在x≥800的右侧、y≤85的顶部薄边与y≥880底部，不跨入左侧文字净区。'};
await fs.writeFile(path.join(root,'source/cover-layout-plan-r2.json'),JSON.stringify(regions,null,2));
for(const id of ['dogtag','firearms','revolver']){
 const art=(await fs.readFile(path.join(root,`assets/illustrations/medic-mouse-${id}-r2.png`))).toString('base64');
 const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000"><rect width="1600" height="1000" fill="#FBFAF5"/><path d="M810 0H1600V1000H810Q730 860 820 728T810 0" fill="#E7F1F5"/><path d="M0 904Q730 865 1600 876V1000H0Z" fill="#D5E5ED"/><rect x="56" y="85" width="744" height="791" rx="50" fill="#FFFEF9"/><rect x="130" y="408" width="610" height="92" rx="36" fill="#20394A"/><rect x="135" y="674" width="600" height="77" rx="35" fill="#E5EEF0"/><rect x="224" y="785" width="420" height="72" rx="30" fill="#EEF1E7"/><image x="810" y="195" width="760" height="595" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,${art}"/></svg>`;
 await sharp(Buffer.from(svg)).png().toFile(path.join(root,`assets/layout-guides/${id}-cover-r2.png`));
}
console.log('Three layout guides prepared; illustration approval recorded; cover work scope set.');
