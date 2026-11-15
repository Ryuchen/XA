import { privateMediaTicket } from "@xa/shared"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { adminClient } from "@/api/request"

export default function PrivateImage({
  urls,
  label = "查看凭证",
  previewSkeleton,
}: {
  urls: Array<string | null | undefined>
  label?: string
  previewSkeleton?: ReactNode
}) {
  const sources = urls.filter(Boolean) as string[]
  const [current, setCurrent] = useState<string[]>(sources)
  const [selected, setSelected] = useState<number | null>(null)
  const [failed, setFailed] = useState<Record<number, boolean>>({})
  const [loaded, setLoaded] = useState<Record<number, boolean>>({})
  const renewed = useRef(new Set<number>())
  const key = sources.join("|")
  useEffect(() => {
    setCurrent(sources)
    setFailed({})
    setLoaded({})
    renewed.current.clear()
    setSelected(null)
  }, [key])
  async function renew(index: number) {
    if (renewed.current.has(index)) {
      setFailed((old) => ({ ...old, [index]: true }))
      return
    }
    renewed.current.add(index)
    const ticket = privateMediaTicket(sources[index] || "")
    if (!ticket) {
      setFailed((old) => ({ ...old, [index]: true }))
      return
    }
    try {
      const response = await adminClient.request<{ url: string }>(
        "/media/renew/",
        { method: "POST", data: { ticket } }
      )
      setCurrent((old) =>
        old.map((url, i) => (i === index ? response.url : url))
      )
    } catch {
      setFailed((old) => ({ ...old, [index]: true }))
    }
  }
  if (!sources.length)
    return <span className="text-xs text-muted-foreground">未提供凭证</span>
  return (
    <>
      <div className="flex flex-wrap gap-2">
        {current.map((url, index) => (
          <button
            key={index}
            type="button"
            className="relative flex min-h-20 w-24 items-center justify-center overflow-hidden rounded-lg border bg-muted text-xs"
            onClick={() => setSelected(index)}
            aria-label={`${label} ${index + 1}`}
          >
            {failed[index] ? (
              "图片加载失败，重试"
            ) : (
              <>
              {!loaded[index] && <Skeleton className="absolute inset-0 h-20 w-full" aria-label="正在读取凭证图片" />}
              <img
                src={url}
                alt={`${label} ${index + 1}`}
                width={96}
                height={80}
                className="h-20 w-full object-cover"
                onLoad={() => setLoaded(old => ({ ...old, [index]: true }))}
                onError={() => void renew(index)}
              />
              </>
            )}
          </button>
        ))}
      </div>
      <Dialog
        open={selected !== null}
        onOpenChange={(value) => {
          if (!value) setSelected(null)
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>{label}</DialogTitle>
            <DialogDescription>
              {selected === null ? "" : `${selected + 1} / ${current.length}`}
            </DialogDescription>
          </DialogHeader>
          {selected !== null &&
            (failed[selected] ? (
              <div className="space-y-3 py-12 text-center">
                <p>图片暂时无法查看</p>
                <Button
                  variant="outline"
                  onClick={() => {
                    renewed.current.delete(selected)
                    setFailed((old) => ({ ...old, [selected]: false }))
                    void renew(selected)
                  }}
                >
                  重试图片
                </Button>
              </div>
            ) : (
              <>
              {previewSkeleton && !loaded[selected] && previewSkeleton}
              <img
                src={current[selected]}
                alt={label}
                width={960}
                height={640}
                className={previewSkeleton && !loaded[selected] ? "hidden" : "mx-auto max-h-[68vh] object-contain"}
                onLoad={() => setLoaded(old => ({ ...old, [selected]: true }))}
                onError={() => void renew(selected)}
              />
              </>
            ))}
          <DialogFooter style={{ justifyContent: "space-between" }}>
            <Button
              variant="outline"
              disabled={selected === null || selected === 0}
              onClick={() =>
                setSelected((index) => Math.max(0, (index || 0) - 1))
              }
            >
              上一张
            </Button>
            <Button
              variant="outline"
              disabled={selected === null || selected >= current.length - 1}
              onClick={() =>
                setSelected((index) =>
                  Math.min(current.length - 1, (index || 0) + 1)
                )
              }
            >
              下一张
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
