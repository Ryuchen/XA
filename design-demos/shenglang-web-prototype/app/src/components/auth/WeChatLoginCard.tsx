import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, ArrowRight, Check, CheckCircle2, Headphones, LoaderCircle, LockKeyhole, Phone, ShieldCheck, Smartphone } from 'lucide-react'
import { useNavigate } from 'react-router'
import { OtpBinding } from '@/components/auth/OtpBinding'
import { useWeChatAuthChannel } from '@/components/auth/useWeChatAuthChannel'
import { WeChatAuthorization } from '@/components/auth/WeChatAuthorization'
import { ExampleQr } from '@/components/prototype/ExampleQr'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

export type WeChatLoginScenario = 'default' | 'verifying' | 'failure' | 'binding' | 'unavailable' | 'loading' | 'empty' | 'error'
type LoginStep = 'scan' | 'profile' | 'phone' | 'otp' | 'done'

interface WeChatLoginCardProps {
  audience: 'boss' | 'provider'
  scenario: WeChatLoginScenario
  storeName: string
  profileName: string
  onRetry?: () => void
  onSuccess?: () => void
  successLabel?: string
  secondaryAction?: ReactNode
}

export function WeChatLoginCard({ audience, scenario, storeName, profileName, onRetry, onSuccess, successLabel, secondaryAction }: WeChatLoginCardProps) {
  const navigate = useNavigate()
  const isProvider = audience === 'provider'
  const effectiveScenario = scenario === 'loading' ? 'verifying' : scenario === 'error' ? 'failure' : scenario === 'empty' ? 'unbound' : scenario
  const bindingMode = effectiveScenario === 'binding' || effectiveScenario === 'unavailable'
  const [step, setStep] = useState<LoginStep>(bindingMode ? 'phone' : 'scan')
  const [otpEnabled, setOtpEnabled] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [agreementError, setAgreementError] = useState(false)
  const [localBusy, setLocalBusy] = useState(false)
  const [codeSent, setCodeSent] = useState(false)
  const [smsCode, setSmsCode] = useState('')
  const [smsError, setSmsError] = useState('')
  const [notice, setNotice] = useState('')
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const busy = localBusy || effectiveScenario === 'verifying'
  const wechatChannel = useWeChatAuthChannel()
  const idPrefix = `${audience}-login`
  const homePath = isProvider ? '/provider' : '/home'
  const securityPath = isProvider ? '/provider/security' : '/security'
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  function run(action: () => void) {
    if (!agreed) { setAgreementError(true); document.getElementById(`${idPrefix}-privacy-agreement`)?.focus(); return }
    if (busy) return
    setAgreementError(false); setNotice(''); setSmsError(''); setLocalBusy(true)
    timerRef.current = setTimeout(() => { setLocalBusy(false); action() }, 650)
  }
  function primaryAction() {
    if (step === 'scan') run(() => { setStep('profile'); setNotice('微信身份已确认，请选择要用于声浪账号的头像与昵称。') })
    else if (step === 'profile') run(() => { setStep('phone'); setNotice('微信头像与昵称已同步，继续验证手机号以找回原账号。') })
    else if (step === 'phone' && !codeSent) run(() => { setCodeSent(true); setNotice('示例验证码已发送，请输入 123456 完成验证。') })
    else if (step === 'phone') {
      if (smsCode !== '123456') { setSmsError('请输入正确的 6 位示例验证码 123456'); return }
      run(() => { setStep('otp'); setNotice('手机号已验证，可选择开启二次验证，也可以稍后再设置。') })
    }
  }
  function resetCode() { setCodeSent(false); setSmsCode(''); setSmsError(''); setNotice('') }

  return <section aria-label="微信登录、手机号与可选二次验证">
    <Card className="rounded-[12px] py-0 shadow-lg"><CardContent className="p-5 md:p-6">
      <div className="flex items-start justify-between gap-3"><div><h2 className="text-xl font-semibold">{{ scan: wechatChannel === 'qr' ? '微信扫码登录' : '微信快捷登录', profile: '确认微信资料', phone: '验证微信手机号', otp: '二次验证（可选）', done: '登录已完成' }[step]}</h2></div><Badge variant="outline" className="shrink-0 rounded-md text-xs">示例流程</Badge></div>

      {step === 'scan' && <WeChatAuthorization busy={busy} retry={effectiveScenario === 'failure'} onAuthorize={primaryAction} refreshable />}

      {step === 'profile' && <div className="mt-6"><div className="flex items-center gap-4 rounded-[8px] border bg-muted/30 p-4"><Avatar className="size-16 border"><AvatarFallback className="bg-primary/10 text-2xl font-semibold text-primary">{Array.from(profileName)[0] ?? '声'}</AvatarFallback></Avatar><div className="min-w-0 flex-1"><p className="truncate text-lg font-semibold">{profileName}</p><p className="mt-1 text-xs text-muted-foreground">微信头像与昵称 · 示例</p></div><CheckCircle2 className="size-5 shrink-0 text-success" /></div><p className="mt-3 text-xs leading-5 text-muted-foreground">仅同步公开头像与昵称，不读取微信聊天记录、通讯录或其他资料。</p><Button type="button" className="mt-5 h-12 w-full rounded-[8px]" disabled={busy} onClick={primaryAction}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}{busy ? '正在同步微信资料' : '使用此头像与昵称并继续'}</Button></div>}

      {(step === 'phone' || step === 'otp' || step === 'done') && <div className="mt-6">
        <div className="flex items-center gap-3 rounded-[8px] bg-muted/45 p-4"><Smartphone className="size-5 shrink-0 text-primary" /><div className="min-w-0 flex-1"><p className="text-sm font-semibold">138 **** 8000</p><p className="mt-1 text-xs leading-5 text-muted-foreground">微信授权手机号 · 仅脱敏展示</p></div>{step !== 'phone' && <CheckCircle2 className="size-5 text-success" />}</div>
        {step === 'phone' && <>{codeSent ? <div className="mt-4"><div className="flex items-center justify-between gap-3"><Label htmlFor={`${idPrefix}-sms-code`} className="text-sm">短信验证码</Label><button type="button" className="min-h-11 text-xs font-medium text-primary" onClick={resetCode}>重新获取</button></div><Input id={`${idPrefix}-sms-code`} value={smsCode} onChange={event => { setSmsCode(event.target.value.replace(/\D/g, '').slice(0, 6)); setSmsError('') }} inputMode="numeric" autoComplete="one-time-code" placeholder="输入 6 位验证码" className="mt-2 h-12 rounded-[8px] text-base tabular-nums" disabled={busy} /><p className="mt-2 text-xs text-muted-foreground">示例验证码 123456，不会发送真实短信</p>{smsError && <p role="alert" className="mt-3 rounded-[8px] bg-destructive/10 p-3 text-xs text-destructive">{smsError}</p>}</div> : <p className="mt-4 text-sm leading-6 text-muted-foreground">{isProvider ? `点击后先完成微信手机号授权，再获取短信验证码。验证后只识别${storeName}的陪玩账号、订单与收益。` : '点击后先完成微信手机号授权，再获取短信验证码。若已有原账号，将优先找回原有余额与订单。'}</p>}<Button type="button" className="mt-4 h-12 w-full rounded-[8px]" disabled={busy || effectiveScenario === 'unavailable'} onClick={primaryAction}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Phone className="size-4" />}{busy ? '正在核验手机号' : codeSent ? '验证手机号并继续' : '微信授权并获取验证码'}</Button></>}
        {step === 'otp' && <OtpBinding disabled={busy} onComplete={() => run(() => { setOtpEnabled(true); setStep('done') })} onSkip={() => run(() => { setOtpEnabled(false); setStep('done') })} />}
        {step === 'done' && <div className="mt-5"><div className="rounded-[8px] border border-success/25 bg-success/5 p-4 text-sm leading-6 text-success">{otpEnabled ? '微信登录、头像昵称同步、手机号验证与 OTP 绑定均已完成，二次验证已开启。' : '微信登录、头像昵称同步与手机号验证已完成。二次验证暂未开启，可稍后在「我的 → 账号安全」中继续绑定。'}</div><Button type="button" className="mt-4 h-12 w-full rounded-[8px]" onClick={() => onSuccess ? onSuccess() : navigate(homePath)}>{successLabel || (isProvider ? '进入接单工作台' : '进入声浪电竞')}<ArrowRight className="size-4" /></Button>{!otpEnabled && <Button type="button" variant="outline" className="mt-3 h-11 w-full rounded-[8px]" onClick={() => navigate(securityPath)}><ShieldCheck className="size-4" />前往账号安全绑定</Button>}</div>}
      </div>}

      {effectiveScenario === 'unbound' && step === 'scan' && <div role="alert" className="mt-4 flex gap-2 rounded-[8px] border border-warning/30 bg-warning/5 p-3 text-xs leading-5 text-warning"><AlertCircle className="mt-0.5 size-4 shrink-0" />当前微信尚未绑定{storeName}陪玩账号，请先提交入驻申请或联系本店管理人员。</div>}
      {effectiveScenario === 'unavailable' && step === 'phone' && <div role="alert" className="mt-4 flex gap-2 rounded-[8px] border border-warning/30 bg-warning/5 p-3 text-xs leading-5 text-warning"><AlertCircle className="mt-0.5 size-4 shrink-0" />当前环境无法完成微信手机号授权，请联系客服协助找回账号。</div>}
      {effectiveScenario === 'failure' && step === 'scan' && <div role="alert" className="mt-4 flex items-start gap-2 rounded-[8px] border border-destructive/25 bg-destructive/5 p-3 text-xs leading-5 text-destructive"><AlertCircle className="mt-0.5 size-4 shrink-0" /><span className="flex-1">暂时无法完成登录，请检查网络后重试，或联系客服。</span>{onRetry && <button type="button" className="min-h-5 shrink-0 font-medium underline underline-offset-4" onClick={onRetry}>重新授权</button>}</div>}
      {notice && <p role="status" className={cn('mt-4 rounded-[8px] bg-primary/5 p-3 text-xs leading-5 text-primary')}>{notice}</p>}

      {step !== 'done' && <div className="mt-5 border-t pt-4"><div className="flex min-h-11 items-center gap-3 py-1 text-xs leading-5 text-muted-foreground"><Checkbox id={`${idPrefix}-privacy-agreement`} checked={agreed} onCheckedChange={checked => { setAgreed(checked === true); if (checked) setAgreementError(false) }} disabled={busy} aria-invalid={agreementError} aria-describedby={agreementError ? `${idPrefix}-agreement-error` : undefined} /><span><label htmlFor={`${idPrefix}-privacy-agreement`} className="cursor-pointer">我已阅读并同意</label><PrivacyDialog audience={audience} /><label htmlFor={`${idPrefix}-privacy-agreement`} className="cursor-pointer">，授权用于账号识别、资料同步与绑定。</label></span></div>{agreementError && <p id={`${idPrefix}-agreement-error`} role="alert" className="mt-1 text-xs text-destructive">请先阅读并同意隐私保护指引</p>}</div>}

      {step !== 'done' && secondaryAction}
      <SupportDialog /><p className="mt-3 flex items-start justify-center gap-2 text-center text-xs leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-3.5 shrink-0" />授权仅用于账号识别与绑定，不读取微信聊天记录</p>
    </CardContent></Card>
  </section>
}

