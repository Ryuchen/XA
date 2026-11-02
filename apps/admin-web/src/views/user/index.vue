<template>
  <div class="us-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">管理老板资料、消费分级、推荐关系与账户状态</p>
    </div>

    <Alert v-if="saveReview && !dialogVisible && !saving"><AlertTitle>老板资料保存结果待核对</AlertTitle><AlertDescription><p>账户 #{{ saveReview.id }} 的请求可能仍在处理。请读取最新资料并核对实际结果。</p><p v-if="reviewNotice" role="status">{{ reviewNotice }}</p><Button variant="outline" :disabled="reviewLoading" @click="checkSaveReview">{{ reviewLoading ? '读取中…' : '读取最新资料' }}</Button><Button v-if="reviewChecked" variant="outline" :disabled="reviewLoading" @click="finishSaveReview">已核对，解除限制</Button></AlertDescription></Alert>
    <p v-if="notice && !dialogVisible" role="status">{{ notice }}</p>

    <div class="us-list-heading"><strong>老板资料</strong>
      <Popover><PopoverTrigger as-child><button type="button" class="us-help" aria-label="账户和余额说明">?</button></PopoverTrigger><PopoverContent>列表展示账户钱包余额；订单支付和退款请以订单及资金流水为准。账户启用与允许登录分别判断。</PopoverContent></Popover>
    </div>

    <SimplePagedList v-if="auth.hasPerm('user:view')" :key="auth.sessionRevision" ref="tableRef" :fetcher="userApi.list" :query="query">
      <template #toolbar="{ reload }">
        <div class="us-filters">
          <SuggestionInput v-model="query.boss_no" :fetch-suggestions="querySuggest('boss_no')" placeholder="编号" label="按编号筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
          <SuggestionInput v-model="query.nickname" :fetch-suggestions="querySuggest('nickname')" placeholder="昵称" label="按昵称筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
          <SuggestionInput v-model="query.phone" :fetch-suggestions="querySuggest('phone')" placeholder="手机号" label="按手机号筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
          <NativeSelect v-model="query.is_active" aria-label="账户状态" @update:model-value="reload"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption value="true">账户启用</NativeSelectOption><NativeSelectOption value="false">账户停用</NativeSelectOption></NativeSelect>
          <Button @click="reload">查询</Button>
        </div>
      </template>
      <template #default="{ rows }">
        <div class="us-desktop-table"><Table><TableHeader><TableRow><TableHead>编号</TableHead><TableHead>头像</TableHead><TableHead>账号</TableHead><TableHead>昵称</TableHead><TableHead>状态</TableHead><TableHead>登录</TableHead><TableHead>手机号</TableHead><TableHead>老板分级</TableHead><TableHead>账户余额</TableHead><TableHead>推荐人</TableHead><TableHead>注册时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody>
          <TableRow v-for="row in rows" :key="row.id"><TableCell>{{ row.boss_no || '—' }}</TableCell><TableCell><img v-if="row.avatar_url" class="us-avatar" :src="row.avatar_url" alt="" /><span v-else class="us-avatar">{{ (row.nickname || row.username || '老板').slice(0, 1) }}</span></TableCell><TableCell>{{ row.username || '—' }}</TableCell><TableCell>{{ row.nickname || '—' }}</TableCell><TableCell><Badge variant="outline">{{ accountStatus(row) }}</Badge></TableCell><TableCell>{{ loginStatus(row) }}</TableCell><TableCell>{{ row.phone || '—' }}</TableCell><TableCell>{{ row.boss_type_name || '—' }}</TableCell><TableCell>{{ balanceLabel(row) }}</TableCell><TableCell>{{ row.inviter_name || '—' }}</TableCell><TableCell>{{ row.created_at ? formatDateTime(row.created_at) : '待核对' }}</TableCell><TableCell><div class="us-row-actions"><Button v-if="auth.hasPerm('order:view')" variant="link" @click="viewOrders(row)">订单</Button><Button v-if="auth.hasPerm('user:edit')" variant="link" :disabled="!!saveReview" @click="onEdit(row)">编辑</Button><Popover><PopoverTrigger as-child><Button variant="ghost">更多</Button></PopoverTrigger><PopoverContent class="us-menu"><Button v-if="auth.hasPerm('user:edit')" variant="ghost" :disabled="retiring" @click="onRetire(row)">退店清空</Button><Button variant="ghost" @click="banTarget = row; banVisible = true">封禁记录</Button></PopoverContent></Popover></div></TableCell></TableRow>
        </TableBody></Table></div>
        <div class="us-mobile-list" role="list" aria-label="老板资料列表"><article v-for="row in rows" :key="row.id" class="us-mobile-card" role="listitem">
          <div class="us-mobile-head"><img v-if="row.avatar_url" class="us-avatar" :src="row.avatar_url" alt="" /><span v-else class="us-avatar">{{ (row.nickname || row.username || '老板').slice(0, 1) }}</span><div class="us-mobile-identity"><strong>{{ row.nickname || row.username || '名称待核对' }}</strong><span>{{ row.boss_no || `账户 #${row.id}` }} · {{ row.username || '账号待核对' }}</span></div><Badge variant="outline">{{ accountStatus(row) }}</Badge></div>
          <dl class="us-mobile-facts"><div><dt>登录</dt><dd>{{ loginStatus(row) }}</dd></div><div><dt>钱包余额</dt><dd>{{ balanceLabel(row) }}</dd></div><div><dt>老板分级</dt><dd>{{ row.boss_type_name || '未分级' }}</dd></div><div><dt>推荐人</dt><dd>{{ row.inviter_name || '无' }}</dd></div><div><dt>手机号</dt><dd>{{ row.phone || '未填写' }}</dd></div><div><dt>注册时间</dt><dd>{{ row.created_at ? formatDateTime(row.created_at) : '待核对' }}</dd></div></dl>
          <div class="us-mobile-actions"><Button v-if="auth.hasPerm('order:view')" variant="outline" @click="viewOrders(row)">查看订单</Button><Button v-if="auth.hasPerm('user:edit')" variant="outline" :disabled="!!saveReview" @click="onEdit(row)">编辑</Button><Popover><PopoverTrigger as-child><Button variant="outline">更多</Button></PopoverTrigger><PopoverContent class="us-menu"><Button v-if="auth.hasPerm('user:edit')" variant="ghost" :disabled="retiring" @click="onRetire(row)">退店清空</Button><Button variant="ghost" @click="banTarget = row; banVisible = true">封禁记录</Button></PopoverContent></Popover></div>
        </article></div>
      </template>
    </SimplePagedList>
  </div>

  <Dialog :open="dialogVisible" @update:open="value => { if (!value) closeEditDialog() }">
    <DialogContent class="us-dialog" :show-close-button="!saving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { event.preventDefault(); closeEditDialog() }">
      <DialogHeader><DialogTitle>{{ reviewViewing ? '核对最新老板资料' : '编辑老板资料' }}</DialogTitle><DialogDescription>核对账户资料、分级和推荐关系。</DialogDescription></DialogHeader>
      <p v-if="notice" role="status">{{ notice }}</p>
      <fieldset class="us-fields" :disabled="saving || saveUnknown || reviewViewing">
        <p v-if="editDirty && !saveUnknown && !reviewViewing" class="us-draft-status" role="status">有未保存的修改</p>
        <section class="us-form-section"><p class="us-section-title">基础信息</p><div class="us-field-grid">
          <label>账号<Input v-model="editForm.username" disabled /></label><label>编号<Input v-model="editForm.boss_no" disabled placeholder="系统自动生成" /></label>
          <label>昵称<Input v-model="editForm.nickname" /></label><label>真实姓名<Input v-model="editForm.real_name" /></label><label>手机号<Input v-model="editForm.phone" /></label>
        </div></section>
        <section class="us-form-section"><p class="us-section-title">归属与消费</p>
          <label>老板分级<NativeSelect :model-value="String(editForm.boss_type ?? '')" :disabled="!bossTypesReady || currentVipManaged || missingBossType" @update:model-value="value => editForm.boss_type = value ? Number(value) : null"><NativeSelectOption value="">不分级</NativeSelectOption><NativeSelectOption v-for="bt in bossTypes" :key="bt.id" :value="String(bt.id)" :disabled="bt.vip_managed && bt.id !== initialBossType">{{ bt.name }}（{{ bt.discount_rate }}%）{{ bt.vip_managed ? ' · VIP自动' : '' }}{{ !bt.is_active ? ' · 已停用' : '' }}</NativeSelectOption></NativeSelect></label>
          <p v-if="bossTypesLoading" class="us-field-message" role="status">正在读取分级…</p><div v-if="bossTypesError || missingBossType" class="us-field-message" role="alert">{{ missingBossType ? '当前分级未找到，请核对' : '分级读取失败' }} <Button variant="link" @click="loadBossTypes">重新读取</Button></div><p v-else-if="currentVipManaged" class="us-field-message">VIP 分级自动管理，退店清空可重置</p>
          <label>搜索推荐人<Input v-model="inviterKeyword" placeholder="输入昵称、账号或编号" @update:model-value="value => searchInviter(String(value))" /></label>
          <label>推荐人<NativeSelect :model-value="String(editForm.inviter ?? '')" @update:model-value="value => { editForm.inviter = value ? Number(value) : null; onInviterChange(editForm.inviter) }"><NativeSelectOption value="">无推荐人</NativeSelectOption><NativeSelectOption v-for="u in inviterOptions" :key="u.id" :value="String(u.id)">{{ inviterLabel(u) }}</NativeSelectOption></NativeSelect></label>
          <p v-if="inviterLoading" class="us-field-message" role="status">正在搜索推荐人…</p><p v-if="inviterError" class="us-field-message" role="alert">推荐人读取失败，请重新搜索</p>
          <label><span>分佣比例（%） <Popover><PopoverTrigger as-child><button type="button" class="us-help" aria-label="分佣比例说明">?</button></PopoverTrigger><PopoverContent>推荐人分得的平台抽成比例，不是订单金额比例。</PopoverContent></Popover></span><Input v-model.number="editForm.inviter_commission_rate" type="number" min="0" max="100" step="1" @update:model-value="rateError = ''" /></label><p v-if="rateError" class="us-field-message" role="alert">{{ rateError }}</p><p v-if="!editForm.inviter" class="us-field-message">未选择推荐人，比例暂不生效</p>
        </section>
        <section class="us-form-section"><p class="us-section-title">账户状态</p><Alert><AlertDescription>{{ accountStatus(editForm) }} · {{ loginStatus(editForm) }}；状态变更请使用封禁记录</AlertDescription></Alert></section>
      </fieldset>
      <Alert v-if="saveUnknown"><AlertDescription>保存结果未确认，请关闭后读取最新资料核对，勿重复提交。</AlertDescription></Alert>
      <DialogFooter><Button variant="outline" :disabled="saving" @click="closeEditDialog()">{{ saveUnknown || reviewViewing ? '关闭' : '取消' }}</Button><Button v-if="!reviewViewing" :disabled="saving || saveUnknown" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  <Dialog v-model:open="retireVisible"><DialogContent><DialogHeader><DialogTitle>退店清空</DialogTitle><DialogDescription>确认清空「{{ retireTarget?.nickname || retireTarget?.username }}」（{{ retireTarget?.boss_no || `账户 #${retireTarget?.id}` }}）的业务权益？历史订单和财务保留；在途订单、预约、客诉及资金须先结清。</DialogDescription></DialogHeader><label class="us-retire-reason">清空原因<Input v-model="retireReason" maxlength="500" placeholder="填写 2 至 500 字原因" /></label><p v-if="retireError" role="alert">{{ retireError }}</p><DialogFooter><Button variant="outline" @click="retireVisible = false">取消</Button><Button @click="submitRetire">确认清空</Button></DialogFooter></DialogContent></Dialog>
  <account-ban-dialog v-model="banVisible" resource="users" :target-id="banTarget?.id" :target-name="banTarget?.nickname || banTarget?.username" @changed="tableRef?.load()" />
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import SuggestionInput from '@/components/SuggestionInput.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import AccountBanDialog from '@/components/AccountBanDialog.vue'
import { bossTypeApi, userApi } from '@/api/modules'
import { request } from '@/api/request'
import { formatDateTime, amountToXaCoin } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

