const MAX_IMAGE_BYTES = 200000;
function fileSize(filePath) {
  return new Promise((resolve, reject) => wx.getFileSystemManager().getFileInfo({
    filePath,
    success: result => Number.isFinite(result.size) && result.size > 0 ? resolve(result.size) : reject(new Error('无法读取图片大小，请重新选择')),
    fail: () => reject(new Error('无法读取图片，请重新选择')),
  }));
}
async function prepareImage(filePath) {
  if (await fileSize(filePath) <= MAX_IMAGE_BYTES) return filePath;
  const info = await new Promise((resolve, reject) => wx.getImageInfo({
    src: filePath, success: resolve, fail: () => reject(new Error('图片格式无效，请重新选择')),
  }));
  if (!(info.width > 0 && info.height > 0)) throw new Error('无法读取图片尺寸，请重新选择');
  for (const edge of [1600, 1200, 900, 640, 420, 280]) {
    const scale = Math.min(1, edge / Math.max(info.width, info.height));
    for (const quality of [85, 65, 45]) {
      const result = await new Promise((resolve, reject) => wx.compressImage({
        src: filePath, quality,
        compressedWidth: Math.max(1, Math.round(info.width * scale)),
        compressedHeight: Math.max(1, Math.round(info.height * scale)),
        success: resolve, fail: () => reject(new Error('图片压缩失败，请重新选择图片')),
      }));
      if (result.tempFilePath && await fileSize(result.tempFilePath) <= MAX_IMAGE_BYTES) return result.tempFilePath;
    }
  }
  throw new Error('图片压缩后仍超过200KB，请选择更小的图片');
}
module.exports = { prepareImage, MAX_IMAGE_BYTES };
