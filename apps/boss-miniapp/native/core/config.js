const environments = require('../config');
function config() {
  let env = 'release';
  try { env = wx.getAccountInfoSync().miniProgram.envVersion || 'release'; } catch (_) { /* fail closed */ }
  const selected = environments[env] || environments.release;
  const apiBase = (selected.apiBase || '').replace(/\/$/, '');
  if (!apiBase || (env !== 'develop' && !/^https:\/\//.test(apiBase))) {
    throw new Error('服务地址尚未配置，请联系管理员');
  }
  return {
    apiBase,
    mediaBase: apiBase.replace(/\/api$/, ''),
    socketBase: (selected.socketBase || apiBase.replace(/\/api$/, '').replace(/^http/, 'ws')).replace(/\/+$/, ''),
  };
}
module.exports = { config };
