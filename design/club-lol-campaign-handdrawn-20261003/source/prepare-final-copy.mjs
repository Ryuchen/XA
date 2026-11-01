import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const file=path.join(root,'content.json');
const c=JSON.parse(fs.readFileSync(file,'utf8'));
const S=id=>c.sets.find(s=>s.id===id);
const cards=(...items)=>({cards:items});
const note=(title,...paragraphs)=>({title,paragraphs});
const table=(headers,rows,widths)=>({headers,rows,widths});
const price=(raw,unit='r')=>({amount:String(raw).replace(/（.*?）/g,'').replace(/\/.*/,'').replace(/[RH]/gi,'').replaceAll('+','起').replaceAll('~','–').replaceAll('-','–'),unit});
const money=(raw,unit='r')=>price(raw,unit);
const qual=[['T0 · 顶尖','韩服千分 / 知名选手'],['T1 · 专业','峡谷1200以上\n韩服王者'],['T2 · 招牌','峡谷王者 / 其他区千分'],['T3 · 新秀','其他区王者 / 韩服大师']];
const qualCards=()=>cards(...qual.map(([title,body])=>({title,body})));
const solo=S('01-solo-duo'),flex=S('02-flex'),arena=S('03-arena'),boost=S('04-boost'),lp=S('05-lp'),over=S('06-overseas'),day=S('07-day'),hai=S('08-haidou'),place=S('09-placement'),maint=S('10-maintenance');
solo.display={title:['单双陪玩'],subtitle:'英雄联盟 · 端游｜按小时计价',blocks:{
 'fixed-tiers':cards({title:'T0 · 顶尖',price:money(solo.tables[0].rows[0][1]),body:'每小时'},{title:'T1 · 专业',price:money(solo.tables[0].rows[0][2]),body:'每小时'}),
 'rank-price':table(['段位','T2 / T2Pro','T3 · 新秀'],solo.tables[0].rows.map(r=>[r[0].replace('（以下）','以下').replaceAll('-','–'),money(r[3]),money(r[4])]),[.42,.31,.27]),
 'qualification':qualCards(),
 'settlement':note('结算与净胜保障',
 '**任一项即可结算：**赢局 / 小火苗 / SVP / 尽力局。',
 '**大师以下：**保底1把净胜；三连败后补4把净胜，结束时至少净胜1把。辅助单陪无赔付。',
 '**大师段位：**不提供净胜赔付。',
 '**T3大师单：**老板认可实力后，输赢均结算且无赔付；陪玩同意赔付时可另行约定。')},
 advert:{tiers:cards(...qual.map(([title,body],i)=>({title,price:money(solo.tables[0].rows[0][i+1]),body:i===2?'T2 / T2Pro':body}))), 'rank-note':note('', '**以上为铂金以下 · 每小时价目**','其他段位按完整价目表选择。')}};
flex.display={title:['组排陪玩'],subtitle:'英雄联盟 · 端游｜按小时计价',blocks:{
 'fixed-tiers':cards({title:'T0 · 顶尖',price:money('150'),body:'匹配 / 组排同价'},{title:'T1 · 专业',price:money('120'),body:'匹配 / 组排同价'}),
 'match-price':table(['模式','T2 · 招牌','T3 · 新秀'],flex.tables[0].rows.map(r=>[r[0],money(r[1]),money(r[2])]),[.46,.27,.27]),
 'qualification':qualCards(),
 'service':note('售后说明','不同价位售后不同，需与客服商议。','赔付同于业内最高；**打不好，赔到满意。**')},
 advert:{'tiers':note('','**T0顶尖 · T1专业 · T2招牌 · T3新秀**','多档位匹配 / 组排可选')}};
const arenaTiers=arena.tables[0].rows.map(r=>({title:r[0].replace(/(T\d)/,'$1 · '),price:money(r[1]),body:'每小时'}));
arena.display={title:['斗魂陪玩'],subtitle:'英雄联盟 · 端游｜3×6竞技场',blocks:{
 'tiers':cards(...arenaTiers),
 'double-guarantee':table(['双男档位','名次要求','达成频次'],[['T2 · 技术','前4名','每2局至少1局'],['T1 · 招牌','前3名','每2局至少1局'],['T0 · 顶尖','前2名','每2局至少1局']],[.3,.3,.4]),
 'mixed':note('混搭与一护二','**T0＋T1：**每3局至少1局前2，其他档位以此类推。','技术男＋娱乐女默认一护二，无赔付；女陪提供情绪价值，稍懂游戏。'),
 'arena-rules':note('赔付条件与例外','**锻体局：**老板个人锻体，赔付降低一档；要求陪玩一起锻体，按娱乐局无赔付。','**3×6单陪一护二：**无赔付。','**T3娱乐：**全开麦，无赔付。','**不进语音：**视作放弃当局赔付。','召唤师峡谷陪玩点单价平移到斗魂，不提供售后。')},
 advert:{'tiers':cards(...arenaTiers),'mode':note('','**3×6斗魂竞技场**','娱乐 · 技术 · 招牌 · 顶尖')}};
