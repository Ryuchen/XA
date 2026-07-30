import { Component } from 'react';
import Taro from '@tarojs/taro';
import { View, Text } from '@tarojs/components';
import TabIcon from '@/components/TabIcon';
import styles from './index.module.scss';

const TAB_LIST = [
  { pagePath: '/pages/orders/index', text: '接单', icon: 'gamepad' as const },
  { pagePath: '/pages/messages/index', text: '消息', icon: 'bell' as const },
  { pagePath: '/pages/wallet/index', text: '钱包', icon: 'wallet' as const },
  { pagePath: '/pages/mine/index', text: '我的', icon: 'user' as const }
];

interface TabBarState {
  selected: number;
}

export default class CustomTabBar extends Component<{}, TabBarState> {
  state: TabBarState = {
    selected: this.resolveSelected()
  };

  resolveSelected(): number {
    const path = Taro.getCurrentInstance().router?.path || '';
    const normalized = path.startsWith('/') ? path : `/${path}`;
    const idx = TAB_LIST.findIndex(item => normalized.indexOf(item.pagePath) === 0);
    return idx >= 0 ? idx : 0;
  }

  componentDidShow() {
    this.setState({ selected: this.resolveSelected() });
  }

  switchTab(url: string) {
    Taro.switchTab({ url });
  }

  render() {
    const { selected } = this.state;
    return (
      <View className={styles.tabBar}>
        {TAB_LIST.map((item, index) => {
          const active = selected === index;
          return (
            <View
              key={item.pagePath}
              className={`${styles.tabItem} ${active ? styles.tabItemActive : ''}`}
              onClick={() => this.switchTab(item.pagePath)}
            >
              <View className={styles.iconWrap}>
                <TabIcon name={item.icon} color={active ? '#007AFF' : '#8E8E93'} />
              </View>
              <Text className={`${styles.tabText} ${active ? styles.tabTextActive : ''}`}>{item.text}</Text>
            </View>
          );
        })}
      </View>
    );
  }
}
