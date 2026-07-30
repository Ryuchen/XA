import React, { useEffect, useState } from 'react';
import { Button, ScrollView, Text, View } from '@tarojs/components';
import Taro, { usePullDownRefresh } from '@tarojs/taro';
import {
  SkillOptionGroup,
  fetchEscortMe,
  fetchSkillOptions,
  updateEscortSkills,
} from '@/services/user';
import { Loading } from '@/components';
import styles from './index.module.scss';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';

const SkillsPage: React.FC = () => {
  const [groups, setGroups] = useState<SkillOptionGroup[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [optionsRes, meRes] = await Promise.all([fetchSkillOptions(), fetchEscortMe()]);
      if (optionsRes.code === 0 && optionsRes.data) setGroups(optionsRes.data);
      if (meRes.code === 0 && meRes.data) {
        setSelected(new Set(meRes.data.service_item_ids || []));
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '加载失败', icon: 'none' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);
  usePullDownRefresh(async () => { await load(); Taro.stopPullDownRefresh(); });

  const toggle = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const submit = async () => {
    setSaving(true);
    try {
      const res = await updateEscortSkills(Array.from(selected));
      if (res.code === 0) {
        if (res.data) setSelected(new Set(res.data.service_item_ids || []));
        Taro.showToast({ title: '技能已保存', icon: 'success' });
      } else {
        Taro.showToast({ title: res.msg || '保存失败', icon: 'none' });
      }
    } catch (error) {
      Taro.showToast({ title: error instanceof Error ? error.message : '保存失败', icon: 'none' });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Loading fullscreen text="加载技能中…" />;

  return (
    <PlatformLayout active='mine'><ScrollView className={styles.container} scrollY>
      <PageNav title="我的技能" />
      <View className={styles.tip}>勾选你可以接单的具体服务项目；选中后，老板下单选到对应项目时才会看到你。</View>

      {groups.length === 0 ? (
        <View className={styles.empty}>暂无可选服务项，请联系运营配置游戏与服务项目。</View>
      ) : groups.map(group => (
        <View key={group.game_category_id} className={styles.card}>
          <Text className={styles.title}>{group.game_category_name}</Text>
          <View className={styles.itemList}>
            {group.items.map(item => {
              const active = selected.has(item.id);
              return (
                <View
                  key={item.id}
                  className={`${styles.item} ${active ? styles.itemActive : ''}`}
                  onClick={() => toggle(item.id)}
                >
                  <Text className={styles.itemName}>{item.name}</Text>
                  <Text className={styles.itemPrice}>¥{(item.price / 100).toFixed(2)}</Text>
                </View>
              );
            })}
          </View>
        </View>
      ))}

      <Button className={styles.primaryButton} loading={saving} disabled={saving} onClick={submit}>保存技能</Button>
    </ScrollView></PlatformLayout>
  );
};

export default SkillsPage;