const notice = ref('')
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const auth = useAuthStore()
const router = useRouter()
let userPageDisposed = false
onUnmounted(() => { userPageDisposed = true })
const tableRef = ref()
const banVisible = ref(false)
const banTarget = ref<any>(null)
const query = reactive<Record<string, any>>({ boss_no: '', nickname: '', phone: '', is_active: '' })
function accountStatus(row: any) { return row.is_active === true ? '账户启用' : row.is_active === false ? '账户停用' : '状态待核对' }
function loginStatus(row: any) { return row.is_active === false ? '不可登录' : row.is_active === true && row.can_login === true ? '可登录' : row.is_active === true && row.can_login === false ? '登录受限' : '待核对' }
function balanceLabel(row: any) { return row.wallet_balance == null ? '待核对' : `${amountToXaCoin(row.wallet_balance)} 币` }
function viewOrders(row: any) { if (auth.hasPerm('order:view') && Number.isSafeInteger(row.id) && row.id > 0) void router.push({ name: 'orders', query: { find_customer: String(row.id) } }) }

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string): Promise<string[]> => {
    if (!keyword) return []
    try {
      const revision = auth.sessionRevision
      const res = (await userApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision) return []
      return [...new Set<string>((res.data.list || []).map((u: any) => u[field]).filter((value: unknown): value is string => typeof value === 'string' && !!value))]
    } catch { return [] }
  }
}

