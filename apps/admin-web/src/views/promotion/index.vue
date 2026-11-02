<template>
  <div class="pm-page">
    <Card class="pm-header"><div><h2>促销活动</h2><p>核对活动覆盖的商品、报价规则和生效时间。</p></div><Button size="xs" v-if="auth.hasPerm('promotion:edit')" @click="onCreate">新增活动</Button></Card>
    <p v-if="pageNotice" role="status">{{ pageNotice }}</p>
    <SimplePagedList ref="tableRef" :fetcher="promotionApi.list" :query="query">
      <template #toolbar="{ reload }"><form class="pm-filters" @submit.prevent="reload">
        <label class="pm-search">搜索活动<Input v-model="query.keyword" placeholder="活动名称或备注" aria-label="搜索促销活动" /></label>
        <label>适用范围<NativeSelect v-model="query.scope" aria-label="适用范围" @update:model-value="reload"><NativeSelectOption value="">全部范围</NativeSelectOption><NativeSelectOption v-for="(item,key) in PROMOTION_SCOPE" :key="key" :value="key">{{ item.label }}</NativeSelectOption></NativeSelect></label>
        <label>启用状态<NativeSelect v-model="query.is_active" aria-label="启用状态" @update:model-value="reload"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption value="true">启用</NativeSelectOption><NativeSelectOption value="false">停用</NativeSelectOption></NativeSelect></label>
        <Button size="xs" type="submit">查询</Button><p class="pm-filter-note">时间状态为读取时快照。商品是否命中活动还取决于适用范围和优先级。</p>
      </form></template>
      <template #default="{ rows }"><div class="pm-records"><Table>
        <TableHeader><TableRow><TableHead>活动 / 优先级</TableHead><TableHead>适用范围</TableHead><TableHead>折扣与抽成</TableHead><TableHead>时间状态 / 起止时间</TableHead><TableHead v-if="auth.hasPerm('promotion:edit') || auth.hasPerm('promotion:delete')">操作</TableHead></TableRow></TableHeader>
        <TableBody><TableRow v-for="row in rows" :key="row.id" class="pm-card">
          <TableCell class="pm-name"><strong>{{ row.title }}</strong><small>#{{ row.id }} · 优先级 {{ row.priority }}</small><small v-if="row.remark" class="pm-remark">{{ row.remark }}</small></TableCell>
          <TableCell class="pm-scope-cell">
            <details v-if="row.scope === 'ITEMS' && row.item_names?.length"><summary>{{ scopeLabel(row) }}</summary><ul><li v-for="item in row.item_names" :key="item.id">{{ item.name }}</li></ul></details>
            <span v-else>{{ scopeLabel(row) }}</span>
          </TableCell>
          <TableCell class="pm-rates"><span>{{ row.discount_rate == null ? '不设置活动折扣' : '按原价 ' + row.discount_rate + '% 支付' }}</span>
            <small>{{ commissionConfigured(row) ? '覆盖抽成 · ' + commissionLabel(row) : '抽成沿用原规则' }}</small></TableCell>
          <TableCell class="pm-time"><Badge :variant="row.window_status === 'IN_WINDOW' ? 'secondary' : 'outline'">{{ windowLabel(row.window_status) }}</Badge><small>开始 {{ formatDateTime(row.start_at) }}</small><small>结束 {{ formatDateTime(row.end_at) }}</small></TableCell>
          <TableCell v-if="auth.hasPerm('promotion:edit') || auth.hasPerm('promotion:delete')" class="pm-commands"><div class="pm-actions"><Button size="xs" v-if="auth.hasPerm('promotion:edit')" variant="outline" @click="onEdit(row)">编辑</Button><Button size="xs" v-if="auth.hasPerm('promotion:delete')" variant="outline" @click="onDelete(row)">删除</Button></div></TableCell>
        </TableRow></TableBody>
      </Table></div></template>
    </SimplePagedList>
  </div>

  <Dialog :open="dialogVisible" @update:open="value => { if (!value) requestClose() }"><DialogContent :show-close-button="!saving" class="pm-edit-dialog" @interact-outside.prevent><DialogHeader><DialogTitle>{{ form.id ? '编辑促销活动' : '新增促销活动' }}</DialogTitle><DialogDescription>配置活动范围、折扣和生效时间。</DialogDescription></DialogHeader>
    <div class="pm-dialog-body"><Alert v-if="saveUncertain"><AlertTitle>保存结果尚未确认</AlertTitle><AlertDescription>请关闭后刷新活动列表，核对名称和配置；原请求可能仍在处理，请勿立即重复创建或保存。</AlertDescription></Alert>
    <div class="pm-form">
      <label>活动名称<Input v-model="form.title" placeholder="如：双十一限时折扣" :disabled="saving" /></label>
      <fieldset class="pm-full" :disabled="saving"><legend>适用范围</legend><RadioGroup v-model="form.scope" class="pm-scopes" aria-label="活动适用范围" :disabled="saving"><label v-for="(item,key) in PROMOTION_SCOPE" :key="key" :for="'promotion-scope-'+key"><RadioGroupItem :id="'promotion-scope-'+key" :value="key" :aria-label="item.label" />{{ item.label }}</label></RadioGroup></fieldset>
      <label v-if="form.scope === 'CATEGORY'">适用分类<NativeSelect :model-value="String(form.category || '')" :disabled="saving || loadingCategories" @update:model-value="value => form.category = value ? Number(value) : null"><NativeSelectOption value="">选择分类</NativeSelectOption><NativeSelectOption v-for="item in serviceCats" :key="item.id" :value="String(item.id)">{{ item.name }}</NativeSelectOption></NativeSelect><span v-if="categoryError" class="option-error">分类读取失败 <Button size="xs" variant="link" @click="loadCategories">重试</Button></span></label>
      <div v-if="form.scope === 'ITEMS'" class="pm-full"><label>搜索指定商品<Input v-model="serviceSearch" placeholder="按名称搜索" :disabled="saving || loadingServices" /></label><p v-if="loadingServices">正在读取商品…</p><p v-if="serviceError" class="option-error">商品读取失败 <Button size="xs" variant="link" @click="loadServices">重试</Button></p><div class="pm-options"><label v-for="item in filteredServices" :key="item.id" :for="`promotion-item-${item.id}`"><Checkbox :id="`promotion-item-${item.id}`" :model-value="form.items.includes(item.id)" :disabled="saving" @update:model-value="value => toggleItem(item.id, value === true)" />{{ item.name }}（{{ amountToXaCoin(item.price) }}币）</label></div><small>已选 {{ form.items.length }} 件商品</small></div>
      <div class="pm-field"><label for="promo-discount" class="pm-inline">活动折扣<Switch id="promo-discount" v-model="discountEnabled" aria-label="活动折扣" :disabled="saving" /></label><Input v-if="discountEnabled" aria-label="按原价支付的百分比" v-model.number="form.discount_rate" type="number" min="1" max="100" step="1" :disabled="saving" /><small>{{ discountEnabled ? '100 为原价，90 为九折；设置折扣后不可用优惠券' : '不设置活动折扣' }}</small></div>
      <div class="pm-field"><label for="promo-commission" class="pm-inline">覆盖抽成<Switch id="promo-commission" v-model="commissionEnabled" aria-label="覆盖抽成" :disabled="saving" /></label><small>{{ commissionEnabled ? '实际抽成以商品规则和派单试算为准' : '沿用原有抽成规则' }}</small></div>
      <div v-if="commissionEnabled" class="pm-full"><CommissionEditor :disabled="saving" v-model:type="commissionType" v-model:rate="form.commission_rate" v-model:fixed="commissionFixed" /></div>
      <Alert v-if="!discountEnabled && !commissionEnabled" class="pm-full"><AlertDescription>未设置折扣或抽成，仍可能按优先级挡住其他活动。</AlertDescription></Alert>
      <label>开始时间<Input v-model="startInput" type="datetime-local" step="1" :disabled="saving" /></label><label>结束时间<Input v-model="endInput" type="datetime-local" step="1" :disabled="saving" /></label>
      <div class="pm-field"><label for="promo-priority">优先级</label><Input id="promo-priority" v-model.number="form.priority" type="number" min="0" step="1" :disabled="saving" /><Popover><PopoverTrigger as-child><Button size="xs" variant="link" aria-label="查看活动优先级说明">规则说明</Button></PopoverTrigger><PopoverContent>同一商品同时命中多个活动时，优先级高者优先；相同则开始时间较晚者优先，再按 ID 较小者优先。只选一条，即使该活动未设置折扣和抽成也会占用优先位置；体验资格商品不参加活动。</PopoverContent></Popover></div>
      <label class="pm-full">备注<Textarea v-model="form.remark" :rows="2" :disabled="saving" /></label><label for="promo-active" class="pm-inline">启用<Switch id="promo-active" v-model="form.is_active" aria-label="启用" :disabled="saving" /></label>
    </div>
    <p v-if="formNotice" role="alert">{{ formNotice }}</p></div>
    <DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="requestClose">取消</Button><Button size="xs" :disabled="saving || saveUncertain" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter>
  </DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Card } from '@xa/shadcn-vue/card'
