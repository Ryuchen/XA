import { useEffect, useMemo, useState, type FormEvent } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { DataRegion } from "@/components/page-data-state"
import { MutationDialog } from "@/components/business"
import { formatDateTime } from "@xa/shared"
import { platformRequest } from "@/lib/platform-session"
import {
  COMPLIANCE_FIELDS,
  complianceCategoryLabels,
  complianceFieldLabels,
  complianceWriteFailure,
  parseComplianceDraftResult,
  parseCompliancePublishResult,
  parseComplianceReceiptPage,
  parseComplianceRuleDirectory,
  parseComplianceRulesJson,
  nextComplianceDraftVersion,
  type ComplianceField,
  type ComplianceReceiptPage,
  type ComplianceRule,
  type ComplianceRuleDirectory,
  type ComplianceRuleVersion,
} from "@/lib/compliance"

type Store = { id: number; slug: string; name: string }

const PAGE_SIZE = 20

export function ComplianceRulePagination({ directory, loading, error, onChange }: {
  directory: ComplianceRuleDirectory | null
  loading: boolean
  error: string
  onChange: (page: number) => void
}) {
  if (!directory) return null
  const totalPages = Math.max(1, Math.ceil(directory.count / directory.pageSize))
  const disabled = loading || !!error
  return <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
    <span>共 {directory.count} 个版本 · 第 {directory.page} / {totalPages} 页</span>
    <div className="flex gap-2">
      <Button type="button" variant="outline" size="sm" disabled={disabled || !directory.previous} onClick={() => onChange(directory.page - 1)}>上一页</Button>
      <Button type="button" variant="outline" size="sm" disabled={disabled || !directory.next} onClick={() => onChange(directory.page + 1)}>下一页</Button>
    </div>
  </div>
}

