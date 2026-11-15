import { useId, useState } from 'react'
import { KeyRound, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface OtpBindingProps {
  disabled?: boolean
  onComplete: () => void
  onSkip?: () => void
  actionLabel?: string
  skipLabel?: string
}

export function OtpBinding({ disabled = false, onComplete, onSkip, actionLabel = '绑定 OTP 并开启二次验证', skipLabel = '暂不开启，直接进入' }: OtpBindingProps) {
  const id = useId()
  const [started, setStarted] = useState(!onSkip)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')

  function bind() {
    if (code !== '123456') {
      setError('请输入正确的 6 位 OTP 示例动态码 123456')
      return
    }
    setError('')
    onComplete()
  }

  if (!started) return <section className="mt-5" data-otp-binding data-otp-step="choice" aria-labelledby={`${id}-title`}>
    <div className="rounded-[8px] border border-primary/20 bg-primary/[0.045] p-4">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 size-5 shrink-0 text-primary" />
        <div>
          <h3 id={`${id}-title`} className="text-sm font-semibold">是否开启二次验证？</h3>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">开启后，登录时需额外输入验证器生成的 6 位动态验证码，进一步保护账号资产。</p>
        </div>
      </div>
    </div>
    <Button type="button" className="mt-4 h-12 w-full rounded-[8px]" disabled={disabled} onClick={() => setStarted(true)}><ShieldCheck className="size-4" />开启二次验证</Button>
    <Button type="button" variant="ghost" className="mt-2 h-11 w-full rounded-[8px]" disabled={disabled} onClick={onSkip}>{skipLabel}</Button>
    <p className="mt-2 text-center text-xs leading-5 text-muted-foreground">可稍后在「我的 → 账号安全」中继续绑定。</p>
  </section>

  return <section className="mt-5" data-otp-binding data-otp-step="binding" aria-labelledby={`${id}-title`}>
    <div className="rounded-[8px] border bg-muted/30 p-4">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 size-5 shrink-0 text-primary" />
        <div>
          <h3 id={`${id}-title`} className="text-sm font-semibold">绑定 OTP 动态验证</h3>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">使用验证器扫码，或手动添加密钥后输入 6 位动态验证码。</p>
        </div>
      </div>
      <div className="mt-4 flex flex-col items-center gap-4 min-[360px]:flex-row min-[360px]:items-start">
        <div className="shrink-0 rounded-[8px] border bg-white p-2"><DemoOtpQrCode /></div>
        <div className="min-w-0 flex-1 max-[359px]:text-center">
          <p className="text-xs text-muted-foreground">手动密钥</p>
          <code className="mt-2 block break-all rounded-[6px] bg-background px-2 py-2 text-xs leading-5">DEMO-ONLY-NOT-A-REAL-SECRET</code>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">二维码与密钥均为示意，不可连接真实验证器。</p>
        </div>
      </div>
    </div>
    <div className="mt-4 space-y-2">
      <Label htmlFor={`${id}-otp`}>OTP 动态验证码</Label>
      <Input id={`${id}-otp`} value={code} disabled={disabled} onChange={event => { setCode(event.target.value.replace(/\D/g, '').slice(0, 6)); setError('') }} inputMode="numeric" autoComplete="one-time-code" placeholder="示例动态码 123456" aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-otp-error` : `${id}-otp-hint`} className="h-12 rounded-[8px] text-base tabular-nums" />
      <p id={`${id}-otp-hint`} className="text-xs leading-5 text-muted-foreground">原型示例动态码为 123456，不会生成或校验真实 OTP。</p>
    </div>
    {error && <p id={`${id}-otp-error`} role="alert" className="mt-3 rounded-[8px] bg-destructive/10 p-3 text-xs leading-5 text-destructive">{error}</p>}
    <Button type="button" className="mt-4 h-12 w-full rounded-[8px]" disabled={disabled} onClick={bind}><ShieldCheck className="size-4" />{actionLabel}</Button>
  </section>
}

function DemoOtpQrCode() {
  const size = 21
  const cells = Array.from({ length: size * size }, (_, index) => {
    const x = index % size
    const y = Math.floor(index / size)
    const finder = (ox: number, oy: number) => x >= ox && x < ox + 7 && y >= oy && y < oy + 7 && (x === ox || x === ox + 6 || y === oy || y === oy + 6 || (x >= ox + 2 && x <= ox + 4 && y >= oy + 2 && y <= oy + 4))
    return finder(0, 0) || finder(14, 0) || finder(0, 14) || (!((x < 8 && y < 8) || (x > 12 && y < 8) || (x < 8 && y > 12)) && ((x * 5 + y * 13 + x * y) % 7 < 3))
  })
  return <svg width="104" height="104" viewBox="0 0 21 21" role="img" aria-label="不可扫描的 OTP 绑定示例二维码" className="block"><rect width="21" height="21" fill="#fff" />{cells.map((filled, index) => filled && <rect key={index} x={index % size} y={Math.floor(index / size)} width="1" height="1" fill="#0F172A" />)}</svg>
}
