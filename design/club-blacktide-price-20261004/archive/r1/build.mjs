import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const project=path.dirname(fileURLToPath(import.meta.url));
const skill='C:/Users/chenh/.codex/skills/xingan-gameplay-posters';
const modules='C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const sharp=createRequire(import.meta.url)(path.join(modules,'sharp'));
const previous='C:/Users/chenh/Documents/ChatGPT/兴安电竞玩法/兴安电竞-黑潮爆破-王者大师纠正-20260929';
const attachmentRoot='C:/Users/chenh/Documents/xwechat_files/chenhao1993_8097/temp/RWTemp/2026-10/9e20f478899dc29eb19741386f9343c8';
for(const d of ['assets/backgrounds','assets/illustrations','source/references','review'])await fs.mkdir(path.join(project,d),{recursive:true});
for(const f of ['workflow.mjs','render.mjs','scope.mjs','outline-text.ps1','pack.ps1'])await fs.copyFile(path.join(skill,'scripts',f),path.join(project,'source',f));
await fs.copyFile(path.join(previous,'assets/黑潮爆破-主题插画.png'),path.join(project,'assets/illustrations/blacktide.png'));
await fs.copyFile(path.join(previous,'content.json'),path.join(project,'source/references/existing-xingan-content.json'));
for(const [src,dst] of [['37d1ceb450bace35b7c9c30b8fcca2a5.jpg','reference-notice.jpg'],['59be08a3ed87a6b1463831f5e01dc9c8.jpg','reference-prices.jpg']])await fs.copyFile(path.join(attachmentRoot,src),path.join(project,'source/references',dst));

