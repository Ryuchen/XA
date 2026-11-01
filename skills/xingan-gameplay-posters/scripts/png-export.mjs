import fs from 'node:fs/promises';

// Dimensions are physical width in millimetres and actual raster PPI. The
// layout/SVG remains unchanged; only the delivered PNG is rasterised smaller.
export async function exportPosterPng(sharp, svg, page, outputPath) {
  const pointWidth = page.layoutUnit === 'pt' ? Number(page.w) : null;
  const widthMm = Number(page.printWidthMm ?? (pointWidth === null ? 200 : pointWidth * 25.4 / 72));
  const requestedPpi = Number(page.printPpi ?? 120);
  const sourceWidth = Number(page.ew);
  if (![widthMm, requestedPpi, sourceWidth].every(v => Number.isFinite(v) && v > 0))
    throw new Error('海报物理宽度、PPI 和 SVG 导出宽度须为正数。');
  if (pointWidth !== null && (!Number.isFinite(pointWidth) || pointWidth <= 0 || Math.abs(pointWidth - widthMm * 72 / 25.4) > 0.1))
    throw new Error('pt 布局宽度须与实际物理宽度一致（1 英寸 = 72 pt）。');
  const maxBytes = 4_000_000;
  let width = Math.max(1, Math.round(widthMm / 25.4 * requestedPpi));
  for (let attempt = 0; attempt < 12; attempt++) {
    const actualPpi = width / (widthMm / 25.4);
    const inputDensity = Math.max(1, 72 * width / sourceWidth);
    const buffer = await sharp(Buffer.from(svg), { density: inputDensity, limitInputPixels: false })
      .resize({ width }).withMetadata({ density: actualPpi })
      .png({ compressionLevel: 9, adaptiveFiltering: true }).toBuffer();
    if (buffer.length <= maxBytes) {
      const metadata = await sharp(buffer).metadata();
      await fs.writeFile(outputPath, buffer);
      return { widthMm, requestedPpi, actualPpi, widthPx: metadata.width, heightPx: metadata.height, bytes: buffer.length };
    }
    const next = Math.floor(width * Math.min(0.9, Math.sqrt(maxBytes / buffer.length) * 0.95));
    if (next < 1 || next >= width) break;
    width = next;
  }
  throw new Error('PNG 在可用尝试次数内仍超过 4 MB，请调整物理尺寸、PPI 或内容后重试。');
}
