import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, Image, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchEscorts, EscortProfile } from '@/services/user';
import { fetchServiceDetail, fetchServices } from '@/services/order';
import { ServiceInfo } from '@/types/order';
import { formatXaCoin } from '@/utils/money';
import { Skeleton } from '@/components';
import Icon from '@/components/Icon';
import { useVoicePreview } from '@/hooks/useVoicePreview';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const COLOR_PRICE = '#FF9500';
const COLOR_TEXT_SECONDARY = '#6E6E73';
const COLOR_WHITE = '#FFFFFF';

const sortTabs = [
  { label: '推荐', value: 'recommend' },
  { label: '评分', value: 'rating' },
  { label: '接单多', value: 'orders' },
  { label: '价格低', value: 'price' }
] as const;

const statusTextMap: Record<string, string> = {
  online: '在线',
  AVAILABLE: '在线',
  busy: '忙碌',
  BUSY: '忙碌',
  offline: '离线',
  OFFLINE: '离线'
};

// 状态归一化：统一大小写映射到样式 key（在线/忙碌/离线）
const statusKey = (status: string): 'online' | 'busy' | 'offline' => {
  const value = (status || '').toLowerCase();
  if (value === 'available' || value === 'online') return 'online';
  if (value === 'busy') return 'busy';
  return 'offline';
};

