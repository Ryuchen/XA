import React, { useEffect, useState } from 'react';
import { Button, Input, Picker, ScrollView, Text, Textarea, View } from '@tarojs/components';
import Taro, { usePullDownRefresh } from '@tarojs/taro';
import {
  EscortMeProfile,
  EscortScheduleSlot,
  fetchEscortMe,
  fetchEscortSchedule,
  saveEscortSchedule,
  uploadEscortAsset,
  updateEscortProfile,
} from '@/services/user';
import { Loading } from '@/components';
import styles from './index.module.scss';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';

const DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const SEGMENTS = [
  { label: '00:00–06:00', start_minute: 0, end_minute: 360 },
  { label: '06:00–12:00', start_minute: 360, end_minute: 720 },
  { label: '12:00–18:00', start_minute: 720, end_minute: 1080 },
  { label: '18:00–24:00', start_minute: 1080, end_minute: 1440 },
] as const;
const GENDER_OPTIONS = [
  { label: '保密', value: 'UNKNOWN' },
  { label: '男', value: 'MALE' },
  { label: '女', value: 'FEMALE' },
] as const;

type FormState = Pick<EscortMeProfile, 'display_name' | 'gender' | 'bio' | 'city' | 'service_area'>;

const makeSlotKey = (slot: EscortScheduleSlot) => `${slot.weekday}-${slot.start_minute}-${slot.end_minute}`;

