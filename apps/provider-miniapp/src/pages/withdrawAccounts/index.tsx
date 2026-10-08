import { useEffect, useRef, useState } from 'react';
import { Button, Input, Text, View } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import PlatformLayout from '@/components/PlatformLayout';
import SurfaceDialog from '@/components/SurfaceDialog';
import { deleteWithdrawalAccount, fetchWithdrawalAccounts, PayeeMethod, SavedWithdrawalAccount, saveWithdrawalAccount } from '@/services/wallet';
import styles from './index.module.scss';

const METHODS: { value: PayeeMethod; label: string; placeholder: string }[] = [
  { value: 'WECHAT', label: '微信', placeholder: '请输入微信号' },
  { value: 'ALIPAY', label: '支付宝', placeholder: '请输入支付宝账号' },
  { value: 'BANK', label: '银行卡', placeholder: '请输入银行卡号' },
];

export default function WithdrawalAccountsPage() {
  const [accounts, setAccounts] = useState<SavedWithdrawalAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editor, setEditor] = useState<{ method: PayeeMethod; id: number; version: number } | null>(null);
  const [number, setNumber] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false), sequence = useRef(0), alive = useRef(true), openedQuery = useRef(false);
  const [editorError, setEditorError] = useState('');
  const [conflict, setConflict] = useState(false);
  const edit = (method: PayeeMethod, rows = accounts) => {
    if (busyRef.current) return;
    const row = rows.find(item => item.payee_method === method);
    setEditor({ method, id: row?.id || 0, version: row?.version || 0 });
    setNumber(row?.payee_account || ''); setName(row?.payee_name || '');
    setEditorError(''); setConflict(false);
  };
  const load = async () => {
    const version = ++sequence.current; setLoading(true); setError('');
    try {
      const result = await fetchWithdrawalAccounts();
      if (!alive.current || version !== sequence.current) return;
      if (result.code !== 0 || !result.data) throw new Error(result.msg || '收款账户加载失败');
      setAccounts(result.data);
      const method = Taro.getCurrentInstance().router?.params.method;
      if (!openedQuery.current && METHODS.some(item => item.value === method)) {
        openedQuery.current = true; edit(method as PayeeMethod, result.data);
      }
    } catch (reason) {
      if (alive.current && version === sequence.current) setError(reason instanceof Error ? reason.message : '加载失败，请重试');
    } finally { if (alive.current && version === sequence.current) setLoading(false); }
  };
  useEffect(() => { alive.current = true; load(); return () => { alive.current = false; sequence.current++; }; }, []);
  useDidShow(() => { if (!busyRef.current) load(); });

  const save = async () => {
    if (!editor || busyRef.current || conflict || loading) return;
    if (!number.trim() || !name.trim()) { setEditorError('请填写收款账号与真实姓名'); return; }
    busyRef.current = true; setBusy(true); setEditorError('');
    try {
      const result = await saveWithdrawalAccount(editor.method, {
        payee_account: number.trim(), payee_name: name.trim(), expected_id: editor.id, expected_version: editor.version,
      });
      if (!alive.current) return;
      if (result.code !== 0 || !result.data) {
        if (result.errorCode === 'PAYEE_CHANGED') { setConflict(true); await load(); }
        throw new Error(result.msg || '保存失败，请重试');
      }
      sequence.current++;
      setAccounts(previous => [...previous.filter(row => row.payee_method !== editor.method), result.data!]);
      setEditor(null);
      Taro.showToast({ title: '收款账户已保存', icon: 'success' });
    } catch (reason) { if (alive.current) setEditorError(reason instanceof Error ? reason.message : '保存失败，请重试'); }
    finally { busyRef.current = false; if (alive.current) setBusy(false); }
  };
  const unbind = async (row: SavedWithdrawalAccount) => {
    if (busyRef.current || loading) return;
    busyRef.current = true; setBusy(true);
    try {
      const result = await Taro.showModal({ title: '解绑收款账户', content: '解绑后可重新绑定。已提交的提现申请仍使用原收款资料。', confirmText: '确认解绑' });
      if (!result.confirm || !alive.current) return;
      const response = await deleteWithdrawalAccount(row);
      if (!alive.current) return;
      if (response.code !== 0) { await load(); throw new Error(response.msg || '解绑失败，请重试'); }
      sequence.current++; setAccounts(previous => previous.filter(item => item.id !== row.id));
      Taro.showToast({ title: '已解绑', icon: 'success' });
    } catch (reason) { if (alive.current) Taro.showToast({ title: reason instanceof Error ? reason.message : '解绑失败', icon: 'none' }); }
    finally { busyRef.current = false; if (alive.current) setBusy(false); }
  };
  const method = METHODS.find(item => item.value === editor?.method);
  const backToWallet = () => {
    if (Taro.getCurrentPages().length > 1) {
      return Taro.navigateBack().catch(() => Taro.switchTab({ url: '/pages/wallet/index' }));
    }
    return Taro.switchTab({ url: '/pages/wallet/index' });
  };

  return <PlatformLayout active='wallet'><View className={`xa-provider-page ${styles.page}`}>
    <View className={styles.heading}><View><Text className={styles.title}>提现账户</Text><Text className={styles.hint}>微信、支付宝、银行卡各可绑定一个</Text></View><Button size='mini' disabled={busy} onClick={backToWallet}>返回钱包</Button></View>
    {!!error && <View className={styles.error}><Text>{error}</Text><Button size='mini' disabled={busy} onClick={load}>重新加载</Button></View>}
    {loading ? <Text className={styles.hint}>正在读取收款账户…</Text> : !error && <View className={styles.accounts}>{METHODS.map(item => {
      const row = accounts.find(account => account.payee_method === item.value);
      return <View key={item.value} className={styles.card}>
        <View className={styles.cardHeading}><Text className={styles.method}>{item.label}</Text><Text className={styles.badge}>{row ? '已绑定' : '未绑定'}</Text></View>
        <Text className={styles.name}>{row?.payee_name || '添加收款账户'}</Text>
        <Text className={styles.number}>{row ? '•••• ' + row.payee_account.slice(-4) : '绑定后可在提现时直接选择'}</Text>
        <View className={styles.actions}><Button size='mini' disabled={busy} onClick={() => edit(item.value)}>{row ? '修改账户' : '绑定账户'}</Button>{row && <Button size='mini' disabled={busy} onClick={() => unbind(row)}>解绑</Button>}</View>
      </View>;
    })}</View>}
    <Text className={styles.hint}>修改或解绑只影响后续申请，已提交申请的收款资料保持不变。</Text>
    <SurfaceDialog open={!!editor} title={`${editor?.id ? '修改' : '绑定'}${method?.label || ''}账户`} busy={busy} onClose={() => setEditor(null)} footer={editor && (conflict ? <Button disabled={busy || loading} onClick={() => edit(editor.method)}>重新载入最新账户</Button> : <Button className={`xa-button-primary ${styles.save}`} disabled={busy || loading || !number.trim() || !name.trim()} loading={busy} onClick={save}>保存账户</Button>)}>
      <Text className={styles.label}>收款账号</Text><Input className={styles.input} disabled={busy || conflict} value={number} maxlength={100} placeholder={method?.placeholder} onInput={event => setNumber(event.detail.value)} />
      <Text className={styles.label}>收款人姓名</Text><Input className={styles.input} disabled={busy || conflict} value={name} maxlength={50} placeholder='请输入真实姓名' onInput={event => setName(event.detail.value)} />
      <Text className={styles.hint}>请核对可正常收款的账号与姓名，客服将按提现申请中的资料打款。</Text>
      {!!editorError && <Text className={styles.errorText}>{editorError}</Text>}
    </SurfaceDialog>
  </View></PlatformLayout>;
}
