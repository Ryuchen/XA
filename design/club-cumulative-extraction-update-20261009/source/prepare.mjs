import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const previous=path.join(path.dirname(root),'club-streak-extraction-campaign-handdrawn');
const skill=process.argv[2];
if(!skill)throw Error('Provide the installed skill directory.');
try{await fs.access(path.join(root,'content.json'));throw Error('Project already exists; edit content.json and run source/typeset.mjs.');}catch(e){if(e.code!=='ENOENT')throw e;}
const read=async f=>JSON.parse((await fs.readFile(f,'utf8')).replace(/^\uFEFF/,''));
const save=async(f,v)=>fs.writeFile(path.join(root,f),JSON.stringify(v,null,2));
for(const d of ['assets/backgrounds','references','source','review','.cache'])await fs.mkdir(path.join(root,d),{recursive:true});
await fs.cp(path.join(previous,'assets/backgrounds'),path.join(root,'assets/backgrounds'),{recursive:true});
await fs.copyFile(path.join(previous,'02-layouts/generated-background-measurements.json'),path.join(root,'source/background-measurements.json'));
await fs.copyFile('C:/Users/chenh/AppData/Local/Temp/codex-clipboard-b760f9a8-e251-4855-a151-2d878a370cf9.png',path.join(root,'references/mobile-price-source.png'));
for(const n of ['render.mjs','scope.mjs','outline-text.ps1','workflow.mjs','pack.ps1'])await fs.copyFile(path.join(skill,'scripts',n),path.join(root,'source',n));
const content=await read(path.join(previous,'content.json'));
content.title='累计撤离单';content.titleLines=['累计','撤离单'];content.subtitle='累计撤离 · 物资保底';
const prices={
 'desktop-absolute':{'巴克什':[288,388,488],'航天':[328,488,648],'监狱':[288,388,488]},
 'desktop-secret':{'核电站':[188,288,388],'巴克什':[248,348,448],'航天':[268,368,468]},
 'mobile-absolute':{'巴克什':[228,328,428],'航天':[248,348,448],'监狱':[228,328,428]},
 'mobile-secret':{'核电站':[128,188,248],'巴克什':[148,218,288],'航天':[188,268,328]}
};
const mobileGuarantees={
 'mobile-absolute':{'巴克什':[1488,2288,3088],'航天':[1688,2488,3288],'监狱':[1488,2288,3088]},
 'mobile-secret':{'核电站':[788,1188,1688],'巴克什':[988,1488,1988],'航天':[1288,1888,2488]}
};
for(const t of content.tiers)for(const g of t.maps)for(const [i,r]of g.rows.entries()){
 r.priceR=prices[t.id][g.map][i];
 if(mobileGuarantees[t.id])r.baseGuaranteeW=mobileGuarantees[t.id][g.map][i];
 r.cumulativeExtractions=r.consecutiveExtractions;delete r.consecutiveExtractions;
 r.requirement=`累计有效撤离${r.cumulativeExtractions}把，且打满基础保底才可结单`;
}
content.mechanism.completion={operator:'and',conditions:['达到对应累计有效撤离把数','打满基础保底'],copy:'累计撤离达标 + 打满基础保底，才可结单'};
content.mechanism.validExtraction.lowThresholdRound={countsTowardCumulative:false,lootOwnership:'板板',copy:'低于门槛局不计累计有效撤离；本局物资归板板。'};
delete content.mechanism.failure;
content.sources={
 prices:'2026-10-09用户文字和截图；手游新价格来自J列，保底来自E列，旧价格D列不用。端游机密巴克什/航天分别取手游绝密对应地图新价格+20；核电站188/288/388。',
 priceSourceByPlatform:{mobile:'references/mobile-price-source.png：15行映射至手游18档，绝密巴克什和监狱同价同保底。',desktop:'用户文字给定价格；仅调整价格，基础保底沿用原端游价格表。'},
 rules:'本轮要求累计撤离，保留有效撤离要求，移除撤离失败连撤说明。其余规则沿用用户所附旧海报和已确认源数据。',
 previousProject:'../club-streak-extraction-campaign-handdrawn'
};
content.contentNotes=[
 '共36档，累计有效撤离2/3/4把与基础保底两项同时满足。',
 '手游价格与保底取本轮截图，端游基础保底沿用原表。',
 '有效撤离：成功撤离，陪玩击杀≥4人且带出绝密≥600w/机密≥300w。',
 '低于门槛局不计累计有效撤离，当局物资归板板；删除连撤重置和不打断连撤文案。',
 '默认允许航天飞升、巴克什飞鼠；禁止对应撤离点时，对应单价上浮25%。',
 '其余房卡、随行装备、补偿、结算、物资累计、速撤规则沿用。'
];
await save('content.json',content);
await save('campaign-state.json',{
 skill:'xingan-gameplay-posters',campaign:'兴安电竞 · 累计撤离单',stage:'vector-production',revision:'cumulative-r1',contentVerified:true,
 deliveryScope:{pageIds:['desktop-absolute','desktop-secret','mobile-absolute','mobile-secret','cover','social-desktop','social-mobile'],platforms:['desktop','mobile'],basis:'修改原套封面、端手游两张广告页和四张完整价格表，沿用原套7张范围。'},
 verifiedPricePlatforms:['desktop','mobile'],priceSourceByPlatform:content.sources.priceSourceByPlatform,
 contentVerificationBasis:content.sources.prices+'有效撤离及其余规则沿用，本轮累计撤离语义按明确要求替换。',
 approvals:{illustration:{status:'approved-reference',basis:'用户本轮附原套成图并要求修改，沿用已确认蜂医和灰鼠。'},background:{status:'approved-reference',basis:'复用原背景；通过SVG版面裁窗移除失败说明卡，未生成或重绘美术。'}},
 assumptions:['端游保底沿用原端游价格表；用户只指定端游价格变化。'],
 visualQA:{status:'pending',record:'review/visual-qa.md'},
 layoutInvariants:['正文数字和r/w共用baseline、固定单位列。','保留有效撤离要求，删除所有连续/连撤/失败重计文案。','广告各自18档与对应平台完整价目共用数据。','封面品牌/标题左对齐，胶囊与平台行在左文字区居中。','价目须知区保持原字号和上下留白，普通说明采用常规字重。']
});
await fs.copyFile(path.join(previous,'layout-adapter.mjs'),path.join(root,'layout-adapter.mjs'));
console.log(JSON.stringify(content.tiers.map(t=>({id:t.id,maps:t.maps.map(g=>({map:g.map,prices:g.rows.map(r=>r.priceR),guarantees:g.rows.map(r=>r.baseGuaranteeW)}))})),null,2));
