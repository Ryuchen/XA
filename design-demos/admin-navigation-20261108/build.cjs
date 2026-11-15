const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const root = __dirname
const data = JSON.parse(fs.readFileSync(path.join(root, 'navigation-options.json'), 'utf8'))
const source = fs.readFileSync(path.resolve(root, '../../fronted/admin/src/lib/navigation.ts'), 'utf8')
const keywords = { dictionaries: ['统一字典', '字典与配置', '业务字典', '薪资岗位', '投流渠道', '成本类别', '初始化', '下单游戏资料'] }
const expectedPaths = ['dictionaries']
for (const match of source.matchAll(/\{ path: "([^"]+)", title: "([^"]+)"(?:, keywords: (\[[^\]]*\]))? \}/g)) {
  expectedPaths.push(match[1])
  keywords[match[1]] = JSON.parse(match[3] || '[]')
}
assert.equal(expectedPaths.length, 45)
const esc = value => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]))
const logo = name => `data:image/svg+xml;base64,${fs.readFileSync(path.join(root, `${name}.svg`)).toString('base64')}`
const logos = { platform: logo('platform'), shenglang: logo('shenglang'), xinan: logo('xinan') }

const iconPaths = {
  store: '<path d="M4 10v10h16V10M3 4h18l-2 6H5L3 4Z"/><path d="M9 20v-6h6v6M3 4l2-2h14l2 2"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  order: '<path d="M7 3h10v4h3v14H4V7h3V3Z"/><path d="M7 3v4h10V3M8 12h8M8 16h5"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 5v1"/>',
  member: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0M17 3l1 2 2 1-2 1-1 2-1-2-2-1 2-1 1-2Z"/>',
  wallet: '<path d="M3 6a3 3 0 0 1 3-3h13v18H5a2 2 0 0 1-2-2V6Z"/><path d="M3 7h16M14 11h7v6h-7a3 3 0 0 1 0-6Z"/>',
  chat: '<path d="M21 11a8 8 0 0 1-8 8H5l-3 3 1-6a9 9 0 1 1 18-5Z"/><path d="M8 11h.01M12 11h.01M16 11h.01"/>',
  gift: '<path d="M3 9h18v4H3zM5 13h14v9H5zM12 9v13M12 9H7a4 4 0 1 1 4-4c1 2 1 4 1 4Zm0 0h5a4 4 0 1 0-4-4c-1 2-1 4-1 4Z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="m19 13 2 1-2 4-2-1a8 8 0 0 1-2 1l-1 3h-4l-1-3a8 8 0 0 1-2-1l-2 1-2-4 2-1a8 8 0 0 1 0-3L3 9l2-4 2 1a8 8 0 0 1 2-1l1-3h4l1 3a8 8 0 0 1 2 1l2-1 2 4-2 1a8 8 0 0 1 0 3Z"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  chevron: '<path d="m8 10 4 4 4-4"/>',
  arrow: '<path d="m9 5 7 7-7 7"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18m7-13-4 4 4 4"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  book: '<path d="M4 3h6a3 3 0 0 1 3 3v15a3 3 0 0 0-3-3H4V3Zm16 0h-4a3 3 0 0 0-3 3v15a3 3 0 0 1 3-3h4V3Z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
}
const icon = (name, className = '') => `<svg class="icon ${className}" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.grid}</svg>`

for (const direction of data.directions) {
  const paths = [...data.globals, ...direction.domains].flatMap(domain => (domain.features || domain.centers.flatMap(center => center.features)).map(feature => feature[0]))
  assert.deepEqual([...paths].sort(), [...expectedPaths].sort(), `${direction.key} must cover each real route once`)
  assert.equal(new Set(paths).size, 45)
  const centerIds = direction.domains.flatMap(domain => domain.centers.map(center => center.id))
  assert.equal(centerIds.length, new Set(centerIds).size)
}