const hourly=[[50,60,70,80,90,100],[60,70,80,90,100,110],[70,80,90,100,110,120],[80,90,100,110,120,130],[null,100,110,120,130,140],[null,110,120,130,140,150]];
const rounds=[[null,30,40,45,50,55],[null,35,45,50,55,60],[null,40,50,55,60,65],[null,45,55,60,65,70],[null,null,60,65,70,75],[null,null,null,75,80,85]];
const reduce=rows=>rows.map(row=>row.map(v=>v===null?null:v-10));
const content={
  brand:'兴安电竞',title:'黑潮爆破',game:'三角洲行动',platform:'端游',
  tierNames:['娱乐','王者','大师','传奇','荣耀','明星'],
  renames:{S:'王者',SS:'大师',SSS:'传奇',魔王:'荣耀'},
  rankNames:['下三／匹配','铂金','钻石','赤枭','传奇','王冠'],
  prices:{adjustment:-10,hourly:{sourceValues:hourly,values:reduce(hourly),unit:'R/小时'},rounds:{sourceValues:rounds,values:reduce(rounds),unit:'R/把'},unavailableLabel:'—'},
  compensation:{
    basis:'现有兴安电竞黑潮爆破赔付标准优先；不采用参考图的名次赔付矩阵、三陪四陪名次车赔及2P0输平前四规则。',
    exclusions:['女陪无赔付','娱乐陪无赔付'],
    one:{anyOf:['赢局','SVP','对局伤害最高'],result:'满足任意一项，即正常结算',exception:'要求打工程位无赔付'},
    two:{who:'两人中任意一人',anyOf:['赢局','SVP','对局伤害最高'],result:'达成任意一项，即正常结算'},
    three:{cause:'输路人',result:'免当局',exception:'不含陪玩车、胜率车'},
    four:[{cause:'输路人',result:'免1小时'},{cause:'输车队',result:'免当局'}],
    five:{result:'只结赢局',exception:'挂局不计入'}
  },
  additions:{
    hourly:{condition:'技术陪1陪多／2陪3',byRank:[{label:'下三',amount:5},{label:'铂金至赤枭',amount:10},{label:'传奇及以上',amount:20}],unit:'R/小时/人',exemption:'无需赔付则无需加价',mixed:'技术陪人数＜娱乐陪人数，按少陪多计算'},
    extras:[{label:'甜蜜／教学',amount:10,unit:'R/人'},{label:'特殊称呼',amount:10,unit:'R/人'}],
    rounds:[{label:'指定女陪',amount:10,unit:'R/把'},{label:'≥4陪',amount:-5,unit:'R/把/人'},{label:'1陪多／2陪3',amount:0,result:'不加价'}]
  },
  redScreen:{scope:'适用于有赔付订单',definition:'红屏即检测到挂',early:{comparison:'<',round:11,result:'不计入'},late:{comparison:'>=',round:11,result:'计半局'},matchPointOvertime:'赛点及加时红屏算整局',entertainment:'娱乐陪红屏正常计费',conversion:{hours:1,rounds:2,freeRoundHours:0.5,freeHalfRoundHours:0.25}},
  settlement:{draws:'平局按赢局结算',applies:'赔付标准适用于时薪陪玩单和按把结算单',missingRank:'无对应段位价格时，按该陪玩最高单价接单，且无赔付',p0:'1P0按1P1赔付标准计算'},
  copy:{pricePage:'陪玩价目表',noticePage:'陪玩点单须知',priceSubtitle:'时薪陪玩 · 按把结算',noticeSubtitle:'赔付 · 加价 · 红屏 · 结算',hourlyTitle:'时薪陪玩',roundsTitle:'排位按把',hourlyCaption:'单位：元／小时',roundsCaption:'单位：元／把',unavailable:'— 表示无对应段位单价，接单口径见须知',priceFooter:'加价与赔付条件，请配合点单须知查看',coverSubtitle:'陪玩价目表',coverLine:'时薪陪玩 / 排位按把',coverTiers:'娱乐 · 王者 · 大师 · 传奇 · 荣耀 · 明星'},
  sources:{price:'本轮用户参考图2逐格录入；全部非空基础价减10，娱乐与明星也减10；加价和优惠维持参考值。',existingRules:'2026-09-29现有兴安黑潮爆破 content.json',newRules:'本轮用户参考图1与图2中不冲突的规则',confirmations:['用户明确选择以现有兴安赔付为主，合并不冲突的加价、红屏、结算规则。','用户明确选择未满11回合不计，11回合及以上计半局。']}
};
await fs.writeFile(path.join(project,'content.json'),JSON.stringify(content,null,2));
const state={date:'2026-10-04',revision:'r1',stage:'content-verified',contentVerified:true,contentVerificationBasis:'参考价目原图核对后，按用户明确映射改名并逐格减10；已确认以现有兴安赔付优先及第11回合计半局。',deliveryScope:{pageIds:['blacktide'],platforms:['desktop'],basis:'用户最新要求合并成一个页面；仅一张完整长图，含价目与合并后的须知。'},verifiedPricePlatforms:['desktop'],priceSourceByPlatform:{desktop:'用户本轮原图2，基础小时价与每把价各减10；不使用旧版男女价目。'},approvals:{illustration:{status:'reused-existing',basis:'复用现有兴安黑潮爆破已使用的主题插画'},background:{status:'derived-layout',basis:'现有插画与配色延续，依据本轮表格和规则组织矢量卡片。用户要求直接给出成图。'},rules:{status:'user-confirmed',basis:content.sources.confirmations}},previousProject:previous};
await fs.writeFile(path.join(project,'campaign-state.json'),JSON.stringify(state,null,2));

function base(w,h){
 let decor='';
 for(let y=62;y<h;y+=84)for(let x=28;x<w;x+=84)decor+=`<circle cx="${x}" cy="${y}" r="1.3" fill="#adc3d3" opacity=".2"/>`;
 return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><defs><linearGradient id="paper" x2=".7" y2="1"><stop stop-color="#fffdf6"/><stop offset=".6" stop-color="#f4f8fc"/><stop offset="1" stop-color="#f2edf9"/></linearGradient></defs><rect width="${w}" height="${h}" fill="url(#paper)"/>${decor}<rect x="20" y="20" width="${w-40}" height="${h-40}" rx="30" fill="none" stroke="#b5c5d7" stroke-width="2"/><rect x="28" y="28" width="${w-56}" height="${h-56}" rx="25" fill="none" stroke="#dfe4eb"/><path d="M0 468 Q210 430 408 478 T${w} 435" fill="none" stroke="#dde7ef" stroke-width="4"/><path d="M18 ${h-190} Q240 ${h-230} 500 ${h-180} T${w} ${h-210}" fill="none" stroke="#dad4ea" stroke-width="3"/></svg>`;
}
const hero=await sharp(path.join(project,'assets/illustrations/blacktide.png')).resize(558,372).png().toBuffer();
for(const [id,w,h] of [['blacktide',1080,6360]]){
 await sharp(Buffer.from(base(w,h))).composite([{input:hero,left:504,top:91}]).png().toFile(path.join(project,'assets/backgrounds',id+'.png'));
}
console.log('Prepared verified content and one combined long-page background.');
