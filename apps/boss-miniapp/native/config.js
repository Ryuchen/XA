// AppSecret 仅配置在后端。体验版/正式版必须填写已配置合法域名的 HTTPS API。
module.exports = {
  develop: { apiBase: 'http://127.0.0.1:18080/api', socketBase: '' },
  trial: { apiBase: 'https://api.xaesports.club/api', socketBase: 'wss://api.xaesports.club' },
  release: { apiBase: 'https://api.xaesports.club/api', socketBase: 'wss://api.xaesports.club' },
};
