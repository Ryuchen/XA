import React, { useMemo, useState } from 'react';
import { ScrollView, View, Text } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { fetchCheckinCalendar, doCheckin, doMakeupCheckin, CheckinCalendar } from '@/services/checkin';
import { formatXaCoin } from '@/utils/money';
import { useWalletStore } from '@/store';
import { Skeleton } from '@/components';
import Icon from '@/components/Icon';
import styles from './index.module.scss';

const weekdayLabels = ['日', '一', '二', '三', '四', '五', '六'] as const;

const CheckinPage: React.FC = () => {
  const [calendar, setCalendar] = useState<CheckinCalendar | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadCalendar = async () => {
    try {
      const res = await fetchCheckinCalendar();
      if (res.code === 0 && res.data) {
        setCalendar(res.data);
      } else {
        Taro.showToast({ title: res.msg || '加载失败', icon: 'none' });
      }
    } catch (e) {
      console.error('加载签到日历失败', e);
    }
  };

  // useDidShow 首次进入也会触发，无需额外 useEffect，避免首屏双份请求
  useDidShow(() => {
    loadCalendar();
  });

  const checkedSet = useMemo(
    () => new Set(calendar?.checked_days || []),
    [calendar]
  );

  // 生成当月日历网格：首行按星期补齐空白
  const cells = useMemo(() => {
    if (!calendar) return [] as Array<{ day: number | null; key: string }>;
    const firstWeekday = new Date(calendar.year, calendar.month - 1, 1).getDay();
    const result: Array<{ day: number | null; key: string }> = [];
    for (let i = 0; i < firstWeekday; i++) {
      result.push({ day: null, key: `blank-${i}` });
    }
    for (let d = 1; d <= calendar.days_in_month; d++) {
      result.push({ day: d, key: `day-${d}` });
    }
    return result;
  }, [calendar]);

  const handleCheckin = async () => {
    if (!calendar || calendar.today_checked || !calendar.today_eligible || submitting) return;

    setSubmitting(true);
    try {
      const res = await doCheckin();
      if (res.code === 0 && res.data) {
        const rewardText = res.data.reward_amount > 0 ? ` +${formatXaCoin(res.data.reward_amount)}币` : '';
        const gift = res.data.gift || {};
        Taro.showToast({ title: `${gift.icon || ''} ${gift.name || '签到奖励'}${rewardText}`, icon: 'none' });
        // 签到奖励会进钱包，让余额缓存失效
        useWalletStore.getState().invalidate();
        if (res.data.full_attendance_awarded) {
          await Taro.showModal({ title: '月度全勤达成', content: `恭喜获得 ${calendar.full_attendance_reward?.name || '全勤奖励'}`, showCancel: false });
        }
        await loadCalendar();
      } else {
        Taro.showToast({ title: res.msg || '签到失败', icon: 'none' });
        if (res.msg === '今日已签到') await loadCalendar();
      }
    } catch (e) {
      console.error('签到失败', e);
      Taro.showToast({ title: '签到失败，请稍后重试', icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  const handleMakeup = async (day: number) => {
    if (!calendar || submitting || !calendar.makeup_available_days.includes(day)) return;
    if (calendar.makeup_cards <= 0) {
      Taro.showToast({ title: `当日消费满${formatXaCoin(calendar.makeup_card_spend_required)}币可获得补签卡`, icon: 'none' });
      return;
    }
    const confirm = await Taro.showModal({
      title: `补签 ${calendar.month}月${day}日`,
      content: `将消耗 1 张补签卡，当前共有 ${calendar.makeup_cards} 张。`,
      confirmText: '确认补签',
    });
    if (!confirm.confirm) return;
    setSubmitting(true);
    try {
      const res = await doMakeupCheckin(day);
      if (res.code !== 0 || !res.data) throw new Error(res.msg || '补签失败');
      const gift = res.data.gift || {};
      Taro.showToast({ title: `${gift.icon || ''} 补签成功`, icon: 'none' });
      await loadCalendar();
    } catch (e) {
      Taro.showToast({ title: e instanceof Error ? e.message : '补签失败', icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  if (!calendar) {
    return (
      <View className={styles.container}>
        <Skeleton variant="block" height="160rpx" className={styles.skeletonBlock} />
        <Skeleton variant="block" height="520rpx" className={styles.skeletonBlock} />
        <Skeleton variant="block" height="200rpx" className={styles.skeletonBlock} />
      </View>
    );
  }

  return (
    <View className={styles.container}>
      <View className={styles.summaryCard}>
        <View className={styles.summaryItem}>
          <Icon name="coins" size={40} color="#FFFFFF" className={styles.summaryIcon} />
          <Text className={styles.summaryValue}>{formatXaCoin(calendar.today_spend)}</Text>
          <Text className={styles.summaryLabel}>今日消费（币）</Text>
        </View>
        <View className={styles.summaryDivider} />
        <View className={styles.summaryItem}>
          <Icon name="calendar-check" size={40} color="#FFFFFF" className={styles.summaryIcon} />
          <Text className={styles.summaryValue}>{calendar.checked_count}</Text>
          <Text className={styles.summaryLabel}>本月已签（天）</Text>
        </View>
        <View className={styles.summaryDivider} />
        <View className={styles.summaryItem}>
          <Icon name="ticket" size={40} color="#FFFFFF" className={styles.summaryIcon} />
          <Text className={styles.summaryValue}>{calendar.makeup_cards}/{calendar.max_makeup_cards}</Text>
          <Text className={styles.summaryLabel}>本月补签卡</Text>
        </View>
      </View>

      <View className={styles.ruleCard}>
        <View className={styles.ruleTop}>
          <View><Text className={styles.ruleTitle}>今日消费签到</Text><Text className={styles.ruleDesc}>满 {formatXaCoin(calendar.daily_spend_required)} 兴安币可签到</Text></View>
          <Text className={`${styles.ruleState} ${calendar.today_eligible ? styles.ruleStateDone : ''}`}>{calendar.today_eligible ? '已达标' : '未达标'}</Text>
        </View>
        <View className={styles.progressTrack}><View className={styles.progressFill} style={{ width: `${calendar.daily_spend_required ? Math.min(calendar.today_spend / calendar.daily_spend_required * 100, 100) : 0}%` }} /></View>
        <Text className={styles.cardRule}>当日消费满 {formatXaCoin(calendar.makeup_card_spend_required)} 兴安币自动获得 1 张补签卡，最多持有 {calendar.max_makeup_cards} 张</Text>
      </View>

      <View className={styles.card}>
        <View className={styles.cardHeader}>
          <View className={styles.cardTitleRow}>
            <Icon name="calendar" size={32} color="#007AFF" className={styles.cardTitleIcon} />
            <Text className={styles.cardTitle}>{calendar.year} 年 {calendar.month} 月</Text>
          </View>
          <Text className={styles.cardDesc}>漏签日期可点击使用补签卡</Text>
        </View>

        <View className={styles.weekRow}>
          {weekdayLabels.map(label => (
            <Text key={label} className={styles.weekLabel}>{label}</Text>
          ))}
        </View>

        <View className={styles.grid}>
          {cells.map(cell => {
            if (cell.day === null) {
              return <View key={cell.key} className={styles.cellBlank} />;
            }
            const isChecked = checkedSet.has(cell.day);
            const isToday = cell.day === calendar.today;
            const canMakeup = calendar.makeup_available_days.includes(cell.day);
            return (
              <View
                key={cell.key}
                className={`${styles.cell} ${isChecked ? styles.cellChecked : ''} ${isToday ? styles.cellToday : ''} ${canMakeup ? styles.cellMakeup : ''}`}
                onClick={() => canMakeup && handleMakeup(cell.day as number)}
              >
                {isChecked ? (
                  <Icon name="check" size={32} color="#34C759" className={styles.cellMark} />
                ) : (
                  <><Text className={styles.cellDay}>{cell.day}</Text>{canMakeup && <Text className={styles.makeupMark}>补</Text>}</>
                )}
              </View>
            );
          })}
        </View>
      </View>

      <View className={styles.card}>
        <View className={styles.cardHeader}>
          <View className={styles.cardTitleRow}>
            <Icon name="trophy" size={32} color="#FF9500" className={styles.cardTitleIcon} />
            <Text className={styles.cardTitle}>本月签到礼物</Text>
          </View>
          <Text className={styles.cardDesc}>礼物内容由平台运营配置</Text>
        </View>
        <ScrollView scrollX className={styles.rewardScroll}><View className={styles.rewardRow}>
          {calendar.rewards.map(reward => {
            const reached = calendar.checked_count >= reward.seq;
            return (
              <View
                key={reward.seq}
                className={`${styles.rewardItem} ${reached ? styles.rewardReached : ''}`}
              >
                <Icon
                  name={reached ? 'coins' : 'gift'}
                  size={36}
                  color={reached ? '#FF9500' : '#AEAEB2'}
                  className={styles.rewardIcon}
                />
                <Text className={styles.rewardSeq}>第{reward.seq}天</Text>
                <Text className={styles.rewardName}>{reward.icon || ''} {reward.name || '神秘礼物'}</Text>
                <Text className={styles.rewardAmount}>{reward.amount > 0 ? `${formatXaCoin(reward.amount)}币` : '专属礼物'}</Text>
              </View>
            );
          })}
        </View></ScrollView>
      </View>

      <View className={`${styles.fullRewardCard} ${calendar.full_attendance ? styles.fullRewardDone : ''}`}>
        <Text className={styles.fullRewardIcon}>🏆</Text>
        <View className={styles.fullRewardCopy}><Text className={styles.fullRewardTitle}>{calendar.full_attendance_reward.name}</Text><Text className={styles.fullRewardDesc}>{calendar.full_attendance_reward.description}</Text></View>
        <Text className={styles.fullRewardState}>{calendar.full_attendance ? '已获得' : `${calendar.checked_count}/${calendar.days_in_month}`}</Text>
      </View>

      <View
        className={`${styles.checkinBtn} ${calendar.today_checked || !calendar.today_eligible ? styles.checkinBtnDone : ''}`}
        onClick={handleCheckin}
      >
        <Icon
          name={calendar.today_checked ? 'check-circle' : 'calendar-check'}
          size={40}
          color="#FFFFFF"
          className={styles.checkinBtnIcon}
        />
        <Text className={styles.checkinBtnText}>
          {calendar.today_checked
            ? '今日已签到'
            : !calendar.today_eligible
              ? `再消费${formatXaCoin(Math.max(calendar.daily_spend_required - calendar.today_spend, 0))}币可签到`
              : `立即签到 · ${calendar.next_gift?.icon || ''}${calendar.next_gift?.name || '签到礼物'}`}
        </Text>
      </View>
    </View>
  );
};

export default CheckinPage;
