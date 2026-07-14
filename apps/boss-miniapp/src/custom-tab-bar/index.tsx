import { Component } from 'react';
import Taro from '@tarojs/taro';
import { View, Text } from '@tarojs/components';
import Icon, { IconName } from '@/components/Icon';
import styles from './index.module.scss';

const TAB_LIST: { pagePath: string; text: string; icon: IconName }[] = [
  { pagePath: '/pages/home/index', text: '首页', icon: 'home' },
  { pagePath: '/pages/companions/index', text: '陪玩', icon: 'users' },
  { pagePath: '/pages/category/index', text: '服务', icon: 'layout-grid' },
  { pagePath: '/pages/chat/index', text: '消息', icon: 'message-circle' },
  { pagePath: '/pages/mine/index', text: '我的', icon: 'user' }
];

const ACTIVE_COLOR = '#007AFF';
const INACTIVE_COLOR = '#AEAEB2';

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
    // 高亮完全由真实路由驱动（componentDidShow → resolveSelected），
    // 此处不做乐观更新，避免与路由切换产生竞态导致高亮/内容错位
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
              <View className={styles.tabIconWrap}>
                <Icon
                  name={item.icon}
                  size={48}
                  color={active ? ACTIVE_COLOR : INACTIVE_COLOR}
                  strokeWidth={active ? 2.4 : 2}
                />
              </View>
              <Text className={`${styles.tabText} ${active ? styles.tabTextActive : ''}`}>
                {item.text}
              </Text>
            </View>
          );
        })}
      </View>
    );
  }
}
