const { privatePage } = require('../../core/page');
const { request } = require('../../core/api');
const { money } = require('../../core/ui');
Page(privatePage({ data: { items: [] }, async fetch() {
  const items = await request('/users/achievements/');
  return { items: items.map(item => ({ ...item, currentText: item.metric === 'amount' ? money(item.current) : item.current, targetText: item.metric === 'amount' ? money(item.target) : item.target })) };
} }));
