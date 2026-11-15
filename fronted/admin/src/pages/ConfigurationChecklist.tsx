import { useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"
import { useCurrentStore } from "@xa/shared"
import { request } from "@/api/request"
import { Button } from "@/components/ui/button"
import { Settings2 } from "@/components/ui/icons"
import { DataPageState, Feedback, MutationDialog, Status, useLoad, type Row } from "@/components/business"
import { SourceDirectoryCard, SourcePageHeader } from "@/pages/Catalog"
import { useAdminAuth } from "@/lib/auth"
import { nextSetupStep, canCheckSetupItem, setupStatusLabels, setupStatusSummary, type SetupItem } from "@/lib/setup-checklist"
import { checklistEditor, type ConfigEditor } from "@/lib/quick-config"

export default function ConfigurationChecklist({ catalog, openEditor }: {
  catalog: ReturnType<typeof useLoad<Row>>
  openEditor: (editor: ConfigEditor) => void
}) {
  const auth = useAdminAuth()
  const setup = useCurrentStore().status === "CONFIGURING"
  const navigate = useNavigate()
  const [confirmingSetup, setConfirmingSetup] = useState<Row | null>(null)
  const evidence: (SetupItem & { path?: string })[] = catalog.data?.setup_evidence || []
  const editorFor = (key: string) => checklistEditor(key, setup, auth.hasPerm)
  const setupAction = nextSetupStep(evidence, item => {
    if (editorFor(item.key)) return auth.hasPerm(item.key === "withdraw" ? "withdraw:config" : "dictionary:edit")
    return item.path === "deposit-rules" && auth.hasPerm("deposit_rule:view") && auth.hasPerm("deposit_rule:edit")
  }, canCheckSetupItem)
  return <DataPageState loading={catalog.loading} hasSnapshot={!!catalog.data} error={catalog.error} retry={catalog.reload} skeleton="dictionary">
    <div className="space-y-4">
      <SourcePageHeader title={setup ? "开店设置清单" : "核心配置核对"} description="逐项核对本店已接入的配置，查看保存记录并确认当前值。正式开业另行核验。">
        <Button asChild variant="outline"><Link to="/dictionaries">返回快捷配置</Link></Button>
      </SourcePageHeader>
        <SourceDirectoryCard
          title="核心配置核对记录"
          icon={Settings2}
          description={catalog.data ? setupStatusSummary(evidence) : "核对记录尚未读取"}
          actions={setup && setupAction && <Button size="sm" disabled={catalog.loading || !!catalog.error} onClick={() => {
            const { item, kind } = setupAction
            const editor = editorFor(item.key)
            if (kind === "confirm") setConfirmingSetup(item)
            else if (editor) openEditor(editor)
            else if (item.path === "deposit-rules") navigate("/deposit-rules")
          }}>
            {setupAction.kind === "confirm" ? "下一步：核对" : "下一步：配置"}{setupAction.item.name}
          </Button>}
          rows={evidence}
          snapshotAvailable={!!catalog.data}
          error={catalog.error || ""}
          retry={catalog.reload}
          loading={catalog.loading}
          columns={[
            { key: "name", label: "配置" },
            { key: "status", label: "状态", render: row => <Status value={row.status} label={setupStatusLabels[row.status]}
              tone={row.status === "INVALID" ? "destructive" : row.status === "NEEDS_CONFIRMATION" ? "warning" : row.status === "CONFIRMED" ? "success" : row.status === "SAVED" ? "primary" : "muted"} /> },
            { key: "message", label: "说明", className: "max-w-3xl whitespace-normal" },
            { key: "actions", label: "管理", render: row => <div className="flex justify-end gap-2">
              {row.status !== "RESTRICTED" && editorFor(row.key) ? <Button actionTone="edit" size="sm" variant="outline" onClick={() => openEditor(editorFor(row.key)!)}>打开配置</Button>
                : row.status !== "RESTRICTED" && row.path === "deposit-rules" && auth.hasPerm("deposit_rule:view") ? <Button actionTone="edit" size="sm" variant="outline" asChild><Link to={`/${row.path}`}>打开配置</Link></Button>
                : <span className="text-xs text-muted-foreground">{row.status === "RESTRICTED" ? "无查看权限" : ["reservation_deadlines", "room_confirmation"].includes(row.key) ? "编辑入口待对接" : "当前无可用编辑入口"}</span>}
              {canCheckSetupItem(row as SetupItem) && <Button actionTone="approve" size="sm" variant="outline" disabled={catalog.loading || !!catalog.error} onClick={() => setConfirmingSetup(row)}>核对并确认</Button>}
            </div> },
          ]}
        />
      {!!catalog.data?.readonly?.length && <details className="rounded-lg border p-4 text-sm">
        <summary className="cursor-pointer font-medium">哪些内容由业务流程管理</summary>
        {catalog.data.readonly.map((text: string) => <p key={text} className="mt-2 text-muted-foreground">{text}</p>)}
      </details>}
      {confirmingSetup && <SetupConfirmation row={confirmingSetup} onClose={() => setConfirmingSetup(null)} onSaved={catalog.reload} />}
    </div>
  </DataPageState>
}

function SetupConfirmation({ row, onClose, onSaved }: { row: Row; onClose: () => void; onSaved: () => Promise<void> }) {
  const preview = useLoad(async () => (await request<Row>({ url: `/dictionaries/setup-confirmation/${row.key}/` })).data)
  const [uncertain, setUncertain] = useState(false)
  return <MutationDialog title={`核对${row.name}`} description="请核对下列本店配置。确认只记录核对人和当前值，不修改实际规则；配置变化后须重新确认。"
    submitText="确认当前配置" disabled={preview.loading || !!preview.error || !preview.data || uncertain} onClose={onClose}
    onSubmit={async () => {
      try {
        await request({ url: `/dictionaries/setup-confirmation/${row.key}/`, method: "POST", data: { fingerprint: preview.data?.fingerprint } })
        toast.success("配置已确认")
        await onSaved()
      } catch (error) {
        setUncertain(true)
        throw error
      }
    }}>
    {preview.loading && <p role="status" className="text-sm">正在读取配置…</p>}
    <Feedback message={preview.error} retry={preview.reload} />
    {!!preview.data?.fields?.length && <dl className="space-y-3">{preview.data.fields.map((field: Row) => <div key={field.label} className="flex justify-between gap-4 text-sm"><dt>{field.label}</dt><dd className="font-medium tabular-nums">{field.value}</dd></div>)}</dl>}
    {uncertain && <p className="text-sm text-muted-foreground">请关闭窗口并刷新后重新核对。</p>}
  </MutationDialog>
}

