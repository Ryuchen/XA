import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const runtimeModules = 'C:/Users/chenh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const sharp = require(path.join(runtimeModules, 'sharp'));
const root = path.dirname(fileURLToPath(import.meta.url));
const mascot = (await readFile(path.join(root, 'assets', 'shushu-mascot.png'))).toString('base64');
const NAVY = '#153349';
const INK = '#17364B';
const SLATE = '#647D8E';
const PALE = '#F5FAFC';
const GOLD = '#D59A3D';
const TEAL = '#3EA694';
const FONT = 'Microsoft YaHei UI, Microsoft YaHei, Arial, sans-serif';

await Promise.all(['backgrounds', 'source', 'png'].map((dir) => mkdir(path.join(root, dir), { recursive: true })));

const esc = (s) => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const txt = (x, y, content, size, fill = INK, weight = 700, anchor = 'start', extra = '') =>
  `<text x="${x}" y="${y}" text-anchor="${anchor}" dominant-baseline="central" font-family="${FONT}" font-size="${size}" font-weight="${weight}" fill="${fill}" ${extra}>${esc(content)}</text>`;
const lineTxt = (x, y, lines, size, fill = INK, weight = 500, gap = 28, extra = '') =>
  lines.map((s, i) => txt(x, y + i * gap, s, size, fill, weight, 'start', extra)).join('');
const rootSvg = (w, h, inner) => `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img">${inner}</svg>`;
const logo = (x, y, scale = 1, color = NAVY) => `<g transform="translate(${x} ${y}) scale(${scale})" fill="none" stroke="${color}" stroke-width="3.4" stroke-linejoin="round"><path d="M24 0 48 42H0Z"/><path d="M24 10 39 36H9Z"/><path d="M24 19 30 30H18Z"/></g>`;
const crate = (x, y, s, tone = '#D7E5EA', accent = '#F0C97E', opacity = 1) => `<g transform="translate(${x} ${y}) scale(${s})" opacity="${opacity}"><path d="M0 18 36 0 72 18 36 38Z" fill="${accent}"/><path d="M0 18 36 38V78L0 58Z" fill="${tone}"/><path d="M72 18 36 38V78L72 58Z" fill="#BACFD8"/><path d="M13 26 36 39V62" fill="none" stroke="#FFFFFF" stroke-width="4" opacity=".8"/><path d="M59 26 36 39" fill="none" stroke="#FFFFFF" stroke-width="4" opacity=".75"/></g>`;
const star = (x, y, s, color) => `<path d="M${x} ${y-s} Q${x+s*.14} ${y-s*.15} ${x+s} ${y} Q${x+s*.15} ${y+s*.15} ${x} ${y+s} Q${x-s*.15} ${y+s*.15} ${x-s} ${y} Q${x-s*.15} ${y-s*.15} ${x} ${y-s}Z" fill="${color}"/>`;

const variants = [
  {
    id: 'desktop-absolute', platform: '端游', mode: '绝密', accent: GOLD, tint: '#F8EBD6',
    price: [188, 268, 468, 658, 868, 1668], loot: [866, 1266, 2466, 3666, 4866, 9066],
    threshold: 600, gear: 40, shortage: [200, 300, 300, 200], cancel: ['符合俱乐部炸单标准：', '航天 / 巴克什 +60w；监狱 +80w。'], safe: 100,
    map: ['绝密：航天 / 巴克什 / 监狱。', '先与客服确认地图，中途换图需协商。', '航天含核心区金红卡；巴克什含塔内、博物馆金红卡；', '监狱含金红卡与一级监狱卡。'],
  },
  {
    id: 'desktop-secret', platform: '端游', mode: '机密', accent: TEAL, tint: '#DFF1EC',
    price: [188, 268, 468, 658, 868, 1668], loot: [1266, 1866, 3266, 4666, 6266, 13866],
    threshold: 300, gear: 40, shortage: [100, 150, 150, 100], cancel: ['符合俱乐部炸单标准：', '航天 / 巴克什 +60w；监狱 +80w。'], safe: 50,
    map: ['机密：航天 / 巴克什 / 核电站。', '先与客服确认地图，中途换图需协商。', '航天含核心区金红卡；巴克什含塔内、博物馆金红卡；', '核电站含金红卡。'],
  },
  {
    id: 'mobile-absolute', platform: '手游', mode: '绝密', accent: GOLD, tint: '#F8EBD6',
    price: [168, 268, 468, 658, 868, 1068], loot: [1388, 2288, 4088, 5888, 7688, 9488],
    threshold: 600, gear: 40, shortage: [200, 300, 300, 200], cancel: ['符合俱乐部炸单标准：', '每局增加30w保底。'], safe: 100,
    map: ['绝密：航天 / 巴克什 / 监狱。', '先与客服确认地图，中途换图需协商。', '航天含核心区金红卡；巴克什含塔内、博物馆金红卡；', '监狱含金红卡与一级监狱卡。'],
  },
  {
    id: 'mobile-secret', platform: '手游', mode: '机密', accent: TEAL, tint: '#DFF1EC',
    price: [168, 268, 468, 658, 868, 1068], loot: [1588, 2588, 4588, 6588, 8588, 10588],
    threshold: 300, gear: 30, shortage: [100, 150, 150, 100], cancel: ['符合俱乐部炸单标准：', '每局增加30w保底。'], safe: 50,
    map: ['机密：航天 / 巴克什 / 核电站。', '先与客服确认地图，中途换图需协商。', '航天含核心区金红卡；巴克什含塔内、博物馆金红卡；', '核电站含金红卡。'],
  },
];

