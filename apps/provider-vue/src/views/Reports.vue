<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { formatXaCoin } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import PrivateImage from '../components/PrivateImage.vue'
import { api } from '../lib/session'
import { coalesced, onRealtime } from '../lib/realtime'

type Order = { id:number; order_no:string; status:string; service_name_snapshot?:string; service?:{name?:string;is_gift?:boolean}; source_order?:unknown; is_gift_snapshot?:boolean; report_entry?:{available?:boolean} }
type Refund = { amount:number; remaining_amount?:number | null; recovered_amount?:number | null; net_income?:number | null; referral_recovered_amount?:number | null; referral_net_income?:number | null; remaining_frozen_amount?:number | null; income_breakdown_unavailable?:boolean }
type Report = { id:number; order_id:number; order_no:string; game_name:string; service_name?:string; fulfillment?:string; expected_income:number | null; submitted_at:string | null; settled_at:string | null; settlement_status?:string; order_status:string; payment_status?:string; aftersale_status?:string; refund_summary?:Refund | null; status:string; status_display:string; entry_image_url:string; completion_image_url:string; result_image_urls:string[]; result_images:{id:number;url:string}[]; audit_remark:string; can_edit:boolean; can_submit:boolean; edit_block_reason:string; evidence_upload_block_reasons?:Partial<Record<'ENTRY'|'COMPLETION',string>> }
const route = useRoute(), router = useRouter(), reports = ref<Report[]>([]), loading = ref(true), busy = ref(false), error = ref(''), notice = ref(''), noticeReportId = ref<number | null>(null)
function routeOrder(value: unknown) { const id = Number(value); return Number.isSafeInteger(id) && id > 0 ? id : null }
const choices = ref<Order[] | null>(null), search = ref(''), selected = ref<number | null>(null), focus = ref(routeOrder(route.query.order))
let sequence = 0, navigationRevision = 0, alive = true
const dialogOpen = computed({ get: () => choices.value !== null, set: value => { if (!value && !busy.value) choices.value = null } })
const shown = computed(() => focus.value ? reports.value.filter(row => row.order_id === focus.value) : reports.value)
const filtered = computed(() => (choices.value || []).filter(row => `${row.order_no} ${row.service_name_snapshot || row.service?.name || ''}`.toLowerCase().includes(search.value.trim().toLowerCase())))
function missing(row:Report) { return [(!row.entry_image_url || row.evidence_upload_block_reasons?.ENTRY) && '入队截图', (!row.completion_image_url || row.evidence_upload_block_reasons?.COMPLETION) && '结单截图', !row.result_image_urls.length && '战绩截图'].filter(Boolean) }
function incomeLabel(row:Report) {
  if (row.payment_status !== 'PARTIALLY_REFUNDED' && row.order_status !== 'CANCELLED') return '打手单钱'
  if (row.status === 'APPROVED') return '原核定单钱'
  return row.settled_at ? '原订单分账' : '原预计单钱'
}
async function load() {
  const current = ++sequence; loading.value = true
  try { const data = await api<Report[]>('/wallet/reports/'); if (alive && current === sequence) { reports.value = data; error.value = '' } }
  catch (cause) { if (alive && current === sequence) error.value = cause instanceof Error ? cause.message : '报单加载失败' }
  finally { if (alive && current === sequence) loading.value = false }
}
async function begin() {
  if (busy.value) return
  const revision = navigationRevision
  busy.value = true; notice.value = ''; noticeReportId.value = null
  try {
    const data = await api<Order[]>('/orders/orders/?role=provider')
    if (!alive || navigationRevision !== revision) return
    choices.value = data.filter(row => ['GRABBED','IN_SERVICE','COMPLETED'].includes(row.status) && !(row.source_order || (row.is_gift_snapshot ?? row.service?.is_gift)) && row.report_entry?.available !== false && !reports.value.some(report => report.order_id === row.id))
    search.value = ''; selected.value = null
  } catch (cause) { if (alive && navigationRevision === revision) notice.value = cause instanceof Error ? cause.message : '订单加载失败' }
  finally { if (alive) busy.value = false }
}
async function create() {
  const id = selected.value
  if (!id || busy.value || !choices.value?.some(row => row.id === id)) return
  const revision = navigationRevision
  busy.value = true; notice.value = ''; noticeReportId.value = null
  try {
    await api('/wallet/reports/', {method:'POST',data:{order_id:id}})
    if (!alive || navigationRevision !== revision) return
    choices.value = null; await router.push({path:'/reports',query:{order:id}}); notice.value = '报单已创建'
  } catch (cause) {
    if (!alive || navigationRevision !== revision) return
    const failure = cause as {status?:number;message?:string}
    if (!failure.status || failure.status >= 500) { choices.value = null; await router.push({path:'/reports',query:{order:id}}); notice.value = '创建结果未确认，请核对报单' }
    else notice.value = failure.message || '报单创建失败'
  } finally { if (alive) { await load(); busy.value = false } }
}
async function upload(row:Report, kind:'ENTRY'|'COMPLETION'|'RESULT', event:Event) {
  const element = event.target as HTMLInputElement, files = Array.from(element.files || []); element.value = ''
  if (!row.can_edit || busy.value || !files.length) return
  const revision = navigationRevision
  const allowed = kind === 'RESULT' ? Math.max(0, 9 - row.result_image_urls.length) : 1
  busy.value = true; notice.value = ''; noticeReportId.value = row.id
  let uploaded = 0
  try {
    for (const file of files.slice(0, allowed)) { const data = new FormData(); data.append('kind',kind); data.append('image',file); if (kind === 'RESULT') { const bytes = crypto.getRandomValues(new Uint8Array(16)); data.append('client_request_id', `report-image-${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}`) } await api(`/wallet/reports/${row.id}/images/`, {method:'POST',data}); uploaded++ }
    if (alive && navigationRevision === revision) notice.value = kind === 'RESULT' && files.length > allowed ? `已上传 ${uploaded} 张，另有 ${files.length - allowed} 张超出上限未上传。` : '截图已上传'
  } catch (cause) {
    if (!alive || navigationRevision !== revision) return
    const failure = cause as {status?:number;code?:number;message?:string}
    const rejected = failure.status && failure.status < 500 && (!failure.code || failure.code < 500)
    if (kind !== 'RESULT') notice.value = rejected ? failure.message || '上传失败' : '上传结果未确认，请核对截图'
    else if (rejected) notice.value = `已上传 ${uploaded} 张；${failure.message || '上传失败'}${uploaded + 1 < files.length ? '，后续图片未上传' : ''}。`
    else notice.value = `已确认上传 ${uploaded} 张；当前图片上传结果未确认${uploaded + 1 < files.length ? '，后续图片未上传' : ''}，请核对截图。`
  }
  finally { if (alive) { await load(); busy.value = false } }
}
async function operate(row:Report, action:'submit'|'delete', imageId?:number) {
  if (busy.value || (action === 'submit' && (!row.can_submit || !!missing(row).length)) || (action === 'delete' && !row.can_edit)) return
  const revision = navigationRevision
  busy.value = true; notice.value = ''; noticeReportId.value = null
  try { await api(action === 'submit' ? `/wallet/reports/${row.id}/submit/` : `/wallet/reports/${row.id}/images/${imageId}/`, {method:action === 'submit' ? 'POST' : 'DELETE'}); if (alive && navigationRevision === revision) notice.value = action === 'submit' ? '已提交审核' : '截图已删除' }
  catch (cause) { if (alive && navigationRevision === revision) { const failure = cause as {status?:number;message?:string}; notice.value = failure.status && failure.status < 500 ? failure.message || '操作失败' : '操作结果未确认，请核对报单状态' } }
  finally { if (alive) { await load(); busy.value = false } }
}
const refresh = coalesced(load), update = () => { if (!document.hidden) refresh.request() }
const offReport = onRealtime('report_status_update', update), offOrder = onRealtime('order_status_update', update), offConnect = onRealtime('connected', update)
watch(() => route.query.order, value => { navigationRevision++; focus.value = routeOrder(value); choices.value = null; selected.value = null; notice.value = ''; noticeReportId.value = null }, {flush:'sync'})
onMounted(() => { void load(); document.addEventListener('visibilitychange', update) })
onUnmounted(() => { alive = false; sequence++; refresh.dispose(); offReport(); offOrder(); offConnect(); document.removeEventListener('visibilitychange', update) })
</script>

