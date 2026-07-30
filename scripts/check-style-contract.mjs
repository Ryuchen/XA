import { readdir, readFile } from 'node:fs/promises'
import { extname, join, relative, resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const sourceRoots = [
  resolve(root, 'apps/boss-miniapp/src'),
  resolve(root, 'apps/provider-miniapp/src'),
  resolve(root, 'apps/admin-web/src'),
]
const supportedExtensions = new Set(['.css', '.scss', '.vue'])
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

async function visit(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) {
      await visit(path)
      continue
    }
    if (!supportedExtensions.has(extname(entry.name))) continue
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

await Promise.all(sourceRoots.map(visit))

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
  console.log('样式契约检查通过：三端基础色和 Token 来源统一。')
}