import { RadioGroup, RadioGroupItem } from '@xa/shadcn-vue/radio-group'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Switch } from '@xa/shadcn-vue/switch'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import CommissionEditor from '@/components/CommissionEditor.vue'
import { commissionConfigured, commissionError, commissionLabel, commissionPayload, inheritedCommission, readCommission, type CommissionType } from '@/utils/commission'
import { promotionApi, serviceItemApi, serviceCategoryApi } from '@/api/modules'
import { PROMOTION_SCOPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', scope: '', is_active: '' })

const pageNotice = ref(''), formNotice = ref(''), deleting = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})
const discountEnabled = ref(true)
const commissionEnabled = ref(false)
const commissionType = ref<CommissionType>('PERCENT')
const commissionFixed = ref<number | undefined>(0)
const serviceOptions = ref<any[]>([])
const serviceSearch = ref('')
const filteredServices = computed(() => serviceOptions.value.filter(item => item.name.toLowerCase().includes(serviceSearch.value.trim().toLowerCase()) || (form.items || []).includes(item.id)))
function toggleItem(id: number, checked: boolean) { const selected: number[] = form.items || []; form.items = checked ? [...new Set([...selected, id])] : selected.filter(item => item !== id) }
function localDateInput(value: string) { if (!value) return ''; const date = new Date(value); if (Number.isNaN(date.getTime())) return value.slice(0,19); const p = (n: number) => String(n).padStart(2, '0'); return `${date.getFullYear()}-${p(date.getMonth()+1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}` }
const startInput = computed({ get: () => localDateInput(form.start_at), set: (value: string) => { form.start_at = value } })
const endInput = computed({ get: () => localDateInput(form.end_at), set: (value: string) => { form.end_at = value } })
const serviceCats = ref<any[]>([])
const loadingCategories = ref(false), loadingServices = ref(false)
const categoryError = ref(false), serviceError = ref(false)
const categoriesLoaded = ref(false), servicesLoaded = ref(false)
const saveUncertain = ref(false)
let optionSequence = 0
let initialDraft = ''
function draft() { return JSON.stringify({ form, discountEnabled: discountEnabled.value, commissionEnabled: commissionEnabled.value, commissionType: commissionType.value, commissionFixed: commissionFixed.value }) }
async function mayClose() {
  if (saving.value) return false
  if (!initialDraft || initialDraft === draft() || saveUncertain.value) return true
  return await askConfirm({ title: '放弃修改？', description: '关闭后未保存的活动修改将丢失。', confirmText: '放弃修改', cancelText: '继续编辑' })
}
async function requestClose() { if (await mayClose()) dialogVisible.value = false }
onBeforeRouteLeave(async () => !dialogVisible.value || await mayClose())
const statusLabels: Record<string, string> = { DISABLED: '停用', SCHEDULED: '未开始', IN_WINDOW: '时间内', ENDED: '已结束' }
function windowLabel(status: string) { return statusLabels[status] || '状态待更新' }
function windowType(status: string) { return status === 'IN_WINDOW' ? 'success' : status === 'SCHEDULED' ? 'warning' : 'info' }
function scopeLabel(row: any) { return row.scope === 'CATEGORY' ? `指定分类 · ${row.category_name || '—'}` : row.scope === 'ITEMS' ? `指定商品 · ${row.item_names?.length || 0} 件` : row.scope_display }
async function loadCategories() {
  const seq = optionSequence, revision = auth.sessionRevision
  loadingCategories.value = true; categoryError.value = false
  try { const res = await serviceCategoryApi.allOptions(undefined, true) as any; if (seq === optionSequence && revision === auth.sessionRevision) { serviceCats.value = res.data?.list || []; categoriesLoaded.value = true } }
  catch { if (seq === optionSequence && revision === auth.sessionRevision) categoryError.value = true }
  finally { if (seq === optionSequence && revision === auth.sessionRevision) loadingCategories.value = false }
}
async function loadServices() {
  const seq = optionSequence, revision = auth.sessionRevision
  loadingServices.value = true; serviceError.value = false
  try { const res = await serviceItemApi.allOptions(undefined, true) as any; if (seq === optionSequence && revision === auth.sessionRevision) { serviceOptions.value = res.data?.list || []; servicesLoaded.value = true } }
  catch { if (seq === optionSequence && revision === auth.sessionRevision) serviceError.value = true }
  finally { if (seq === optionSequence && revision === auth.sessionRevision) loadingServices.value = false }
}
watch([dialogVisible, () => form.scope], ([visible, scope]) => {
  if (!visible) return
  if (scope === 'CATEGORY' && !categoriesLoaded.value && !loadingCategories.value) void loadCategories()
  if (scope === 'ITEMS' && !servicesLoaded.value && !loadingServices.value) void loadServices()
})
watch(() => auth.sessionRevision, () => {
  ++optionSequence; dialogVisible.value = false; saving.value = false; saveUncertain.value = false
  serviceCats.value = []; serviceOptions.value = []; categoriesLoaded.value = false; servicesLoaded.value = false
  loadingCategories.value = false; loadingServices.value = false; categoryError.value = false; serviceError.value = false
})

