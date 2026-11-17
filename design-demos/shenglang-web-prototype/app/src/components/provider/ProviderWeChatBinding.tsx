import { useId, useState } from 'react'
import { ArrowRight, CheckCircle2, LoaderCircle, Phone, ShieldCheck, Smartphone } from 'lucide-react'
import { OtpBinding } from '@/components/auth/OtpBinding'
import { WeChatAuthorization } from '@/components/auth/WeChatAuthorization'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface ProviderWeChatBindingProps {
  purpose: 'login' | 'register' | 'status'
  storeName: string
  disabled?: boolean
  onComplete: (phone: string, otpEnabled?: boolean) => void
}

export function ProviderWeChatBinding({ purpose, storeName, disabled = false, onComplete }: ProviderWeChatBindingProps) {
  const id = useId()
  const [step, setStep] = useState<'authorize' | 'phone' | 'sms' | 'otp'>('authorize')
  const [agreed, setAgreed] = useState(false)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const actionLabel = purpose === 'login' ? '登录' : purpose === 'register' ? '入驻' : '查询'

  function authorize() {
    if (!agreed) { setError('请先阅读并同意隐私保护指引'); return }
    setError('')
    setStep('phone')
  }

  function verify() {
    if (code !== '123456') { setError('请输入正确的 6 位示例验证码 123456'); return }
    setError('')
    if (purpose === 'login') setStep('otp')
    else onComplete('13800000000')
  }

  return <div className="mt-4 md:mt-2" data-wechat-purpose={purpose}>
    {step === 'authorize' && <>
      <WeChatAuthorization actionLabel={actionLabel} busy={disabled} compact onAuthorize={authorize} />
      <p className="mt-2 text-center text-xs leading-5 text-muted-foreground">授权仅用于{storeName}账号识别，不读取聊天或通讯录。</p>
      <label htmlFor={`${id}-agreement`} className="mt-3 flex min-h-11 cursor-pointer items-start gap-3 py-1 text-xs leading-5 text-muted-foreground"><Checkbox id={`${id}-agreement`} checked={agreed} disabled={disabled} onCheckedChange={checked => { setAgreed(checked === true); if (checked) setError('') }} className="mt-0.5" /><span>我已阅读并同意隐私保护指引，授权用于本店账号识别、微信绑定与手机号验证。</span></label>
      <PrivacyDialog purpose={purpose} />
    </>}

    {step !== 'authorize' && <div>
      <div className="flex items-center gap-3 rounded-[8px] border border-success/25 bg-success/5 p-3"><CheckCircle2 className="size-5 shrink-0 text-success" /><div><p className="text-sm font-medium text-success">{step === 'otp' ? '微信与手机号均已确认' : '微信身份已确认'}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{step === 'phone' ? '接下来绑定微信手机号，仅脱敏展示' : step === 'sms' ? '请输入短信验证码，确认手机号归属' : '二次验证为可选步骤，也可以稍后在账号安全中设置'}</p></div></div>
      {step === 'phone' ? <>
        <div className="mt-4 flex items-start gap-3 rounded-[8px] bg-muted/45 p-4"><Smartphone className="mt-0.5 size-5 shrink-0 text-primary" /><p className="text-xs leading-5 text-muted-foreground">正式环境通过微信公众号网页授权确认身份，手机号以短信验证码核对归属，网页不接受手填号码代替授权。</p></div>
        <Button type="button" className="mt-4 h-12 w-full" disabled={disabled} onClick={() => { setStep('sms'); setError('') }}><Phone className="size-4" />微信授权并获取验证码</Button>
      </> : step === 'sms' ? <>
        <div className="mt-4 rounded-[8px] bg-muted/45 p-3"><p className="text-sm font-semibold tabular-nums">138 **** 8000</p><p className="mt-1 text-xs text-muted-foreground">微信授权手机号 · 示例</p></div>
        <div className="mt-4 space-y-2"><Label htmlFor={`${id}-sms`}>短信验证码</Label><Input id={`${id}-sms`} value={code} disabled={disabled} onChange={event => { setCode(event.target.value.replace(/\D/g, '').slice(0, 6)); setError('') }} inputMode="numeric" autoComplete="one-time-code" placeholder="示例验证码 123456" className="h-12 text-base tabular-nums" /></div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">示例短信已发送，不会向真实号码发送短信。</p>
        <Button type="button" className="mt-4 h-12 w-full" disabled={disabled} onClick={verify}>{disabled ? <LoaderCircle className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}{purpose === 'login' ? '验证手机号并继续' : `完成验证并${actionLabel}`}<ArrowRight className="size-4" /></Button>
      </> : null}
    </div>}

    {step === 'otp' && <OtpBinding disabled={disabled} onComplete={() => onComplete('13800000000', true)} onSkip={() => onComplete('13800000000', false)} />}

    {error && <p role="alert" className="mt-3 rounded-[8px] bg-destructive/10 p-3 text-xs leading-5 text-destructive">{error}</p>}
    {purpose !== 'login' && <p className="mt-3 text-center text-xs leading-5 text-muted-foreground">原型仅演示授权步骤，不唤起真实微信、读取手机号或发送短信。</p>}
  </div>
}

function PrivacyDialog({ purpose }: { purpose: 'login' | 'register' | 'status' }) {
  return <Dialog><DialogTrigger asChild><Button type="button" variant="link" className="h-9 w-full text-xs text-primary">查看《隐私保护指引》</Button></DialogTrigger><DialogContent className="prototype-dialog rounded-[12px] p-6"><DialogHeader><DialogTitle className="text-left">隐私保护指引</DialogTitle><DialogDescription className="text-left leading-6">原型摘要 · 实际产品以正式发布版本为准</DialogDescription></DialogHeader><div className="space-y-3 text-sm leading-6 text-muted-foreground"><p>微信身份、授权手机号{purpose === 'login' ? '与 OTP 绑定' : ''}仅用于当前门店陪玩账号的{purpose === 'login' ? '登录识别、账号绑定与安全找回' : purpose === 'register' ? '入驻申请、审核联系与账号绑定' : '审核进度查询与账号识别'}，不自动成为其他门店的账号或入驻资格。</p><p>正式环境由微信公众号网页授权确认身份，并通过短信验证码核对手机号归属；网页不会将手填号码冒充微信授权，也不会读取聊天记录或通讯录。本原型不连接真实验证器。</p></div><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full">我知道了</Button></DialogClose></DialogFooter></DialogContent></Dialog>
}
