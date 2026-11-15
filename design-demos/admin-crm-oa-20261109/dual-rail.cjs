const fs = require('node:fs')
const path = require('node:path')
const file = path.join(__dirname, 'dir-1.html')
let html = fs.readFileSync(file, 'utf8')
const brandLogo = html.match(/class="brand-logo" src="([^"]+)"/)[1]

const aside = `<aside class="sidebar" aria-label="后台业务导航" data-admin-sidebar data-sidebar-mode="rail-panel">
  <div class="sidebar-rail" data-admin-primary-rail>
    <div class="brand rail-brand"><img class="brand-logo" src="${brandLogo}" alt="电竞运营管理平台"></div>
    <button class="quick-dispatch quick-rail" id="quick-rail" aria-label="快捷派单" title="快捷派单" data-admin-quick-dispatch><svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65"><use href="#icon-order"/></svg></button>
    <button class="search-rail" id="search-rail" aria-label="搜索功能" title="搜索功能" data-admin-search-rail><svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65"><use href="#icon-search"/></svg></button>
    <nav class="sidebar-nav" id="rail-navigation" aria-label="一级功能导航"></nav>
    <section class="platform-navigation" id="platform-navigation" aria-label="平台管理" data-admin-platform-navigation></section>
  </div>
  <div class="sidebar-panel" data-admin-secondary-panel>
    <div class="panel-brand"><strong>电竞管理系统</strong><span>多门店运营管理</span></div>
    <div class="store-area"><span class="field-label">当前管理门店</span><button class="store-trigger" id="store-trigger" aria-expanded="false" aria-controls="store-menu"><img class="store-logo" id="store-logo" src="" alt=""><span class="store-name" id="store-trigger-label">声浪电竞</span><svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65"><use href="#icon-chevron"/></svg></button><div class="store-menu" id="store-menu" hidden></div></div>
    <div class="sidebar-search" data-admin-sidebar-search><svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65"><use href="#icon-search"/></svg><input class="sidebar-search-input" id="sidebar-search-input" aria-label="搜索后台功能" placeholder="搜索功能" autocomplete="off"><kbd>Ctrl K</kbd></div>
    <div class="panel-heading"><h2 id="panel-title">订单管理</h2></div>
    <nav class="admin-domain-panel" id="navigation" aria-label="二级功能组与三级页面"></nav>
    <div class="panel-quick"><button class="quick-dispatch" id="quick-dispatch" data-admin-quick-dispatch><svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65"><use href="#icon-order"/></svg><span>快捷派单</span></button></div>
    <div class="sidebar-footer"><button class="icon-button collapse-button" id="collapse" aria-label="收起菜单"><svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65"><use href="#icon-panel"/></svg><span class="collapse-label">收起菜单</span></button><button class="icon-button" id="mobile-close" aria-label="关闭导航"><svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65"><use href="#icon-close"/></svg></button></div>
  </div>
</aside>`
html = html.replace(/<aside class="sidebar"[\s\S]*?<\/aside>/, aside)
html = html.replace(/<button class="search-trigger"[\s\S]*?<\/button>/, '')
html = html.replace(/<dialog class="dialog" id="search-dialog"[\s\S]*?<\/dialog>/, '')

