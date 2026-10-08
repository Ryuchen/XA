const { image, money } = require('./ui');

function prepareCompanion(item) {
  return { ...item, qualifications: (item.qualifications || []).filter(q => q.is_active),
    avatarUrl: image(item.avatar), voiceUrl: image(item.voice_card_url),
    monthlyAmountText: money(item.monthly_order_amount),
    hasHourlyPrice: item.hasReferenceHourlyPrice === undefined ? Number(item.pricePerHour) > 0 : !!item.hasReferenceHourlyPrice,
    hourlyPriceText: money(item.pricePerHour), initial: (item.nickname || '伴').slice(0, 1) };
}

module.exports = { prepareCompanion };
