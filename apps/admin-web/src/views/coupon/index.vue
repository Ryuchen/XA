<template>
  <div class="cp-page">
    <Card class="cp-header">
      <div><h2>优惠券模板</h2><p>核对优惠条件、发放量和有效期。每位老板持有的券可在领取记录中查看。</p></div>
      <div class="ap-page-head-actions">
        <Button size="xs" v-if="auth.hasPerm('coupon:edit')" @click="onCreate">新增优惠券</Button>
      </div>
    </Card>
    <p v-if="pageNotice" role="status">{{ pageNotice }}</p>

    <SimplePagedList ref="tableRef" :fetcher="couponApi.list" :query="query">
      <template #toolbar="{ reload }"><label class="cp-filter">模板状态<NativeSelect v-model="query.is_active" aria-label="状态" @update:model-value="reload"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption value="true">启用</NativeSelectOption><NativeSelectOption value="false">停用</NativeSelectOption></NativeSelect></label></template>
      <template #default="{ rows }">
        <div class="coupon-records"><Table>
          <TableHeader><TableRow><TableHead>券名称 / 类型</TableHead><TableHead>优惠条件</TableHead><TableHead>发放与有效期</TableHead><TableHead>模板状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id" class="coupon-item">
            <TableCell class="cp-name"><strong>{{ row.name }}</strong><small>{{ row.discount_type_display }} · #{{ row.id }}</small></TableCell>
            <TableCell class="cp-value">
              <strong>{{ benefitLabel(row) }}</strong>
              <small v-if="row.discount_type === 'QUALIFICATION'">适用 {{ row.service_ids?.length || 0 }} 个指定套餐</small>
              <small v-else>门槛 {{ amountToXaCoin(row.threshold) }} 币</small>
              <small v-if="row.discount_type === 'RATE' && row.max_discount > 0">每张优惠上限 {{ formatRmb(row.max_discount) }} 元</small>
              <small v-if="row.experience_kind === 'DAILY_VALUE' || row.experience_kind === 'NEW_VALUE'">{{ row.experience_kind === 'DAILY_VALUE' ? '每日超值' : '新客超值' }} · {{ row.platform === 'PC' ? '端游' : row.platform === 'MOBILE' ? '手游' : '端手游共用' }}</small>
            </TableCell>
            <TableCell class="cp-quantity"><span>已领 {{ row.claimed_qty }} · 发放 {{ row.total_qty === 0 ? '不限' : row.total_qty }}</span><small>{{ validityLabel(row) }}</small></TableCell>
            <TableCell class="cp-status"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '模板启用' : '模板停用' }}</Badge></TableCell>
            <TableCell class="cp-actions"><div class="coupon-actions">
              <Button size="xs" variant="outline" @click="onClaims(row)">领取记录</Button>
              <Button size="xs" v-if="auth.hasPerm('coupon:edit')" variant="outline" @click="onEdit(row)">编辑</Button>
              <Button size="xs" v-if="auth.hasPerm('coupon:delete')" variant="outline" :disabled="row.claimed_qty > 0 || deleting" @click="onDelete(row)">删除</Button>
            </div></TableCell>
          </TableRow></TableBody>
        </Table></div>
      </template>
    </SimplePagedList>
  </div>

  <Dialog :open="dialogVisible" @update:open="value => { if (!saving) dialogVisible = value }"><DialogContent :show-close-button="!saving" class="cp-edit-dialog"><DialogHeader><DialogTitle>{{ form.id ? '编辑优惠券' : '新增优惠券' }}</DialogTitle><DialogDescription>配置优惠条件、发放数量和有效期。</DialogDescription></DialogHeader>
    <div class="cp-editor-body"><div class="cp-form">
      <label>券名称<Input v-model="form.name" :disabled="saving" /></label>
      <label>类型<NativeSelect v-model="form.discount_type" aria-label="优惠券类型" :disabled="saving"><NativeSelectOption v-for="(v, k) in COUPON_TYPE" :key="k" :value="k">{{ v.label }}</NativeSelectOption></NativeSelect></label>
      <label v-if="form.discount_type !== 'QUALIFICATION'">门槛（兴安币）<Input v-model.number="thresholdCoin" type="number" min="0" step="0.1" :disabled="saving" /></label>
      <label v-if="!['QUALIFICATION','RATE'].includes(form.discount_type)">面额（兴安币）<Input v-model.number="amountCoin" type="number" min="0.1" step="0.1" :disabled="saving" /></label>
      <label v-if="form.discount_type === 'RATE'">折扣率（%）<Input v-model.number="form.discount_rate" type="number" min="0" max="100" step="1" :disabled="saving" /><small>0 为赠送服务，100 为原价</small></label>
      <div v-if="form.discount_type === 'QUALIFICATION' || form.service_ids !== null" class="cp-services"><label>搜索适用套餐<Input v-model="serviceSearch" placeholder="按名称搜索" :disabled="saving || loadingServices" /></label><p v-if="loadingServices">正在读取套餐…</p><div class="cp-service-options"><label v-for="item in filteredServices" :key="item.id" :for="`coupon-service-${item.id}`"><Checkbox :id="`coupon-service-${item.id}`" :model-value="(form.service_ids || []).includes(item.id)" :disabled="saving" @update:model-value="value => toggleService(item.id, value === true)" />{{ item.name }}</label></div><small>已选 {{ (form.service_ids || []).length }} 个套餐</small></div>
      <p v-if="form.discount_type === 'QUALIFICATION'" class="cp-full">每张券仅能购买一份指定套餐，按套餐价格支付；核销后取消退款不返还资格。</p>
      <label v-if="form.discount_type === 'RATE'">每张优惠上限（元）<Input v-model.number="maxDiscountYuan" type="number" min="0" step="0.01" :disabled="saving" /><small>0 表示不另设上限</small></label>
      <label v-if="form.discount_type === 'QUALIFICATION'">体验资格<NativeSelect v-model="form.experience_kind" :disabled="saving"><NativeSelectOption value="">其他指定套餐资格</NativeSelectOption><NativeSelectOption value="NEW_VALUE">新客超值：每周期一次、永久有效</NativeSelectOption><NativeSelectOption value="DAILY_VALUE">每日超值：每个平台每日一次、当天有效</NativeSelectOption></NativeSelect></label>
      <label v-if="form.experience_kind">适用平台<NativeSelect v-model="form.platform" :disabled="saving"><NativeSelectOption value="">端游/手游共享新客次数</NativeSelectOption><NativeSelectOption value="PC">端游</NativeSelectOption><NativeSelectOption value="MOBILE">手游</NativeSelectOption></NativeSelect></label>
      <label>发放总量<Input v-model.number="form.total_qty" type="number" min="0" step="1" :disabled="saving" /><small>0 为不限</small></label>
      <label>模板有效期至（可留空）<Input v-model="validToInput" type="datetime-local" step="1" :disabled="saving" /></label>
      <label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" :disabled="saving" /></label>
      <label class="cp-switch" for="coupon-active">启用<Switch id="coupon-active" v-model="form.is_active" aria-label="启用" :disabled="saving" /></label>
    </div>
    <p v-if="formNotice" role="alert">{{ formNotice }}</p></div><DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">取消</Button><Button size="xs" :disabled="saving" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter>
  </DialogContent></Dialog>

  <Dialog v-model:open="claimsVisible"><DialogContent class="cp-claims-dialog"><DialogHeader><DialogTitle>领取记录 · {{ claimsCoupon?.name || '' }}</DialogTitle><DialogDescription>持券状态按读取时的有效期和账号周期计算。核销时还需满足订单的使用条件。</DialogDescription></DialogHeader>
    <div class="cp-claims-body"><Popover><PopoverTrigger as-child><Button size="xs" variant="link">状态说明</Button></PopoverTrigger><PopoverContent>同时满足多个条件时，依次优先显示已使用、已过期、模板停用和非当前周期。</PopoverContent></Popover>
    <Alert v-if="claimsIncompatible" variant="destructive"><AlertDescription>领取状态接口版本不兼容，请更新服务端后重试。</AlertDescription></Alert>
    <SimplePagedList v-if="claimsVisible && claimsCoupon" :key="claimsCoupon.id" :fetcher="fetchClaims" :query="claimsQuery"><template #toolbar="{ reload }"><NativeSelect v-model="claimsQuery.status" aria-label="领取状态" @update:model-value="reload"><NativeSelectOption value="">全部领取状态</NativeSelectOption><NativeSelectOption value="UNUSED">未使用</NativeSelectOption><NativeSelectOption value="USED">已使用</NativeSelectOption><NativeSelectOption value="EXPIRED">已过期</NativeSelectOption><NativeSelectOption value="DISABLED">模板停用</NativeSelectOption><NativeSelectOption value="PREVIOUS_CYCLE">非当前周期</NativeSelectOption></NativeSelect></template>
      <template #default="{ rows }"><div class="claim-records"><Table>
        <TableHeader><TableRow><TableHead>领取人</TableHead><TableHead>持券状态</TableHead><TableHead>领取 / 使用时间</TableHead><TableHead>有效期至</TableHead><TableHead>核销订单</TableHead></TableRow></TableHeader>
        <TableBody><TableRow v-for="row in rows" :key="row.id">
          <TableCell><strong>{{ row.user_name || '—' }}</strong><small>{{ row.username || '—' }}</small></TableCell>
          <TableCell class="holder-status"><Badge variant="outline">{{ row.status_display }}</Badge></TableCell>
          <TableCell class="holder-time"><span>领取 {{ formatDateTime(row.claimed_at) }}</span><small v-if="row.used_at">使用 {{ formatDateTime(row.used_at) }}</small></TableCell>
          <TableCell class="holder-expiry">{{ row.effective_valid_to ? formatDateTime(row.effective_valid_to) : '无期限' }}</TableCell>
          <TableCell class="holder-order"><router-link v-if="row.order_no && auth.hasPerm('order:view')" :to="{ path:'/orders', query:{ find_order:row.order_no } }">{{ row.order_no }}</router-link><span v-else>{{ row.order_no || '—' }}</span></TableCell>
        </TableRow></TableBody>
      </Table></div></template>
    </SimplePagedList></div>
  </DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { Switch } from '@xa/shadcn-vue/switch'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { couponApi, userCouponApi } from '@/api/modules'