// 关闭开关时清空对应字段，避免提交残留值
watch(discountEnabled, (on) => { if (!on) form.discount_rate = null })
watch(commissionEnabled, (on) => { if (!on) form.commission_rate = null; else if (form.commission_rate == null) form.commission_rate = 0 })

function blank() {
  return {
    id: 0, title: '', scope: 'ALL', category: null, items: [],
    discount_rate: 90, commission_rate: null,
    start_at: '', end_at: '', priority: 0, remark: '', is_active: true,
  }
}

function onCreate() {
  formNotice.value = ''
  serviceSearch.value = ''
  saveUncertain.value = false
  Object.assign(form, blank())
  discountEnabled.value = true
  commissionEnabled.value = false
  commissionType.value = 'PERCENT'
  commissionFixed.value = 0
  dialogVisible.value = true
  void nextTick(() => { initialDraft = draft() })
}

function onEdit(row: any) {
  formNotice.value = ''
  serviceSearch.value = ''
  saveUncertain.value = false
  Object.assign(form, {
    ...row,
    items: (row.item_names || []).map((i: any) => i.id),
  })
  discountEnabled.value = row.discount_rate != null
  const commission = readCommission(row)
  commissionType.value = commission.type
  commissionFixed.value = commission.fixed
  form.commission_rate = commission.rate
  commissionEnabled.value = commissionConfigured(row)
  dialogVisible.value = true
  void nextTick(() => { initialDraft = draft() })
}

