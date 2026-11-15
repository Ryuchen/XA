import { useState } from 'react'
import { LoaderCircle, MessageCircle, QrCode, RefreshCw, Smartphone } from 'lucide-react'
import { useWeChatAuthChannel } from '@/components/auth/useWeChatAuthChannel'
import { Button } from '@/components/ui/button'

interface WeChatAuthorizationProps {
  actionLabel?: string
  busy?: boolean
  busyLabel?: string
  compact?: boolean
  retry?: boolean
  refreshable?: boolean
  onAuthorize: () => void
}

export function WeChatAuthorization({
  actionLabel = '登录',
  busy = false,
  busyLabel = '正在核验微信身份',
  compact = false,
  retry = false,
  refreshable = false,
  onAuthorize,
}: WeChatAuthorizationProps) {
  const channel = useWeChatAuthChannel()
  const [qrVersion, setQrVersion] = useState(0)

  if (channel === 'open-in-wechat') {
    return <div className="mt-6" data-wechat-channel={channel}>
      <div className="flex items-center gap-3 rounded-[8px] bg-muted/45 p-4">
        <Smartphone className="size-6 shrink-0 text-primary" />
        <div><p className="text-sm font-semibold">请在微信中打开</p><p className="mt-1 text-xs leading-5 text-muted-foreground">当前是手机普通浏览器。请使用微信打开本页，再通过官方授权完成{actionLabel}。</p></div>
      </div>
      <Button type="button" className="mt-4 h-12 w-full rounded-[8px]" disabled>当前环境无法调用微信授权</Button>
      <p className="mt-3 text-center text-xs leading-5 text-muted-foreground">不会将普通网页表单冒充微信官方授权。</p>
    </div>
  }

  if (channel === 'wechat') {
    return <div className="mt-6" data-wechat-channel={channel}>
      <div className="flex items-center gap-3 rounded-[8px] bg-muted/45 p-4">
        <MessageCircle className="size-6 shrink-0 text-primary" />
        <div><p className="text-sm font-semibold">微信官方快捷{actionLabel}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">在微信内唤起官方授权页，确认后返回当前页面，无需扫码。</p></div>
      </div>
      <Button type="button" className="mt-4 h-12 w-full rounded-[8px]" disabled={busy} onClick={onAuthorize}>
        {busy ? <LoaderCircle className="size-4 animate-spin" /> : <MessageCircle className="size-4" />}
        {busy ? busyLabel : retry ? `重新使用微信${actionLabel}` : `微信快捷${actionLabel}`}
      </Button>
      <p className="mt-3 text-center text-xs leading-5 text-muted-foreground">原型演示官方授权步骤，不唤起真实微信。</p>
    </div>
  }

  return <div className="mt-6 flex flex-col items-center" data-wechat-channel={channel}>
    <div className="rounded-[12px] border bg-white p-4"><DemoQrCode size={compact ? 120 : 184} version={qrVersion} /></div>
    <p className="mt-4 text-sm font-medium">打开微信扫一扫</p>
    <p className="mt-1 text-center text-xs leading-5 text-muted-foreground">在微信官方授权页确认{actionLabel}，二维码为原型示意图</p>
    <Button type="button" className="mt-5 h-11 w-full rounded-[8px]" disabled={busy} onClick={onAuthorize}>
      {busy ? <LoaderCircle className="size-4 animate-spin" /> : <QrCode className="size-4" />}
      {busy ? busyLabel : retry ? '重新模拟微信扫码' : '模拟微信扫码并继续'}
    </Button>
    {refreshable && <Button type="button" variant="ghost" className="mt-1 h-11 w-full rounded-[8px] text-xs" disabled={busy} onClick={() => setQrVersion(version => version + 1)}><RefreshCw className="size-4" />刷新二维码</Button>}
  </div>
}

function DemoQrCode({ size, version }: { size: number; version: number }) {
  const cells = Array.from({ length: 21 * 21 }, (_, index) => {
    const x = index % 21
    const y = Math.floor(index / 21)
    const finder = (ox: number, oy: number) => x >= ox && x < ox + 7 && y >= oy && y < oy + 7 && (x === ox || x === ox + 6 || y === oy || y === oy + 6 || (x >= ox + 2 && x <= ox + 4 && y >= oy + 2 && y <= oy + 4))
    return finder(0, 0) || finder(14, 0) || finder(0, 14) || (!((x < 8 && y < 8) || (x > 12 && y < 8) || (x < 8 && y > 12)) && ((x * 7 + y * 11 + x * y + version * 13 + x * version * 3 + y * version * 5) % 5 < 2))
  })
  return <svg width={size} height={size} viewBox="0 0 21 21" role="img" aria-label="不可扫描的微信官方授权示例二维码" className="block"><rect width="21" height="21" fill="#fff" />{cells.map((filled, index) => filled && <rect key={index} x={index % 21} y={Math.floor(index / 21)} width="1" height="1" fill="#0F172A" />)}</svg>
}