import { COUPON_TYPE } from '@/utils/dict'
import { formatRmb } from '@xa/money'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const pageNotice = ref(''), formNotice = ref(''), deleting = ref(false)
const tableRef = ref()
const query = reactive<Record<string, any>>({ is_active: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const thresholdCoin = ref(0)
const amountCoin = ref(0)
const form = reactive<any>({})
const serviceOptions = ref<{ id: number; name: string }[]>([])
const loadingServices = ref(false), serviceSearch = ref('')
const filteredServices = computed(() => serviceOptions.value.filter(item => item.name.toLowerCase().includes(serviceSearch.value.trim().toLowerCase()) || (form.service_ids || []).includes(item.id)))
function toggleService(id: number, checked: boolean) { const selected: number[] = form.service_ids || []; form.service_ids = checked ? [...new Set([...selected, id])] : selected.filter(item => item !== id) }
const maxDiscountYuan = computed({ get: () => (form.max_discount || 0) / 100, set: (value: number | string) => { form.max_discount = value === '' ? 0 : Math.round(Number(value) * 100) } })
const validToInput = computed({ get: () => { if (!form.valid_to) return ''; const date = new Date(form.valid_to); if (Number.isNaN(date.getTime())) return String(form.valid_to).slice(0, 19); const pad = (n: number) => String(n).padStart(2, '0'); return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` }, set: (value: string) => { form.valid_to = value || null } })
watch([dialogVisible, () => form.discount_type], async ([visible, kind]) => {
  if (!visible) return
  if (kind === 'QUALIFICATION' && form.service_ids === null) form.service_ids = []
  if (kind !== 'QUALIFICATION' && form.service_ids === null) return
  loadingServices.value = true
  try { serviceOptions.value = (await couponApi.serviceOptions()).data }
  catch { formNotice.value = '套餐加载失败，请关闭后重试' }
  finally { loadingServices.value = false }
})
const claimsVisible = ref(false)
const claimsCoupon = ref<any>(null)
const claimsQuery = reactive({ coupon: 0, status: '' })
const claimsIncompatible = ref(false)
let claimsSequence = 0

async function fetchClaims(params: Record<string, any>) {
  const sequence = claimsSequence, revision = auth.sessionRevision
  const result = await userCouponApi.list(params)
  if (sequence !== claimsSequence || revision !== auth.sessionRevision || !claimsVisible.value || claimsCoupon.value?.id !== params.coupon) throw new Error('领取记录已切换')
  if ((result.data as any).claim_status_version !== 1) { claimsIncompatible.value = true; throw new Error('领取状态接口版本不兼容') }
  claimsIncompatible.value = false
  return result
}

function onClaims(row: any) {
  ++claimsSequence
  claimsIncompatible.value = false
  claimsCoupon.value = row
  claimsQuery.coupon = row.id
  claimsQuery.status = ''
  claimsVisible.value = true
}
watch(claimsVisible, (visible) => { if (!visible) { ++claimsSequence; claimsIncompatible.value = false } }, { flush: 'sync' })
watch(() => auth.sessionRevision, () => { ++claimsSequence; claimsVisible.value = false; claimsCoupon.value = null; claimsIncompatible.value = false }, { flush: 'sync' })

function benefitLabel(row: any) {
  if (row.discount_type === 'QUALIFICATION') return '按套餐价购买 1 份'
  if (row.discount_type === 'RATE') return row.discount_rate === 0 ? '赠送服务' : `按原价 ${row.discount_rate}% 支付`
  return `减 ${amountToXaCoin(row.amount)} 币`
}
function validityLabel(row: any) {
  if (row.valid_to) return '截止 ' + formatDateTime(row.valid_to)
  return row.experience_kind === 'DAILY_VALUE' ? '领取当日有效' : '无固定截止时间'
}
function blank() {
  return {
    id: 0, name: '', discount_type: 'THRESHOLD', total_qty: 0, claimed_qty: 0,
    valid_to: null, max_discount: 0, experience_kind: '', platform: '', sort_order: 0, is_active: true, discount_rate: 100, service_ids: null,
    scope: 'ALL', scope_variant: '', limit_units: 0, is_public: true,
  }
}

function onCreate() {
  formNotice.value = ''
  serviceSearch.value = ''
  Object.assign(form, blank())
  thresholdCoin.value = 0
  amountCoin.value = 0
  dialogVisible.value = true
}

function onEdit(row: any) {
  formNotice.value = ''
  serviceSearch.value = ''
  Object.assign(form, blank(), row, { service_ids: row.service_ids === null ? null : [...(row.service_ids || [])] })
  thresholdCoin.value = Number(amountToXaCoin(row.threshold))
  amountCoin.value = Number(amountToXaCoin(row.amount))
  dialogVisible.value = true
}

async function onSave() {
  if (saving.value || !auth.hasPerm('coupon:edit')) return
  if (!form.name) { formNotice.value = '请填写券名称'; return }
  const qualification = form.discount_type === 'QUALIFICATION'
  if (qualification && !form.service_ids?.length) { formNotice.value = '请选择资格券适用套餐'; return }
  const validCoin = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 && Math.abs(value * 10 - Math.round(value * 10)) < 1e-8
  if (!qualification && !validCoin(thresholdCoin.value)) { formNotice.value = '门槛须不小于零且最多一位小数'; return }
  if (!qualification && form.discount_type !== 'RATE' && !validCoin(amountCoin.value)) { formNotice.value = '面额须不小于零且最多一位小数'; return }
  if (form.discount_type === 'RATE' && (typeof form.discount_rate !== 'number' || !Number.isFinite(form.discount_rate) || form.discount_rate < 0 || form.discount_rate > 100 || !Number.isFinite(form.max_discount) || form.max_discount < 0)) { formNotice.value = '请检查折扣率及优惠上限'; return }
  if (!Number.isInteger(form.total_qty) || form.total_qty < 0 || !Number.isInteger(form.sort_order) || form.sort_order < 0) { formNotice.value = '发放总量和排序须为非负整数'; return }
  if (!qualification && form.discount_type !== 'RATE' && !(amountCoin.value > 0)) { formNotice.value = '面额必须大于零'; return }
  if (form.total_qty > 0 && form.total_qty < (form.claimed_qty || 0)) { formNotice.value = '发放总量不能少于已领取数量'; return }
  formNotice.value = ''
  saving.value = true
  try {
    const payload = {
      name: form.name,
      discount_type: form.discount_type,
      threshold: qualification ? 0 : xaCoinToAmount(thresholdCoin.value),
      amount: qualification ? 0 : form.discount_type === 'RATE' ? Math.max(1, xaCoinToAmount(amountCoin.value)) : xaCoinToAmount(amountCoin.value),
      discount_rate: qualification ? 100 : form.discount_rate,
      service_ids: form.service_ids,
      limit_units: qualification ? 1 : form.limit_units,
      scope: form.scope, scope_variant: form.scope_variant, is_public: form.is_public,
      total_qty: form.total_qty,
      valid_to: form.valid_to || null, max_discount: form.max_discount || 0, experience_kind: qualification ? form.experience_kind : '', platform: qualification ? form.platform : '',
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await couponApi.update(form.id, payload)
    } else {
      await couponApi.create(payload)
    }
    pageNotice.value = '保存成功'
    dialogVisible.value = false
    tableRef.value.reload()
  } catch (error) { formNotice.value = error instanceof Error ? error.message : '保存失败，请重试' }
  finally { saving.value = false }
}

async function onDelete(row: any) {
  if (deleting.value || row.claimed_qty > 0 || !auth.hasPerm('coupon:delete')) return
  if (!await askConfirm({ title: '删除优惠券', description: `确定删除优惠券「${row.name}」吗？`, confirmText: '删除', cancelText: '取消' })) return
  deleting.value = true
  try { await couponApi.remove(row.id); pageNotice.value = '已删除'; tableRef.value.reload() }
  catch (error) { pageNotice.value = error instanceof Error ? error.message : '删除失败，请重试' }
  finally { deleting.value = false }
}
</script>

<style scoped>
.cp-page { display:grid; min-width:0; gap:12px; container:coupons / inline-size; }
.cp-header { display:flex; flex-flow:row wrap; align-items:center; justify-content:space-between; gap:8px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.cp-header h2 { font-size:16px; font-weight:600; }
.cp-header p { font-size:12px; color:var(--muted-foreground); margin-top:4px; }
.cp-filter,.cp-form label { display:grid; gap:4px; align-content:start; min-width:0; font-size:12px; }
.cp-filter { justify-self:start; }
.coupon-records,.claim-records { min-width:0; background:var(--card); }
.coupon-records :deep(th),.claim-records :deep(th) { background:var(--muted); font-size:12px; }
.coupon-records :deep(td),.claim-records :deep(td) { white-space:normal; overflow-wrap:anywhere; font-size:13px; vertical-align:top; }
.coupon-records :deep(td),.coupon-records :deep(th),.claim-records :deep(td),.claim-records :deep(th) { padding:8px; }
.coupon-records small,.claim-records small,.cp-form small { display:block; margin-top:4px; font-size:12px; color:var(--muted-foreground); }
.coupon-actions { display:flex; flex-wrap:wrap; gap:4px; }
:global(.cp-edit-dialog),:global(.cp-claims-dialog) { display:grid; gap:12px; padding:16px; width:min(760px,calc(100vw - 24px)); max-width:calc(100vw - 24px); max-height:calc(100dvh - 24px); }
:global(.cp-edit-dialog) { grid-template-rows:auto minmax(0,1fr) auto; }
:global(.cp-claims-dialog) { grid-template-rows:auto minmax(0,1fr); }
:global(.cp-edit-dialog [data-slot='dialog-title']),:global(.cp-claims-dialog [data-slot='dialog-title']) { padding-right:32px; }
.cp-editor-body,.cp-claims-body { min-height:0; overflow:auto; overscroll-behavior:contain; }
.cp-editor-body>p { margin-top:8px; color:var(--destructive); font-size:13px; }
.cp-claims-body { container:claims / inline-size; display:grid; gap:8px; align-content:start; }
.cp-claims-body>button { justify-self:start; }
.cp-form { display:grid; align-items:start; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.cp-form :deep([data-slot='input']),.cp-form :deep(select),.cp-page :deep(select),.cp-claims-body :deep(select) { min-height:28px; height:28px; font-size:12px; padding-block:4px; }
.cp-form :deep([data-slot='native-select-wrapper']) { width:100%; }
.cp-form :deep(select),.cp-page :deep(select),.cp-claims-body :deep(select) { padding-right:32px; }
.cp-form .cp-switch,.cp-service-options label { display:flex; align-items:center; gap:8px; min-width:0; padding:4px 8px; border:1px solid var(--border); border-radius:8px; cursor:pointer; }
.cp-form .cp-switch { justify-content:space-between; align-self:end; }
.cp-services,.cp-full { grid-column:1/-1; font-size:12px; }
.cp-service-options { display:grid; gap:4px; max-height:160px; overflow:auto; margin:8px 0; }
.holder-order a { color:var(--primary); text-decoration:underline; }
@container coupons (max-width:820px) {
  .cp-page :deep(.table-scroll),.coupon-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .coupon-records { background:transparent; }
  .coupon-records :deep(table),.coupon-records :deep(tbody) { display:grid; }
  .coupon-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .coupon-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .coupon-records .coupon-item { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-content:start; padding:12px; background:var(--card); border:1px solid var(--border); border-radius:8px; }
  .coupon-records :deep(td) { padding:0; }
  .cp-status { grid-column:2; grid-row:1; text-align:right; }
  .cp-value,.cp-quantity,.cp-actions { grid-column:1/-1; }
  .cp-page :deep(.pages) { justify-content:flex-start; }
}
@container coupons (max-width:480px) { .coupon-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@container claims (max-width:600px) {
  .cp-claims-body :deep(.table-scroll),.claim-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .claim-records :deep(table),.claim-records :deep(tbody) { display:grid; }
  .claim-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .claim-records :deep(tbody) { gap:8px; }
  .claim-records :deep(tr) { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
  .claim-records :deep(td) { padding:0; } .holder-status { grid-column:2; grid-row:1; }
  .holder-time,.holder-expiry,.holder-order { grid-column:1/-1; }
  .holder-expiry::before { content:'有效期至 '; } .holder-order::before { content:'核销订单 '; } .holder-expiry::before,.holder-order::before { color:var(--muted-foreground); font-size:12px; }
}
@media(max-width:560px) { .cp-form { grid-template-columns:minmax(0,1fr); } :global(.cp-edit-dialog),:global(.cp-claims-dialog) { padding:12px; } }
@media(pointer:coarse) {
  .cp-page :deep([data-slot='button']),:global(.cp-edit-dialog [data-slot='button']),:global(.cp-claims-dialog [data-slot='button']),.holder-order a { min-width:44px; min-height:44px; }
  .cp-form :deep([data-slot='input']),.cp-form :deep(select),.cp-page :deep(select),.cp-claims-body :deep(select),.cp-switch,.cp-service-options label { min-height:44px; }
  .holder-order a { display:inline-flex; align-items:center; }
}
</style>
