/* Room-scoped client. All outcomes come from the server. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const room = (document.querySelector('meta[name="room-code"]')?.content || '').trim();
  let base = (document.querySelector('meta[name="api-base"]')?.content || '/api/').trim();
  if (!base || base === '/') base = '/api/';
  base = base.replace(/\/?$/, '/');
  const sessionKey = `xa_lottery_session:${room}`;
  const read = (key) => { try { return JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) { return null; } };
  const write = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } };
  const remove = (key) => { try { sessionStorage.removeItem(key); } catch (_) { /* In-memory session still works. */ } };
  const saved = read(sessionKey);
  const storedSessionIsCurrent = (value) => Boolean(room && value?.token && value.account_id && value.room === room);
  const savedIsCurrent = storedSessionIsCurrent(saved);
  if (saved && !savedIsCurrent) remove(sessionKey);
  const state = { catalog: null, pool: 'small', session: savedIsCurrent ? saved : null, sessionEpoch: 0, me: null, pending: null, busy: false, loading: true, catalogRequest: 0, meLoading: false, meRequest: 0, loginRequest: 0, drawRequest: 0, meFailed: false, lastResult: null, history: null, historyRequest: 0, historyLoading: false, historyStarted: false, historyTarget: null, historyError: '' };
  const roomCodeFor = (value) => typeof value === 'string' ? value : value?.code;
  const accountIDFor = (value) => value?.account_id ?? value?.participant_id;
  const roomIsCurrent = (value) => Boolean(room && roomCodeFor(value) === room);
  const sessionSnapshot = () => state.session ? { ...state.session, epoch: state.sessionEpoch } : null;
  const sessionIsCurrent = (snapshot) => Boolean(snapshot && state.session && snapshot.epoch === state.sessionEpoch && snapshot.token === state.session.token && String(snapshot.account_id) === String(state.session.account_id) && String(snapshot.business_cycle || 1) === String(state.session.business_cycle || 1) && snapshot.room === room && state.session.room === room);
  const meIsCurrent = (data) => Boolean(state.session && roomIsCurrent(data?.room) && String(accountIDFor(data)) === String(state.session.account_id));
  const drawIsCurrent = (draw, snapshot = state.session) => Boolean(snapshot && snapshot.room === room && draw?.room_code === room && String(accountIDFor(draw)) === String(snapshot.account_id) && (!state.catalog?.room?.id || String(draw.room_id) === String(state.catalog.room.id)));
  const pool = () => state.catalog?.campaigns.find((item) => item.slug === state.pool);
  const remainingOnly = () => state.catalog?.remaining_only === true;
  const ownerKey = () => state.session ? `xa_lottery_pending:${room}:${state.session.account_id}:${state.session.business_cycle || 1}` : '';
  const loadPending = () => { state.pending = state.session ? read(ownerKey()) : null; };
  const storePending = (value) => { state.pending = value; if (value) write(ownerKey(), value); else if (state.session) remove(ownerKey()); };
  const put = (id, value) => { if ($(id)) $(id).textContent = String(value ?? ''); };
  const show = (id, visible) => { if ($(id)) $(id).hidden = !visible; };
  const announce = (text) => put('live-status', text);
  const message = (text = '') => { put('draw-message', text); show('draw-message', Boolean(text)); };
  const element = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = String(text); return node; };
  const statusText = { active: '活动进行中', upcoming: '活动尚未开始', ended: '活动已结束', disabled: '暂未开放' };
  const dateText = (value) => { const d = new Date(value); return Number.isNaN(d.getTime()) ? '时间待确认' : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(d); };
  const countFor = (slug) => state.me && Object.hasOwn(state.me.entries || {}, slug) ? Number(state.me.entries[slug]) : null;
  const bonusesFor = (draw) => Array.isArray(draw?.bonuses) ? draw.bonuses.filter((bonus) => bonus && typeof bonus.label === 'string') : [];
  const awardStatus = (award) => award.status === 'FULFILLED' ? '已兑奖' : '待管理员兑奖';

  function renderPity() {
    const raw = state.me?.pity?.[state.pool] || (state.lastResult?.campaign === state.pool ? state.lastResult.pity : null);
    const config = pool()?.pity_rewards || raw;
    const valid = raw && Number.isInteger(raw.total_rolls) && raw.total_rolls >= 0 && Number.isInteger(raw.until_lower) && raw.until_lower >= 1 && raw.until_lower <= 30 && Number.isInteger(raw.until_upper) && raw.until_upper >= 1 && raw.until_upper <= 50;
    put('pity-total', valid ? raw.total_rolls : '—');
    [['lower', 30], ['upper', 50]].forEach(([tier, interval]) => {
      const remaining = valid ? raw[`until_${tier}`] : null;
      const amount = config?.[`${tier}_yuan`];
      put(`pity-label-${tier}`, Number.isInteger(amount) && amount > 0 ? `${amount}元店铺余额保底` : `${interval}次店铺余额保底`);
      put(`pity-next-${tier}`, remaining == null ? (state.session ? '同步后查看' : '登录后查看') : `还差 ${remaining} 次`);
      $(`pity-progress-${tier}`).style.width = remaining == null ? '0%' : `${(interval - remaining) / interval * 100}%`;
    });
    const lower = config?.lower_yuan, upper = config?.upper_yuan;
    put('pity-cash-rule', Number.isInteger(lower) && lower > 0 && Number.isInteger(upper) && upper > 0 ? `每30次额外${lower}元，每50次额外${upper}元` : '每30次与每50次额外获得本奖池余额保底');
  }

  class APIError extends Error {
    constructor(text, status = 0, uncertain = false) { super(text); this.status = status; this.uncertain = uncertain; }
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    const headers = { Accept: 'application/json' };
    if (options.body != null) headers['Content-Type'] = 'application/json';
    const authSession = options.session || state.session;
    if (options.auth) {
      headers['X-Lottery-Room'] = room;
      if (authSession) headers.Authorization = `Bearer ${authSession.token}`;
    }
    try {
      const response = await fetch(base + path, { method: options.body == null ? 'GET' : 'POST', body: options.body == null ? undefined : JSON.stringify(options.body), headers, signal: controller.signal, credentials: 'omit', cache: 'no-store' });
      let envelope;
      try { envelope = await response.json(); } catch (_) { throw new APIError('服务器返回了无法识别的响应，请稍后重试。', response.status, true); }
      if (!response.ok || envelope.code !== 0) {
        const text = typeof envelope.message === 'string' ? envelope.message : typeof envelope.msg === 'string' ? envelope.msg : typeof envelope.detail === 'string' ? envelope.detail : '暂时无法完成操作，请稍后再试。';
        throw new APIError(text, response.status, response.status >= 500 || [408, 425, 429].includes(response.status));
      }
      return envelope.data;
    } catch (error) {
      if (error instanceof APIError) throw error;
      throw new APIError(error.name === 'AbortError' ? '网络响应超时。' : '网络连接暂时中断。', 0, true);
    } finally { clearTimeout(timeout); }
  }

  function openDialog(id) {
    const dialog = $(id);
    if (!dialog || dialog.open) return;
    if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => (id === 'login-dialog' ? $('participation-code') : dialog.querySelector('button'))?.focus());
  }
  function closeDialog(id) {
    const dialog = $(id);
    if (!dialog) return;
    if (typeof dialog.close === 'function') dialog.close(); else { dialog.removeAttribute('open'); document.body.style.overflow = ''; }
  }
  document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => closeDialog(button.dataset.close)));
  document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.addEventListener('close', () => { document.body.style.overflow = ''; });
    dialog.addEventListener('click', (event) => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeDialog(dialog.id); } });
    dialog.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') return;
      const controls = [...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),a[href]')].filter((item) => !item.hidden);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
  });

  function renderAccount() {
    put('account-label', state.session ? state.me?.nickname || state.session.nickname || '我的账号' : '登录参与');
    put('account-nickname', state.me?.nickname || state.session?.nickname || '店庆伙伴');
    const total = state.me ? Object.values(state.me.entries || {}).reduce((sum, n) => sum + Number(n || 0), 0) : null;
    put('account-total-entries', total == null ? '—' : total);
    put('account-room', state.catalog?.room?.name ? `当前房间：${state.catalog.room.name}。你的次数与记录仅在本房间有效。` : '你的抽奖次数与中奖记录已关联本房间的个人账号。');
  }
  function renderDraw() {
    const campaign = pool();
    const count = countFor(state.pool);
    const button = $('draw-button');
    const countNode = $('entry-count');
    countNode.replaceChildren(document.createTextNode(count == null ? '—' : String(count)), element('small', '', ' 次'));
    show('pending-note', Boolean(state.pending));
    let label = '登录，开启幸运', help = '凭管理员提供的个人参与码登录', disabled = false;
    if (state.busy) { label = '正在确认本次结果…'; help = '请稍候，抽奖结果由服务器确认'; disabled = true; }
    else if (state.session && state.pending) { label = state.pending.count === 10 ? '查询上次十连抽结果' : '查询上次抽奖结果'; help = '使用原请求查询，不会重复扣减次数'; }
    else if (state.loading) { label = '正在加载奖池'; disabled = true; }
    else if (!campaign) { label = '奖池暂不可用'; help = '请重新加载奖池'; disabled = true; }
    else if (campaign.status !== 'active' || !campaign.enabled) { label = statusText[campaign.status] || '暂未开放'; help = '奖池开放后才可参与'; disabled = true; }
    else if (state.session && state.meLoading && !state.me) { label = '正在读取可用次数'; help = '正在同步你的活动账号'; disabled = true; }
    else if (state.session && state.meFailed) { label = '重新读取可用次数'; help = '暂未读到最新次数，请重试'; }
    else if (state.session && count === 0) { label = '本奖池暂无次数'; help = remainingOnly() ? '本奖池没有剩余次数，补抽期间不再添加次数' : '请联系房间管理员发放抽奖次数'; disabled = true; }
    else if (state.session && count == null) { label = '重新读取可用次数'; help = '请先同步账号信息'; }
    else if (state.session) { label = '掷出我的幸运'; help = '每次消耗 1 次本奖池机会'; }
    button.disabled = disabled;
    put('draw-button-label', label); put('draw-help', help);
    put('draw-intro', state.session ? `${state.me?.nickname || state.session.nickname || '店庆伙伴'}，这次好运为你准备` : '登录后查看你的专属抽奖次数');
    $('pool-tabs').querySelectorAll('button').forEach((tab) => { tab.disabled = state.busy; });
    const batchAllowed = Boolean(state.session && state.me && !state.meFailed && !state.busy && !state.loading && !state.pending && campaign?.enabled && campaign.status === 'active' && count >= 10);
    $('batch-draw-button').disabled = !batchAllowed;
    put('batch-draw-label', state.busy && state.pending?.count === 10 ? '十连抽正在确认…' : '十连抽 · 一次开启10份惊喜');
    let batchHelp = '至少持有10次机会，抽中再来一次会返还';
    if (!state.session) batchHelp = '登录后查看十连抽资格';
    else if (state.pending) batchHelp = state.pending.count === 10 ? '请先查询上次十连抽结果' : '请先确认上一笔抽奖结果';
    else if (state.meFailed || count == null) batchHelp = '同步可用次数后才能开启十连抽';
    else if (count < 10) batchHelp = remainingOnly() ? (count > 0 ? `本奖池剩余 ${count} 次，可使用单抽；不再添加次数` : '本奖池没有剩余次数，补抽期间不再添加次数') : `十连抽需至少10次，本奖池还差 ${10 - count} 次`;
    else if (!campaign?.enabled || campaign.status !== 'active') batchHelp = '奖池开放后才可参与十连抽';
    put('batch-draw-help', batchHelp);
    renderPity();
  }
  function selectCell(cell, fromResult = false) {
    $('matrix-body').querySelectorAll('.prize-cell').forEach((button) => {
      const selected = Number(button.dataset.row) === Number(cell.row) && Number(button.dataset.column) === Number(cell.column);
      button.classList.toggle('is-selected', selected);
      button.classList.toggle('is-result', selected && fromResult);
      button.setAttribute('aria-pressed', String(selected));
    });
    put('cell-detail-text', `红骰 ${cell.row} · 蓝骰 ${cell.column}：${cell.label}。`);
    $('cell-detail').classList.add('has-selection');
  }
  function renderPool() {
    const campaign = pool();
    $('pool-tabs').querySelectorAll('button').forEach((tab) => { const active = tab.dataset.pool === state.pool; tab.classList.toggle('is-active', active); tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1; });
    $('pool-panel').setAttribute('aria-labelledby', `tab-${state.pool}`);
    if (!campaign) { renderDraw(); return; }
    put('pool-name', campaign.name);
    put('pool-status', remainingOnly() && campaign.status === 'active' ? '剩余次数补抽中' : statusText[campaign.status] || '暂未开放');
    $('pool-status').classList.toggle('is-inactive', campaign.status !== 'active');
    const body = document.createDocumentFragment();
    for (let row = 1; row <= 6; row += 1) {
      const tr = document.createElement('tr');
      const th = element('th', '', row); th.scope = 'row'; tr.append(th);
      for (let column = 1; column <= 6; column += 1) {
        const cell = campaign.cells.find((item) => item.row === row && item.column === column);
        const td = document.createElement('td');
        const button = element('button', 'prize-cell', cell.label);
        button.type = 'button'; button.dataset.row = row; button.dataset.column = column;
        button.setAttribute('aria-label', `红骰${row}，蓝骰${column}，${cell.label}`);
        button.setAttribute('aria-pressed', 'false');
        button.classList.toggle('is-thanks', cell.kind === 'THANKS');
        button.classList.toggle('is-retry', cell.kind === 'RETRY');
        button.classList.toggle('is-featured', cell.kind === 'REWARD' && ['TAG', 'NAMING'].includes(cell.reward_kind));
        button.addEventListener('click', () => selectCell(cell));
        td.append(button); tr.append(td);
      }
      body.append(tr);
    }
    $('matrix-body').replaceChildren(body);
    put('cell-detail-text', '点数组合由服务端生成，抽奖结果自动记录。');
    $('cell-detail').classList.remove('has-selection');
    show('matrix-loading', false); show('prize-matrix', true);
    $('pool-panel').setAttribute('aria-busy', 'false');
    if (state.lastResult?.campaign === state.pool) selectCell(state.lastResult, true);
    renderDraw();
  }
  function validCatalog(data) {
    return data && Array.isArray(data.campaigns) && data.campaigns.length === 3 && ['small', 'red', 'grand'].every((slug) => {
      const p = data.campaigns.find((item) => item.slug === slug);
      return p && Array.isArray(p.cells) && p.cells.length === 36 && new Set(p.cells.map((c) => `${c.row},${c.column}`)).size === 36 && p.cells.every((c) => Number.isInteger(c.row) && c.row >= 1 && c.row <= 6 && Number.isInteger(c.column) && c.column >= 1 && c.column <= 6 && typeof c.label === 'string');
    });
  }
  async function loadCatalog() {
    const requestNumber = ++state.catalogRequest;
    state.loading = true; renderDraw(); show('catalog-error', false);
    $('reload-catalog').disabled = true;
    try {
      const data = await api(`catalog/?room=${encodeURIComponent(room)}`);
      if (requestNumber !== state.catalogRequest) return;
      if (!roomIsCurrent(data?.room)) {
        state.catalog = null;
        expireSession();
        $('matrix-body').replaceChildren();
        show('prize-matrix', false);
        throw new APIError('房间信息与当前页面不匹配，请从本房间的活动链接重新进入。', 403);
      }
      if (!validCatalog(data)) throw new APIError('奖池数据暂不完整，请联系房间管理员或稍后重新加载。');
      state.catalog = data;
      put('room-badge', data.room?.name ? `幸运房间 / ${data.room.name}` : 'YOUR LUCKY MOMENT');
      if (remainingOnly()) {
        put('room-window', '历史剩余次数可继续使用，不受原结束时间限制；仅限已开放房间及启用账号，不再添加资格或次数。');
        show('room-window', true);
      } else if (data.room?.starts_at && data.room?.ends_at) {
        const dateFormat = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
        put('room-window', `本房间开放时间：${dateFormat.format(new Date(data.room.starts_at))} 至 ${dateFormat.format(new Date(data.room.ends_at))}（北京时间，结束时刻不含）`);
        show('room-window', true);
      }
      if (Array.isArray(data.rules) && data.rules.length) $('server-rules').replaceChildren(...data.rules.map((rule) => element('li', '', rule)));
      renderPool(); renderAccount(); renderHistory();
    } catch (error) {
      if (requestNumber !== state.catalogRequest) return;
      put('catalog-error-text', error.message); show('catalog-error', true);
      if (!state.catalog) { show('matrix-loading', false); put('pool-status', '加载失败'); $('pool-status').classList.add('is-inactive'); $('pool-panel').setAttribute('aria-busy', 'false'); }
    } finally { if (requestNumber === state.catalogRequest) { state.loading = false; $('reload-catalog').disabled = false; renderDraw(); } }
  }
  function renderHistory() {
    const loggedIn = Boolean(state.session && state.session.room === room);
    const roomVerified = roomIsCurrent(state.catalog?.room);
    const verified = loggedIn && roomVerified && meIsCurrent(state.me) && drawIsCurrent(state.history);
    const draws = verified ? state.history.draws.filter((draw) => drawIsCurrent(draw)) : [];
    const pagination = verified ? state.history.pagination : null;
    show('history-login', !loggedIn); show('refresh-history', loggedIn);
    $('refresh-history').disabled = state.historyLoading;
    show('history-empty', !draws.length); show('history-list', Boolean(draws.length)); show('history-caption', Boolean(pagination));
    show('history-pagination', Boolean(pagination));
    $('history-previous').disabled = state.historyLoading || !pagination?.has_previous;
    $('history-next').disabled = state.historyLoading || !pagination?.has_next;
    $('history-page-input').disabled = state.historyLoading || !pagination; $('history-page-input').max = String(Math.max(1, pagination?.total_pages || 1));
    $('history-jump-button').disabled = state.historyLoading || !pagination;
    if (pagination) put('history-page-info', `第${pagination.page} / ${Math.max(1, pagination.total_pages)}页 · 共${pagination.total}条`);
    $('history-list').setAttribute('aria-busy', String(state.historyLoading));
    $('history-list').classList.toggle('is-page-loading', state.historyLoading);
    show('history-load-status', loggedIn && state.historyLoading);
    put('history-load-status', state.historyLoading ? `正在加载第${state.historyTarget?.page || 1}页记录…` : '');
    show('history-error-row', loggedIn && Boolean(state.historyError));
    put('history-error', state.historyError);
    put('history-retry', `重试第${state.historyTarget?.page || 1}页 ↻`);
    $('history-retry').disabled = state.historyLoading;
    if (!draws.length) {
      put('history-empty-title', loggedIn ? '第一份幸运，还在路上' : '你的幸运收藏，等你开启');
      put('history-empty-text', loggedIn ? (state.historyLoading ? '正在读取你在本房间的完整抽奖记录。' : state.historyError ? '记录暂未加载成功，请重试。' : state.meFailed ? '暂时未能确认本房间账号，请点击“刷新记录”重试。' : !roomVerified || !state.me ? '正在确认本房间信息，确认后仅展示你的记录。' : '完成抽奖后，你在本房间的结果与兑奖状态会出现在这里。') : '登录本房间后，仅展示你自己的抽奖结果与奖品兑奖状态。');
    }
    $('history-list').replaceChildren(...draws.map((draw) => {
      const item = element('article', 'history-item');
      const bonuses = bonusesFor(draw);
      item.classList.toggle('has-bonus', bonuses.length > 0);
      const icon = element('span', `history-icon${!bonuses.length && draw.kind === 'THANKS' ? ' is-thanks' : draw.kind === 'RETRY' ? ' is-retry' : ''}`, bonuses.length ? '✦' : draw.kind === 'RETRY' ? '↻' : draw.kind === 'THANKS' ? '♡' : '✦'); icon.setAttribute('aria-hidden', 'true');
      const main = element('div'); main.append(element('h3', '', draw.label));
      const meta = element('p', 'history-meta');
      const time = element('time', '', dateText(draw.created_at)); time.dateTime = draw.created_at;
      meta.append(element('span', '', draw.campaign_name));
      if (Number.isInteger(draw.total_rolls)) meta.append(element('span', '', `本池第 ${draw.total_rolls} 次`));
      meta.append(time); main.append(meta);
      const right = element('div', 'history-right');
      const award = draw.award || draw.claim;
      right.append(element('span', 'history-claim', award ? (award.status === 'FULFILLED' ? '已兑奖' : '待联系管理员兑奖') : draw.kind === 'RETRY' ? '已返还机会' : '结果已记录'), element('span', 'history-dice', `红 ${draw.row} · 蓝 ${draw.column}`));
      item.append(icon, main, right);
      if (award?.delivery_note) { const note = element('p', 'history-delivery', award.delivery_note); main.append(note); }
      if (bonuses.length) {
        const list = element('div', 'history-bonuses');
        bonuses.forEach((bonus) => {
          const row = element('div', 'history-bonus');
          row.append(element('span', 'history-bonus-label', `额外保底 · ${bonus.label}`), element('span', `history-bonus-status${bonus.status === 'FULFILLED' ? ' is-fulfilled' : ''}`, awardStatus(bonus)));
          if (bonus.delivery_note) row.append(element('p', 'history-delivery', bonus.delivery_note));
          list.append(row);
        });
        main.append(list);
      }
      return item;
    }));
  }
  async function loadHistory({ page = 1, reset = false, retry = false } = {}) {
    if (!state.session) return;
    const snapshot = sessionSnapshot();
    const requestNumber = ++state.historyRequest;
    const target = retry && state.historyTarget ? { ...state.historyTarget } : { page, before_id: reset ? null : state.history?.pagination.before_id ?? null };
    state.historyTarget = target; state.historyLoading = true; state.historyStarted = true; state.historyError = ''; show('history-jump-error', false); $('history-page-input').removeAttribute('aria-invalid'); renderHistory();
    const query = new URLSearchParams({ page: String(target.page), page_size: '10' });
    if (target.before_id != null) query.set('before_id', String(target.before_id));
    try {
      const data = await api(`history/?${query}`, { auth: true, session: snapshot });
      if (!sessionIsCurrent(snapshot) || requestNumber !== state.historyRequest) return;
      if (!drawIsCurrent(data, snapshot) || (Array.isArray(data?.draws) && data.draws.some((draw) => !drawIsCurrent(draw, snapshot)))) throw new APIError('记录与当前房间或账号不匹配，请重新登录本房间。', 403);
      const p = data.pagination;
      if (!Array.isArray(data.draws) || !p || !Number.isInteger(p.page) || p.page !== target.page || !Number.isInteger(p.page_size) || p.page_size !== 10 || !Number.isInteger(p.total) || p.total < data.draws.length || !Number.isInteger(p.total_pages) || p.total_pages < 0 || p.page > Math.max(1, p.total_pages) || typeof p.has_previous !== 'boolean' || typeof p.has_next !== 'boolean' || data.draws.length > p.page_size || (target.before_id != null && String(p.before_id) !== String(target.before_id))) throw new APIError('记录分页暂未完整返回，请重试当前页。');
      state.history = data; $('history-page-input').value = String(p.page);
      announce(`已显示第${p.page}页，共${p.total}条本房间记录。`);
    } catch (error) {
      if (!sessionIsCurrent(snapshot) || requestNumber !== state.historyRequest) return;
      if (error.status === 401 || error.status === 403) { expireSession(); message(error.message); }
      else state.historyError = `第${target.page}页加载失败：${error.message}${state.history ? ` 当前仍显示第${state.history.pagination.page}页。` : ''}`;
    } finally {
      if (sessionIsCurrent(snapshot) && requestNumber === state.historyRequest) { state.historyLoading = false; renderHistory(); }
    }
  }
  function refreshHistory() {
    state.meRequest += 1; state.meLoading = false;
    return Promise.allSettled([loadMe(), loadHistory({ reset: true })]);
  }
  function expireSession({ removeStored = true } = {}) {
    if (removeStored) remove(sessionKey);
    state.session = null; state.sessionEpoch += 1; state.me = null; state.pending = null; state.meFailed = false; state.meLoading = false; state.meRequest += 1; state.loginRequest += 1; state.drawRequest += 1; state.lastResult = null; state.busy = false; state.historyRequest += 1; state.history = null; state.historyLoading = false; state.historyStarted = false; state.historyTarget = null; state.historyError = '';
    $('die-row').dataset.value = '0'; $('die-column').dataset.value = '0'; $('dice-stage').setAttribute('aria-label', '两枚骰子等待抽奖');
    $('dice-stage').classList.remove('is-rolling', 'reduced-roll');
    $('matrix-body').querySelectorAll('.prize-cell').forEach((button) => { button.classList.remove('is-selected', 'is-result'); button.setAttribute('aria-pressed', 'false'); });
    put('cell-detail-text', '点数组合由服务端生成，抽奖结果自动记录。'); $('cell-detail').classList.remove('has-selection');
    $('history-list').replaceChildren(); show('history-error-row', false); put('history-error', ''); show('history-pagination', false); put('history-page-info', '第1 / 1页 · 共0条');
    $('batch-results').replaceChildren(); $('batch-summary').replaceChildren(); put('batch-pool', ''); closeDialog('batch-dialog');
    $('history-page-input').value = '1'; show('history-jump-error', false); $('history-page-input').removeAttribute('aria-invalid');
    $('result-bonus-list').replaceChildren(); show('result-bonuses', false); $('result-dialog').classList.remove('has-bonus');
    ['result-title', 'result-pool', 'result-row', 'result-column', 'result-description', 'result-reference'].forEach((id) => put(id, ''));
    put('result-symbol', '✦'); put('result-eyebrow', 'A LITTLE GOOD FORTUNE'); put('result-bonus-title', '累计保底，额外惊喜');
    closeDialog('result-dialog'); closeDialog('account-dialog');
    $('participation-code').value = ''; show('login-error', false); $('participation-code').removeAttribute('aria-invalid');
    $('login-submit').disabled = false; put('login-submit', '登录，开启幸运 ↗'); $('refresh-history').disabled = false;
    message(); announce('');
    renderAccount(); renderDraw(); renderHistory();
  }
  async function loadMe() {
    if (!state.session || state.meLoading) return;
    const snapshot = sessionSnapshot();
    const requestNumber = ++state.meRequest;
    state.meLoading = true; state.meFailed = false; renderDraw();
    try {
      const data = await api('me/', { auth: true, session: snapshot });
      if (!sessionIsCurrent(snapshot) || requestNumber !== state.meRequest) return;
      if (!roomIsCurrent(data?.room) || String(accountIDFor(data)) !== String(snapshot.account_id)) throw new APIError('账号信息与当前房间不匹配，请重新输入本房间的个人参与码。', 403);
      if (!data.entries) throw new APIError('账号信息暂不完整，请重新登录。');
      state.me = data;
      state.session.nickname = data.nickname || state.session.nickname;
      write(sessionKey, state.session);
      if (!state.historyStarted) void loadHistory({ reset: true });
    } catch (error) {
      if (!sessionIsCurrent(snapshot) || requestNumber !== state.meRequest) return;
      if (error.status === 401 || error.status === 403) { expireSession(); message(error.status === 403 ? error.message : '登录状态已失效，请使用本房间的个人参与码重新登录。'); }
      else { state.meFailed = true; message(error.message); }
    } finally {
      if (sessionIsCurrent(snapshot) && requestNumber === state.meRequest) { state.meLoading = false; renderAccount(); renderHistory(); renderDraw(); }
    }
  }

  $('pool-tabs').addEventListener('click', (event) => {
    const tab = event.target.closest('[data-pool]'); if (!tab || state.busy) return;
    state.pool = tab.dataset.pool; message(); renderPool();
  });
  $('pool-tabs').addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || state.busy) return;
    event.preventDefault(); const tabs = [...$('pool-tabs').querySelectorAll('button')];
    let i = tabs.indexOf(document.activeElement); if (i < 0) i = 0;
    i = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[i].click(); tabs[i].focus();
  });
  $('reload-catalog').addEventListener('click', loadCatalog);
  $('refresh-history').addEventListener('click', refreshHistory);
  $('history-previous').addEventListener('click', () => { if (state.history?.pagination.has_previous && !state.historyLoading) void loadHistory({ page: state.history.pagination.page - 1 }); });
  $('history-next').addEventListener('click', () => { if (state.history?.pagination.has_next && !state.historyLoading) void loadHistory({ page: state.history.pagination.page + 1 }); });
  $('history-retry').addEventListener('click', () => { if (!state.historyLoading) void loadHistory({ retry: true }); });
  $('history-jump-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (state.historyLoading || !state.history) return;
    const value = $('history-page-input').value.trim();
    const page = Number(value), maximum = Math.max(1, state.history.pagination.total_pages);
    if (!/^\d+$/.test(value) || !Number.isInteger(page) || page < 1 || page > maximum) { put('history-jump-error', `请输入1到${maximum}之间的页码。`); show('history-jump-error', true); $('history-page-input').setAttribute('aria-invalid', 'true'); $('history-page-input').focus(); return; }
    show('history-jump-error', false); $('history-page-input').removeAttribute('aria-invalid');
    if (page !== state.history.pagination.page) void loadHistory({ page });
  });
  $('history-login').addEventListener('click', () => openDialog('login-dialog'));
  $('account-button').addEventListener('click', () => { if (!state.busy) openDialog(state.session ? 'account-dialog' : 'login-dialog'); });
  $('logout-button').addEventListener('click', () => { if (state.busy) return; expireSession(); closeDialog('account-dialog'); message('已退出当前活动账号。'); announce('已退出当前活动账号'); });
  $('participation-code').addEventListener('input', () => { show('login-error', false); $('participation-code').removeAttribute('aria-invalid'); });
  $('login-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const code = $('participation-code').value.trim(); if (!code || !room) return;
    const button = $('login-submit'); button.disabled = true; button.textContent = '正在验证参与码…'; show('login-error', false);
    const requestNumber = ++state.loginRequest;
    const loginEpoch = state.sessionEpoch;
    try {
      const data = await api('login/', { body: { room, code } });
      if (requestNumber !== state.loginRequest || loginEpoch !== state.sessionEpoch) return;
      if (!roomIsCurrent(data?.room)) {
        expireSession();
        put('login-error', '参与码登录结果与当前房间不匹配，请核对本房间的参与码。'); show('login-error', true); $('participation-code').setAttribute('aria-invalid', 'true'); $('participation-code').focus();
        return;
      }
      if (!data?.token || !data.account_id) throw new APIError('登录信息暂不完整，请联系管理员。');
      expireSession();
      state.session = { token: data.token, nickname: data.nickname || '店庆伙伴', account_id: data.account_id, business_cycle: data.business_cycle || 1, room };
      state.me = null; state.meFailed = false; write(sessionKey, state.session); loadPending();
      $('participation-code').value = ''; closeDialog('login-dialog'); message(); renderAccount(); renderHistory(); renderDraw(); announce('登录成功，正在读取抽奖次数');
      await loadMe();
    } catch (error) {
      if (requestNumber !== state.loginRequest) return;
      put('login-error', error.message); show('login-error', true); $('participation-code').setAttribute('aria-invalid', 'true'); $('participation-code').focus();
    } finally { if (requestNumber === state.loginRequest) { button.disabled = false; button.textContent = '登录，开启幸运 ↗'; } }
  });
  function requestID() {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); return [...bytes].map((n) => n.toString(16).padStart(2, '0')).join('');
  }
  function showResult(draw) {
    const bonuses = bonusesFor(draw);
    const hasBonus = bonuses.length > 0;
    put('result-title', draw.label); put('result-pool', `${draw.campaign_name}${Number.isInteger(draw.total_rolls) ? ` · 本池第 ${draw.total_rolls} 次` : ''}`); put('result-row', draw.row); put('result-column', draw.column);
    put('result-reference', `抽奖编号 ${draw.id} · ${dateText(draw.created_at)}`);
    const description = draw.kind === 'THANKS' ? (hasBonus ? '本轮骰子奖项为感谢参与。你的累计次数已达成保底，额外奖励已单独记录。' : '这次与好运擦肩而过。感谢你的参与，愿下一次惊喜恰好落在你手里。') : draw.kind === 'RETRY' ? '好运给你多一次机会！已返还 1 次本奖池抽奖次数，可继续参与。' : '中奖结果已记录，请联系房间管理员核对并兑奖。所有奖品均以本房间的兑奖记录为准。';
    put('result-description', description); put('result-eyebrow', hasBonus ? 'AN EXTRA LUCKY MILESTONE' : draw.kind === 'RETRY' ? 'ONE MORE LUCKY MOMENT' : draw.kind === 'THANKS' ? 'THANK YOU FOR BEING HERE' : 'A LITTLE GOOD FORTUNE');
    put('result-symbol', hasBonus ? '✦' : draw.kind === 'THANKS' ? '♡' : draw.kind === 'RETRY' ? '↻' : '✦');
    put('result-done', hasBonus ? '收下这份额外惊喜 ✓' : draw.kind === 'RETRY' ? '再接一份好运 ↗' : draw.kind === 'THANKS' ? '期待下一次好运 ✓' : '收下这份心意 ✓');
    $('result-dialog').classList.toggle('has-bonus', hasBonus);
    show('result-bonuses', hasBonus);
    put('result-bonus-title', bonuses.length > 1 ? '双份保底，好运加倍' : '累计保底，额外惊喜');
    $('result-bonus-list').replaceChildren(...bonuses.map((bonus) => {
      const item = element('div', 'result-bonus-item');
      item.append(element('strong', '', bonus.label), element('span', `bonus-status${bonus.status === 'FULFILLED' ? ' is-fulfilled' : ''}`, awardStatus(bonus)));
      if (bonus.delivery_note) item.append(element('p', 'bonus-delivery', bonus.delivery_note));
      return item;
    }));
    openDialog('result-dialog'); announce(`抽奖结果：${draw.label}。红骰${draw.row}，蓝骰${draw.column}。${hasBonus ? `另获累计保底：${bonuses.map((bonus) => bonus.label).join('、')}。` : ''}`);
  }
  function showBatchResult(batch) {
    closeDialog('result-dialog');
    put('batch-pool', `${batch.campaign_name} · 10次结果均已保存`);
    const returned = 10 - batch.entries_consumed;
    const cash = (Array.isArray(batch.bonuses) ? batch.bonuses : []).reduce((sum, bonus) => sum + (Number.isInteger(bonus?.data?.balance_amount) ? bonus.data.balance_amount : 0), 0);
    const summaries = [['实际消耗机会', `${batch.entries_consumed} 次`, false], ['再来一次返还', `${returned} 次`, false], ['额外余额保底', cash > 0 ? `${(cash / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 元` : '未触发', cash > 0]];
    $('batch-summary').replaceChildren(...summaries.map(([label, value, bonus]) => {
      const item = element('div', `batch-summary-item${bonus ? ' is-bonus' : ''}`);
      item.append(element('span', '', label), element('strong', '', value)); return item;
    }));
    $('batch-results').replaceChildren(...batch.draws.map((draw, index) => {
      const bonuses = bonusesFor(draw);
      const card = element('article', `batch-result-card${bonuses.length ? ' has-bonus' : ''}`);
      const top = element('div', 'batch-result-top');
      top.append(element('span', 'batch-result-index', `第 ${String(index + 1).padStart(2, '0')} 抽`), element('span', '', Number.isInteger(draw.total_rolls) ? `本池累计 ${draw.total_rolls} 次` : '结果已保存'));
      const dice = element('div', 'batch-result-dice'); dice.append(element('span', '', `红骰 ${draw.row}`), element('span', '', `蓝骰 ${draw.column}`));
      const award = draw.award || draw.claim;
      card.append(top, element('h3', '', draw.label), dice, element('p', 'batch-result-status', award ? awardStatus(award) : draw.kind === 'RETRY' ? '已返还1次本奖池机会' : '结果已记录'));
      if (award?.delivery_note) card.append(element('p', 'history-delivery', award.delivery_note));
      if (bonuses.length) {
        const list = element('div', 'history-bonuses');
        bonuses.forEach((bonus) => { const row = element('div', 'history-bonus'); row.append(element('span', 'history-bonus-label', `额外保底 · ${bonus.label}`), element('span', `history-bonus-status${bonus.status === 'FULFILLED' ? ' is-fulfilled' : ''}`, awardStatus(bonus))); if (bonus.delivery_note) row.append(element('p', 'history-delivery', bonus.delivery_note)); list.append(row); });
        card.append(list);
      }
      card.append(element('p', 'batch-result-reference', `记录 #${draw.id}`));
      return card;
    }));
    $('batch-results').scrollTop = 0;
    openDialog('batch-dialog');
    announce(`十连抽已完成，实际消耗${batch.entries_consumed}次，返还${returned}次机会。${cash > 0 ? `额外余额保底${cash / 100}元。` : ''}`);
  }
  function startDiceThrow() {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    $('dice-stage').classList.toggle('reduced-roll', reduced);
    $('dice-stage').classList.add('is-rolling');
    return new Promise((resolve) => setTimeout(resolve, reduced ? 80 : 1100));
  }
  $('result-done').addEventListener('click', () => closeDialog('result-dialog'));
  $('result-history').addEventListener('click', () => { closeDialog('result-dialog'); $('my-history').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' }); });
  function goToHistory(dialog) {
    closeDialog(dialog);
    $('my-history').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
  }
  $('batch-done').addEventListener('click', () => closeDialog('batch-dialog'));
  $('batch-history').addEventListener('click', () => goToHistory('batch-dialog'));
  async function performDraw(requestedCount = 1) {
    if (state.busy) return;
    if (!state.session) { openDialog('login-dialog'); return; }
    if (!state.pending && (state.meFailed || !state.me)) { await loadMe(); return; }
    if (!state.pending) {
      const campaign = pool(); if (!campaign || campaign.status !== 'active' || !campaign.enabled || countFor(state.pool) < requestedCount) return;
      storePending({ campaign: state.pool, version: campaign.version, client_request_id: requestID(), count: requestedCount });
    }
    const pending = { ...state.pending };
    const batchMode = pending.count === 10;
    const snapshot = sessionSnapshot();
    const requestNumber = ++state.drawRequest;
    state.pool = pending.campaign; state.busy = true; message(); renderPool();
    $('die-row').dataset.value = '0'; $('die-column').dataset.value = '0'; $('dice-stage').setAttribute('aria-label', batchMode ? '十连抽正在确认，两枚骰子分别投掷中' : '两枚骰子分别投掷中，正在等待服务器确认结果');
    const throwFinished = startDiceThrow();
    announce(batchMode ? '正在进行十连抽，请等待服务器一次确认10份结果' : '正在抽奖，请等待服务器确认结果');
    try {
      const payload = { campaign: pending.campaign, version: pending.version, client_request_id: pending.client_request_id };
      const [data] = await Promise.all([api(batchMode ? 'draw-batch/' : 'draw/', { auth: true, session: snapshot, body: payload }), throwFinished]);
      if (!sessionIsCurrent(snapshot) || requestNumber !== state.drawRequest) return;
      if (!data || typeof data !== 'object') throw new APIError('抽奖结果尚未完整返回，请再次查询本次结果。', 0, true);
      const draws = batchMode ? data.draws : [data];
      if (!drawIsCurrent(data, snapshot) || (Array.isArray(draws) && draws.some((draw) => !drawIsCurrent(draw, snapshot)))) {
        expireSession();
        message('抽奖响应与当前房间或账号不匹配。请重新登录本房间后查询，原请求已保留，不会重复扣减次数。');
        return;
      }
      const validDraw = (draw) => draw.campaign === pending.campaign && Number.isInteger(draw.row) && draw.row >= 1 && draw.row <= 6 && Number.isInteger(draw.column) && draw.column >= 1 && draw.column <= 6 && typeof draw.label === 'string' && Boolean(draw.id) && ['THANKS', 'RETRY', 'REWARD'].includes(draw.kind);
      const validBatch = !batchMode || (data.count === 10 && Array.isArray(draws) && draws.length === 10 && new Set(draws.map((draw) => String(draw.id))).size === 10 && draws.every((draw, index) => draw.batch_index === index + 1) && Number.isInteger(data.entries_consumed) && data.entries_consumed === 10 - draws.filter((draw) => draw.kind === 'RETRY').length);
      if (!Array.isArray(draws) || !draws.every(validDraw) || !validBatch || data.campaign !== pending.campaign || !Number.isInteger(data.entries_remaining) || data.entries_remaining < 0) throw new APIError('抽奖结果尚未完整返回，请再次查询本次结果。', 0, true);
      // Older profile/page GETs cannot overwrite the committed draw or batch.
      state.meRequest += 1; state.meLoading = false; state.historyRequest += 1;
      const last = draws[draws.length - 1];
      storePending(null); state.lastResult = last;
      if (state.me) {
        state.me.entries[pending.campaign] = data.entries_remaining;
        const pity = data.pity || last.pity;
        if (pity) {
          state.me.pity = state.me.pity || {};
          const previous = state.me.pity[pending.campaign];
          if (!previous || pity.total_rolls >= previous.total_rolls) state.me.pity[pending.campaign] = pity;
        }
      }
      $('die-row').dataset.value = last.row; $('die-column').dataset.value = last.column; $('dice-stage').setAttribute('aria-label', `${batchMode ? '十连抽最后一组点数' : '本次结果'}：红骰${last.row}，蓝骰${last.column}`);
      selectCell(last, true); renderAccount();
      if (batchMode) showBatchResult(data); else showResult(data);
      void loadHistory({ reset: true });
      void loadMe();
    } catch (error) {
      if (!sessionIsCurrent(snapshot) || requestNumber !== state.drawRequest) return;
      if (error.status === 401 || error.status === 403) { expireSession(); message('登录已失效或房间不匹配，请重新登录本房间后查询上次结果。'); }
      else if (error.uncertain) message(`${error.message} 本次结果尚未确认，请点击“${batchMode ? '查询上次十连抽结果' : '查询上次抽奖结果'}”。`);
      else { storePending(null); message(error.message); await Promise.allSettled([loadCatalog(), loadMe()]); }
      announce(error.message);
    } finally {
      await throwFinished;
      if (sessionIsCurrent(snapshot) && requestNumber === state.drawRequest) { state.busy = false; $('dice-stage').classList.remove('is-rolling', 'reduced-roll'); renderDraw(); }
    }
  }
  $('draw-button').addEventListener('click', () => performDraw(1));
  $('batch-draw-button').addEventListener('click', () => performDraw(10));

  $('room-form')?.addEventListener('submit', async (event) => {
    event.preventDefault(); const code = $('room-code-input').value.trim();
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(code)) { put('room-entry-error', '请输入管理员提供的房间号（字母、数字、短横线或下划线）。'); show('room-entry-error', true); return; }
    const roomBase = base.replace(/api\/$/, '');
    window.location.assign(`${roomBase}r/${encodeURIComponent(code)}/`);
  });
  if (!room) { document.querySelector('.desktop-nav').hidden = true; return; }
  window.addEventListener('pagehide', () => {
    expireSession({ removeStored: false });
    state.catalogRequest += 1; state.catalog = null; state.loading = false;
  });
  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    expireSession({ removeStored: false });
    state.catalogRequest += 1; state.catalog = null; state.loading = false;
    const current = read(sessionKey);
    if (storedSessionIsCurrent(current)) state.session = current;
    else if (current) remove(sessionKey);
    loadPending(); renderAccount(); renderDraw(); renderHistory();
    void Promise.allSettled([loadCatalog(), loadMe()]);
  });
  loadPending(); renderAccount(); renderHistory();
  Promise.allSettled([loadCatalog(), loadMe()]);
})();