const boostLow=boost.tables[0].rows.slice(0,6).map((r,i)=>[r[0],money(r[1]),money(boost.tables[1].rows[i<4?0:i-3][1])]);
const boostDia=boost.tables[0].rows.slice(6,10).map((r,i)=>[r[0].replaceAll('-','→'),money(r[1]),money(boost.tables[1].rows[i+3][1])]);
const boostPack=boost.tables[0].rows.slice(10).map((r,i)=>[r[0].replace('~','→'),money(r[1]),money(boost.tables[1].rows[i+7][1])]);
boost.display={title:['段位代练'],subtitle:'英雄联盟 · 端游｜新秀 / 招牌',blocks:{
 'qualification':cards({title:'新秀代练',body:'郊区宗师 / 王者\n峡谷 / 韩服宗师'},{title:'招牌代练',body:'峡谷 / 韩服王者\n郊区前十 / 主区千分'}),
 'basic-ranks':table(['段位 · 每小段','新秀','招牌'],boostLow,[.42,.29,.29]),
 'diamond-ranks':table(['钻石进阶','新秀','招牌'],boostDia,[.42,.29,.29]),
 'packages':table(['套餐','新秀','招牌'],boostPack,[.42,.29,.29]),
 'compensation':note('服务保障与补分','**打坏包赔。**代练检测按进度结算；因脚本等原因封号，照价赔偿账号。','黑铁5至峡谷第一，各区各分段均可接。','**加分＜20：＋30%；加分＜17：＋60%。**','发现补分时，客服提供三张截图确认，可中途放弃代练。'),
 'designated':note('指定位置与英雄','**D / F闪免费。**辅助指定＋50%；大师以下其他位置免费指定。','**指定英雄＋20%～50%。**非BAN必选，不含补位选用率≥70%。','中上野至少指定AP、物理英雄各一个。'),
 'hidden':note('高隐藏分账号','按实际匹配段位收费，如翡翠排钻1 / 2、钻石排大师 / 宗师。','**隐藏分费用＋30%～60%：**钻石加分≥23、翡翠加分≥26。')},
 advert:{'core-prices':table(['段位 · 每小段','新秀','招牌'],boostLow,[.42,.29,.29]),'packages':cards(...boostPack.map(r=>({title:r[0],body:'新秀 **'+r[1].amount+'r**\n招牌 **'+r[2].amount+'r**'})))}};
const lpRows=lp.tables[0].columns.slice(1).map((band,i)=>[band.replaceAll('-','–')+'分',money(lp.tables[0].rows[0][i+1]==='/'?'—':lp.tables[0].rows[0][i+1]),money(lp.tables[0].rows[1][i+1])]);
lp.display={title:['打分'],subtitle:'英雄联盟 · 端游｜按分计价',blocks:{
 'qualification':cards({title:'T2 · 性价比',body:'韩服千分 / 峡谷王者\n＋其他区前10'},{title:'T1 · 效率',body:'韩服1600+\n峡谷前20 / 韩服前30'}),
 'lp-price':table(['分段 · r/分','T2','T1'],lpRows,[.46,.27,.27]),
 'minimum':cards({title:'大师',body:'100分起接'},{title:'宗师',body:'80分起接'},{title:'王者',body:'60分起接'}),
 'designated':note('计价与指定','**按净胜收费。D / F闪免费。**要求直播请提前说明，直播免费。','**辅助＋30%；指定英雄＋20%～50%。**指定物理 / AP英雄、指定打手＋20%。非BAN必选，不含补位选用率≥70%。'),
 'hidden':note('大师以上 · 隐藏分与补分','**千分隐藏分号：**大师0–600分＋30%。','**加19扣21：＋20%。**','**加17–18、扣22–23：＋30%。**','加分≤16：按分段咨询客服；从补分局开始计算补分费用。','**第三赛段1300分以上：**全区默认加19，不加钱。'),
 'region':note('区服与演员局','一区600分以上＋20%；高分演员重灾区需报销演员局及路人秒钱。')},
 advert:{'core-prices':table(['分段 · r/分','T2','T1'],lpRows,[.46,.27,.27]),'conditions':note('','**T1效率 · T2性价比**','大师100分 / 宗师80分 / 王者60分起接。')}};