function PrivacyDialog({ audience }: { audience: 'boss' | 'provider' }) {
  const isProvider = audience === 'provider'
  return <Dialog><DialogTrigger asChild><Button type="button" variant="link" className="inline h-auto p-0 align-baseline text-xs font-normal leading-5 text-primary underline-offset-4">《隐私保护指引》</Button></DialogTrigger><DialogContent className="prototype-dialog rounded-[12px] p-6 sm:max-w-[640px]" showCloseButton={false}><DialogHeader className="gap-3 text-left"><div className="flex items-center gap-3"><div className="grid size-9 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary sm:size-11"><ShieldCheck className="size-5" /></div><DialogTitle className="text-left text-base sm:text-xl">隐私保护指引</DialogTitle></div><DialogDescription className="text-left text-xs leading-5 sm:text-sm sm:leading-6">原型摘要 · 实际产品以正式发布版本为准</DialogDescription></DialogHeader><div className="space-y-4 text-sm leading-6 text-muted-foreground"><p>{isProvider ? '微信身份、公开头像昵称、手机号与 OTP 绑定仅用于识别、绑定当前门店的陪玩账号，以便加载本店订单、收益与服务记录。' : '微信身份、公开头像昵称、手机号与 OTP 绑定仅用于识别、创建或绑定当前门店的老板账号，以便找回原有余额、订单与服务记录。'}</p><p>授权不会读取微信聊天记录、通讯录或与账号服务无关的信息。本原型不请求真实授权、不保存手机号、不发送短信，也不连接真实验证器。</p></div><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full rounded-[8px]">我知道了</Button></DialogClose></DialogFooter></DialogContent></Dialog>
}

function SupportDialog() {
  return <Dialog><DialogTrigger asChild><Button type="button" variant="ghost" className="mt-2 h-11 w-full rounded-[8px] text-xs text-muted-foreground"><Headphones className="size-4" />账号有问题？联系客服</Button></DialogTrigger><DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><DialogTitle className="text-center text-xl">企业微信客服</DialogTitle><DialogDescription className="text-center text-sm leading-6">使用微信扫码，添加企业微信客服</DialogDescription></DialogHeader><div className="mx-auto mt-2 rounded-[12px] border bg-white p-4"><ExampleQr large /></div><p className="text-center text-xs leading-5 text-muted-foreground">原型示意二维码，不可扫码</p><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full rounded-[8px]">返回登录</Button></DialogClose></DialogFooter></DialogContent></Dialog>
}
