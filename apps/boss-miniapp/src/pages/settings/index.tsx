import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, Input, Image } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { fetchMe, updateMe, MeProfile } from '@/services/user';
import { fetchGameCategories, GameCategoryInfo } from '@/services/order';
import { getStoredUser, setStoredUser } from '@/utils/auth';
import Icon from '@/components/Icon';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const SettingsPage: React.FC = () => {
  const [nickname, setNickname] = useState('');
  const [phone, setPhone] = useState('');
  const [gameCategories, setGameCategories] = useState<GameCategoryInfo[]>([]);
  const [selectedGameId, setSelectedGameId] = useState<number | null>(null);
  const [gameProfiles, setGameProfiles] = useState<Record<number, { region: string; nickname: string; uid: string }>>({});
  const [submitting, setSubmitting] = useState(false);

  const applyProfile = (data: MeProfile) => {
    setNickname(data.nickname || '');
    setPhone(data.phone || '');
    const profiles = Object.fromEntries(
      (data.game_profiles || []).map(item => [item.game_category, {
        region: item.region || '',
        nickname: item.nickname || '',
        uid: item.uid || '',
      }])
    );
    setGameProfiles(profiles);
    const defaultProfile = (data.game_profiles || []).find(item => item.is_default) || data.game_profiles?.[0];
    if (defaultProfile) setSelectedGameId(defaultProfile.game_category);
  };

  useEffect(() => {
    Promise.all([fetchMe(), fetchGameCategories()]).then(([profileRes, categoryRes]) => {
      if (categoryRes.code === 0 && categoryRes.data) {
        setGameCategories(categoryRes.data);
        setSelectedGameId(current => current ?? categoryRes.data?.[0]?.id ?? null);
      }
      if (profileRes.code === 0 && profileRes.data) applyProfile(profileRes.data);
    }).catch(() => Taro.showToast({ title: '资料加载失败', icon: 'none' }));
  }, []);

  const currentGameProfile = selectedGameId == null
    ? { region: '', nickname: '', uid: '' }
    : gameProfiles[selectedGameId] || { region: '', nickname: '', uid: '' };

  const updateCurrentGameProfile = (field: 'region' | 'nickname' | 'uid', value: string) => {
    if (selectedGameId == null) return;
    setGameProfiles(prev => ({
      ...prev,
      [selectedGameId]: { ...(prev[selectedGameId] || { region: '', nickname: '', uid: '' }), [field]: value },
    }));
  };

  const handleSave = async () => {
    if (submitting) return;
    if (!nickname.trim()) {
      Taro.showToast({ title: '请填写昵称', icon: 'none' });
      return;
    }
    setSubmitting(true);
    const res = await updateMe({
      nickname: nickname.trim(),
      phone: phone.trim(),
      game_profiles: gameCategories.map(category => ({
        game_category: category.id,
        region: (gameProfiles[category.id]?.region || '').trim(),
        nickname: (gameProfiles[category.id]?.nickname || '').trim(),
        uid: (gameProfiles[category.id]?.uid || '').trim(),
        is_default: category.id === selectedGameId,
      })),
    });
    setSubmitting(false);
    if (res.code === 0 && res.data) {
      applyProfile(res.data);
      // 同步本地存储的昵称，使「我的」页头部即时刷新
      const stored = getStoredUser();
      if (stored) {
        setStoredUser({ ...stored, nickname: res.data.nickname });
      }
      Taro.showToast({ title: '已保存', icon: 'success' });
    } else {
      Taro.showToast({ title: res.msg || '保存失败', icon: 'none' });
    }
  };

  return (
    <ScrollView className={styles.container} scrollY>
      <View className={styles.section}>
        <View className={styles.sectionHeader}>
          <Icon name="user" size={32} color="#8E8E93" />
          <Text className={styles.sectionTitle}>基本资料</Text>
        </View>
        <View className={styles.card}>
          <View className={styles.formRow}>
            <Text className={styles.formLabel}>昵称</Text>
            <Input
              className={styles.formInput}
              value={nickname}
              placeholder="请输入昵称"
              onInput={e => setNickname(e.detail.value)}
            />
          </View>
          <View className={styles.formRow}>
            <Text className={styles.formLabel}>手机号</Text>
            <Input
              className={styles.formInput}
              type="number"
              value={phone}
              placeholder="请输入手机号"
              onInput={e => setPhone(e.detail.value)}
            />
          </View>
        </View>
      </View>

      <View className={styles.section}>
        <View className={styles.sectionHeader}>
          <Icon name="gamepad-2" size={32} color="#8E8E93" />
          <Text className={styles.sectionTitle}>常用游戏资料</Text>
        </View>
        <Text className={styles.sectionHint}>保存后下单时自动回填，省去重复输入</Text>
        {gameCategories.length > 0 ? (
          <ScrollView className={styles.gameTabs} scrollX>
            <View className={styles.gameTabTrack}>
              {gameCategories.map(category => (
                <View
                  key={category.id}
                  className={`${styles.gameTab} ${selectedGameId === category.id ? styles.gameTabActive : ''}`}
                  onClick={() => setSelectedGameId(category.id)}
                >
                  {category.icon_url && (
                    <Image className={styles.gameTabIcon} src={resolveImageUrl(category.icon_url)} mode="aspectFit" />
                  )}
                  <Text>{category.name}</Text>
                </View>
              ))}
            </View>
          </ScrollView>
        ) : (
          <Text className={styles.emptyGames}>后台暂未配置可用游戏类目</Text>
        )}
        <View className={styles.card}>
          <View className={styles.formRow}>
            <Text className={styles.formLabel}>默认大区</Text>
            <Input
              className={styles.formInput}
              value={currentGameProfile.region}
              placeholder="例如：微信区 / QQ区 / 国服"
              onInput={e => updateCurrentGameProfile('region', e.detail.value)}
            />
          </View>
          <View className={styles.formRow}>
            <Text className={styles.formLabel}>常用昵称</Text>
            <Input
              className={styles.formInput}
              value={currentGameProfile.nickname}
              placeholder="请输入游戏内昵称"
              onInput={e => updateCurrentGameProfile('nickname', e.detail.value)}
            />
          </View>
          <View className={styles.formRow}>
            <Text className={styles.formLabel}>常用UID</Text>
            <Input
              className={styles.formInput}
              value={currentGameProfile.uid}
              placeholder="请输入游戏UID / 数字ID"
              onInput={e => updateCurrentGameProfile('uid', e.detail.value)}
            />
          </View>
        </View>
      </View>

      <View className={styles.saveBtn} onClick={handleSave}>
        <Icon name="check" size={36} color="#FFFFFF" />
        <Text className={styles.saveBtnText}>{submitting ? '保存中...' : '保存'}</Text>
      </View>
    </ScrollView>
  );
};

export default SettingsPage;
