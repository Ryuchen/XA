import { useState, type FormEvent, type ReactNode } from 'react'
import { AlertCircle, ArrowLeft, ArrowRight, BadgeCheck, Check, CheckCircle2, ClipboardCheck, FileImage, LoaderCircle, RefreshCw, ShieldCheck, Upload, UserRoundPlus, WalletCards } from 'lucide-react'
import { NavLink, useLocation, useNavigate, useSearchParams } from 'react-router'
import { WeChatLoginCard } from '@/components/auth/WeChatLoginCard'
import { ProviderWeChatBinding } from '@/components/provider/ProviderWeChatBinding'
import { useStore } from '@/components/store/store-context'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { cn } from '@/lib/utils'
import type { ProviderScenario } from '@/pages/provider/provider-data'

type DepositMode = 'CASH' | 'EARNINGS'
type ReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED'

export function ProviderPublicFrame({ children, centerContent = false }: { children: ReactNode; centerContent?: boolean }) {
  const { store } = useStore()
  const location = useLocation()
  return <div className="phone-frame" aria-label={`${store.name}陪玩账号入口`} data-store-scope={store.slug} data-store-page={location.pathname}>
    <div className="phone-screen">
      <div className={cn('phone-scroll phone-scrollbar', centerContent && 'md:flex md:flex-col')} data-testid="provider-public-scroll">
        <header className="sticky top-0 z-30 h-16 shrink-0 border-b bg-background/90 backdrop-blur-xl">
          <div className="mx-auto flex h-full max-w-[1120px] items-center gap-3 px-3 md:px-6">
            <NavLink to="/provider-login" className="flex min-w-0 items-center gap-2 rounded-lg" aria-label={`${store.name}陪玩登录`}>
              <img src="./brand.svg" alt="" className="size-8 shrink-0" />
              <span className="hidden min-[360px]:block"><span className="block max-w-28 truncate text-sm font-semibold leading-5">{store.shortName}</span><span className="block text-xs leading-4 text-muted-foreground">陪玩账号入口</span></span>
            </NavLink>
          </div>
        </header>
        {children}
      </div>
    </div>
  </div>
}

function AuthNotice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <p role={error ? 'alert' : 'status'} className={cn('flex items-start gap-2 rounded-[8px] border p-3 text-xs leading-5', error ? 'border-destructive/25 bg-destructive/5 text-destructive' : 'border-primary/20 bg-primary/5 text-primary')}><AlertCircle className="mt-0.5 size-4 shrink-0" />{children}</p>
}

function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return <div className="space-y-2"><Label htmlFor={id}>{label}</Label>{children}{error && <p role="alert" className="text-xs text-destructive">{error}</p>}</div>
}

function FormSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="rounded-[12px] border bg-card p-4 md:p-5"><div><h2 className="font-semibold">{title}</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p></div><div className="mt-5">{children}</div></section>
}

export function ProviderLoginPage({ scenario, onRetry }: { scenario: ProviderScenario; onRetry: () => void }) {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { store } = useStore()
  const redirect = params.get('redirect') || ''
  const auditionReturn = redirect.startsWith('/provider/audition?') && redirect.includes('token=') && redirect.includes('role=provider') ? redirect : null

  return <ProviderPublicFrame centerContent><main className="min-h-[640px] pb-8 md:grid md:min-h-[calc(100dvh-64px)] md:flex-1 md:grid-cols-[minmax(0,480px)_420px] md:grid-rows-[1fr_auto] md:items-center md:justify-center md:gap-x-8 md:gap-y-0 md:px-8 md:py-8 xl:grid-cols-[480px_460px]">
    <section className="px-5 pb-6 pt-4 md:px-0 md:pb-0 md:pt-6" aria-labelledby="provider-login-welcome">
      <Badge variant="outline" className="mt-0 rounded-md border-primary/20 bg-primary/5 text-xs text-primary md:mt-8">微信安全登录</Badge>
      <h1 id="provider-login-welcome" className="mt-4 max-w-lg text-[26px] font-semibold leading-[1.3] tracking-[-0.03em] md:text-[32px]">专注每一次服务，<br className="hidden md:block" />清楚每一份收益。</h1>
      <p className="mt-3 max-w-md text-sm leading-6 text-muted-foreground">使用微信快捷登录，同步头像与昵称，验证手机号后即可管理接单、履约与本店收益，也可按需开启二次验证。</p>
      <div className="mt-6 hidden max-w-md grid-cols-3 border-y py-4 md:grid"><LoginBenefit icon={ClipboardCheck} label="接单履约" /><LoginBenefit icon={WalletCards} label="收益结算" /><LoginBenefit icon={BadgeCheck} label="本店档案" /></div>
    </section>

    <WeChatLoginCard audience="provider" scenario={scenario} storeName={store.name} profileName={store.provider.nickname} onRetry={onRetry} onSuccess={auditionReturn ? () => navigate(auditionReturn, { state: { wechatAuthenticated: true } }) : undefined} successLabel={auditionReturn ? '返回试音邀请' : undefined} secondaryAction={<Button type="button" variant="ghost" className="mt-1 h-11 w-full rounded-[8px] text-xs text-primary" onClick={() => navigate('/provider-register')}><UserRoundPlus className="size-4" />申请成为陪玩 / 查询审核</Button>} />
    <footer className="px-5 pb-2 pt-6 text-center text-xs text-muted-foreground md:col-span-2 md:pb-0 md:pt-4">声浪电竞 · 让每一次组队更安心</footer>
  </main></ProviderPublicFrame>
}