<template>
  <main class="page stack">
    <header class="row"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>服务报单</h1></div><div class="actions"><Button variant="outline" @click="router.push('/orders')">返回订单</Button><Button :disabled="busy || loading" @click="begin">从订单创建</Button></div></header>
    <div v-if="focus" class="row"><p class="muted">正在查看关联订单的报单</p><Button variant="outline" @click="router.push('/reports')">查看全部报单</Button></div><p v-if="notice && noticeReportId === null" role="status" class="notice">{{ notice }}</p><p v-if="error" role="alert" class="error">{{ error }} <Button variant="outline" @click="load">重试</Button></p><p v-if="loading" class="muted">正在加载报单…</p>
    <Card v-if="!loading && !shown.length && !error" class="panel"><strong>{{ focus ? '未找到关联报单' : '暂无报单' }}</strong><p class="muted">{{ focus ? '可查看全部报单或刷新后重试。' : '接单后可创建报单，服务截图会自动同步。' }}</p></Card>
    <Card v-for="report in shown" :key="report.id" class="panel report">
      <div class="row"><div><strong>{{ report.service_name || report.game_name }}</strong><p class="muted">订单 {{ report.order_no }}</p></div><strong>{{ report.status_display }}</strong></div>
      <p v-if="report.aftersale_status === 'OPEN'" class="notice">售后处理中，收益状态以钱包流水为准。</p><p v-else-if="report.aftersale_status === 'RESOLVED'" class="notice">售后已处理，收益状态以本单结算信息为准。</p>
      <p v-if="report.fulfillment">履约目标：{{ report.fulfillment }}</p><div class="row"><span class="muted">{{ incomeLabel(report) }}</span><strong>{{ report.expected_income == null ? '待核对' : `${formatXaCoin(report.expected_income)} 兴安币` }}</strong></div>
      <p v-if="report.submitted_at" class="muted">提交时间：{{ new Date(report.submitted_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) }}（北京时间）</p><p v-if="report.order_status === 'CANCELLED'" class="notice">订单已取消，报单流程已停止。</p><p v-if="report.edit_block_reason && ['DRAFT','REJECTED'].includes(report.status)" class="notice">{{ report.edit_block_reason }}</p><p v-if="report.audit_remark" class="notice">审核备注：{{ report.audit_remark }}</p>
      <p v-if="report.status === 'APPROVED'" class="notice">{{ report.payment_status === 'PARTIALLY_REFUNDED' ? '订单已部分退款，净收益以钱包流水为准。' : report.order_status === 'CANCELLED' ? '订单已取消或退款，收益以钱包流水为准。' : report.expected_income == null ? '凭证已通过，收益待核对。' : report.settlement_status === 'SETTLED' ? '收益已结算，冻结及可用金额以钱包为准。' : report.settlement_status === 'WAITING' ? '凭证已通过，等待全部审核及售后窗口结束。' : '凭证已通过，收益待核对。' }}</p>
      <div v-if="report.refund_summary" class="notice"><strong>退款核对</strong><p>累计已退 {{ formatXaCoin(report.refund_summary.amount) }} 币<span v-if="report.refund_summary.remaining_amount != null"> · 剩余 {{ formatXaCoin(report.refund_summary.remaining_amount) }} 币</span></p><p v-if="report.refund_summary.recovered_amount != null">服务收益累计追回 {{ formatXaCoin(report.refund_summary.recovered_amount) }} 币</p><p v-if="report.refund_summary.net_income != null">服务净收益 {{ formatXaCoin(report.refund_summary.net_income) }} 币</p><p v-if="report.refund_summary.referral_recovered_amount">推荐分佣追回 {{ formatXaCoin(report.refund_summary.referral_recovered_amount) }} 币</p><p v-if="report.refund_summary.referral_net_income">推荐分佣净额 {{ formatXaCoin(report.refund_summary.referral_net_income) }} 币</p><p v-if="report.refund_summary.remaining_frozen_amount != null">本单剩余冻结 {{ formatXaCoin(report.refund_summary.remaining_frozen_amount) }} 币</p><p v-if="report.refund_summary.income_breakdown_unavailable">收益明细待核对，请查看钱包流水或联系客服。</p></div>
      <div class="section"><div class="row"><strong>服务凭证</strong><span class="muted">{{ 3 - missing(report).length }}/3 类已就绪</span></div><div class="evidence"><div v-for="item in [{kind:'ENTRY' as const, title:'入队截图', url:report.entry_image_url}, {kind:'COMPLETION' as const, title:'结单截图', url:report.completion_image_url}]" :key="item.kind" class="evidence-item"><strong>{{ item.title }}</strong><PrivateImage v-if="item.url" :src="item.url" :alt="item.title" /><p v-else class="muted">暂无截图</p><template v-if="report.can_edit"><template v-if="report.evidence_upload_block_reasons?.[item.kind]"><p class="muted">{{ report.evidence_upload_block_reasons[item.kind] }}</p><Button variant="outline" :disabled="busy" @click="router.push({path:'/orders',query:{order:report.order_id}})">前往订单</Button></template><label v-else class="file-button">{{ item.url ? '更换截图' : '上传截图' }}<input type="file" accept="image/*" :disabled="busy" @change="upload(report,item.kind,$event)" /></label></template></div></div></div>
      <div class="section"><div class="row"><strong>战绩截图</strong><span class="muted">{{ report.result_image_urls.length }}/9 张</span></div><div class="evidence"><div v-for="(picture,index) in report.result_images" :key="picture.id" class="evidence-item"><PrivateImage :src="picture.url" :alt="`战绩截图 ${index + 1}`" /><Button v-if="report.can_edit" variant="outline" :disabled="busy" @click="operate(report,'delete',picture.id)">删除此图</Button></div></div><p v-if="!report.result_image_urls.length" class="muted">{{ report.can_edit ? '至少上传一张战绩截图' : '暂无战绩截图' }}</p></div>
      <div v-if="report.can_edit" class="row"><label class="file-button">添加战绩截图<input type="file" accept="image/*" multiple :disabled="busy || report.result_image_urls.length >= 9" @change="upload(report,'RESULT',$event)" /></label><Button :disabled="busy || !report.can_submit || !!missing(report).length" @click="operate(report,'submit')">{{ !report.can_submit ? '服务完成后提交' : '提交审核' }}</Button></div><p v-if="notice && noticeReportId === report.id" role="status" class="notice">{{ notice }}</p><p v-if="report.can_edit" class="muted">{{ missing(report).length ? `待补齐：${missing(report).join('、')}` : '三类凭证已齐全' }}</p>
    </Card>
    <Dialog v-model:open="dialogOpen"><DialogContent :show-close-button="!busy"><DialogHeader><DialogTitle>选择报单订单</DialogTitle><DialogDescription>请选择需要补充服务凭证的订单，已有报单可继续编辑。</DialogDescription></DialogHeader><Input v-model="search" placeholder="搜索订单号或服务名称" /><p class="muted">{{ filtered.length }} 笔可选订单</p><div class="choices"><button v-for="order in filtered" :key="order.id" class="choice" :aria-pressed="selected === order.id" :disabled="busy" @click="selected = order.id; notice = ''"><strong>{{ order.service_name_snapshot || order.service?.name || '陪玩服务' }}</strong><span class="muted">{{ order.order_no }}{{ selected === order.id ? ' · 已选择' : '' }}</span></button><p v-if="!filtered.length" class="muted">暂无匹配订单</p></div><p v-if="notice" role="alert" class="notice">{{ notice }}</p><DialogFooter><Button variant="outline" :disabled="busy" @click="choices = null">取消</Button><Button :disabled="busy || !selected" @click="create">{{ busy ? '创建中…' : '创建报单' }}</Button></DialogFooter></DialogContent></Dialog>
  </main>
</template>
<style scoped>
h1 { margin:4px 0 0; font-size:24px; } p { margin:0; overflow-wrap:anywhere; } .report { gap:12px; } .actions,.evidence { display:flex; flex-wrap:wrap; gap:8px; } .section { border-top:1px solid var(--border); padding-top:12px; display:grid; gap:8px; } .evidence-item { display:grid; gap:8px; min-width:120px; } .evidence-item :deep(img) { width:96px; height:96px; object-fit:cover; border-radius:8px; } .file-button { display:inline-flex; align-items:center; min-height:36px; padding:4px 12px; border:1px solid var(--border); border-radius:8px; font-size:13px; cursor:pointer; position:relative; overflow:hidden; } .file-button input { position:absolute; inset:0; opacity:0; width:100%; cursor:pointer; } .file-button:has(input:disabled) { opacity:.5; } .choices { display:grid; gap:8px; max-height:40vh; overflow:auto; } .choice { display:grid; text-align:left; gap:4px; padding:12px; background:var(--card); border:1px solid var(--border); border-radius:8px; } .choice[aria-pressed=true] { border-color:var(--primary); background:var(--secondary); } .notice { padding:8px 12px; border-radius:8px; color:#1e3a8a; background:#eff6ff; }
</style>