const dialogVisible = ref(false)
const saving = ref(false)
const retiring = ref(false)
const retireVisible = ref(false)
const retireTarget = ref<any>(null)
const retireReason = ref('')
const retireError = ref('')
const editForm = reactive<any>({})

const bossTypes = ref<any[]>([])
const inviterOptions = ref<any[]>([])
const inviterKeyword = ref('')
const inviterLoading = ref(false)
const inviterError = ref(false)
const selectedInviter = ref<any>(null)
const bossTypesLoading = ref(false)
const bossTypesReady = ref(false)
const bossTypesError = ref(false)
const initialBossType = ref<number | null>(null)
const currentVipManaged = computed(() => bossTypes.value.some(bt => bt.id === initialBossType.value && bt.vip_managed))
const missingBossType = computed(() => bossTypesReady.value && initialBossType.value != null && !bossTypes.value.some(bt => bt.id === initialBossType.value))
const rateError = ref('')
const saveUnknown = ref(false)
const reviewViewing = ref(false)
const saveReview = ref<{ id: number } | null>(null)
const reviewChecked = ref(false)
const reviewLoading = ref(false)
const reviewNotice = ref('')
const editBaseline = ref<string | null>(null)
const draftFields = ['nickname', 'real_name', 'phone', 'boss_type', 'inviter', 'inviter_commission_rate']
function draftSignature() { return JSON.stringify(draftFields.map(key => editForm[key] ?? null)) }
const editDirty = computed(() => editBaseline.value !== null && draftSignature() !== editBaseline.value)
let editRevision = 0
let searchRevision = 0
let reviewOwnerKey = ''
let discardPromptOpen = false

