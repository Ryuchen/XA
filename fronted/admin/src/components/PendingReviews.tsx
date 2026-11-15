import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { useSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { MutationDialog } from "@/components/business"
import {
  clearReview,
  pendingReviews,
  reviewActive,
  type PendingReview,
} from "@/lib/reviews"
import { formatDateTime } from "@/lib/format"

export default function PendingReviews() {
  const { revision } = useSession("admin")
  const [items, setItems] = useState(pendingReviews)
  const [review, setReview] = useState<PendingReview | null>(null)
  const [checked, setChecked] = useState(false)
  const navigate = useNavigate()
  useEffect(() => {
    const update = () => setItems(pendingReviews())
    update()
    window.addEventListener("xa:admin-reviews", update)
    window.addEventListener("storage", update)
    return () => {
      window.removeEventListener("xa:admin-reviews", update)
      window.removeEventListener("storage", update)
    }
  }, [revision])
  if (!items.length) return null
  return (
    <section
      className="mb-4 space-y-3 rounded-xl border border-warning/35 bg-warning/5 p-4"
      aria-label="操作结果核对"
    >
      <h2 className="text-sm font-semibold">处理中的操作与待核对结果</h2>
      {items.map((item) => (
        <div
          key={item.key}
          className="flex flex-wrap items-center justify-between gap-3 text-sm"
        >
          <div>
            <p>
              {item.description} ·{" "}
              {reviewActive(item.key) ? "正在提交" : "结果待核对"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {formatDateTime(item.startedAt)}
              {item.requestId ? ` · 请求 ${item.requestId}` : ""}
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate(item.path)}
            >
              查看相关记录
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={reviewActive(item.key)}
              onClick={() => {
                setReview(item)
                setChecked(false)
              }}
            >
              核对后解除
            </Button>
          </div>
        </div>
      ))}
      {review && (
        <MutationDialog
          title="解除结果待核对限制"
          description={`${review.description} · ${formatDateTime(review.startedAt)}。原请求仍可能完成，刷新只展示当前快照；解除限制不会撤销原操作。`}
          onClose={() => setReview(null)}
          disabled={!checked || reviewActive(review.key)}
          submitText="已核对，解除限制"
          submitVariant="outline"
          onSubmit={async () => {
            clearReview(review.key)
          }}
        >
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-blue-600"
              checked={checked}
              onChange={(event) => setChecked(event.target.checked)}
            />
            已核对关联订单、账户流水或处理记录，确认本次结果，并将自行判断是否还需再次操作
          </label>
        </MutationDialog>
      )}
    </section>
  )
}