const css = `
:root{--blue:#2563eb;--navy:#0f172a;--muted:#526078;--line:#dfe5ef;--canvas:#f5f7fb;--paper:#fff;--sidebar:#fbfcfe;--tint:#eaf1ff;color-scheme:light;font-family:MiSans,Inter,system-ui,-apple-system,"Microsoft YaHei",sans-serif;font-size:14px;color:var(--navy);background:var(--canvas)}
*{box-sizing:border-box}body{margin:0}button,input{font:inherit}button{cursor:pointer}button,a{-webkit-tap-highlight-color:transparent}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #7dd3fc;outline-offset:2px}button{color:inherit}.icon{display:block;flex:none}.app{display:grid;grid-template-columns:264px minmax(0,1fr);min-height:100vh}.sidebar{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;background:var(--sidebar);border-right:1px solid var(--line);z-index:40;transition:width .2s ease,transform .2s ease}.brand{height:80px;display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line);flex:none}.brand-logo,.store-logo{width:40px;height:40px;object-fit:contain;flex:none}.brand-name{min-width:0}.brand-name strong{display:block;font-size:15px;font-weight:650;white-space:nowrap}.brand-name span{display:block;font-size:12px;color:var(--muted);margin-top:2px}.store-area{padding:12px;border-bottom:1px solid var(--line);position:relative;flex:none}.field-label{display:block;font-size:12px;color:var(--muted);padding:0 8px 4px}.store-trigger{height:44px;width:100%;display:flex;align-items:center;gap:8px;border:1px solid transparent;border-radius:8px;background:transparent;padding:4px 8px;text-align:left}.store-trigger:hover{background:#eef3fa}.store-trigger .store-logo{width:32px;height:32px}.store-trigger .store-name{flex:1;font-weight:550}.store-menu{position:absolute;z-index:70;left:12px;top:88px;width:256px;padding:8px;background:var(--paper);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 40px #0f172a1a}.store-option{display:flex;align-items:center;gap:12px;min-height:56px;width:100%;padding:8px;border:0;border-radius:6px;background:transparent;text-align:left}.store-option:hover,.store-option[aria-pressed=true]{background:var(--tint)}.store-option .store-logo{width:32px;height:32px}.store-option small{display:block;color:var(--muted);font-size:12px}.sidebar-nav{flex:1;overflow-y:auto;padding:16px 12px;scrollbar-width:none}.sidebar-nav::-webkit-scrollbar{display:none}.nav-section-label{font-size:12px;color:var(--muted);padding:0 12px;margin:20px 0 8px}.nav-section-label:first-child{margin-top:0}.global-link,.domain-trigger{display:flex;align-items:center;gap:12px;min-height:44px;width:100%;padding:8px 12px;border:0;border-radius:8px;background:transparent;text-align:left;color:#34435b}.global-link:hover,.domain-trigger:hover{background:#edf2f9;color:var(--navy)}.global-link[aria-current=page],.domain-trigger[aria-current=true]{background:var(--tint);color:#1d4ed8;font-weight:600}.domain-trigger .chevron,.global-link .page-title,.domain-trigger .page-title{flex:1}.domain-trigger[aria-expanded=true] .chevron{transform:rotate(180deg)}.chevron{transition:transform .16s ease}.secondary{margin:4px 0 8px 20px;padding-left:12px;border-left:1px solid var(--line)}.center{margin:0 0 12px}.center:last-child{margin-bottom:0}.center-label{font-size:12px;line-height:20px;color:var(--muted);padding:0 8px}.page-link{display:flex;align-items:center;gap:8px;min-height:44px;width:100%;padding:8px;border:0;border-radius:6px;background:transparent;text-align:left;font-size:13px;color:#46546c}.page-link:hover{background:#edf2f9;color:var(--navy)}.page-link[aria-current=page]{background:#e2ebfc;color:#1d4ed8;font-weight:600}.page-link .page-title{flex:1}.sidebar-footer{display:flex;align-items:center;gap:4px;padding:12px;border-top:1px solid var(--line);flex:none}.icon-button{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-width:44px;min-height:44px;padding:8px;border:0;border-radius:8px;background:transparent;color:#43516a}.icon-button:hover{background:#edf2f9}.collapse-button{flex:1;justify-content:flex-start;font-size:13px}.workspace{min-width:0}.topbar{height:64px;position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:16px;padding:0 28px;border-bottom:1px solid var(--line);background:#ffffffed;backdrop-filter:blur(12px)}.breadcrumbs{display:flex;align-items:center;gap:8px;min-width:0;flex:1;color:var(--muted);font-size:13px}.breadcrumbs span{white-space:nowrap}.breadcrumbs strong{color:var(--navy);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.search-trigger{display:flex;align-items:center;gap:8px;height:40px;min-width:220px;padding:0 12px;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--muted);text-align:left}.search-trigger kbd{margin-left:auto;border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:12px;font-family:inherit;color:#53637b}.account{display:flex;align-items:center;gap:8px;white-space:nowrap;font-size:13px}.account-dot{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;background:var(--blue);color:white;font-size:12px}.mobile-menu{display:none}.content{padding:32px;max-width:1680px}.intro{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:24px}.direction-name{font-size:14px;color:#1d4ed8;font-weight:600;margin-bottom:8px}.intro h1{margin:0;font-size:28px;line-height:1.35;letter-spacing:-.02em;font-weight:650}.intro p{max-width:780px;margin:12px 0 0;color:var(--muted);font-size:15px;line-height:1.75}.outline-button{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:8px 16px;border:1px solid #c6d2e5;border-radius:8px;background:var(--paper);white-space:nowrap;font-weight:550}.outline-button:hover{background:var(--tint);border-color:#9bb6e9}.prototype-note{display:flex;align-items:flex-start;gap:12px;padding:12px 16px;margin-bottom:24px;border:1px solid #d8e2f1;border-radius:8px;background:#f0f5fc;color:#3c4c65;line-height:1.65}.prototype-note .icon{margin-top:2px;color:var(--blue)}.lifecycle{display:flex;align-items:center;gap:8px;overflow-x:auto;padding:0 0 20px;scrollbar-width:none}.lifecycle::-webkit-scrollbar{display:none}.lifecycle-step{display:flex;align-items:center;gap:8px;min-height:44px;padding:8px 16px;background:var(--paper);border:1px solid var(--line);border-radius:8px;white-space:nowrap}.lifecycle-step.active{border-color:#9db9ed;background:var(--tint);color:#1d4ed8;font-weight:600}.lifecycle-arrow{color:#8a99af}.structure{background:var(--paper);border:1px solid var(--line);border-radius:12px;overflow:hidden}.structure-header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding:24px;border-bottom:1px solid var(--line)}.structure-header h2{font-size:19px;margin:0;font-weight:650}.structure-header p{margin:8px 0 0;color:var(--muted);line-height:1.7}.page-count{flex:none;font-size:12px;padding:4px 8px;background:#eef3fa;border-radius:4px;color:#3d4d66}.center-row{display:grid;grid-template-columns:192px minmax(0,1fr);gap:24px;padding:24px;border-bottom:1px solid #e8edf4}.center-row:last-child{border-bottom:0}.center-heading h3{font-size:15px;margin:0;font-weight:600}.center-heading p{font-size:12px;color:var(--muted);margin:8px 0 0}.feature-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.feature{display:flex;align-items:center;gap:12px;min-height:68px;padding:12px 16px;border:1px solid #dce4ef;border-radius:8px;background:#fff;text-align:left}.feature:hover{border-color:#94b3ea;background:#f8faff}.feature[aria-current=page]{border-color:#8aaee9;background:var(--tint)}.feature-text{flex:1;min-width:0}.feature-name{display:block;font-size:14px;font-weight:550}.feature-route{display:block;margin-top:4px;font-size:12px;color:#53647d;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.feature .icon{color:#6680a7}.feature[aria-current=page] .icon{color:var(--blue)}.work-role{display:flex;align-items:center;gap:12px;min-height:48px;margin-bottom:16px;padding:0 16px;background:#edf3fd;border:1px solid #d8e4f7;border-radius:8px;color:#284a85}.work-role strong{font-weight:650}.work-role span{color:#536c92}.overview-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:24px}.overview-domain{padding:16px;border:1px solid var(--line);border-radius:8px}.overview-domain h3{margin:0 0 8px;font-size:16px}.overview-domain p{margin:0 0 12px;color:var(--muted);line-height:1.65}.text-button{border:0;background:transparent;color:#1d4ed8;min-height:44px;padding:4px 0;font-weight:550}.dialog{width:min(860px,calc(100vw - 48px));max-height:calc(100vh - 64px);padding:0;border:1px solid var(--line);border-radius:12px;background:var(--paper);color:var(--navy);box-shadow:0 24px 100px #0f172a33}.dialog::backdrop{background:#0f172a66;backdrop-filter:blur(3px)}.dialog-header{display:flex;align-items:center;gap:16px;padding:16px 24px;border-bottom:1px solid var(--line)}.dialog-header h2{flex:1;font-size:18px;margin:0}.dialog-body{padding:24px;overflow-y:auto;max-height:calc(100vh - 152px)}.search-input{width:100%;height:48px;padding:0 16px;border:1px solid #bdccdf;border-radius:8px;background:#fff;color:var(--navy);font-size:15px}.search-results{margin-top:16px}.search-result{display:flex;align-items:center;gap:12px;width:100%;min-height:64px;padding:12px;border:0;border-bottom:1px solid #e7edf4;background:transparent;text-align:left}.search-result:hover{background:#f2f6fd}.search-result strong{display:block;font-size:14px}.search-result small{display:block;color:var(--muted);font-size:12px;margin-top:4px}.directory-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.directory-domain{border:1px solid var(--line);border-radius:8px;padding:16px}.directory-domain h3{display:flex;gap:8px;align-items:center;font-size:15px;margin:0 0 12px}.directory-center{margin-top:12px}.directory-center h4{font-size:12px;color:var(--muted);margin:0 0 4px;font-weight:500}.directory-page{display:block;width:100%;min-height:32px;padding:4px 0;border:0;background:transparent;text-align:left;color:#28466f;font-size:13px}.directory-page:hover{text-decoration:underline;color:var(--blue)}.empty{padding:32px 0;text-align:center;color:var(--muted)}.scrim{display:none}.is-collapsed{grid-template-columns:72px minmax(0,1fr)}.is-collapsed .brand{padding:16px;justify-content:center}.is-collapsed .brand-name,.is-collapsed .field-label,.is-collapsed .store-name,.is-collapsed .store-trigger>.icon,.is-collapsed .nav-section-label,.is-collapsed .page-title,.is-collapsed .chevron,.is-collapsed .collapse-label,.is-collapsed .secondary{display:none}.is-collapsed .store-trigger,.is-collapsed .global-link,.is-collapsed .domain-trigger{justify-content:center;padding:8px}.is-collapsed .sidebar-nav{padding:16px 8px}.is-collapsed .store-menu{left:60px;top:12px}.is-collapsed .collapse-button .icon{transform:rotate(180deg)}
.direction-B .structure{border-top:3px solid #6c83b5}.direction-B .center-row{grid-template-columns:224px minmax(0,1fr)}.direction-B .center-heading{border-left:1px solid #bac9e2;padding-left:16px}.direction-C .structure-header{background:#f0f4fa}.direction-C .center-row{grid-template-columns:160px minmax(0,1fr)}.direction-C .feature-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.direction-C .feature{padding:12px;align-items:flex-start}.direction-C .feature>.icon:last-child{display:none}
@media(min-width:1600px){.feature-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.content{padding:40px}.center-row{grid-template-columns:224px minmax(0,1fr)}}
@media(max-width:1100px){.direction-C .feature-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.account .account-label{display:none}.topbar{padding:0 20px}.content{padding:24px}.intro{flex-direction:column}.center-row{grid-template-columns:160px minmax(0,1fr);gap:16px}}
@media(max-width:900px){.app,.is-collapsed{display:block}.sidebar,.is-collapsed .sidebar{position:fixed;left:0;top:0;width:264px;transform:translateX(-100%);box-shadow:16px 0 48px #0f172a1f}.mobile-open .sidebar{transform:translateX(0)}.is-collapsed .brand-name,.is-collapsed .field-label,.is-collapsed .store-name,.is-collapsed .store-trigger>.icon,.is-collapsed .nav-section-label,.is-collapsed .page-title,.is-collapsed .chevron,.is-collapsed .collapse-label{display:initial}.is-collapsed .brand{padding:16px 20px;justify-content:flex-start}.is-collapsed .store-trigger,.is-collapsed .global-link,.is-collapsed .domain-trigger{justify-content:flex-start;padding:8px 12px}.is-collapsed .sidebar-nav{padding:16px 12px}.is-collapsed .secondary{display:block}.mobile-open .scrim{display:block;position:fixed;inset:0;background:#0f172a66;z-index:35;border:0}.mobile-menu{display:inline-flex}.topbar{height:64px;padding:0 16px;gap:8px}.breadcrumbs{font-size:12px}.breadcrumbs .context-crumb{display:none}.search-trigger{min-width:44px;width:44px;padding:0;justify-content:center}.search-trigger span,.search-trigger kbd{display:none}.content{padding:20px 16px}.intro h1{font-size:24px}.intro p{font-size:16px}.structure-header{padding:20px;flex-direction:column}.center-row,.direction-B .center-row,.direction-C .center-row{grid-template-columns:1fr;padding:20px;gap:16px}.direction-B .center-heading{border-left:0;padding-left:0}.feature-grid,.direction-C .feature-grid{grid-template-columns:1fr}.feature-name{font-size:16px}.overview-grid,.directory-grid{grid-template-columns:1fr}.dialog{width:calc(100vw - 32px);max-height:calc(100vh - 32px)}.dialog-body{max-height:calc(100vh - 112px);padding:16px}.dialog-header{padding:12px 16px}.prototype-note{font-size:14px}.page-count{white-space:nowrap}.store-menu{left:12px;top:88px}.collapse-button{display:none}}
@media(min-width:901px){#mobile-close{display:none}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;scroll-behavior:auto!important}}
`

