import React, { useMemo } from 'react';
import { Image } from '@tarojs/components';

type Name = 'gamepad' | 'receipt' | 'bell' | 'wallet' | 'user';

const PATHS: Record<Name, string> = {
  gamepad: '<path d="M6 11h4M8 9v4"/><path d="M15 12h.01M18 10h.01"/><path d="M17.3 5H6.7a4 4 0 0 0-4 3.6C2.6 9.4 2 14.5 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.4-1.4A2 2 0 0 1 9.8 16h4.4a2 2 0 0 1 1.4.6L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.5-.6-6.6-.7-7.4A4 4 0 0 0 17.3 5Z"/>',
  receipt: '<path d="M5 3v18l2-1.3L9 21l2-1.3L13 21l2-1.3L17 21l2-1.3V3l-2 1.3L15 3l-2 1.3L11 3 9 4.3 7 3Z"/><path d="M9 9h6M9 13h6M9 17h4"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
  wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3v4a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2V5"/>',
  user: '<circle cx="12" cy="7" r="4"/><path d="M5 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2"/>',
};

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
function encode(input: string) {
  const bytes = Array.from(input).map(char => char.charCodeAt(0));
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i], b = bytes[i + 1], c = bytes[i + 2];
    out += B64[a >> 2] + B64[((a & 3) << 4) | ((b || 0) >> 4)]
      + (b === undefined ? '=' : B64[((b & 15) << 2) | ((c || 0) >> 6)])
      + (c === undefined ? '=' : B64[c & 63]);
  }
  return out;
}

export default function TabIcon({ name, color, size = 46 }: { name: Name; color: string; size?: number }) {
  const src = useMemo(() => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PATHS[name]}</svg>`;
    return `data:image/svg+xml;base64,${encode(svg)}`;
  }, [name, color]);
  return <Image src={src} mode="aspectFit" style={{ width: `${size}rpx`, height: `${size}rpx` }} />;
}
