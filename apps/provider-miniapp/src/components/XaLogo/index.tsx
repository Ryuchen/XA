import React, { useMemo } from 'react';
import { Image } from '@tarojs/components';

interface XaLogoProps {
  /** 尺寸（rpx），宽高一致 */
  size?: number;
  /** 图形颜色 */
  color?: string;
  className?: string;
  style?: React.CSSProperties;
}

// 小程序端无 btoa，手动实现 UTF-8 安全 base64 编码
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
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
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

/** 兴安电竞 XA logo（矢量复刻，双 X/A 交叠电竞标志） */
const XaLogo: React.FC<XaLogoProps> = ({
  size = 80,
  color = '#2E7BD6',
  className = '',
  style,
}) => {
  const src = useMemo(() => {
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1456 816" fill="${color}">` +
      `<polygon points="300,200 470,200 610,315 555,360 430,320 300,320"/>` +
      `<polygon points="430,320 600,320 230,715 60,715"/>` +
      `<polygon points="760,95 835,95 560,715 485,715"/>` +
      `<polygon points="855,95 945,95 675,715 585,715"/>` +
      `<polygon points="875,95 1015,95 1345,715 1205,715 1150,595 1255,595 1075,470 1180,470 1010,340"/>` +
      `</svg>`;
    return `data:image/svg+xml;base64,${encodeBase64(svg)}`;
  }, [color]);

  return (
    <Image
      className={className}
      style={{ width: `${size}rpx`, height: `${size}rpx`, ...style }}
      src={src}
      mode="aspectFit"
    />
  );
};

export default XaLogo;
