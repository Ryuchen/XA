(() => {
  const brands = window.XA_BRANDS
  const navigation = window.XA_NAVIGATION
  const icons = window.XA_ICONS
  const labels = {
    A: '清晰的任务层级', B: '留白的门店工作簿', C: '悬浮的门店导航台',
    D: '专注的深色业务树', E: '强网格的业务索引', F: '双栏界栏导航',
  }
  const domainIcons = {
    'platform-stores': 'HomeIcon', overview: 'DashboardIcon', orders: 'FileTextIcon',
    providers: 'PersonIcon', bosses: 'IdCardIcon', finance: 'CardStackIcon',
    support: 'ChatBubbleIcon', operations: 'BackpackIcon', system: 'GearIcon',
  }
  const esc = value => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character])
  const icon = name => icons[name] || icons.DashboardIcon
  const workspaceIds = ['platform-stores', 'overview']
  const featureIcon = path => ({ stores: 'HomeIcon', dashboard: 'DashboardIcon', orders: 'FileTextIcon', reports: 'CheckIcon', chat: 'ChatBubbleIcon' })[path] || 'FileTextIcon'
  const storeLogo = (slug, className = '') => `<img class="${className}" src="${brands[slug].logo}" alt="${esc(brands[slug].name)} Logo">`
  const currentFeature = state => navigation.flatMap(domain => domain.centers.map(center => ({ domain, center, features: center.features }))).flatMap(group => group.features.map(feature => ({ ...feature, domain: group.domain, center: group.center }))).find(feature => feature.path === state.feature) || { title: '订单与派单', domain: navigation.find(domain => domain.id === 'orders') }

  function storeSwitcher(state) {
    return `<div class="store-switcher">
      <span class="store-label">当前管理门店</span>
      <button class="store-current" data-store-menu aria-expanded="false" aria-label="切换当前管理门店，仅原型预览">
        ${storeLogo(state.store, 'store-logo')}
        <span class="store-copy"><strong>${esc(brands[state.store].name)}</strong><small>门店数据独立管理</small></span>
        ${icon('ChevronDownIcon')}
      </button>
      <div class="store-menu" hidden>
        ${Object.values(brands).filter(brand => brand.slug !== 'platform').map(brand => `<button class="store-option" data-store="${brand.slug}">${storeLogo(brand.slug)}<span>${esc(brand.name)}</span><small>${brand.currency}</small></button>`).join('')}
      </div>
    </div>`
  }

  function centerMarkup(center, state) {
    return `<section class="domain-center">
      ${center.features.length > 1 || state.variant === 'F' ? `<h3>${esc(center.title)}</h3>` : ''}
      ${center.features.map(feature => `<button class="child-link ${state.feature === feature.path ? 'is-active' : ''}" data-feature="${feature.path}" data-domain="${state.domain}" aria-current="${state.feature === feature.path ? 'page' : 'false'}"><span>${esc(feature.title)}</span>${state.feature === feature.path ? icon('ChevronRightIcon') : ''}</button>`).join('')}
    </section>`
  }

  function domainMarkup(domain, state) {
    const open = state.open.includes(domain.id)
    return `<section class="domain-group">
      <button class="domain-trigger ${state.domain === domain.id ? 'is-current' : ''}" data-domain="${domain.id}" aria-expanded="${open}" title="${esc(domain.title)}">${icon(domainIcons[domain.id])}<span class="domain-label">${esc(domain.title)}</span>${icon('ChevronDownIcon')}</button>
      <div class="domain-children" ${open ? '' : 'hidden'}>${domain.centers.map(center => centerMarkup(center, { ...state, domain: domain.id })).join('')}</div>
    </section>`
  }

  function workspaceMarkup(state) {
    return navigation.filter(domain => workspaceIds.includes(domain.id)).flatMap(domain => domain.centers.flatMap(center => center.features.map(feature => ({ ...feature, domain: domain.id })))).map(feature => `<button class="workspace-link ${state.feature === feature.path ? 'is-active' : ''}" data-feature="${feature.path}" data-domain="${feature.domain}" title="${esc(feature.title)}">${icon(featureIcon(feature.path))}<span>${esc(feature.title)}</span></button>`).join('')
  }

  function primaryNavigation(state) {
    const groups = [
      { title: '平台与工作区', content: workspaceMarkup(state) },
      { title: '门店经营', content: navigation.filter(domain => !workspaceIds.includes(domain.id) && !['operations', 'system'].includes(domain.id)).map(domain => domainMarkup(domain, state)).join('') },
      { title: '门店配置', content: navigation.filter(domain => ['operations', 'system'].includes(domain.id)).map(domain => domainMarkup(domain, state)).join('') },
    ]
    return groups.map(group => `<div class="nav-section-label">${group.title}</div>${group.content}`).join('')
  }

  function railNavigation(state) {
    return navigation.map(domain => `<button class="rail-domain ${state.domain === domain.id ? 'is-current' : ''}" data-domain="${domain.id}" title="${esc(domain.title)}" aria-label="${esc(domain.title)}">${icon(domainIcons[domain.id])}<span class="domain-label">${esc(domain.title)}</span></button>`).join('')
  }

  function sidebar(state) {
    const footer = `<div class="sidebar-footer"><button class="collapse-button" data-collapse title="${state.collapsed ? '展开侧栏' : '收起侧栏'}">${icon(state.collapsed ? 'PinRightIcon' : 'PinLeftIcon')}<span>${state.collapsed ? '展开侧栏' : '收起侧栏'}</span></button><button class="pin-button" data-pin aria-pressed="${state.locked}" title="${state.locked ? '取消锁定展开' : '锁定展开侧栏'}" aria-label="${state.locked ? '取消锁定展开' : '锁定展开侧栏'}">${icon(state.locked ? 'DrawingPinFilledIcon' : 'DrawingPinIcon')}</button></div>`
    if (state.variant === 'F') {
      const domain = navigation.find(item => item.id === state.domain) || navigation.find(item => item.id === 'orders')
      return `<aside class="prototype-sidebar" aria-label="后台业务导航">
        <div class="prototype-rail"><div class="prototype-brand"><img class="brand-logo" src="${brands.platform.logo}" alt="电竞运营管理平台 Logo"></div><nav class="prototype-navigation" aria-label="一级业务导航">${railNavigation(state)}</nav>${footer}</div>
        <div class="prototype-subnav">
          <div class="prototype-subnav-header"><strong>${esc(domain.title)}</strong><p>当前业务子菜单</p></div>
          ${storeSwitcher(state)}
          <div class="subnav-scroll">${domain.centers.map(center => centerMarkup(center, state)).join('')}</div>
          <button class="subnav-collapse" data-collapse>${icon('PinLeftIcon')}<span>收起二级导航</span></button>
        </div>
      </aside>`
    }
    return `<aside class="prototype-sidebar" aria-label="后台业务导航">
      <div class="prototype-brand"><img class="brand-logo" src="${brands.platform.logo}" alt="电竞运营管理平台 Logo"><div class="brand-copy"><strong>电竞管理系统</strong><small>多门店运营管理</small></div></div>
      ${storeSwitcher(state)}
      <nav class="prototype-navigation" aria-label="后台主导航">${primaryNavigation(state)}</nav>
      ${footer}
    </aside>`
  }

  function content(state) {
    const feature = currentFeature(state)
    return `<main class="prototype-main">
      <header class="prototype-topbar"><div class="breadcrumbs">${esc(brands[state.store].name)}　/　${esc(feature.domain?.title || '订单中心')}　/　<strong>${esc(feature.title)}</strong></div><button class="prototype-search" data-search>${icon('MagnifyingGlassIcon')}<span>搜索功能</span><kbd>Ctrl K</kbd></button></header>
      <div class="prototype-content"><div class="prototype-context"><span class="prototype-eyebrow">方案 ${state.variant} · ${labels[state.variant]}</span><a class="gallery-return" href="index.html">返回方案比较</a></div><h1>${esc(feature.title)}<span class="prototype-ribbon">交互设计原型 · 非真实业务数据</span></h1><p class="prototype-intro">只比较导航结构、门店上下文和内容区空间，不改变真实菜单权限。</p>
        <div class="prototype-notes"><section class="prototype-note">${icon('HomeIcon')}<h2>当前门店清晰</h2><p>${esc(brands[state.store].name)}作为数据边界。切换仅用于演示，不请求真实门店接口。</p></section><section class="prototype-note">${icon('FileTextIcon')}<h2>业务路径清楚</h2><p>订单、陪玩与老板仍是核心入口；分类和页面归属保持与当前后台一致。</p></section><section class="prototype-note">${icon('CheckIcon')}<h2>当前位置明确</h2><p>${esc(feature.title)}通过位置、字重和品牌色共同反馈，不只依靠单一颜色。</p></section></div>
        <section class="prototype-stage"><h2>内容区对照</h2><p>这里用于观察侧栏宽度、菜单密度与当前位置。页面中的结构和留白仅供设计评审，不作为业务功能或数据验收。</p><div class="prototype-skeleton"><span></span><span></span><span></span></div></section>
      </div>
    </main>`
  }

  function searchDialog(state) {
    return `<dialog class="search-dialog" aria-label="搜索后台功能"><form><input type="search" aria-label="搜索菜单" placeholder="输入菜单名称，例如：订单、陪玩、财务" autocomplete="off"></form><div class="search-results">${navigation.flatMap(domain => domain.centers.flatMap(center => center.features.map(feature => `<button class="search-result" data-feature="${feature.path}" data-domain="${domain.id}" data-search-text="${esc(feature.title + domain.title + center.title)}">${icon(featureIcon(feature.path))}<span>${esc(feature.title)}</span><small>${esc(domain.title)}</small></button>`))).join('')}</div><form method="dialog"><button>关闭</button></form></dialog>`
  }

  function initialState(variant) {
    return { variant, store: 'shenglang', feature: 'orders', domain: 'orders', open: ['orders'], collapsed: false, locked: true }
  }

  function mount(root, variant, options = {}, previous) {
    const state = previous || initialState(variant)
    root.__sidebarState = state
    root.className = `prototype-app variant-${variant}${state.collapsed ? ' is-collapsed' : ''}`
    root.dataset.variant = variant
    root.innerHTML = sidebar(state) + content(state) + searchDialog(state)
    if (options.mini) return
    const dialog = root.querySelector('.search-dialog')
    const notify = text => {
      const toast = document.createElement('div')
      toast.className = 'prototype-toast'
      toast.setAttribute('role', 'status')
      toast.textContent = text
      root.appendChild(toast)
      setTimeout(() => toast.remove(), 2600)
    }
    const update = () => mount(root, variant, options, state)
    root.onclick = event => {
      const target = event.target.closest('button')
      if (!target) return
      if (target.matches('[data-store-menu]')) {
        const menu = target.parentElement.querySelector('.store-menu')
        menu.hidden = !menu.hidden
        target.setAttribute('aria-expanded', String(!menu.hidden))
        return
      }
      if (target.dataset.store) {
        state.store = target.dataset.store
        update()
        notify(`已切换到${brands[state.store].name} · 仅原型预览，未修改真实门店`)
        return
      }
      if (target.hasAttribute('data-search')) {
        dialog.showModal()
        dialog.querySelector('input').focus()
        return
      }
      if (target.dataset.feature) {
        state.feature = target.dataset.feature
        state.domain = target.dataset.domain
        if (!state.open.includes(state.domain)) state.open.push(state.domain)
        if (state.variant === 'F' && state.collapsed && !workspaceIds.includes(state.domain)) state.collapsed = false
        update()
        return
      }
      if (target.dataset.domain) {
        const domain = navigation.find(item => item.id === target.dataset.domain)
        state.domain = target.dataset.domain
        if (state.variant === 'F') {
          state.feature = domain.centers[0].features[0].path
        } else if (state.collapsed) {
          state.collapsed = false
          state.open = [target.dataset.domain]
        } else {
          state.open = state.open.includes(target.dataset.domain) ? state.open.filter(id => id !== target.dataset.domain) : [target.dataset.domain]
        }
        update()
        return
      }
      if (target.hasAttribute('data-collapse')) {
        state.collapsed = !state.collapsed
        state.locked = false
        update()
        return
      }
      if (target.hasAttribute('data-pin')) {
        state.locked = !state.locked
        if (state.locked) state.collapsed = false
        update()
      }
    }
    dialog.querySelector('input').oninput = event => {
      const keyword = event.target.value.trim().toLocaleLowerCase()
      dialog.querySelectorAll('[data-search-text]').forEach(button => { button.hidden = keyword && !button.dataset.searchText.toLocaleLowerCase().includes(keyword) })
    }
    root.onkeydown = event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') {
        event.preventDefault()
        dialog.showModal()
        dialog.querySelector('input').focus()
      }
    }
  }

  window.SidebarPrototype = { mount, labels }
})()
