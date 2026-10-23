import React, { useEffect, useState } from 'react';
import { View, Text, Image, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import Icon from '@/components/Icon';
import { fetchContactCard, openWecomCustomerService, SupportContactCard } from '@/services/support';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const PRIMARY_COLOR = '#002fa7';

const FALLBACK_CARD: SupportContactCard = {
  id: 0,
  name: '客服暂未配置',
  company: '兴安电竞官方陪玩平台',
  wechat_id: '',
  wecom_corp_id: '',
  wecom_service_url: '',
  avatar_url: '',
  qrcode_url: '',
  tips: '客服入口暂不可用，请稍后重试。',
};

const ServiceCardPage: React.FC = () => {
  const [card, setCard] = useState<SupportContactCard>(FALLBACK_CARD);
  const [opening, setOpening] = useState(false);
  const params = Taro.getCurrentInstance().router?.params || {};
  const isRecharge = params.scene === 'recharge';
  const context = { orderId: Number(params.orderId) || undefined, cardId: Number(params.cardId) || undefined };

  useEffect(() => {
    fetchContactCard(context).then(res => {
      if (res.code === 0 && res.data) {
        setCard(res.data);
      }
    }).catch(() => Taro.showToast({ title: '客服信息加载失败，请稍后重试', icon: 'none' }));
  }, []);

  const handleCopyWechatId = () => {
    Taro.setClipboardData({
      data: card.wechat_id,
      success: () => {
        Taro.showToast({
          title: '微信号已复制',
          icon: 'success'
        });
      }
    });
  };

  const handleOpenWecom = async () => {
    if (opening) return;
    setOpening(true);
    const result = await openWecomCustomerService({
      ...context,
      title: isRecharge ? '我要充值兴安币' : '我要咨询玩法服务',
      path: `/pages/serviceCard/index${isRecharge ? '?scene=recharge' : ''}`,
    });
    setOpening(false);
    if (!result.opened) {
      Taro.showToast({
        title: result.reason === 'unsupported' ? '请扫码或复制微信号联系客服' : '企业微信客服暂时无法打开',
        icon: 'none',
        duration: 2500,
      });
    }
  };

  const handleClose = () => {
    if (isRecharge && Taro.getCurrentPages().length > 1) {
      Taro.navigateBack();
      return;
    }
    Taro.switchTab({
      url: '/pages/home/index'
    });
  };

  const tipsLines = (card.tips || '').split(/\n+/).filter(Boolean);

  return (
    <View className={styles.container}>
      <View className={styles.header}>
        <View className={styles.headerIcon}>
          <Icon name="headphones" size={48} color={PRIMARY_COLOR} />
        </View>
        <Text className={styles.title}>{isRecharge ? '联系客服充值' : '咨询玩法服务'}</Text>
        <Text className={styles.subtitle}>{isRecharge ? '人工客服收款、后台充值，余额到账后返回确认订单' : '进入企业微信客服，咨询方案、下单与售后'}</Text>
      </View>

      <View className={styles.card}>
        <View className={styles.serviceInfo}>
          {card.avatar_url && <Image
            className={styles.avatar}
            src={resolveImageUrl(card.avatar_url)}
            mode="aspectFill"
          />}
          <View className={styles.info}>
            <Text className={styles.name}>{card.name}</Text>
            <Text className={styles.company}>{card.company}</Text>
            {card.wechat_id && <View className={styles.wechatId}>
              <Icon name="message-circle" size={24} color={PRIMARY_COLOR} />
              <Text className={styles.wechatIdText}>微信号: {card.wechat_id}</Text>
            </View>}
          </View>
        </View>

        {card.qrcode_url && <View className={styles.qrcode}>
          <Text className={styles.qrcodeTitle}>长按识别二维码添加客服</Text>
          <Image
            className={styles.qrcodeImage}
            src={resolveImageUrl(card.qrcode_url)}
            mode="aspectFill"
            showMenuByLongpress
          />
        </View>}

        {card.customer_reference && <View className={styles.tips}>
          <Text className={styles.referenceText}>我的客服识别编号：{card.customer_reference}</Text>
          <View className={styles.referenceCopyBtn} onClick={() => Taro.setClipboardData({ data: `老板编号 ${card.customer_reference}${card.order_no ? `，订单 ${card.order_no}` : ''}` })}>复制给客服</View>
        </View>}

        {tipsLines.length > 0 && (
          <View className={styles.tips}>
            <View className={styles.tipsHeader}>
              <Icon name="info" size={28} color={PRIMARY_COLOR} />
              <Text className={styles.tipsTitle}>{isRecharge ? '充值温馨提示' : '客服温馨提示'}</Text>
            </View>
            {tipsLines.map((line, idx) => (
              <View key={idx} className={styles.tipsItem}>
                <Icon
                  name="check-circle"
                  size={28}
                  color={PRIMARY_COLOR}
                  className={styles.tipsIcon}
                />
                <Text className={styles.tipsText}>{line}</Text>
              </View>
            ))}
          </View>
        )}
      </View>

      <View className={styles.actions}>
        <Button className={styles.copyBtn} disabled={!card.wecom_service_url} loading={opening} onClick={handleOpenWecom}>
          <Icon name="message-circle" size={32} color="#fffdf5" />
          <Text className={styles.btnText}>{opening ? '正在连接' : '打开企业微信客服'}</Text>
        </Button>
        {card.wechat_id && <Button className={styles.addWechatBtn} onClick={handleCopyWechatId}>
          <Text className={styles.btnText}>复制客服微信号</Text>
          <Icon name="credit-card" size={32} color={PRIMARY_COLOR} />
        </Button>}
        <Button className={styles.closeBtn} onClick={handleClose}>
          {isRecharge && Taro.getCurrentPages().length > 1 ? '返回上一页' : '返回首页'}
        </Button>
      </View>
    </View>
  );
};

export default ServiceCardPage;
