import { MEDIA_BASE } from './env';

/**
 * 统一解析后端图片地址，确保老板端稳定展示。
 * - 空值返回兜底图（若提供）或空串
 * - 已是绝对地址（http/https/data/base64）直接返回
 * - 相对路径（如 /media/xxx.png）补全为 MEDIA_BASE + path
 */
export function resolveImageUrl(src?: string | null, fallback = ''): string {
  const value = (src || '').trim();
  if (!value) return fallback;
  if (/^(https?:|data:|blob:)/i.test(value)) return value;
  const path = value.startsWith('/') ? value : `/${value}`;
  return `${MEDIA_BASE}${path}`;
}
