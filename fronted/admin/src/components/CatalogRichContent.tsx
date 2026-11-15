import DOMPurify from "dompurify"
import { getSession } from "@xa/shared"
import { useEffect, useMemo, useRef } from "react"
import { catalogMediaSource, readCatalogMedia } from "@/lib/catalog-media"

/** Sanitize rich HTML first; protected images never enter the DOM with a raw URL. */
export default function CatalogRichContent({ html, className = "" }: { html: string; className?: string }) {
  const container = useRef<HTMLDivElement>(null)
  const sanitized = useMemo(() => {
    const document = new DOMParser().parseFromString(DOMPurify.sanitize(html), "text/html")
    document.querySelectorAll("img").forEach(image => {
      const source = catalogMediaSource(image.getAttribute("src") || "")
      if (!source) return
      image.setAttribute("data-catalog-media", source)
      image.setAttribute("aria-busy", "true")
      image.removeAttribute("src")
    })
    return document.body.innerHTML
  }, [html])
  useEffect(() => {
    const root = container.current
    if (!root) return
    let active = true
    const revision = getSession("admin").revision
    const objectUrls: string[] = []
    const images = Array.from(root.querySelectorAll<HTMLImageElement>("img[data-catalog-media]"))
    for (const image of images) {
      void readCatalogMedia(image.dataset.catalogMedia || "").then(url => {
        if (!active || getSession("admin").revision !== revision) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrls.push(url)
        image.src = url
        image.removeAttribute("aria-busy")
      }).catch(() => {
        if (!active || getSession("admin").revision !== revision) return
        image.removeAttribute("aria-busy")
        image.removeAttribute("src")
        image.alt = `${image.alt || "图片"}（暂不可读取）`
        image.title = "图片暂不可读取，请刷新后重试"
      })
    }
    return () => {
      active = false
      objectUrls.forEach(url => URL.revokeObjectURL(url))
    }
  }, [sanitized])
  return <div ref={container} className={className} dangerouslySetInnerHTML={{ __html: sanitized }} />
}
