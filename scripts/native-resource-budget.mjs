import fs from 'node:fs';
import path from 'node:path';

// Use decimal KB conservatively, satisfying a 200 KiB gate as well.
export const MAX_NATIVE_MEDIA_BYTES = 200_000;
const mediaExtensions = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg',
  '.bmp', '.ico', '.tif', '.tiff', '.avif', '.heic', '.mp3', '.wav', '.m4a',
  '.aac', '.ogg', '.silk', '.amr', '.flac', '.wma', '.opus']);

export function auditNativeResources(root) {
  const files = [];
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error('Unexpected symlink in native package: ' + file);
      if (entry.isDirectory()) walk(file);
      else if (entry.isFile() && mediaExtensions.has(path.extname(file).toLowerCase())) {
        files.push({ path: path.relative(root, file).replaceAll('\\', '/'), bytes: fs.statSync(file).size });
      }
    }
  }
  walk(root);
  const totalBytes = files.reduce((sum, file) => sum + file.bytes, 0);
  return { files, totalBytes, limitBytes: MAX_NATIVE_MEDIA_BYTES, passed: totalBytes <= MAX_NATIVE_MEDIA_BYTES };
}
