import React, { useMemo } from 'react';
import { Image } from '@tarojs/components';
import { ICON_PATHS, IconName } from './icons';

export type { IconName } from './icons';

interface IconProps {
  /** lucide 图标名，需已在 icons.ts 注册 */
  name: IconName;
  /** 尺寸（rpx），宽高一致 */
  size?: number;
  /** 描边颜色，支持 currentColor 语义外的任意 CSS 颜色 */
  color?: string;
  /** 线宽（lucide 默认 2） */
  strokeWidth?: number;
  /** 填充色（用于实心图标，如实心星星）；默认 none */
  fill?: string;
  className?: string;
  style?: React.CSSProperties;
  onClick?: () => void;
}

// base64 编码（小程序端无 btoa，需手动实现 UTF-8 安全编码）
const B64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
function encodeBase64(input: string): string {
  const bytes: number[] = [];
  for (let i = 0; i < input.length; i += 1) {
    const code = input.charCodeAt(i);
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    } else {
      bytes.push(
        0xe0 | (code >> 12),
        0x80 | ((code >> 6) & 0x3f),
        0x80 | (code & 0x3f)
      );
    }
  }
  let output = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const b1 = bytes[i];
    const b2 = i + 1 < bytes.length ? bytes[i + 1] : NaN;
    const b3 = i + 2 < bytes.length ? bytes[i + 2] : NaN;
    const enc1 = b1 >> 2;
    const enc2 = ((b1 & 0x3) << 4) | (isNaN(b2) ? 0 : b2 >> 4);
    const enc3 = isNaN(b2) ? 64 : ((b2 & 0xf) << 2) | (isNaN(b3) ? 0 : b3 >> 6);
    const enc4 = isNaN(b3) ? 64 : b3 & 0x3f;
    output +=
      B64_CHARS.charAt(enc1) +
      B64_CHARS.charAt(enc2) +
      (enc3 === 64 ? '=' : B64_CHARS.charAt(enc3)) +
      (enc4 === 64 ? '=' : B64_CHARS.charAt(enc4));
  }
  return output;
}

const Icon: React.FC<IconProps> = ({
  name,
  size = 40,
  color = '#1D1D1F',
  strokeWidth = 2,
  fill = 'none',
  className = '',
  style,
  onClick
}) => {
  const src = useMemo(() => {
    const body = ICON_PATHS[name] || '';
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" ` +
      `fill="${fill}" stroke="${color}" stroke-width="${strokeWidth}" ` +
      `stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
    return `data:image/svg+xml;base64,${encodeBase64(svg)}`;
  }, [name, color, strokeWidth, fill]);

  const mergedStyle: React.CSSProperties = {
    width: `${size}rpx`,
    height: `${size}rpx`,
    ...style
  };

  return (
    <Image
      className={className}
      style={mergedStyle}
      src={src}
      mode="aspectFit"
      onClick={onClick}
    />
  );
};

export default Icon;
