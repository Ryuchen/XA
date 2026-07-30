import React, { useEffect, useState } from 'react';
import { View, Text, Image } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { openWecomCustomerService } from '@/services/support';
import { clearStoredUser, getStoredToken, getStoredUser, roleTextMap, LoginUser } from '@/utils/auth';
import { fetchOrderStats } from '@/services/order';
import { fetchWalletInfo } from '@/services/wallet';
import { fetchMe, MeProfile } from '@/services/user';
import { formatXaCoin } from '@/utils/format';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import Icon, { IconName } from '@/components/Icon';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const MinePage: React.FC = () => {
  const [user, setUser] = useState<LoginUser | null>(getStoredUser());
  const { ensureLogin, loginSheet } = useLoginGuard();

  const [stats, setStats] = useState({
    pending: 0,
    grabbed: 0,
    in_service: 0,
    completed: 0,
  });

  const [balance, setBalance] = useState(0);
  const [profile, setProfile] = useState<MeProfile | null>(null);

  // 拉取需鉴权数据：仅登录后调用，避免匿名态触发无意义 401
  const loadAuthedData = () => {
    if (!getStoredToken()) return;
    fetchOrderStats()
      .then((res: any) => {
        if (res.code === 0 && res.data) setStats(res.data);
      })
      .catch(() => {});
    fetchWalletInfo()
      .then((res: any) => {
        if (res.code === 0 && res.data) setBalance(res.data.balance);
      })
      .catch(() => {});
    fetchMe()
      .then(res => {
        if (res.code === 0 && res.data) setProfile(res.data);
      })
      .catch(() => {});
  };

  useEffect(() => {
    loadAuthedData();
  }, []);

  // 从设置页返回或登录后刷新资料
  useDidShow(() => {
    setUser(getStoredUser());
    loadAuthedData();
  });

  const handleLogin = () => {
    ensureLogin(() => {
      setUser(getStoredUser());
      loadAuthedData();
    });
  };

  const orderStats: { icon: IconName; label: string; count: number; status: string }[] = [
    { icon: 'timer', label: '待接单', count: stats.pending, status: 'pending' },
    { icon: 'package', label: '已接单', count: stats.grabbed, status: 'grabbed' },
    { icon: 'gamepad-2', label: '服务中', count: stats.in_service, status: 'in_progress' },
    { icon: 'check-circle', label: '已完成', count: stats.completed, status: 'completed' }
  ];

  const menuItems: { icon: IconName; text: string; arrow: boolean; url?: string; logout?: boolean; wecom?: boolean }[] = [
    { icon: 'wallet', text: '我的钱包', arrow: true, url: '/pages/customer/wallet/index' },
    { icon: 'file-text', text: '全部订单', arrow: true, url: '/pages/orderList/index' },
    { icon: 'heart', text: '我的收藏', arrow: true, url: '/pages/favorite/index' },
    { icon: 'headphones', text: '联系官方客服', arrow: true, wecom: true },
    { icon: 'settings', text: '账号设置', arrow: true, url: '/pages/settings/index' },
    { icon: 'calendar-check', text: '每月签到', arrow: true, url: '/pages/customer/checkin/index' },
    { icon: 'gift', text: '领券中心', arrow: true, url: '/pages/coupon/index' },
    { icon: 'ticket', text: '我的券包', arrow: true, url: '/pages/coupon/mine/index' },
    // 退出登录仅登录态展示
    ...(user ? [{ icon: 'log-out' as IconName, text: '退出登录', arrow: true, logout: true }] : [])
  ];

  const handleNavigate = (item) => {
    if (item.logout) {
      clearStoredUser();
      setUser(null);
      setStats({ pending: 0, grabbed: 0, in_service: 0, completed: 0 });
      setBalance(0);
      setProfile(null);
      Taro.showToast({ title: '已退出登录', icon: 'none' });
      return;
    }
    if (item.wecom) {
      ensureLogin(async () => {
        Taro.showLoading({ title: '正在连接客服' });
        const result = await openWecomCustomerService({
          title: '我要联系官方客服',
          path: '/pages/mine/index',
        });
        Taro.hideLoading();
        if (!result.opened) Taro.navigateTo({ url: '/pages/serviceCard/index' });
      });
      return;
    }
    if (item.url) {
      // 需登录的菜单项：未登录先弹登录窗，登录后再跳转
      ensureLogin(() => Taro.navigateTo({ url: item.url }));
      return;
    }
    Taro.showToast({ title: item.toast || '功能完善中', icon: 'none' });
  };

  const gameProfiles = profile?.game_profiles || [];
  const hasProfile = gameProfiles.length > 0 || !!(profile?.game_region || profile?.game_nickname || profile?.game_uid);

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <Text className={styles.eyebrow}>CUSTOMER PROFILE</Text>
        <View className={styles.headerTop}>
          <View
            className={styles.userInfo}
            onClick={() => { if (!user) handleLogin(); }}
          >
            <Image
              className={styles.avatar}
              src={resolveImageUrl(user?.avatar, 'https://picsum.photos/id/64/200/200')}
              mode="aspectFill"
            />
            <View className={styles.userDetail}>
              <Text className={styles.nickname}>{user?.nickname || '点击登录'}</Text>
              <Text className={styles.userId}>
                {user ? `${roleTextMap[user.role]} · ID: ${user.id.slice(-6)}` : '微信快捷登录，即刻下单'}
              </Text>
              {user && (
                <View className={styles.memberBadge}>
                  <Icon name="shield-check" size={24} color="#FFFFFF" />
                  <Text className={styles.memberBadgeText}>已登录老板</Text>
                </View>
              )}
            </View>
          </View>
          <View
            className={styles.settingsBtn}
            onClick={() => handleNavigate({ url: '/pages/settings/index' })}
          >
            <Icon name="settings" size={40} color="#FFFFFF" />
          </View>
        </View>
        <View className={styles.walletCard} onClick={() => handleNavigate({ url: '/pages/customer/wallet/index' })}>
          <View className={styles.walletMain}>
            <Text className={styles.walletLabel}>兴安币余额</Text>
            <Text className={styles.walletAmount}>{formatXaCoin(balance)}币</Text>
          </View>
          <Text className={styles.walletAction}>企微充值 ›</Text>
        </View>
      </View>

      <View className={styles.orderCard}>
        <View className={styles.orderHeader}>
          <Text className={styles.orderTitle}>我的订单</Text>
          <View className={styles.viewAll} onClick={() => handleNavigate({ url: '/pages/orderList/index' })}>
            <Text className={styles.viewAllText}>查看全部</Text>
            <Icon name="chevron-right" size={28} color="#AEAEB2" />
          </View>
        </View>
        <View className={styles.orderStats}>
          {orderStats.map((stat, index) => (
            <View
              key={index}
              className={styles.orderStatItem}
              onClick={() => handleNavigate({ url: `/pages/orderList/index?status=${stat.status}` })}
            >
              <View className={styles.orderIconWrap}>
                <Icon name={stat.icon} size={48} color="#007AFF" />
                {stat.count > 0 && <Text className={styles.orderBadge}>{stat.count}</Text>}
              </View>
              <Text className={styles.orderLabel}>{stat.label}</Text>
            </View>
          ))}
        </View>
      </View>

      <View className={styles.profileCard}>
        <View className={styles.profileHeader}>
          <Text className={styles.profileTitle}>常用游戏资料</Text>
          <Text className={styles.profileAction} onClick={() => handleNavigate({ url: '/pages/settings/index' })}>
            {hasProfile ? '编辑' : '去完善'}
          </Text>
        </View>
        {hasProfile ? (
          gameProfiles.length > 0 ? gameProfiles.map(item => (
            <View key={item.game_category} className={styles.profileRow}>
              <Text className={styles.profileLabel}>{item.game_category_name}</Text>
              <Text className={styles.profileValue}>
                {[item.region, item.nickname, item.uid].filter(Boolean).join(' · ') || '未填写'}
              </Text>
            </View>
          )) : (
            <View className={styles.profileRow}>
              <Text className={styles.profileLabel}>旧版默认资料</Text>
              <Text className={styles.profileValue}>
                {[profile?.game_region, profile?.game_nickname, profile?.game_uid].filter(Boolean).join(' · ')}
              </Text>
            </View>
          )
        ) : (
          <View className={styles.profileRow}>
            <Text className={styles.profileLabel}>填写后下单可自动回填大区、昵称和UID</Text>
          </View>
        )}
      </View>

      <View className={styles.serviceCard}>
        <View className={styles.serviceItem} onClick={() => Taro.switchTab({ url: '/pages/category/index' })}>
          <Text className={styles.serviceName}>挑选服务</Text>
          <Text className={styles.serviceDesc}>陪玩 / 上分 / 护航</Text>
        </View>
        <View className={styles.serviceItem} onClick={() => Taro.navigateTo({ url: '/pages/serviceCard/index' })}>
          <Text className={styles.serviceName}>联系官方客服</Text>
          <Text className={styles.serviceDesc}>改期、售后、指定陪玩</Text>
        </View>
      </View>

      <View className={styles.menuCard}>
        {menuItems.map((item, index) => (
          <View key={index} className={styles.menuItem} onClick={() => handleNavigate(item)}>
            <View className={styles.menuIcon}>
              <Icon name={item.icon} size={40} color="#007AFF" />
            </View>
            <Text className={styles.menuText}>{item.text}</Text>
            {item.arrow && (
              <Icon name="chevron-right" size={32} color="#AEAEB2" className={styles.menuArrow} />
            )}
          </View>
        ))}
      </View>
      {loginSheet}
    </View>
  );
};

export default MinePage;
