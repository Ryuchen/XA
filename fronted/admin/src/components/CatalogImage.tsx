import { getSession } from "@xa/shared"
import { useEffect, useState } from "react"
import { adminClient } from "@/api/request"

/** Admin media needs the current tenant's Bearer credential, never a public rewrite. */
export default function CatalogImage({
  src,
  alt,
  className = "",
  width = 160,
  height = 96,
}: {
  src: string
  alt: string
  className?: string
  width?: number
  height?: number
}) {
  const protectedSource = (() => {
    try {
      const target = new URL(src, window.location.origin)
      return target.origin === window.location.origin && target.pathname.startsWith("/media/")
        ? `${target.pathname}${target.search}`
        : ""
    } catch {
      return ""
    }
  })()
  const [url, setUrl] = useState(protectedSource ? "" : src)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    let objectUrl = ""
    const revision = getSession("admin").revision
    setFailed(false)
    setUrl(protectedSource ? "" : src)
    if (!protectedSource) return () => { active = false }
    const valid = () => active && getSession("admin").revision === revision
    async function read() {
      for (let retry = 0; retry < 2; retry += 1) {
        const token = getSession("admin").token
        if (!token) throw new Error("请先登录后查看图片")
        const response = await fetch(protectedSource, {
          headers: { Authorization: `Bearer ${token}` },
          credentials: "same-origin",
          cache: "no-store",
        })
        if (response.status === 401 && retry === 0) {
          await adminClient.get("/admin/auth/profile")
          continue
        }
        if (!response.ok) throw new Error("图片暂不可读取")
        const blob = await response.blob()
        if (!blob.type.startsWith("image/")) throw new Error("图片格式不可用")
        if (!valid()) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
        return
      }
    }
    void read().catch(() => { if (valid()) setFailed(true) })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [src, protectedSource, attempt])
  if (failed) {
    return <button type="button" className="text-xs text-muted-foreground underline" onClick={() => { setFailed(false); setAttempt(value => value + 1) }}>重新加载图片</button>
  }
  if (!url) return <span className={`inline-block animate-pulse rounded-lg bg-muted ${className}`} aria-busy="true" aria-label={`${alt}加载中`} />
  return <img src={url} alt={alt} width={width} height={height} className={className} onError={() => setFailed(true)} />
}
