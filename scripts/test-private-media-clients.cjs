const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../apps/provider-miniapp/node_modules/typescript');
const root = path.resolve(__dirname, '..');

function nativePage(request) {
  let page;
  const previews = [];
  const notices = [];
  vm.runInNewContext(fs.readFileSync(path.join(root, 'apps/boss-miniapp/native/pages/chat/index.js'), 'utf8'), {
    Page: value => { page = value; },
    wx: { previewImage: data => previews.push(data) },
    require: name => ({
      '../../core/auth': {}, '../../core/api': { request }, '../../core/events': {},
      '../../core/ui': { image: url => url, toast: error => notices.push(error) },
    })[name],
  });
  page._alive = page._visible = true;
  page.data.content = '尚未发送的咨询';
  return { page, previews, notices };
}

function providerMedia(request) {
  const previews = [];
  const notices = [];
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, 'apps/provider-miniapp/src/utils/privateMedia.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
  }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports, require: name => {
    if (name === './request') return { request };
    if (name === '@tarojs/taro') return { previewImage: data => previews.push(data), showToast: data => notices.push(data) };
    throw new Error(name);
  } });
  return { media: mod.exports, previews, notices };
}

test('native preview renews only the selected image without reloading chat or clearing draft', async () => {
  const calls = [];
  const { page, previews } = nativePage(async (...args) => { calls.push(args); return { url: 'https://api.test/fresh.png' }; });
  await page.preview({ currentTarget: { dataset: { url: 'https://api.test/api/media/private/expired:signature/' } } });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/media/renew/');
  assert.equal(calls[0][2].ticket, 'expired:signature');
  assert.equal(previews[0].urls[0], 'https://api.test/fresh.png');
  assert.equal(page.data.content, '尚未发送的咨询');
});

test('native preview stops after page unload and coalesces repeated taps', async () => {
  let finish;
  let calls = 0;
  const { page, previews } = nativePage(() => { ++calls; return new Promise(resolve => { finish = resolve; }); });
  const event = { currentTarget: { dataset: { url: '/api/media/private/expired/' } } };
  const first = page.preview(event);
  await page.preview(event);
  page._alive = false;
  finish({ url: '/fresh.png' });
  await first;
  assert.equal(calls, 1);
  assert.equal(previews.length, 0);
});

test('native revoked-image errors preserve draft and leave preview retryable', async () => {
  const { page, previews, notices } = nativePage(async () => { throw new Error('已撤权'); });
  await page.preview({ currentTarget: { dataset: { url: '/api/media/private/expired/' } } });
  assert.equal(previews.length, 0);
  assert.equal(notices.length, 1);
  assert.equal(page.data.content, '尚未发送的咨询');
  assert.equal(page._previewing, false);
});

test('provider evidence gallery renews all images and retains the selected image index', async () => {
  const calls = [];
  const { media, previews } = providerMedia(async (url, method, data) => {
    calls.push({ url, method, data });
    return { code: 0, data: { url: `/fresh/${data.ticket}.png` } };
  });
  const urls = ['/api/media/private/first/', '/api/media/private/second/'];
  await media.previewPrivateImages(urls[1], urls);
  assert.equal(calls.length, 2);
  assert.equal(previews[0].current, '/fresh/second.png');
  assert.equal(previews[0].urls.length, 2);
});

test('public images bypass the private-image renewal endpoint', async () => {
  const { media, previews } = providerMedia(async () => { throw new Error('Should not request'); });
  await media.previewPrivateImages('/media/banners/image.png', ['/media/banners/image.png']);
  assert.equal(previews[0].current, '/media/banners/image.png');
});

test('provider renewal failure displays an error and does not open a broken viewer', async () => {
  const { media, previews, notices } = providerMedia(async () => ({ code: 404, msg: '图片已变更' }));
  await media.previewPrivateImages('/api/media/private/old/', ['/api/media/private/old/']);
  assert.equal(previews.length, 0);
  assert.equal(notices[0].title, '图片已变更');
});
