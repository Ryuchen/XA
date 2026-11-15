// Snap native WXSS component spacing to the shared 4pt token scale.
// Only margin / padding / gap declarations are changed; dimensions, borders,
// typography and generated theme.wxss are intentionally left untouched.
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '../apps/boss-miniapp/native');
const WRITE = process.argv.includes('--write');
const CHECK = process.argv.includes('--check');
const tokens = [8, 16, 24, 32, 48, 64, 80, 112];
const property = /((?:^|[;{])\s*(?:margin(?:-(?:top|right|bottom|left))?|padding(?:-(?:top|right|bottom|left))?|gap|row-gap|column-gap)\s*:\s*)([^;{}]+)(;?)/gm;
const files = [];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(file);
    else if (entry.name.endsWith('.wxss') && entry.name !== 'theme.wxss') files.push(file);
  }
}
function token(value) {
  const nearest = tokens.reduce((best, item) => Math.abs(item - value) < Math.abs(best - value) ? item : best, tokens[0]);
  return `var(--xa-s-${tokens.indexOf(nearest) + 1})`;
}
walk(ROOT);
let changedFiles = 0;
let replacements = 0;
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  const output = source.replace(property, (full, prefix, value, suffix) => {
    const next = value.replace(/(?<![\w-])(\d*\.?\d+)(rpx|px)\b/g, (raw, number, unit) => {
      const n = Number(number) * (unit === 'px' ? 2 : 1);
      if (!n) return raw;
      replacements += 1;
      return token(n);
    });
    return prefix + next + suffix;
  });
  if (output !== source) {
    changedFiles += 1;
    if (WRITE) fs.writeFileSync(file, output);
    console.log(path.relative(ROOT, file));
  }
}
console.log(`${WRITE ? 'Updated' : 'Would update'} ${changedFiles} stylesheets; ${replacements} spacing values.`);
if (CHECK && changedFiles) {
  console.error('Component spacing must use --xa-s-* tokens. Run node scripts/snap-spacing-scale.mjs --write.');
  process.exitCode = 1;
}
