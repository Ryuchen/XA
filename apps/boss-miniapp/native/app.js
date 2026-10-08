const realtime = require('./core/realtime');
App({
  onShow() { realtime.show(); },
  onHide() { realtime.hide(); },
});
