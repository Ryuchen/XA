import { readdir, readFile } from 'node:fs/promises'
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

await Promise.all(styleSourceRoots.map(visitStyle))
await Promise.all(moneySourceRoots.map(visitMoney))

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
