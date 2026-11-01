const auth = require('./auth');
const events = require('./events');

let visible = false;
let sequence = 0;

function badge(count) {
  if (count > 0) wx.setTabBarBadge({ index: 3, text: count > 99 ? '99+' : String(count) });
  else wx.removeTabBarBadge({ index: 3 });
}

async function refresh() {
  const current = ++sequence;
  const revision = auth.revision();
  if (!auth.getSession()) { badge(0); return; }
  if (!visible) return;
  try {
    const data = await require('./api').request('/messages/unread-count/');
    if (visible && current === sequence && revision === auth.revision()) badge(Number(data.unread) || 0);
  } catch (_) { /* On reconnect or foreground the count is read again. */ }
}

events.on('site-message.changed', refresh);
events.on('site-message.badge', refresh);
events.on('session.changed', () => { ++sequence; badge(0); if (visible) refresh(); });

module.exports = {
  show() { visible = true; refresh(); },
  hide() { visible = false; ++sequence; },
};
