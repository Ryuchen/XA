const api = require('../../core/api');
const auth = require('../../core/auth');
const ui = require('../../core/ui');
const events = require('../../core/events');
const { privatePage } = require('../../core/page');
Page(privatePage({
  data: { loading: false, error: '', calendar: null, cells: [], rewards: [], submitting: false, weekdays: ['日','一','二','三','四','五','六'] },
  async fetch() {
      const calendar = await api.request('/users/checkin/');
      const checked = ui.list(calendar.checked_days);
      const available = ui.list(calendar.makeup_available_days);
      const cells = [];
      for (let day = 0; day < new Date(calendar.year, calendar.month - 1, 1).getDay(); day++) cells.push({ key: 'blank-' + day, day: 0 });
      for (let day = 1; day <= calendar.days_in_month; day++) cells.push({ key: 'day-' + day, day, checked: checked.includes(day), today: day === calendar.today, makeup: available.includes(day) });
      const remaining = Math.max(Number(calendar.daily_spend_required) - Number(calendar.today_spend), 0);
      return { calendar, cells,
        rewards: ui.list(calendar.rewards).map(item => Object.assign({}, item, { amountText: ui.money(item.amount), reached: calendar.checked_count >= item.seq })),
        spendText: ui.money(calendar.today_spend), thresholdText: ui.money(calendar.daily_spend_required), cardThresholdText: ui.money(calendar.makeup_card_spend_required),
        progress: calendar.daily_spend_required > 0 ? Math.min(100, Math.max(0, calendar.today_spend / calendar.daily_spend_required * 100)) : 100,
        buttonText: calendar.today_checked ? '今日已签到' : calendar.today_eligible ? '立即签到' : '再消费 ' + ui.money(remaining) + ' 兴安币可签到' };
  },
  checkin() {
    const calendar = this.data.calendar;
    if (!calendar || this.data.submitting || this.data.loading || calendar.today_checked || !calendar.today_eligible) return;
    return this.submit();
  },
  async makeup(e) {
    const day = Number(e.currentTarget.dataset.day);
    const calendar = this.data.calendar;
    if (!calendar || !day || this.data.submitting || this.data.loading || !ui.list(calendar.makeup_available_days).includes(day)) return;
    if (!calendar.makeup_cards) { ui.toast('暂无补签卡，消费达标后可获得'); return; }
    this.setData({ submitting: true });
    try {
      const choice = await new Promise((resolve, reject) => wx.showModal({ title: '补签 ' + calendar.month + ' 月 ' + day + ' 日', content: '将消耗 1 张补签卡，当前共有 ' + calendar.makeup_cards + ' 张。', confirmText: '确认补签', success: resolve, fail: reject }));
      if (choice.confirm) await this.submit(day, true);
    } catch (error) { ui.toast(error); }
    finally { this.setData({ submitting: false }); }
  },
  async submit(day, alreadyLocked) {
    if ((!alreadyLocked && this.data.submitting) || !auth.ensure()) return;
    this.setData({ submitting: true });
    try {
      const result = await api.request('/users/checkin/', 'POST', day ? { day } : {});
      const reward = result.reward_amount > 0 ? '，获得 ' + ui.money(result.reward_amount) + ' 兴安币' : '';
      ui.toast((day ? '补签成功' : '签到成功') + reward);
      events.emit('wallet.changed');
      if (result.full_attendance_awarded) wx.showModal({ title: '本月全勤达成', content: this.data.calendar.full_attendance_reward.name || '已获得全勤奖励', showCancel: false });
    } catch (error) { ui.toast(error); }
    finally { await this.load(); this.setData({ submitting: false }); }
  }
}));