function posterBackground(v) {
  const p = [];
  p.push(`<defs><pattern id="grid" width="72" height="72" patternUnits="userSpaceOnUse"><path d="M72 0H0V72" fill="none" stroke="#DDE9EE" stroke-width="1.1"/></pattern><linearGradient id="mist" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#EAF4F7"/><stop offset="1" stop-color="#FBFDFE"/></linearGradient></defs>`);
  p.push(`<rect width="1600" height="3600" fill="${PALE}"/><rect width="1600" height="3600" fill="url(#grid)" opacity=".46"/>`);
  p.push(`<path d="M0 0H1600V12H0Z" fill="${NAVY}"/><path d="M0 0H560V12H0Z" fill="${v.accent}"/>`);
  p.push(`<ellipse cx="1330" cy="285" rx="330" ry="292" fill="url(#mist)"/><ellipse cx="1448" cy="330" rx="232" ry="228" fill="${v.tint}" opacity=".72"/><path d="M1060 474 1240 396l360 137v70H1060Z" fill="#E7F0F3" opacity=".68"/>`);
  p.push(star(1006, 229, 18, v.accent), star(1510, 138, 12, '#9AB5C0'), star(975, 446, 9, '#9AB5C0'));
  p.push(`<image href="data:image/png;base64,${mascot}" x="1124" y="72" width="414" height="414" preserveAspectRatio="xMidYMid meet"/>`);
  p.push(crate(1407, 466, 1.03, '#DEE9ED', '#F5DCA9', .65), crate(1312, 516, .78, '#D2E1E7', '#F0D5A1', .55));
  // quiet abstract evacuation-route marks; deliberately behind the information cards
  p.push(`<path d="M0 1900C260 1840 340 1940 552 1882S930 1840 1130 1905 1440 1958 1600 1878V3600H0Z" fill="#F0F6F8" opacity=".7"/><path d="M0 3520Q440 3370 840 3486T1600 3450V3600H0Z" fill="#E9F2F5"/>`);
  // all copy-free panels are part of the high-resolution base PNG
  p.push(`<rect x="80" y="650" width="1440" height="1170" rx="28" fill="#FFFFFF" stroke="#C9DCE4" stroke-width="2.5"/><path d="M108 650H1492Q1520 650 1520 678V740H80V678Q80 650 108 650Z" fill="${NAVY}"/>`);
  for (let i = 0; i < 6; i++) {
    const y = 740 + i * 180;
    p.push(`<rect x="82" y="${y}" width="1436" height="180" fill="${i === 5 ? v.tint : (i % 2 ? '#FFFFFF' : '#EFF6F8')}"/><path d="M105 ${y+179}H1495" stroke="#D8E6EB" stroke-width="1.5"/>`);
  }
  p.push(`<rect x="80" y="1640" width="8" height="180" fill="${v.accent}"/>`);
  p.push(`<rect x="80" y="1950" width="1440" height="220" rx="25" fill="#FFFFFF" stroke="${v.accent}" stroke-width="3"/><rect x="80" y="1950" width="12" height="220" rx="6" fill="${v.accent}"/><path d="M425 1983V2137" stroke="#D7E5E9" stroke-width="2.5"/>`);
  p.push(`<rect x="80" y="2250" width="1440" height="270" rx="26" fill="#FFFFFF" stroke="#D4E2E7" stroke-width="2.5"/><path d="M106 2250H288V2520H106Q80 2520 80 2494V2276Q80 2250 106 2250Z" fill="${NAVY}"/>`);
  p.push(`<rect x="340" y="2336" width="394" height="62" rx="15" fill="#EAF2F5"/><rect x="862" y="2336" width="600" height="62" rx="15" fill="${v.tint}"/>`);
  p.push(`<rect x="80" y="2648" width="704" height="218" rx="18" fill="#FFFFFF" stroke="#D5E4E9" stroke-width="2"/><rect x="816" y="2648" width="704" height="218" rx="18" fill="#FFFFFF" stroke="#D5E4E9" stroke-width="2"/><rect x="80" y="2880" width="704" height="218" rx="18" fill="#FFFFFF" stroke="#D5E4E9" stroke-width="2"/><rect x="816" y="2880" width="704" height="218" rx="18" fill="#FFFFFF" stroke="#D5E4E9" stroke-width="2"/><rect x="80" y="3112" width="704" height="218" rx="18" fill="#FFFFFF" stroke="#D5E4E9" stroke-width="2"/><rect x="816" y="3112" width="704" height="218" rx="18" fill="#FFFFFF" stroke="#D5E4E9" stroke-width="2"/>`);
  for (const y of [2662, 2894, 3126]) {
    p.push(`<rect x="80" y="${y}" width="7" height="190" rx="3" fill="${v.accent}"/><rect x="816" y="${y}" width="7" height="190" rx="3" fill="${v.accent}"/>`);
  }
  p.push(`<rect x="80" y="3392" width="1440" height="82" rx="20" fill="#E5F0F4"/>`);
  return rootSvg(1600, 3600, p.join(''));
}

