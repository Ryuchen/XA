import React, { useEffect, useRef, useState } from 'react';
import { Button, Image, Input, Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { formatRmb } from '@xa/money';
import XaLogo from '@/components/XaLogo';
import { queryRegistration, submitRegistration, validateRegistration, fetchRegistrationRules } from '@/services/registration';
import type { DepositRules, RegistrationErrors, RegistrationForm, RegistrationPhotos, RegistrationResult, PhotoField } from '@/services/registration';
import styles from './index.module.scss';

const PHOTO_LABELS: Record<PhotoField, { title: string; hint: string }> = {
  id_front: { title: '身份证正面', hint: '人像面，姓名与号码清晰可见' },
  id_back: { title: '身份证反面', hint: '国徽面，证件有效期清晰可见' },
  deposit_proof: { title: '押金交付凭证', hint: '支付时间、金额和收款方清晰可见' },
};
const EMPTY: RegistrationForm = { username: '', password: '', phone: '', real_name: '', id_number: '', display_name: '', deposit_mode: 'CASH' };

const RegisterPage: React.FC = () => {
  const [rules, setRules] = useState<DepositRules | null>(null);
  const [ruleError, setRuleError] = useState('');
  const [mode, setMode] = useState<'apply' | 'status'>('apply');
  const [form, setForm] = useState<RegistrationForm>(EMPTY);
  const [confirmation, setConfirmation] = useState('');
  const [photos, setPhotos] = useState<RegistrationPhotos>({});
  const [errors, setErrors] = useState<RegistrationErrors>({});
  const [error, setError] = useState('');
  const [result, setResult] = useState<RegistrationResult | null>(null);
  const [resubmit, setResubmit] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const chosen = useRef<PhotoField>('id_front');
  const photosRef = useRef<RegistrationPhotos>({});
  const active = useRef(true);
  const submitting = useRef(false);
  photosRef.current = photos;
  const loadRules = () => { setRuleError(''); fetchRegistrationRules().then(value => { if (active.current) setRules(value); }).catch(() => { if (active.current) setRuleError('押金规则加载失败，请重试'); }); };
  useEffect(() => { active.current = true; loadRules(); return () => {
    active.current = false;
    if (process.env.TARO_ENV === 'h5') Object.values(photosRef.current).forEach(photo => photo?.path.startsWith('blob:') && URL.revokeObjectURL(photo.path));
  }; }, []);

  const patch = <K extends keyof RegistrationForm>(field: K, value: RegistrationForm[K]) => {
    setForm(current => ({ ...current, [field]: value }));
    setErrors(current => ({ ...current, [field]: undefined }));
  };
  const putPhoto = (field: PhotoField, photo: NonNullable<RegistrationPhotos[PhotoField]>) => {
    if (photo.size > 5 * 1024 * 1024) { setErrors(current => ({ ...current, [field]: '照片不能超过5MB' })); return; }
    setPhotos(current => {
      if (process.env.TARO_ENV === 'h5' && current[field]?.path.startsWith('blob:')) URL.revokeObjectURL(current[field]!.path);
      return { ...current, [field]: photo };
    });
    setErrors(current => ({ ...current, [field]: undefined }));
  };

  const choosePhoto = async (field: PhotoField) => {
    if (busy) return;
    chosen.current = field;
    if (process.env.TARO_ENV === 'h5') { input.current?.click(); return; }
    try {
      const selected = await Taro.chooseImage({ count: 1, sourceType: ['album', 'camera'], sizeType: ['compressed'] });
      const file = selected.tempFiles[0];
      putPhoto(field, { path: selected.tempFilePaths[0], name: field + '.jpg', size: file.size });
    } catch (failure) {
      if (!(failure as { errMsg?: string }).errMsg?.includes('cancel')) setError('照片选择失败，请重试');
    }
  };
  const browserPhoto = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { setErrors(current => ({ ...current, [chosen.current]: '请选择JPG、PNG或WebP照片' })); return; }
    if (file.size > 5 * 1024 * 1024) { setErrors(current => ({ ...current, [chosen.current]: '照片不能超过5MB' })); return; }
    putPhoto(chosen.current, { path: URL.createObjectURL(file), name: file.name, size: file.size, file });
  };

  const submit = async () => {
    if (submitting.current || (mode === 'apply' && !rules)) return;
    const validation = validateRegistration(form, photos, confirmation, mode === 'status');
    setErrors(validation); setError('');
    if (Object.keys(validation).length) {
      Taro.pageScrollTo({ selector: '#registration-field-' + Object.keys(validation)[0], duration: 200 });
      return;
    }
    submitting.current = true; setBusy(true);
    try {
      const next = mode === 'status'
        ? await queryRegistration(form.username, form.password)
        : await submitRegistration(form, photos, resubmit);
      if (!active.current) return;
      setResult(next);
      if (mode === 'apply') {
        if (process.env.TARO_ENV === 'h5') Object.values(photosRef.current).forEach(photo => photo?.path.startsWith('blob:') && URL.revokeObjectURL(photo.path));
        setPhotos({});
        setForm(current => ({ ...EMPTY, username: current.username, password: current.password }));
        setConfirmation('');
        setMode('status'); setResubmit(false);
      }
    } catch (failure) {
      if (!active.current) return;
      setError(failure instanceof Error ? failure.message : '提交失败，请检查网络后重试');
      setErrors((failure as { fields?: RegistrationErrors }).fields || {});
    } finally {
      submitting.current = false;
      if (active.current) setBusy(false);
    }
  };

  const field = (name: Exclude<keyof RegistrationForm, 'deposit_mode'>, label: string, placeholder: string, limit: number, password = false) => <View className={styles.field} key={name} id={'registration-field-' + name}>
    <Text className={styles.label}>{label}<Text className={styles.required}> *</Text></Text>
    <Input className={styles.input + (errors[name] ? ' ' + styles.invalid : '')} value={form[name]} placeholder={placeholder} maxlength={limit}
      password={password} disabled={busy || (resubmit && name === 'username')} onInput={event => patch(name, event.detail.value)} aria-label={label} />
    {errors[name] && <Text className={styles.fieldError}>{errors[name]}</Text>}
  </View>;

  return <View className={styles.page + ' xa-provider-public'}>
    <View className={styles.top}>
      <View className={styles.brand}><XaLogo size={64} color='#136398' /><Text>兴安电竞</Text></View>
      <Button className={styles.back} onClick={() => Taro.reLaunch({ url: '/pages/login/index' })}>返回登录</Button>
    </View>
    <View className={styles.heading}><Text className={styles.title}>{mode === 'status' ? '申请审核进度' : resubmit ? '补充注册资料' : '申请成为陪玩'}</Text>
      <Text className={styles.description}>提交实名与押金资料，由管理人员审核。通过后使用注册账号登录并绑定验证器。</Text>
    </View>
    <View className={styles.tabs}>
      <Button className={styles.tab + (mode === 'apply' ? ' ' + styles.selected : '')} disabled={busy} onClick={() => { setMode('apply'); setError(''); setErrors({}); }}>填写申请</Button>
      <Button className={styles.tab + (mode === 'status' ? ' ' + styles.selected : '')} disabled={busy} onClick={() => { setMode('status'); setError(''); setErrors({}); }}>查询审核</Button>
    </View>
    <View className={styles.form}>
      <View className={styles.section}>
        <Text className={styles.sectionTitle}>{mode === 'status' ? '使用注册时的账号密码查询' : '登录信息'}</Text>
        <View className={styles.fields}>
          {field('username', '登录账号', '4至32位字母、数字或下划线', 32)}
          {field('password', '登录密码', '至少8位，请牢记', 128, true)}
          {mode === 'apply' && <>
            <View className={styles.field} id="registration-field-confirm_password"><Text className={styles.label}>确认密码<Text className={styles.required}> *</Text></Text>
              <Input className={styles.input + (errors.confirm_password ? ' ' + styles.invalid : '')} value={confirmation} password placeholder='再次输入登录密码' maxlength={128} disabled={busy}
                aria-label='确认密码' onInput={event => { setConfirmation(event.detail.value); setErrors(current => ({ ...current, confirm_password: undefined })); }} />
              {errors.confirm_password && <Text className={styles.fieldError}>{errors.confirm_password}</Text>}
            </View>
            {field('phone', '联系手机号', '用于审核联系', 11)}
          </>}
        </View>
      </View>
      {mode === 'apply' && <>
        <View className={styles.section}><Text className={styles.sectionTitle}>实名与结单昵称</Text>
          <Text className={styles.sectionHint}>真实姓名与证件一致，结单昵称用于接单和报单展示。</Text>
          <View className={styles.fields}>
            {field('real_name', '真实姓名', '填写身份证上的姓名', 50)}
            {field('id_number', '身份证号码', '18位身份证号码', 18)}
            <View className={styles.fullField}>{field('display_name', '结单昵称', '填写你在平台使用的昵称', 50)}</View>
          </View>
        </View>
        <View className={styles.section}><Text className={styles.sectionTitle}>押金方式</Text>
          {ruleError ? <View className={styles.error}><Text>{ruleError}</Text><Button className={styles.secondary} onClick={loadRules}>重试加载</Button></View> : !rules ? <Text className={styles.sectionHint}>正在读取押金规则</Text> : <>
            <View className={styles.depositModes}>
              {(['CASH', 'EARNINGS'] as const).map(value => <Button key={value} className={styles.depositMode + (form.deposit_mode === value ? ' ' + styles.selectedMode : '')} disabled={busy} onClick={() => patch('deposit_mode', value)}>
                <Text className={styles.modeTitle}>{value === 'CASH' ? '现金缴纳' : '单抵双倍'}</Text>
                <Text className={styles.modeAmount}>{formatRmb(value === 'CASH' ? rules.cash_target : rules.earnings_target)} 元</Text>
                <Text className={styles.modeHint}>{value === 'CASH' ? '上传押金交付凭证，审核到账' : '从后续订单净收益抵扣押金'}</Text>
              </Button>)}
            </View>
            <Text className={styles.sectionHint}>单抵押金为现金押金的{rules.earnings_multiplier}倍。当前净收益{rules.deposit_percent}%抵押金、{rules.settlement_percent}%结款，抵满后全部结款。{rules.hold_hours ? '结款部分冻结' + rules.hold_hours + '小时后解冻。' : ''}</Text>
          </>}
        </View>
        <View className={styles.section}><Text className={styles.sectionTitle}>上传审核材料</Text>
          <Text className={styles.sectionHint}>资料仅供有权限的管理人员审核，不公开展示。每张照片不超过5MB。</Text>
          <View className={styles.photoGrid + (form.deposit_mode === 'EARNINGS' ? ' ' + styles.identityGrid : '')}>{(Object.keys(PHOTO_LABELS) as PhotoField[]).filter(name => name !== 'deposit_proof' || form.deposit_mode === 'CASH').map(name => <View className={styles.photoField + (name === 'deposit_proof' ? ' ' + styles.receipt : '')} key={name} id={'registration-field-' + name}>
            <Text className={styles.label}>{PHOTO_LABELS[name].title}<Text className={styles.required}> *</Text></Text>
            <Button className={styles.photoSlot + (errors[name] ? ' ' + styles.invalid : '')} disabled={busy} aria-label={'上传' + PHOTO_LABELS[name].title} onClick={() => choosePhoto(name)}>
              {photos[name] ? <><Image className={styles.photo} src={photos[name]!.path} mode='aspectFit' /><Text className={styles.replace}>重新选择照片</Text></>
                : <View className={styles.photoPrompt}><Text className={styles.plus}>+</Text><Text>选择照片</Text><Text className={styles.photoFormat}>JPG / PNG / WebP</Text></View>}
            </Button>
            <Text className={styles.photoHint}>{PHOTO_LABELS[name].hint}</Text>
            {errors[name] && <Text className={styles.fieldError}>{errors[name]}</Text>}
          </View>)}</View>
          <View className={styles.notice}><Text>{form.deposit_mode === 'CASH' ? '现金押金凭证须由管理人员核实实际到账。' : '单抵方式通过审核后，按平台比例从后续净收益抵扣，不登记现金到账。'}提交申请后需等待人工审核。</Text></View>
        </View>
      </>}
      {error && <View className={styles.error} role='alert'><Text>{error}</Text></View>}
      {mode === 'status' && result && <View className={styles.result}>
        <Text className={styles.resultStatus}>{result.status_display}</Text>
        <Text className={styles.resultName}>{result.display_name}</Text>
        <Text className={styles.resultHint}>{result.status === 'PENDING' ? '资料已提交，请等待管理人员审核。可使用本账号密码查询最新进度。' : result.status === 'APPROVED' ? '账号已开通。返回登录，使用注册账号密码并完成验证器绑定。' : '请按下面的审核意见补充资料，再重新提交。'}</Text>
        {result.review_note && <View className={styles.reviewNote}><Text>审核意见</Text><Text>{result.review_note}</Text></View>}
        {result.status === 'APPROVED' && <Button className={styles.secondary} onClick={() => Taro.reLaunch({ url: '/pages/login/index' })}>前往登录</Button>}
        {result.status === 'REJECTED' && <Button className={styles.secondary} onClick={() => { setMode('apply'); setResubmit(true); setForm(current => ({ ...current, display_name: result.display_name, deposit_mode: result.deposit_mode })); }}>补充资料并重新提交</Button>}
      </View>}
      <View className={styles.submitRow}><Text className={styles.submitHint}>{mode === 'status' ? '申请进度仅凭你的注册账号密码查询。' : '确认资料清晰、完整后提交。'}</Text>
        <Button className={styles.primary + ' xa-button-primary'} loading={busy} disabled={busy || (mode === 'apply' && !rules)} onClick={submit}>{busy ? '正在提交' : mode === 'status' ? '查询进度' : resubmit ? '重新提交审核' : '提交审核'}</Button>
      </View>
    </View>
    {process.env.TARO_ENV === 'h5' && <input ref={input} className={styles.hiddenInput} type='file' accept='image/jpeg,image/png,image/webp' onChange={browserPhoto} />}
  </View>;
};
export default RegisterPage;
