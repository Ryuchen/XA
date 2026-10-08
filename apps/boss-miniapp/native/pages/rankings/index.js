const { privatePage } = require('../../core/page');
const { request } = require('../../core/api');
const { image, money } = require('../../core/ui');
Page(privatePage({
  data: { period: 'month', periods: [{ value: 'day', label: '今日' }, { value: 'week', label: '本周' }, { value: 'month', label: '本月' }], consumption: [], providers: [] },
  async fetch() {
    const result = await request('/orders/rankings/?period=' + this.data.period);
    const format = items => (items || []).map(item => ({ ...item, avatar: image(item.avatar), amountText: money(item.total_amount) }));
    return { consumption: format(result.consume_rank), providers: format(result.order_rank) };
  },
  period(event) { this.setData({ period: event.currentTarget.dataset.value, consumption: [], providers: [] }); this.load(); },
}));
