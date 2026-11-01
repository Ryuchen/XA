const realtime = require('./core/realtime');
const messageBadge = require('./core/message-badge');
App({
  onShow() { messageBadge.show(); realtime.show(); },
  onHide() { messageBadge.hide(); realtime.hide(); },
});