function LoginBenefit({ icon: Icon, label }: { icon: typeof ClipboardCheck; label: string }) {
  return <div className="flex items-center gap-2 text-sm"><Icon className="size-4 text-primary" /><span>{label}</span></div>
}

interface RegisterForm {
  phone: string
  realName: string
  idNumber: string
  displayName: string
}

export function ProviderRegisterPage({ scenario }: { scenario: ProviderScenario }) {
  const navigate = useNavigate()
  const { store } = useStore()
  const initialStatus: ReviewStatus | null = scenario === 'empty' ? 'APPROVED' : scenario === 'error' ? 'REJECTED' : null
  const [mode, setMode] = useState<'apply' | 'status'>(initialStatus ? 'status' : 'apply')
  const [wechatBound, setWechatBound] = useState(Boolean(initialStatus))
  const [depositMode, setDepositMode] = useState<DepositMode>('CASH')
  const [form, setForm] = useState<RegisterForm>({ phone: initialStatus ? '13800000000' : '', realName: '示例用户', idNumber: '110101199001010000', displayName: store.provider.nickname })
  const [photos, setPhotos] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState('')
  const [result, setResult] = useState<ReviewStatus | null>(initialStatus)
  const [resubmit, setResubmit] = useState(false)
  const busy = scenario === 'loading'
  const requiredPhotos = depositMode === 'CASH' ? ['idFront', 'idBack', 'depositProof'] : ['idFront', 'idBack']
  const photoLabels: Record<string, string> = { idFront: '身份证正面（人像面）', idBack: '身份证反面（国徽面）', depositProof: '押金交付凭证' }

  function field(key: keyof RegisterForm, value: string) {
    setForm(old => ({ ...old, [key]: value }))
    setErrors(old => ({ ...old, [key]: '' }))
    setNotice('')
  }
  function fillMaterials() {
    setPhotos(Object.fromEntries(requiredPhotos.map(key => [key, `示例-${photoLabels[key]}.jpg`])))
    setErrors(old => ({ ...old, idFront: '', idBack: '', depositProof: '' }))
    setNotice('已填入本地示例材料名称，不会上传真实证件或凭证。')
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const next: Record<string, string> = {}
    if (!wechatBound || !/^1[3-9]\d{9}$/.test(form.phone)) next.wechat = mode === 'status' ? '请使用入驻时绑定的微信完成手机号验证' : '请先完成微信绑定、手机号授权与短信验证'
    if (mode === 'apply') {
      if (form.realName.trim().length < 2) next.realName = '请填写与证件一致的真实姓名'
      if (!/^[1-9]\d{16}[\dXx]$/.test(form.idNumber)) next.idNumber = '请输入 18 位身份证号码'
      if (!form.displayName.trim()) next.displayName = '请填写用于接单和报单的昵称'
      requiredPhotos.forEach(key => { if (!photos[key]) next[key] = '请选择材料，或使用本地示例材料' })
    }
    setErrors(next)
    if (Object.keys(next).length) return
    setResult('PENDING')
    setMode('status')
    setNotice(mode === 'apply' ? '入驻申请已提交为示例状态，等待本店管理人员审核。' : '已读取本店示例申请进度。')
    setResubmit(false)
  }

  return <ProviderPublicFrame><main className="mx-auto w-full max-w-4xl px-4 py-6 md:px-6 md:py-8">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><Button type="button" variant="ghost" className="-ml-3 h-11 px-3 text-muted-foreground" onClick={() => navigate('/provider-login')}><ArrowLeft className="size-4" />返回登录</Button><h1 className="mt-2 text-2xl font-semibold tracking-tight">{mode === 'status' ? '申请审核进度' : resubmit ? '补充注册资料' : '陪玩入驻申请'}</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">申请只提交至{store.name}，微信绑定、审核、账号、等级与押金记录均由本店独立管理。</p></div><Badge variant="outline" className="mt-1">示例资料</Badge></div>

    <div className="mt-6 grid grid-cols-2 rounded-[8px] bg-muted/50 p-1" role="group" aria-label="入驻申请步骤">{(['apply', 'status'] as const).map(item => <button key={item} type="button" aria-pressed={mode === item} className={cn('min-h-11 rounded-[6px] text-sm', mode === item ? 'bg-card font-medium text-primary shadow-sm' : 'text-muted-foreground')} disabled={busy} onClick={() => { setMode(item); setResult(null); setErrors({}); setNotice('') }}>{item === 'apply' ? '填写申请' : '查询审核'}</button>)}</div>
    {notice && <div className="mt-4"><AuthNotice>{notice}</AuthNotice></div>}

    <form className="mt-4 space-y-4" onSubmit={submit}>
      <FormSection title={mode === 'status' ? '微信查询审核' : '微信与手机号'} description={mode === 'status' ? '使用入驻时绑定的微信完成手机号验证，仅查询当前店申请。' : '微信快捷入驻需先绑定当前微信；手机号由微信授权读取，再通过短信验证归属。'}>
        {wechatBound ? <div className="flex items-center gap-3 rounded-[8px] border border-success/25 bg-success/5 p-4"><CheckCircle2 className="size-5 shrink-0 text-success" /><div><p className="text-sm font-semibold text-success">{mode === 'status' ? '微信与手机号已验证' : '微信与手机号已绑定'}</p><p className="mt-1 text-xs tabular-nums text-muted-foreground">138 **** 8000 · 仅用于{store.shortName}入驻与账号识别</p></div></div> : <ProviderWeChatBinding purpose={mode === 'status' ? 'status' : 'register'} storeName={store.name} disabled={busy} onComplete={phone => { setWechatBound(true); setForm(old => ({ ...old, phone })); setErrors(old => ({ ...old, wechat: '' })); setNotice('') }} />}
        {errors.wechat && <p role="alert" className="mt-3 text-xs text-destructive">{errors.wechat}</p>}
      </FormSection>

      {mode === 'apply' && <>
        <FormSection title="实名资料" description="真实姓名与证件仅用于本店入驻审核，结单昵称用于接单与报单展示。"><div className="grid gap-4 sm:grid-cols-3"><Field id="provider-register-realname" label="真实姓名" error={errors.realName}><Input id="provider-register-realname" value={form.realName} onChange={event => field('realName', event.target.value)} disabled={busy} /></Field><Field id="provider-register-idnumber" label="身份证号码" error={errors.idNumber}><Input id="provider-register-idnumber" value={form.idNumber} onChange={event => field('idNumber', event.target.value)} disabled={busy} /></Field><Field id="provider-register-displayname" label="结单昵称" error={errors.displayName}><Input id="provider-register-displayname" value={form.displayName} onChange={event => field('displayName', event.target.value)} disabled={busy} /></Field></div></FormSection>

        <FormSection title="押金方式" description="以下为本原型示例规则；正式金额与结算比例以当前店发布的规则版本为准。">
          <RadioGroup value={depositMode} onValueChange={value => { setDepositMode(value as DepositMode); setPhotos({}); setErrors({}) }} className="grid sm:grid-cols-2" disabled={busy}>
            <label className={cn('flex min-h-24 cursor-pointer items-start gap-3 rounded-[8px] border p-4', depositMode === 'CASH' && 'border-primary bg-primary/5')}><RadioGroupItem value="CASH" className="mt-1" aria-label="现金缴纳押金" /><span><span className="block text-sm font-semibold">现金缴纳 · 200 元</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">需上传押金凭证，由本店管理人员核实实际到账。</span></span></label>
            <label className={cn('flex min-h-24 cursor-pointer items-start gap-3 rounded-[8px] border p-4', depositMode === 'EARNINGS' && 'border-primary bg-primary/5')}><RadioGroupItem value="EARNINGS" className="mt-1" aria-label="订单收益抵扣押金" /><span><span className="block text-sm font-semibold">单抵双倍 · 400 元</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">不登记现金到账，按本店规则从后续订单净收益抵扣。</span></span></label>
          </RadioGroup>
        </FormSection>

        <FormSection title="审核材料" description="支持 JPG、PNG、WebP，每张最多 5 MB。原型仅记录文件名，不上传或保存文件。">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">当前需提交 {requiredPhotos.length} 项材料</p><Button type="button" variant="outline" size="sm" className="h-11" onClick={fillMaterials} disabled={busy}><FileImage className="size-4" />使用本地示例材料</Button></div>
          <div className="grid gap-3 sm:grid-cols-3">{requiredPhotos.map(key => <div key={key}><label className={cn('flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-[8px] border border-dashed p-3 text-center', photos[key] && 'border-success/40 bg-success/5')}><input type="file" className="sr-only" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (!file) return; if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) { setErrors(old => ({ ...old, [key]: '请选择 5 MB 以内的 JPG、PNG 或 WebP' })); return } setPhotos(old => ({ ...old, [key]: file.name })); setErrors(old => ({ ...old, [key]: '' })) }} />{photos[key] ? <Check className="size-5 text-success" /> : <Upload className="size-5 text-primary" />}<span className="mt-2 text-xs font-medium">{photoLabels[key]}</span><span className="mt-1 break-all text-xs text-muted-foreground">{photos[key] || '选择本地图片'}</span></label>{errors[key] && <p role="alert" className="mt-2 text-xs text-destructive">{errors[key]}</p>}</div>)}</div>
        </FormSection>
      </>}

      <Button type="submit" className="h-12 w-full" disabled={busy}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : mode === 'status' ? <RefreshCw className="size-4" /> : <ShieldCheck className="size-4" />}{busy ? '正在提交，请勿重复操作…' : mode === 'status' ? '查询本店审核进度' : resubmit ? '重新提交审核' : '提交入驻申请'}</Button>
    </form>

    {result && <section className="mt-5 rounded-[12px] border bg-card p-5" aria-live="polite"><div className="flex items-start justify-between gap-3"><div><Badge variant="outline" className={result === 'APPROVED' ? 'border-success/30 text-success' : result === 'REJECTED' ? 'border-warning/30 text-warning' : 'text-primary'}>{result === 'APPROVED' ? '已通过' : result === 'REJECTED' ? '需补充资料' : '待审核'}</Badge><h2 className="mt-3 text-xl font-semibold">{form.displayName} · {store.shortName}入驻申请</h2></div><Badge variant="secondary">#{store.slug.toUpperCase()}-20261105</Badge></div><p className="mt-3 text-sm leading-6 text-muted-foreground">{result === 'APPROVED' ? '账号已开通，请返回并使用微信快捷登录。' : result === 'REJECTED' ? '押金凭证无法辨认，请重新上传清晰照片；其他已填写资料会保留。' : '资料已提交，请等待本店管理人员审核。审核结果不会同步为其他门店的入驻资格。'}</p><dl className="mt-4 grid grid-cols-2 gap-3 rounded-[8px] bg-muted/40 p-4 text-xs"><div><dt className="text-muted-foreground">押金方式</dt><dd className="mt-1 font-medium">{depositMode === 'CASH' ? '现金缴纳 · 200 元' : '单抵双倍 · 400 元'}</dd></div><div><dt className="text-muted-foreground">{result === 'APPROVED' ? '开通等级' : '受理门店'}</dt><dd className="mt-1 font-medium">{result === 'APPROVED' ? `${store.shortName}陪玩 Lv.1` : store.shortName}</dd></div></dl>{result === 'APPROVED' && <Button className="mt-5 h-11 w-full" onClick={() => navigate('/provider-login')}>前往登录<ArrowRight className="size-4" /></Button>}{result === 'REJECTED' && <Button className="mt-5 h-11 w-full" onClick={() => { setMode('apply'); setResult(null); setResubmit(true); setNotice('请按审核意见补充材料后重新提交。') }}>补充资料并重新提交</Button>}</section>}
  </main></ProviderPublicFrame>
}