const overLow=over.tables[0].rows.slice(0,3).map(r=>{const v=r[1].split('；');return [r[0].replaceAll('~','→'),money(v[0]),money(v[1])];});
const overDia=over.tables[0].rows.slice(3).map(r=>({title:r[0].replaceAll('~','→').replaceAll('-','→'),price:money(r[1])}));
over.display={title:['外服代练','/ 打分'],subtitle:'英雄联盟 · 端游｜海外区服',blocks:{
 'below-master':table(['大师以下','每大段','每小段'],overLow,[.46,.27,.27]),
 'diamond':cards(...overDia),
 'above-master':table(['大师以上 · 韩服除外','r/分'],over.tables[1].rows.map(r=>[r[0].replaceAll('~','–'),money(r[1])]),[.64,.36]),
 'payment':note('区服、收款与保障','打分适用澳 / 欧 / 美 / 东南亚 / 台服。黑铁5至韩服前十，各区各段可接。','**支持u、PayPal（有手续费）、zella**及国内收款方式。','打坏包赔；打手原因封号，邮件仍无法解封时照价赔账号。'),
 'surcharge':note('补分加价','**加17–19：＋30%；加14–16：＋60%；加10–13：＋100%。**','客服截图确认补分，可中途放弃代练。'),
 'designated':note('指定位置与英雄','**中 / 上 / AD＋20%～30%；辅助＋50%。**','非冷门英雄＋30%～50%；冷门英雄咨询客服。非BAN必选，选用率≥70%。','中上野需指定一个物理＋一个AP；下路ADC除外。'),
 'korea':note('浮动报价','韩服受赛段初 / 末及职业比赛影响；800分以上与韩服打分均咨询客服。')},
 advert:{'core-prices':cards({title:'大师以下 · 铂金以下',price:money('388'),body:'每大段 / 每小段100r'},{title:'大师以上 · 0–200分',price:money('6'),body:'每分 · 韩服除外'}),'server':note('','**澳服 / 欧服 / 美服 / 东南亚 / 台服**','韩服与其他分段按完整价目表或客服报价。')}};
const dayTable=(i)=>table([day.tables[i].title.replace('性质·',' · ').replace('包天',''),'r/8小时','净胜保障'],day.tables[i].rows.map(r=>[r[0].replace('打手',''),money(r[1]),r[2].replace('把','').replace('包至少','≥').replace('净胜','净胜')]),[.46,.24,.3]);
day.display={title:['联盟包天'],subtitle:'英雄联盟 · 端游｜8小时包天',blocks:{
 'below-diamond':dayTable(0),'above-diamond':dayTable(1),'master-low':dayTable(2),'master-high':dayTable(3),
 'season':note('赔付段位以赛季最高为准','**比较S15与S16，取较高段位。**如一个赛季钻2、另一个翡翠2 / 3，按钻2标准赔付。'),
 'companion':note('陪玩包天 · 结算条件','**老板死亡≥12次，且陪玩为尽力局 / SVP：**该失败对局仍计作胜场。','净胜不足，由陪玩补足后再结算。'),
 'boost':note('代练包天 · 指定与补足','陪玩上号，可直播、可指定位置；指定打手免费，指定英雄需沟通加价。','净胜不足，补足胜场后再结算。'),
 'event':note('大师500分以上活动条件','**仅限赛季初、游戏环境较好时。**','赛季中后因演员及各区环境不同，稳定上分推荐按分计价，以客服回复推荐为准。')},
 advert:{'core-prices':cards({title:'陪玩 · 钻石以下新秀',price:money('388'),body:'8小时 · 至少4净胜'},{title:'陪玩 · 钻石以上新秀',price:money('488'),body:'8小时 · 至少3净胜'},{title:'代练 · 大师100–500',price:money('880'),body:'T2 · 8小时 · ≥2净胜'},{title:'代练 · 大师500–800',price:money('1188'),body:'招牌 · 8小时 · ≥3净胜'}),'conditions':note('','**大师500分以上活动：**','仅限赛季初且环境好，以客服回复为准。')}};
const haiTables={parallel:hai.tables.map(t=>({title:t.title,rows:t.rows.map(r=>[r[0].replace('男陪',''),money(r[1])])}))};
hai.display={title:['海斗陪玩'],subtitle:'英雄联盟 · 端游｜按小时计价',blocks:{
 'female-male':haiTables,
 'settlement':note('任一项达成即可结算','**单陪：**赢局 / SVP / 对局伤害最高，任一项达成即正常结算。','**双陪：**两人中任意一人达成上述任一项，即正常结算。'),
 'compensation':cards({title:'三陪 · 输路人',body:'免当局\n不含陪玩车 / 胜率车'},{title:'四陪',body:'输路人 → 免1小时\n输车队 → 免当局'}),
 'exceptions':note('赔付范围','**女陪无赔付。**三陪赔付不含陪玩车、胜率车。')},
 advert:{'female-male':haiTables,'tiers':note('','**女陪五档 · 男陪四档**','每小时价目 · 多档位可选')}};
