import { useContext } from 'react'
import { PreviewViewportContext } from '@/components/prototype/PreviewViewportContext'

export type WeChatAuthChannel = 'qr' | 'wechat' | 'open-in-wechat'

function detectWeChatAuthChannel(): WeChatAuthChannel {
  if (typeof navigator === 'undefined') return 'qr'
  const userAgent = navigator.userAgent
  const mobileDevice = /Android|iPhone|iPad|iPod|Mobile|HarmonyOS/i.test(userAgent)
    || (/Macintosh/i.test(userAgent) && navigator.maxTouchPoints > 1)
  if (!mobileDevice) return 'qr'
  return /MicroMessenger/i.test(userAgent) ? 'wechat' : 'open-in-wechat'
}

export function useWeChatAuthChannel(): WeChatAuthChannel {
  const previewViewport = useContext(PreviewViewportContext)
  if (previewViewport) return previewViewport === 'mobile' ? 'wechat' : 'qr'
  return detectWeChatAuthChannel()
}
