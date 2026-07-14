import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import {
  exchangeAuditionToken,
  fetchAuditionInfo,
  AuditionInfo,
} from '@/services/audition';
import { goRoleHome, persistLoginData } from '@/utils/auth';
import { Loading } from '@/components';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

type PageState = 'loading' | 'ready' | 'error';

const formatExpire = (value: string | null): string => {
  if (!value) return '长期有效';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const AuditionPage: React.FC = () => {
  const [state, setState] = useState<PageState>('loading');
  const [info, setInfo] = useState<AuditionInfo | null>(null);
  const [errorMsg, setErrorMsg] = useState('链接无效');
  const [token, setToken] = useState('');
  const [entering, setEntering] = useState(false);

  useEffect(() => {
    const params = Taro.getCurrentInstance().router?.params || {};
    const tokenParam = params.token || '';
    const role = params.role || '';
    // 老板端仅接受老板专属试音链接。
    if (!tokenParam || role !== 'boss') {
      setErrorMsg('链接参数缺失或无效');
      setState('error');
      return;
    }
    setToken(tokenParam);
    fetchAuditionInfo(tokenParam, 'boss')
      .then(res => {
        if (res.code === 0 && res.data) {
          setInfo(res.data);
          setState('ready');
        } else {
          setErrorMsg(res.msg || '链接已失效');
          setState('error');
        }
      })
      .catch(() => {
        setErrorMsg('网络异常，请稍后再试');
        setState('error');
      });
  }, []);

  const handleCta = async () => {
    if (!info || entering) return;
    setEntering(true);
    try {
      const res = await exchangeAuditionToken(token, 'boss');
      if (res.code !== 0 || !res.data) {
        Taro.showToast({ title: res.msg || '进入失败，请联系客服', icon: 'none' });
        return;
      }
      persistLoginData(res.data);
      Taro.showToast({ title: '已进入', icon: 'success' });
      setTimeout(() => goRoleHome(), 300);
    } catch (err) {
      Taro.showToast({ title: '网络异常，请稍后再试', icon: 'none' });
    } finally {
      setEntering(false);
    }
  };

  if (state === 'loading') {
    return (
      <View className={styles.container}>
        <Loading fullscreen text="加载中…" />
      </View>
    );
  }

  if (state === 'error' || !info) {
    return (
      <View className={styles.container}>
        <View className={styles.errorCard}>
          <View className={styles.errorIcon}>
            <Icon name="shield-alert" size={64} color="#FF3B30" />
          </View>
          <Text className={styles.errorTitle}>无法打开试音链接</Text>
          <Text className={styles.errorDesc}>{errorMsg}</Text>
        </View>
      </View>
    );
  }

  return (
    <ScrollView className={styles.container} scrollY>
      <View className={styles.hero}>
        <Text className={styles.roleTag}>老板专属入口</Text>
        <Text className={styles.title}>{info.title}</Text>
        <Text className={styles.heroDesc}>查看本次试音活动详情，可联系官方客服预约心仪陪玩。</Text>
      </View>

      <View className={styles.card}>
        <View className={styles.row}>
          <Text className={styles.label}>活动说明</Text>
          <Text className={styles.value}>{info.remark || '暂无补充说明'}</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.label}>截止时间</Text>
          <Text className={styles.value}>{formatExpire(info.expire_at)}</Text>
        </View>
      </View>

      <View className={styles.ctaBtn} onClick={handleCta}>
        <Text className={styles.ctaText}>{entering ? '进入中…' : '进入专属页'}</Text>
      </View>
      <Text className={styles.footnote}>通过专属分享链接进入，将以绑定账号自动登录。</Text>
    </ScrollView>
  );
};

export default AuditionPage;