function runtime(direction) {
  const config = JSON.stringify({ globals: data.globals, direction, keywords }).replace(/</g, '\\u003c')
  return `
const config=${config};
const state={store:'shenglang',currentPath:config.direction.defaultPath,activeDomain:config.direction.defaultDomain,openDomain:config.direction.defaultDomain,collapsed:false,mobileOpen:false};
const storeInfo={shenglang:{name:'声浪电竞',currency:'浪花',logo:${JSON.stringify(logos.shenglang)}},xinan:{name:'兴安电竞',currency:'星辉',logo:${JSON.stringify(logos.xinan)}}};
const allFeatures=config.globals.flatMap(domain=>domain.features.map(([path,title])=>({path,title,domain:domain.title,center:'平台与工作区',icon:domain.icon}))).concat(config.direction.domains.flatMap(domain=>domain.centers.flatMap(center=>center.features.map(([path,title])=>({path,title,domain:domain.title,domainId:domain.id,center:center.title,icon:domain.icon})))));
const getFeature=path=>allFeatures.find(feature=>feature.path===path);
const getDomain=id=>config.direction.domains.find(domain=>domain.id===id);
const featureCount=domain=>(domain.features||domain.centers.flatMap(center=>center.features)).length;
const icon=name=>document.querySelector('#icon-'+name)?.innerHTML||'';
function svg(name,className=''){return '<svg class="icon '+className+'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icon(name)+'</svg>'}
function esc(value){return String(value).replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]))}
function navigate(path){
  const feature=getFeature(path);if(!feature)return;
  state.currentPath=path;state.activeDomain=feature.domainId||null;state.openDomain=feature.domainId||null;state.mobileOpen=false;
  history.replaceState(null,'','#/'+path);render();
}
function renderNav(){
  const globals=config.globals.map(domain=>{const [path,title]=domain.features[0];return '<button class="global-link" data-page="'+path+'" aria-current="'+(state.currentPath===path?'page':'false')+'" title="'+domain.title+'">'+svg(domain.icon)+'<span class="page-title">'+domain.title+'</span></button>'}).join('');
  const domains=config.direction.domains.map(domain=>{
    const active=state.activeDomain===domain.id;const expanded=!state.collapsed&&state.openDomain===domain.id;
    const centers=domain.centers.map(center=>'<section class="center" aria-label="'+esc(center.title)+'"><h3 class="center-label">'+esc(center.title)+'</h3>'+center.features.map(([path,title])=>'<button class="page-link" data-page="'+path+'" aria-current="'+(state.currentPath===path?'page':'false')+'"><span class="page-title">'+title+'</span>'+(state.currentPath===path?svg('arrow'):'')+'</button>').join('')+'</section>').join('');
    return '<div class="domain"><button class="domain-trigger" data-domain="'+domain.id+'" aria-current="'+active+'" aria-expanded="'+expanded+'" aria-controls="menu-'+domain.id+'" title="'+domain.title+'">'+svg(domain.icon)+'<span class="page-title">'+domain.title+'</span>'+svg('chevron','chevron')+'</button>'+(expanded?'<div class="secondary" id="menu-'+domain.id+'">'+centers+'</div>':'')+'</div>';
  }).join('');
  document.querySelector('#navigation').innerHTML='<div class="nav-section-label">平台与工作区</div>'+globals+'<div class="nav-section-label">'+(config.direction.key==='C'?'岗位工作区':'业务导航')+'</div>'+domains;
  document.querySelector('#app').classList.toggle('is-collapsed',state.collapsed);
  document.querySelector('#app').classList.toggle('mobile-open',state.mobileOpen);
  document.querySelector('#collapse').setAttribute('aria-label',state.collapsed?'展开侧栏':'收起侧栏');
  document.querySelector('#collapse').title=state.collapsed?'展开侧栏':'收起侧栏';
}
function renderChrome(){
  const store=storeInfo[state.store];
  document.querySelector('#store-trigger-label').textContent=store.name;
  document.querySelector('#store-logo').src=store.logo;
  document.querySelector('#store-menu').innerHTML=Object.entries(storeInfo).map(([key,item])=>'<button class="store-option" data-store="'+key+'" aria-pressed="'+(state.store===key)+'"><img class="store-logo" src="'+item.logo+'" alt=""><span>'+item.name+'<small>'+item.currency+' · '+(state.store===key?'当前门店':'切换演示')+'</small></span></button>').join('');
  document.querySelector('#store-menu').hidden=true;
  document.querySelector('#store-trigger').setAttribute('aria-expanded','false');
  document.title=store.name+' · 管理后台导航方案 '+config.direction.key;
}
function featureCards(center){
  return '<div class="feature-grid">'+center.features.map(([path,title])=>'<button class="feature" data-page="'+path+'" aria-current="'+(state.currentPath===path?'page':'false')+'">'+svg('book')+'<span class="feature-text"><span class="feature-name">'+title+'</span><span class="feature-route">/'+path+'</span></span>'+svg('arrow')+'</button>').join('')+'</div>';
}
function lifecycle(domain){
  if(config.direction.key==='B')return '<div class="lifecycle" aria-label="服务业务链路">'+config.direction.domains.slice(0,4).map((item,index)=>'<button class="lifecycle-step '+(item.id===domain.id?'active':'')+'" data-domain="'+item.id+'">'+item.title+'</button>'+(index<3?'<span class="lifecycle-arrow">'+svg('arrow')+'</span>':'')).join('')+'</div>';
  if(config.direction.key==='C')return '<div class="work-role">'+svg(domain.icon)+'<strong>'+domain.title+'</strong><span>'+config.direction.principle+'</span></div>';
  return '';
}
function renderMain(){
  const feature=getFeature(state.currentPath);const domain=getDomain(state.activeDomain);
  document.querySelector('#breadcrumbs').innerHTML='<span>'+storeInfo[state.store].name+'</span>'+svg('arrow')+(domain?'<span class="context-crumb">'+domain.title+'</span>'+svg('arrow'):'')+'<strong>'+feature.title+'</strong>';
  let body='';
  if(!domain){
    body='<section class="structure"><div class="structure-header"><div><h2>'+feature.title+'</h2><p>'+(feature.path==='stores'?'平台管理保持独立授权；普通门店角色不会看到此入口。':'工作台汇总当前账号有权查看的经营信息。此原型不读取真实业务数据。')+'</p></div></div><div class="overview-grid">'+config.direction.domains.map(item=>'<article class="overview-domain"><h3>'+item.title+'</h3><p>'+item.description+'</p><button class="text-button" data-domain="'+item.id+'">查看 '+featureCount(item)+' 个页面 →</button></article>').join('')+'</div></section>';
  }else{
    body=lifecycle(domain)+'<section class="structure"><div class="structure-header"><div><h2>'+domain.title+'</h2><p>'+domain.description+'</p></div><span class="page-count">'+domain.centers.length+' 个中心 · '+featureCount(domain)+' 个页面</span></div>'+domain.centers.map(center=>'<section class="center-row"><div class="center-heading"><h3>'+center.title+'</h3><p>'+center.features.length+' 个现有页面</p></div>'+featureCards(center)+'</section>').join('')+'</section>';
  }
  document.querySelector('#domain-content').innerHTML=body;
}
function render(){renderNav();renderChrome();renderMain()}
function openSearch(){
  const dialog=document.querySelector('#search-dialog');renderSearch(document.querySelector('#search-input').value);dialog.showModal();document.querySelector('#search-input').focus();
}
function renderSearch(query){
  const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const results=allFeatures.filter(feature=>{const text=[feature.title,feature.domain,feature.center,feature.path,...(config.keywords[feature.path]||[])].join(' ').toLocaleLowerCase();return terms.every(term=>text.includes(term))});
  document.querySelector('#search-results').innerHTML=results.length?results.map(feature=>'<button class="search-result" data-page="'+feature.path+'">'+svg(feature.icon)+'<span class="feature-text"><strong>'+feature.title+'</strong><small>'+feature.domain+' · '+feature.center+' · /'+feature.path+'</small></span>'+svg('arrow')+'</button>').join(''):'<p class="empty">没有匹配的功能，试试“统一字典”“提现”或“试音”。</p>';
}
function openDirectory(){
  const groups=[...config.globals,...config.direction.domains];
  document.querySelector('#directory-content').innerHTML='<p class="empty" style="padding:0 0 16px;text-align:left">共 '+allFeatures.length+' 个页面，每个页面仅出现一次。实际菜单继续按原角色权限过滤。</p><div class="directory-grid">'+groups.map(domain=>{
    const centers=domain.centers||[{title:'全局入口',features:domain.features}];
    return '<section class="directory-domain"><h3>'+svg(domain.icon)+domain.title+'</h3>'+centers.map(center=>'<div class="directory-center"><h4>'+center.title+'</h4>'+center.features.map(([path,title])=>'<button class="directory-page" data-page="'+path+'">'+title+' <span class="feature-route">/'+path+'</span></button>').join('')+'</div>').join('')+'</section>';
  }).join('')+'</div>';
  document.querySelector('#directory-dialog').showModal();
}
document.addEventListener('click',event=>{
  const target=event.target.closest('button');if(!target)return;
  if(target.dataset.page){document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());navigate(target.dataset.page);return}
  if(target.dataset.domain){const id=target.dataset.domain;if(state.collapsed){navigate(getDomain(id).centers[0].features[0][0]);return}if(state.activeDomain===id&&state.openDomain===id){state.openDomain=null;renderNav();return}navigate(getDomain(id).centers[0].features[0][0]);return}
  if(target.dataset.store){state.store=target.dataset.store;render();return}
  switch(target.id){
    case 'store-trigger':{const menu=document.querySelector('#store-menu');menu.hidden=!menu.hidden;target.setAttribute('aria-expanded',String(!menu.hidden));break}
    case 'collapse':state.collapsed=!state.collapsed;renderNav();break;
    case 'mobile-toggle':state.mobileOpen=!state.mobileOpen;renderNav();break;
    case 'mobile-close':state.mobileOpen=false;renderNav();break;
    case 'search-open':openSearch();break;
    case 'directory-open':openDirectory();break;
    case 'search-close':document.querySelector('#search-dialog').close();break;
    case 'directory-close':document.querySelector('#directory-dialog').close();break;
  }
});
document.addEventListener('input',event=>{if(event.target.id==='search-input')renderSearch(event.target.value)});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();openSearch()}if(event.key==='Escape'&&state.mobileOpen){state.mobileOpen=false;renderNav()}});
render();
`
}

