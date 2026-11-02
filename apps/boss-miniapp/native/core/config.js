const environments = require('../config');
const { runtime } = require('./runtime');
function config() {
  const current = runtime();
  const env = current.envVersion;
  if (!env) throw new Error('无法确认运行环境，请重新进入后重试');
  const selected = environments[env];
  if (!selected) throw new Error('当前运行环境的服务地址尚未配置，请联系管理员');
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