function ruleCard(x, y, n, title, lines, accent) {
  return `${txt(x, y, String(n).padStart(2, '0'), 17, accent, 900)}${txt(x + 47, y, title, 23, NAVY, 800)}${lineTxt(x, y + 34, lines, 17.5, '#425F70', 600, 26)}`;
}

function posterOverlay(v) {
  const p = [];
  p.push(`<image href="../backgrounds/${v.id}.png" x="0" y="0" width="1600" height="3600"/>`);
  p.push(logo(83, 75, .83), txt(138, 93, '兴安电竞', 35, NAVY, 900), txt(139, 126, 'XINGAN ESPORTS  ·  MATERIAL SUPPLY', 13, SLATE, 700, 'start', 'letter-spacing="1.25"'));
  p.push(txt(82, 268, '常规物资价目', 82, NAVY, 900, 'start', 'letter-spacing="1"'));
  p.push(`<rect x="82" y="344" width="274" height="66" rx="18" fill="${v.accent}"/>${txt(219, 377, `${v.platform} · ${v.mode}`, 29, '#FFFFFF', 900, 'middle')}`);
  p.push(txt(391, 377, '物资累计保底', 31, INK, 800), txt(84, 456, 'PRICE LIST   /   基础档带出目标', 16, SLATE, 800, 'start', 'letter-spacing="1.4"'), txt(1512, 456, '鼠鼠得吃 · 这趟也稳稳带回家', 19, SLATE, 700, 'end'));
  p.push(`<path d="M80 500H1520" stroke="${v.accent}" stroke-width="2.5" opacity=".85"/>`);
  // table headings and values; number edges and unit suffixes are fixed across every row
  p.push(txt(370, 696, '订单价格', 30, '#FFFFFF', 900, 'middle'), txt(801, 696, '带出目标', 28, '#DCE9EE', 800, 'middle'), txt(1203, 696, '累计物资保底', 30, '#FFFFFF', 900, 'middle'));
  for (let i = 0; i < 6; i++) {
    const cy = 830 + i * 180;
    p.push(txt(122, cy - 44, String(i + 1).padStart(2, '0'), 19, '#8AA3B0', 900));
    p.push(txt(486, cy, v.price[i], 69, INK, 900, 'end', 'font-variant-numeric="tabular-nums"'));
    p.push(txt(507, cy + 3, 'r', 33, SLATE, 800));
    p.push(`<path d="M630 ${cy-13}l14 13-14 13M655 ${cy-13}l14 13-14 13" fill="none" stroke="${v.accent}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>`);
    p.push(txt(790, cy, '累计带出', 25, SLATE, 800, 'middle'));
    p.push(txt(1324, cy, v.loot[i], 69, v.accent === GOLD ? '#9B6223' : '#167C73', 900, 'end', 'font-variant-numeric="tabular-nums"'));
    p.push(txt(1340, cy + 3, 'w', 31, v.accent === GOLD ? '#9B6223' : '#167C73', 900));
    p.push(crate(1433, cy - 28, .62, '#D9E6EB', v.accent === GOLD ? '#F2D29A' : '#9BD4C9', .9));
  }
  p.push(txt(91, 1870, '表内为基础累计物资保底，工作日活动按下方规则执行。', 22, SLATE, 700), txt(1512, 1870, 'r = 元   /   w = 万', 22, SLATE, 800, 'end'));
  p.push(txt(126, 2017, '工作日专享', 29, NAVY, 900), txt(126, 2074, '限时权益', 18, SLATE, 700), txt(470, 2015, '累计物资保底上浮', 31, NAVY, 800), txt(470, 2072, '补偿另计，不参与上浮', 20, SLATE, 700), txt(1455, 2058, '+20%', 81, v.accent === GOLD ? '#9B6223' : '#167C73', 1000, 'end'));
  // rescue rule: both eligibility chips are vertically centered in their panels
  p.push(`<path d="M184 2301v48l-32 18-32-18v-48l32-18Z" fill="none" stroke="#F2C777" stroke-width="5" stroke-linejoin="round"/><path d="m137 2338 12 12 23-27" fill="none" stroke="#F2C777" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>`);
  p.push(txt(184, 2403, '撤离保障', 25, '#FFFFFF', 900, 'middle'), txt(184, 2444, '成功撤离适用', 16, '#D5E2E7', 700, 'middle'));
  p.push(txt(344, 2290, '满足以下任意一项', 22, SLATE, 800));
  p.push(txt(537, 2367, '陪玩击杀人数 ≤4人', 22, NAVY, 900, 'middle'), txt(798, 2367, '或', 24, v.accent, 900, 'middle'), txt(1162, 2367, `当局物资 < ${v.threshold}w`, 22, v.accent === GOLD ? '#9B6223' : '#167C73', 900, 'middle'));
  p.push(txt(344, 2441, '当局带出的物资不计入累计保底', 29, NAVY, 900), txt(344, 2485, '两项为“或”的关系，任一满足即适用。', 19, SLATE, 700));
  p.push(txt(82, 2583, '下单须知', 34, NAVY, 900), txt(1515, 2583, '地图 · 随行装备 · 缺项补偿 · 结算', 19, SLATE, 800, 'end'));
  const gap1 = v.map;
  const gearFirst = v.gear === 40 ? '默认40格以上背包 + 一把左轮。进图前核对；' : '默认30格以上背包 + 一把左轮。进图前核对；';
  const [goldCard, redCard, backpack, revolver] = v.shortage;
  const cardItems = [
    ['地图与房卡', gap1],
    ['随行装备', [gearFirst, '板板主动免费除外。']],
    ['缺项补偿', [`应带未带：金卡 +${goldCard}w，红卡 +${redCard}w；`, `未经同意未带：背包 +${backpack}w，左轮 +${revolver}w。`]],
    ['炸单补偿', v.cancel],
    ['结算口径', ['符合俱乐部炸单标准时，安全箱带出的物资均不算；', `安全箱物资 >${v.safe}w 无需补战损，否则按炸单标准`, '增加累计保底。']],
    ['物资与售后', ['对局产出默认归板板；假账须提前征得板板同意，', '按约定价值计入。全程录屏，争议交客服核实。']],
  ];
  const coords = [[110, 2675], [846, 2675], [110, 2907], [846, 2907], [110, 3139], [846, 3139]];
  cardItems.forEach((item, i) => p.push(ruleCard(coords[i][0], coords[i][1], i + 1, item[0], item[1], v.accent)));
  p.push(logo(102, 3413, .72, NAVY), txt(148, 3432, '兴安电竞', 23, NAVY, 900), txt(1496, 3432, `${v.platform} · ${v.mode}   /   订单价 r=元 · 物资 w=万`, 18, SLATE, 800, 'end'));
  return rootSvg(1600, 3600, p.join(''));
}