export function PlatformCompliance({ stores }: { stores: Store[] }) {
  const [directory, setDirectory] = useState<ComplianceRuleDirectory | null>(null)
  const [directoryLoading, setDirectoryLoading] = useState(true)
  const [directoryError, setDirectoryError] = useState("")
  const [directoryRevision, setDirectoryRevision] = useState(0)
  const [rulesPage, setRulesPage] = useState(1)
  const [draftSource, setDraftSource] = useState<ComplianceRuleVersion | null | undefined>(undefined)
  const [draftRaw, setDraftRaw] = useState("")
  const [draftError, setDraftError] = useState("")
  const [draftBusy, setDraftBusy] = useState(false)
  const [draftUncertain, setDraftUncertain] = useState(false)
  const [writeDenied, setWriteDenied] = useState(false)
  const [notice, setNotice] = useState("")
  const [publishing, setPublishing] = useState<ComplianceRuleVersion | null>(null)
  const [publishUncertain, setPublishUncertain] = useState(false)
  const [storeId, setStoreId] = useState("")
  const [field, setField] = useState<"" | ComplianceField>("")
  const [page, setPage] = useState(1)
  const [receipts, setReceipts] = useState<ComplianceReceiptPage | null>(null)
  const [receiptsLoading, setReceiptsLoading] = useState(false)
  const [receiptsError, setReceiptsError] = useState("")
  const [receiptRevision, setReceiptRevision] = useState(0)

  useEffect(() => {
    let active = true
    setDirectoryLoading(true)
    setDirectoryError("")
    platformRequest<unknown>(`compliance-rules?page=${rulesPage}&page_size=${PAGE_SIZE}`)
      .then(value => {
        if (!active) return
        setDirectory(parseComplianceRuleDirectory(value, { page: rulesPage, pageSize: PAGE_SIZE }))
        setDraftUncertain(false)
        setPublishUncertain(false)
      })
      .catch(cause => { if (active) setDirectoryError(cause instanceof Error ? cause.message : "无法读取合规规则") })
      .finally(() => { if (active) setDirectoryLoading(false) })
    return () => { active = false }
  }, [directoryRevision, rulesPage])

  useEffect(() => {
    if (!storeId) {
      setReceipts(null)
      setReceiptsError("")
      setReceiptsLoading(false)
      return
    }
    let active = true
    const store = stores.find(item => String(item.id) === storeId)
    if (!store) {
      setReceipts(null)
      setReceiptsError("所选门店已不在当前平台目录中，请重新选择门店。")
      setReceiptsLoading(false)
      return
    }
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
    if (field) params.set("field_name", field)
    setReceiptsLoading(true)
    setReceiptsError("")
    platformRequest<unknown>(`store-management/${storeId}/content-compliance-receipts?${params}`)
      .then(value => {
        if (!active) return
        const parsed = parseComplianceReceiptPage(value, {
          storeId: store.id,
          storeSlug: store.slug,
          fieldName: field,
          page,
          pageSize: PAGE_SIZE,
        })
        setReceipts(parsed)
      })
      .catch(cause => { if (active) setReceiptsError(cause instanceof Error ? cause.message : "无法读取门店内容合规回执") })
      .finally(() => { if (active) setReceiptsLoading(false) })
    return () => { active = false }
  }, [storeId, field, page, receiptRevision, stores])

  const versions = useMemo(
    () => [...(directory?.versions || [])].sort((left, right) => right.version - left.version),
    [directory],
  )
  const current = directory?.current || null
  const selectedStore = stores.find(item => String(item.id) === storeId)
  const totalPages = receipts ? Math.max(1, Math.ceil(receipts.count / PAGE_SIZE)) : 1
  const nextDraftVersion = nextComplianceDraftVersion(directory)

  function retryRules() {
    setDirectoryRevision(value => value + 1)
  }

  function reloadRules() {
    setRulesPage(1)
    setDirectoryRevision(value => value + 1)
  }

  function changeRulesPage(next: number) {
    if (!directory || next < 1 || next > Math.max(1, Math.ceil(directory.count / directory.pageSize)) || next === directory.page) return
    setRulesPage(next)
  }

  function beginDraft(source: ComplianceRuleVersion | null) {
    if (writeDenied || draftBusy || draftUncertain || publishUncertain) return
    setDraftSource(source)
    setDraftRaw(JSON.stringify(source?.rules || [], null, 2))
    setDraftError("")
    setDraftUncertain(false)
    setNotice("")
    requestAnimationFrame(() => document.getElementById("compliance-rule-draft")?.scrollIntoView({ block: "start", behavior: "smooth" }))
  }

  async function createDraft(event: FormEvent) {
    event.preventDefault()
    if (draftBusy || draftUncertain || writeDenied) return
    let rules: ComplianceRule[]
    try {
      rules = parseComplianceRulesJson(draftRaw)
    } catch (cause) {
      setDraftError(cause instanceof Error ? cause.message : "规则内容无效")
      return
    }
    setDraftBusy(true)
    setDraftError("")
    setNotice("")
    try {
      const created = parseComplianceDraftResult(
        await platformRequest<unknown>("compliance-rules", { method: "POST", data: { rules } }),
        { version: nextDraftVersion, rules },
      )
      setDraftSource(undefined)
      setNotice(`规则草稿 v${created.version} 已创建；发布前请复核规则摘要和适用字段。`)
      reloadRules()
    } catch (cause) {
      const outcome = complianceWriteFailure(cause)
      setDraftUncertain(outcome.uncertain)
      if (outcome.denied) setWriteDenied(true)
      setDraftError(cause instanceof Error ? cause.message : "规则草稿创建失败，请刷新核对")
    } finally {
      setDraftBusy(false)
    }
  }

  async function publish() {
    if (!publishing || publishUncertain || writeDenied) return
    try {
      const published = parseCompliancePublishResult(
        await platformRequest<unknown>(`compliance-rules/${publishing.version}/publish`, { method: "POST", data: {} }),
        publishing,
      )
      setNotice(`规则 v${published.version} 已发布，后续门店内容校验将使用该版本。`)
      setPublishing(null)
      reloadRules()
    } catch (cause) {
      const outcome = complianceWriteFailure(cause)
      setPublishUncertain(outcome.uncertain)
      if (outcome.denied) {
        setWriteDenied(true)
        setPublishing(null)
      }
      throw cause
    }
  }

  function closePublish() {
    setPublishing(null)
    if (publishUncertain) reloadRules()
  }

  function closeDraft() {
    setDraftSource(undefined)
    setDraftError("")
    if (draftUncertain) reloadRules()
  }

  function changePage(next: number) {
    setPage(next)
    setReceipts(null)
    setReceiptsError("")
  }

  return (
    <div className="space-y-6">
      <section className="saas-section" aria-labelledby="compliance-rules-title">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 id="compliance-rules-title">合规规则版本</h2>
            <p className="saas-note">平台发布不可变规则版本；门店保存内容时记录版本、内容摘要、命中项和豁免项。规则不可用时拒绝保存。</p>
          </div>
          <Button type="button" disabled={directoryLoading || !!directoryError || draftUncertain || publishUncertain || writeDenied} onClick={() => beginDraft(current || null)}>新建规则草稿</Button>
        </div>
        {notice && <p role="status" className="mt-4 rounded-md border bg-muted/30 px-3 py-2 text-sm">{notice}</p>}
        {writeDenied && <p role="alert" className="mt-4 rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">平台写权限已失效，正在重新核验身份；核验完成前已停用规则创建和发布入口。</p>}
        {directoryError && <div role="alert" className="mt-4 space-y-2 text-sm text-destructive"><p>{directoryError}</p>{directory && <p>当前保留上次读取结果；规则状态可能已变化。<Button type="button" variant="outline" size="sm" onClick={retryRules}>重试读取</Button></p>}</div>}
        {directoryLoading && directory && <p role="status" className="mt-3 text-sm text-muted-foreground">正在刷新规则目录，当前保留上次读取结果。</p>}
        <div className="saas-definition">
          <div><dt>当前生效</dt><dd>{current ? `v${current.version}` : "尚未发布"}</dd></div>
          <div><dt>已登记版本</dt><dd>{directory ? directory.count : "待核对"}</dd></div>
          <div><dt>当前规则摘要</dt><dd className="break-all font-mono text-xs">{current?.rulesSha256 || "待核对"}</dd></div>
        </div>
        <div className="overflow-x-auto" aria-busy={directoryLoading}>
          <table className="saas-table">
            <thead><tr><th scope="col">版本</th><th scope="col">状态</th><th scope="col">规则</th><th scope="col">创建 / 发布</th><th scope="col">规则集摘要</th><th scope="col" data-table-action>操作</th></tr></thead>
            <tbody>
              {versions.map(item => <tr key={item.version}>
                <td className="font-medium">v{item.version}{item.version === current?.version && <Badge variant="secondary" className="ml-2">当前生效</Badge>}</td>
                <td><Badge variant={item.status === "PUBLISHED" ? "default" : "outline"}>{item.status === "PUBLISHED" ? "已发布" : "草稿"}</Badge></td>
                <td>{item.rules.length} 条，启用 {item.rules.filter(rule => rule.enabled).length} 条</td>
                <td><span className="block">{item.createdAt ? formatDateTime(item.createdAt) : "尚未登记"}</span><span className="block text-xs text-muted-foreground">{item.publishedAt ? formatDateTime(item.publishedAt) : "尚未发布"}</span></td>
                <td><code className="break-all text-xs" title={item.rulesSha256}>{item.rulesSha256}</code></td>
                <td data-table-action><div className="admin-table-actions ml-auto flex flex-wrap justify-end gap-1">
                  <Button type="button" actionTone="read" variant="ghost" size="sm" disabled={directoryLoading || !!directoryError || draftUncertain || publishUncertain || writeDenied} onClick={() => beginDraft(item)}>以此新建草稿</Button>
                  {item.status === "DRAFT" && <Button type="button" actionTone="approve" variant="outline" size="sm" disabled={directoryLoading || !!directoryError || draftUncertain || publishUncertain || writeDenied} onClick={() => { if (!writeDenied) { setPublishUncertain(false); setPublishing(item) } }}>发布</Button>}
                </div></td>
              </tr>)}
              {!versions.length && <tr><td colSpan={6}><DataRegion label="合规规则版本" loading={directoryLoading} hasSnapshot={!!directory} error={directoryError} retry={retryRules}><span>{directory ? "尚无规则版本，请创建并发布首个版本。" : "等待读取合规规则版本。"}</span></DataRegion></td></tr>}
            </tbody>
          </table>
        </div>
        <ComplianceRulePagination directory={directory} loading={directoryLoading} error={directoryError} onChange={changeRulesPage} />
        {versions.map(item => <details key={`detail-${item.version}`} className="mt-3 border-t pt-3 text-sm">
          <summary className="cursor-pointer text-muted-foreground">查看 v{item.version} 的规则内容</summary>
          <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/20 p-3 text-xs">{JSON.stringify(item.rules, null, 2)}</pre>
        </details>)}
      </section>

      {draftSource !== undefined && <section id="compliance-rule-draft" className="saas-section scroll-mt-6" aria-labelledby="compliance-draft-title">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h2 id="compliance-draft-title">新建规则草稿</h2><p className="saas-note">来源：{draftSource ? `v${draftSource.version}` : "空白规则集"}。仅支持字面匹配词，不执行正则表达式。</p></div>
          <Button type="button" variant="ghost" disabled={draftBusy} onClick={closeDraft}>收起</Button>
        </div>
        <form onSubmit={createDraft} className="mt-4 space-y-3">
          <label htmlFor="compliance-rules-json" className="block text-sm font-medium">规则 JSON</label>
          <Textarea id="compliance-rules-json" value={draftRaw} onChange={event => setDraftRaw(event.target.value)} disabled={draftBusy || draftUncertain || writeDenied} spellCheck={false} className="min-h-96 font-mono text-xs" aria-describedby="compliance-rules-help" />
          <div id="compliance-rules-help" className="grid gap-2 text-xs leading-5 text-muted-foreground md:grid-cols-2">
            <p>每条规则包含 code、category、applies_to、terms、message、enabled。code 必须唯一，terms 为 1–30 个 100 字以内的非空字面匹配词，message 不超过 300 字。</p>
            <p>类别：{Object.values(complianceCategoryLabels).join("、")}。昵称保留词仅 content:official_name 权限可豁免。</p>
          </div>
          {draftError && <p role="alert" className="text-sm text-destructive">{draftError}</p>}
          {draftUncertain && <p role="alert" className="text-sm text-muted-foreground">草稿创建结果尚未确认。请先刷新规则版本列表，确认是否已创建后再继续。</p>}
          <div className="flex gap-2"><Button type="submit" disabled={draftBusy || draftUncertain || writeDenied}>{draftBusy ? "正在创建…" : "校验并创建草稿"}</Button><Button type="button" variant="outline" disabled={draftBusy} onClick={closeDraft}>{draftUncertain ? "关闭并刷新" : "取消"}</Button></div>
        </form>
      </section>}

      <section className="saas-section" aria-labelledby="compliance-receipts-title">
        <div><h2 id="compliance-receipts-title">门店内容合规回执</h2><p className="saas-note">按门店核对留痕，不展示门店正文。内容摘要用于关联原记录，命中项和豁免项用于复核当次保存。</p></div>
        <div className="mt-4 grid gap-3 md:grid-cols-[minmax(220px,1fr)_minmax(180px,280px)_auto]">
          <label className="space-y-1 text-sm"><span>门店</span><select className="saas-select w-full" value={storeId} onChange={event => { setStoreId(event.target.value); setPage(1); setReceipts(null); setReceiptsError("") }}><option value="">请选择门店</option>{stores.map(store => <option key={store.id} value={store.id}>{store.name}（{store.slug}）</option>)}</select></label>
          <label className="space-y-1 text-sm"><span>内容字段</span><select className="saas-select w-full" value={field} onChange={event => { setField(event.target.value as "" | ComplianceField); setPage(1); setReceipts(null); setReceiptsError("") }}><option value="">全部字段</option>{COMPLIANCE_FIELDS.map(item => <option key={item} value={item}>{complianceFieldLabels[item]}</option>)}</select></label>
          <Button type="button" variant="outline" className="self-end" disabled={!storeId || receiptsLoading} onClick={() => setReceiptRevision(value => value + 1)}>重新读取</Button>
        </div>
        {!selectedStore && <p className="mt-4 text-sm text-muted-foreground">选择门店后读取本店回执。</p>}
        {receiptsError && <div role="alert" className="mt-4 space-y-2 text-sm text-destructive"><p>{receiptsError}</p>{receipts && <p>当前保留上次读取结果。<Button type="button" variant="outline" size="sm" onClick={() => setReceiptRevision(value => value + 1)}>重试读取</Button></p>}</div>}
        {receiptsLoading && receipts && <p role="status" className="mt-3 text-sm text-muted-foreground">正在刷新门店回执，当前保留上次读取结果。</p>}
        {selectedStore && <div className="overflow-x-auto" aria-busy={receiptsLoading}>
          <table className="saas-table">
            <thead><tr><th scope="col">保存时间</th><th scope="col">字段</th><th scope="col">规则版本</th><th scope="col">命中 / 豁免</th><th scope="col">内容摘要</th><th scope="col">操作者</th></tr></thead>
            <tbody>
              {(receipts?.results || []).map(item => <tr key={item.id}>
                <td>{formatDateTime(item.createdAt)}</td>
                <td>{complianceFieldLabels[item.fieldName]}</td>
                <td>v{item.ruleVersion}<span className="block break-all font-mono text-[11px] text-muted-foreground" title={item.ruleSetSha256}>{item.ruleSetSha256}</span></td>
                <td><span className="block">命中：{item.matchedRuleCodes.join("、") || "无"}</span><span className="block text-xs text-muted-foreground">豁免：{item.exemptedRuleCodes.join("、") || "无"}</span></td>
                <td><code className="break-all text-xs" title={item.contentSha256}>{item.contentSha256}</code></td>
                <td><span className="block">{item.actorLabel || "未知操作者"}</span><span className="block text-xs text-muted-foreground">{item.actorType}{item.actorId ? ` · ${item.actorId}` : ""}</span></td>
              </tr>)}
              {!receipts?.results.length && <tr><td colSpan={6}><DataRegion label="门店内容合规回执" loading={receiptsLoading} hasSnapshot={!!receipts} error={receiptsError} retry={() => setReceiptRevision(value => value + 1)}><span>{receipts ? "该门店暂无符合筛选条件的合规回执。" : "等待读取门店回执。"}</span></DataRegion></td></tr>}
            </tbody>
          </table>
        </div>}
        {receipts && <div className="mt-4 flex items-center justify-between gap-3 text-sm text-muted-foreground"><span>共 {receipts.count} 条 · 第 {page} / {totalPages} 页</span><div className="flex gap-2"><Button type="button" variant="outline" size="sm" disabled={!receipts.previous || receiptsLoading} onClick={() => changePage(Math.max(1, page - 1))}>上一页</Button><Button type="button" variant="outline" size="sm" disabled={!receipts.next || receiptsLoading} onClick={() => changePage(page + 1)}>下一页</Button></div></div>}
      </section>

      {publishing && <MutationDialog title={`发布规则 v${publishing.version}`} description={`该版本包含 ${publishing.rules.length} 条规则。发布后不可修改，后续门店内容保存将按此版本校验并留痕。`} submitText="确认发布" disabled={publishUncertain || writeDenied} onClose={closePublish} onSubmit={publish}>
        <dl className="space-y-2 text-sm"><div><dt className="text-muted-foreground">规则集摘要</dt><dd className="break-all font-mono text-xs">{publishing.rulesSha256}</dd></div><div><dt className="text-muted-foreground">启用规则</dt><dd>{publishing.rules.filter(rule => rule.enabled).length} 条</dd></div></dl>
        {publishUncertain && <p role="alert" className="mt-3 text-sm text-muted-foreground">发布结果尚未确认。请关闭后刷新版本列表，核对状态后再继续。</p>}
      </MutationDialog>}
    </div>
  )
}
