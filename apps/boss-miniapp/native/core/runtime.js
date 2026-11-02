// Official multi-platform markers: getAppBaseInfo().host.env === SAAASDK,
// or the SDK's miniapp/host account IDs. miniProgram.envVersion is invalid in App.
const environments = ['develop', 'trial', 'release'];
function runtime() {
  let info = {}, base = {}, device = {}, baseFailed = false;
  try { info = wx.getAccountInfoSync() || {}; } catch (_) {}
  try { if (typeof wx.getAppBaseInfo === 'function') base = wx.getAppBaseInfo() || {}; } catch (_) { baseFailed = true; }
  try { if (typeof wx.getDeviceInfo === 'function') device = wx.getDeviceInfo() || {}; } catch (_) {}
  const app = base.host && base.host.env === 'SAAASDK'
    || info.miniapp && info.miniapp.miniappId || info.host && info.host.miniappId;
  if (app) {
    let value = info.miniapp && info.miniapp.envVersion;
    // The official IDE simulator exposes SAAASDK + devtools but no App account IDs.
    // Permit only its explicit develop value; never select trial/release from this field.
    const simulator = device.platform === 'devtools';
    if (!value && simulator && base.host && base.host.env === 'SAAASDK'
        && info.miniProgram && info.miniProgram.envVersion === 'develop') value = 'develop';
    return { platform: 'app', envVersion: environments.includes(value) ? value : null, phoneAuthorizationSupported: false };
  }
  if (baseFailed) return { platform: 'unknown', envVersion: null, phoneAuthorizationSupported: false };
  if (info.miniProgram) {
    const value = info.miniProgram.envVersion;
    return { platform: 'wechat', envVersion: environments.includes(value) ? value : 'release', phoneAuthorizationSupported: true };
  }
  return { platform: 'unknown', envVersion: null, phoneAuthorizationSupported: false };
}
module.exports = { runtime };
