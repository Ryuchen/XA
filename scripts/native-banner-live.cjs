// Actual native click handler against isolated HTTP; staff disables the live target.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(), actions = []; let beforeModal, dropNextCreate = false, lostOrderId = null;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  navigateTo: value => actions.push({ type: 'navigate', url: value.url }),
  redirectTo: value => actions.push({ type: 'redirect', url: value.url }),
  stopPullDownRefresh() {},
  showToast: value => actions.push({ type: 'toast', title: value.title }),
  showModal: value => {
    actions.push({ type: 'modal', title: value.title, content: value.content });
    const hook = beforeModal; beforeModal = null;
    if (hook) hook().then(() => value.success?.({ confirm: true }), error => { console.error(error); value.fail?.(error); });
    else value.success?.({ confirm: true });
  },
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => {
        const data = await response.json();
        if (dropNextCreate && url.pathname === '/api/orders/create/' && data.code === 0) {
          dropNextCreate = false; lostOrderId = data.data.id; options.fail({ errMsg: 'response lost after commit' });
        } else options.success({ statusCode: response.status, data });
      }).catch(options.fail);
  },
};
require(path.join(root, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
let definition;
global.Page = value => { definition = value; };
require(path.join(root, 'pages/home/index.js'));
(async () => {
  const api = require(path.join(root, 'core/api'));
  const banners = await api.request('/banners/');
  const page = { ...definition, _alive: true, _visible: true, data: { banners }, setData(patch) { Object.assign(this.data, patch); } };
  const event = { currentTarget: { dataset: { id: input.bannerId } } };
  if (input.claimCouponId) {
    const couponStatus = async () => {
      const result = await (await fetch(`${input.base}/api/coupons/`, { headers: { Authorization: 'Bearer ' + input.bossToken } })).json();
      assert.equal(result.code, 0); return result.data.find(row => row.id === input.claimCouponId);
    };
    assert.equal((await couponStatus()).claim_status, 'AVAILABLE');
    assert.equal(banners.find(row => row.id === input.bannerId).link_available, false);
    await page.banner(event);
    assert.deepEqual(actions, [{ type: 'modal', title: '需要套餐资格', content: '请先前往领券中心查看是否有适用的资格券。' },
      { type: 'navigate', url: '/pages/coupons/index' }]);
    actions.length = 0;
    const claim = await fetch(`${input.base}/api/coupons/${input.claimCouponId}/claim/`, {
      method: 'POST', headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' }, body: '{}'
    });
    const result = await claim.json(); assert.equal(result.code, 0, JSON.stringify(result));
    assert.equal((await couponStatus()).claim_status, 'CLAIMED');
    let couponPage, ownedCoupon;
    if (input.staffToken) {
      const owned = await api.request('/coupons/mine/');
      ownedCoupon = owned.find(row => row.experience_kind === 'NEW_VALUE');
      assert.ok(ownedCoupon);
      global.Page = value => { definition = value; };
      require(path.join(root, 'pages/coupons/index.js'));
      couponPage = { ...definition, _alive: true, _visible: true,
        data: { ...definition.data, tab: 'mine', items: [ownedCoupon] }, setData(patch) { Object.assign(this.data, patch); } };
      await couponPage.use({ currentTarget: { dataset: { id: ownedCoupon.id } } });
      assert.deepEqual(actions.pop(), { type: 'navigate', url: '/pages/catalog/index' });
      require(path.join(root, 'pages/catalog/index.js'));
      const catalog = { ...definition, _alive: true, data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch); } };
      catalog.onShow(); await catalog.load();
      assert.ok(catalog._services.some(row => row.id === input.serviceId));
      catalog.open({ currentTarget: { dataset: { id: input.serviceId } } });
      assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/service/index?id=${input.serviceId}&coupon_id=${ownedCoupon.id}` });
      const scoped = await api.request(`/orders/services/${input.serviceId}/?coupon_id=${ownedCoupon.id}`);
      assert.deepEqual(scoped.variants.map(row => row.id), [input.serviceId]);
      const allVariants = await api.request(`/orders/services/${input.serviceId}/`);
      assert.ok(allVariants.variants.some(row => row.id === input.otherServiceId));
      require(path.join(root, 'pages/service/index.js'));
      const servicePage = { ...definition, data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch); } };
      servicePage.onLoad({ id: input.serviceId, coupon_id: ownedCoupon.id });
      await servicePage.load();
      assert.deepEqual(servicePage._variants.map(row => row.id), [input.serviceId]);
      servicePage.clearCoupon(); await servicePage.load();
      assert.ok(servicePage._variants.some(row => row.id === input.otherServiceId));
      servicePage.onUnload();
      const regularClaim = await fetch(`${input.base}/api/coupons/${input.regularCouponId}/claim/`, { method: 'POST',
        headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' }, body: '{}' });
      assert.equal((await regularClaim.json()).code, 0);
      const regularOwned = (await api.request('/coupons/mine/')).find(row => row.name === '指定套餐优惠券');
      assert.ok(regularOwned);
      couponPage.data.items = [regularOwned];
      await couponPage.use({ currentTarget: { dataset: { id: regularOwned.id } } });
      assert.deepEqual(actions.pop(), { type: 'navigate', url: '/pages/catalog/index' });
      const updateScope = await fetch(`${input.base}/api/admin/coupons/${input.regularCouponId}/`, { method: 'PATCH',
        headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_ids: [input.regularServiceId, input.regularOtherId] }) });
      const changedScope = await updateScope.json(); assert.equal(changedScope.code, 0, JSON.stringify(changedScope));
      catalog.onShow(); await catalog.load();
      assert.ok(catalog._services.some(row => row.id === input.regularServiceId));
      assert.ok(catalog._services.some(row => row.id === input.regularOtherId), 'the live scope must override the old local snapshot');
      const regularPage = { ...definition, data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch); } };
      regularPage.onLoad({ id: input.regularServiceId, coupon_id: regularOwned.id });
      await regularPage.load(); regularPage.checkout();
      assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/checkout/index?service_id=${input.regularServiceId}&coupon_id=${regularOwned.id}` });
      regularPage.onUnload();
      require(path.join(root, 'pages/checkout/index.js'));
      const checkout = { ...definition, _visible: true, data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch); } };
      checkout.onLoad({ service_id: input.regularServiceId, coupon_id: regularOwned.id });
      await checkout.load();
      assert.equal(checkout.data.error, '');
      assert.equal(checkout.payload().user_coupon_id, regularOwned.id);
      assert.equal(checkout.data.quote.coupon_discount, 1000);
      checkout.setData({ nickname: '隔离老板', region: '隔离区', uid: 'isolated-uid', remark: '请先联系', rounds: 2 });
      await checkout.onPullDownRefresh();
      assert.deepEqual([checkout.data.nickname, checkout.data.region, checkout.data.uid, checkout.data.remark, checkout.data.rounds],
        ['隔离老板', '隔离区', 'isolated-uid', '请先联系', 2]);
      assert.equal(checkout.payload().user_coupon_id, regularOwned.id);
      assert.equal(checkout.data.quote.original_amount, 20000);
      checkout.setData({ rounds: 1 }); await checkout.refreshQuote();
      beforeModal = async () => {
        const res = await fetch(`${input.base}/api/admin/coupons/${input.regularCouponId}/`, { method: 'PATCH',
          headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: 2000 }) });
        const data = await res.json(); assert.equal(data.code, 0, JSON.stringify(data));
      };
      await checkout.submit();
      assert.equal(checkout.data.pendingOrder, false, 'first price rejection is a confirmed no-order result');
      assert.equal(checkout.data.quote, null);
      assert.match(checkout.data.quoteError, /价格或优惠已变化/);
      assert.ok(!actions.some(row => row.type === 'navigate' && row.url.includes('order-detail')));
      actions.length = 0;
      await checkout.refreshAvailability();
      assert.equal(checkout.data.quote.coupon_discount, 2000);
      dropNextCreate = true;
      await checkout.submit();
      assert.ok(lostOrderId && checkout.data.pendingOrder, 'committed order with lost response must remain recoverable');
      const changeAfterCommit = await fetch(`${input.base}/api/admin/coupons/${input.regularCouponId}/`, { method: 'PATCH',
        headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ amount: 3000 }) });
      assert.equal((await changeAfterCommit.json()).code, 0);
      await checkout.recoverPending();
      assert.equal(checkout.data.pendingOrder, false);
      assert.ok(actions.some(row => row.type === 'redirect' && row.url === `/pages/order-detail/index?id=${lostOrderId}`));
      const recovered = await api.request(`/orders/orders/${lostOrderId}/`);
      assert.equal(recovered.amount, 6000);
      await api.request(`/orders/orders/${lostOrderId}/cancel/`, 'POST', {});
      actions.length = 0;
      checkout.onUnload();
      const rateClaim = await fetch(`${input.base}/api/coupons/${input.rateCouponId}/claim/`, { method: 'POST',
        headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' }, body: '{}' });
      assert.equal((await rateClaim.json()).code, 0);
      const rateOwned = (await api.request('/coupons/mine/')).find(row => row.name === '九折券');
      const betterVip = { ...definition, _visible: true, data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch); } };
      betterVip.onLoad({ service_id: input.regularServiceId, coupon_id: rateOwned.id });
      await betterVip.load();
      assert.equal(betterVip.data.quote.couponUnused, true);
      assert.equal(betterVip.data.quote.coupon_discount, 0);
      assert.equal(betterVip.data.quote.boss_discount, 2000);
      const paidRegular = await api.request('/orders/create/', 'POST', { ...betterVip.payload(), client_request_id: 'vip-better-coupon-live' });
      assert.equal(paidRegular.amount, 8000);
      assert.equal((await api.request('/coupons/mine/')).find(row => row.id === rateOwned.id).status, 'UNUSED');
      await api.request(`/orders/orders/${paidRegular.id}/cancel/`, 'POST', {});
      betterVip.onUnload();
      require(path.join(root, 'pages/companion-picker/index.js'));
      const picker = { ...definition, _visible: true, data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch); } };
      picker.onLoad({ service_id: input.regularServiceId, coupon_id: regularOwned.id });
      await picker.load(); await picker.confirmSelection();
      assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/checkout/index?service_id=${input.regularServiceId}&provider_gender=ANY&coupon_id=${regularOwned.id}` });
      picker.onUnload();
      const generalClaim = await fetch(`${input.base}/api/coupons/${input.generalCouponId}/claim/`, { method: 'POST',
        headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' }, body: '{}' });
      assert.equal((await generalClaim.json()).code, 0);
      const generalOwned = (await api.request('/coupons/mine/')).find(row => row.name === '通用优惠券');
      couponPage.data.items = [generalOwned];
      await couponPage.use({ currentTarget: { dataset: { id: generalOwned.id } } });
      assert.deepEqual(actions.pop(), { type: 'navigate', url: '/pages/catalog/index' });
      catalog.onShow(); await catalog.load();
      assert.equal(catalog.data.couponName, '通用优惠券');
      assert.ok(catalog._services.some(row => row.id === input.regularServiceId));
      assert.ok(!catalog._services.some(row => row.id === input.serviceId || row.id === input.otherServiceId));
      catalog.open({ currentTarget: { dataset: { id: input.regularServiceId } } });
      assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/service/index?id=${input.regularServiceId}&coupon_id=${generalOwned.id}` });
      const incompatible = await fetch(`${input.base}/api/orders/services/${input.otherServiceId}/?coupon_id=${ownedCoupon.id}`,
        { headers: { Authorization: 'Bearer ' + input.bossToken } });
      assert.notEqual((await incompatible.json()).code, 0);
      couponPage.data.items = [ownedCoupon];
      await couponPage.use({ currentTarget: { dataset: { id: ownedCoupon.id } } });
      assert.deepEqual(actions.pop(), { type: 'navigate', url: '/pages/catalog/index' });
      catalog.onShow(); await catalog.load();
      const setActive = async is_active => {
        const res = await fetch(`${input.base}/api/admin/service-items/${input.serviceId}/`, { method: 'PATCH',
          headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active }) });
        const data = await res.json(); assert.equal(data.code, 0, JSON.stringify(data));
      };
      await setActive(false); await catalog.load();
      assert.equal(catalog.data.couponEmpty, true); assert.equal(catalog.data.total, 0);
      await setActive(true); await catalog.load();
      assert.equal(catalog.data.couponEmpty, false);
    }
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/service/index?id=${input.serviceId}` });
    const purchase = await fetch(`${input.base}/api/orders/create/`, { method: 'POST',
      headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' },
      body: JSON.stringify({ service_id: input.serviceId }) });
    const order = await purchase.json(); assert.equal(order.code, 0, JSON.stringify(order));
    const usedCoupon = await couponStatus();
    assert.equal(usedCoupon.claim_status, 'USED');
    if (couponPage) {
      couponPage.data.items = [ownedCoupon];
      await couponPage.use({ currentTarget: { dataset: { id: ownedCoupon.id } } });
      assert.deepEqual(actions.pop(), { type: 'toast', title: '优惠券状态已变化，请重新查看' });
      assert.ok(!couponPage.data.items.some(row => row.id === ownedCoupon.id));
    }
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'toast', title: '该项资格或今日额度已使用' });
    assert.equal(actions.length, 0);
    return;
  }
  await page.banner(event);
  assert.deepEqual(actions.pop(), { type: 'navigate', url: input.expectedUrl || `/pages/service/index?id=${input.serviceId}` });
  if (input.observeOnly) return;
  if (input.replacementContentId) {
    const updateBanner = async (type, value) => {
      const response = await fetch(`${input.base}/api/admin/banners/${input.bannerId}/`, { method: 'PATCH',
        headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' },
        body: JSON.stringify({ link_type: type, link_value: String(value) }) });
      const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    };
    await updateBanner('CONTENT', input.replacementContentId);
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'toast', title: '活动已更新，请重新查看' });
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/club-content/index?id=${input.replacementContentId}` });
    await updateBanner('PAGE', 'catalog');
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'toast', title: '活动已更新，请重新查看' });
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'navigate', url: '/pages/catalog/index' });
    await updateBanner('PRODUCT', input.serviceId);
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'toast', title: '活动已更新，请重新查看' });
    await page.banner(event);
    assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/service/index?id=${input.serviceId}` });
  }
  const response = await fetch(input.base + (input.disablePath || `/api/admin/service-items/${input.serviceId}/`), { method: 'PATCH',
    headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: false }) });
  const disabled = await response.json(); assert.equal(disabled.code, 0, JSON.stringify(disabled));
  assert.equal(page.data.banners[0].link_available, true, 'the open page retains its old snapshot');
  await page.banner(event);
  assert.deepEqual(actions.pop(), { type: 'toast', title: '活动暂不可用，请浏览其他服务' });
  assert.equal(actions.length, 0);
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
