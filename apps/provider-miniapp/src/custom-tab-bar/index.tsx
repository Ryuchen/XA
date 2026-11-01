import { nativeColors } from '@/styles/nativeColors';
import { Component } from 'react';
import Taro from '@tarojs/taro';
import { Button, View, Text } from '@tarojs/components';
import TabIcon from '@/components/TabIcon';
import styles from './index.module.scss';
import { MessageUnreadContext } from '@/hooks/useMessageUnread';

const TAB_LIST = [
  { pagePath: '/pages/orders/index', text: '接单', icon: 'gamepad' as const },
  { pagePath: '/pages/report/index', text: '报单', icon: 'receipt' as const },
  { pagePath: '/pages/messages/index', text: '消息', icon: 'bell' as const },
  { pagePath: '/pages/wallet/index', text: '钱包', icon: 'wallet' as const },
  { pagePath: '/pages/mine/index', text: '我的', icon: 'user' as const }
];

interface TabBarState {
  selected: number;
}

export default class CustomTabBar extends Component<{ active?: 'orders' | 'report' | 'messages' | 'wallet' | 'mine' }, TabBarState> {
  static contextType = MessageUnreadContext;
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
    const unread = this.context as number | null;
    const selected = this.props.active ? TAB_LIST.findIndex(item => item.pagePath === `/pages/${this.props.active}/index`) : this.state.selected;
    return (
      <View className={styles.tabBar}>
        {TAB_LIST.map((item, index) => {
          const active = selected === index;
          return (
            <Button
              key={item.pagePath}
              className={`${styles.tabItem} ${active ? styles.tabItemActive : ''}`}
              aria-label={item.text === '消息' && unread ? `${item.text}，${unread}条未读` : item.text}
              aria-current={active ? 'page' : undefined}
              onClick={() => this.switchTab(item.pagePath)}
            >
              <View className={styles.iconWrap}>
                <TabIcon name={item.icon} color={active ? nativeColors.primary : nativeColors.secondary} />
                {item.text === '消息' && !!unread && <Text className={styles.unread}>{unread > 99 ? '99+' : unread}</Text>}
              </View>
              <Text className={`${styles.tabText} ${active ? styles.tabTextActive : ''}`}>{item.text}</Text>
            </Button>
          );
        })}
      </View>
    );
  }
}
