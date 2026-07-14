import React, { useMemo } from 'react';
import { View, Text } from '@tarojs/components';
import Taro from '@tarojs/taro';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

interface NavBarProps {
  title?: string;
  /** 是否显示返回按钮（默认根据页面栈自动判断） */
  showBack?: boolean;
  /** 透明背景（用于头图沉浸式，标题与图标转白） */
  transparent?: boolean;
  /** 自定义背景，传入则覆盖默认渐变 */
  background?: string;
  /** 文字与图标使用白色（沉浸式头图场景） */
  light?: boolean;
  className?: string;
  /** 标题区右侧的自定义内容 */
  extra?: React.ReactNode;
}

let cachedStatusBarHeight = 0;
const getStatusBarHeight = (): number => {
  if (cachedStatusBarHeight) return cachedStatusBarHeight;
  try {
    const info = Taro.getWindowInfo ? Taro.getWindowInfo() : Taro.getSystemInfoSync();
    cachedStatusBarHeight = info.statusBarHeight || 20;
  } catch {
    cachedStatusBarHeight = 20;
  }
  return cachedStatusBarHeight;
};

const NavBar: React.FC<NavBarProps> = ({
  title,
  showBack = true,
  transparent = false,
  background,
  light = false,
  className = '',
  extra
}) => {
  const statusBarHeight = useMemo(getStatusBarHeight, []);

  const canBack = useMemo(() => {
    if (!showBack) return false;
    const pages = Taro.getCurrentPages?.() || [];
    return pages.length > 1;
  }, [showBack]);

  const handleBack = () => {
    Taro.navigateBack({ delta: 1 }).catch(() => {
      Taro.switchTab({ url: '/pages/home/index' });
    });
  };

  const rootClass = [
    styles.navbar,
    transparent ? styles.transparent : '',
    light || transparent ? styles.light : '',
    className
  ]
    .filter(Boolean)
    .join(' ');

  const style: React.CSSProperties = {
    paddingTop: `${statusBarHeight}px`
  };
  if (background) style.background = background;

  return (
    <View className={rootClass} style={style}>
      <View className={styles.bar}>
        {canBack && (
          <View className={styles.back} onClick={handleBack}>
            <Icon
              name="chevron-left"
              size={44}
              color={light || transparent ? '#FFFFFF' : '#1D1D1F'}
              strokeWidth={2.4}
            />
          </View>
        )}
        <Text className={styles.title}>{title}</Text>
        {extra && <View className={styles.extra}>{extra}</View>}
      </View>
    </View>
  );
};

export default NavBar;