const ProfilePage: React.FC = () => {
  const [form, setForm] = useState<FormState>({
    display_name: '', gender: 'UNKNOWN', bio: '', city: '', service_area: '',
  });
  const [slots, setSlots] = useState<EscortScheduleSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [profile, setProfile] = useState<EscortMeProfile | null>(null);
  const [uploading, setUploading] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const [profileRes, scheduleRes] = await Promise.all([fetchEscortMe(), fetchEscortSchedule()]);
      if (profileRes.code === 0 && profileRes.data) {
        setProfile(profileRes.data);
        const { display_name, gender, bio, city, service_area } = profileRes.data;
        setForm({ display_name, gender, bio, city, service_area });
      }
      if (scheduleRes.code === 0 && scheduleRes.data) setSlots(scheduleRes.data);
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '加载失败', icon: 'none' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);
  usePullDownRefresh(async () => { await load(); Taro.stopPullDownRefresh(); });

  const patch = (field: keyof FormState, value: string) => setForm(prev => ({ ...prev, [field]: value }));
  const isSelected = (slot: EscortScheduleSlot) => slots.some(item => makeSlotKey(item) === makeSlotKey(slot));
  const toggleSlot = (slot: EscortScheduleSlot) => {
    setSlots(prev => isSelected(slot)
      ? prev.filter(item => makeSlotKey(item) !== makeSlotKey(slot))
      : [...prev, slot],
    );
  };

  const submitProfile = async () => {
    const displayName = form.display_name.trim();
    if (!displayName) {
      Taro.showToast({ title: '展示昵称不能为空', icon: 'none' });
      return;
    }
    setSavingProfile(true);
    try {
      const res = await updateEscortProfile({
        display_name: displayName,
        gender: form.gender as 'MALE' | 'FEMALE' | 'UNKNOWN',
        bio: form.bio.trim(), city: form.city.trim(), service_area: form.service_area.trim(),
      });
      if (res.code === 0) Taro.showToast({ title: '资料已保存', icon: 'success' });
      else Taro.showToast({ title: res.msg || '保存失败', icon: 'none' });
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '保存失败', icon: 'none' });
    } finally {
      setSavingProfile(false);
    }
  };

  const submitSchedule = async () => {
    setSavingSchedule(true);
    try {
      const res = await saveEscortSchedule(slots);
      if (res.code === 0) {
        setSlots(res.data || slots);
        Taro.showToast({ title: '档期已保存', icon: 'success' });
      } else {
        Taro.showToast({ title: res.msg || '保存失败', icon: 'none' });
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '保存失败', icon: 'none' });
    } finally {
      setSavingSchedule(false);
    }
  };

  const uploadAsset = async (field: 'avatar' | 'intro_video' | 'voice_card' | 'cheat_proof') => {
    try {
      let path = '';
      if (field === 'avatar' || field === 'cheat_proof') {
        const result = await Taro.chooseImage({ count: 1, sizeType: ['compressed'] });
        path = result.tempFilePaths?.[0] || '';
      } else {
        const result = await Taro.chooseMessageFile({ count: 1, type: 'file' });
        path = result.tempFiles?.[0]?.path || '';
      }
      if (!path) return;
      setUploading(field);
      const res = await uploadEscortAsset(field, path);
      if (res.code === 0 && res.data) {
        setProfile(res.data);
        Taro.showToast({ title: '上传成功', icon: 'success' });
      } else Taro.showToast({ title: res.msg || '上传失败', icon: 'none' });
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      if (message) Taro.showToast({ title: message, icon: 'none' });
    } finally {
      setUploading('');
    }
  };

  const genderIndex = Math.max(0, GENDER_OPTIONS.findIndex(item => item.value === form.gender));

  if (loading) return <Loading fullscreen text="加载资料中…" />;

  return (
    <PlatformLayout active='mine'><ScrollView className={styles.container} scrollY>
      <PageNav title="资料与档期" />
      <View className={styles.tip}>认证、时价与等级由运营后台维护；这里可更新对外展示资料和每周接单档期。</View>

      <View className={styles.card}>
        <Text className={styles.title}>对外展示资料</Text>
        <View className={styles.assetField}>
          <Text className={styles.label}>头像</Text>
          <Button size="mini" loading={uploading === 'avatar'} disabled={!!uploading} onClick={() => uploadAsset('avatar')}>
            {profile?.avatar_url ? '更换头像' : '上传头像'}
          </Button>
        </View>
        <View className={styles.assetField}>
          <Text className={styles.label}>介绍视频</Text>
          <Button size="mini" loading={uploading === 'intro_video'} disabled={!!uploading} onClick={() => uploadAsset('intro_video')}>
            {profile?.intro_video_url ? '更换视频' : '上传视频'}
          </Button>
        </View>
        <View className={styles.assetField}>
          <Text className={styles.label}>语音卡</Text>
          <Button size="mini" loading={uploading === 'voice_card'} disabled={!!uploading} onClick={() => uploadAsset('voice_card')}>
            {profile?.voice_card_url ? '更换语音卡' : '上传语音卡'}
          </Button>
        </View>
        <View className={styles.assetField}>
          <Text className={styles.label}>查外挂凭证</Text>
          <Button size="mini" loading={uploading === 'cheat_proof'} disabled={!!uploading} onClick={() => uploadAsset('cheat_proof')}>
            {profile?.cheat_proof_url ? '更换凭证' : '上传凭证'}
          </Button>
        </View>
        <View className={styles.field}>
          <Text className={styles.label}>展示昵称</Text>
          <Input className={styles.input} value={form.display_name} maxlength={50} onInput={e => patch('display_name', e.detail.value)} />
        </View>
        <View className={styles.field}>
          <Text className={styles.label}>性别</Text>
          <Picker mode="selector" range={GENDER_OPTIONS.map(item => item.label)} value={genderIndex} onChange={e => patch('gender', GENDER_OPTIONS[Number(e.detail.value)].value)}>
            <View className={styles.picker}>{GENDER_OPTIONS[genderIndex].label}<Text className={styles.chevron}>›</Text></View>
          </Picker>
        </View>
        <View className={styles.field}>
          <Text className={styles.label}>所在城市</Text>
          <Input className={styles.input} value={form.city} maxlength={50} placeholder="例如：杭州" onInput={e => patch('city', e.detail.value)} />
        </View>
        <View className={styles.field}>
          <Text className={styles.label}>擅长项目</Text>
          <Input className={styles.input} value={form.service_area} maxlength={255} placeholder="例如：王者荣耀、英雄联盟" onInput={e => patch('service_area', e.detail.value)} />
        </View>
        <View className={styles.textareaField}>
          <Text className={styles.label}>个人简介</Text>
          <Textarea className={styles.textarea} value={form.bio} maxlength={500} placeholder="介绍擅长位置、风格和服务特点" autoHeight onInput={e => patch('bio', e.detail.value)} />
        </View>
        <Button className={styles.primaryButton} loading={savingProfile} disabled={savingProfile} onClick={submitProfile}>保存资料</Button>
      </View>

      <View className={styles.card}>
        <Text className={styles.title}>每周接单档期</Text>
        <Text className={styles.subtitle}>点击可接单的 6 小时时段；不设置即表示暂不开放固定档期。</Text>
        {DAYS.map((day, weekday) => (
          <View key={day} className={styles.scheduleRow}>
            <Text className={styles.day}>{day}</Text>
            <View className={styles.segmentList}>
              {SEGMENTS.map(segment => {
                const slot = { weekday, start_minute: segment.start_minute, end_minute: segment.end_minute };
                const selected = isSelected(slot);
                return <Text key={segment.start_minute} className={`${styles.segment} ${selected ? styles.segmentSelected : ''}`} onClick={() => toggleSlot(slot)}>{segment.label}</Text>;
              })}
            </View>
          </View>
        ))}
        <Button className={styles.primaryButton} loading={savingSchedule} disabled={savingSchedule} onClick={submitSchedule}>保存档期</Button>
      </View>
    </ScrollView></PlatformLayout>
  );
};

export default ProfilePage;
