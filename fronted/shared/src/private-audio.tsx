import { useEffect, useRef, useState } from "react"
import { createPrivateMediaRecovery } from "./media"
import type { Role } from "./session"

/** Reauthorize an expired playback link without changing its owner or store. */
export function PrivateAudio({ role, src, label, className }: {
  role: Role; src: string; label: string; className?: string
}) {
  const recovery = useRef(createPrivateMediaRecovery(role, src))
  const player = useRef<HTMLAudioElement>(null)
  const [url, setUrl] = useState(src)
  const [failed, setFailed] = useState(false)
  const [renewed, setRenewed] = useState(false)
  useEffect(() => {
    recovery.current.dispose()
    recovery.current = createPrivateMediaRecovery(role, src)
    setUrl(src); setFailed(false); setRenewed(false)
    return () => recovery.current.dispose()
  }, [role, src])
  async function recover() {
    const result = await recovery.current.recover()
    if (result.stale) return
    setUrl(result.url)
    setFailed(result.failed)
    if (!result.failed) {
      setRenewed(true)
      if (result.url === url) player.current?.load()
    }
  }
  if (failed) return <div role="alert">录音暂时无法播放。<button type="button" onClick={() => {
    recovery.current.retry(); setFailed(false); setRenewed(false); setUrl(src)
  }}>重新加载</button></div>
  return <div>{renewed && <p>录音已重新授权，请再次播放。</p>}
    <audio ref={player} controls preload="none" className={className} src={url} aria-label={label} onError={() => void recover()}
      onCanPlay={() => {
        if (!renewed) return
        recovery.current.dispose()
        recovery.current = createPrivateMediaRecovery(role, url)
        setRenewed(false)
      }} />
  </div>
}
