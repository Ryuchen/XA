import { useEffect, useMemo, useState, type FormEvent } from "react"
import { createApiClient } from "./client"

type OtpSetupData = {
  isOtpBound?: boolean
  requiresOTPSetup?: boolean
  otpChallenge?: string
  otpQr?: string
  otpSecret?: string
  recoveryCodes?: string[]
}

/** Authenticated enrollment; recovery codes are displayed only in this session. */
export function OtpSetupPanel({ role }: { role: "boss" | "provider" | "admin" }) {
  const client = useMemo(() => createApiClient(role), [role])
  const path = role === "admin" ? "/admin/auth/otp-setup" : "/users/otp-setup/"
  const [bound, setBound] = useState<boolean | null>(null)
  const [draft, setDraft] = useState<OtpSetupData | null>(null)
  const [code, setCode] = useState("")
  const [recovery, setRecovery] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    let active = true
    client.get<OtpSetupData>(path).then(data => { if (active) setBound(Boolean(data.isOtpBound)) })
      .catch((cause: Error) => { if (active) setError(cause.message || "无法读取验证器状态") })
    return () => { active = false }
  }, [client, path])

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    if (busy) return
    setBusy(true); setError("")
    try {
      const data = await client.post<OtpSetupData>(path, draft ? { otpChallenge: draft.otpChallenge, otpCode: code.trim() } : {})
      if (data.isOtpBound) {
        setBound(true); setDraft(null); setCode(""); setRecovery(data.recoveryCodes || [])
      } else setDraft(data)
    } catch (cause) { setError(cause instanceof Error ? cause.message : "验证器绑定失败") }
    finally { setBusy(false) }
  }

  return <section className="space-y-3 rounded-lg border p-4" aria-label="验证器设置">
    <h2 className="font-semibold">手机验证器</h2>
    <p className="text-sm">{bound === null ? "正在读取绑定状态" : bound ? "已绑定，下次密码登录默认使用动态验证码。" : "绑定后密码登录默认使用动态验证码，也可切换短信。"}</p>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {bound === false && !draft && <button type="button" className="rounded-md border px-3 py-2 text-sm" disabled={busy} onClick={() => void submit()}>绑定验证器</button>}
    {draft && <form onSubmit={submit} className="space-y-3">
      <p className="text-sm">使用手机验证器扫描二维码，或手动输入密钥。</p>
      {draft.otpQr && <img className="size-40" src={draft.otpQr} alt="验证器绑定二维码" width={160} height={160} />}
      <p className="break-all text-sm">密钥：<code>{draft.otpSecret}</code></p>
      <label className="block text-sm">动态验证码<input className="mt-1 block w-full rounded-md border bg-background px-3 py-2" autoComplete="one-time-code" inputMode="numeric" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ""))} maxLength={6} /></label>
      <button className="rounded-md border px-3 py-2 text-sm" disabled={busy || code.length !== 6}>确认绑定</button>
    </form>}
    {recovery.length > 0 && <div role="status" className="space-y-2 text-sm"><p>请安全保存以下一次性恢复码，关闭此页面后不再显示：</p><ul>{recovery.map(item => <li key={item}><code>{item}</code></li>)}</ul><button type="button" className="rounded-md border px-3 py-2" onClick={() => setRecovery([])}>已保存</button></div>}
  </section>
}