let script = html.match(/<script>([\s\S]*?)<\/script>/)[1]
script = script.replace(/const allFeatures=[\s\S]*?;\n/, `const allFeatures=config.globals.flatMap(domain=>domain.features.map(([path,title])=>({path,title,domain:domain.title,domainId:domain.id,center:'平台与工作区',icon:domain.icon}))).concat(config.direction.domains.flatMap(domain=>domain.centers.flatMap(center=>center.features.map(([path,title])=>({path,title,domain:domain.title,domainId:domain.id,center:center.title,icon:domain.icon})))));
`)
script = script.replace("const getDomain=id=>config.direction.domains.find(domain=>domain.id===id);", "const getDomain=id=>[...config.globals,...config.direction.domains].find(domain=>domain.id===id);")
script = script.replace("const center=domain?.centers.find", "const center=domain?.centers?.find")
script = script.replace("domain?'<span class=\"context-crumb\">'+domain.title+'</span>'+svg('arrow')", "(domain&&domain.id!=='overview')?'<span class=\"context-crumb\">'+domain.title+'</span>'+svg('arrow')")
script = script.replace("state.currentPath=path;state.activeDomain=feature.domainId||null;state.openDomain=feature.domainId||null;state.mobileOpen=false;", "state.currentPath=path;state.activeDomain=feature.domainId||null;state.openDomain=path==='dashboard'?null:(feature.domainId||null);state.collapsed=path==='dashboard';state.mobileOpen=false;")
script = script.replace(/const state=\{store:'shenglang',currentPath:config.direction.defaultPath,activeDomain:config.direction.defaultDomain,openDomain:config.direction.defaultDomain,collapsed:false,mobileOpen:false\};/, `const requestedPath=(location.hash.match(/^#\\/([^?]+)/)?.[1])||config.direction.defaultPath;
const initialDomain=[...config.globals,...config.direction.domains].find(domain=>(domain.features||domain.centers.flatMap(center=>center.features)).some(([path])=>path===requestedPath));
const initialPath=initialDomain?requestedPath:config.direction.defaultPath;
const initialDomainId=initialDomain?.id||config.direction.defaultDomain;
const state={store:'shenglang',currentPath:initialPath,activeDomain:initialDomainId,openDomain:initialPath==='dashboard'?null:initialDomainId,collapsed:initialPath==='dashboard',mobileOpen:false};`)
function replaceBetween(source, start, end, replacement) {
  const from = source.indexOf(start)
  const to = source.indexOf(end, from)
  if (from < 0 || to < 0) throw new Error(`Prototype block missing: ${start}`)
  return source.slice(0, from) + replacement + source.slice(to)
}
const renderNav = `function renderNav(){
  const domainButton=domain=>{
    const active=state.activeDomain===domain.id;const expanded=!state.collapsed&&state.openDomain===domain.id;
    const landing=domain.features?domain.features[0][0]:domain.centers[0].features[0][0];
    return '<button class="domain-trigger" data-domain="'+domain.id+'" data-admin-primary-nav="'+landing+'" aria-current="'+active+'" aria-expanded="'+expanded+'" title="'+domain.title+'" aria-label="'+domain.title+'">'+svg(domain.icon)+'<span class="page-title">'+domain.title+'</span></button>';
  };
  const workspace=config.globals.filter(domain=>domain.id==='overview');
  document.querySelector('#rail-navigation').innerHTML=workspace.map(domainButton).join('')+config.direction.domains.map(domainButton).join('');
  const platform=config.globals.find(domain=>domain.id==='platform');
  document.querySelector('#platform-navigation').innerHTML=domainButton(platform);
  const domain=getDomain(state.openDomain);
  document.querySelector('#panel-title').textContent=domain?.title||'功能导航';
  const centers=domain?(domain.centers||[{title:'',features:domain.features}]):[];
  document.querySelector('#navigation').innerHTML=domain?'<div class="secondary" data-admin-secondary-nav="'+domain.id+'">'+centers.map(center=>'<section class="center" aria-label="'+esc(center.title)+'">'+(centers.length>1&&center.title?'<h3 class="center-label">'+esc(center.title)+'</h3>':'')+center.features.map(([path,title])=>'<button class="page-link" data-page="'+path+'" data-admin-secondary-page="'+path+'" aria-current="'+(state.currentPath===path?'page':'false')+'"><span class="page-title">'+title+'</span>'+(state.currentPath===path?svg('arrow'):'')+'</button>').join('')+'</section>').join('')+'</div>':'<p class="panel-empty">选择左侧图标查看功能</p>';
  document.querySelector('#app').classList.toggle('is-collapsed',state.collapsed);document.querySelector('#app').classList.toggle('mobile-open',state.mobileOpen);
  document.querySelector('#collapse').setAttribute('aria-label',state.collapsed?'展开菜单':'收起菜单');
  const query=document.querySelector('#sidebar-search-input')?.value.trim()||'';if(query)renderSearch(query);
}
`
script = replaceBetween(script, 'function renderNav(){', 'function renderChrome(){', renderNav)
script = replaceBetween(script, 'function openSearch(){', 'function renderSearch(query){', `function openSearch(){state.collapsed=false;renderNav();const input=document.querySelector('#sidebar-search-input');input.focus();input.select()}
`)
script = replaceBetween(script, 'function renderSearch(query){', 'function clearFilters(){', `function renderSearch(query){
  const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const results=allFeatures.filter(feature=>{const text=[feature.title,feature.domain,feature.center,feature.path,...(config.keywords[feature.path]||[])].join(' ').toLocaleLowerCase();return terms.every(term=>text.includes(term))});
  document.querySelector('#panel-title').textContent='功能搜索 · '+results.length+' 项';
  document.querySelector('#navigation').innerHTML='<div class="sidebar-search-results" data-admin-sidebar-search-results>'+(results.length?results.map(feature=>'<button class="search-result" data-page="'+feature.path+'">'+svg(feature.icon)+'<span class="feature-text"><strong>'+feature.title+'</strong><small>'+feature.domain+' · '+feature.center+' · /'+feature.path+'</small></span>'+svg('arrow')+'</button>').join(''):'<p class="empty">没有找到可访问的功能，请尝试其他关键词</p>')+'</div>';
}
`)
const clickHandler = `document.addEventListener('click',event=>{
  const target=event.target.closest('button');if(!target)return;
  if(target.dataset.page){if(target.closest('.search-result'))document.querySelector('#sidebar-search-input').value='';document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());navigate(target.dataset.page);return}
  if(target.dataset.domain){const id=target.dataset.domain;if(state.collapsed)state.collapsed=false;if(state.activeDomain===id&&state.openDomain===id){state.openDomain=null;renderNav();return}navigate(getDomain(id).centers?getDomain(id).centers[0].features[0][0]:getDomain(id).features[0][0]);return}
  if(target.dataset.store){state.store=target.dataset.store;render();return}
  if(target.dataset.queue){document.querySelectorAll('[data-queue]').forEach(button=>button.setAttribute('aria-pressed',String(button===target)));document.querySelector('#filter-note').textContent='已切换到“'+target.dataset.queue+'”；仅演示队列状态，未发起业务查询。';return}
  if(target.id==='quick-dispatch'||target.id==='quick-rail'){state.collapsed=false;navigate('orders');history.replaceState(null,'','#/orders?dispatch=quick');document.querySelector('#quick-dialog').showModal();return}
  switch(target.id){
    case 'search-rail':openSearch();break;
    case 'store-trigger':{const menu=document.querySelector('#store-menu');menu.hidden=!menu.hidden;target.setAttribute('aria-expanded',String(!menu.hidden));break}
    case 'collapse':state.collapsed=!state.collapsed;renderNav();break;
    case 'mobile-toggle':state.mobileOpen=!state.mobileOpen;renderNav();break;
    case 'mobile-close':state.mobileOpen=false;renderNav();break;
    case 'quick-close':document.querySelector('#quick-dialog').close();history.replaceState(null,'','#/orders');break;
    case 'advanced-toggle':{const advanced=document.querySelector('#advanced-filters');advanced.hidden=!advanced.hidden;target.setAttribute('aria-expanded',String(!advanced.hidden));break}
    case 'clear-filters':case 'clear-table':clearFilters();break;
    case 'refresh-preview':notify('已刷新布局预览，未读取业务数据。');break;
  }
});
`
script = replaceBetween(script, "document.addEventListener('click'", "document.addEventListener('submit'", clickHandler)
script = replaceBetween(script, "document.addEventListener('input'", 'render();', `document.addEventListener('input',event=>{if(event.target.id==='sidebar-search-input'){const query=event.target.value.trim();if(query)renderSearch(query);else renderNav()}if(event.target.closest('#order-filter'))document.querySelector('#filter-note').textContent='筛选输入已更改，请点击查询更新列表。'});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();openSearch()}if(event.key==='Escape'&&state.mobileOpen){state.mobileOpen=false;renderNav()}});
window.addEventListener('hashchange',()=>{const path=(location.hash.match(/^#\\/([^?]+)/)?.[1])||config.direction.defaultPath;if(path!==state.currentPath)navigate(path)});
`)
const css = `
.app{grid-template-columns:288px minmax(0,1fr)}.sidebar{display:grid;grid-template-columns:72px minmax(0,1fr)}.sidebar-rail{display:flex;flex-direction:column;height:100vh;min-width:0;border-right:1px solid var(--line)}.rail-brand{display:flex;align-items:center;justify-content:center}.sidebar-panel{display:flex;flex-direction:column;height:100vh;min-width:0}.is-collapsed{grid-template-columns:72px minmax(0,1fr)}.is-collapsed .sidebar-panel{display:none}.panel-brand{height:80px;flex:none;display:flex;flex-direction:column;justify-content:center;padding:16px;border-bottom:1px solid var(--line)}.panel-brand strong{font-size:15px}.panel-brand span{font-size:12px;color:var(--muted);margin-top:2px}#rail-navigation{padding:8px}#rail-navigation .page-title{display:none}#rail-navigation .domain-trigger{justify-content:center;padding:8px}.platform-navigation{padding:8px}.platform-navigation .page-title{display:none}.platform-navigation .domain-trigger{justify-content:center;padding:8px}.sidebar-search{display:flex;align-items:center;gap:8px;flex:none;padding:12px;border-bottom:1px solid var(--line);color:var(--muted)}.sidebar-search-input{min-width:0;flex:1;height:40px;padding:0 4px;border:0;background:transparent;color:var(--navy);font-size:13px;outline:none}.sidebar-search kbd{font-size:10px;white-space:nowrap}.panel-heading{display:flex;align-items:center;min-height:48px;padding:12px 16px 4px}.panel-heading h2{font-size:14px;font-weight:600}.admin-domain-panel{min-height:0;flex:1;overflow-y:auto;padding:0 12px 16px;scrollbar-width:none}.admin-domain-panel::-webkit-scrollbar{display:none}.panel-empty{padding:16px 4px;color:var(--muted);font-size:13px}.panel-quick{flex:none;padding:12px;border-top:1px solid var(--line)}.quick-rail{display:none}.is-collapsed .quick-rail{display:flex;margin:8px}.sidebar-search-results{display:grid;gap:8px}.sidebar-search-results .search-result{border:1px solid var(--line);border-radius:6px;padding:8px}.sidebar-search-results .search-result small{line-height:1.5}.sidebar-search-results .search-result strong{font-size:13px}.is-collapsed .sidebar-footer{display:none}
@media(max-width:900px){.sidebar{grid-template-columns:72px minmax(0,1fr);width:288px}.sidebar-panel{width:215px}.is-collapsed .sidebar{width:72px}.sidebar-rail{width:72px}.panel-brand{padding:16px 12px}.panel-quick{padding:12px}}
`
const motionCss = `
.app{transition:grid-template-columns 200ms cubic-bezier(.2,.8,.2,1)}.search-rail{display:none;align-items:center;justify-content:center;width:44px;height:44px;padding:8px;border:0;border-radius:8px;background:transparent;color:#43516a}.search-rail:hover{background:#edf2f9}.is-collapsed .search-rail{display:flex;margin:0 8px 8px}.quick-dispatch svg,.search-rail svg{animation:sidebar-icon-enter 180ms cubic-bezier(.2,.8,.2,1);transition:transform 180ms cubic-bezier(.2,.8,.2,1)}.quick-dispatch:hover svg,.quick-dispatch:focus-visible svg,.search-rail:hover svg,.search-rail:focus-visible svg{transform:scale(1.12)}.quick-dispatch:active svg,.search-rail:active svg{transform:scale(.94)}.sidebar-panel{animation:sidebar-panel-enter 200ms cubic-bezier(.2,.8,.2,1);transform-origin:left center}.sidebar-search{animation:sidebar-search-enter 180ms cubic-bezier(.2,.8,.2,1);transform-origin:left center}@keyframes sidebar-icon-enter{from{transform:scale(.82)}to{transform:scale(1)}}@keyframes sidebar-panel-enter{from{opacity:0;transform:translateX(-8px) scale(.97)}to{opacity:1;transform:translateX(0) scale(1)}}@keyframes sidebar-search-enter{from{opacity:0;transform:scaleX(.92)}to{opacity:1;transform:scaleX(1)}}@media(prefers-reduced-motion:reduce){.app{transition:none}.sidebar-panel,.sidebar-search,.quick-dispatch svg,.search-rail svg{animation:none!important;transition:none!important;transform:none!important}}
`
html = html.replace('</style>', `${css}${motionCss}</style>`)
html = html.replace(/<script>[\s\S]*?<\/script>/, `<script>${script}</script>`)
fs.writeFileSync(file, html)
console.log(JSON.stringify({ updated: 'dir-1.html', layout: '72px icon rail + 215px menu panel', search: 'inline panel search', platform: 'independent rail section' }, null, 2))
