import { useMemo, useState, type FormEvent } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  Moon,
  ShieldCheck,
  Smartphone,
  Sun,
} from 'lucide-react'
import { type AdminLoginScenario } from '@/components/admin/admin-config'
import { StoreSwitcher } from '@/components/store/StoreSwitcher'
import { useStore } from '@/components/store/store-context'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

interface AdminLoginPageProps {
  scenario: AdminLoginScenario
  dark: boolean
  onScenarioChange: (scenario: AdminLoginScenario) => void
  onThemeChange: (dark: boolean) => void
  onSuccess: () => void
  onBoss: () => void
}

type Stage = 'account' | 'otp' | 'binding' | 'recovery'

export function AdminLoginPage({ scenario, dark, onScenarioChange, onThemeChange, onSuccess, onBoss }: AdminLoginPageProps) {
  const { store } = useStore()
  const initialStage: Stage = scenario === 'otp' ? 'otp' : scenario === 'binding' ? 'binding' : scenario === 'recovery' ? 'recovery' : 'account'
  const [stage, setStage] = useState<Stage>(initialStage)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('prototype')
  const [showPassword, setShowPassword] = useState(false)
  const [code, setCode] = useState('')
  const [recoveryCode, setRecoveryCode] = useState('')
  const [manualKey, setManualKey] = useState(false)
  const [copied, setCopied] = useState(false)
  const [recovered, setRecovered] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [localFailure, setLocalFailure] = useState('')
  const busy = scenario === 'verifying'
  const qrCells = useMemo(() => Array.from({ length: 225 }, (_, index) => ((index * 17 + index % 7 * 11 + Math.floor(index / 15) * 3) % 13) < 6), [])

  function submitAccount(event: FormEvent) {
    event.preventDefault()
    const nextErrors: Record<string, string> = {}
    if (!username.trim()) nextErrors.username = '请输入管理员账号'
    if (!password) nextErrors.password = '请输入登录密码'
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length) return
    setLocalFailure('')
    setStage('otp')
    onScenarioChange('otp')
  }

  function submitCode(event: FormEvent) {
    event.preventDefault()
    if (code.replace(/\s/g, '').length !== 6) {
      setErrors({ code: '请输入验证器中的 6 位动态码' })
      return
    }
    setErrors({})
    onSuccess()
  }

  function submitRecovery(event: FormEvent) {
    event.preventDefault()
    if (!recoveryCode.trim()) {
      setErrors({ recovery: '请输入一枚未使用的备用恢复码' })
      return
    }
    setErrors({})
    setRecovered(true)
  }

  const stageTitle = stage === 'account' ? '欢迎回来' : stage === 'otp' ? '验证动态码' : stage === 'binding' ? '绑定身份验证器' : '使用备用恢复码'
  const stageDescription = stage === 'account' ? `登录${store.name}管理后台` : stage === 'otp' ? `打开身份验证器，输入${store.shortName}账号的当前 6 位动态码` : stage === 'binding' ? '首次登录需要完成当前门店账号的双重验证绑定' : '每枚恢复码只能使用一次，且仅适用于当前门店账号'

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[minmax(420px,0.92fr)_minmax(560px,1.08fr)]" data-store-scope={store.slug} data-store-page="login">
      <section className="relative hidden min-h-screen overflow-hidden bg-[#071a3d] p-12 text-white lg:flex lg:flex-col" aria-label="声浪电竞管理后台介绍">
        <div className="absolute inset-0 opacity-60" style={{ background: 'radial-gradient(circle at 18% 12%, rgba(56,189,248,.25), transparent 35%), radial-gradient(circle at 90% 72%, rgba(37,99,235,.35), transparent 38%)' }} />
        <div className="absolute inset-0 opacity-[0.08]" style={{ backgroundImage: 'linear-gradient(rgba(255,255,255,.7) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.7) 1px, transparent 1px)', backgroundSize: '32px 32px' }} />
        <button type="button" onClick={onBoss} className="relative z-10 flex w-fit items-center gap-3 text-left">
          <span className="grid size-11 place-items-center rounded-2xl border border-white/15 bg-white/10 backdrop-blur"><img src="./brand.svg" alt="" className="size-8" /></span>
          <span><span className="block text-lg font-semibold tracking-tight">声浪电竞</span><span className="block text-xs text-blue-100/70">SHENGLANG ESPORTS</span></span>
        </button>
        <div className="relative z-10 my-auto max-w-[520px] py-12">
          <Badge className="border border-sky-300/20 bg-sky-300/10 px-3 py-1 text-xs font-medium text-sky-100 hover:bg-sky-300/10">澄海 · 任务优先</Badge>
          <h1 className="mt-6 text-[42px] font-semibold leading-[1.2] tracking-[-0.04em]">让每一次服务，<br /><span className="text-sky-300">都有清晰的回响。</span></h1>
          <p className="mt-5 max-w-md text-[15px] leading-7 text-blue-100/70">从订单履约到资金核对，将关键任务、异常状态与处理进度放在同一个工作台。</p>
          <div className="mt-10 grid max-w-md grid-cols-3 gap-3">
            {[['订单履约', '节点可追踪'], ['资金快照', '口径更清楚'], ['权限协作', '角色有边界']].map(([title, note]) => <div key={title} className="rounded-xl border border-white/10 bg-white/[0.06] p-4 backdrop-blur"><CheckCircle2 className="size-4 text-sky-300" /><p className="mt-3 text-sm font-medium">{title}</p><p className="mt-1 text-xs text-blue-100/60">{note}</p></div>)}
          </div>
        </div>
        <div className="relative z-10 flex items-center justify-between text-xs text-blue-100/50"><span>管理后台高保真原型</span><span>桌面端 · iPad Pro 横屏</span></div>
      </section>

      <main className="relative flex min-h-screen flex-col px-5 py-5 sm:px-8 lg:px-12">
        <header className="flex items-center justify-between gap-3">
          <button type="button" onClick={onBoss} className="flex items-center gap-2 lg:hidden"><img src="./brand.svg" alt="" className="size-9" /><span className="font-semibold">声浪电竞</span></button>
          <Badge variant="outline" className="border-warning/40 bg-warning/10 text-xs">原型 · 示例流程</Badge>
          <div className="ml-auto flex items-center gap-2">
            <Button variant="ghost" onClick={onBoss} className="hidden h-10 sm:flex"><Smartphone className="size-4" />老板端原型</Button>
            <Button variant="outline" size="icon" className="size-10 rounded-full" onClick={() => onThemeChange(!dark)} aria-label={dark ? '切换为浅色模式' : '切换为深色模式'}>{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</Button>
          </div>
        </header>

        <div className="mx-auto flex w-full max-w-[440px] flex-1 items-center py-10">
          <div className="w-full">
            <div className="mb-8">
              <span className="grid size-12 place-items-center rounded-2xl border bg-card text-primary shadow-sm">{stage === 'account' ? <LockKeyhole className="size-5" /> : stage === 'recovery' ? <KeyRound className="size-5" /> : <ShieldCheck className="size-5" />}</span>
              <h2 className="mt-5 text-[28px] font-semibold tracking-[-0.03em]">{stageTitle}</h2>
              <p className="mt-2 text-sm text-muted-foreground">{stageDescription}</p>
            </div>

            <section className="mb-6 rounded-xl border bg-card p-4" aria-label="当前登录门店">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0"><p className="text-xs text-muted-foreground">当前登录门店</p><p className="mt-1 truncate text-sm font-semibold">{store.name}</p></div>
                <StoreSwitcher product="admin" compact />
              </div>
              <p className="mt-3 border-t pt-3 text-xs leading-5 text-muted-foreground">账号、角色与双重验证按门店独立授权。切店后需使用目标店账号重新登录。</p>
            </section>

            {(scenario === 'failure' || localFailure) && stage === 'account' && <Alert variant="destructive" className="mb-5" role="alert"><AlertTitle>账号或密码不正确</AlertTitle><AlertDescription>请核对后重试。连续失败时请按后台安全策略处理，不要反复提交。</AlertDescription></Alert>}

            {stage === 'account' && <form onSubmit={submitAccount} noValidate className="space-y-5">
              <div className="space-y-2"><Label htmlFor="admin-username">管理员账号</Label><Input id="admin-username" autoComplete="username" value={username} disabled={busy} onChange={event => setUsername(event.target.value)} aria-invalid={!!errors.username} className="h-12" placeholder="请输入账号" />{errors.username && <p className="text-xs text-destructive">{errors.username}</p>}</div>
              <div className="space-y-2"><div className="flex items-center justify-between"><Label htmlFor="admin-password">登录密码</Label><span className="text-xs text-muted-foreground">区分大小写</span></div><div className="relative"><Input id="admin-password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} disabled={busy} onChange={event => setPassword(event.target.value)} aria-invalid={!!errors.password} className="h-12 pr-12" placeholder="请输入密码" /><Button type="button" variant="ghost" size="icon" disabled={busy} onClick={() => setShowPassword(value => !value)} className="absolute right-1 top-1 size-10" aria-label={showPassword ? '隐藏密码' : '显示密码'}>{showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</Button></div>{errors.password && <p className="text-xs text-destructive">{errors.password}</p>}</div>
              <Button type="submit" disabled={busy} className="h-12 w-full text-sm">{busy ? <><LoaderCircle className="size-4 animate-spin" />正在核验账号…</> : <>安全登录<ArrowRight className="size-4" /></>}</Button>
              <div className="flex items-start gap-2 rounded-lg bg-muted/60 p-3 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0" />登录后可能需要动态码。原型不会校验或发送真实账号凭证。</div>
            </form>}

            {(stage === 'otp' || stage === 'binding') && <form onSubmit={submitCode} className="space-y-5">
              {stage === 'binding' && <div className="rounded-2xl border bg-card p-5">
                <div className="flex flex-col items-center">
                  <div className="grid grid-cols-[repeat(15,6px)] gap-[1px] rounded-xl border-8 border-white bg-white shadow-sm" aria-label="示例验证器二维码">{qrCells.map((filled, index) => <span key={index} className={cn('size-1.5', filled ? 'bg-slate-950' : 'bg-white')} />)}</div>
                  <p className="mt-3 text-center text-xs leading-5 text-muted-foreground">使用身份验证器扫码<br />二维码仅为原型图形，不包含真实密钥</p>
                </div>
                <Button type="button" variant="link" className="mx-auto mt-2 flex h-9" onClick={() => setManualKey(value => !value)}>{manualKey ? '收起手动密钥' : '无法扫码？手动输入密钥'}</Button>
                {manualKey && <div className="mt-2 flex items-center gap-2 rounded-lg border bg-muted/50 p-2 pl-3"><code className="min-w-0 flex-1 break-all text-xs">SLD4-DEMO-7Q2X-9K6P</code><Button type="button" variant="ghost" size="icon" className="size-9 shrink-0" onClick={() => setCopied(true)} aria-label="复制示例绑定密钥">{copied ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}</Button></div>}
              </div>}
              <div className="space-y-2"><Label htmlFor="admin-otp">6 位动态码</Label><Input id="admin-otp" autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ''))} aria-invalid={!!errors.code} className="h-14 text-center font-mono text-2xl tracking-[0.5em]" placeholder="000000" />{errors.code && <p className="text-xs text-destructive">{errors.code}</p>}<p className="text-xs text-muted-foreground">原型中输入任意 6 位数字即可继续。</p></div>
              <Button type="submit" className="h-12 w-full">{stage === 'binding' ? '完成绑定并登录' : '验证并登录'}<ArrowRight className="size-4" /></Button>
              <div className="flex items-center justify-between gap-3"><Button type="button" variant="ghost" onClick={() => { setStage('account'); onScenarioChange('default') }}><ArrowLeft className="size-4" />返回账号密码</Button>{stage === 'otp' && <Button type="button" variant="link" onClick={() => { setStage('recovery'); onScenarioChange('recovery') }}>使用恢复码</Button>}</div>
            </form>}

            {stage === 'recovery' && !recovered && <form onSubmit={submitRecovery} className="space-y-5">
              <Alert><ShieldCheck className="size-4" /><AlertTitle>恢复码仅用于无法使用验证器时</AlertTitle><AlertDescription>验证成功后，本枚恢复码立即失效，其余恢复码仍可分别使用一次。</AlertDescription></Alert>
              <div className="space-y-2"><Label htmlFor="admin-recovery">备用恢复码</Label><Input id="admin-recovery" autoFocus value={recoveryCode} onChange={event => setRecoveryCode(event.target.value)} aria-invalid={!!errors.recovery} className="h-12 font-mono" placeholder="XXXX-XXXX-XXXX" />{errors.recovery && <p className="text-xs text-destructive">{errors.recovery}</p>}</div>
              <Button type="submit" className="h-12 w-full">验证恢复码<ArrowRight className="size-4" /></Button>
              <Button type="button" variant="ghost" className="w-full" onClick={() => { setStage('otp'); onScenarioChange('otp') }}><ArrowLeft className="size-4" />返回动态码验证</Button>
            </form>}

            {stage === 'recovery' && recovered && <div className="space-y-5">
              <Alert className="border-success/40 bg-success/10"><CheckCircle2 className="size-4 text-success" /><AlertTitle>恢复码验证成功</AlertTitle><AlertDescription>本枚示例恢复码已标记为使用。以下剩余恢复码仅在本次展示，关闭后不再出现。</AlertDescription></Alert>
              <div className="grid grid-cols-2 gap-2 rounded-xl border bg-card p-4 font-mono text-xs">{['K7DM-P2XA-91QW', 'T4ZN-C8VB-63LK', 'Q9ER-H5YU-27MP', 'W2CX-J6NS-84DF'].map(item => <code key={item} className="rounded-md bg-muted px-2 py-2 text-center">{item}</code>)}</div>
              <Button className="h-12 w-full" onClick={onSuccess}>已保存，进入工作台<ArrowRight className="size-4" /></Button>
            </div>}

          </div>
        </div>
        <footer className="text-center text-[11px] text-muted-foreground">声浪电竞 · 管理后台设计原型 · 不连接生产环境</footer>
      </main>
    </div>
  )
}
