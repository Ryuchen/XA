// Isolated WeChat IDE project, real account, HTTP, Redis and order confirmation.
const assert = require('node:assert/strict'), fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const automator = require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const x = JSON.parse(fs.readFileSync(0, 'utf8'));
async function wait(page, predicate, message) {
  let timer;
  try { await Promise.race([page.waitFor(predicate), new Promise((_, reject) => { timer = setTimeout(() => reject(Error(message)), process.env.XA_WECHAT_PHYSICAL_TAP === '1' ? 45000 : 15000); })]); }
  finally { clearTimeout(timer); }
}
(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:19435' });
  let original, originalPage, failure, page, chipDetails;
  const messages = [];
  mini.on('exception', event => messages.push(JSON.stringify(event)));
  try {
    const device = await mini.systemInfo();
    assert.equal(device.model, 'iPhone 12/13 (Pro)'); assert.equal(device.windowWidth, 390);
    originalPage = await mini.currentPage();
    original = await mini.evaluate(() => ({ api: require('config.js').develop.apiBase,
      socket: require('config.js').develop.socketBase, session: require('core/auth.js').getSession() }));
    await mini.evaluate(value => {
      const config = require('config.js').develop; config.apiBase = value.base + '/api';
      config.socketBase = value.wsBase.replace(/^http/, 'ws');
      require('core/auth.js').setSession({ token: value.token, user: { id: value.account, role: 'customer' } });
    }, x);
    page = await mini.reLaunch('/pages/membership/index');
    await wait(page, async () => (await page.data()).tags?.some(tag => tag.title === '体验标签'), '会员标签未加载');
    const key = (await page.data()).tags.find(tag => tag.title === '体验标签').key;
    const chip = await page.$(`button[data-key="${key}"]`); assert.ok(chip);
    chipDetails = { wxml: await chip.outerWxml(), disabled: await chip.property('disabled'), size: await chip.size() };
    await new Promise(resolve => setTimeout(resolve, 800));
    await mini.pageScrollTo(Math.max(0, (await chip.offset()).top - 150));
    if (process.env.XA_WECHAT_PHYSICAL_TAP === '1') {
      console.log('READY_FOR_PHYSICAL_TAG_TAP');
    } else {
      await chip.tap();
      await new Promise(resolve => setTimeout(resolve, 500));
      if (!(await page.data()).selected?.includes(key)) { console.log('标签选择：IDE tap 未生效，改用框架事件'); await chip.trigger('tap'); }
    }
    await mini.screenshot({ path: x.screenshot.replace('.png', '-tag-click.png') });
    await wait(page, async () => (await page.data()).selected?.includes(key), '标签选择未生效');
    assert.deepEqual((await page.data()).selected, [key]);
    const task = spawnSync(x.python, ['-c', `import django; django.setup(); from engagement.confirmation import process_order; assert process_order(${x.orderId})`],
      { cwd: x.backend, env: process.env, encoding: 'utf8', timeout: 30000 });
    assert.equal(task.status, 0, task.stderr || task.stdout);
    await wait(page, async () => (await page.data()).refreshPending === true, '会员信息更新提示未显示');
    const notice = await page.$('.membership-refresh-notice'); assert.ok(notice);
    assert.match(await notice.text(), /会员信息已更新/);
    assert.ok((await notice.size()).width <= device.windowWidth);
    await mini.pageScrollTo(Math.max(0, (await notice.offset()).top - 120));
    await mini.screenshot({ path: x.screenshot });
    const discard = await notice.$('button'); await discard.tap();
    await new Promise(resolve => setTimeout(resolve, 500));
    if ((await page.data()).refreshPending) { console.log('撤销按钮：IDE tap 未生效，改用框架事件'); await discard.trigger('tap'); }
    await wait(page, async () => (await page.data()).member?.vip?.level === 6 && !(await page.data()).refreshPending, '撤销后会员信息未刷新');
    assert.deepEqual((await page.data()).selected, []);
    console.log('iPhone 12 Pro: real VIP update and pending notice verified; check tap fallback messages');
  } catch (error) {
    if (page) {
      try { const data = await page.data(); console.error('验收状态', JSON.stringify({ selected:data.selected, busy:data.busy, loading:data.loading, error:data.error, tags:data.tags, refreshPending:data.refreshPending })); } catch (_) {}
    }
    if (chipDetails) console.error('标签控件', JSON.stringify(chipDetails));
    if (messages.length) console.error('小程序异常', messages.join('\n'));
    failure = error;
  }
  finally {
    if (original) {
      try {
        await mini.evaluate(value => {
          const config = require('config.js').develop; config.apiBase = value.api; config.socketBase = value.socket;
          const auth = require('core/auth.js'); if (value.session) auth.setSession(value.session); else auth.clearSession();
        }, original);
        if (originalPage?.path) await mini.reLaunch('/' + originalPage.path);
      } catch (error) { if (!failure) failure = error; }
    }
    try { await mini.disconnect(); } catch (_) {}
  }
  if (failure) throw failure;
})().then(() => process.exit(0)).catch(error => { console.error(error.stack || error); process.exit(1); });
