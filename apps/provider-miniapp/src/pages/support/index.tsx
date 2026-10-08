import { useState } from 'react';
import { Button, Image, Text, View } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { ApiResponse, request } from '@/utils/request';
import PlatformLayout from '@/components/PlatformLayout';
import PageNav from '@/components/PageNav';
import styles from './index.module.scss';

type Card = {
  name: string; wechat_id: string; wecom_service_url: string; qrcode_url: string;
  tips: string; customer_reference: string;
};

export default function SupportPage() {
  const [card, setCard] = useState<Card | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const result = await request<ApiResponse<Card | null>>('/support/contact-card/');
      if (result.code !== 0) throw new Error(result.msg || '客服信息加载失败');
      setCard(result.data || null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '客服信息加载失败'); }
    finally { setLoading(false); }
  };
  useDidShow(load);
  const copy = async (data: string) => {
    try { await Taro.setClipboardData({ data }); }
    catch { Taro.showToast({ title: '复制失败，请手动复制页面信息', icon: 'none' }); }
  };
  return <PlatformLayout active='wallet'><View className={`xa-provider-page ${styles.container}`}>
    <PageNav title="客服与帮助" fallbackUrl="/pages/wallet/index" />
    <View className={styles.card}>
      <Text className={styles.title}>客服充值与钱包咨询</Text>
      <Text className={styles.description}>先与客服确认收款方式和金额，付款后由客服凭收款流水在后台充值。1 元人民币兑换 10 兴安币，到账后可缴押金或购买通行证。</Text>
      {loading ? <Text>正在加载客服信息…</Text> : error ? <><Text>{error}</Text><Button onClick={load}>重试</Button></> : card ? <>
        <Text className={styles.label}>接待客服：{card.name}</Text>
        <Text className={styles.label} selectable>打手编号：{card.customer_reference}</Text>
        <Button onClick={() => copy(`打手编号：${card.customer_reference}，咨询充值/钱包事项`)}>复制账号信息给客服</Button>
        {card.qrcode_url && <Image src={card.qrcode_url} className={styles.qrcode} mode='aspectFit' showMenuByLongpress onClick={() => Taro.previewImage({ urls: [card.qrcode_url] })} />}
        {card.wechat_id && <><Text className={styles.label} selectable>客服微信：{card.wechat_id}</Text><Button onClick={() => copy(card.wechat_id)}>复制客服微信</Button></>}
        {card.wecom_service_url && <><Text className={styles.label} selectable>{card.wecom_service_url}</Text><Button onClick={() => copy(card.wecom_service_url)}>复制企业微信客服链接</Button></>}
        {card.tips && <Text className={styles.description}>{card.tips}</Text>}
        {!card.qrcode_url && !card.wechat_id && !card.wecom_service_url && <Text className={styles.description}>客服联系方式尚未配置，请联系俱乐部管理员补充。</Text>}
      </> : <Text>当前未配置接待客服，请联系俱乐部管理员。</Text>}
      <Button onClick={() => Taro.switchTab({ url: '/pages/wallet/index' })}>返回钱包查看到账</Button>
    </View>
  </View></PlatformLayout>;
}
