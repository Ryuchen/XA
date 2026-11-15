import {
  SourceDirectoryCard,
  SourcePageHeader,
  useSourceReviewState,
} from "@/pages/Catalog"
import { Switch } from "@/components/ui/switch"
import { Radio, ListOrdered, History } from "@/components/ui/icons"
import { useEffect, useState } from "react"
import { randomUuid } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  DataPageState,
  Feedback,
  Field,
  MutationDialog,
  RefreshButton,
  Status,
  useLoad,
  type Row,
} from "@/components/business"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { DataRegion, DataValue } from "@/components/page-data-state"
import { formatDateTime } from "@/lib/format"

const api = "/config/kook-dispatch/"
const statuses: Row = {
  SENT: "发送成功",
  FAILED: "明确失败",
  UNKNOWN: "结果待核对",
  SENDING: "发送中 / 待核对",
}
export default function KookDispatchPage() {
  const auth = useAdminAuth()
  const result = useLoad(async () => (await request({ url: api })).data)
  const [form, setForm] = useState<Row | null>(null)
  const [baseline, setBaseline] = useState("")
  const [route, setRoute] = useState<Row | null>(null)
  const [action, setAction] = useState<{ kind: string; route?: Row } | null>(
    null
  )
  const [blocked, setBlocked] = useState("")
  const [probeKeys, setProbeKeys] = useState<Record<string, string>>({})
  useEffect(() => {
    if (result.data) {
      const data = result.data
      const value = {
        revision: data.revision,
        enabled: data.enabled,
        bot_token: "",
        clear_token: false,
        auto_new_orders: data.auto_new_orders,
        auto_rejected_orders: data.auto_rejected_orders,
        provider_order_url: data.provider_order_url,
        routes: data.routes,
      }
      setForm(value)
      setBaseline(JSON.stringify(value))
      setBlocked("")
    }
  }, [result.data])
  const review = useSourceReviewState("/admin/config/kook-dispatch/")
  const dirty = !!form && JSON.stringify(form) !== baseline
  const saved = result.data
  const missing = !saved && result.loading ? "loading" : !saved && result.error ? "error" : "unread"
  const editable =
    !!result.data && !!form &&
    auth.hasPerm("dispatch_config:edit") &&
    !result.loading &&
    !result.error &&
    !blocked &&
    !review.locked
  function change(key: string, value: any) {
    setForm((old) => (old ? { ...old, [key]: value } : old))
  }
  function condition(row: Row) {
    return (
      [
        row.game_category_id
          ? result.data?.games?.find((x: Row) => x.id === row.game_category_id)
              ?.name || `游戏 #${row.game_category_id}`
          : "",
        row.gameplay_id
          ? result.data?.gameplays?.find((x: Row) => x.id === row.gameplay_id)
              ?.name || `玩法 #${row.gameplay_id}`
          : "",
        row.platform === "PC"
          ? "端游"
          : row.platform === "MOBILE"
            ? "手游"
            : "",
      ]
        .filter(Boolean)
        .join(" · ") || "全部订单（兜底）"
    )
  }
  function move(index: number, delta: number) {
    if (!form) return
    const routes = [...form.routes]
    const [row] = routes.splice(index, 1)
    routes.splice(index + delta, 0, row)
    change("routes", routes)
  }
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton="kook-config"
    >
      {
        <div
          className="admin-source-page space-y-4"
          data-resource="kook-dispatch"
        >
          <SourcePageHeader
            title="群派单配置"
            tag="KOOK 派单"
            metrics={[
              { label: "频道规则", value: result.data?.routes?.length ?? "—" },
              { label: "配置版本", value: result.data?.revision ?? "—" },
            ]}
            description="规则按顺序匹配，每笔订单只进入第一个命中的频道。保存后才能校验频道和发送测试。"
          >
            <RefreshButton
              loading={result.loading}
              reload={() => {
                if (dirty) setAction({ kind: "reload" })
                else void result.reload()
              }}
            />
          </SourcePageHeader>
          <Feedback message={result.error} retry={result.reload} />
          <Feedback message={blocked} retry={result.reload} />
            <>
              <section className="admin-source-config-card space-y-5 rounded-xl border bg-card p-6 text-sm leading-5 shadow-sm">
                <div className="flex items-center justify-between">
                  <h2 className="flex items-center gap-2 text-base font-medium">
                    <Radio className="size-4 text-primary" />
                    机器人与发送方式
                  </h2>
                  <Status
                    value={saved?.enabled === false ? "DISABLED" : saved?.ready ? "ENABLED" : "PENDING"}
                    label={saved?.enabled === false ? "未启用" : saved?.ready ? "已启用且配置完整" : "配置待完善"}
                    readState={typeof saved?.enabled !== "boolean" || (saved.enabled && typeof saved.ready !== "boolean") ? missing : undefined}
                  />
                </div>
                {result.data?.token_error && (
                  <Feedback message="已保存的 Token 无法读取，请重新填写并保存。" />
                )}
                {!form && <DataRegion label="机器人与发送方式" loading={result.loading} hasSnapshot={false} error={result.error} retry={result.reload}>{null}</DataRegion>}
                <fieldset
                  disabled={!editable || !form}
                  className="grid grid-cols-2 gap-4"
                >
                  {[
                    {
                      key: "enabled",
                      label: "启用 KOOK 派单",
                      type: "boolean",
                    },
                    {
                      key: "bot_token",
                      label: result.data?.token_saved
                        ? "Bot Token（留空保留已保存 Token）"
                        : "Bot Token",
                      type: "password",
                    },
                    {
                      key: "clear_token",
                      label: "清除已保存 Token",
                      type: "boolean",
                    },
                    {
                      key: "auto_new_orders",
                      label: "自动发送新订单",
                      type: "boolean",
                    },
                    {
                      key: "auto_rejected_orders",
                      label: "自动发送拒单回池",
                      type: "boolean",
                    },
                    { key: "provider_order_url", label: "陪玩端网页地址" },
                  ].map((field) =>
                    field.type === "boolean" ? (
                      <label
                        key={field.key}
                        className="flex min-h-14 items-center justify-between gap-3 rounded-lg border px-4 py-3"
                      >
                        <span className="text-sm font-medium">
                          {field.label}
                        </span>
                        {form ? <Switch name={field.label}
                          aria-label={field.label}
                          checked={Boolean(form[field.key])}
                          onCheckedChange={(value) => change(field.key, value)}
                        /> : <DataValue known={false} loading={result.loading}>{null}</DataValue>}
                      </label>
                    ) : (
                      <Field
                        key={field.key}
                        field={field as any}
                        value={form?.[field.key] ?? ""}
                        onChange={(value) => change(field.key, value)}
                      />
                    )
                  )}
                </fieldset>
              </section>
              <section className="admin-source-config-card space-y-4 rounded-xl border bg-card p-6 text-sm leading-5 shadow-sm">
                <div className="flex items-center justify-between">
                  <h2 className="flex items-center gap-2 text-base font-medium">
                    <ListOrdered className="size-4 text-primary" />
                    频道与派单文案
                  </h2>
                  {editable && (
                    <Button
                      size="sm"
                      onClick={() =>
                        setRoute({
                          id: randomUuid(),
                          name: "",
                          enabled: true,
                          channel_id: "",
                          guild_id: "",
                          game_category_id: null,
                          gameplay_id: null,
                          platform: "",
                          template: result.data?.default_template || "",
                          mention_role_ids: [],
                        })
                      }
                    >
                      新增频道规则
                    </Button>
                  )}
                </div>
                <SourceDirectoryCard
                  title="派单规则"
                  description="按顺序匹配首个频道"
                  rows={form?.routes || []}
                  loading={result.loading || (!!result.data && !form)}
                  error={result.error}
                  retry={result.reload}
                  snapshotAvailable={!!result.data && !!form}
                  columns={[
                    {
                      key: "name",
                      label: "规则 / 顺序",
                      render: (row: Row) =>
                        `${(form?.routes.indexOf(row) ?? -1) + 1}. ${row.name}`,
                    },
                    { key: "condition", label: "匹配条件", render: condition },
                    { key: "channel_id", label: "文字频道 ID" },
                    {
                      key: "enabled",
                      label: "启用",
                      align: "center",
                      render: (row) => (
                        <Status value={row.enabled} readState={typeof row.enabled === "boolean" ? undefined : "unread"} />
                      ),
                    },
                    {
                      key: "actions",
                      label: "操作",
                      align: "right",
                      render: (row) => {
                        const index = form?.routes.indexOf(row) ?? -1
                        const uncertain = result.data?.tests?.some(
                          (x: Row) =>
                            x.channel_id === row.channel_id &&
                            ["UNKNOWN", "SENDING"].includes(x.status)
                        )
                        return (
                          <div className="flex max-w-lg flex-wrap justify-end gap-1">
                            <Button
                              actionTone="edit"
                              size="sm"
                              variant="outline"
                              disabled={!editable}
                              onClick={() => setRoute(structuredClone(row))}
                            >
                              编辑
                            </Button>
                            <Button
                              actionTone="read"
                              size="sm"
                              variant="ghost"
                              disabled={
                                !editable || dirty || !result.data?.token_saved
                              }
                              onClick={() =>
                                setAction({ kind: "check", route: row })
                              }
                            >
                              校验频道
                            </Button>
                            <Button
                              actionTone={probeKeys[row.id] ? "history" : "edit"}
                              size="sm"
                              variant="ghost"
                              disabled={
                                !editable ||
                                dirty ||
                                !result.data?.token_saved ||
                                (uncertain && !probeKeys[row.id])
                              }
                              onClick={() =>
                                setAction({ kind: "test", route: row })
                              }
                            >
                              {probeKeys[row.id] ? "核对上次测试" : "发送测试"}
                            </Button>
                            <Button
                              actionTone="edit"
                              size="sm"
                              variant="ghost"
                              disabled={!editable || index === 0}
                              onClick={() => move(index, -1)}
                            >
                              上移
                            </Button>
                            <Button
                              actionTone="edit"
                              size="sm"
                              variant="ghost"
                              disabled={
                                !editable || index === (form?.routes.length ?? 0) - 1
                              }
                              onClick={() => move(index, 1)}
                            >
                              下移
                            </Button>
                            <Button
                              actionTone="reject"
                              size="sm"
                              variant="ghost"
                              disabled={!editable}
                              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                              onClick={() =>
                                setAction({ kind: "remove", route: row })
                              }
                            >
                              移除
                            </Button>
                          </div>
                        )
                      },
                    },
                  ]}
                />
                <p className="text-xs text-muted-foreground">
                  填文字频道
                  ID；按游戏、玩法分类的规则放前面，全部订单的兜底规则放最后。
                </p>
                <div className="flex items-center gap-3">
                  {auth.hasPerm("dispatch_config:edit") && (
                    <Button
                      size="sm"
                      disabled={!dirty || !editable}
                      onClick={() => setAction({ kind: "save" })}
                    >
                      保存配置
                    </Button>
                  )}
                  {dirty && (
                    <span className="text-sm text-amber-700 dark:text-amber-400">
                      有未保存的修改
                    </span>
                  )}
                </div>
              </section>
              <section className="space-y-4">
                <SourceDirectoryCard
                  title="最近测试记录"
                  icon={History}
                  rows={result.data?.tests || []}
                  loading={result.loading}
                  error={result.error}
                  retry={result.reload}
                  snapshotAvailable={!!result.data}
                  columns={[
                    { key: "channel_id", label: "目标频道" },
                    {
                      key: "status",
                      label: "结果",
                      align: "center",
                      render: (row) => <Status value={row.status} label={statuses[row.status] || row.status}
                        tone={row.status === "SENT" ? "success" : row.status === "FAILED" ? "destructive" : ["UNKNOWN", "SENDING"].includes(row.status) ? "warning" : "muted"} />,
                    },
                    { key: "message_id", label: "KOOK 消息 ID" },
                    { key: "last_error", label: "说明" },
                    {
                      key: "created_at",
                      label: "时间",
                      render: (row) => formatDateTime(row.created_at),
                    },
                  ]}
                />
                <p className="text-xs text-muted-foreground">
                  测试卡片标明“非真实订单”，不提醒身份组。结果待核对时先查看频道消息和记录，避免重复发送。
                </p>
              </section>
            </>
          {route && (
            <RouteEditor
              row={route}
              locked={!editable}
              config={result.data}
              onClose={() => setRoute(null)}
              onSave={(value) => {
                if (!form) return
                const exists = form.routes.some((x: Row) => x.id === value.id)
                change(
                  "routes",
                  exists
                    ? form.routes.map((x: Row) =>
                        x.id === value.id ? value : x
                      )
                    : [...form.routes, value]
                )
                setRoute(null)
              }}
            />
          )}
          {action && (
            <MutationDialog
              readOnly={action.kind !== "reload" && !editable}
              submitVariant={
                ["reload", "remove"].includes(action.kind)
                  ? "destructive"
                  : "default"
              }
              title={
                action.kind === "save"
                  ? "保存群派单配置"
                  : action.kind === "reload"
                    ? "重新读取配置"
                    : action.kind === "remove"
                      ? "移除频道规则"
                      : action.kind === "test"
                        ? "发送 KOOK 测试卡片"
                        : "校验文字频道"
              }
              description={
                action.kind === "save"
                  ? `保存后启用状态为${form?.enabled ? "启用" : "停用"}，共 ${form?.routes.length} 条规则；匹配按页面顺序生效。`
                  : action.kind === "reload"
                    ? "重新读取将放弃尚未保存的草稿。"
                    : action.kind === "remove"
                      ? `移除「${action.route?.name}」；保存配置后对新订单生效。`
                      : `${action.route?.name} · 频道 ${action.route?.channel_id}${action.kind === "test" ? "。将向该频道发送一条标明测试的卡片。" : "。核对频道类型及所属服务器。"}`
              }
              onClose={() => setAction(null)}
              onSubmit={async () => {
                if (action.kind === "reload") {
                  await result.reload()
                  return
                }
                if (action.kind === "remove") {
                  change(
                    "routes",
                    form?.routes.filter((x: Row) => x.id !== action.route?.id)
                  )
                  return
                }
                if (action.kind === "save") {
                  try {
                    const value = (
                      await request({ url: api, method: "PUT", data: form })
                    ).data
                    result.setData(value)
                    toast.success("KOOK 配置已保存")
                  } catch (cause: any) {
                    if (
                      cause.status === 409 ||
                      !cause.status ||
                      cause.status >= 500
                    )
                      setBlocked(
                        cause.status === 409
                          ? "配置已被其他管理员修改，请重新读取后核对。"
                          : "保存结果未确认。请先核对请求和最新配置，再继续编辑。"
                      )
                    throw cause
                  }
                  return
                }
                if (action.kind === "check") {
                  const data = (
                    await request({
                      url: `${api}check/`,
                      method: "POST",
                      data: { route_id: action.route?.id },
                    })
                  ).data
                  toast.success(
                    `已确认文字频道「${data.name}」，服务器 ${data.guild_id}`
                  )
                  return
                }
                const id = action.route!.id
                const key = probeKeys[id] || randomUuid()
                setProbeKeys((old) => ({ ...old, [id]: key }))
                const data = (
                  await request({
                    url: `${api}test/`,
                    method: "POST",
                    data: { route_id: id, request_id: key, confirm: true },
                  })
                ).data
                result.setData((old: Row | null) =>
                  old
                    ? {
                        ...old,
                        tests: [
                          data,
                          ...old.tests.filter((x: Row) => x.id !== data.id),
                        ].slice(0, 10),
                      }
                    : old
                )
                if (["SENT", "FAILED"].includes(data.status))
                  setProbeKeys((old) => {
                    const next = { ...old }
                    delete next[id]
                    return next
                  })
                toast.info(statuses[data.status] || data.status)
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}

function RouteEditor({
  row,
  locked,
  config,
  onClose,
  onSave,
}: {
  row: Row
  locked: boolean
  config: Row
  onClose: () => void
  onSave: (row: Row) => void
}) {
  const [form, setForm] = useState<Row>({
    ...row,
    mentions: (row.mention_role_ids || []).join(","),
  })
  const [initialDraft] = useState(() => JSON.stringify(form))
  const [preview, setPreview] = useState("")
  const [previewError, setPreviewError] = useState("")
  const [busy, setBusy] = useState(false)
  async function previewTemplate() {
    setBusy(true)
    setPreviewError("")
    try {
      const content = (
        await request({
          url: `${api}preview/`,
          method: "POST",
          data: { template: form.template },
        })
      ).data.content
      setPreview(content)
      return content
    } catch (cause: any) {
      setPreviewError(cause.message)
      throw cause
    } finally {
      setBusy(false)
    }
  }
  return (
    <MutationDialog
      dirty={JSON.stringify(form) !== initialDraft}
      title="频道与文案规则"
      readOnly={locked}
      contentClassName="sm:max-w-4xl"
      confirmDiscard
      description="编辑匹配条件和派单文案，应用后仍需保存整份配置。"
      onClose={onClose}
      submitText="应用到配置"
      disabled={busy}
      onSubmit={async () => {
        if (!form.name.trim() || !/^[0-9]{1,64}$/.test(form.channel_id))
          throw new Error("请填写规则名称和数字格式的文字频道 ID。")
        const ids = form.mentions
          .split(",")
          .map((x: string) => x.trim())
          .filter(Boolean)
        if (
          ids.length > 10 ||
          ids.some((x: string) => !/^[0-9]{1,64}$/.test(x))
        )
          throw new Error("身份组 ID 应为数字，最多十个，使用英文逗号分隔。")
        await previewTemplate()
        const { mentions, ...payload } = form
        void mentions
        onSave({
          ...payload,
          game_category_id: Number(form.game_category_id) || null,
          gameplay_id: Number(form.gameplay_id) || null,
          mention_role_ids: ids,
        })
      }}
    >
      <div className="admin-source-editor grid grid-cols-2 gap-4">
        {[
          { key: "name", label: "规则名称", required: true },
          { key: "channel_id", label: "文字频道 ID", required: true },
          { key: "guild_id", label: "服务器 ID（选填）" },
          {
            key: "game_category_id",
            label: "游戏",
            type: "select",
            options: (config.games || []).map((x: Row) => [
              String(x.id),
              x.name,
            ]),
          },
          {
            key: "gameplay_id",
            label: "玩法",
            type: "select",
            options: (config.gameplays || []).map((x: Row) => [
              String(x.id),
              x.name,
            ]),
          },
          {
            key: "platform",
            label: "平台",
            type: "select",
            options: [
              ["PC", "端游"],
              ["MOBILE", "手游"],
            ],
          },
          { key: "mentions", label: "提醒身份组 ID（英文逗号分隔）" },
          { key: "enabled", label: "启用此规则", type: "boolean" },
          { key: "template", label: "派单文案", type: "textarea", full: true },
        ].map((field) => (
          <Field
            key={field.key}
            field={field as any}
            value={form[field.key]}
            onChange={(value) => {
              setForm((old) => ({ ...old, [field.key]: value }))
              setPreview("")
            }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(config.template_fields || {}).map(([key, label]) => (
          <Button
            key={key}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setForm((old) => ({
                ...old,
                template: old.template + `{${key}}`,
              }))
              setPreview("")
            }}
          >
            {String(label)} {`{${key}}`}
          </Button>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        必须保留 {"{order_no}"} 和 {"{original_amount}"}；群发展示服务原价，不承诺陪玩收益。
      </p>
      {(form.template.includes("{provider_income") || !form.template.includes("{original_amount}")) && (
        <Feedback message="旧文案不符合当前群发规则，发送时会改用默认文案；请更新并保存此规则。" />
      )}
      {form.template.includes("报单审核通过后结算") && (
        <Feedback message="请核对旧结算说明：报单通过后仍可能需要等待订单结算或收益解冻。" />
      )}
      <Button
        type="button"
        variant="outline"
        disabled={busy || locked}
        onClick={() => {
          void previewTemplate().catch(() => {})
        }}
      >
        预览文案
      </Button>
      <Feedback message={previewError} />
      {preview && (
        <pre className="rounded-lg bg-muted p-4 text-sm whitespace-pre-wrap">
          {preview}
        </pre>
      )}
    </MutationDialog>
  )
}
