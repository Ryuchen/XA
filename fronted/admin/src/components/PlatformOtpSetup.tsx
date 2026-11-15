import { Status } from "@/components/business"
import { useEffect, useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { platformRequest } from "@/lib/platform-session"

type Setup = { isOtpBound?: boolean; otpChallenge?: string; otpQr?: string; otpSecret?: string; recoveryCodes?: string[] }

export function PlatformOtpSetup() {
  const [reading, setReading] = useState(true)
  const [bound, setBound] = useState<boolean | null>(null)
  const [draft, setDraft] = useState<Setup | null>(null)
  const [code, setCode] = useState("")
  const [recovery, setRecovery] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true
    platformRequest<Setup>("auth/otp-setup").then(data => { if (active) setBound(data.isOtpBound ?? null) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "无法读取验证器状态") })
      .finally(() => { if (active) setReading(false) })
    return () => { active = false }
  }, [])
  async function submit(event?: FormEvent) {
    event?.preventDefault()
    if (busy) return
    setBusy(true); setError("")
    try {
      const data = await platformRequest<Setup>("auth/otp-setup", { method: "POST", data: draft ? { otpChallenge: draft.otpChallenge, otpCode: code.trim() } : {} })
      if (data.isOtpBound) { setBound(true); setDraft(null); setCode(""); setRecovery(data.recoveryCodes || []) }
      else setDraft(data)
    } catch (cause) { setError(cause instanceof Error ? cause.message : "验证器绑定失败") }
    finally { setBusy(false) }
  }
  async function cancel() {
    if (!draft?.otpChallenge || busy) return
    setBusy(true); setError("")
    try {
      await platformRequest<Setup>("auth/otp-setup", { method: "POST", data: { otpChallenge: draft.otpChallenge, cancel: true } })
      setDraft(null); setCode("")
    } catch (cause) { setError(cause instanceof Error ? cause.message : "取消绑定失败") }
    finally { setBusy(false) }
  }
  return <section className="mt-6 max-w-xl space-y-3 rounded-lg border p-4" aria-label="平台验证器设置">
    <h2 className="font-semibold">手机验证器</h2>
    <div className="flex items-center gap-2 text-sm"><Status value={bound} label={bound == null ? undefined : bound ? "已绑定" : "未绑定"} readState={bound === null ? reading ? "loading" : error ? "error" : "unread" : undefined} />
      {bound !== null && <span>{bound ? "下次密码登录默认使用动态验证码。" : "绑定后密码登录默认使用动态验证码，也可切换短信。"}</span>}</div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {bound === false && !draft && <Button type="button" variant="outline" disabled={busy} onClick={() => void submit()}>绑定验证器</Button>}
    {draft && <form onSubmit={submit} className="space-y-3"><p className="text-sm">使用手机验证器扫描二维码，或手动输入密钥。</p>{draft.otpQr && <img className="size-40" src={draft.otpQr} alt="验证器绑定二维码" width={160} height={160} />}<p className="break-all text-sm">密钥：<code>{draft.otpSecret}</code></p><label className="block text-sm">动态验证码<input name="code" className="mt-1 block w-full rounded-md border bg-background px-3 py-2" autoComplete="one-time-code" inputMode="numeric" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ""))} maxLength={6} /></label><Button type="submit" disabled={busy || code.length !== 6}>确认绑定</Button><Button type="button" variant="ghost" onClick={() => void cancel()} disabled={busy}>取消</Button></form>}
    {recovery.length > 0 && <div role="status" className="space-y-2 text-sm"><p>请安全保存以下一次性恢复码，关闭此页面后不再显示：</p><ul>{recovery.map(item => <li key={item}><code>{item}</code></li>)}</ul><Button type="button" variant="outline" onClick={() => setRecovery([])}>已保存</Button></div>}
  </section>
}