function coverBackground() {
  return rootSvg(1600, 900, `<defs><pattern id="grid" width="68" height="68" patternUnits="userSpaceOnUse"><path d="M68 0H0V68" fill="none" stroke="#DCE8ED" stroke-width="1.2"/></pattern><linearGradient id="sky" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#EDF6F9"/><stop offset="1" stop-color="#FDFDF9"/></linearGradient></defs><rect width="1600" height="900" fill="#F9FCFD"/><rect width="1600" height="900" fill="url(#grid)" opacity=".5"/><path d="M0 0H1600V13H0Z" fill="${NAVY}"/><path d="M0 0H350V13H0Z" fill="${GOLD}"/><ellipse cx="1295" cy="440" rx="395" ry="340" fill="url(#sky)"/><ellipse cx="1295" cy="472" rx="295" ry="275" fill="#E5F0F3"/><path d="M740 900q130-110 280-20t255-8 325 0v28H740Z" fill="#DDEAF0"/><path d="M850 860 1010 794l160 71-161 74Z" fill="#F0D29C" opacity=".7"/><image href="data:image/png;base64,${mascot}" x="1015" y="146" width="505" height="505" preserveAspectRatio="xMidYMid meet"/>${crate(1220, 660, 1.8, '#DCE8ED', '#E8BD70', .97)}${crate(1068, 716, 1.42, '#C8D9E0', '#DDAA53', .94)}${crate(1374, 740, 1.34, '#CBDDE3', '#F0D198', .82)}<path d="M765 720h132" stroke="${GOLD}" stroke-width="4" stroke-linecap="round"/><circle cx="750" cy="720" r="8" fill="${GOLD}"/>`);
}