async function onSave() {
  if (saving.value || saveUncertain.value) return
  if (!form.title?.trim()) { formNotice.value = '请填写活动名称'; return }
  if (form.scope === 'CATEGORY' && !categoriesLoaded.value) { formNotice.value = '请先加载分类'; return }
  if (form.scope === 'ITEMS' && !servicesLoaded.value) { formNotice.value = '请先加载商品'; return }
  if (form.scope === 'CATEGORY' && !form.category) { formNotice.value = '请选择适用分类'; return }
  if (form.scope === 'ITEMS' && !form.items.length) { formNotice.value = '请选择指定商品'; return }
  if (discountEnabled.value && (!Number.isInteger(form.discount_rate) || form.discount_rate < 1 || form.discount_rate > 100)) { formNotice.value = '活动折扣须为 1 到 100 的整数'; return }
  if (!form.start_at || !form.end_at) { formNotice.value = '请选择起止时间'; return }
  if (!Number.isFinite(Date.parse(form.start_at)) || !Number.isFinite(Date.parse(form.end_at)) || Date.parse(form.end_at) <= Date.parse(form.start_at)) { formNotice.value = '结束时间必须晚于开始时间'; return }
  if (!Number.isInteger(form.priority) || form.priority < 0) { formNotice.value = '优先级须为非负整数'; return }
  if (commissionEnabled.value) {
    const error = commissionError(commissionType.value, form.commission_rate, commissionFixed.value)
    if (error) { formNotice.value = error; return }
  }
  formNotice.value = ''
  saving.value = true
  const revision = auth.sessionRevision
  try {
    const payload = {
      title: form.title.trim(),
      scope: form.scope,
      category: form.scope === 'CATEGORY' ? form.category : null,
      items: form.scope === 'ITEMS' ? form.items : [],
      discount_rate: discountEnabled.value ? form.discount_rate : null,
      ...(commissionEnabled.value ? commissionPayload(commissionType.value, form.commission_rate, commissionFixed.value) : inheritedCommission()),
      start_at: form.start_at,
      end_at: form.end_at,
      priority: form.priority,
      remark: form.remark,
      is_active: form.is_active,
    }
    if (form.id) {
      await promotionApi.update(form.id, payload, true)
    } else {
      await promotionApi.create(payload, true)
    }
    if (revision !== auth.sessionRevision) return
    pageNotice.value = '保存成功'
    dialogVisible.value = false
    tableRef.value.reload()
  } catch (error: any) {
    if (revision === auth.sessionRevision) {
      if (!error?.response || error.response.status >= 500) saveUncertain.value = true
      else formNotice.value = error.response.data?.msg || '保存失败，请检查输入后重试'
    }
  } finally {
    if (revision === auth.sessionRevision) saving.value = false
  }
}

