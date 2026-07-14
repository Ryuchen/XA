import React, { useEffect, useState } from 'react';
import { View, Text, Image, ScrollView } from '@tarojs/components';
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro';
import { clearStoredUser, getStoredUser, LoginUser } from '@/utils/auth';
import { fetchEscortMe, EscortMeProfile, updateEscortStatus, EscortStatus } from '@/services/user';
import { fetchMyEvaluations, replyEvaluation } from '@/services/order';
import { Evaluation } from '@/types/order';
import { fetchDepositInfo, payDeposit, DepositOverview } from '@/services/wallet';
import { formatXaCoin } from '@/utils/format';
import styles from './index.module.scss';
import PlatformLayout from '@/components/PlatformLayout';
import ReplyDialog from '@/components/ReplyDialog';

const STATUS_LABEL: Record<string, string> = {
  AVAILABLE: '接单中',
  BUSY: '忙碌',
  OFFLINE: '离线',
};

const STATUS_OPTIONS: EscortStatus[] = ['AVAILABLE', 'BUSY', 'OFFLINE'];

const MinePage: React.FC = () => {
  const [user, setUser] = useState<LoginUser | null>(getStoredUser());
  const [profile, setProfile] = useState<EscortMeProfile | null>(null);
  const [deposit, setDeposit] = useState<DepositOverview | null>(null);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [statusUpdating, setStatusUpdating] = useState(false);
  const [replying, setReplying] = useState<Evaluation | null>(null);
  const [replySubmitting, setReplySubmitting] = useState(false);

  const loadData = async () => {
    setUser(getStoredUser());
    const [profileRes, depositRes, evalRes] = await Promise.all([
      fetchEscortMe().catch(() => null),
      fetchDepositInfo().catch(() => null),
      fetchMyEvaluations().catch(() => null),
    ]);
    if (profileRes && profileRes.code === 0 && profileRes.data) {
      setProfile(profileRes.data);
    }
    if (depositRes && depositRes.code === 0 && depositRes.data) {
      setDeposit(depositRes.data);
    }
    if (evalRes && evalRes.code === 0 && evalRes.data) {
      setEvaluations(evalRes.data.list);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useDidShow(() => {
    loadData();
  });

  usePullDownRefresh(async () => {
    await loadData();
    Taro.stopPullDownRefresh();
  });

  const handlePayDeposit = async () => {
    if (!deposit) return;
    const remaining = deposit.deposit_remaining;
    if (remaining <= 0) {
      Taro.showToast({ title: '押金已缴清', icon: 'none' });
      return;
    }
    const confirm = await Taro.showModal({
      title: '缴纳押金',
      content: `将从钱包余额扣除 ${formatXaCoin(remaining)} 兴安币缴纳押金。`,
      confirmText: '确认缴纳',
    });
    if (!confirm.confirm) return;

    Taro.showLoading({ title: '缴纳中' });
    try {
      const res = await payDeposit(remaining);
      if (res.code === 0 && res.data) {
        setDeposit(res.data);
        Taro.showToast({ title: '押金缴纳成功', icon: 'success' });
      } else {
        Taro.showToast({ title: res.msg || '缴纳失败', icon: 'none' });
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '缴纳失败', icon: 'none' });
    } finally {
      Taro.hideLoading();
    }
  };

  const handleChangeStatus = async () => {
    if (statusUpdating) return;
    const { tapIndex } = await Taro.showActionSheet({
      itemList: STATUS_OPTIONS.map(s => STATUS_LABEL[s]),
    }).catch(() => ({ tapIndex: -1 }));
    if (tapIndex < 0) return;
    const next = STATUS_OPTIONS[tapIndex];
    if (profile && profile.status === next) return;

    setStatusUpdating(true);
    Taro.showLoading({ title: '切换中' });
    try {
      const res = await updateEscortStatus(next);
      if (res.code === 0) {
        setProfile(prev => (prev ? { ...prev, status: next } : prev));
        Taro.showToast({ title: '状态已更新', icon: 'success' });
      } else {
        Taro.showToast({ title: res.msg || '切换失败', icon: 'none' });
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '切换失败', icon: 'none' });
    } finally {
      Taro.hideLoading();
      setStatusUpdating(false);
    }
  };

  const handleReplyEvaluation = async (text: string) => {
    if (!replying) return;
    const item = replying;
    setReplySubmitting(true);
    try {
      const res = await replyEvaluation(item.id, text);
      if (res.code === 0) {
        setEvaluations(prev =>
          prev.map(e => (e.id === item.id ? { ...e, reply_content: text } : e)),
        );
        setReplying(null);
        Taro.showToast({ title: '回复成功', icon: 'success' });
      } else {
        Taro.showToast({ title: res.msg || '回复失败', icon: 'none' });
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '回复失败', icon: 'none' });
    } finally {
      setReplySubmitting(false);
    }
  };

  const handleLogout = async () => {
    const confirm = await Taro.showModal({ title: '退出登录', content: '确认退出当前账号？' });
    if (!confirm.confirm) return;
    clearStoredUser();
    Taro.reLaunch({ url: '/pages/login/index' });
  };

  return (
    <PlatformLayout active='mine'><ScrollView className={styles.container} scrollY>
      <View className={styles.header}>
        <Image
          className={styles.avatar}
          src={user?.avatar || 'https://picsum.photos/id/1005/200/200'}
          mode="aspectFill"
        />
        <View className={styles.userDetail}>
          <Text className={styles.nickname}>{profile?.display_name || user?.nickname || '陪玩用户'}</Text>
          <Text className={styles.sub}>
            {user?.username ? `账号：${user.username}` : '陪玩'}
          </Text>
          {profile && (
            <View className={styles.badgeRow}>
              <Text className={styles.statusSwitch} onClick={handleChangeStatus}>
                {STATUS_LABEL[profile.status] || profile.status} ▾
              </Text>
              {profile.is_verified ? (
                <Text className={styles.badgeVerified}>已认证</Text>
              ) : (
                <Text className={styles.badgeUnverified}>未认证</Text>
              )}
            </View>
          )}
        </View>
      </View>

      {profile && (
        <View className={styles.statsCard}>
          <View className={styles.statItem}>
            <Text className={styles.statValue}>{Number(profile.rating_avg || 0).toFixed(1)}</Text>
            <Text className={styles.statLabel}>评分</Text>
          </View>
          <View className={styles.statItem}>
            <Text className={styles.statValue}>{profile.rating_count}</Text>
            <Text className={styles.statLabel}>评价数</Text>
          </View>
          <View className={styles.statItem}>
            <Text className={styles.statValue}>{profile.completed_order_count}</Text>
            <Text className={styles.statLabel}>完成单</Text>
          </View>
        </View>
      )}

      {deposit && (
        <View className={styles.card}>
          <View className={styles.cardHeader}>
            <Text className={styles.cardTitle}>押金</Text>
            {deposit.deposit_remaining > 0 && (
              <Text className={styles.cardAction} onClick={handlePayDeposit}>去缴纳</Text>
            )}
          </View>
          <View className={styles.depositRow}>
            <Text className={styles.depositLabel}>应缴</Text>
            <Text className={styles.depositValue}>{formatXaCoin(deposit.deposit_required)}币</Text>
          </View>
          <View className={styles.depositRow}>
            <Text className={styles.depositLabel}>已缴</Text>
            <Text className={styles.depositValue}>{formatXaCoin(deposit.deposit_paid)}币</Text>
          </View>
          <View className={styles.depositRow}>
            <Text className={styles.depositLabel}>待缴</Text>
            <Text className={styles.depositRemaining}>{formatXaCoin(deposit.deposit_remaining)}币</Text>
          </View>
        </View>
      )}

      {evaluations.length > 0 && (
        <View className={styles.card}>
          <View className={styles.cardHeader}>
            <Text className={styles.cardTitle}>我收到的评价</Text>
          </View>
          {evaluations.map(item => (
            <View key={item.id} className={styles.evalItem}>
              <View className={styles.evalHead}>
                <Text className={styles.evalName}>{item.customer_name}</Text>
                <Text className={styles.evalScore}>{item.score} 分</Text>
              </View>
              {!!item.content && <Text className={styles.evalContent}>{item.content}</Text>}
              {item.reply_content ? (
                <Text className={styles.evalReply}>我的回复：{item.reply_content}</Text>
              ) : (
                <Text className={styles.evalReplyBtn} onClick={() => setReplying(item)}>
                  回复
                </Text>
              )}
            </View>
          ))}
        </View>
      )}

      <View className={styles.menuCard}>
        <View className={styles.menuItem} onClick={() => Taro.navigateTo({ url: '/pages/profile/index' })}>
          <Text className={styles.menuIcon}>🪪</Text>
          <Text className={styles.menuText}>资料与档期</Text>
          <Text className={styles.menuArrow}>›</Text>
        </View>
        <View className={styles.menuItem} onClick={() => Taro.navigateTo({ url: '/pages/evaluations/index' })}>
          <Text className={styles.menuIcon}>⭐️</Text>
          <Text className={styles.menuText}>评价管理</Text>
          {evaluations.some(item => !item.reply_content) && (
            <Text className={styles.menuBadge}>{evaluations.filter(item => !item.reply_content).length}</Text>
          )}
          <Text className={styles.menuArrow}>›</Text>
        </View>
        <View className={styles.menuItem} onClick={() => Taro.switchTab({ url: '/pages/wallet/index' })}>
          <Text className={styles.menuIcon}>💰</Text>
          <Text className={styles.menuText}>我的钱包</Text>
          <Text className={styles.menuArrow}>›</Text>
        </View>
        <View className={styles.menuItem} onClick={() => Taro.switchTab({ url: '/pages/report/index' })}>
          <Text className={styles.menuIcon}>📝</Text>
          <Text className={styles.menuText}>我的报单</Text>
          <Text className={styles.menuArrow}>›</Text>
        </View>
        <View className={styles.menuItem} onClick={handleLogout}>
          <Text className={styles.menuIcon}>🚪</Text>
          <Text className={styles.menuText}>退出登录</Text>
          <Text className={styles.menuArrow}>›</Text>
        </View>
      </View>
      <ReplyDialog
        open={!!replying}
        title={`回复 ${replying?.customer_name || '老板'} 的评价`}
        initialValue={replying?.reply_content || ''}
        placeholder='感谢老板的认可，也可以说明本次服务情况…'
        submitting={replySubmitting}
        onClose={() => setReplying(null)}
        onSubmit={handleReplyEvaluation}
      />
    </ScrollView></PlatformLayout>
  );
};

export default MinePage;
