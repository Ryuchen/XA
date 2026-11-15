import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Copy, Send, MessageSquareText } from "@/components/ui/icons"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DataTable,
  Feedback,
  MutationDialog,
  RefreshButton,
  Status,
  useLiveReload,
  useLoad,
  type Row,
} from "@/components/business"
import { request } from "@/api/request"

const labels: Row = {
  NOT_SENT: "未发送",
  PENDING: "已入队",
  SENDING: "发送中",
  SENT: "已发送",
  FAILED: "明确失败",
  UNKNOWN: "结果待核对",
  SKIPPED: "已跳过",
}
function cardValid(row: Row) {
  return (
    row.channel !== "KOOK" ||
    (row.card_details &&
      Array.isArray(row.card_details.mention_role_ids) &&
      row.card_details.mention_role_ids.every(
        (id: unknown) => typeof id === "string" && /^[0-9]{1,64}$/.test(id)
      ) &&
      Number.isSafeInteger(row.card_details.link_count) &&
      row.card_details.link_count >= 0)
  )
}
function selectable(row: Row) {
  return (
    row.configured &&
    ["NOT_SENT", "FAILED"].includes(row.status) &&
    cardValid(row) &&
    !!row.confirmation_key
  )
}
export default function OrderMessages({
  order,
  onClose,
}: {
  order: Row
  onClose: () => void
}) {
  const result = useLoad(
    async () =>
      (await request({ url: `/orders/${order.id}/dispatch-message/` })).data,
    [order.id]
  )
  const [selected, setSelected] = useState<string[]>([])
  const [channel, setChannel] = useState("COMMON")
  const [confirm, setConfirm] = useState<string | null>(null)
  const [unknown, setUnknown] = useState("")
  useLiveReload(result.reload)
  const content =
    channel === "KOOK"
      ? result.data?.channels?.find((row: Row) => row.channel === "KOOK")
          ?.content
      : result.data?.content
  const chosen: Row[] = (result.data?.channels || []).filter((row: Row) =>
    selected.includes(row.channel)
  )
  const duplicate = result.data?.channels?.some((row: Row) =>
    ["PENDING", "SENDING", "SENT", "UNKNOWN"].includes(row.status)
  )
  async function copy() {
    try {
      await navigator.clipboard.writeText(content || "")
      toast.success("派单文案已复制")
    } catch {
      toast.error("浏览器未允许复制，请在文案框中选择复制。")
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value) onClose()
      }}
    >
      <DialogContent className="admin-order-message-dialog max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            订单一键发单 {result.data?.order_no || order.order_no}
          </DialogTitle>
          <DialogDescription>
            核对实际渠道、目标、文案、身份组提醒和链接后提交发送。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="flex gap-2">
            <RefreshButton loading={result.loading} reload={result.reload} />
            <Button
              variant={channel === "COMMON" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setChannel("COMMON")}
            >
              通用文案
            </Button>
            <Button
              variant={channel === "KOOK" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setChannel("KOOK")}
            >
              KOOK 文案
            </Button>
          </div>
          <Feedback message={result.error || unknown} retry={result.reload} />
          {result.data && !result.data.can_send && (
            <Feedback
              message={
                result.data.reason || "当前订单不能发单，请读取订单状态核对。"
              }
            />
          )}
          {result.loading && !result.data ? (
            <div
              aria-busy="true"
              aria-label="正在读取发单文案"
              className="space-y-3"
            >
              <Skeleton className="h-40 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : (
            <pre className="min-h-40 rounded-lg border bg-muted/40 p-4 text-sm whitespace-pre-wrap">
              {content || "暂无可用文案"}
            </pre>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={!content || !result.data?.can_send || !!result.error}
            onClick={() => {
              if (duplicate) setConfirm("copy")
              else void copy()
            }}
          >
            <Copy size={16} />
            复制文案
          </Button>
          <DataTable
            title="发单渠道"
            minWidth={760}
            titleIcon={MessageSquareText}
            description="按真实渠道状态核对目标与内容"
            rows={result.data?.channels || []}
            loading={result.loading}
            rowKey="channel"
            selection={{ selectedKeys: selected, onChange: keys => setSelected(keys.map(String)) }}
            rowSelectable={selectable}
            selectionDisabled={!result.data?.can_send || !!result.error}
            columns={[
              { key: "label", label: "渠道" },
              { key: "target_id", label: "目标频道" },
              {
                key: "status",
                label: "状态",
                render: (row) => (
                  <Status
                    value={row.status}
                    label={labels[row.status] || row.status}
                    tone={
                      row.status === "SENT"
                        ? "success"
                        : ["UNKNOWN", "SENDING", "PENDING"].includes(row.status)
                          ? "warning"
                          : row.status === "FAILED"
                            ? "destructive"
                            : "muted"
                    }
                  />
                ),
              },
              {
                key: "card_details",
                label: "卡片内容",
                render: (row) =>
                  row.channel === "KOOK"
                    ? cardValid(row)
                      ? `${row.card_details.mention_role_ids.length ? `提醒身份组 ${row.card_details.mention_role_ids.join("、")}` : "不提醒身份组"} · ${row.card_details.link_count} 个链接`
                      : "卡片明细不可用"
                    : "按渠道文案",
              },
              { key: "last_error", label: "说明" },
              {
                key: "actions",
                label: "操作",
                render: (row) => {
                  const checked = selected.includes(row.channel)
                  return (
                    <Button
                      type="button"
                      size="sm"
                      variant={checked ? "secondary" : "outline"}
                      aria-pressed={checked}
                      disabled={!selectable(row) || !result.data?.can_send || !!result.error || result.loading}
                      onClick={() => setSelected(current => checked ? current.filter(channel => channel !== row.channel) : [...current, row.channel])}
                    >
                      {checked ? "取消选择" : "选择渠道"}
                    </Button>
                  )
                },
              },
            ]}
          />
          <Button
            size="sm"
            disabled={
              !chosen.length ||
              chosen.some((row) => !selectable(row)) ||
              !result.data?.can_send ||
              !!result.error ||
              result.loading
            }
            onClick={() => setConfirm("send")}
          >
            <Send size={16} />
            核对并发送
          </Button>
        </div>
        {confirm && (
          <MutationDialog
            title={confirm === "copy" ? "确认复制文案" : "确认提交发单"}
            description={
              confirm === "copy"
                ? "已有渠道正在处理、结果待核对或已发送；人工发送可能重复，请先核对群消息。"
                : `订单 ${result.data?.order_no} 将提交至 ${chosen.map((row) => `${row.label}${row.target_id ? `（${row.target_id}）` : ""}`).join("、")}。请核对页面展示的文案、身份组提醒与链接。`
            }
            onClose={() => setConfirm(null)}
            onSubmit={async () => {
              if (confirm === "copy") {
                await copy()
                return
              }
              if (!chosen.length || chosen.some((row) => !selectable(row)))
                throw new Error("渠道状态已变化，请重新读取。")
              const keys = Object.fromEntries(
                chosen.map((row) => [row.channel, row.confirmation_key])
              )
              try {
                const data = (
                  await request({
                    url: `/orders/${order.id}/dispatch-message/`,
                    method: "POST",
                    data: {
                      channels: chosen.map((row) => row.channel),
                      confirmation_keys: keys,
                    },
                  })
                ).data
                result.setData(data)
                setSelected([])
                setUnknown("")
                toast.info("已处理发单请求，请核对各渠道状态。")
              } catch (cause: any) {
                setSelected([])
                setUnknown(
                  cause.status >= 400 && cause.status < 500
                    ? "订单或渠道已变化，请刷新后重新确认。"
                    : "发送结果未确认，请核对渠道消息与状态；请求可能仍在处理。"
                )
                throw cause
              }
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
