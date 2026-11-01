<template>
  <div class="pm-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理活动范围、折扣与生效时间</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('promotion:edit')" type="primary" @click="onCreate">新增活动</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="promotionApi.list" :query="query">
      <template #toolbar="{ reload }">
        <span class="pm-filter-note">时间状态为读取时快照；时间内不代表一定命中商品。</span>
        <el-input
          v-model="query.keyword"
          placeholder="活动名称/备注"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.scope" placeholder="适用范围" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in PROMOTION_SCOPE" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.is_active" placeholder="启用状态" aria-label="启用状态" clearable style="width: 120px" @change="reload">
          <el-option label="启用" value="true" />
          <el-option label="停用" value="false" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="活动名称" min-width="150" />
      <el-table-column label="适用范围" width="120">
        <template #default="{ row }">
          <el-tag :type="PROMOTION_SCOPE[row.scope]?.type" effect="light" round>{{ row.scope_display }}</el-tag>
          <span v-if="row.scope === 'CATEGORY'" class="sub">{{ row.category_name || '—' }}</span>
          <span v-else-if="row.scope === 'ITEMS'" class="sub">{{ row.item_names?.length || 0 }} 件</span>
        </template>
      </el-table-column>
      <el-table-column label="活动折扣" width="100">
        <template #default="{ row }">
          <span v-if="row.discount_rate != null">{{ row.discount_rate }}%</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="覆盖抽成" min-width="150">
        <template #default="{ row }">
          <span v-if="commissionConfigured(row)">{{ commissionLabel(row) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="时间窗" min-width="280">
        <template #default="{ row }">
          {{ formatDateTime(row.start_at) }} ~ {{ formatDateTime(row.end_at) }}
        </template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" width="80" />
      <el-table-column label="时间状态" width="100">
        <template #default="{ row }">
          <el-tag :type="windowType(row.window_status)" effect="light" size="small">{{ windowLabel(row.window_status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('promotion:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('promotion:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #mobile="{ rows }">
        <ul class="pm-list" aria-label="促销活动列表">
          <li v-for="row in rows" :key="row.id" class="pm-card">
            <div class="pm-card-head"><strong>{{ row.title }}</strong><el-tag :type="windowType(row.window_status)" size="small">{{ windowLabel(row.window_status) }}</el-tag></div>
            <p>{{ scopeLabel(row) }}</p>
            <dl><div><dt>活动折扣</dt><dd>{{ row.discount_rate == null ? '不设置' : `${row.discount_rate}%` }}</dd></div><div><dt>覆盖抽成</dt><dd>{{ commissionConfigured(row) ? commissionLabel(row) : '沿用规则' }}</dd></div><div><dt>优先级</dt><dd>{{ row.priority }}</dd></div></dl>
            <p class="pm-time">{{ formatDateTime(row.start_at) }} 至 {{ formatDateTime(row.end_at) }}</p>
            <div class="pm-actions"><el-button v-if="auth.hasPerm('promotion:edit')" size="small" @click="onEdit(row)">编辑</el-button><el-button v-if="auth.hasPerm('promotion:delete')" size="small" type="danger" plain @click="onDelete(row)">删除</el-button></div>
          </li>
        </ul>
      </template>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑促销活动' : '新增促销活动'" width="min(760px, calc(100vw - 24px))" class="ap-modal" :close-on-click-modal="false" :before-close="beforeClose">
    <el-alert v-if="saveUncertain" title="保存结果尚未确认" type="warning" :closable="false" show-icon>请关闭后刷新活动列表，核对名称和配置；原请求可能仍在处理，请勿立即重复创建或保存。</el-alert>
    <el-form :model="form" :disabled="saving" class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="活动名称"><el-input v-model="form.title" placeholder="如：双十一限时折扣" /></el-form-item>
      <el-form-item label="适用范围" class="ap-field--full">
        <el-radio-group v-model="form.scope">
          <el-radio v-for="(v, k) in PROMOTION_SCOPE" :key="k" :label="k">{{ v.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.scope === 'CATEGORY'" label="适用分类">
        <el-select v-model="form.category" :loading="loadingCategories" placeholder="选择分类" style="width: 100%">
          <el-option v-for="c in serviceCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
        <span v-if="categoryError" class="option-error">分类读取失败 <el-button link type="primary" @click="loadCategories">重试</el-button></span>
      </el-form-item>
      <el-form-item v-if="form.scope === 'ITEMS'" label="指定商品">
        <el-select
          v-model="form.items"
          multiple
          filterable
          :loading="loadingServices"
          placeholder="选择参与活动的商品"
          style="width: 100%"
        >
          <el-option
            v-for="s in serviceOptions"
            :key="s.id"
            :label="`${s.name}（${amountToXaCoin(s.price)}币）`"
            :value="s.id"
          />
        </el-select>
        <span v-if="serviceError" class="option-error">商品读取失败 <el-button link type="primary" @click="loadServices">重试</el-button></span>
      </el-form-item>

      <el-form-item label="活动折扣">
        <el-switch v-model="discountEnabled" />
        <el-input-number
          v-if="discountEnabled"
          v-model="form.discount_rate"
          :min="1"
          :max="100"
        />
        <span class="tip">{{ discountEnabled ? '100 为原价，90 为九折；设置折扣后不可用优惠券' : '不设置活动折扣' }}</span>
      </el-form-item>
      <el-form-item label="覆盖抽成">
        <el-switch v-model="commissionEnabled" />
        <span class="tip">{{ commissionEnabled ? '实际抽成以商品规则和派单试算为准' : '沿用原有抽成规则' }}</span>
      </el-form-item>
      <CommissionEditor v-if="commissionEnabled" v-model:type="commissionType" v-model:rate="form.commission_rate" v-model:fixed="commissionFixed" />
      <el-alert v-if="!discountEnabled && !commissionEnabled" class="ap-field--full" type="warning" :closable="false" show-icon title="未设置折扣或抽成，仍可能按优先级挡住其他活动" />

      <el-form-item label="开始时间">
        <el-date-picker v-model="form.start_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
      </el-form-item>
      <el-form-item label="结束时间">
        <el-date-picker v-model="form.end_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
      </el-form-item>
      <el-form-item label="优先级">
        <el-input-number v-model="form.priority" :min="0" />
        <el-tooltip content="同一商品同时命中多个活动时，优先级高者优先；相同则开始时间较晚者优先，再按 ID 较小者优先。只选一条，即使该活动未设置折扣和抽成也会占用优先位置；体验资格商品不参加活动。" :trigger="['hover', 'focus', 'click']" placement="top"><el-button link type="primary" aria-label="查看活动优先级说明">规则说明</el-button></el-tooltip>
      </el-form-item>
      <el-form-item label="备注" class="ap-field--full"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="requestClose">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="saveUncertain" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { nextTick, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import CommissionEditor from '@/components/CommissionEditor.vue'
import { commissionConfigured, commissionError, commissionLabel, commissionPayload, inheritedCommission, readCommission, type CommissionType } from '@/utils/commission'
import { promotionApi, serviceItemApi, serviceCategoryApi } from '@/api/modules'
import { PROMOTION_SCOPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', scope: '', is_active: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})
const discountEnabled = ref(true)
const commissionEnabled = ref(false)
const commissionType = ref<CommissionType>('PERCENT')
const commissionFixed = ref<number | undefined>(0)
const serviceOptions = ref<any[]>([])
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
  try { await ElMessageBox.confirm('关闭后未保存的活动修改将丢失。', '放弃修改？', { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' }); return true }
  catch { return false }
}
async function requestClose() { if (await mayClose()) dialogVisible.value = false }
function beforeClose(done: () => void) { void mayClose().then(allowed => { if (allowed) done() }) }
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
  if (!form.title?.trim()) { ElMessage.warning('请填写活动名称'); return }
  if (form.scope === 'CATEGORY' && !categoriesLoaded.value) { ElMessage.warning('请先加载分类'); return }
  if (form.scope === 'ITEMS' && !servicesLoaded.value) { ElMessage.warning('请先加载商品'); return }
  if (form.scope === 'CATEGORY' && !form.category) { ElMessage.warning('请选择适用分类'); return }
  if (form.scope === 'ITEMS' && !form.items.length) { ElMessage.warning('请选择指定商品'); return }
  if (discountEnabled.value && (!Number.isInteger(form.discount_rate) || form.discount_rate < 1 || form.discount_rate > 100)) { ElMessage.warning('活动折扣须为 1 到 100 的整数'); return }
  if (!form.start_at || !form.end_at) { ElMessage.warning('请选择起止时间'); return }
  if (form.end_at <= form.start_at) { ElMessage.warning('结束时间必须晚于开始时间'); return }
  if (commissionEnabled.value) {
    const error = commissionError(commissionType.value, form.commission_rate, commissionFixed.value)
    if (error) { ElMessage.warning(error); return }
  }
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
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } catch (error: any) {
    if (revision === auth.sessionRevision) {
      if (!error?.response || error.response.status >= 500) saveUncertain.value = true
      else ElMessage.error(error.response.data?.msg || '保存失败，请检查输入后重试')
    }
  } finally {
    if (revision === auth.sessionRevision) saving.value = false
  }
}

async function onDelete(row: any) {
  try { await ElMessageBox.confirm(`删除「${row.title}」后，历史订单将失去该活动关联；已保存的订单金额和抽成不会重新计算。若只需停止新订单使用，可编辑并停用。`, '删除活动', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }) }
  catch { return }
  try { await promotionApi.remove(row.id, true); ElMessage.success('已删除'); tableRef.value.reload() }
  catch (error: any) { if (!error?.response || error.response.status >= 500) ElMessage.warning('删除结果尚未确认，请刷新列表核对，原请求可能仍在处理'); else ElMessage.error(error.response.data?.msg || '删除失败，请重试') }
}
</script>

<style scoped lang="scss">
.pm-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.pm-page :deep(.page-container) {
  padding: 0;
}
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}
.sub {
  margin-left: 6px;
  font-size: 12px;
  color: var(--muted-foreground);
}
.muted {
  color: var(--muted-foreground);
}
.pm-filter-note { color: var(--muted-foreground); font-size: 12px; flex-basis: 100%; }
.pm-list { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.pm-card { padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.pm-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.pm-card-head strong { overflow-wrap: anywhere; }
.pm-card p { margin: 9px 0; color: var(--muted-foreground); font-size: 12px; }
.pm-card dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 12px 0; }
.pm-card dt { color: var(--muted-foreground); font-size: 12px; }
.pm-card dd { margin: 3px 0 0; overflow-wrap: anywhere; }
.pm-actions { display: flex; gap: 8px; margin-top: 12px; }
.pm-actions :deep(.el-button + .el-button) { margin-left: 0; }
.option-error { color: var(--el-color-danger); font-size: 12px; }
</style>
