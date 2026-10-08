const automator = require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

(async () => {
  const out = path.resolve(__dirname, '../.tools/tencent-deploy');
  // Start official CLI separately on Windows; Node cannot spawn a .bat directly.
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9430' });
  const report = { api: 'https://api.xaesports.club', legalDomainCheck: true,
    phoneAuthorizationTested: false, realDeviceTested: false };
  try {
    const login = await mini.callWxMethod('login', { timeout: 10000 });
    assert(login.code, 'WeChat did not issue a login code');
    const response = await fetch(report.api + '/api/users/wechat-login/', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: login.code, checkOnly: true }),
      signal: AbortSignal.timeout(30000),
    });
    const result = await response.json();
    assert(response.ok && result.code === 0,
      `Real WeChat exchange failed: HTTP ${response.status}, code ${result.code}, ${result.msg}`);
    report.realWxLoginCode = true;
    report.backendExchange = true;
    report.requiresBinding = result.data.requiresBinding === true;
    report.existingAccountRecognized = !!result.data.token;
    const home = await mini.reLaunch('/pages/home/index');
    await home.waitFor(async () => !(await home.data('loading')));
    const data = await home.data();
    assert(!data.error, `Native home: ${data.error}`);
    report.gameplays = data.gameplays.length;
    report.services = data.services.length;
    await mini.screenshot({ path: path.join(out, 'native-cloud-home.png') });
    const catalog = await mini.reLaunch('/pages/catalog/index');
    await catalog.waitFor(async () => !(await catalog.data('loading')));
    const catalogData = await catalog.data();
    assert(!catalogData.error, `Native catalog: ${catalogData.error}`);
    await mini.screenshot({ path: path.join(out, 'native-cloud-catalog.png') });
    report.homeAndCatalog = true;
    report.brandArtwork = [];
    const artwork = {
      companionshipArt: 'companionship.ef8c5df7748b.png',
      giftMomentArt: 'gift-moment.621950ab1523.png',
    };
    for (const [field, filename] of Object.entries(artwork)) {
      const url = report.api + '/static/miniapp/brand/' + filename;
      const response = await fetch(url, { signal: AbortSignal.timeout(20000) });
      assert(response.ok, `${filename}: HTTP ${response.status}`);
      assert.match(response.headers.get('content-type') || '', /^image\/png/);
      const bytes = Buffer.from(await response.arrayBuffer());
      const original = fs.readFileSync(path.resolve(__dirname,
        '../services/backend/engagement/static/miniapp/brand', filename));
      assert(bytes.equals(original), `${filename} changed during publication`);
      const image = await mini.callWxMethod('getImageInfo', { src: url });
      assert(image.width > 0 && image.height > 0, `${filename} could not be decoded by WeChat`);
      report.brandArtwork.push({ field, url, bytes: bytes.length,
        sha256: createHash('sha256').update(bytes).digest('hex'), width: image.width, height: image.height });
    }
    report.artworkPages = [];
    for (const name of ['home', 'login', 'mine', 'gifts']) {
      const page = await mini.reLaunch('/pages/' + name + '/index');
      const pageData = await page.data();
      for (const [field, filename] of Object.entries(artwork)) {
        assert.equal(pageData[field], report.api + '/static/miniapp/brand/' + filename, `${name}: ${field}`);
      }
      await page.waitFor(1200);
      await mini.screenshot({ path: path.join(out, 'native-cloud-' + name + '-artwork.png') });
      report.artworkPages.push(name);
    }
    fs.writeFileSync(path.join(out, 'native-cloud-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { mini.disconnect(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
