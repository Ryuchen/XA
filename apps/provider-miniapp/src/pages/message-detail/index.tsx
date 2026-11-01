import React, { useEffect, useRef, useState } from 'react';
import { Button, Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchMessageDetail, SiteMessage } from '@/services/message';
import styles from './index.module.scss';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';
import { clearOrderFocus, queueOrderFocus, queueOrderPool } from '@/utils/orderNavigation';
import { clearReportOrder, queueReportOrder } from '@/utils/reportNavigation';
import { getSessionRevision } from '@/utils/auth';

const MessageDetailPage: React.FC = () => {
  const [message, setMessage] = useState<SiteMessage | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [sessionChanged, setSessionChanged] = useState(false);
  const viewSession = useRef(getSessionRevision());
  useEffect(() => {
    const session = viewSession.current;
    let active = true;
    const onAuthChanged = () => {
      if (getSessionRevision() === session) return;
      setMessage(null); setLoaded(true); setSessionChanged(true);
    };
    Taro.eventCenter.on('xa:auth.changed', onAuthChanged);
    const id = Taro.getCurrentInstance().router?.params?.id;
    if (!id) setLoaded(true);
    else fetchMessageDetail(id).then(res => { if (active && session === getSessionRevision() && res.code === 0 && res.data) { setMessage(res.data); Taro.eventCenter.trigger('xa:messages.changed'); } }).catch(() => { if (active && session === getSessionRevision()) setMessage(null); }).finally(() => { if (active && session === getSessionRevision()) setLoaded(true); });
    return () => { active = false; Taro.eventCenter.off('xa:auth.changed', onAuthChanged); };
  }, []);
  const isCurrent = () => viewSession.current === getSessionRevision();
  const openOrder = async () => {
    if (!isCurrent()) return;
    const id = message?.related_order_id;
    if (!Number.isSafeInteger(id) || !id || id < 1) return;
    if (message?.action_url === '/pages/orders/index?tab=pool' || message?.title === '队友已撤回组队报名') {
      queueOrderPool();
      try { await Taro.switchTab({ url: '/pages/orders/index' }); }
      catch { clearOrderFocus(); Taro.showToast({ title: '抢单池暂时无法打开', icon: 'none' }); }
      return;
    }
    const application = /^\/pages\/orders\/index\?application_id=([1-9]\d*)$/.exec(message?.action_url || '');
    if (application || message?.action_url === '/pages/orders/index?tab=pending') {
      try { await Taro.navigateTo({ url: `/pages/application-detail/index?orderId=${id}${application ? `&applicationId=${application[1]}` : ''}` }); }
      catch { Taro.showToast({ title: '报名详情暂时无法打开', icon: 'none' }); }
      return;
    }
    queueOrderFocus(id);
    try { await Taro.switchTab({ url: '/pages/orders/index' }); }
    catch { clearOrderFocus(); Taro.showToast({ title: '订单页面暂时无法打开', icon: 'none' }); }
  };
  const openWallet = async () => {
    if (!isCurrent()) return;
    try { await Taro.switchTab({ url: '/pages/wallet/index' }); }
    catch { Taro.showToast({ title: '钱包暂时无法打开', icon: 'none' }); }
  };
  const openReport = async () => {
    if (!isCurrent()) return;
    if (message?.related_order_id) queueReportOrder(message.related_order_id);
    try { await Taro.switchTab({ url: '/pages/report/index' }); }
    catch { clearReportOrder(); Taro.showToast({ title: '报单页面暂时无法打开', icon: 'none' }); }
  };
  const reservationUrl = /^\/pages\/reservations\/index\?id=[1-9]\d*$/.test(message?.action_url || '') ? message!.action_url : '';
  const openReservation = async () => {
    if (!isCurrent()) return;
    if (!reservationUrl) return;
    try { await Taro.navigateTo({ url: reservationUrl }); }
    catch { Taro.showToast({ title: '预约暂时无法打开', icon: 'none' }); }
  };
  return <PlatformLayout active='messages'><View className={`xa-provider-page ${styles.page}`}><PageNav title="消息详情" fallbackUrl="/pages/messages/index" />{!loaded || !message ? <View className={styles.card}><Text className={styles.empty}>{sessionChanged ? '登录已变化，请返回消息列表' : loaded ? '消息不存在或暂时无法加载' : '加载中…'}</Text></View> : <View className={styles.card}><Text className={styles.title}>{message.title}</Text><Text className={styles.time}>{new Date(message.created_at).toLocaleString()}</Text><View className={styles.divider} /><Text className={styles.preview}>{message.preview}</Text>{(message.detail || '').split(/\n+/).filter(Boolean).map((line, i) => <Text className={styles.paragraph} key={i}>{line}</Text>)}{reservationUrl && <Button className={styles.orderButton} onClick={openReservation}>查看预约</Button>}{message.action_url === '/pages/report/index' && <Button className={styles.orderButton} onClick={openReport}>查看报单</Button>}{!!message.related_order_id && <Button className={styles.orderButton} onClick={openOrder}>{message.action_url === '/pages/orders/index?tab=pool' || message.title === '队友已撤回组队报名' ? '前往抢单池' : ['报名订单已取消', '报名结果已更新'].includes(message.title) ? '查看报名' : /\?application_id=[1-9]\d*$/.test(message.action_url || '') || message.action_url === '/pages/orders/index?tab=pending' ? '查看组队报名' : '查看关联订单'}</Button>}{!message.related_order_id && message.action_url === '/pages/wallet/index' && <Button className={styles.orderButton} onClick={openWallet}>前往钱包</Button>}</View>}</View></PlatformLayout>;
};
export default MessageDetailPage;
