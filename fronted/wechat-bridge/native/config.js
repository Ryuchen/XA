// AppSecret and SMS credentials belong only in the backend environment.
module.exports = {
  develop: { apiBase: 'http://127.0.0.1:18080/api' },
  trial: { apiBase: 'https://api.xaesports.club/api' },
  release: { apiBase: 'https://api.xaesports.club/api' },
};