async function onDelete(row: any) {
  if (deleting.value || !auth.hasPerm('promotion:delete')) return
  if (!await askConfirm({ title: '删除活动', description: `删除「${row.title}」后，历史订单将失去该活动关联；已保存的订单金额和抽成不会重新计算。若只需停止新订单使用，可编辑并停用。`, confirmText: '确认删除', cancelText: '取消' })) return
  deleting.value = true
  try { await promotionApi.remove(row.id, true); pageNotice.value = '已删除'; tableRef.value.reload() }
  catch (error: any) { pageNotice.value = !error?.response || error.response.status >= 500 ? '删除结果尚未确认，请刷新列表核对，原请求可能仍在处理' : error.response.data?.msg || '删除失败，请重试' }
  finally { deleting.value = false }
}
</script>

<style scoped>
.pm-page { display:grid; gap:12px; min-width:0; container:promotions / inline-size; }
.pm-header { display:flex; flex-flow:row wrap; align-items:center; justify-content:space-between; gap:8px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.pm-header h2 { font-size:16px; font-weight:600; } .pm-header p,.pm-filter-note,.pm-form small { color:var(--muted-foreground); font-size:12px; }
.pm-header p { margin-top:4px; }
.pm-filters { display:flex; flex-wrap:wrap; align-items:end; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.pm-filters label,.pm-form label,.pm-field { display:grid; gap:4px; min-width:0; font-size:12px; }
.pm-search { flex:1 1 180px; max-width:400px; } .pm-filter-note { flex-basis:100%; }
.pm-filters :deep(input),.pm-filters :deep(select),.pm-form :deep([data-slot='input']),.pm-form :deep(select) { width:100%; min-width:0; min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.pm-filters :deep(select),.pm-form :deep(select) { padding-right:32px; }
.pm-filters :deep([data-slot='native-select-wrapper']),.pm-form :deep([data-slot='native-select-wrapper']) { width:100%; }
.pm-records { min-width:0; background:var(--card); }
.pm-records :deep(th),.pm-records :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; vertical-align:top; }
.pm-records :deep(th) { font-size:12px; background:var(--muted); } .pm-records :deep(td) { font-size:13px; }
.pm-records small { display:block; margin-top:4px; font-size:12px; color:var(--muted-foreground); }
.pm-remark { white-space:pre-wrap; }
.pm-records summary { cursor:pointer; color:var(--primary); }
.pm-records details ul { padding-left:16px; list-style:disc; font-size:12px; }
.pm-actions,.pm-form .pm-scopes { display:flex; flex-wrap:wrap; gap:8px; }
:global(.pm-edit-dialog) { display:grid; grid-template-rows:auto minmax(0,1fr) auto; gap:12px; padding:16px; width:min(760px,calc(100vw - 24px)); max-width:calc(100vw - 24px); max-height:calc(100dvh - 24px); }
:global(.pm-edit-dialog [data-slot='dialog-title']) { padding-right:32px; }
.pm-dialog-body { min-height:0; overflow:auto; overscroll-behavior:contain; }
.pm-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-items:start; gap:12px; }
.pm-form fieldset { border:0; padding:0; min-width:0; } .pm-form legend { font-size:12px; margin-bottom:4px; }
.pm-full { grid-column:1/-1; }
.pm-options { display:grid; gap:4px; max-height:160px; margin:8px 0; overflow:auto; }
.pm-form .pm-inline,.pm-options label,.pm-scopes label { display:flex; align-items:center; gap:8px; padding:4px 8px; border:1px solid var(--border); border-radius:8px; cursor:pointer; }
.pm-form .pm-inline { justify-content:space-between; }
.pm-scopes label:has([aria-checked='true']) { border-color:var(--primary); background:var(--secondary); }
.option-error,.pm-dialog-body [role='alert'] { color:var(--destructive); font-size:12px; }
@container promotions (max-width:760px) {
  .pm-page :deep(.table-scroll),.pm-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .pm-records { background:transparent; } .pm-records :deep(table),.pm-records :deep(tbody) { display:grid; }
  .pm-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .pm-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .pm-records .pm-card { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; align-content:start; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
  .pm-records :deep(td) { padding:0; } .pm-name { grid-row:1; grid-column:1; } .pm-time { grid-row:1; grid-column:2; text-align:right; }
  .pm-rates,.pm-scope-cell,.pm-commands { grid-column:1/-1; }
  .pm-page :deep(.pages) { justify-content:flex-start; }
}
@container promotions (max-width:480px) { .pm-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } .pm-search { flex-basis:100%; max-width:none; } }
@media(max-width:560px) { .pm-form { grid-template-columns:minmax(0,1fr); } :global(.pm-edit-dialog) { padding:12px; gap:8px; } }
@media(pointer:coarse) {
  .pm-page :deep([data-slot='button']),:global(.pm-edit-dialog [data-slot='button']),.pm-records summary { min-height:44px; min-width:44px; }
  .pm-filters :deep(input),.pm-filters :deep(select),.pm-form :deep([data-slot='input']),.pm-form :deep(select),.pm-options label,.pm-scopes label,.pm-form .pm-inline { min-height:44px; }
  .pm-records summary { line-height:44px; }
}
</style>
