import { access, readdir, readFile } from 'node:fs/promises'
import { extname, join, relative, resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')

// ---------- 颜色 / Token 检查（保持原行为，覆盖全部三端） ----------
const styleSourceRoots = [
  resolve(root, 'apps/boss-miniapp/src'),
  resolve(root, 'apps/provider-miniapp/src'),
  resolve(root, 'apps/admin-web/src'),
]
const styleExtensions = new Set(['.css', '.scss', '.vue'])
const forbiddenPalette = [
  '#007aff',
  '#0a84ff',
  '#34c759',
  '#30d158',
  '#ff9500',
  '#ff9f0a',
  '#ff3b30',
  '#ff453a',
  '#f2f2f7',
  '#e5e5ea',
  '#8e8e93',
  '#1d1d1f',
]
const violations = []

async function visitStyle(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) {
      await visitStyle(path)
      continue
    }
    if (!styleExtensions.has(extname(entry.name))) continue
    const content = await readFile(path, 'utf8')
    const lines = content.split(/\r?\n/)
    lines.forEach((line, index) => {
      const normalized = line.toLowerCase()
      for (const color of forbiddenPalette) {
        if (normalized.includes(color)) {
          violations.push(`${relative(root, path)}:${index + 1} 禁止硬编码基础色 ${color}`)
        }
      }
      if (/--c-[\w-]+\s*:/.test(line)) {
        violations.push(`${relative(root, path)}:${index + 1} 旧 --c-* Token 只能在设计系统适配层定义`)
      }
    })
  }
}

// ---------- 金额换算检查（B2 新增；仅覆盖可被修复的两前端） ----------
// 不在 boss-miniapp 上执行：boss 自带 money.ts，且含 URL / 超时 *1000 / 注释等
// 伪命中；而 boss 当前 54 个在途文件不可改动，无法修复此类“违规”。
const moneySourceRoots = [
  resolve(root, 'apps/provider-miniapp/src'),
  resolve(root, 'apps/admin-web/src'),
]
const moneyExtensions = new Set(['.ts', '.tsx', '.vue'])

// 合法百分比计算（非单位换算），显式豁免。
const moneyExemptLines = new Set([
  'apps/admin-web/src/views/report/index.vue:235',
  'apps/admin-web/src/views/order/index.vue:692',
  'apps/admin-web/src/views/dashboard/index.vue:185',
])

// 裸的单位换算：/10 /100 *10 *100（带词边界，避免误伤 *1000 等）。
// 例外：packages/money 是换算的唯一真理来源，由该包集中承载，不在此检查。
const moneyPattern = /[*/]\s*(?:10|100)\b/
const moneyRoot = resolve(root, 'packages/money')

// 仅取 .vue 的 <script> 段，避免把 <style> 里的 100% / 0.5 等误判为金额换算。
function scriptLines(content, ext) {
  if (ext !== '.vue') {
    return content.split(/\r?\n/).map((text, i) => ({ line: i + 1, text }))
  }
  const out = []
  const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi
  let m
  while ((m = re.exec(content)) !== null) {
    const start = content.slice(0, m.index).split(/\r?\n/).length
    m[1].split(/\r?\n/).forEach((text, i) => out.push({ line: start + i, text }))
  }
  return out
}

async function visitMoney(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) {
      await visitMoney(path)
      continue
    }
    if (!moneyExtensions.has(extname(entry.name))) continue
    if (path.startsWith(moneyRoot)) continue
    const content = await readFile(path, 'utf8')
    const rel = relative(root, path)
    for (const { line, text } of scriptLines(content, extname(entry.name))) {
      const key = `${rel}:${line}`
      if (moneyExemptLines.has(key)) continue
      // 去掉行内注释，避免把说明文字误判为换算。
      const code = text.split('//')[0]
      if (moneyPattern.test(code)) {
        violations.push(
          `${rel}:${line} 禁止裸金额换算（/10 /100 *10 *100），请改用 @xa/money 的 toCoin / toRawAmount / formatXaCoin`,
        )
      }
    }
  }
}

// ---------- @xa/money 可解析性检查 ----------
// 上面的金额规则会提示「请改用 @xa/money」。如果这个包名只是装饰性的（没有任何
// 构建器能解析它），开发者照提示修改后会直接构建失败。这里把「提示里的写法必须
// 真的能用」固化成契约：包产物齐全，且两个消费方都声明了别名。
async function exists(path) {
  try {
    await access(path)
    return true
  } catch {
    return false
  }
}

async function checkMoneyPackageResolvable() {
  // 1) 运行时入口必须是不带类型注解的 .js —— Taro 的 TS/babel loader 只覆盖各 app
  //    的 sourceRoot，包目录下的 .ts 会撞上没有 loader 的 webpack（ModuleParseError）。
  const runtimeEntry = resolve(moneyRoot, 'src/index.js')
  const typesEntry = resolve(moneyRoot, 'src/index.d.ts')
  if (!(await exists(runtimeEntry))) {
    violations.push('packages/money/src/index.js 缺失：共享金额包的运行时入口必须是纯 JS')
  }
  if (!(await exists(typesEntry))) {
    violations.push('packages/money/src/index.d.ts 缺失：共享金额包必须手写类型声明')
  }
  if (await exists(resolve(moneyRoot, 'src/index.ts'))) {
    violations.push(
      'packages/money/src/index.ts 不允许存在：Taro webpack 无法解析 sourceRoot 之外的 .ts 源码',
    )
  }

  // 2) 两个消费方都必须能把 `@xa/money` 解析到该包（构建器别名 + tsconfig paths）。
  const aliasSites = [
    ['apps/provider-miniapp/config/index.ts', "'@xa/money'"],
    ['apps/provider-miniapp/tsconfig.json', '"@xa/money"'],
    ['apps/admin-web/vite.config.ts', "'@xa/money'"],
    ['apps/admin-web/tsconfig.json', '"@xa/money"'],
  ]
  for (const [file, needle] of aliasSites) {
    const content = await readFile(resolve(root, file), 'utf8')
    if (!content.includes(needle)) {
      violations.push(`${file} 必须声明 @xa/money 别名，否则金额规则的提示无法照做`)
    }
  }
}

await Promise.all(styleSourceRoots.map(visitStyle))
await Promise.all(moneySourceRoots.map(visitMoney))
await checkMoneyPackageResolvable()

// design-system 转发检查（保持原行为）
const expectedEntrypoint = "@forward '../../../../packages/design-system/src/miniapp';"
for (const app of ['boss-miniapp', 'provider-miniapp']) {
  const entry = resolve(root, `apps/${app}/src/styles/variables.scss`)
  const content = await readFile(entry, 'utf8')
  if (!content.includes(expectedEntrypoint)) {
    violations.push(`${relative(root, entry)} 必须转发共享小程序设计系统`)
  }
}

if (violations.length) {
  console.error(`样式契约检查失败（${violations.length} 项）：`)
  violations.forEach((item) => console.error(`- ${item}`))
  process.exitCode = 1
} else {
  console.log('样式契约检查通过：三端基础色、Token 来源与金额换算口径统一。')
}
