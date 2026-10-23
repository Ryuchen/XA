import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Picker, ScrollView, Text, View } from '@tarojs/components';
import Taro, { usePullDownRefresh } from '@tarojs/taro';
import { SkillOptionGroup, fetchEscortMe, fetchSkillOptions, updateEscortSkills } from '@/services/user';
import { Loading } from '@/components';
import PageNav from '@/components/PageNav';
import PlatformLayout from '@/components/PlatformLayout';
import { formatXaCoin } from '@/utils/format';
import styles from './index.module.scss';

const SkillsPage: React.FC = () => {
  const [groups, setGroups] = useState<SkillOptionGroup[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [saved, setSaved] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [keyword, setKeyword] = useState('');
  const [game, setGame] = useState(0);
  const [platform, setPlatform] = useState(0);
  const [difficulty, setDifficulty] = useState(0);
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [limit, setLimit] = useState(24);
  const platforms = ['', 'MOBILE', 'PC'];
  const difficulties = ['', 'CONFIDENTIAL', 'TOP_SECRET', 'ANY'];
  const busy = useRef(false);
  const sequence = useRef(0);
  const dirty = selected.size !== saved.size || [...selected].some(id => !saved.has(id));
  const offered = new Set(groups.flatMap(group => group.items.map(item => item.id)));
  const retired = [...selected].filter(id => !offered.has(id));
  const selectedGames = groups.filter(group => group.items.some(item => selected.has(item.id))).length;
  const load = async () => {
    if (busy.current) return;
    const current = ++sequence.current;
    setLoading(true); setError('');
    try {
      const [optionsRes, meRes] = await Promise.all([fetchSkillOptions(), fetchEscortMe()]);
      if (optionsRes.code !== 0 || !optionsRes.data) throw new Error(optionsRes.msg || '服务目录加载失败');
      if (meRes.code !== 0 || !meRes.data) throw new Error(meRes.msg || '已选技能加载失败');
      if (current !== sequence.current) return;
      setGroups(optionsRes.data);
      const ids = new Set(meRes.data.service_item_ids || []);
      setSelected(ids); setSaved(ids); setGame(0);
    } catch (cause) {
      if (current === sequence.current) setError(cause instanceof Error ? cause.message : '加载失败');
    } finally { if (current === sequence.current) setLoading(false); }
  };
  useEffect(() => { load(); return () => { sequence.current += 1; }; }, []);
  usePullDownRefresh(async () => {
    try {
      if (dirty && !(await Taro.showModal({ title: '重新读取技能', content: '当前有未保存的选择，刷新将放弃这些修改。' })).confirm) return;
      await load();
    } finally { Taro.stopPullDownRefresh(); }
  });
  const toggle = (id: number) => {
    if (busy.current) return;
    setSelected(prev => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; });
  };
  const submit = async () => {
    if (busy.current || error || loading || !dirty || retired.length) return;
    busy.current = true;
    try {
      if (!selected.size && !(await Taro.showModal({ title: '清空服务技能', content: '清空后，你将不再出现在具体游戏服务的选人列表，也无法接取这些服务的新订单。' })).confirm) return;
      setSaving(true);
      const res = await updateEscortSkills([...selected]);
      if (res.code !== 0 || !res.data) throw new Error(res.msg || '保存失败，请重试');
      const ids = new Set(res.data.service_item_ids || []);
      setSelected(ids); setSaved(ids);
      Taro.showToast({ title: '技能已同步', icon: 'success' });
    } catch (cause) {
      Taro.showToast({ title: cause instanceof Error ? cause.message : '保存失败', icon: 'none' });
    } finally { busy.current = false; setSaving(false); }
  };
  const visible = groups.filter(group => !game || group.game_category_id === game).map(group => ({
    ...group, items: group.items.filter(item => `${group.game_category_name} ${item.name}`.includes(keyword.trim())
      && (!platform || item.platform === platforms[platform])
      && (!difficulty || item.security_level === difficulties[difficulty])
      && (!selectedOnly || selected.has(item.id))),
  })).filter(group => group.items.length);
  return <PlatformLayout active='mine'><View className={`xa-provider-page ${styles.container}`}>
    <PageNav title='我的技能' />
    {loading ? <Loading text='加载服务目录…' /> : error ? <View className={styles.empty}>
      <Text className={styles.title}>技能暂未加载</Text><Text>{error}</Text>
      <Button className={styles.secondaryButton} onClick={load}>重新加载</Button>
    </View> : <>
      <View className={styles.overview}>
        <Text className={styles.eyebrow}>服务范围</Text>
        <Text className={styles.heading}>{selected.size - retired.length} 项技能 · {selectedGames} 款游戏</Text>
        <Text className={styles.description}>选择你能稳定完成的服务。保存后同步老板选人列表与后台档案，接单仍需满足认证、押金、档期和具体接单资格要求。</Text>
      </View>
      {retired.length > 0 && <View className={styles.notice}>
        <Text>已选的 {retired.length} 项服务已停用或调整，请移除后保存。当前其他选择将保留。</Text>
        <Button className={styles.secondaryButton} disabled={saving} onClick={() => setSelected(new Set([...selected].filter(id => offered.has(id))))}>移除失效项目</Button>
      </View>}
      <View className={styles.filters}>
        <Input className={styles.search} placeholder='搜索游戏或服务名称' value={keyword} onInput={event => { setKeyword(event.detail.value); setLimit(24); }} />
        <ScrollView scrollX className={styles.tabs}><View className={styles.tabRow}>
          <Button className={`${styles.tab} ${!game ? styles.tabActive : ''}`} onClick={() => { setGame(0); setLimit(24); }}>全部游戏</Button>
          {groups.map(group => <Button key={group.game_category_id} className={`${styles.tab} ${game === group.game_category_id ? styles.tabActive : ''}`} onClick={() => { setGame(group.game_category_id); setLimit(24); }}>{group.game_category_name}</Button>)}
        </View></ScrollView>
        <View className={styles.scopeFilters}>
          <Picker range={['全部平台', '手游', '端游']} value={platform} onChange={event => { setPlatform(Number(event.detail.value)); setLimit(24); }}><View className={styles.filterControl}>{['全部平台', '手游', '端游'][platform]} ▾</View></Picker>
          <Picker range={['全部难度', '机密', '绝密', '不限难度']} value={difficulty} onChange={event => { setDifficulty(Number(event.detail.value)); setLimit(24); }}><View className={styles.filterControl}>{['全部难度', '机密', '绝密', '不限难度'][difficulty]} ▾</View></Picker>
          <Button className={`${styles.tab} ${selectedOnly ? styles.tabActive : ''}`} onClick={() => { setSelectedOnly(!selectedOnly); setLimit(24); }}>只看已选</Button>
        </View>
      </View>
      <View className={styles.saveBar}><View><Text className={styles.saveTitle}>{dirty ? '有未保存的修改' : '已与服务档案同步'}</Text><Text className={styles.description}>套餐售价不代表陪玩实得收入</Text></View><Button className={`xa-button-primary ${styles.primaryButton}`} loading={saving} disabled={saving || !dirty || retired.length > 0} onClick={submit}>保存技能</Button></View>
      {!visible.length && <View className={styles.empty}>{groups.length ? '没有匹配的服务，试试其他名称或游戏。' : '暂无开放的游戏服务，请联系运营配置。'}</View>}
      {visible.map(group => <View key={group.game_category_id} className={styles.card}>
        <View className={styles.groupHead}><Text className={styles.title}>{group.game_category_name}</Text><Text className={styles.count}>{group.items.filter(item => selected.has(item.id)).length} / {group.items.length} 已选</Text></View>
        <View className={styles.itemList}>{group.items.slice(0, limit).map(item => <Button key={item.id} disabled={saving || (item.qualified === false && !selected.has(item.id))} aria-label={`${item.name}，${selected.has(item.id) ? '已选择' : '未选择'}`} className={`${styles.item} ${selected.has(item.id) ? styles.itemActive : ''}`} onClick={() => toggle(item.id)}>
          <View className={styles.itemHeading}><Text className={styles.itemName}>{item.name}</Text><Text className={styles.check}>{selected.has(item.id) ? '已选' : '选择'}</Text></View>
          <Text className={styles.requirement}>{[item.platform_name, item.security_level_name].filter(Boolean).join(' · ') || '通用服务'}</Text>
          <Text className={styles.itemPrice}>{formatXaCoin(item.price)} 币 / {item.price_unit || '份'}</Text>
          <Text className={styles.requirement}>{item.provider_requirement?.label ? `${item.provider_requirement.label} · ${item.qualified ? '资格已满足' : '需运营认证'}` : item.required_level_name ? `${item.required_level_name}及以上可接` : '历史服务规则'}</Text>
        </Button>)}</View>
        {group.items.length > limit && <Button className={styles.secondaryButton} onClick={() => setLimit(limit + 24)}>继续查看（还有 {group.items.length - limit} 项）</Button>}
      </View>)}

    </>}
  </View></PlatformLayout>;
};
export default SkillsPage;
