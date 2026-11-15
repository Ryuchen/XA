import { useState } from "react"
import { Link } from "react-router"
import { Mic2, History } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  PageHeader,
  DetailGrid,
  PendingFeature,
  LoadState,
  Status,
  Field,
  UploadField,
} from "@/components/provider-ui"
import { api, useLoad, date } from "@/lib/provider"

type Signup = {
  id: number
  link_title: string
  game: string
  status: string
  status_display: string
  audit_remark: string
  created_at: string
}

export function AuditionsPage() {
  const [preview, setPreview] = useState(false)
  const history = useLoad(() => api<Signup[]>("/audition/my-signups"))
  return (
    <div className="space-y-6">
      <PageHeader title="试音报名" />
      <section className="rounded-[12px] border bg-card p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <Badge variant="outline" className="rounded-[4px]">
              <Mic2 className="size-3" />
              游戏 / 服务待公布
            </Badge>
            <h2 className="mt-3 text-xl font-semibold">公开试音招募</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              招募名额待公布 · 截止时间待公布
            </p>
          </div>
          <PendingFeature
            label="公开试音招募"
            description="公开招募列表暂未开放。现可使用客服提供的有效邀请链接报名，已有报名记录仍可查看。"
          />
        </div>
        <div className="mt-5">
          <DetailGrid
            items={[
              { label: "段位要求", value: "待公布" },
              { label: "服务时段", value: "待公布" },
              { label: "试音时长", value: "待公布" },
              { label: "审核周期", value: "待公布" },
            ]}
          />
        </div>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          公开招募待开放，可联系客服获取试音邀请。
        </p>
        <Button className="mt-5 h-11 w-full rounded-[8px]" disabled>
          <Mic2 className="size-4" />
          公开报名待开放
        </Button>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="outline"
            className="h-11 rounded-[8px]"
            onClick={() => setPreview(true)}
          >
            查看报名表
          </Button>
          <Button variant="outline" className="h-11 rounded-[8px]" asChild>
            <Link to="/support-chat">联系客服获取邀请</Link>
          </Button>
        </div>
      </section>
      <section className="space-y-4" aria-label="我的试音记录">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <History className="size-4 text-primary" />
          我的报名记录
        </h2>
        <LoadState
          loading={history.loading}
          hasData={history.data != null}
          error={history.error}
          reload={() => history.reload().catch(() => {})}
          empty={!history.data?.length}
          emptyTitle="暂无试音记录"
          emptyDescription="使用有效邀请报名后，可在这里查看审核结果。"
        >
          <div className="grid items-start gap-4 md:grid-cols-2">
            {history.data?.map((row) => (
              <article
                key={row.id}
                className="rounded-[12px] border bg-card p-4 md:p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-base font-semibold">{row.link_title}</h3>
                  <Status value={row.status} label={row.status_display} />
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  {row.game} · {date(row.created_at)}
                </p>
                {row.audit_remark && (
                  <p className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5">
                    {row.audit_remark}
                  </p>
                )}
              </article>
            ))}
          </div>
        </LoadState>
      </section>
      <Dialog open={preview} onOpenChange={setPreview}>
        <DialogContent className="max-h-[calc(100dvh-32px)] overflow-y-auto rounded-[12px] p-5">
          <DialogHeader className="flex-row items-center gap-3">
            <div className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary">
              <Mic2 className="size-5" />
            </div>
            <div className="min-w-0 space-y-2">
              <DialogTitle>公开试音报名表</DialogTitle>
              <DialogDescription>
                公开报名与音频样本提交待开放。
              </DialogDescription>
            </div>
          </DialogHeader>
          <div className="space-y-4">
            <Field label="服务介绍">
              <Textarea disabled rows={3} placeholder="待开放后填写服务介绍…" />
            </Field>
            <Field label="试音样本">
              <UploadField
                disabled
                accept="audio/*"
                aria-label="试音音频样本待开放"
              />
              <span className="text-xs font-normal text-muted-foreground">
                时长要求待公布，音频上传待开放
              </span>
            </Field>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" className="h-10 rounded-[8px]">
                关闭
              </Button>
            </DialogClose>
            <Button disabled className="h-10 rounded-[8px]">
              提交报名待开放
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
