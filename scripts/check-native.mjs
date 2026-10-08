import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { auditNativeResources } from './native-resource-budget.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.join(repo, 'apps/boss-miniapp/native');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
const errors = [];
const project = JSON.parse(fs.readFileSync(path.join(repo, 'apps/boss-miniapp/project.config.json'), 'utf8'));
if (project.miniprogramRoot !== 'native/' || project.compileType !== 'miniprogram') {
  errors.push('WeChat project must load the native/ miniprogram; legacy dist/ is not the release artifact.');
}
function walk(directory) { return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? walk(path.join(directory, entry.name)) : [path.join(directory, entry.name)]); }
for (const route of manifest.pages) {
  for (const ext of ['js', 'json', 'wxml', 'wxss']) if (!fs.existsSync(path.join(root, route + '.' + ext))) errors.push(`Missing ${route}.${ext}`);
}
const files = walk(root);
const mediaBudget = auditNativeResources(root);
if (!mediaBudget.passed) errors.push(`Packaged images/audio exceed the combined 200KB budget: ${mediaBudget.totalBytes} bytes. Move large artwork/audio to the approved HTTPS asset origin.`);
for (const file of files) {
  const relative = path.relative(root, file);
  if (/\.(js|json|wxml|wxss)$/.test(file)) {
    const text = fs.readFileSync(file, 'utf8');
    try {
      if (file.endsWith('.js')) {
        new vm.Script(text, { filename: relative });
        for (const [, dependency] of text.matchAll(/require\(['"]([^'"]+)['"]\)/g)) {
          if (!dependency.startsWith('.')) errors.push(`${relative}: non-native dependency ${dependency}`);
          else if (!fs.existsSync(path.resolve(path.dirname(file), dependency + '.js'))) errors.push(`${relative}: missing ${dependency}.js`);
        }
      }
      if (file.endsWith('.json')) JSON.parse(text);
      if (file.endsWith('.wxml')) {
        if (/wx:(?:if|elif|for)="(?!{{)/.test(text)) errors.push(`${relative}: literal wx condition/list binding`);
        if (/<(?:div|span|br|img)\b/.test(text)) errors.push(`${relative}: HTML element in native template`);
      }
    } catch (error) { errors.push(`${relative}: ${error.message}`); }
  }
}
for (const tab of manifest.tabBar.list) {
  if (!manifest.pages.includes(tab.pagePath)) errors.push(`Unregistered tab ${tab.pagePath}`);
  for (const icon of [tab.iconPath, tab.selectedIconPath]) if (icon && !fs.existsSync(path.join(root, icon))) errors.push(`Missing tab icon ${icon}`);
}
const totalBytes = files.reduce((size, file) => size + fs.statSync(file).size, 0);
if (totalBytes > 2 * 1024 * 1024) errors.push(`Main package exceeds 2MB: ${totalBytes} bytes`);
if (errors.length) { console.error(errors.join('\n')); process.exit(1); }
console.log(`Native structure and JS: ${manifest.pages.length} pages, ${(totalBytes / 1024).toFixed(1)} KB`);
console.log(`Packaged images/audio: ${mediaBudget.files.length} files, ${(mediaBudget.totalBytes / 1024).toFixed(1)} KiB (${mediaBudget.totalBytes} bytes); combined budget ${mediaBudget.limitBytes} bytes`);

const compilerDirectory = process.env.WECHAT_COMPILER_DIR || 'C:/Program Files (x86)/Tencent/微信web开发者工具/resources/app.asar.unpacked/node_modules/wcc-exec';
const compiler = path.join(compilerDirectory, process.platform === 'win32' ? 'wcc.exe' : 'wcc');
if (fs.existsSync(compiler)) {
  const outputDir = path.join(repo, '.tools/native-verification');
  fs.mkdirSync(outputDir, { recursive: true });
  const templates = files.filter(file => file.endsWith('.wxml')).map(file => './' + path.relative(root, file).replaceAll('\\', '/'));
  const output = path.join(outputDir, 'compiled-wxml.js');
  const result = spawnSync(compiler, ['-o', output, ...templates], { cwd: root, encoding: 'utf8', windowsHide: true });
  if (result.status !== 0) { console.error(result.stderr || result.stdout || result.error); process.exit(1); }
  console.log(`WeChat WXML compiler: ${templates.length} templates passed`);
  const stylesheets = files.filter(file => file.endsWith('.wxss')).map(file => './' + path.relative(root, file).replaceAll('\\', '/'));
  const styleCompiler = path.join(compilerDirectory, process.platform === 'win32' ? 'wcsc.exe' : 'wcsc');
  const styleResult = spawnSync(styleCompiler, ['-o', path.join(outputDir, 'compiled-wxss.js'), ...stylesheets], { cwd: root, encoding: 'utf8', windowsHide: true });
  if (styleResult.status !== 0) { console.error(styleResult.stderr || styleResult.stdout || styleResult.error); process.exit(1); }
  console.log(`WeChat WXSS compiler: ${stylesheets.length} stylesheets passed`);
} else if (process.argv.includes('--require-compiler')) {
  console.error('Official WeChat compiler is required. Set WECHAT_COMPILER_DIR to its installed directory.');
  process.exitCode = 1;
} else {
  console.log('Official WeChat compiler not found; run in Developer Tools before release.');
}
