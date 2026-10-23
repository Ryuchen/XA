const rarities = ['SSR', 'SR', 'R'];
function campaignView(campaign) {
  if (!campaign) return null;
  const pity = campaign.pity || {};
  const limit = Number.isInteger(pity.limit) && pity.limit > 0 ? pity.limit : 240;
  const count = Math.min(limit, Math.max(0, Number(pity.count) || 0));
  const tenGuarantee = campaign.ten_guarantee || {};
  return { ...campaign, ten_draw_cost: Number.isFinite(campaign.ten_draw_cost) ? campaign.ten_draw_cost : campaign.point_cost * 10,
    daily_remaining: Number.isInteger(campaign.daily_remaining) ? Math.max(0, campaign.daily_remaining) : null,
    ten_guarantee: { ...tenGuarantee, enabled: tenGuarantee.enabled === true, available: tenGuarantee.available === true,
      unavailable_reason: String(tenGuarantee.unavailable_reason || tenGuarantee.reason || '十连奖池准备中，请稍后再试') },
    prizes: (campaign.prizes || []).map(row => ({ ...row, rarity: rarities.includes(row.rarity) ? row.rarity : 'R', probabilityText: (Number(row.probability) / 100).toString() })),
    pity: { ...pity, limit, count, remaining: Math.max(0, Number.isInteger(pity.remaining) ? pity.remaining : limit - count), percent: Math.min(100, count / limit * 100) } };
}
function resultView(response, expectedCount) {
  if (!response || response.count !== expectedCount || !Array.isArray(response.draws) || response.draws.length !== expectedCount || !Number.isFinite(response.points_balance) || response.points_balance < 0) throw new Error('抽奖结果格式异常');
  const ids = new Set();
  return response.draws.map((row, index) => {
    if (!row || !Number.isInteger(row.id) || !Number.isInteger(row.prize_id) || !row.claim || !Number.isInteger(row.claim.id) || !row.claim.name_snapshot || !rarities.includes(row.rarity) || ids.has(row.claim.id)) throw new Error('抽奖奖品记录异常');
    ids.add(row.claim.id);
    return { id: row.id, prize_id: row.prize_id, claim_id: row.claim.id, name: row.claim.name_snapshot,
      rarity: row.rarity, rarityLabel: ({ SSR: 'SSR · 典藏', SR: 'SR · 稀有', R: 'R · 积分' })[row.rarity],
      is_pity: row.is_pity === true, is_ten_guarantee: row.is_ten_guarantee === true, number: String(index + 1).padStart(2, '0'),
      claimLabel: row.claim.status === 'COMPLETED' ? '查看奖励' : '领取奖励', statusLabel: row.claim.status === 'COMPLETED' ? '已到账' : '待领取' };
  });
}
module.exports = { campaignView, resultView };