function prototype(direction) {
  const symbols = Object.entries(iconPaths).map(([name, paths]) => `<symbol id="icon-${name}" viewBox="0 0 24 24">${paths}</symbol>`).join('')
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${direction.name} · 管理后台导航方案 ${direction.key}</title>
<link rel="icon" href="${logos.platform}" type="image/svg+xml">
<style>${css}</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>${symbols}</defs></svg>
<div class="app direction-${direction.key}" id="app">
  <aside class="sidebar" aria-label="后台业务导航">
    <div class="brand"><img class="brand-logo" src="${logos.platform}" alt="电竞运营管理平台"><div class="brand-name"><strong>电竞管理系统</strong><span>多门店运营管理</span></div></div>
    <div class="store-area"><span class="field-label">当前管理门店</span><button class="store-trigger" id="store-trigger" aria-expanded="false" aria-controls="store-menu"><img class="store-logo" id="store-logo" src="${logos.shenglang}" alt=""><span class="store-name" id="store-trigger-label">声浪电竞</span>${icon('chevron')}</button><div class="store-menu" id="store-menu" hidden></div></div>
    <nav class="sidebar-nav" id="navigation" aria-label="一级业务域、二级中心与三级页面"></nav>
    <div class="sidebar-footer"><button class="icon-button collapse-button" id="collapse" aria-label="收起侧栏">${icon('panel')}<span class="collapse-label">收起侧栏</span></button><button class="icon-button" id="mobile-close" aria-label="关闭导航">${icon('close')}</button></div>
  </aside>
  <button class="scrim" id="mobile-close-scrim" aria-label="关闭导航" onclick="state.mobileOpen=false;renderNav()"></button>
  <div class="workspace">
    <header class="topbar"><button class="icon-button mobile-menu" id="mobile-toggle" aria-label="打开导航">${icon('menu')}</button><div class="breadcrumbs" id="breadcrumbs"></div><button class="search-trigger" id="search-open" aria-label="搜索后台功能">${icon('search')}<span>搜索功能</span><kbd>Ctrl K</kbd></button><div class="account" title="原型展示完整菜单，实际按角色权限过滤"><span class="account-dot">平台</span><span class="account-label">完整菜单预览</span></div></header>
    <main class="content" id="main-content">
      <div class="intro"><div><div class="direction-name">方案 ${direction.key} · ${direction.name}</div><h1>${direction.headline}</h1><p>${direction.summary}</p></div><button class="outline-button" id="directory-open">${icon('book')}查看完整目录</button></div>
      <div class="prototype-note">${icon('info')}<span><strong>信息架构选择稿。</strong>仅演示菜单组织与跳转，未改动生产页面。每个入口保留原路由；实际可见性继续由门店与角色权限决定。</span></div>
      <div id="domain-content"></div>
    </main>
  </div>
</div>
<dialog class="dialog" id="search-dialog" aria-labelledby="search-title"><div class="dialog-header"><h2 id="search-title">搜索后台功能</h2><button class="icon-button" id="search-close" aria-label="关闭搜索">${icon('close')}</button></div><div class="dialog-body"><input class="search-input" id="search-input" aria-label="搜索后台功能" placeholder="试试“统一字典”“提现”或“试音”" autocomplete="off"><div class="search-results" id="search-results"></div></div></dialog>
<dialog class="dialog" id="directory-dialog" aria-labelledby="directory-title"><div class="dialog-header"><h2 id="directory-title">方案 ${direction.key} · 完整三级目录</h2><button class="icon-button" id="directory-close" aria-label="关闭完整目录">${icon('close')}</button></div><div class="dialog-body" id="directory-content"></div></dialog>
<script>${runtime(direction)}</script>
</body></html>`
}
for (const direction of data.directions) fs.writeFileSync(path.join(root, `dir-${direction.key === 'A' ? 1 : direction.key === 'B' ? 2 : 3}.html`), prototype(direction))

const thumbFeature = (title, active = false) => `<div class="tf ${active ? 'on' : ''}">${title}</div>`
const thumbHead = (title, subtitle) => `<div class="thumb-head"><div><strong>${title}</strong><span>${subtitle}</span></div><span class="thumb-chip">45 个页面</span></div>`
const miniBrand = `<div class="mini-brand"><img src="${logos.platform}" alt=""><b>电竞管理系统</b></div>`
const cards = [
  { number: 1, title: '稳定业务域', label: '完整版 A', content: `<div class="mini-app">${miniBrand}<div class="mini-body"><div class="mini-side">${thumbFeature('运营工作台')}${thumbFeature('订单与履约', true)}<div class="mini-sub">预约与派单<br>预约管理　订单与派单<br>完成与评价<br>报单审核　订单评价</div>${thumbFeature('陪玩与招募')}${thumbFeature('老板与会员')}${thumbFeature('门店配置')}</div><div class="mini-main"><h3>按业务对象找功能</h3><p>日常履约和门店配置分开</p><div class="mini-lines"><b>订单与履约</b><span>预约管理</span><span>订单与派单</span><span>报单审核</span><span>订单评价</span></div></div></div></div>` },
  { number: 2, title: '岗位任务工作区', label: '完整版 C', content: `${thumbHead('一天的工作，按岗位连续完成', '不是按数据库表找页面')}<div class="task-grid"><div><b>客服与调度</b>${thumbFeature('会话接待')}${thumbFeature('订单与派单', true)}${thumbFeature('报单审核')}</div><div><b>陪玩运营</b>${thumbFeature('试音招募')}${thumbFeature('陪玩档案')}${thumbFeature('押金与结款规则')}</div><div><b>会员与活动</b>${thumbFeature('老板资料')}${thumbFeature('积分礼品与会员')}${thumbFeature('优惠券')}</div><div><b>财务与经营</b>${thumbFeature('钱包账户')}${thumbFeature('月度薪资核算')}${thumbFeature('经营 ROI')}</div></div>` },
  { number: 3, title: '对象与设置分家', label: '备选 · Shopify 公开模型', content: `<div class="mini-app">${miniBrand}<div class="object-layout"><div><small>核心业务</small>${thumbFeature('订单与履约', true)}${thumbFeature('陪玩与招募')}${thumbFeature('老板与会员')}${thumbFeature('资金与薪资')}</div><div><small>店铺设置</small>${thumbFeature('服务与玩法')}${thumbFeature('统一字典')}${thumbFeature('团队与权限')}${thumbFeature('安全与审计')}</div></div></div>` },
  { number: 4, title: '上下文先于页面', label: '备选 · Linear 界面层级', content: `${thumbHead('订单与履约', '预约与派单 / 订单与派单')}<div class="layer-demo"><div><span>定位</span><b>声浪电竞　›　订单与履约</b></div><div><span>中心</span><b>预约与派单　　完成与评价</b></div><div class="selected"><span>页面</span><b>订单与派单</b><small>保留 /orders</small></div><div><span>内容</span><b>真实业务页面保持原样</b></div></div>` },
  { number: 5, title: '大阶段任务路径', label: '备选 · 轮盘 Web 4', content: `${thumbHead('先完成当前阶段，再进入下一步', '游戏化的阶段反馈，不增加积分或指标')}<div class="big-stages"><div><small>预约</small><b>锁定档期</b><span>预约管理</span></div><div class="now"><small>接单</small><b>订单与派单</b><span>当前处理</span></div><div><small>结算</small><b>核对收益</b><span>资金流水</span></div></div>` },
  { number: 6, title: '服务生命周期', label: '完整版 B', content: `${thumbHead('从人员供给到经营复盘', '每个阶段都能看到前后关系')}<div class="chain"><b>招募与供给</b><span>→</span><b class="active">接单与履约</b><span>→</span><b>会员与增长</b><span>→</span><b>结算与经营</b></div><div class="chain-details"><div><small>上一阶段</small><b>入驻与建档</b><span>陪玩档案</span></div><div><small>当前阶段</small><b>预约与接单</b><span>预约管理 · 订单与派单</span></div><div><small>后续支撑</small><b>完成与售后</b><span>报单审核 · 订单评价</span></div></div>` },
]
const thumbCss = `
*{box-sizing:border-box}body{margin:0;background:#e9edf3;color:#0f172a;font-family:MiSans,system-ui,-apple-system,"Microsoft YaHei",sans-serif;font-size:14px}.sheet{padding:32px}.sheet-header{display:flex;justify-content:space-between;align-items:end;margin-bottom:24px}.sheet-header h1{font-size:28px;margin:0;letter-spacing:-.02em}.sheet-header p{margin:8px 0 0;color:#526078}.sheet-header span{font-size:13px;color:#526078}.grid{display:grid;grid-template-columns:repeat(3,480px);gap:24px}.card{background:#fff;border:1px solid #d5deea;border-radius:10px;overflow:hidden}.card-caption{padding:16px 20px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #e2e8f0}.card-caption b{font-size:16px}.card-caption span{margin-left:auto;font-size:12px;color:#526078}.stage{height:300px;padding:20px;background:#f9fbfd}.mini-app{height:100%;border:1px solid #d6e0ed;border-radius:8px;overflow:hidden;background:#fff}.mini-brand{display:flex;align-items:center;gap:8px;height:44px;padding:8px 12px;border-bottom:1px solid #e4eaf2;font-size:12px}.mini-brand img{width:24px;height:24px}.mini-body{display:grid;grid-template-columns:180px 1fr;height:calc(100% - 44px)}.mini-side{padding:8px;background:#f7f9fc;border-right:1px solid #e1e8f1;font-size:12px}.tf{padding:7px 8px;min-height:28px;border-radius:4px;font-size:12px;line-height:1.4}.tf.on{background:#e6efff;color:#1d4ed8;font-weight:600}.mini-sub{margin:4px 0 8px 12px;padding-left:8px;border-left:1px solid #cdd9e8;color:#53647b;line-height:1.7}.mini-main{padding:16px}.mini-main h3{font-size:18px;margin:0}.mini-main p{font-size:12px;color:#526078}.mini-lines{margin-top:20px;display:grid;grid-template-columns:1fr 1fr;gap:8px}.mini-lines b{grid-column:1/-1;font-size:13px}.mini-lines span{padding:10px 8px;border:1px solid #dce4ef;border-radius:4px;font-size:12px}.thumb-head{display:flex;align-items:start;justify-content:space-between;gap:12px;margin-bottom:20px}.thumb-head strong{font-size:16px;display:block;line-height:1.5}.thumb-head span:not(.thumb-chip){display:block;color:#526078;font-size:12px;margin-top:8px}.thumb-chip{font-size:12px;color:#526078;white-space:nowrap}.task-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.task-grid>div{border:1px solid #d7e2f0;padding:8px;border-radius:6px;background:#fff}.task-grid b{display:block;font-size:13px;margin:0 0 6px}.object-layout{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px}.object-layout small{display:block;font-size:12px;color:#526078;padding:0 8px 8px}.layer-demo{border-top:1px solid #d8e1ee}.layer-demo>div{display:flex;align-items:center;gap:16px;padding:14px 12px;border-bottom:1px solid #d8e1ee;font-size:12px}.layer-demo span{width:36px;color:#526078}.layer-demo b{font-size:13px}.layer-demo small{margin-left:auto}.layer-demo .selected{background:#eaf1ff;border-left:3px solid #2563eb}.big-stages{display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:8px;align-items:center;height:176px}.big-stages>div{padding:16px 12px;background:#fff;border:1px solid #d6e0ed;border-radius:12px;box-shadow:0 4px 0 #d4dfec}.big-stages .now{background:#2563eb;color:#fff;border-color:#1d4ed8;box-shadow:0 4px 0 #173c92;transform:translateY(-8px)}.big-stages small,.big-stages span{display:block;font-size:12px}.big-stages b{display:block;font-size:16px;margin:12px 0}.big-stages .now small,.big-stages .now span{color:#e0ebff}.chain{display:flex;align-items:center;gap:8px;font-size:12px}.chain b{padding:10px 8px;border:1px solid #d6e0ed;border-radius:4px;background:#fff}.chain b.active{background:#eaf1ff;border-color:#9db9ed;color:#1d4ed8}.chain-details{display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:12px;margin-top:28px}.chain-details>div{padding:12px;border-top:1px solid #9fb1ca}.chain-details small,.chain-details span{display:block;font-size:12px;color:#526078}.chain-details b{display:block;font-size:13px;margin:12px 0}.links{display:flex;gap:16px;margin-top:24px}.links a{color:#1d4ed8;font-size:14px}
`
const thumbs = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>管理后台信息架构 · 六个候选</title><link rel="icon" href="${logos.platform}" type="image/svg+xml"><style>${thumbCss}</style></head><body><main class="sheet"><header class="sheet-header"><div><h1>后台菜单重排 · 六个候选</h1><p>同一套真实页面，不同的信息组织方式；不改动业务与权限。</p></div><span>已选 A、B、C 制作完整版</span></header><div class="grid">${cards.map(card => `<section class="card"><div class="card-caption"><b>候选 ${card.number} · ${card.title}</b><span>${card.label}</span></div><div class="stage">${card.content}</div></section>`).join('')}</div><nav class="links"><a href="dir-1.html">打开方案 A</a><a href="dir-2.html">打开方案 B</a><a href="dir-3.html">打开方案 C</a></nav></main></body></html>`
fs.writeFileSync(path.join(root, 'thumbs.html'), thumbs)
console.log(JSON.stringify({ generated: ['thumbs.html', 'dir-1.html', 'dir-2.html', 'dir-3.html'], directions: data.directions.map(direction => ({ key: direction.key, name: direction.name, domains: direction.domains.length, centers: direction.domains.reduce((total, domain) => total + domain.centers.length, 0), uniquePages: 45 })) }, null, 2))
