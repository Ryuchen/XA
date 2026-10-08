import React, { useEffect, useState } from 'react';
import { View, Text, Textarea, Input, Button, Picker } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { createOrder, quoteOrder, CreateOrderPayload, OrderQuote } from '@/services/order';
import { fetchMyCoupons, UserCoupon } from '@/services/coupon';
import { fetchMe, fetchEscorts, fetchEscortShortcuts, EscortProfile, MeProfile } from '@/services/user';
import { formatXaCoin } from '@xa/money';
import { openWecomCustomerService, SupportContactCard } from '@/services/support';
import { useCatalogStore, useOrderStore, useWalletStore } from '@/store';
import { useLoginGuard } from '@/hooks/useLoginGuard';
import { Skeleton, Empty } from '@/components';
import GameplayCover from '@/components/GameplayCover';
import { ServiceInfo } from '@/types/order';
import Icon from '@/components/Icon';
import { getStoredToken } from '@/utils/auth';
import styles from './index.module.scss';

type Service = ServiceInfo;

type DispatchMode = 'assign' | 'platform';

const CheckoutPage: React.FC = () => {
  const [services, setServices] = useState<Service[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [guidedFlow, setGuidedFlow] = useState(false);
  const [selectedService, setSelectedService] = useState<Service | null>(null);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [selectedEscort, setSelectedEscort] = useState<EscortProfile | null>(null);
  const [providerGender, setProviderGender] = useState<'ANY' | 'MALE' | 'FEMALE'>('ANY');
  const [shortcuts, setShortcuts] = useState<{ recent: EscortProfile[]; recommended: EscortProfile[] }>({ recent: [], recommended: [] });
  const [autoGrab, setAutoGrab] = useState(false);
  const [dispatchMode, setDispatchMode] = useState<DispatchMode>('platform');
  const [supportContacts, setSupportContacts] = useState<SupportContactCard[]>([]);
  const [supportContactId, setSupportContactId] = useState<number | null>(null);
  const [gameRounds, setGameRounds] = useState(1);
  const [gameAccount, setGameAccount] = useState({
    region: '',
    nickname: '',
    uid: ''
  });
  const [meProfile, setMeProfile] = useState<MeProfile | null>(null);
  const [remark, setRemark] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [usableCoupons, setUsableCoupons] = useState<UserCoupon[]>([]);
  const [selectedCouponId, setSelectedCouponId] = useState<number | null>(null);
  const [quoteRecord, setQuoteRecord] = useState<{ key: string; data: OrderQuote } | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState('');
  const { ensureLogin, loginSheet } = useLoginGuard();
  useDidShow(() => setRefresh(value => value + 1));

  useEffect(() => {
    const params = Taro.getCurrentInstance().router?.params || {};
    const presetServiceId = params.serviceId || params.service_id;
    const presetPlayerId = params.playerId || params.provider_id;
    const presetSupportContactId = params.supportContactId || params.support_contact_id;
    const presetRounds = params.game_rounds ? parseInt(params.game_rounds, 10) : 1;
    const isGuidedFlow = params.flow === 'self_order' || !!presetServiceId;
    setGuidedFlow(isGuidedFlow);

    if (presetRounds > 0) setGameRounds(presetRounds);
    if (presetPlayerId) {
      const pid = Number(presetPlayerId);
      if (!Number.isNaN(pid)) {
        setProviderId(pid);
        setDispatchMode('assign');
        // 解析已指定陪玩的展示信息（昵称/头像），下单页需明确"指定的是谁"
        fetchEscorts()
          .then(res => {
            if (res.code === 0 && res.data) {
              const matched = res.data.find(e => e.id === pid);
              if (matched) setSelectedEscort(matched);
            }
          })
          .catch(e => console.warn('获取指定陪玩信息失败', e));
      }
    }

    // 客服名片与服务目录复用全局缓存，避免每次进结算页都重拉
    const catalog = useCatalogStore.getState();

    catalog
      .loadContactCards()
      .then(list => {
        if (!list.length) return;
        setSupportContacts(list);
        const preset = Number(presetSupportContactId);
        const selected = list.find(item => item.id === preset) || list[0];
        setSupportContactId(selected?.id || null);
      })
      .catch(e => console.warn('获取负责客服失败', e));

    catalog
      .loadServices(undefined, true)
      .then(list => {
        if (!list.length) return;
        setServices(
          isGuidedFlow && presetServiceId
            ? list.filter(service => service.id === Number(presetServiceId))
            : list
        );
        if (presetServiceId) {
          const match = list.find(s => s.id === Number(presetServiceId));
          if (match) setSelectedService(match);
        } else {
          setSelectedService(list[0]);
        }
      })
      .catch(error => console.warn('获取服务列表失败', error))
      .finally(() => setCatalogLoading(false));
  }, []);

  useEffect(() => {
    fetchMe()
      .then(res => {
        if (res.code === 0 && res.data) setMeProfile(res.data);
      })
      .catch(e => console.warn('获取常用游戏资料失败', e));
  }, []);

  useEffect(() => {
    if (!meProfile || !selectedService) return;
    const matched = (meProfile.game_profiles || []).find(
      item => item.game_category === selectedService.game_category
    );
    if (matched) {
      setGameAccount({ region: matched.region, nickname: matched.nickname, uid: matched.uid });
      return;
    }
    if ((meProfile.game_profiles || []).length === 0) {
      setGameAccount({
        region: meProfile.game_region || '',
        nickname: meProfile.game_nickname || '',
        uid: meProfile.game_uid || '',
      });
      return;
    }
    setGameAccount({ region: '', nickname: '', uid: '' });
  }, [meProfile, selectedService]);

  useEffect(() => {
    let current = true;
    setShortcuts({ recent: [], recommended: [] });
    if (selectedService) {
      fetchEscortShortcuts(selectedService.id, selectedService.service_type === 'COMPANION' ? providerGender : 'ANY').then(res => {
        if (current && res.code === 0 && res.data) setShortcuts(res.data);
      }).catch(() => {});
    }
    return () => { current = false; };
  }, [selectedService?.id, providerGender, refresh]);

  const originalAmount = selectedService ? selectedService.price * gameRounds : 0;

  // 报价试算与正式下单共用的订单载荷，避免共享字段重复构造。
  // includeGameAccount 为 true 时附带游戏账号信息与备注（仅下单需要）。
  const buildOrderPayload = (opts?: { includeGameAccount?: boolean }): CreateOrderPayload => {
    const payload: CreateOrderPayload = {
      dispatch_mode: dispatchMode !== 'assign' && autoGrab ? 'AUTO_GRAB' : 'BOSS_SELECT',
      service_id: selectedService!.id,
      game_rounds: gameRounds,
      provider_gender: selectedService?.service_type === 'COMPANION' ? providerGender : 'ANY',
    };
    if (supportContactId) payload.support_contact_id = supportContactId;
    // 仅"指定陪玩"模式携带 provider_id；平台派单不指定，交由后台待接单/客服指派
    if (dispatchMode === 'assign' && providerId) payload.provider_id = providerId;
    if (selectedCouponId != null) payload.user_coupon_id = selectedCouponId;
    if (opts?.includeGameAccount) {
      payload.game_region = gameAccount.region.trim();
      payload.game_nickname = gameAccount.nickname.trim();
      payload.game_uid = gameAccount.uid.trim();
      payload.remark = remark.trim();
    }
    return payload;
  };

  // A changed quantity, coupon, provider or refreshed wallet must never use an older quote.
  const quoteKey = selectedService ? JSON.stringify([buildOrderPayload(), refresh]) : '';
  const quote = quoteRecord?.key === quoteKey ? quoteRecord.data : null;

  useEffect(() => {
    if (originalAmount <= 0) {
      setUsableCoupons([]);
      return;
    }
    fetchMyCoupons({ usable: true, amount: originalAmount })
      .then(res => {
        if (res.code === 0 && res.data) {
          setUsableCoupons(res.data);
          setSelectedCouponId(prev =>
            prev != null && res.data!.some(c => c.id === prev) ? prev : null
          );
        }
      })
      .catch(e => console.warn('获取可用优惠券失败', e));
  }, [originalAmount]);

  useEffect(() => {
    if (!selectedService || !getStoredToken()) {
      setQuoteRecord(null);
      return;
    }
    let active = true;
    setQuoteLoading(true);
    setQuoteError('');
    const timer = setTimeout(async () => {
      setQuoteLoading(true);
      setQuoteError('');
      const payload = buildOrderPayload();
      try {
        const res = await quoteOrder(payload);
        if (!active) return;
        if (res.code === 0 && res.data) {
          setQuoteRecord({ key: quoteKey, data: res.data });
        } else {
          setQuoteRecord(null);
          setQuoteError(res.msg || '价格试算失败');
        }
      } catch {
        if (active) {
          setQuoteRecord(null);
          setQuoteError('价格试算失败，请检查网络');
        }
      } finally {
        if (active) setQuoteLoading(false);
      }
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [quoteKey]);

  useEffect(() => { if (selectedService?.gameplay_kind === 'ROULETTE') setGameRounds(1); }, [selectedService?.id]);

  const totalAmount = quote?.amount ?? originalAmount;
  const totalDiscount = quote
    ? quote.boss_discount + quote.promo_discount + quote.coupon_discount
    : 0;

  // 切到平台派单：清除指定陪玩；切到指定陪玩但无人选：引导回选择页
  const handleSelectPlatform = () => {
    setDispatchMode('platform');
    setProviderId(null);
    setSelectedEscort(null);
  };

  const handleSelectAssign = () => {
    if (!providerId) {
      handleReselectEscort();
      return;
    }
    setDispatchMode('assign');
  };

  const handleReselectEscort = () => {
    const url = selectedService
      ? `/pages/players/index?serviceId=${selectedService.id}&game_rounds=${gameRounds}&fromCheckout=1&provider_gender=${providerGender}`
      : '/pages/players/index';
    Taro.navigateTo({ url, events: {
      providerSelected: (escort: EscortProfile | null) => {
        setSelectedEscort(escort); setProviderId(escort?.id || null);
        setDispatchMode(escort ? 'assign' : 'platform');
      },
    } });
  };

  const handleRecharge = async () => {
    const result = await openWecomCustomerService({ cardId: supportContactId || undefined, title: '充值兴安币', path: '/pages/checkout/index' });
    if (!result.opened) Taro.navigateTo({ url: `/pages/serviceCard/index?scene=recharge&cardId=${supportContactId || ''}` });
  };

  const handleSubmit = async () => {
    if (submitting) return;

    // 下单前登录守卫：未登录弹出微信快捷登录窗，登录后由用户再次点击提交
    if (!ensureLogin()) return;

    if (!gameAccount.region.trim() || !gameAccount.nickname.trim() || !gameAccount.uid.trim()) {
      Taro.showToast({ title: '请填写游戏大区、昵称和UID', icon: 'none' });
      return;
    }

    if (!selectedService) {
      Taro.showToast({ title: '请选择服务', icon: 'none' });
      return;
    }

    if (!supportContactId) {
      Taro.showToast({ title: '请选择负责订单的客服', icon: 'none' });
      return;
    }

    if (quoteLoading) {
      Taro.showToast({ title: '正在核算订单价格', icon: 'none' });
      return;
    }
    if (!quote) {
      Taro.showToast({ title: quoteError || '请等待价格核算完成', icon: 'none' });
      return;
    }
    if (!quote.balance_sufficient) {
      const result = await Taro.showModal({
        title: '兴安币不足',
        content: `需支付 ${formatXaCoin(quote.amount)} 兴安币，当前余额 ${formatXaCoin(quote.wallet_balance)} 兴安币。`,
        confirmText: '联系企微客服',
      });
      if (result.confirm) {
        const serviceResult = await openWecomCustomerService({
          cardId: supportContactId || undefined,
          title: '订单余额不足，需要充值兴安币',
          path: '/pages/checkout/index',
        });
        if (!serviceResult.opened) Taro.navigateTo({ url: `/pages/serviceCard/index?scene=recharge&cardId=${supportContactId || ''}` });
      }
      return;
    }

    const payload = buildOrderPayload({ includeGameAccount: true });

    const confirmation = await Taro.showModal({
      title: '确认提交订单',
      content: `本次将从钱包支付 ${formatXaCoin(quote.amount)} 兴安币，${dispatchMode === 'assign' ? '由指定大神接单' : autoGrab ? '由合格陪玩直接抢单' : '陪玩申请后由你选择'}，由${supportContacts.find(item => item.id === supportContactId)?.name || '所选客服'}负责跟进。`,
      confirmText: '确认支付',
      confirmColor: '#002fa7',
    });
    if (!confirmation.confirm) return;

    try {
      setSubmitting(true);
      const response = await createOrder(payload);

      if (response?.code !== 0) {
        Taro.showToast({ title: response?.msg || '下单失败，请稍后重试', icon: 'none' });
        return;
      }

      // 下单会扣款并改变订单统计，主动让缓存失效，下次进入相关页面必然拉新
      useWalletStore.getState().invalidate();
      useOrderStore.getState().invalidate();

      const createdOrderId = response.data?.id;
      const isAssign = dispatchMode === 'assign' && !!providerId;
      const assignName = selectedEscort?.nickname ? `「${selectedEscort.nickname}」` : '指定大神';
      Taro.showToast({ title: '下单成功', icon: 'success' });
      Taro.showModal({
        title: '订单已提交',
        content: isAssign
          ? `已为你指定陪玩${assignName}，等待对方接单，可在"我的订单"中查看进度。`
          : '订单已进入待接单，将由陪玩师主动接单或客服为你指派，请在"我的订单"中留意状态。',
        showCancel: false,
        success: () => {
          Taro.redirectTo({
            url: createdOrderId
              ? `/pages/orderList/index?orderId=${createdOrderId}`
              : '/pages/orderList/index',
          });
        }
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '下单失败，请稍后重试';
      Taro.showToast({ title: message, icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  if (catalogLoading) {
    return (
      <View className={styles.container}>
        <Skeleton variant="card-list" rows={4} />
      </View>
    );
  }

  if (!services.length) return <View className={styles.container}><Empty title="服务暂不可下单" desc="服务可能已下架或加载失败，请返回重新选择" /><Button onClick={() => Taro.switchTab({ url: '/pages/category/index' })}>返回玩法目录</Button></View>;

  return (
    <View className={styles.container}>
      <View className={styles.content}>
        <View className={styles.section}>
          <Text className={styles.sectionTitle}>{guidedFlow ? '确认商品' : '选择服务'}</Text>
          {services.map(service => {
            const isSelected = selectedService?.id === service.id;
            return (
              <View
                key={service.id}
                className={`${styles.serviceModeBox} ${isSelected ? styles.serviceModeBoxActive : ''}`}
                onClick={() => { if (!guidedFlow) setSelectedService(service); }}
              >
                <GameplayCover size="thumb" name={service.gameplay_name || service.name} theme={service.gameplay_theme} cover={service.cover_url || service.gameplay_cover_url} />
                <View className={styles.serviceModeMain}>
                  <Text className={styles.serviceModeName}>{service.name}</Text>
                  <Text className={styles.serviceModeDesc}>{service.gameplay ? `${service.platform_name} / ${service.security_level_name}` : service.description}</Text>
                  <Text className={styles.price}>{formatXaCoin(service.price)}兴安币</Text>
                </View>
                {isSelected && !guidedFlow && <Icon name="check-circle" size={40} color="#002fa7" className={styles.serviceModeCheck} />}
              </View>
            );
          })}
        </View>

        {selectedService?.gameplay_kind === 'ROULETTE' && <View className={styles.section}>
          <Text className={styles.serviceModeDesc}>每单 1 个服务目标，支付成功时按配置概率确定，结果会保存到订单并用于派单履约。</Text>
          <Text style={{ display: 'block' }} className={styles.price} onClick={() => Taro.navigateTo({ url: `/pages/productDetail/index?id=${selectedService.id}` })}>查看转盘目标、概率与规则 →</Text>
        </View>}

        {selectedService && <View className={styles.section}>
          {selectedService.service_type === 'COMPANION' && <Picker range={['不限', '男陪', '女陪']} value={['ANY','MALE','FEMALE'].indexOf(providerGender)} disabled={submitting} onChange={event => {
            const gender = (['ANY','MALE','FEMALE'] as const)[Number(event.detail.value)];
            if (!gender) return;
            setProviderGender(gender);
            if (gender !== 'ANY' && selectedEscort?.gender !== gender) { setProviderId(null); setSelectedEscort(null); setDispatchMode('platform'); }
          }}><Text className={styles.sectionTitle}>陪玩选择：{{ANY:'不限', MALE:'男陪', FEMALE:'女陪'}[providerGender]} ›</Text></Picker>}
          {(['recent','recommended'] as const).map(group => <View key={group}>
            <Text className={styles.sectionTitle}>{group === 'recent' ? '最近点过' : '推荐陪玩'}</Text>
            <View style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '8px' }}>
              {shortcuts[group].map(provider => <Button key={provider.id} disabled={submitting || !provider.can_order} onClick={() => { setProviderId(provider.id); setSelectedEscort(provider); setDispatchMode('assign'); }}>{provider.nickname}{provider.can_order ? '' : '（暂不可接）'}</Button>)}
            </View>
            {!shortcuts[group].length && <Text className={styles.dispatchDesc}>暂无符合当前选择的陪玩</Text>}
          </View>)}
        </View>}
        <View className={styles.section}>
          <Text className={styles.sectionTitle}>接单方式</Text>
          <View
            className={`${styles.dispatchOption} ${dispatchMode === 'assign' ? styles.dispatchOptionActive : ''}`}
            onClick={handleSelectAssign}
          >
            <View className={styles.dispatchMain}>
              <View className={styles.dispatchHead}>
                <Text className={styles.dispatchName}>指定陪玩</Text>
                {dispatchMode === 'assign' && (
                  <View className={styles.dispatchBadge}>
                    <Icon name="check" size={24} color="#fffdf5" strokeWidth={3} />
                    <Text className={styles.dispatchBadgeText}>已选</Text>
                  </View>
                )}
              </View>
              {selectedEscort ? (
                <Text className={styles.dispatchDesc}>
                  已指定「{selectedEscort.nickname}」{selectedEscort.rank ? ` · ${selectedEscort.rank}` : ''}，下单后等待其接单
                </Text>
              ) : (
                <Text className={styles.dispatchDesc}>自己挑选心仪大神，由其专属服务</Text>
              )}
            </View>
            {dispatchMode === 'assign' && (
              <View className={styles.dispatchReselect} onClick={event => { event.stopPropagation(); handleReselectEscort(); }}>
                <Text className={styles.dispatchReselectText}>重新选择</Text>
                <Icon name="chevron-right" size={24} color="#002fa7" />
              </View>
            )}
          </View>

          <View
            className={`${styles.dispatchOption} ${dispatchMode === 'platform' ? styles.dispatchOptionActive : ''}`}
            onClick={handleSelectPlatform}
          >
            <View className={styles.dispatchMain}>
              <View className={styles.dispatchHead}>
                <Text className={styles.dispatchName}>{autoGrab ? '直接抢单' : '老板选人'}</Text>
                {dispatchMode === 'platform' && (
                  <View className={styles.dispatchBadge}>
                    <Icon name="check" size={24} color="#fffdf5" strokeWidth={3} />
                    <Text className={styles.dispatchBadgeText}>已选</Text>
                  </View>
                )}
              </View>
              <Text className={styles.dispatchDesc}>
                {autoGrab ? '合格陪玩抢到后直接接单，适合单人服务' : '陪玩申请后，由你在订单详情选择候选人'}
              </Text>
              <Button disabled={submitting} onClick={event => { event.stopPropagation(); setAutoGrab(value => !value); }}>切换为{autoGrab ? '老板选人' : '直接抢单'}</Button>
            </View>
          </View>
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>负责订单的客服</Text>
          {supportContacts.map(contact => {
            const selected = supportContactId === contact.id;
            return (
              <View
                key={contact.id}
                className={`${styles.dispatchOption} ${selected ? styles.dispatchOptionActive : ''}`}
                onClick={() => setSupportContactId(contact.id)}
              >
                <View className={styles.dispatchMain}>
                  <Text className={styles.dispatchName}>{contact.name}</Text>
                  <Text className={styles.dispatchDesc}>{contact.tips || contact.company || '负责订单进度跟进与售后服务'}</Text>
                </View>
                {selected && <Icon name="check-circle" size={40} color="#002fa7" />}
              </View>
            );
          })}
          {supportContacts.length === 0 && (
            <Text className={styles.couponEmpty}>暂无可选客服，请联系平台处理</Text>
          )}
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>
            {selectedService?.game_category_name
              ? `${selectedService.game_category_name}账号信息`
              : '游戏账号信息'}
          </Text>
          <View className={styles.formItem}>
            <Text className={styles.formLabel}>游戏大区</Text>
            <Input
              className={styles.formInput}
              value={gameAccount.region}
              placeholder="例如：微信区 / QQ区 / 国服 / 国际服"
              onInput={(event) => setGameAccount(prev => ({ ...prev, region: event.detail.value }))}
            />
          </View>
          <View className={styles.formItem}>
            <Text className={styles.formLabel}>游戏昵称</Text>
            <Input
              className={styles.formInput}
              value={gameAccount.nickname}
              placeholder="请输入老板的游戏昵称"
              onInput={(event) => setGameAccount(prev => ({ ...prev, nickname: event.detail.value }))}
            />
          </View>
          <View className={styles.formItem}>
            <Text className={styles.formLabel}>游戏UID</Text>
            <Input
              className={styles.formInput}
              value={gameAccount.uid}
              placeholder="请输入游戏UID / 数字ID"
              type="text"
              onInput={(event) => setGameAccount(prev => ({ ...prev, uid: event.detail.value }))}
            />
          </View>
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>服务数量</Text>
          <View className={styles.stepperRow}>
            <Text className={styles.formLabel}>{selectedService?.gameplay_kind === 'ROULETTE' ? '转盘服务目标' : '购买数量（份）'}</Text>
            {selectedService?.gameplay_kind === 'ROULETTE' ? <Text className={styles.formLabel}>1 个（每单一次）</Text> : <View className={styles.stepper}>
              <View
                className={`${styles.stepperBtn} ${gameRounds <= 1 ? styles.stepperBtnDisabled : ''}`}
                onClick={() => setGameRounds(gameRounds > 1 ? gameRounds - 1 : 1)}
              >
                <Icon name="minus" size={24} color={gameRounds <= 1 ? '#AEAEB2' : '#1D1D1F'} strokeWidth={2.5} />
              </View>
              <Input
                className={styles.stepperInput}
                disabled={selectedService?.gameplay_kind === 'ROULETTE'}
                value={String(gameRounds)}
                type="number"
                placeholder="1"
                onInput={(event) => {
                  const val = parseInt(event.detail.value, 10);
                  setGameRounds(val > 0 ? val : 1);
                }}
              />
              <View
                className={styles.stepperBtn}
                onClick={() => setGameRounds(selectedService?.gameplay_kind === 'ROULETTE' ? 1 : gameRounds + 1)}
              >
                <Icon name="plus" size={24} color="#002fa7" strokeWidth={2.5} />
              </View>
            </View>}
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.couponTitleRow}>
            <Icon name="ticket" size={32} color="#002fa7" />
            <Text className={styles.sectionTitle}>优惠券</Text>
          </View>
          {usableCoupons.length === 0 ? (
            <Text className={styles.couponEmpty}>暂无可用优惠券</Text>
          ) : (
            <>
              <View
                className={`${styles.couponOption} ${selectedCouponId === null ? styles.couponOptionActive : ''}`}
                onClick={() => setSelectedCouponId(null)}
              >
                <Text className={styles.couponOptionName}>不使用优惠券</Text>
                {selectedCouponId === null && (
                  <Icon name="check-circle" size={40} color="#002fa7" />
                )}
              </View>
              {usableCoupons.map(coupon => {
                const isSelected = selectedCouponId === coupon.id;
                return (
                  <View
                    key={coupon.id}
                    className={`${styles.couponOption} ${isSelected ? styles.couponOptionActive : ''}`}
                    onClick={() => setSelectedCouponId(coupon.id)}
                  >
                    <View className={styles.couponOptionMain}>
                      <Text className={styles.couponOptionName}>{coupon.name}</Text>
                      <Text className={styles.couponOptionDesc}>
                        {coupon.discount_type === 'QUALIFICATION' ? '指定套餐 · 核销后不返券' : coupon.discount_type === 'DIRECT'
                          ? '无门槛'
                          : `满${formatXaCoin(coupon.threshold)}兴安币可用`}
                      </Text>
                    </View>
                    <Text className={styles.couponOptionAmount}>{coupon.discount_type === 'QUALIFICATION' ? '资格券' : `-${formatXaCoin(coupon.amount)}币`}</Text>
                    {isSelected && (
                      <Icon name="check-circle" size={40} color="#002fa7" className={styles.couponOptionCheck} />
                    )}
                  </View>
                );
              })}
            </>
          )}
        </View>

        <View className={styles.section}>
          <Text className={styles.sectionTitle}>订单备注</Text>
          <Textarea
            className={styles.remark}
            value={remark}
            maxlength={80}
            placeholder="例如：想冲分、需要教学、指定英雄/地图、是否介意多人车队"
            onInput={(event) => setRemark(event.detail.value)}
          />
        </View>

        <View className={styles.section}>
          <View className={styles.priceSummaryHead}>
            <Text className={styles.sectionTitle}>费用明细</Text>
            <Text className={styles.quoteState}>{quoteLoading ? '正在计算优惠…' : '已计入可用优惠'}</Text>
          </View>
          <View className={styles.priceLine}>
            <Text>商品金额</Text>
            <Text>{formatXaCoin(quote?.original_amount ?? originalAmount)}兴安币</Text>
          </View>
          {!!quote?.boss_discount && (
            <View className={styles.priceLineDiscount}><Text>老板等级优惠</Text><Text>-{formatXaCoin(quote.boss_discount)}币</Text></View>
          )}
          {!!quote?.promo_discount && (
            <View className={styles.priceLineDiscount}><Text>{quote.promotion?.title || '限时活动'}</Text><Text>-{formatXaCoin(quote.promo_discount)}币</Text></View>
          )}
          {!!quote?.coupon_discount && (
            <View className={styles.priceLineDiscount}><Text>优惠券</Text><Text>-{formatXaCoin(quote.coupon_discount)}币</Text></View>
          )}
          {quoteError && <Text className={styles.quoteError} onClick={() => setRefresh(value => value + 1)}>{quoteError}，点击重试</Text>}
          {quote && (
            <View className={styles.balanceLine}>
              <Text>钱包余额</Text>
              <Text className={quote.balance_sufficient ? styles.balanceEnough : styles.balanceInsufficient}>
                {formatXaCoin(quote.wallet_balance)}兴安币{quote.balance_sufficient ? '' : '（不足）'}
              </Text>
            </View>
          )}
        </View>
        <View className={styles.section}>
          <View className={styles.paymentHeader}><Text className={styles.sectionTitle}>支付方式</Text><Text className={styles.paymentBadge}>兴安币余额</Text></View>
          <Text className={styles.serviceModeDesc}>请联系人工客服付款，充值到账后即可使用余额下单。</Text>
          <Button className={styles.rechargeBtn} onClick={handleRecharge}>联系客服充值</Button>
        </View>
      </View>

      <View className={styles.footer}>
        <View className={styles.totalBlock}>
          <Text className={styles.totalLabel}>合计</Text>
          <Text className={styles.totalPrice}>{formatXaCoin(totalAmount)}兴安币</Text>
          {totalDiscount > 0 && (
            <Text className={styles.discountText}>已优惠 {formatXaCoin(totalDiscount)}兴安币</Text>
          )}
        </View>
        <Button
          className={styles.submitBtn}
          disabled={submitting || quoteLoading || !quote}
          onClick={handleSubmit}
        >
          <Text>{submitting ? '提交中...' : quoteLoading ? '核算中...' : '余额支付'}</Text>
        </Button>
      </View>
      {loginSheet}
    </View>
  );
};

export default CheckoutPage;
