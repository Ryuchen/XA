import { useEffect, useRef, useState } from 'react'
import { Check, CheckCircle2, ChevronDown, Eye, FileCheck2, ImagePlus, Plus, Trash2, Undo2 } from 'lucide-react'
import { ProviderDialogContent as DialogContent, ProviderDialogFooter as DialogFooter, ProviderDialogHeader } from '@/components/provider/ProviderDialog'
import { ProviderFrame } from '@/components/provider/ProviderFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose } from '@/components/ui/dialog'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ProviderHeader, ProviderNotice, ProviderPage, ProviderSection, ProviderState } from '@/pages/provider/ProviderShared'
import { providerOrders, providerReports, type ProviderScenario } from '@/pages/provider/provider-data'

type EvidenceImage = { id: string; url: string; name: string }
type ReportRow = (typeof providerReports)[number] & {
  entryImage: EvidenceImage | null
  completionImage: EvidenceImage | null
  resultImages: EvidenceImage[]
}

function sampleImage(id: string, name: string, url: string): EvidenceImage {
  return { id, name, url }
}

function initialReportRows(): ReportRow[] {
  return providerReports.map((report, reportIndex) => ({
    ...report,
    evidence: { ...report.evidence },
    entryImage: report.evidence.entry ? sampleImage(`entry-${report.id}`, '入队截图（示例）', '/report-evidence/entry.svg') : null,
    completionImage: report.evidence.completion ? sampleImage(`completion-${report.id}`, '结单截图（示例）', '/report-evidence/completion.svg') : null,
    resultImages: report.evidence.result ? Array.from({ length: reportIndex === 1 ? 2 : 3 }, (_, index) => sampleImage(`result-${report.id}-${index}`, `战绩截图 ${index + 1}（示例）`, `/report-evidence/result-${index + 1}.svg`)) : [],
  }))
}