watch(() => auth.sessionRevision, () => { editRevision++; searchRevision++; dialogVisible.value = false; retireVisible.value = false; editBaseline.value = null; saveUnknown.value = false; reviewViewing.value = false; saving.value = false }, { flush: 'sync' })
watch(dialogVisible, visible => { if (!visible) { editRevision++; searchRevision++ } }, { flush: 'sync' })

async function confirmDiscard() {
  if (!editDirty.value || saveUnknown.value || reviewViewing.value || !auth.hasPerm('user:edit')) return true
  if (discardPromptOpen) return false
  discardPromptOpen = true
  try { return await askConfirm({ title: '放弃修改？', description: '关闭后未保存的老板资料将丢失。', confirmText: '放弃修改', cancelText: '继续编辑' }) }
  finally { discardPromptOpen = false }
}
async function closeEditDialog(done?: () => void) {
  if (saving.value || !dialogVisible.value) return
  const revision = auth.sessionRevision
  if (!await confirmDiscard() || userPageDisposed || revision !== auth.sessionRevision || !dialogVisible.value) return
  editBaseline.value = null; reviewViewing.value = false
  if (done) done(); else dialogVisible.value = false
}
onBeforeRouteLeave(async () => {
  if (!dialogVisible.value) return true
  if (saving.value) { notice.value = '正在保存，请稍候'; return false }
  return confirmDiscard()
})
function onUserBeforeUnload(event: BeforeUnloadEvent) {
  if (dialogVisible.value && auth.hasPerm('user:edit') && (saving.value || (editDirty.value && !saveUnknown.value && !reviewViewing.value))) { event.preventDefault(); event.returnValue = '' }
}
onMounted(() => window.addEventListener('beforeunload', onUserBeforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', onUserBeforeUnload))

function saveReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_user_save_review_${id}` : ''
}
function restoreSaveReview() {
  const key = saveReviewKey()
  if (key === reviewOwnerKey) return
  reviewOwnerKey = key; saveReview.value = null; reviewChecked.value = false; reviewLoading.value = false; reviewNotice.value = ''
  if (!key) return
  try { const marker = JSON.parse(sessionStorage.getItem(key) || 'null'); if (Number.isSafeInteger(marker?.id) && marker.id > 0) saveReview.value = { id: marker.id } } catch { /* In-memory guard remains available. */ }
}
function beginSaveReview(id: number) {
  saveReview.value = { id }; reviewChecked.value = false; reviewNotice.value = ''
  const key = saveReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id })) } catch { /* Current page still retains the marker. */ }
}
function clearSaveReview() {
  const key = saveReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  saveReview.value = null; reviewChecked.value = false; reviewNotice.value = ''
}
async function checkSaveReview() {
  const marker = saveReview.value, revision = auth.sessionRevision
  if (!marker || reviewLoading.value || !auth.hasPerm('user:edit')) return
  reviewLoading.value = true; reviewChecked.value = false; reviewNotice.value = ''
  try {
    const row = (await userApi.retrieve(marker.id, true)).data
    if (userPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker) return
    await onEdit(row, true)
    if (userPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker || !dialogVisible.value || !reviewViewing.value) return
    reviewChecked.value = true; reviewNotice.value = '已读取最新资料；请核对内容和请求处理情况。'
  } catch (error: any) {
    if (userPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker) return
    if (error?.response?.status === 404) { reviewChecked.value = true; reviewNotice.value = '未找到该账户。请求可能仍在处理，请确认后再解除限制。' }
    else reviewNotice.value = '读取失败，请稍后重新读取。'
  } finally { if (revision === auth.sessionRevision) reviewLoading.value = false }
}
async function finishSaveReview() {
  const marker = saveReview.value, revision = auth.sessionRevision
  if (!marker || !reviewChecked.value || reviewLoading.value) return
  if (!await askConfirm({ title: '解除保存限制', description: '请确认已核对资料和请求处理情况；请求可能仍在处理中。', confirmText: '已核对，解除限制', cancelText: '继续核对' })) return
  if (!userPageDisposed && revision === auth.sessionRevision && saveReview.value === marker && reviewChecked.value) { clearSaveReview(); saveUnknown.value = false; tableRef.value?.load() }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreSaveReview, { immediate: true, flush: 'sync' })

async function loadBossTypes() {
  const revision = editRevision
  bossTypesLoading.value = true
  bossTypesReady.value = false
  bossTypesError.value = false
  try {
    const res = await bossTypeApi.allOptions() as any
    if (revision !== editRevision || userPageDisposed) return
    bossTypes.value = res.data.list || []
    bossTypesReady.value = true
  } catch {
    if (revision === editRevision && !userPageDisposed) bossTypesError.value = true
  } finally {
    if (revision === editRevision && !userPageDisposed) bossTypesLoading.value = false
  }
}

async function searchInviter(keyword: string) {
  const revision = ++searchRevision, edit = editRevision
  inviterError.value = false
  if (!keyword.trim()) {
    inviterLoading.value = false
    inviterOptions.value = selectedInviter.value ? [selectedInviter.value] : []
    return
  }
  inviterLoading.value = true
  try {
    const res = await request<any>({ url: '/users/', params: { keyword: keyword.trim(), page: 1, page_size: 20 }, silent: true })
    if (revision !== searchRevision || edit !== editRevision || userPageDisposed) return
    const rows = (res.data.list || []).filter((u: any) => u.id !== editForm.id)
    if (selectedInviter.value && !rows.some((u: any) => u.id === selectedInviter.value.id)) rows.unshift(selectedInviter.value)
    inviterOptions.value = rows
  } catch {
    if (revision === searchRevision && edit === editRevision && !userPageDisposed) inviterError.value = true
  } finally {
    if (revision === searchRevision && edit === editRevision && !userPageDisposed) inviterLoading.value = false
  }
}
function inviterLabel(u: any) { return [u.nickname || u.username || '未命名', u.boss_no || `账户 #${u.id}`, u.username].filter(Boolean).join(' · ') }
function onInviterChange(id: number | null) { selectedInviter.value = inviterOptions.value.find(u => u.id === id) || null }

async function onEdit(row: any, review = false) {
  if (!auth.hasPerm('user:edit') || (saveReview.value && !review)) return
  editRevision++
  searchRevision++
  notice.value = ''
  Object.assign(editForm, row)
  initialBossType.value = row.boss_type ?? null
  bossTypes.value = []
  bossTypesReady.value = false
  bossTypesError.value = false
  inviterError.value = false
  inviterLoading.value = false
  inviterKeyword.value = ''
  rateError.value = ''
  saveUnknown.value = false
  reviewViewing.value = review
  // 预置推荐人选项，保证回显当前推荐人名称（编号未知，仅回显昵称）
  selectedInviter.value = row.inviter
    ? { id: row.inviter, nickname: row.inviter_name, boss_no: '' }
    : null
  inviterOptions.value = selectedInviter.value ? [selectedInviter.value] : []
  editBaseline.value = draftSignature()
  dialogVisible.value = true
  await loadBossTypes()
}

async function onSave() {
  if (saving.value || saveUnknown.value || reviewViewing.value || saveReview.value || !auth.hasPerm('user:edit')) return
  if (!Number.isInteger(editForm.inviter_commission_rate) || editForm.inviter_commission_rate < 0 || editForm.inviter_commission_rate > 100) { rateError.value = '请输入 0 到 100 的整数'; return }
  const revision = editRevision, session = auth.sessionRevision
  saving.value = true
  try {
    const payload: any = {
      nickname: editForm.nickname,
      real_name: editForm.real_name,
      phone: editForm.phone,
      inviter: editForm.inviter || null,
      inviter_commission_rate: editForm.inviter_commission_rate,
    }
    if ((editForm.boss_type ?? null) !== initialBossType.value) payload.boss_type = editForm.boss_type || null
    beginSaveReview(editForm.id)
    await request({ url: `/users/${editForm.id}/`, method: 'patch', data: payload, silent: true })
    if (revision !== editRevision || session !== auth.sessionRevision || userPageDisposed) return
    notice.value = '保存成功'
    clearSaveReview(); editBaseline.value = null
    dialogVisible.value = false
    tableRef.value?.load()
  } catch (error: any) {
    if (revision !== editRevision || session !== auth.sessionRevision || userPageDisposed) return
    const body = error?.response?.data || error, status = error?.response?.status
    if ((!status || status < 500) && ((status >= 400 && status < 500) || (typeof body?.code === 'number' && body.code >= 400 && body.code < 500))) { clearSaveReview(); notice.value = typeof body?.msg === 'string' && body.msg ? body.msg : '保存被拒绝，请核对资料' }
    else saveUnknown.value = true
  } finally {
    if (session === auth.sessionRevision && !userPageDisposed) saving.value = false
  }
}
function onRetire(row: any) {
  if (retiring.value || !auth.hasPerm('user:edit')) return
  retireTarget.value = row; retireReason.value = ''; retireError.value = ''; notice.value = ''; retireVisible.value = true
}
async function submitRetire() {
  const row = retireTarget.value, reason = retireReason.value.trim(), revision = auth.sessionRevision
  if (!row || retiring.value || !auth.hasPerm('user:edit')) return
  if (reason.length < 2 || reason.length > 500) { retireError.value = '请填写 2 至 500 字原因'; return }
  retireVisible.value = false; retiring.value = true
  try {
    await userApi.action(row.id, 'retire', { reason }, true)
    if (userPageDisposed || revision !== auth.sessionRevision) return
    notice.value = '业务权益已重置'; tableRef.value?.load()
  } catch (error: any) {
    if (userPageDisposed || revision !== auth.sessionRevision) return
    const body = error?.response?.data || error, status = error?.response?.status
    const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
    notice.value = rejected ? (typeof body?.msg === 'string' && body.msg ? body.msg : '退店请求被拒绝，请核对资料') : '退店结果未确认，请先联系管理员核对，勿重复操作。'
  } finally { retiring.value = false }
}
</script>

<style scoped lang="scss">
:global(.us-dialog) { width: min(760px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); overflow-y: auto; }
.us-fields { display: grid; gap: 16px; border: 0; padding: 0; margin: 0; min-width: 0; }
.us-fields label { display: grid; gap: 4px; min-width: 0; }
.us-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.us-form-section { display: grid; gap: 8px; }
@media (max-width: 480px) { .us-field-grid { grid-template-columns: 1fr; } }
.us-retire-reason { display: grid; gap: 4px; }
.us-draft-status { margin: 0; color: var(--muted-foreground); font-size: 12px; }
.us-field-message { width: 100%; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; margin-top: 4px; }
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}

/* 编辑弹窗：分区分组 + token 视觉 */

.us-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 us-page 的间距节奏 */
.us-page :deep(.page-container) {
  padding: 0;
}

.us-list-heading { display: flex; align-items: center; gap: 4px; }
.us-help { width: var(--control-height-sm); height: var(--control-height-sm); border: 0; border-radius: 50%; color: var(--primary); background: transparent; cursor: pointer; }
.us-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.us-mobile-list { display: none; gap: 12px; }
.us-filters, .us-row-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.us-filters > * { min-width: 120px; flex: 1; }
.us-filters > button { flex: none; }
.us-avatar { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; flex: none; border-radius: 50%; background: var(--muted); object-fit: cover; }
.us-menu { display: grid; width: max-content; gap: 4px; }
@media (max-width: 720px) { .us-desktop-table { display: none; } .us-mobile-list { display: grid; } }
.us-mobile-card { padding: 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.us-mobile-head { display: flex; align-items: center; gap: 8px; }
.us-mobile-identity { display: grid; flex: 1; min-width: 0; gap: 4px; }
.us-mobile-identity strong, .us-mobile-identity span { overflow-wrap: anywhere; }
.us-mobile-identity span { color: var(--muted-foreground); font-size: 12px; }
.us-mobile-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; margin: 12px 0; padding-top: 12px; border-top: 1px solid var(--border); }
.us-mobile-facts div { min-width: 0; }
.us-mobile-facts dt { color: var(--muted-foreground); font-size: 12px; }
.us-mobile-facts dd { margin: 2px 0 0; overflow-wrap: anywhere; }
.us-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.us-code {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--secondary);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted-foreground);
}
.us-lock-tag {
  padding: 1px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--chart-3) 16%, var(--card));
  color: var(--chart-3);
  font-size: 11px;
}
.us-balance-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.us-no {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.02em;
}

</style>
