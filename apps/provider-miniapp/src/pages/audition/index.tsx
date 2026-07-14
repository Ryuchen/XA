import React, { useEffect, useState } from 'react';
import { Button, Input, ScrollView, Text, Textarea, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import {
  AuditionInfo,
  fetchAuditionInfo,
  fetchMyAuditionSignups,
  submitAuditionSignup,
  exchangeAuditionToken,
} from '@/services/audition';
import { getStoredToken, persistLoginData } from '@/utils/auth';
import { Empty, Loading } from '@/components';
import styles from './index.module.scss';
import PlatformLayout from '@/components/PlatformLayout';
import PageNav from '@/components/PageNav';

type ViewState = 'loading' | 'ready' | 'error';

const formatExpire = (value: string | null) => {
  if (!value) return '长期有效';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};

const AuditionPage: React.FC = () => {
  const [state, setState] = useState<ViewState>('loading');
  const [token, setToken] = useState('');
  const [info, setInfo] = useState<AuditionInfo | null>(null);
  const [error, setError] = useState('链接无效');
  const [contact, setContact] = useState('');
  const [game, setGame] = useState('');
  const [remark, setRemark] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [mySignups, setMySignups] = useState<any[]>([]);

  const loadMySignups = async () => {
    if (!getStoredToken()) return;
    const res = await fetchMyAuditionSignups();
    if (res.code === 0 && res.data) setMySignups(res.data);
  };

  useEffect(() => {
    const params = Taro.getCurrentInstance().router?.params || {};
    const linkToken = params.token || '';
    const role = params.role || 'provider';
    if (!linkToken || role !== 'provider') {
      setError('链接参数缺失或无效');
      setState('error');
      return;
    }
    setToken(linkToken);
    fetchAuditionInfo(linkToken)
      .then(async res => {
        if (res.code !== 0 || !res.data) {
          setError(res.msg || '链接已失效');
          setState('error');
          return;
        }
        setInfo(res.data);
        // 专属链接可直接换发登录态；普通招募链接则保留给已登录陪玩报名。
        if (!getStoredToken()) {
          const exchange = await exchangeAuditionToken(linkToken);
          if (exchange.code === 0 && exchange.data) persistLoginData(exchange.data);
        }
        await loadMySignups();
        setState('ready');
      })
      .catch(() => { setError('网络异常，请稍后再试'); setState('error'); });
  }, []);

  const goLogin = () => {
    const redirect = encodeURIComponent(`/pages/audition/index?token=${encodeURIComponent(token)}&role=provider`);
    Taro.redirectTo({ url: `/pages/login/index?redirect=${redirect}` });
  };

  const submit = async () => {
    if (!getStoredToken()) { goLogin(); return; }
    if (!contact.trim() || !game.trim()) {
      Taro.showToast({ title: '请填写联系方式和擅长游戏', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const res = await submitAuditionSignup({ token, contact: contact.trim(), game: game.trim(), remark: remark.trim() });
      if (res.code === 0) {
        Taro.showToast({ title: '报名已提交', icon: 'success' });
        await loadMySignups();
      } else {
        Taro.showToast({ title: res.msg || '报名失败', icon: 'none' });
      }
    } catch (err) {
      Taro.showToast({ title: err instanceof Error ? err.message : '报名失败', icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  if (state === 'loading') return <Loading fullscreen text="加载试音活动…" />;
  if (state === 'error' || !info) return <View className={styles.center}><Empty icon="🎧" title="无法打开试音链接" desc={error} /></View>;

  const hasSubmitted = mySignups.some(item => item.link === info.id);
  return (
    <PlatformLayout active='mine'><ScrollView className={styles.container} scrollY>
      <PageNav title="试音报名" />
      <View className={styles.hero}><Text className={styles.tag}>陪玩招募</Text><Text className={styles.title}>{info.title}</Text><Text className={styles.remark}>{info.remark || '欢迎报名本次试音活动'}</Text><Text className={styles.expire}>截止：{formatExpire(info.expire_at)}</Text></View>
      {!getStoredToken() ? (
        <View className={styles.card}><Text className={styles.notice}>请先使用陪玩账号登录后报名。专属链接已绑定账号时会自动登录。</Text><Button className={styles.primaryButton} onClick={goLogin}>去登录</Button></View>
      ) : hasSubmitted ? (
        <View className={styles.card}><Text className={styles.notice}>你已报名本次试音活动，请等待运营审核。</Text></View>
      ) : (
        <View className={styles.card}>
          <Text className={styles.sectionTitle}>提交报名信息</Text>
          <Text className={styles.label}>联系方式</Text><Input className={styles.input} value={contact} maxlength={100} placeholder="微信号或手机号" onInput={e => setContact(e.detail.value)} />
          <Text className={styles.label}>擅长游戏</Text><Input className={styles.input} value={game} maxlength={100} placeholder="例如：王者荣耀" onInput={e => setGame(e.detail.value)} />
          <Text className={styles.label}>补充说明（选填）</Text><Textarea className={styles.textarea} value={remark} maxlength={255} placeholder="段位、可接档期、试音经验等" autoHeight onInput={e => setRemark(e.detail.value)} />
          <Button className={styles.primaryButton} loading={submitting} disabled={submitting || !contact.trim() || !game.trim()} onClick={submit}>提交报名</Button>
        </View>
      )}
      {mySignups.length > 0 && <View className={styles.card}><Text className={styles.sectionTitle}>我的报名记录</Text>{mySignups.map(item => <View key={item.id} className={styles.record}><View><Text className={styles.recordTitle}>{item.link_title}</Text><Text className={styles.recordSub}>{item.game} · {item.contact}</Text>{item.audit_remark && <Text className={styles.recordSub}>审核备注：{item.audit_remark}</Text>}</View><Text className={styles.status}>{item.status_display}</Text></View>)}</View>}
    </ScrollView></PlatformLayout>
  );
};

export default AuditionPage;
