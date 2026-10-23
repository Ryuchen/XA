const auth = require('./auth');
const events = require('./events');
const { config } = require('./config');
let socket = null;
let retry = null;
let heartbeat = null;
let reply = null;
let opening = null;
let visible = false;
let attempts = 0;
let generation = 0;
function disconnect() {
  generation += 1;
  clearTimeout(retry); retry = null;
  clearInterval(heartbeat); heartbeat = null;
  clearTimeout(reply); reply = null;
  clearTimeout(opening); opening = null;
  const old = socket; socket = null;
  if (old) old.close({});
}
async function connect(recover = false) {
  disconnect();
  const version = generation;
  if (recover && visible && auth.getSession()) {
    try {
      // The HTTP path refreshes expired credentials and rejects logged-out users.
      await require('./api').request('/users/me/');
    } catch (_) {
      if (version === generation && visible && auth.getSession()) {
        retry = setTimeout(() => { retry = null; connect(true); }, Math.min(30000, 2000 * Math.pow(2, Math.min(attempts++, 4))));
      }
      return;
    }
  }
  if (version !== generation) return;
  const session = auth.getSession();
  if (!visible || !session) return;
  let url;
  try { url = config().socketBase + '/ws/orders/?token=' + encodeURIComponent(session.token); } catch (_) { return; }
  let current;
  try { current = wx.connectSocket({ url, fail: () => {} }); } catch (_) {
    retry = setTimeout(() => { retry = null; connect(true); }, Math.min(30000, 2000 * Math.pow(2, Math.min(attempts++, 4))));
    return;
  }
  socket = current;
  const schedule = () => {
    if (socket !== current || !visible || retry) return;
    clearInterval(heartbeat); heartbeat = null;
    clearTimeout(reply); reply = null;
    clearTimeout(opening); opening = null;
    retry = setTimeout(() => { retry = null; connect(true); }, Math.min(30000, 2000 * Math.pow(2, Math.min(attempts++, 4))));
  };
  opening = setTimeout(schedule, 10000);
  current.onOpen(() => {
    if (socket !== current) return;
    clearTimeout(opening); opening = null;
    clearTimeout(retry); retry = null;
    attempts = 0;
    events.emit('order.changed');
    events.emit('message.changed');
    events.emit('feed.changed');
    events.emit('wallet.changed', { account_id: auth.getSession()?.user?.id });
    const ping = () => {
      if (socket !== current) return;
      reply = setTimeout(schedule, 10000);
      current.send({ data: '{"type":"ping"}', fail: schedule });
    };
    ping();
    if (socket !== current || retry) return;
    heartbeat = setInterval(ping, 25000);
  });
  current.onMessage(result => {
    if (socket !== current) return;
    try {
      const message = JSON.parse(result.data);
      if (message.type === 'pong') { clearTimeout(reply); reply = null; return; }
      if (message.type === 'order_status_update') events.emit('order.changed', message.data);
      if (message.type === 'wallet_update' && message.data?.account_id != null
          && String(message.data.account_id) === String(auth.getSession()?.user?.id)) events.emit('wallet.changed', message.data);
      if (message.type === 'club_feed_update') events.emit('feed.changed');
      if (['chat_message', 'chat_session_update'].includes(message.type)) events.emit('message.changed', message.data);
    } catch (_) { /* Ignore malformed realtime payload; HTTP remains the source of truth. */ }
  });
  current.onClose(schedule);
  current.onError(schedule);
}
events.on('session.changed', () => connect());
events.on('token.changed', () => connect());
module.exports = { show() { visible = true; connect(); }, hide() { visible = false; disconnect(); } };
