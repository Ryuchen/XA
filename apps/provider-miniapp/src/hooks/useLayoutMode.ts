import { useEffect, useState } from 'react';

export type LayoutMode = 'mobile' | 'desktop';

const resolveMode = (): LayoutMode => {
  if (process.env.TARO_ENV !== 'h5' || typeof window === 'undefined') return 'mobile';
  return window.innerWidth >= 960 ? 'desktop' : 'mobile';
};

/** H5 手机与桌面 Web 使用独立布局分支，960px 为产品形态切换点。 */
export const useLayoutMode = (): LayoutMode => {
  const [mode, setMode] = useState<LayoutMode>(resolveMode);
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const update = () => setMode(resolveMode());
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);
  return mode;
};
