import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Monitor, Smartphone, Tablet } from 'lucide-react'
import { PreviewViewportContext, type PreviewViewport } from '@/components/prototype/PreviewViewportContext'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface ViewportPreviewProps {
  children: ReactNode
  dark: boolean
  pageLabel: string
  product?: 'boss' | 'provider' | 'admin'
  fitToContainer?: boolean
}

export function ViewportPreview({ children, dark, pageLabel, product = 'boss', fitToContainer = false }: ViewportPreviewProps) {
  const productLabel = product === 'provider' ? '陪玩工作台' : product === 'admin' ? '管理后台' : '老板端 H5'
  const [viewport, setViewport] = useState<PreviewViewport>(() => product !== 'admin' && window.matchMedia('(max-width: 767px)').matches ? 'mobile' : 'pc')
  const viewportLabel = viewport === 'mobile' ? '移动端' : viewport === 'tablet' ? 'iPad Pro' : 'PC 端'
  const viewportDescription = viewport === 'mobile' ? '移动端预览 · 最大 390 px 触屏视口' : viewport === 'tablet' ? 'iPad Pro 12.9 横屏预览 · 1366 × 1024 CSS 视口' : `PC 端预览 · 1440 px 桌面网页视口${fitToContainer ? ' · 等比完整显示' : ''}`
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [mount, setMount] = useState<HTMLDivElement | null>(null)
  const [fitScale, setFitScale] = useState(1)
  const [fitHeight, setFitHeight] = useState<number>()
  const frameWidth = viewport === 'tablet' ? 1366 : 1440
  const fitted = fitToContainer && viewport !== 'mobile'

  useEffect(() => {
    if (!fitted) {
      const frame = requestAnimationFrame(() => {
        setFitScale(1)
        setFitHeight(undefined)
      })
      return () => cancelAnimationFrame(frame)
    }
    const container = containerRef.current
    const iframe = iframeRef.current
    if (!container || !iframe) return
    const updateFit = () => {
      const scale = Math.min(1, container.clientWidth / frameWidth)
      setFitScale(scale)
      setFitHeight(iframe.offsetHeight * scale)
    }
    const frame = requestAnimationFrame(updateFit)
    const observer = new ResizeObserver(updateFit)
    observer.observe(container)
    observer.observe(iframe)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
    }
  }, [fitted, frameWidth])

  useEffect(() => {
    const iframe = iframeRef.current
    if (!iframe) return
    let observer: MutationObserver | undefined

    function preservePreviewFocus(event: FocusEvent) {
      if (document.activeElement === iframe) event.stopImmediatePropagation()
    }
    window.addEventListener('blur', preservePreviewFocus, true)

    function setup() {
      const frame = iframe as HTMLIFrameElement
      const previewDocument = frame.contentDocument
      if (!previewDocument) return
      const doc: Document = previewDocument
      const base = doc.createElement('base')
      base.href = document.baseURI
      doc.head.replaceChildren(base)
      doc.documentElement.lang = 'zh-CN'

      const layout = doc.createElement('style')
      layout.textContent = 'html,body,#root{min-height:100%;margin:0}.phone-frame{height:100dvh;min-height:0;border:0;border-radius:0;box-shadow:none}.phone-screen{border-radius:0}'

      function syncStyles() {
        doc.head.querySelectorAll('[data-preview-style]').forEach(node => node.remove())
        document.head.querySelectorAll('style, link[rel="stylesheet"]').forEach(node => {
          const clone = node.cloneNode(true) as Element
          clone.setAttribute('data-preview-style', '')
          doc.head.appendChild(clone)
        })
        doc.head.appendChild(layout)
      }
      syncStyles()
      observer = new MutationObserver(syncStyles)
      observer.observe(document.head, { childList: true, subtree: true, characterData: true })
      doc.body.replaceChildren()
      doc.body.style.margin = '0'
      const root = doc.createElement('div')
      root.id = 'root'
      doc.body.appendChild(root)
      setMount(root)
    }

    if (iframe.contentDocument?.readyState === 'complete') setup()
    else iframe.addEventListener('load', setup, { once: true })
    return () => {
      observer?.disconnect()
      iframe.removeEventListener('load', setup)
      window.removeEventListener('blur', preservePreviewFocus, true)
      setMount(null)
    }
  }, [])

  useEffect(() => {
    const doc = mount?.ownerDocument
    if (!doc) return
    doc.documentElement.classList.toggle('dark', dark)
  }, [dark, mount])

  return (
    <>
      <div className="mb-4 flex w-full flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">{productLabel} · {pageLabel}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{viewportDescription}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="grid grid-cols-2 rounded-[8px] bg-muted/50 p-1" role="group" aria-label={`切换${productLabel}预览设备`}>
            {product === 'admin' ? <button type="button" className={cn('flex h-11 items-center gap-2 rounded-[6px] px-3 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', viewport === 'tablet' && 'bg-card font-medium text-primary shadow-sm')} aria-pressed={viewport === 'tablet'} onClick={() => setViewport('tablet')}><Tablet className="size-4" />iPad Pro</button> : <button type="button" className={cn('flex h-11 items-center gap-2 rounded-[6px] px-3 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', viewport === 'mobile' && 'bg-card font-medium text-primary shadow-sm')} aria-pressed={viewport === 'mobile'} onClick={() => setViewport('mobile')}><Smartphone className="size-4" />移动端</button>}
            <button type="button" className={cn('flex h-11 items-center gap-2 rounded-[6px] px-3 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', viewport === 'pc' && 'bg-card font-medium text-primary shadow-sm')} aria-pressed={viewport === 'pc'} onClick={() => setViewport('pc')}><Monitor className="size-4" />PC 端</button>
          </div>
          <Badge variant="secondary" className="hidden shrink-0 text-[11px] sm:inline-flex">React + shadcn/ui</Badge>
        </div>
      </div>
      <div ref={containerRef} className={cn('w-full max-w-full', fitted ? 'overflow-hidden' : 'overflow-x-auto')} data-preview-viewport={viewport} data-preview-scale={fitScale}>
        <div style={fitted ? { width: frameWidth * fitScale, height: fitHeight, margin: '0 auto' } : undefined}>
          <iframe
            ref={iframeRef}
            title={`${productLabel}${viewportLabel}预览`}
            aria-label={`${productLabel}${viewportLabel}预览`}
            data-preview-product={product}
            data-logical-width={frameWidth}
            style={fitted ? { transform: `scale(${fitScale})`, transformOrigin: 'top left' } : undefined}
            className={cn(
              'block rounded-[16px] outline-1 -outline-offset-1 bg-background shadow-[0_16px_48px_hsl(var(--ocean-950)/0.12)]',
              viewport === 'mobile' && 'mx-auto h-[844px] w-full max-w-[390px]',
              viewport === 'pc' && 'h-[min(920px,calc(100dvh-180px))] min-h-[640px] w-[1440px] min-w-[1440px]',
              viewport === 'tablet' && 'h-[1024px] w-[1366px] min-w-[1366px]',
            )}
          />
        </div>
      </div>
      {mount && <PreviewViewportContext.Provider value={viewport}>{createPortal(children, mount)}</PreviewViewportContext.Provider>}
    </>
  )
}
