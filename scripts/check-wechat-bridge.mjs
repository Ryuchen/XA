import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = path.join(repo, 'fronted/wechat-bridge')
const root = path.join(projectRoot, 'native')
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
const project = JSON.parse(fs.readFileSync(path.join(projectRoot, 'project.config.json'), 'utf8'))
if (JSON.stringify(manifest.pages) !== JSON.stringify(['pages/web-login/index']) || manifest.tabBar) throw new Error('The bridge must contain exactly the authorization page and no business navigation')
if (project.miniprogramRoot !== 'native/' || project.compileType !== 'miniprogram') throw new Error('Invalid bridge release root')
function walk(dir) { return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? walk(path.join(dir, entry.name)) : [path.join(dir, entry.name)]) }
const files = walk(root)
for (const ext of ['js', 'json', 'wxml', 'wxss']) if (!fs.existsSync(path.join(root, `pages/web-login/index.${ext}`))) throw new Error(`Missing bridge page ${ext}`)
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8')
  if (file.endsWith('.json')) JSON.parse(source)
  if (!file.endsWith('.js')) continue
  new vm.Script(source, { filename: path.relative(root, file) })
  if (/wx\.(?:setStorage|setStorageSync|requestPayment|connectSocket)\s*\(/.test(source)) throw new Error(`Business session/payment/socket capability in the bridge: ${file}`)
  for (const [, dependency] of source.matchAll(/require\(['"]([^'"]+)['"]\)/g)) {
    const target = path.resolve(path.dirname(file), dependency + '.js')
    const relative = path.relative(root, target)
    if (!dependency.startsWith('.') || relative.startsWith('..') || !fs.existsSync(target)) throw new Error(`Bridge dependency leaves its minimal package: ${dependency}`)
  }
}
const compilerDir = process.env.WECHAT_COMPILER_DIR || 'C:/Program Files (x86)/Tencent/微信web开发者工具/resources/app.asar.unpacked/node_modules/wcc-exec'
const output = path.join(repo, '.tools/bridge-verification')
fs.mkdirSync(output, { recursive: true })
for (const [name, ext] of [['wcc', 'wxml'], ['wcsc', 'wxss']]) {
  const compiler = path.join(compilerDir, name + (process.platform === 'win32' ? '.exe' : ''))
  if (!fs.existsSync(compiler)) {
    if (process.argv.includes('--require-compiler')) throw new Error(`Official WeChat compiler missing: ${name}`)
    console.log(`Official ${name} compiler unavailable; template compile was not verified`)
    continue
  }
  const inputs = files.filter(file => file.endsWith('.' + ext)).map(file => './' + path.relative(root, file).replaceAll('\\', '/'))
  const result = spawnSync(compiler, ['-o', path.join(output, `${name}.js`), ...inputs], { cwd: root, encoding: 'utf8', windowsHide: true })
  if (result.status !== 0) throw new Error(result.stderr || result.stdout || String(result.error))
  console.log(`Official WeChat ${ext}: ${inputs.length} passed`)
}
console.log(`Standalone authorization bridge: 1 page, ${files.reduce((sum, file) => sum + fs.statSync(file).size, 0)} bytes, no business pages or stored JWT`)
