import { ArrowRight, MessageCircle } from "lucide-react"
import { isWechatBrowser, useH5Auth } from "@xa/shared"
import { Button } from "@/components/ui/button"

export function WechatBindingButton({ onBound }: { onBound: () => void }) {
  const wechat = useH5Auth("boss", onBound)
  if (!isWechatBrowser()) return <Button type="button" variant="outline" disabled>请在微信内打开本页绑定</Button>
  return <div className="grid gap-2">
    <Button type="button" disabled={wechat.busy} onClick={() => wechat.start({ purpose: "bind_wechat" })}><MessageCircle size={18} />{wechat.busy ? "正在打开微信授权…" : "绑定当前微信"}</Button>
    {wechat.flow && <Button asChild variant="ghost"><a href={wechat.flow.authorization_url} target="_blank" rel="noopener noreferrer">继续微信授权<ArrowRight size={16} /></a></Button>}
    {wechat.error && <div className="boss-feedback error" role="alert">{wechat.error}</div>}
  </div>
}
