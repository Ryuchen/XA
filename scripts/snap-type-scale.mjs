#!/usr/bin/env node
/**
 * 字阶收敛：把散落的 34 种 font-size 吸附到 7 级字阶上。
 *
 * 阶梯（rpx @750）  24 / 26 / 28 / 30 / 34 / 40 / 48  =  12 / 13 / 14 / 15 / 17 / 20 / 24 px
 * 规矩：底下不破 12px（真机可读性与对比度底线），上面按最近档吸附。
 *
 * 用法：node scripts/snap-type-scale.mjs [--dry]
 */
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const NATIVE = resolve(process.cwd(), 'apps/boss-miniapp/native');
const DRY = process.argv.includes('--dry');

const RPX = [[24, 24], [27, 26], [29, 28], [32, 30], [36, 34], [42, 40], [Infinity, 48]];
const PX = [[12, 12], [13, 13], [16, 15], [19, 17], [22, 20], [Infinity, 24]];
const snap = (v, table) => table.find(([max]) => v <= max)[1];

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith('.wxss')) out.push(p);
  }
  return out;
}

const changes = [];
for (const file of walk(NATIVE)) {
  const src = readFileSync(file, 'utf8');
  const next = src.replace(/font-size:\s*(-?[\d.]+)(rpx|px)/g, (m, raw, unit) => {
    const v = parseFloat(raw);
    const to = snap(v, unit === 'rpx' ? RPX : PX);
    if (to === v) return m;
    changes.push({ file: file.replace(NATIVE + '\\', ''), from: `${raw}${unit}`, to: `${to}${unit}` });
    return `font-size:${to}${unit}`;
  });
  if (next !== src && !DRY) writeFileSync(file, next, 'utf8');
}

const tally = {};
for (const c of changes) {
  const k = `${c.from} → ${c.to}`;
  tally[k] = (tally[k] || 0) + 1;
}
console.log(DRY ? '— 预演，未写入 —' : '— 已写入 —');
console.log(`改动 ${changes.length} 处，涉及 ${new Set(changes.map((c) => c.file)).size} 个样式表\n`);
Object.entries(tally)
  .sort((a, b) => b[1] - a[1])
  .forEach(([k, n]) => console.log(`  ${k.padEnd(18)} ${String(n).padStart(3)} 处`));
