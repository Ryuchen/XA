const { money, image } = require('./ui');

const serviceTabs = [
  { id: 'experience', name: '体验单' },
  { id: 'regular', name: '常规单' },
  { id: 'companion', name: '陪玩单' },
  { id: 'fun', name: '趣味单' },
  { id: 'other', name: '其他' },
];

function serviceTabForGameplay(gameplay, service) {
  const code = String(gameplay && gameplay.code || '').replace(/^xa-catalog-/, '');
  if (service && /瓦罗兰特|无畏契约|英雄联盟/.test(service.game_category_name || '')) return 'companion';
  if (service && /跑刀|部门任务|安全箱/.test([service.name, service.catalog_family].join(' '))) return 'other';
  if (code === 'experience') return 'experience';
  if (code === 'companion') return 'companion';
  if (['fun', 'roulette'].includes(code)) return 'fun';
  if (code === 'mission') return 'other';
  // Supplies, banks, extraction challenges, missions and custom services.
  return 'regular';
}

function familyKey(item) {
  return item.catalog_family ? JSON.stringify([item.game_category || '', item.gameplay || '', item.catalog_family]) : 'service:' + item.id;
}

// Keep prices as separate purchasable items; only the discovery cards are grouped.
function groupServices(items) {
  const groups = new Map();
  for (const item of items) {
    if (item.is_gift || item.is_addon) continue;
    const key = familyKey(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return Array.from(groups, ([key, variants]) => {
    const first = variants.reduce((best, item) => Number(item.price) < Number(best.price) ? item : best);
    const labels = field => Array.from(new Set(variants.map(item => item[field]).filter(Boolean))).join(' / ');
    return { ...first, familyKey: key, name: first.catalog_family || first.name,
      description: first.description,
      platform_name: labels('platform_name'), security_level_name: labels('security_level_name'),
      variantCount: variants.length, cover: image(first.cover_url || first.gameplay_cover_url || first.cover), displayPrice: money(first.price) };
  });
}

function variantPresentation(item, selected) {
  const name = item.catalog_variant || item.name || '';
  const guarantee = (name.match(/保底\s*[\d,.，]+\s*(?:[wW万亿kK千])?(?:\s*[-–~～至]\s*[\d,.，]+\s*(?:[wW万亿kK千])?)?/) || [''])[0].trim();
  const context = new Set([item.platform_name, selected.platform_name, item.security_level_name, selected.security_level_name].filter(Boolean));
  const displayName = name.replace(guarantee, '').split(/\s*[·•|｜]\s*/).map(part => part.trim()).filter(part => part && !context.has(part)).join(' · ');
  return { displayName: displayName || (guarantee ? '服务套餐' : name), guarantee };
}

function variantOptions(items, selected) {
  const normal = items.filter(item => !item.is_addon && !item.is_gift);
  const unique = (rows, field, label) => Array.from(new Map(rows.map(item => [item[field] || '', { id: item[field] || '', name: item[label] || '通用' }])).values());
  const platforms = unique(normal, 'platform', 'platform_name');
  const samePlatform = normal.filter(item => item.platform === selected.platform);
  const levels = unique(samePlatform, 'security_level', 'security_level_name');
  const choices = samePlatform.filter(item => item.security_level === selected.security_level).map(item => ({
    id: item.id, name: item.catalog_variant || item.name, price: item.price,
    displayPrice: money(item.price), price_unit: item.price_unit || '份', description: item.description,
    ...variantPresentation(item, selected),
  }));
  const addons = items.filter(item => item.is_addon && (!item.platform || item.platform === selected.platform) && (!item.security_level || item.security_level === 'ANY' || item.security_level === selected.security_level)).map(item => ({
    id: item.id, name: item.catalog_variant || item.name, displayPrice: money(item.price), price_unit: item.price_unit || '份',
  }));
  return { variantPlatforms: platforms, variantLevels: levels, variantChoices: choices, variantTotal: normal.length,
    variantIndex: Math.max(0, choices.findIndex(item => item.id === selected.id)),
    variantLabels: choices.map(item => item.name + ' · ' + item.displayPrice + '币/' + item.price_unit), addons };
}

module.exports = { familyKey, groupServices, variantOptions, serviceTabs, serviceTabForGameplay };