function coverOverlay() {
  const p = [`<image href="../backgrounds/cover.png" width="1600" height="900"/>`, logo(91, 84, 1.03), txt(157, 107, '兴安电竞', 42, NAVY, 900), txt(158, 147, 'XINGAN ESPORTS  ·  MATERIAL SUPPLY', 16, SLATE, 800, 'start', 'letter-spacing="1.5"')];
  p.push(txt(91, 354, '常规物资单', 120, NAVY, 1000, 'start', 'letter-spacing="1.5"'));
  p.push(`<rect x="96" y="434" width="420" height="72" rx="20" fill="${NAVY}"/>${txt(306, 470, '端游 · 手游 · 绝密 / 机密', 29, '#FFFFFF', 900, 'middle')}`);
  p.push(`<path d="M96 554H754" stroke="${GOLD}" stroke-width="3"/>`);
  p.push(txt(98, 613, '鼠鼠得吃 · 物资带出指南', 35, INK, 900), txt(98, 676, '看清每档投入，把战利品稳稳带回家。', 24, SLATE, 700));
  p.push(txt(98, 813, '常规物资  /  价目封面', 18, SLATE, 800, 'start', 'letter-spacing="1.2"'));
  p.push(star(827, 255, 19, GOLD), star(907, 340, 10, '#79B8AE'));
  return rootSvg(1600, 900, p.join(''));
}

function socialBackground() {
  return rootSvg(1080, 1350, `<defs><pattern id="grid" width="54" height="54" patternUnits="userSpaceOnUse"><path d="M54 0H0V54" fill="none" stroke="#DDE9EE" stroke-width="1.2"/></pattern><linearGradient id="hero" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#EDF6F8"/><stop offset="1" stop-color="#FCFBF6"/></linearGradient></defs><rect width="1080" height="1350" fill="#F8FBFC"/><rect width="1080" height="1350" fill="url(#grid)" opacity=".48"/><path d="M0 0H1080V12H0Z" fill="${NAVY}"/><path d="M0 0H380V12H0Z" fill="${TEAL}"/><ellipse cx="900" cy="242" rx="192" ry="212" fill="url(#hero)"/><ellipse cx="900" cy="275" rx="140" ry="150" fill="#E5F0F2"/><image href="data:image/png;base64,${mascot}" x="766" y="63" width="270" height="270" preserveAspectRatio="xMidYMid meet"/><path d="M0 1270q260-52 528-4t552-12v96H0Z" fill="#EAF3F5"/><rect x="64" y="691" width="952" height="506" rx="28" fill="#FFFFFF" stroke="#CCDEE4" stroke-width="2.5"/><path d="M90 691H990Q1016 691 1016 717V779H64V717Q64 691 90 691Z" fill="${NAVY}"/>${[0,1,2,3].map(i=>`<rect x="66" y="${779+i*104}" width="948" height="103.5" fill="${i%2?'#FFFFFF':'#EFF6F8'}"/><path d="M90 ${882+i*104}H990" stroke="#DAE7EC" stroke-width="1.5"/>`).join('')}<rect x="64" y="1200" width="952" height="91" rx="22" fill="#E3F0F3"/>${crate(830, 1234, .56, '#D7E5E9', '#EAC77D', .75)}`);
}

