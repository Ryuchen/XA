import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, Image, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchServices, fetchGameCategories, GameCategoryInfo } from '@/services/order';
import { fetchEscorts, EscortProfile } from '@/services/user';
import { fetchContactCards, SupportContactCard } from '@/services/support';
import { ServiceInfo } from '@/types/order';
import { formatXaCoin } from '@/utils/format';
import { Empty, Icon, Skeleton } from '@/components';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

type DispatchMode = 'platform' | 'assign';

const steps = ['游戏类目', '游玩项目', '选择陪玩', '负责客服'];
const FALLBACK_IMAGE = 'https://picsum.photos/id/1/300/300';

const SelfOrderPage: React.FC = () => {
  const [step, setStep] = useState(0);
  const [services, setServices] = useState<ServiceInfo[]>([]);
  const [categories, setCategories] = useState<GameCategoryInfo[]>([]);
  const [escorts, setEscorts] = useState<EscortProfile[]>([]);
  const [contacts, setContacts] = useState<SupportContactCard[]>([]);
  const [gameCategoryId, setGameCategoryId] = useState<number | null>(null);
  const [serviceId, setServiceId] = useState<number | null>(null);
  const [dispatchMode, setDispatchMode] = useState<DispatchMode>('platform');
  const [providerId, setProviderId] = useState<number | null>(null);
  const [supportContactId, setSupportContactId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetchServices(), fetchGameCategories(), fetchContactCards()])
      .then(([serviceRes, categoryRes, contactRes]) => {
        const availableServices = (serviceRes.data || []).filter(
          item => !item.service_category_name?.includes('礼')
        );
        setServices(availableServices);
        setCategories(categoryRes.data || []);
        setContacts(contactRes.data || []);
      })
      .catch(() => Taro.showToast({ title: '下单信息加载失败', icon: 'none' }))
      .finally(() => setLoading(false));
  }, []);

  // 已选服务项后，按服务项精准拉取可接该服务项的陪玩（最细粒度）；
  // 尚未选服务项时退回按游戏类目筛选；两者都未选时清空。
  useEffect(() => {
    if (gameCategoryId == null) {
      setEscorts([]);
      return;
    }
    let active = true;
    fetchEscorts(undefined, gameCategoryId, serviceId ?? undefined)
      .then(res => {
        if (active && res.code === 0 && res.data) setEscorts(res.data);
      })
      .catch(() => {
        if (active) setEscorts([]);
      });
    return () => { active = false; };
  }, [gameCategoryId, serviceId]);

  const serviceCountByCategory = useMemo(() => {
    const counter = new Map<number, number>();
    services.forEach(item => {
      if (item.game_category != null) {
        counter.set(item.game_category, (counter.get(item.game_category) || 0) + 1);
      }
    });
    return counter;
  }, [services]);

  const gameCategories = useMemo(
    () => categories.filter(category => (serviceCountByCategory.get(category.id) || 0) > 0),
    [categories, serviceCountByCategory]
  );

  const currentServices = useMemo(
    () => services.filter(item => item.game_category === gameCategoryId),
    [services, gameCategoryId]
  );

  const canNext =
    (step === 0 && gameCategoryId != null) ||
    (step === 1 && serviceId != null) ||
    (step === 2 && (dispatchMode === 'platform' || providerId != null)) ||
    (step === 3 && supportContactId != null);

  const selectGame = (id: number) => {
    if (id !== gameCategoryId) {
      setGameCategoryId(id);
      setServiceId(null);
      // 换游戏后可接陪玩集合变化，重置已选陪玩，回落平台派单
      setProviderId(null);
      setDispatchMode('platform');
    }
  };

  const selectService = (id: number) => {
    if (id !== serviceId) {
      setServiceId(id);
      // 换服务项后可接陪玩集合变化，重置已选陪玩，回落平台派单
      setProviderId(null);
      setDispatchMode('platform');
    }
  };

  const selectProvider = (id: number) => {
    setDispatchMode('assign');
    setProviderId(id);
  };

  const next = () => {
    if (!canNext) {
      const hints = ['请选择游戏类目', '请选择游玩项目', '请选择陪玩方式', '请选择负责订单的客服'];
      Taro.showToast({ title: hints[step], icon: 'none' });
      return;
    }
    if (step < 3) {
      setStep(step + 1);
      return;
    }
    const params = [
      'flow=self_order',
      `service_id=${serviceId}`,
      `support_contact_id=${supportContactId}`,
      dispatchMode === 'assign' && providerId ? `provider_id=${providerId}` : ''
    ].filter(Boolean).join('&');
    Taro.navigateTo({ url: `/pages/checkout/index?${params}` });
  };

  if (loading) {
    return <View className={styles.container}><Skeleton variant="card-list" rows={6} /></View>;
  }

  return (
    <View className={styles.container}>
      <View className={styles.progress}>
        {steps.map((label, index) => (
          <View key={label} className={styles.progressItem}>
            <View className={`${styles.progressNo} ${index <= step ? styles.progressNoActive : ''}`}>
              <Text>{index + 1}</Text>
            </View>
            <Text className={`${styles.progressText} ${index === step ? styles.progressTextActive : ''}`}>{label}</Text>
            {index < steps.length - 1 && <View className={`${styles.progressLine} ${index < step ? styles.progressLineActive : ''}`} />}
          </View>
        ))}
      </View>

      <ScrollView className={styles.content} scrollY>
        <View className={styles.heading}>
          <Text className={styles.title}>{steps[step]}</Text>
          <Text className={styles.subtitle}>
            {['先选择本次需要游玩的游戏', '选择具体玩法与计价项目', '可以指定心仪陪玩，也可以交给平台安排', '选择一位客服负责跟进本订单'][step]}
          </Text>
        </View>

        {step === 0 && (
          <View className={styles.optionGrid}>
            {gameCategories.map(category => (
              <View
                key={category.id}
                className={`${styles.gameCard} ${gameCategoryId === category.id ? styles.selected : ''}`}
                onClick={() => selectGame(category.id)}
              >
                <View className={styles.gameIcon}>
                  {category.icon_url ? (
                    <Image className={styles.gameIconImage} src={resolveImageUrl(category.icon_url)} mode="aspectFit" />
                  ) : (
                    <Icon name="gamepad-2" size={44} color={gameCategoryId === category.id ? '#007AFF' : '#6E6E73'} />
                  )}
                </View>
                <Text className={styles.gameName}>{category.name}</Text>
                <Text className={styles.optionHint}>{serviceCountByCategory.get(category.id) || 0} 个项目</Text>
                {gameCategoryId === category.id && <Icon name="check-circle" size={36} color="#007AFF" />}
              </View>
            ))}
            {gameCategories.length === 0 && <Empty icon="🎮" title="暂无游戏类目" desc="请先在管理后台配置游戏类目和服务项目" />}
          </View>
        )}

        {step === 1 && (
          <View className={styles.optionList}>
            {currentServices.map(service => (
              <View
                key={service.id}
                className={`${styles.serviceCard} ${serviceId === service.id ? styles.selected : ''}`}
                onClick={() => selectService(service.id)}
              >
                <Image className={styles.serviceImage} src={resolveImageUrl(service.cover_url, FALLBACK_IMAGE)} mode="aspectFill" />
                <View className={styles.optionMain}>
                  <Text className={styles.optionName}>{service.name}</Text>
                  <Text className={styles.optionDesc}>{service.description || '专业陪玩服务'}</Text>
                  <Text className={styles.price}>{formatXaCoin(service.price)}兴安币/次</Text>
                </View>
                {serviceId === service.id && <Icon name="check-circle" size={38} color="#007AFF" />}
              </View>
            ))}
          </View>
        )}

        {step === 2 && (
          <View className={styles.optionList}>
            <View
              className={`${styles.platformCard} ${dispatchMode === 'platform' ? styles.selected : ''}`}
              onClick={() => { setDispatchMode('platform'); setProviderId(null); }}
            >
              <View className={styles.platformIcon}><Icon name="sparkles" size={40} color="#007AFF" /></View>
              <View className={styles.optionMain}>
                <Text className={styles.optionName}>不指定陪玩</Text>
                <Text className={styles.optionDesc}>进入抢单池，由符合条件的陪玩接单或客服安排</Text>
              </View>
              {dispatchMode === 'platform' && <Icon name="check-circle" size={38} color="#007AFF" />}
            </View>
            {escorts.map(escort => (
              <View
                key={escort.id}
                className={`${styles.providerCard} ${providerId === escort.id ? styles.selected : ''}`}
                onClick={() => selectProvider(escort.id)}
              >
                <Image className={styles.providerAvatar} src={resolveImageUrl(escort.avatar, FALLBACK_IMAGE)} mode="aspectFill" />
                <View className={styles.optionMain}>
                  <Text className={styles.optionName}>{escort.nickname}</Text>
                  <Text className={styles.optionDesc}>{escort.rank} · 评分 {escort.rating} · 已接 {escort.orderCount}</Text>
                  <Text className={styles.price}>{formatXaCoin(escort.pricePerHour)}兴安币/小时</Text>
                </View>
                {providerId === escort.id && <Icon name="check-circle" size={38} color="#007AFF" />}
              </View>
            ))}
            {escorts.length === 0 && (
              <Empty icon="🧑‍💻" title="暂无可接该游戏的陪玩" desc="可选择不指定陪玩，交给平台安排接单" />
            )}
          </View>
        )}

        {step === 3 && (
          <View className={styles.optionList}>
            {contacts.map(contact => (
              <View
                key={contact.id}
                className={`${styles.contactCard} ${supportContactId === contact.id ? styles.selected : ''}`}
                onClick={() => setSupportContactId(contact.id)}
              >
                <Image className={styles.contactAvatar} src={resolveImageUrl(contact.avatar_url, FALLBACK_IMAGE)} mode="aspectFill" />
                <View className={styles.optionMain}>
                  <Text className={styles.optionName}>{contact.name}</Text>
                  <Text className={styles.optionDesc}>{contact.tips || contact.company || '负责订单进度与售后跟进'}</Text>
                </View>
                {supportContactId === contact.id && <Icon name="check-circle" size={38} color="#007AFF" />}
              </View>
            ))}
            {contacts.length === 0 && <Empty icon="💬" title="暂无可选客服" desc="请先在管理后台配置启用的客服名片" />}
          </View>
        )}
      </ScrollView>

      <View className={styles.footer}>
        <View className={`${styles.backButton} ${step === 0 ? styles.backButtonDisabled : ''}`} onClick={() => step > 0 && setStep(step - 1)}>
          <Text>上一步</Text>
        </View>
        <View className={`${styles.nextButton} ${!canNext ? styles.nextButtonDisabled : ''}`} onClick={next}>
          <Text>{step === 3 ? '填写订单并结算' : '下一步'}</Text>
        </View>
      </View>
    </View>
  );
};

export default SelfOrderPage;
