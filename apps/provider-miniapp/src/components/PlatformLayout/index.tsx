import React from 'react';
import { Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import CustomTabBar from '@/custom-tab-bar';
import TabIcon from '@/components/TabIcon';
import { useLayoutMode } from '@/hooks/useLayoutMode';
import styles from './index.module.scss';

export type MainSection = 'orders' | 'report' | 'messages' | 'wallet' | 'mine';
const NAV = [
  { key: 'orders' as const, path: '/pages/orders/index', label: '接单工作台', caption: '订单与服务', icon: 'gamepad' as const },
  { key: 'report' as const, path: '/pages/report/index', label: '订单报单', caption: '凭证与审核', icon: 'receipt' as const },
  { key: 'messages' as const, path: '/pages/messages/index', label: '消息中心', caption: '通知与动态', icon: 'bell' as const },
  { key: 'wallet' as const, path: '/pages/wallet/index', label: '资产钱包', caption: '流水与提现', icon: 'wallet' as const },
  { key: 'mine' as const, path: '/pages/mine/index', label: '个人中心', caption: '资料与设置', icon: 'user' as const },
];

const PlatformLayout: React.FC<React.PropsWithChildren<{ active: MainSection }>> = ({ active, children }) => {
  const mode = useLayoutMode();
  if (mode === 'mobile') {
    return <><View className={styles.mobileContent}>{children}</View>{process.env.TARO_ENV === 'h5' && <CustomTabBar />}</>;
  }
  return <View className={styles.desktopShell}>
    <View className={styles.sidebar}>
      <View className={styles.brand}><View className={styles.brandMark}>XA</View><View><Text className={styles.brandName}>兴安电竞</Text><Text className={styles.brandSub}>陪玩工作台</Text></View></View>
      <View className={styles.navList}>{NAV.map(item => <View key={item.key} className={`${styles.navItem} ${active === item.key ? styles.navActive : ''}`} onClick={() => active !== item.key && Taro.switchTab({ url: item.path })}>
        <View className={styles.navIcon}><TabIcon name={item.icon} color={active === item.key ? '#ffffff' : '#8E8E93'} /></View><View><Text className={styles.navLabel}>{item.label}</Text><Text className={styles.navCaption}>{item.caption}</Text></View>
      </View>)}</View>
      <Text className={styles.sidebarFoot}>桌面 Web 工作模式</Text>
    </View>
    <View className={styles.desktopMain}><View className={styles.desktopContent}>{children}</View></View>
  </View>;
};
export default PlatformLayout;
