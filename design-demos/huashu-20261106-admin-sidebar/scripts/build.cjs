const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')

const workspace = process.cwd()
const root = path.join(workspace, 'design-demos/huashu-20261106-admin-sidebar')
const adminRequire = createRequire(path.join(workspace, 'fronted/admin/package.json'))
const React = adminRequire('react')
const { renderToStaticMarkup } = adminRequire('react-dom/server')
const Radix = adminRequire('@radix-ui/react-icons')
const ts = adminRequire('typescript')
const source = fs.readFileSync(path.join(workspace, 'fronted/admin/src/lib/navigation.ts'), 'utf8')
const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context)
const navigation = context.exports.navigationDomains
const iconNames = ['DashboardIcon', 'FileTextIcon', 'PersonIcon', 'IdCardIcon', 'CardStackIcon', 'ChatBubbleIcon', 'BackpackIcon', 'GearIcon', 'HomeIcon', 'ChevronDownIcon', 'ChevronRightIcon', 'PinLeftIcon', 'PinRightIcon', 'DrawingPinIcon', 'DrawingPinFilledIcon', 'MagnifyingGlassIcon', 'CheckIcon']
const icons = Object.fromEntries(iconNames.map(name => {
  if (!Radix[name]) throw new Error(`Missing Radix icon: ${name}`)
  return [name, renderToStaticMarkup(React.createElement(Radix[name], { width: 16, height: 16, 'aria-hidden': true }))]
}))
const svgData = name => 'data:image/svg+xml;base64,' + fs.readFileSync(path.join(workspace, 'fronted/admin/public', name + '.svg')).toString('base64')
const brands = {
  platform: { slug: 'platform', name: '电竞管理系统', logo: svgData('platform') },
  shenglang: { slug: 'shenglang', name: '声浪电竞', currency: '浪花', logo: svgData('brand') },
  xinan: { slug: 'xinan', name: '兴安电竞', currency: '星辉', logo: svgData('xinan') },
}
const css = fs.readFileSync(path.join(root, 'src/prototype.css'), 'utf8')
const js = fs.readFileSync(path.join(root, 'src/prototype.js'), 'utf8')
const sharedData = `<script>window.XA_BRANDS=${JSON.stringify(brands)};window.XA_NAVIGATION=${JSON.stringify(navigation)};window.XA_ICONS=${JSON.stringify(icons)};</script><script>${js}</script>`
const html = (title, body, boot = '') => `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><style>${css}</style></head><body>${body}${sharedData}<script>${boot}</script></body></html>`

const mode = process.argv[2] || 'thumbs'
if (mode === 'thumbs') {
  const variants = ['A', 'B', 'C', 'D', 'E', 'F']
  const body = `<main class="thumb-board">${variants.map(variant => `<section class="thumb-card"><header><strong>${variant} · ${{ A: '任务层级', B: '门店工作簿', C: '悬浮导航台', D: '深色业务树', E: '业务索引', F: '双栏界栏' }[variant]}</strong><span>${['最佳设计师', '最佳设计师', '现实参照', '现实参照', '轮盘网页 01', '轮盘东亚 02'][variants.indexOf(variant)]}</span></header><div id="thumb-${variant}"></div></section>`).join('')}</main>`
  const boot = variants.map(variant => `SidebarPrototype.mount(document.getElementById('thumb-${variant}'),'${variant}',{mini:true});`).join('')
  fs.writeFileSync(path.join(root, 'thumbs.html'), html('侧边栏六格候选', body, boot))
  console.log(`Built six thumbnails using ${navigation.length} real navigation domains and ${iconNames.length} Radix icons.`)
} else if (mode === 'full') {
  const variants = (process.argv[3] || 'C,D,F').split(',')
  for (const variant of variants) {
    fs.writeFileSync(path.join(root, `dir-${variant}.html`), html(`方案 ${variant} · 管理后台侧边栏`, '<div id="prototype"></div>', `SidebarPrototype.mount(document.getElementById('prototype'),'${variant}');`))
  }
  console.log(`Built full directions: ${variants.join(', ')}`)
} else {
  throw new Error('Usage: node scripts/build.cjs thumbs|full [C,D,F]')
}