export function ProviderReportsPage({ scenario, onRetry }: { scenario: ProviderScenario; onRetry: () => void }) {
  const [reports, setReports] = useState<ReportRow[]>(initialReportRows)
  const [createOpen, setCreateOpen] = useState(false)
  const [selectedOrder, setSelectedOrder] = useState<number | null>(null)
  const [preview, setPreview] = useState<{ image: EvidenceImage; alt: string } | null>(null)
  const [notice, setNotice] = useState('')
  const blobUrls = useRef(new Set<string>())
  const entitySequence = useRef(4)
  useEffect(() => {
    const urls = blobUrls.current
    return () => { urls.forEach(url => URL.revokeObjectURL(url)) }
  }, [])

  function localImage(file: File, id: string): EvidenceImage {
    const url = URL.createObjectURL(file)
    blobUrls.current.add(url)
    return { id, url, name: file.name }
  }

  function create() {
    if (!selectedOrder) return
    const order = providerOrders.find(item => item.id === selectedOrder)
    const id = entitySequence.current++
    setReports(items => [{
      id,
      orderNo: order?.orderNo || '',
      service: order?.service || '',
      status: '待提交',
      income: 96,
      remark: '服务截图已同步，请补充至少 1 张战绩截图。',
      evidence: { entry: true, completion: true, result: false },
      editable: true,
      entryImage: sampleImage(`entry-${id}`, '入队截图（示例）', '/report-evidence/entry.svg'),
      completionImage: sampleImage(`completion-${id}`, '结单截图（示例）', '/report-evidence/completion.svg'),
      resultImages: [],
    }, ...items])
    setCreateOpen(false)
    setSelectedOrder(null)
    setNotice('报单已创建，请核对并补齐凭证。')
  }

  function uploadServiceImage(reportId: number, kind: 'entry' | 'completion', files: File[]) {
    const report = reports.find(item => item.id === reportId)
    const file = files[0]
    if (!report?.editable || !file) return
    const image = localImage(file, `${kind}-${reportId}-${entitySequence.current++}`)
    setReports(items => items.map(item => item.id === reportId ? {
      ...item,
      ...(kind === 'entry' ? { entryImage: image } : { completionImage: image }),
      evidence: { ...item.evidence, [kind]: true },
    } : item))
    setNotice(`已更新${kind === 'entry' ? '入队' : '结单'}截图，可点击缩略图查看。`)
  }

  function uploadResultImages(reportId: number, files: File[]) {
    const report = reports.find(item => item.id === reportId)
    if (!report?.editable || !files.length) return
    const remaining = Math.max(0, 9 - report.resultImages.length)
    const accepted = files.slice(0, remaining)
    if (!accepted.length) { setNotice('每笔报单最多保留 9 张战绩截图，可先删除后再添加。'); return }
    const additions = accepted.map((file, index) => localImage(file, `result-${reportId}-${entitySequence.current++}-${index}`))
    setReports(items => items.map(item => item.id === reportId ? {
      ...item,
      resultImages: [...item.resultImages, ...additions],
      evidence: { ...item.evidence, result: true },
      remark: '三类凭证已齐全，可提交审核；提交后仍可查看。',
    } : item))
    setNotice(`已添加 ${additions.length} 张战绩截图${files.length > additions.length ? `，${files.length - additions.length} 张超出 9 张上限未添加` : ''}。`)
  }

  function removeResultImage(reportId: number, imageId: string) {
    const report = reports.find(item => item.id === reportId)
    if (!report?.editable) return
    setReports(items => items.map(item => {
      if (item.id !== reportId) return item
      const resultImages = item.resultImages.filter(image => image.id !== imageId)
      return { ...item, resultImages, evidence: { ...item.evidence, result: resultImages.length > 0 } }
    }))
    setNotice('战绩截图已删除，其余凭证保留。')
  }

  function submitReport(reportId: number) {
    const report = reports.find(item => item.id === reportId)
    if (!report?.editable || !report.entryImage || !report.completionImage || !report.resultImages.length) return
    setReports(items => items.map(item => item.id === reportId ? { ...item, status: '审核中', editable: false, remark: '已提交审核，可查看服务凭证；审核通过前可撤回。' } : item))
    setNotice('报单已提交审核，可查看凭证或撤回审核。')
  }

  function withdrawReport(reportId: number) {
    const report = reports.find(item => item.id === reportId)
    if (report?.status !== '审核中') return
    setReports(items => items.map(item => item.id === reportId ? { ...item, status: '待提交', editable: true, remark: '已撤回审核，凭证已保留，可修改后重新提交。' } : item))
    setNotice('已撤回审核，凭证保留，可修改后重新提交。')
  }

  return <ProviderFrame><ProviderPage>
    <ProviderHeader compact title="我的报单" description="核对订单凭证、审核结果与实际结算。" action={<Button size="sm" className="rounded-[6px]" onClick={() => setCreateOpen(true)}><Plus className="size-4" /><span className="hidden sm:inline">从订单创建</span><span className="sm:hidden">创建</span></Button>} />
    {scenario !== 'default' ? <ProviderState scenario={scenario} onRetry={onRetry} emptyTitle="暂无报单" emptyDescription="接单后创建报单，服务截图会自动关联。" emptyAction={<Button onClick={() => setCreateOpen(true)}>从订单创建</Button>} /> : <ProviderSection>
      {notice && <ProviderNotice>{notice}</ProviderNotice>}
      <div className="grid items-start gap-4 xl:grid-cols-2">{reports.map(report => {
        const canSubmit = report.editable && report.entryImage && report.completionImage && report.resultImages.length > 0
        return <article key={report.id} className="rounded-[12px] border bg-card p-4 shadow-sm md:p-5">
          <div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold">{report.service}</h2><p className="mt-1 font-mono text-xs text-muted-foreground">{report.orderNo}</p></div><Badge variant="outline" className={cn(report.status === '已通过' ? 'border-success/25 bg-success/10 text-success' : report.status === '审核中' ? 'border-primary/25 bg-primary/10 text-primary' : 'border-warning/25 bg-warning/10 text-warning')}>{report.status}</Badge></div>
          <dl data-testid="report-summary" className="mt-4 grid grid-cols-2 divide-x divide-border border-y py-3 text-xs">{[
            { label: report.status === '已通过' ? '结算收益' : '预计净收益', value: `${formatWaveAmount(report.income)} 浪花` },
            { label: '凭证准备', value: `${Object.values(report.evidence).filter(Boolean).length} / 3 类` },
          ].map((item, index) => <div key={item.label} className={cn('flex min-w-0 items-center justify-between gap-2', index === 0 ? 'pr-3' : 'pl-3')}><dt className="text-muted-foreground">{item.label}</dt><dd className="whitespace-nowrap text-sm font-semibold">{item.value}</dd></div>)}</dl>
          <div className="mt-4 grid grid-cols-3 gap-2">{[
            { key: 'entry', label: '入队截图', ready: Boolean(report.entryImage), count: '' },
            { key: 'completion', label: '结单截图', ready: Boolean(report.completionImage), count: '' },
            { key: 'result', label: '战绩截图', ready: report.resultImages.length > 0, count: report.resultImages.length ? `${report.resultImages.length} 张` : '待补充' },
          ].map(item => <div key={item.key} className={cn('flex min-h-20 flex-col items-center justify-center gap-1 rounded-[8px] border text-center text-xs', item.ready ? 'border-success/20 bg-success/5 text-success' : 'border-dashed text-muted-foreground')}><span>{item.ready ? <CheckCircle2 className="size-5" /> : <ImagePlus className="size-5" />}</span><span>{item.label}</span>{item.count && <span>{item.count}</span>}</div>)}</div>
          <p className="mt-4 text-xs leading-5 text-muted-foreground">{report.remark}</p>
          <details className="group/evidence mt-4 overflow-hidden rounded-[8px] border bg-muted/10">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-3 text-xs font-medium [&::-webkit-details-marker]:hidden">
              <span className="flex items-center gap-2"><FileCheck2 className="size-4 text-primary" />{report.editable ? '查看与补充凭证' : '查看服务凭证'}</span>
              <ChevronDown className="size-4 text-muted-foreground transition-transform group-open/evidence:rotate-180" />
            </summary>
            <div className="space-y-4 border-t p-3">
              <section>
                <h3 className="text-sm font-semibold">服务凭证</h3>
                <div className="mt-3 grid gap-4 sm:grid-cols-2">{[
                  { kind: 'entry' as const, label: '入队截图', image: report.entryImage },
                  { kind: 'completion' as const, label: '结单截图', image: report.completionImage },
                ].map(item => <div key={item.kind}>
                  <p className="text-xs font-medium">{item.label}</p>
                  {item.image ? <button type="button" className="mt-2 block w-full rounded-[8px] text-left" aria-label={`查看${item.label}`} onClick={() => setPreview({ image: item.image!, alt: item.label })}><img src={item.image.url} alt={item.image.name} className="aspect-[16/10] w-full rounded-[8px] border bg-muted object-cover" /><span className="mt-1 flex items-center gap-1 text-xs text-primary"><Eye className="size-3.5" />查看大图</span></button> : <p className="mt-2 rounded-[8px] border border-dashed p-5 text-center text-xs text-muted-foreground">暂无截图</p>}
                  {report.editable && <label className="mt-2 flex min-h-9 cursor-pointer items-center justify-center gap-2 rounded-[8px] border border-dashed text-xs text-primary"><input type="file" accept="image/*" className="sr-only" aria-label={`更换${report.service}${item.label}`} onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; uploadServiceImage(report.id, item.kind, files) }} /><ImagePlus className="size-4" />{item.image ? '更换截图' : '上传截图'}</label>}
                </div>)}</div>
              </section>
              <section className="border-t pt-4">
                <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-semibold">战绩截图</h3><span data-testid={`result-count-${report.id}`} className="text-xs text-muted-foreground">{report.resultImages.length} / 9 张</span></div>
                <p className="mt-1 text-xs text-muted-foreground">至少 1 张，最多 9 张；提交审核后仍可逐张查看。</p>
                {report.resultImages.length > 0 && <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">{report.resultImages.map((image, index) => <div key={image.id}>
                  <button type="button" className="block w-full rounded-[8px] text-left" aria-label={`查看战绩截图 ${index + 1}`} onClick={() => setPreview({ image, alt: `战绩截图 ${index + 1}` })}><img src={image.url} alt={image.name} className="aspect-[16/10] w-full rounded-[8px] border bg-muted object-cover" /><span className="mt-1 flex items-center gap-1 text-xs text-primary"><Eye className="size-3.5" />第 {index + 1} 张</span></button>
                  {report.editable && <Button type="button" size="sm" variant="ghost" className="mt-1 w-full rounded-[6px] px-2 text-xs text-muted-foreground" onClick={() => removeResultImage(report.id, image.id)}><Trash2 className="size-3.5" />删除此图</Button>}
                </div>)}</div>}
                {report.editable && <label className={cn('mt-3 flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-[8px] border border-dashed text-xs', report.resultImages.length >= 9 ? 'cursor-not-allowed text-muted-foreground' : 'text-primary')}><input data-testid={`result-upload-${report.id}`} type="file" multiple accept="image/*" className="sr-only" disabled={report.resultImages.length >= 9} aria-label={`添加${report.service}战绩截图`} onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; uploadResultImages(report.id, files) }} /><ImagePlus className="size-4" />{report.resultImages.length >= 9 ? '已达到 9 张上限' : '添加战绩截图'}</label>}
              </section>
            </div>
          </details>
          {(report.editable || report.status === '审核中') && <div className="mt-4 flex flex-nowrap items-center justify-between gap-3 border-t pt-3">
            <p className="min-w-0 text-xs leading-5 text-muted-foreground">{report.status === '审核中' ? '审核通过前可撤回，凭证不会删除。' : canSubmit ? '三类凭证已齐全' : '待补齐：至少 1 张战绩截图'}</p>
            {report.editable && <Button size="sm" className="shrink-0 rounded-[6px]" disabled={!canSubmit} onClick={() => submitReport(report.id)}><FileCheck2 className="size-3.5" />提交审核</Button>}
            {report.status === '审核中' && <Button size="sm" variant="outline" className="shrink-0 rounded-[6px]" onClick={() => withdrawReport(report.id)}><Undo2 className="size-3.5" />撤回审核</Button>}
          </div>}
        </article>
      })}</div>
      <p className="mt-4 text-xs leading-5 text-muted-foreground">本机图片只在当前页面生成预览，不会上传；提交、撤回与审核状态均为原型示例。</p>
    </ProviderSection>}
    <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent className="rounded-[12px]"><ProviderDialogHeader icon={FileCheck2} title="从订单创建报单" description="仅展示当前可报的已完成订单，每笔订单只创建一份报单。" /><div className="space-y-2">{providerOrders.filter(order => order.status === 'COMPLETED').map(order => <button key={order.id} type="button" onClick={() => setSelectedOrder(order.id)} aria-pressed={selectedOrder === order.id} className={cn('flex min-h-16 w-full items-center gap-3 rounded-[8px] border p-3 text-left', selectedOrder === order.id && 'border-primary bg-primary/5')}><span className="min-w-0 flex-1"><span className="block text-sm font-medium">{order.service}</span><span className="mt-1 block text-xs text-muted-foreground">{order.orderNo}</span></span>{selectedOrder === order.id && <Check className="size-4 text-primary" />}</button>)}</div><DialogFooter><DialogClose asChild><Button variant="outline" className="h-11">取消</Button></DialogClose><Button className="h-11" disabled={!selectedOrder} onClick={create}>创建报单</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={preview !== null} onOpenChange={open => !open && setPreview(null)}><DialogContent className="sm:max-w-2xl"><ProviderDialogHeader icon={ImagePlus} title={preview?.alt ?? '服务凭证'} description="查看当前报单凭证；本机选择的图片仅在当前页面展示。" />{preview && <img src={preview.image.url} alt={preview.image.name} className="max-h-[62dvh] w-full rounded-[8px] border bg-muted object-contain" />}<DialogFooter><DialogClose asChild><Button variant="outline" className="h-11">关闭预览</Button></DialogClose></DialogFooter></DialogContent></Dialog>
  </ProviderPage></ProviderFrame>
}