const PlayerSelectPage: React.FC = () => {
  const [service, setService] = useState<ServiceInfo | null>(null);
  const [escorts, setEscorts] = useState<EscortProfile[]>([]);
  const [activeSort, setActiveSort] = useState<typeof sortTabs[number]['value']>('recommend');
  const [selectedPlayerId, setSelectedPlayerId] = useState<number | null>(null);
  const [gameRounds, setGameRounds] = useState(1);
  const [loading, setLoading] = useState(true);
  const { playingId, toggle: toggleVoice } = useVoicePreview();

  useEffect(() => {
    const params = Taro.getCurrentInstance().router?.params || {};
    const serviceId = params.serviceId || params.id;
    const presetEscortId = params.escortId || params.playerId;
    const presetRounds = params.game_rounds ? parseInt(params.game_rounds, 10) : 1;
    if (presetRounds > 0) setGameRounds(presetRounds);
    if (presetEscortId) {
      const eid = Number(presetEscortId);
      if (!Number.isNaN(eid)) setSelectedPlayerId(eid);
    }

    if (serviceId) {
      fetchServiceDetail(serviceId).then(res => {
        if (res.code === 0 && res.data) setService(res.data);
      }).catch(e => console.warn('加载服务详情失败', e));
    } else {
      // 无 serviceId 入口（如首页大神卡片直接下单）：兜底取第一个可用服务，保证下单链路可继续
      fetchServices().then(res => {
        if (res.code === 0 && res.data && res.data.length > 0) {
          setService(res.data[0]);
        }
      }).catch(e => console.warn('兜底获取默认服务失败', e));
    }

    fetchEscorts()
      .then(res => {
        if (res.code === 0 && res.data) setEscorts(res.data);
      })
      .catch(e => console.warn('加载陪玩列表失败', e))
      .finally(() => setLoading(false));
  }, []);

  const players = useMemo(() => {
    const list = escorts.slice();
    if (activeSort === 'rating') return list.sort((a, b) => b.rating - a.rating);
    if (activeSort === 'orders') return list.sort((a, b) => b.orderCount - a.orderCount);
    if (activeSort === 'price') return list.sort((a, b) => a.pricePerHour - b.pricePerHour);
    return list.sort((a, b) => {
      const onlineScore = (e: EscortProfile) =>
        e.status === 'online' || e.status === 'AVAILABLE' ? 2 :
        e.status === 'busy' || e.status === 'BUSY' ? 1 : 0;
      return onlineScore(b) - onlineScore(a) || b.rating - a.rating;
    });
  }, [activeSort, escorts]);

  const handleSmartMatch = () => {
    if (!service) {
      Taro.showToast({ title: '服务加载中，请稍候', icon: 'none' });
      return;
    }
    Taro.redirectTo({
      url: `/pages/checkout/index?service_id=${service.id}&game_rounds=${gameRounds}`
    });
  };

  const handleConfirm = () => {
    if (!selectedPlayerId) {
      Taro.showToast({ title: '请先选择心仪大神', icon: 'none' });
      return;
    }
    if (!service) {
      Taro.showToast({ title: '服务加载中，请稍候', icon: 'none' });
      return;
    }

    Taro.redirectTo({
      url: `/pages/checkout/index?service_id=${service.id}&playerId=${selectedPlayerId}&game_rounds=${gameRounds}`
    });
  };

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <Text className={styles.title}>{service?.name || '选择大神'}</Text>
        <Text className={styles.subtitle}>挑选心仪大神，由其专属服务</Text>
      </View>

      <ScrollView className={styles.sortTabs} scrollX>
        {sortTabs.map((tab, index) => (
          <View
            key={tab.value}
            className={`${styles.sortTab} ${activeSort === tab.value ? styles.active : ''}`}
            onClick={() => setActiveSort(tab.value)}
          >
            {index === 0 && (
              <Icon
                name="sliders-horizontal"
                size={24}
                color={activeSort === tab.value ? COLOR_WHITE : COLOR_TEXT_SECONDARY}
                className={styles.sortIcon}
              />
            )}
            <Text>{tab.label}</Text>
          </View>
        ))}
      </ScrollView>

      <ScrollView className={styles.playerList} scrollY>
        <View className={styles.smartCard} onClick={handleSmartMatch}>
          <View>
            <Text className={styles.smartTitle}>平台智能匹配</Text>
            <Text className={styles.smartText}>不纠结人选，客服按时间和需求自动安排</Text>
          </View>
          <Text className={styles.smartAction}>直接下单</Text>
        </View>

        {players.map(player => {
          const sKey = statusKey(player.status);
          const isSelected = selectedPlayerId === player.id;
          return (
            <View
              key={player.id}
              className={`${styles.playerCard} ${isSelected ? styles.selected : ''}`}
              onClick={() => setSelectedPlayerId(player.id)}
            >
              {isSelected && (
                <View className={styles.selectBadge}>
                  <Icon name="check" size={22} color={COLOR_WHITE} strokeWidth={3} />
                </View>
              )}
              <View className={styles.avatarWrap}>
                <Image className={styles.avatar} src={resolveImageUrl(player.avatar)} mode="aspectFill" />
                <View className={`${styles.statusDot} ${styles[`dot_${sKey}`]}`} />
              </View>
              <View className={styles.playerInfo}>
                <View className={styles.nameRow}>
                  <Text className={styles.nickname}>{player.nickname}</Text>
                </View>
                <Text className={styles.rank}>{player.rank}</Text>
                {player.voice_card_url && (
                  <View
                    className={`${styles.voicePreview} ${playingId === player.id ? styles.voicePreviewPlaying : ''}`}
                    onClick={event => {
                      event.stopPropagation();
                      toggleVoice(player.id, player.voice_card_url);
                    }}
                  >
                    <Text className={styles.voicePreviewIcon}>{playingId === player.id ? '⏸' : '🔊'}</Text>
                    <Text>{playingId === player.id ? '播放中' : '试听'}</Text>
                  </View>
                )}
                <View className={styles.metaRow}>
                  <Icon name="star" size={26} color={COLOR_PRICE} fill={COLOR_PRICE} className={styles.metaIcon} />
                  <Text className={styles.ratingValue}>{player.rating}</Text>
                  <Text className={styles.meta}>
                    · 已接 {player.orderCount}{player.ratingCount ? ` · 评价 ${player.ratingCount}` : ''} · 胜率 {player.winRate}%
                  </Text>
                </View>
                <Text className={styles.intro}>{player.bio}</Text>
                <View className={styles.bottomRow}>
                  <View className={styles.priceBlock}>
                    <Text className={styles.price}>{formatXaCoin(player.pricePerHour)}兴安币</Text>
                    <Text className={styles.priceUnit}>/小时</Text>
                  </View>
                  <View className={`${styles.status} ${styles[sKey] || ''}`}>
                    <View className={`${styles.statusPillDot} ${styles[`dot_${sKey}`]}`} />
                    <Text>{statusTextMap[player.status] || statusTextMap[statusKey(player.status).toUpperCase()] || '离线'}</Text>
                  </View>
                </View>
              </View>
            </View>
          );
        })}

        {loading && <Skeleton variant="card-list" rows={4} />}

        {!loading && players.length === 0 && (
          <View className={styles.emptyBox}>
            <Icon name="gamepad-2" size={96} color="#AEAEB2" className={styles.emptyIcon} />
            <Text className={styles.emptyTitle}>暂无可选大神</Text>
            <Text className={styles.emptyDesc}>建议使用上方智能匹配，由客服为你安排</Text>
          </View>
        )}
      </ScrollView>

      <View className={styles.footer}>
        <View className={styles.footerInfo}>
          <Text className={styles.footerLabel}>已选择</Text>
          <Text className={styles.footerName}>
            {players.find(player => player.id === selectedPlayerId)?.nickname || '请选择大神'}
          </Text>
        </View>
        <View className={styles.confirmBtn} onClick={handleConfirm}>
          <Text>选TA下单</Text>
        </View>
      </View>
    </View>
  );
};

export default PlayerSelectPage;
