import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Picker, Switch, Text, Textarea, View } from '@tarojs/components';
import Taro, { useDidHide, usePullDownRefresh } from '@tarojs/taro';
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
type AssetField = 'avatar' | 'intro_video' | 'voice_card' | 'cheat_proof';

const makeSlotKey = (slot: EscortScheduleSlot) => `${slot.weekday}-${slot.start_minute}-${slot.end_minute}`;

const ProfilePage: React.FC = () => {
  const [form, setForm] = useState<FormState>({
    display_name: '', gender: 'UNKNOWN', bio: '', city: '', service_area: '',
  });
  const [slots, setSlots] = useState<EscortScheduleSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingNotifications, setSavingNotifications] = useState(false);
  const notificationBusy = useRef(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [profile, setProfile] = useState<EscortMeProfile | null>(null);
  const [uploading, setUploading] = useState('');
  const [loadError, setLoadError] = useState('');
  const [playing, setPlaying] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const chosenField = useRef<AssetField>('voice_card');
  const assetBusy = useRef(false);
  const audio = useRef<Taro.InnerAudioContext | null>(null);

  const stopVoice = () => { audio.current?.stop(); setPlaying(false); };
  useDidHide(stopVoice);
  useEffect(() => () => { audio.current?.destroy(); }, []);

  const load = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const [profileRes, scheduleRes] = await Promise.all([fetchEscortMe(), fetchEscortSchedule()]);
      if (profileRes.code !== 0 || !profileRes.data) throw new Error(profileRes.msg || '资料加载失败');
      if (scheduleRes.code !== 0 || !scheduleRes.data) throw new Error(scheduleRes.msg || '档期加载失败');
      if (profileRes.code === 0 && profileRes.data) {
        setProfile(profileRes.data);
        const { display_name, gender, bio, city, service_area } = profileRes.data;
        setForm({ display_name, gender, bio, city, service_area });
      }
      if (scheduleRes.code === 0 && scheduleRes.data) setSlots(scheduleRes.data);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);
  usePullDownRefresh(async () => { await load(); Taro.stopPullDownRefresh(); });

  const patch = (field: keyof FormState, value: string) => setForm(prev => ({ ...prev, [field]: value }));
  const isSelected = (slot: EscortScheduleSlot) => slots.some(item => makeSlotKey(item) === makeSlotKey(slot));
  const toggleSlot = (slot: EscortScheduleSlot) => {
    setSlots(prev => prev.some(item => makeSlotKey(item) === makeSlotKey(slot))
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

  const sendAsset = async (field: AssetField, path: string, name?: string) => {
    if (assetBusy.current || !path) return;
    assetBusy.current = true;
    setUploading(field);
    try {
      const res = await uploadEscortAsset(field, path, name);
      if (res.code === 0 && res.data) {
        if (field === 'voice_card') stopVoice();
        setProfile(res.data);
        Taro.showToast({ title: '上传成功', icon: 'success' });
      } else Taro.showToast({ title: res.msg || '上传失败', icon: 'none' });
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '上传失败，请重试', icon: 'none' });
    } finally {
      assetBusy.current = false;
      setUploading('');
    }
  };

  const chooseBrowserFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const field = chosenField.current;
    const limit = field === 'intro_video' ? 50 : field === 'avatar' ? 5 : 10;
    if (file.size > limit * 1024 * 1024) {
      Taro.showToast({ title: `文件不能超过 ${limit}MB`, icon: 'none' });
      return;
    }
    const path = URL.createObjectURL(file);
    try { await sendAsset(field, path, file.name); }
    finally { URL.revokeObjectURL(path); }
  };

  const uploadAsset = async (field: AssetField) => {
    if (assetBusy.current) return;
    if (process.env.TARO_ENV === 'h5') {
      chosenField.current = field;
      const input = fileInput.current;
      if (input) {
        input.accept = field === 'voice_card' ? '.mp3,.m4a,.wav,.aac,.ogg' : field === 'intro_video' ? 'video/*' : 'image/*';
        input.click();
      }
      return;
    }
    try {
      let path = '';
      let name: string | undefined;
      if (field === 'avatar' || field === 'cheat_proof') {
        const result = await Taro.chooseImage({ count: 1, sizeType: ['compressed'] });
        path = result.tempFilePaths?.[0] || '';
      } else {
        const result = await Taro.chooseMessageFile({ count: 1, type: 'file' });
        path = result.tempFiles?.[0]?.path || '';
        name = result.tempFiles?.[0]?.name;
      }
      await sendAsset(field, path, name);
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      if (message) Taro.showToast({ title: message, icon: 'none' });
    }
  };

  const previewVoice = () => {
    if (playing) { stopVoice(); return; }
    if (!profile?.voice_card_url) return;
    if (!audio.current) {
      audio.current = Taro.createInnerAudioContext();
      audio.current.onEnded(() => setPlaying(false));
      audio.current.onError(() => {
        setPlaying(false);
        Taro.showToast({ title: '语音播放失败，请检查文件后重试', icon: 'none' });
      });
    }
    audio.current.src = profile.voice_card_url;
    audio.current.play();
    setPlaying(true);
  };

  const removeVoice = async () => {
    if (assetBusy.current) return;
    assetBusy.current = true;
    setUploading('remove_voice_card');
    try {
      const res = await updateEscortProfile({ remove_voice_card: true });
      if (res.code !== 0 || !res.data) throw new Error(res.msg || '移除失败');
      stopVoice();
      setProfile(res.data);
      Taro.showToast({ title: '语音卡已移除', icon: 'success' });
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '移除失败', icon: 'none' });
    } finally {
      assetBusy.current = false;
      setUploading('');
    }
  };

  const changeNotification = async (key:string, enabled:boolean) => {
    if (notificationBusy.current) return;
    notificationBusy.current=true;setSavingNotifications(true);
    try { const res=await updateEscortProfile({dispatch_notification_preferences:{[key]:enabled}}); if(res.code!==0 || !res.data) throw new Error(res.msg||'设置失败');setProfile(res.data);Taro.showToast({title:'通知设置已保存',icon:'success'}); }
    catch(e){ Taro.showToast({title:e instanceof Error ? e.message:'设置失败',icon:'none'}); }
    finally{notificationBusy.current=false;setSavingNotifications(false);}
  };
  const genderIndex = Math.max(0, GENDER_OPTIONS.findIndex(item => item.value === form.gender));

  if (loading) return <Loading fullscreen text="加载资料中…" />;
  if (loadError) return <PlatformLayout active='mine'><View className={`xa-provider-page ${styles.container}`}>
    <PageNav title="资料与档期" /><View className={styles.tip}>{loadError}</View>
    <Button className={`xa-button-primary ${styles.primaryButton}`} onClick={load}>重新加载</Button>
  </View></PlatformLayout>;

  return (
    <PlatformLayout active='mine'><View className={`xa-provider-page ${styles.container}`}>
      <PageNav title="资料与档期" />
      {process.env.TARO_ENV === 'h5' && <input type="file" ref={fileInput} style={{ display: 'none' }} onChange={chooseBrowserFile} aria-label="选择资料文件" />}
      <View className={styles.tip}>认证、参考时价与接单资格由运营后台维护；这里可更新对外展示资料和每周接单档期。</View>

      <View className={styles.card}>
        <Text className={styles.title}>我的接单资格</Text><Text className={styles.tip}>同一范围可接此档及以下；不同游戏、方向、平台和难度独立认证。</Text>
        {(profile?.qualifications || []).filter(q=>q.is_active).map(q=><View key={q.id} className={styles.qualRow}><Text>{q.label}</Text></View>)}
        {!profile?.qualifications?.length && <Text className={styles.tip}>资格待配置，请联系运营。</Text>}
      </View>
      <View className={styles.card}>
        <Text className={styles.title}>护航派单通知</Text>
        <Text className={styles.tip}>关闭仅停止此类新单提醒，仍可在接单池查看并主动接单。陪玩等其他服务保持原通知设置。</Text>
        {(profile?.dispatch_notification_options || []).map(option=><View key={option.key} className={styles.notificationRow}><View><Text>{option.label}</Text>{!option.eligible && <Text className={styles.tip}>尚未认证此类型</Text>}</View><Switch color="#002fa7" checked={option.eligible && option.enabled} disabled={!option.eligible || savingNotifications} onChange={e=>changeNotification(option.key,e.detail.value)} /></View>)}
      </View>
      <View className={styles.cards}>
      <View className={`${styles.card} ${styles.formCard}`}>
        <Text className={styles.title}>对外展示资料</Text>
        <View className={styles.assets}>
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
        <View className={styles.voiceAsset}>
        <View className={styles.assetField}>
          <Text className={styles.label}>语音卡</Text>
          <View className={styles.assetActions}>
          {profile?.voice_card_url && <>
            <Button size="mini" disabled={!!uploading} onClick={previewVoice}>{playing ? '停止' : '试听'}</Button>
            <Button size="mini" disabled={!!uploading} loading={uploading === 'remove_voice_card'} onClick={removeVoice}>移除</Button>
          </>}
          <Button size="mini" loading={uploading === 'voice_card'} disabled={!!uploading} onClick={() => uploadAsset('voice_card')}>
            {profile?.voice_card_url ? '更换语音卡' : '上传语音卡'}
          </Button>
          </View>
        </View>
        <Text className={styles.assetHint}>支持 MP3、M4A、WAV、AAC、OGG，最大 10MB；上传后可在老板端试听。</Text>
        </View>
        <View className={styles.assetField}>
          <Text className={styles.label}>查外挂凭证</Text>
          <Button size="mini" loading={uploading === 'cheat_proof'} disabled={!!uploading} onClick={() => uploadAsset('cheat_proof')}>
            {profile?.cheat_proof_url ? '更换凭证' : '上传凭证'}
          </Button>
        </View>
        </View>
        <View className={styles.fields}>
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
        </View>
        <View className={styles.formActions}><Button className={`xa-button-primary ${styles.primaryButton}`} loading={savingProfile} disabled={savingProfile} onClick={submitProfile}>保存资料</Button></View>
      </View>

      <View className={styles.card}>
        <Text className={styles.title}>每周接单档期</Text>
        <Text className={styles.subtitle}>选择每周可接单的 6 小时时段；未设置时不限接单时间。暂停接单请在接单状态中切换为离线。</Text>
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
        <View className={styles.formActions}><Button className={`xa-button-primary ${styles.primaryButton}`} loading={savingSchedule} disabled={savingSchedule} onClick={submitSchedule}>保存档期</Button></View>
      </View>
      </View>
    </View></PlatformLayout>
  );
};

export default ProfilePage;