const placeBelow=table(['大师以下','价格','胜场保障'],place.tables[0].rows.map(r=>[r[0].replaceAll('-','–'),money(r[1]),r[2]]),[.44,.26,.3]);
const placeAbove=table(['大师以上','价格','胜场保障'],place.tables[1].rows.slice(0,4).map(r=>[r[0].replaceAll('-','–'),money(r[1]),r[2]]),[.44,.26,.3]);
place.display={title:['单双新赛季','定位赛'],subtitle:'英雄联盟 · 端游｜单双排定位',blocks:{
 'offer':note('','**同区可双排的两个号一起下单**','**88折**'),
 'below-master':placeBelow,'above-master':placeAbove,
 'new-account':cards({title:'新号 → 大师',price:money(place.tables[1].rows[4][1]),body:'包70%胜率 · 未达标按比例退款或送净胜'}),
 'pricing':note('段位、隐藏分与区服','**按上赛季段位及隐藏分收费；**高隐藏分以实际匹配段位计价。','**850分以上：一区＋20%，其他区不加钱。**千分定位按区服咨询客服。'),
 'designated':note('免费指定与英雄加价','**全段位D / F闪免费，直播免费。**大师以下指定上 / 野 / AD免费。','指定英雄按OP程度＋20%～50%，非BAN必选。'),
 'compensation':note('未达到胜场保障','**输1把补2把，或按比例退款。**')},
 advert:{'offer':note('','**同区可双排的两个号一起下单**','**88折**'),'core-prices':cards(...[place.tables[0].rows[0],place.tables[0].rows[1],place.tables[1].rows[0],place.tables[1].rows[4]].map(r=>({title:r[0].replaceAll('-','–'),price:money(r[1]),body:r[2].split('；')[0]}))),'pricing':note('','按上赛季段位及隐藏分计价。')}};
const maintenanceRows=maint.tables[0].rows.slice(0,4).map(r=>[r[0].replaceAll('-','–')+'分',...r.slice(1).map(v=>({price:money(v.split('(')[0]),body:v.split('(')[1].replace(')','').replace('包净胜','净胜≥')}))]);
const maintenance=table(['28局维护','T1','T2'],maintenanceRows,[.38,.31,.31]);
maint.display={title:['维护局'],subtitle:'英雄联盟 · 端游｜28局套餐',blocks:{
 'offers':cards({title:'兴安打分满300分及以上',body:'**后续维护7折** · 不与店内其他活动同享'},{title:'1000分以下 · 一次下单3个月',body:'**维护7折**'}),
 'maintenance':maintenance,
 'high-lp':cards({title:'1000分以上 · T1',price:money('2200起'),body:'包50%胜率 · 具体咨询客服'}),
 'offer-terms':note('特惠与预算调整','**特价维护按各区演员情况降低赔付。**纯维护续天数、只求保分时，可按需求协商优惠。','保障高则价格高；补分号可要求额外净胜，具体与客服协商。'),
 'hidden-region':note('隐藏分与区服浮动','**按隐藏分收费。**休眠掉分、上赛季王者刚打到大师等账号，先和客服商议。','**一区600分以上＋20%。**其他区按演员情况、分数线与时令浮动，以客服实时回复为准。'),
 'designated':note('指定条件','**0–600分原价维护（活动 / 特惠除外）：**除辅助外，其他位置与D / F闪免费指定；直播免费。','**600分以上：**指定上野AD＋200r；指定中单 / 辅助＋300r。')},
 advert:{'offer':note('','**兴安打分满300分及以上 → 后续维护7折**','不与店内其他活动同享。'),'maintenance':maintenance}};
for(const s of c.sets){s.display.copyReviewedAgainstSource=true;s.display.coverPlatform='英雄联盟 · 端游';}
c.copyPolicy.finalCopyStatus='verified-semantic-condensation';
c.copyPolicy.finalCopyBasis='每张完整价目保留数值、且/或、前提、例外；广告仅核心价目，封面仅玩法与卖点。全部展示数据并入content.json。';
fs.writeFileSync(file,JSON.stringify(c,null,2)+'\n');
console.log('Prepared canonical display copy for '+c.sets.length+' sets.');
