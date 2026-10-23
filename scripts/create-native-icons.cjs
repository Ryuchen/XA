// Rasterize official Tabler MIT-licensed icons for the native tab bar and UI.
// Run with SHARP_MODULE pointing at an installed sharp package. No app dependency.
// Use --tabbar-only to leave existing in-page UI icons unchanged.
// Use --favorite-only to regenerate just the selected favorite heart.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require(process.env.SHARP_MODULE || 'sharp');
const version = 'v3.31.0';
const manifest = require('../apps/boss-miniapp/native/app.json');
const sourceDirectory = path.resolve(__dirname, 'icon-sources/tabler', version);
const icons = { home: 'home', catalog: 'category', companions: 'headphones', orders: 'clipboard-list', messages: 'message-circle', mine: 'user-circle' };
const uiIcons = { headphones: 'headphones', ticket: 'ticket', trophy: 'trophy', support: 'headset', heart: 'heart', calendar: 'calendar-check', award: 'award', profile: 'user-circle', shield: 'shield-check', bell: 'bell', chat: 'messages', search: 'search', filter: 'adjustments-horizontal', chevron: 'chevron-right' };

async function readOfficialSource(icon) {
  const file = path.join(sourceDirectory, icon + '.svg');
  try {
    return await fs.readFile(file, 'utf8');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const response = await fetch('https://raw.githubusercontent.com/tabler/tabler-icons/' + version + '/icons/outline/' + icon + '.svg');
  if (!response.ok) throw new Error('Could not retrieve official Tabler icon: ' + icon);
  const source = await response.text();
  if (!source.includes('<svg') || !source.includes('stroke="currentColor"')) {
    throw new Error('Unexpected official Tabler SVG: ' + icon);
  }
  await fs.mkdir(sourceDirectory, { recursive: true });
  await fs.writeFile(file, source);
  return source;
}

async function writeFavoriteIcon() {
  const nativeRoot = path.resolve(__dirname, '../apps/boss-miniapp/native');
  const theme = await fs.readFile(path.join(nativeRoot, 'theme.wxss'), 'utf8');
  const color = theme.match(/--xa-color-favorite:\s*(#[\da-f]{6})\s*;/i)?.[1];
  if (!color) throw new Error('Favorite theme color is missing; run sync-native-theme.mjs first');
  const source = (await readOfficialSource('heart')).replace('fill="none"', 'fill="' + color + '"').replaceAll('currentColor', color);
  const directory = path.join(nativeRoot, 'assets/icons');
  await fs.mkdir(directory, { recursive: true });
  await sharp(Buffer.from(source)).resize(72, 72).png({ palette: true, compressionLevel: 9 }).toFile(path.join(directory, 'heart-active.png'));
}

async function main() {
  if (process.argv.includes('--favorite-only')) {
    await writeFavoriteIcon();
    console.log('Created selected favorite heart');
    return;
  }
  const directory = path.resolve(__dirname, '../apps/boss-miniapp/native/assets/tabbar');
  await fs.mkdir(directory, { recursive: true });
  for (const [name, icon] of Object.entries(icons)) {
    const svg = await readOfficialSource(icon);
    for (const [suffix, color] of [['', manifest.tabBar.color], ['-active', manifest.tabBar.selectedColor]]) {
      const source = svg.replaceAll('currentColor', color).replace('stroke-width="2"', 'stroke-width="1.75"');
      // Optical padding reduces the glyph, without changing the native tab hit area.
      await sharp(Buffer.from(source))
        .resize(60, 60)
        .extend({ top: 6, bottom: 6, left: 6, right: 6, background: { r: 0, g: 0, b: 0, alpha: 0 } })
        .png().toFile(path.join(directory, name + suffix + '.png'));
    }
  }
  if (process.argv.includes('--tabbar-only')) {
    console.log('Created ' + Object.keys(icons).length * 2 + ' tab bar icons; UI icons unchanged');
    return;
  }
  const uiDirectory = path.resolve(directory, '../icons');
  await fs.mkdir(uiDirectory, { recursive: true });
  for (const [name, icon] of Object.entries(uiIcons)) {
    const source = (await readOfficialSource(icon)).replaceAll('currentColor', manifest.tabBar.selectedColor);
    await sharp(Buffer.from(source)).resize(72, 72).png().toFile(path.join(uiDirectory, name + '.png'));
  }
  await writeFavoriteIcon();
  console.log('Created ' + Object.keys(icons).length * 2 + ' tab bar and ' + (Object.keys(uiIcons).length + 1) + ' UI icons');
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
