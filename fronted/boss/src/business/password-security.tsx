import { useState, type FormEvent } from "react"
import { ArrowRight, LockKeyhole } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "./ui"

export function PasswordSecurityForm({ username: initialUsername, onSaved }: { username: string; onSaved: (username: string) => void }) {
  const [username, setUsername] = useState(initialUsername)
  const [password, setPassword] = useState("")
  const [confirmation, setConfirmation] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [saved, setSaved] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (password.length < 8 || password !== confirmation) {
      setError(password.length < 8 ? "密码至少 8 位" : "两次输入的密码不一致")
      return
    }
    setBusy(true)
    setError("")
    setSaved(false)
    try {
      const result = await api.post<{ username: string }>("/users/password/", { username: username.trim(), password })
      onSaved(result.username)
      setPassword("")
      setConfirmation("")
      setSaved(true)
    } catch (cause: any) {
      setError(cause.message || "密码保存失败，请重试")
    } finally {
      setBusy(false)
    }
  }

  return <form className="boss-form" onSubmit={submit}>
    <label className="boss-field"><span>登录账号</span><Input name="username" value={username} minLength={3} maxLength={32} autoComplete="username" disabled={busy} onChange={event => setUsername(event.target.value)} /></label>
    <label className="boss-field"><span>新密码</span><Input name="new_password" type="password" minLength={8} maxLength={128} autoComplete="new-password" value={password} disabled={busy} onChange={event => setPassword(event.target.value)} placeholder="至少 8 位字母与数字…" /></label>
    <label className="boss-field"><span>确认新密码</span><Input name="password_confirmation" type="password" minLength={8} maxLength={128} autoComplete="new-password" value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} /></label>
    {error && <div className="boss-feedback error" role="alert">{error}</div>}
    {saved && <p role="status" className="boss-muted">登录账号和密码已保存，下次可使用账号密码和短信验证码登录。</p>}
    <Button type="submit" disabled={busy}><LockKeyhole size={16} />{busy ? "正在保存…" : "保存登录账号和密码"}{!busy && <ArrowRight size={16} />}</Button>
  </form>
}