function socialOverlay() {
  const p = [`<image href="../backgrounds/social-special-price.png" width="1080" height="1350"/>`, logo(64, 56, .75), txt(111, 75, '兴安电竞', 28, NAVY, 900), txt(112, 101, 'XINGAN ESPORTS', 11.5, SLATE, 800, 'start', 'letter-spacing="1.1"')];
  p.push(txt(69, 193, '工作日专享', 47, NAVY, 900), txt(70, 251, '累计物资保底上浮', 29, SLATE, 800));
  p.push(txt(67, 422, '+20%', 135, '#167C73', 1000, 'start', 'letter-spacing="-2"'), txt(605, 415, '补偿另计', 23, NAVY, 900), txt(605, 451, '不参与上浮', 18, SLATE, 700));
  p.push(txt(102, 736, '版本', 21, '#FFFFFF', 900), txt(576, 736, '订单价格', 21, '#FFFFFF', 900, 'end'), txt(600, 736, 'r', 18, '#D8E7EB', 900), txt(868, 736, '累计保底', 21, '#FFFFFF', 900, 'end'), txt(896, 736, 'w', 18, '#D8E7EB', 900));
  const entries = [
    ['端游 · 绝密', 188, 866], ['端游 · 机密', 188, 1266], ['手游 · 绝密', 168, 1388], ['手游 · 机密', 168, 1588],
  ];
  entries.forEach(([label, price, loot], i) => {
    const y = 831 + i * 104;
    p.push(txt(103, y, label, 23, NAVY, 800), txt(577, y, price, 31, INK, 900, 'end', 'font-variant-numeric="tabular-nums"'), txt(600, y + 2, 'r', 19, SLATE, 800), txt(868, y, loot, 31, '#167C73', 900, 'end', 'font-variant-numeric="tabular-nums"'), txt(896, y + 2, 'w', 19, '#167C73', 900));
  });
  p.push(txt(99, 1244, '基础档参考', 20, NAVY, 900), txt(740, 1244, '各档完整价目见版本海报', 17, SLATE, 700, 'middle'));
  p.push(txt(540, 1310, '鼠鼠得吃 · 带出更有底气', 19, NAVY, 800, 'middle'));
  return rootSvg(1080, 1350, p.join(''));
}

async function writeBase(id, svg, width, height) {
  const svgPath = path.join(root, 'backgrounds', `${id}.svg`);
  const pngPath = path.join(root, 'backgrounds', `${id}.png`);
  await writeFile(svgPath, svg, 'utf8');
  await sharp(Buffer.from(svg)).resize(width, height).png({ compressionLevel: 8, adaptiveFiltering: true }).toFile(pngPath);
  return pngPath;
}

const renders = [];
for (const v of variants) {
  await writeBase(v.id, posterBackground(v), 3200, 7200);
  const svg = posterOverlay(v);
  const src = path.join(root, 'source', `${v.id}.svg`);
  await writeFile(src, svg, 'utf8');
  renders.push({ src, out: path.join(root, 'png', `${v.id}.png`), width: 1600, height: 3600, scale: 2 });
}
await writeBase('cover', coverBackground(), 3200, 1800);
const coverSrc = path.join(root, 'source', 'cover.svg');
await writeFile(coverSrc, coverOverlay(), 'utf8');
renders.push({ src: coverSrc, out: path.join(root, 'png', 'cover.png'), width: 1600, height: 900, scale: 2 });
await writeBase('social-special-price', socialBackground(), 2160, 2700);
const socialSrc = path.join(root, 'source', 'social-special-price.svg');
await writeFile(socialSrc, socialOverlay(), 'utf8');
renders.push({ src: socialSrc, out: path.join(root, 'png', 'social-special-price.png'), width: 1080, height: 1350, scale: 2 });

for (const r of renders) {
  const id = path.basename(r.src, '.svg');
  const bg = (await readFile(path.join(root, 'backgrounds', `${id}.png`))).toString('base64');
  const vector = (await readFile(r.src, 'utf8')).replace(
    `href="../backgrounds/${id}.png"`,
    `href="data:image/png;base64,${bg}"`,
  );
  await sharp(Buffer.from(vector))
    .resize(r.width * r.scale, r.height * r.scale, { fit: 'fill' })
    .png({ compressionLevel: 8, adaptiveFiltering: true })
    .toFile(r.out);
}

console.log('Rendered assets:');
for (const r of renders) {
  const meta = await sharp(r.out).metadata();
  console.log(`${path.relative(root, r.out)} — ${meta.width}×${meta.height}`);
}
