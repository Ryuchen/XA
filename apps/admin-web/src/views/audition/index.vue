<template>
  <div class="au-page">
    <Alert v-if="marker && !bindingVisible"><AlertTitle>{{ roleLabel(marker.role) }}入口绑定结果待核对</AlertTitle><AlertDescription><p>「{{ marker.title }}」可能已更换绑定；原请求可能仍在处理。</p><Button size="xs" variant="link" :disabled="checking" @click="checkBinding">{{ checking ? '读取中…' : '读取当前绑定' }}</Button><span v-if="checkText">{{ checkText }}</span><Button size="xs" variant="link" :disabled="!checked || checking" @click="clearAfterReview">已核对，解除限制</Button></AlertDescription></Alert>
    <p v-if="notice && !dialogVisible && !bindingVisible" role="status">{{ notice }}</p>
    <Card class="au-header"><div><h2>试音入口</h2><p>每个活动分别管理老板和陪玩入口。绑定账号的登录链接仅交给账号本人。</p></div>
      <Button size="xs" v-if="auth.hasPerm('audition:edit')" :disabled="saving || !!marker" @click="onCreate">新增试音链接</Button>
    </Card>
    <SimplePagedList ref="tableRef" :fetcher="auditionApi.list" :query="query">
      <template #toolbar="{ reload }">
        <form class="au-filters" @submit.prevent="reload">
          <label class="au-keyword">搜索活动<Input v-model="query.keyword" placeholder="活动标题或备注" aria-label="搜索试音链接" @update:model-value="value => { if (value === '') reload() }" /></label>
          <label>启用状态<NativeSelect v-model="query.is_active" aria-label="启用状态" @update:model-value="reload"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption value="1">启用</NativeSelectOption><NativeSelectOption value="0">停用</NativeSelectOption></NativeSelect></label>
          <Button size="xs" type="submit">查询</Button>
        </form>
      </template>
      <template #default="{ rows }">
        <div class="au-records"><Table>
          <TableHeader><TableRow><TableHead>试音活动</TableHead><TableHead>老板入口</TableHead><TableHead>陪玩入口</TableHead><TableHead>状态 / 有效期</TableHead><TableHead v-if="auth.hasPerm('audition:edit') || auth.hasPerm('audition:delete')">活动操作</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id">
            <TableCell class="au-title"><strong>{{ row.title }}</strong><small>活动 #{{ row.id }}</small><small v-if="row.remark" class="au-remark">{{ row.remark }}</small></TableCell>
            <TableCell class="au-entry" data-label="老板入口">
              <span>{{ bindingLabel(row.boss_binding) }}</span>
              <small v-if="bossDetail(row.boss_binding)">{{ bossDetail(row.boss_binding) }}</small>
              <small v-if="row.boss_url_placeholder" class="au-warning">入口域名待配置</small>
              <div v-if="auth.hasPerm('audition:bind_boss')" class="au-actions">
                <Button size="xs" variant="outline" @click="copyLink(row, 'boss')">老板链接</Button>
                <Button size="xs" variant="outline" :disabled="busy || !!marker" @click="openBinding(row, 'boss')">老板绑定</Button>
              </div>
            </TableCell>
            <TableCell class="au-entry" data-label="陪玩入口">
              <span>{{ providerLabel(row.provider_binding) }}</span>
              <small v-if="bossDetail(row.provider_binding)">{{ bossDetail(row.provider_binding) }}</small>
              <small v-if="row.provider_binding?.otp_required" class="au-warning">需使用账号和验证器登录</small>
              <small v-if="row.provider_url_placeholder" class="au-warning">入口域名待配置</small>
              <div v-if="canSeeProvider(row) || auth.hasPerm('audition:bind_provider')" class="au-actions">
                <Button size="xs" v-if="canSeeProvider(row)" variant="outline" @click="copyLink(row, 'provider')">陪玩链接</Button>
                <Button size="xs" v-if="auth.hasPerm('audition:bind_provider')" variant="outline" :disabled="busy || !!marker" @click="openBinding(row, 'provider')">陪玩绑定</Button>
              </div>
            </TableCell>
            <TableCell class="au-status"><Badge :variant="row.entry_status === 'ACTIVE' ? 'secondary' : 'outline'">{{ entryLabel(row.entry_status) }}</Badge><small>{{ row.expire_at ? '截止 ' + formatDateTime(row.expire_at) : '长期有效' }}</small></TableCell>
            <TableCell v-if="auth.hasPerm('audition:edit') || auth.hasPerm('audition:delete')" class="au-command"><div class="au-actions">
              <Button size="xs" v-if="auth.hasPerm('audition:edit')" variant="outline" :disabled="saving || !!marker" @click="onEdit(row)">编辑</Button>
              <Button size="xs" v-if="auth.hasPerm('audition:delete')" variant="outline" :disabled="saving || !!marker" @click="onDelete(row)">删除</Button>
            </div></TableCell>
          </TableRow></TableBody>
        </Table></div>
      </template>
    </SimplePagedList>
  </div>
  <Dialog :open="dialogVisible" @update:open="value => { if (!saving) dialogVisible = value }"><DialogContent class="au-dialog" :show-close-button="!saving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (saving) event.preventDefault() }"><DialogHeader><DialogTitle>{{ form.id ? '编辑试音链接' : '新增试音链接' }}</DialogTitle><DialogDescription>管理试音入口及有效时间。</DialogDescription></DialogHeader>
    <Alert v-if="saveUncertain"><AlertTitle>保存结果未确认</AlertTitle><AlertDescription>请关闭后刷新列表核对，原请求可能仍在处理，避免重复新建。</AlertDescription></Alert>
    <p v-if="notice" role="status">{{ notice }}</p>
    <div class="au-form"><label>标题<Input v-model="form.title" maxlength="100" placeholder="如：试音招募" :disabled="saving" /></label><label>备注<Textarea v-model="form.remark" maxlength="255" rows="2" :disabled="saving" /></label><label>截止时间 <Input v-model="form.expire_at" type="datetime-local" step="1" :disabled="saving" /><small>留空为长期有效</small></label><label class="au-switch" for="audition-active">启用<Switch id="audition-active" v-model="form.is_active" aria-label="启用" :disabled="saving" /></label></div>
    <p class="au-hint">新建后可管理老板入口；未绑定时老板链接不能用于登录。陪玩可使用陪玩账号登录后报名。</p><DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">取消</Button><Button size="xs" :disabled="saving || saveUncertain" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="bindingVisible" @update:open="value => { if (!bindingSaving) bindingVisible = value }"><DialogContent class="au-dialog" :show-close-button="!bindingSaving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (bindingSaving) event.preventDefault() }"><DialogHeader><DialogTitle>管理{{ roleLabel(bindingRole) }}入口</DialogTitle><DialogDescription>核对试音活动与绑定账号。</DialogDescription></DialogHeader>
    <template v-if="bindingRow">
      <dl class="au-details">
        <div><dt>试音活动</dt><dd>{{ bindingRow.title }} · #{{ bindingRow.id }}</dd></div>
        <div><dt>当前绑定</dt><dd>{{ bindingRole === 'provider' ? providerLabel(roleBinding(bindingRow, bindingRole)) : bindingLabel(roleBinding(bindingRow, bindingRole)) }} {{ bossDetail(roleBinding(bindingRow, bindingRole)) }}</dd></div>
        <div><dt>截止时间</dt><dd>{{ bindingRow.expire_at ? formatDateTime(bindingRow.expire_at) : '长期有效' }}</dd></div>
      </dl>
      <Alert><AlertTitle>持有{{ roleLabel(bindingRole) }}专属链接的人可能登录绑定账号</AlertTitle><AlertDescription>只交给账号本人；变更会使旧{{ roleLabel(bindingRole) }}链接失效，已签发的登录态不会自动撤销，另一端链接不变。</AlertDescription></Alert>
      <p v-if="bindingRole === 'provider'" class="au-hint">绑定不会限制其他已登录陪玩报名；原有报名记录保留。若启用陪玩验证器，需先登录后使用。</p>
      <label class="au-search">搜索{{ roleLabel(bindingRole) }}账号<Input v-model="searchText" :disabled="bindingSaving" placeholder="搜索编号、账号或昵称" @update:model-value="value => searchBosses(String(value))" /></label>
      <p v-if="searchLoading" role="status">正在查找账号…</p><p v-if="searchError" class="au-warning">账号读取失败 <Button size="xs" variant="link" @click="searchBosses(lastKeyword)">重试</Button></p>
      <label class="au-search">选择账号<NativeSelect :model-value="selectedBoss === null ? '' : String(selectedBoss)" :disabled="bindingSaving" @update:model-value="value => selectedBoss = value ? Number(value) : null"><NativeSelectOption value="">请选择账号</NativeSelectOption><NativeSelectOption v-for="item in bossOptions" :key="item.id" :value="String(item.id)">{{ item.nickname || item.username }} · {{ item.account_no }} · {{ item.username }}</NativeSelectOption></NativeSelect></label>
      <p v-if="notice" role="status">{{ notice }}</p><p class="au-hint">账号状态、链接有效期和部署地址会影响实际使用；状态以再次读取时为准。</p>
    </template><DialogFooter><Button size="xs" variant="outline" :disabled="bindingSaving" @click="bindingVisible = false">取消</Button><Button size="xs" v-if="bindingRow && roleBinding(bindingRow, bindingRole)?.kind !== 'unbound'" variant="outline" :disabled="bindingSaving" @click="confirmBinding(null)">解除绑定</Button><Button size="xs" :disabled="bindingSaving || !selectedBoss" @click="confirmBinding(selectedBoss)">{{ bindingSaving ? '保存中…' : '绑定账号' }}</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { reactive, ref, watch, onBeforeUnmount } from 'vue'
import { Alert, AlertTitle, AlertDescription } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Switch } from '@xa/shadcn-vue/switch'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { auditionApi } from '@/api/modules'
import { request } from '@/api/request'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const notice = ref(''), searchText = ref('')
const feedback = { warning: (text: string) => { notice.value = text }, error: (text: string) => { notice.value = text }, success: (text: string) => { notice.value = text }, info: (text: string) => { notice.value = text } }
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const auth = useAuthStore(), tableRef = ref(), query = reactive<Record<string, any>>({ keyword: '', is_active: '' })
const dialogVisible = ref(false), saving = ref(false), saveUncertain = ref(false), form = reactive<any>({})
const bindingVisible = ref(false), bindingRow = ref<any>(null), selectedBoss = ref<number | null>(null), bossOptions = ref<any[]>([])
type EntryRole = 'boss' | 'provider'
const bindingRole = ref<EntryRole>('boss')
const busy = ref(false), bindingSaving = ref(false), searchLoading = ref(false), searchError = ref(false), lastKeyword = ref('')
const marker = ref<{ id: number; title: string; role: EntryRole } | null>(null), checked = ref(false), checking = ref(false), checkText = ref('')
let ownerKey = '', sequence = 0, searchSequence = 0, disposed = false
function roleLabel(role: EntryRole) { return role === 'boss' ? '老板' : '陪玩' }
function roleBinding(row: any, role: EntryRole) { return role === 'boss' ? row?.boss_binding : row?.provider_binding }
function rolePermission(role: EntryRole) { return role === 'boss' ? 'audition:bind_boss' : 'audition:bind_provider' }
function bindingLabel(b: any) { return !b ? '状态待更新' : b.kind === 'unbound' ? '未绑定' : b.kind === 'legacy' ? '旧账号绑定' : b.available ? '已绑定' : '账号不可用' }
function providerLabel(b: any) { return !b ? '状态待更新' : b.kind === 'unbound' ? '普通报名' : b.kind === 'legacy' ? '旧账号专属' : b.available ? '账号专属' : '绑定账号不可用' }
function canSeeProvider(row: any) { return !!row.provider_binding && (row.provider_binding.kind === 'unbound' || auth.hasPerm('audition:bind_provider')) }
function bossDetail(b: any) { return b?.kind === 'account' ? `${b.name} · ${b.account_no} · ${b.username}` : b?.kind === 'legacy' ? `${b.name} · ${b.username}（使用时校验）` : '' }
function entryLabel(status: string) { return ({ ACTIVE: '启用', DISABLED: '停用', EXPIRED: '已过期' } as Record<string, string>)[status] || '状态待更新' }
function markerKey() { const id = auth.profile?.id; return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_audition_binding_review_${id}` : '' }
function restoreMarker() { const key = markerKey(); if (key === ownerKey) return; ownerKey = key; marker.value = null; checked.value = false; checkText.value = ''; if (key) try { const item = JSON.parse(sessionStorage.getItem(key) || 'null'); if (Number.isSafeInteger(item?.id) && item.id > 0 && typeof item.title === 'string') marker.value = { ...item, role: item.role === 'provider' ? 'provider' : 'boss' } } catch { /* In-page guard remains. */ } }
function beginMarker(item: { id: number; title: string; role: EntryRole }) { marker.value = item; checked.value = false; checkText.value = ''; const key = markerKey(); if (key) try { sessionStorage.setItem(key, JSON.stringify(item)) } catch { /* In-page guard remains. */ } }
function clearMarker() { const key = markerKey(); if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }; marker.value = null; checked.value = false; checkText.value = '' }
function localDateTime(value: string | null) { if (!value) return ''; const date = new Date(value); if (Number.isNaN(date.getTime())) return ''; const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000); return local.toISOString().slice(0, 19) }
function onCreate() { notice.value = ''; Object.assign(form, { id: 0, title: '', remark: '', expire_at: '', is_active: true }); saveUncertain.value = false; dialogVisible.value = true }
function onEdit(row: any) { notice.value = ''; Object.assign(form, { id: row.id, title: row.title, remark: row.remark, expire_at: localDateTime(row.expire_at), is_active: row.is_active }); saveUncertain.value = false; dialogVisible.value = true }
async function copyLink(row: any, role: 'boss' | 'provider') {
  if (marker.value) { feedback.warning('请先核对上次绑定结果'); return }
  const revision = auth.sessionRevision
  let current: any
  try { current = (await auditionApi.retrieve(row.id, true)).data }
  catch { if (revision === auth.sessionRevision) feedback.error('入口读取失败，请重试'); return }
  if (disposed || revision !== auth.sessionRevision || marker.value || !auth.hasPerm('audition:view') || (role === 'boss' && !auth.hasPerm('audition:bind_boss'))) return
  if (current.entry_status !== 'ACTIVE') { feedback.warning('入口未启用、已过期或状态待更新，请先核对'); return }
  if (role === 'boss' && (!current.boss_binding || current.boss_binding.kind === 'unbound' || current.boss_binding.available === false)) { feedback.warning('老板入口未绑定或账号不可用，请先核对'); return }
  if (role === 'provider' && !current.provider_binding) { feedback.warning('服务端版本不支持核对陪玩入口，请更新后重试'); return }
  if (role === 'provider' && current.provider_binding.kind !== 'unbound' && !auth.hasPerm('audition:bind_provider')) { feedback.warning('无权读取陪玩专属链接'); return }
  if ((role === 'boss' ? current.boss_url_placeholder : current.provider_url_placeholder) !== false) { feedback.warning('入口地址未配置或服务端版本不支持，请先核对部署配置'); return }
  if (typeof (role === 'boss' ? current.boss_url : current.provider_url) !== 'string' || !(role === 'boss' ? current.boss_url : current.provider_url)) { feedback.warning('链接不可读取，请核对权限'); return }
  if (role === 'provider' && current.provider_binding.kind !== 'unbound') {
    if (!await askConfirm({ title: '复制专属链接', description: `该链接可能登录陪玩账号「${current.provider_binding.name}」；其他已登录陪玩也可报名。${current.provider_binding.otp_required ? '当前需使用账号和验证器登录。' : '请仅交给账号本人。'}`, confirmText: '确认复制' })) return
    if (disposed || revision !== auth.sessionRevision || marker.value || !auth.hasPerm('audition:bind_provider')) return
  }
  try { await navigator.clipboard.writeText(role === 'boss' ? current.boss_url : current.provider_url); feedback.success('已复制，请仅交给预定使用人') }
  catch { feedback.error('复制失败，请检查浏览器剪贴板权限') }
}
async function onSave() {
  if (saving.value || saveUncertain.value) return
  if (!form.title?.trim()) { feedback.warning('请填写标题'); return }
  const revision = auth.sessionRevision; saving.value = true
  try { const payload = { title: form.title.trim(), remark: form.remark, expire_at: form.expire_at ? new Date(form.expire_at).toISOString() : null, is_active: form.is_active }; if (form.id) await auditionApi.update(form.id, payload, true); else await auditionApi.create(payload, true); if (revision !== auth.sessionRevision || disposed) return; feedback.success('保存成功'); dialogVisible.value = false; tableRef.value?.reload() }
  catch (error: any) { if (revision === auth.sessionRevision && !disposed) { if (!error?.response || error.response.status >= 500) saveUncertain.value = true; else feedback.error(error.response.data?.msg || '保存失败，请检查后重试') } }
  finally { if (revision === auth.sessionRevision && !disposed) saving.value = false }
}
async function onDelete(row: any) { if (!await askConfirm({ title: '删除试音链接', description: `删除「${row.title}」将使老板及陪玩链接失效；已有报名时不能删除，请停用以保留记录。已签发的登录态不会自动撤销。`, confirmText: '确认删除' })) return; try { await auditionApi.remove(row.id, true); feedback.success('已删除'); tableRef.value?.reload() } catch (error: any) { if (!error?.response || error.response.status >= 500) feedback.warning('删除结果未确认，请刷新列表核对'); else feedback.error(error.response.data?.msg || '删除失败，请核对是否已有报名') } }
async function openBinding(row: any, role: EntryRole) {
  if (busy.value || marker.value || !auth.hasPerm(rolePermission(role))) return
  const seq = ++sequence, revision = auth.sessionRevision; busy.value = true
  try { const res = await auditionApi.retrieve(row.id, true); if (disposed || seq !== sequence || revision !== auth.sessionRevision || !auth.hasPerm(rolePermission(role))) return; if (!roleBinding(res.data, role) || !res.data.updated_at) { feedback.error('服务端版本不支持入口绑定，请更新后重试'); return }; bindingRole.value = role; bindingRow.value = res.data; selectedBoss.value = null; bossOptions.value = []; searchText.value = ''; notice.value = ''; searchError.value = false; bindingVisible.value = true }
  catch { if (!disposed && seq === sequence && revision === auth.sessionRevision) feedback.error('链接读取失败，请刷新后重试') }
  finally { if (!disposed && seq === sequence && revision === auth.sessionRevision) busy.value = false }
}
async function searchBosses(keyword: string) {
  const seq = ++searchSequence, revision = auth.sessionRevision, role = bindingRole.value; lastKeyword.value = keyword; searchError.value = false
  if (!keyword.trim()) { bossOptions.value = bossOptions.value.filter(item => item.id === selectedBoss.value); searchLoading.value = false; return }
  searchLoading.value = true
  try { const res = await request({ url: role === 'boss' ? '/options/audition-bosses/' : '/options/audition-providers/', params: { keyword, page_size: 30 }, silent: true }); if (!disposed && seq === searchSequence && revision === auth.sessionRevision && bindingVisible.value && bindingRole.value === role) { const selected = bossOptions.value.filter(item => item.id === selectedBoss.value); bossOptions.value = [...new Map([...selected, ...res.data.list].map(item => [item.id, item])).values()] } }
  catch { if (!disposed && seq === searchSequence && revision === auth.sessionRevision && bindingVisible.value) searchError.value = true }
  finally { if (!disposed && seq === searchSequence && revision === auth.sessionRevision) searchLoading.value = false }
}
async function confirmBinding(id: number | null) {
  const row = bindingRow.value, revision = auth.sessionRevision, role = bindingRole.value, chosen = id === null ? null : bossOptions.value.find(item => item.id === id)
  if (!row || bindingSaving.value || marker.value || !auth.hasPerm(rolePermission(role)) || (id !== null && !chosen)) return
  if (id !== null && roleBinding(row, role)?.kind === 'account' && roleBinding(row, role).id === id) { feedback.info('该账号已绑定，无需重复操作'); return }
  const target = chosen ? `${chosen.nickname || chosen.username} · ${chosen.account_no} · ${chosen.username}` : '解除绑定'
  const impact = role === 'boss' ? '陪玩链接不变' : '老板链接与已有报名记录不变；其他已登录陪玩仍可报名'
  if (!await askConfirm({ title: id === null ? '确认解除绑定' : `确认绑定${roleLabel(role)}`, description: `试音「${row.title}」：${target}。变更后旧${roleLabel(role)}链接失效，已签发的登录态不会自动撤销；${impact}。`, cancelText: '返回' })) return
  if (disposed || revision !== auth.sessionRevision || !bindingVisible.value || bindingRow.value !== row || bindingRole.value !== role || !auth.hasPerm(rolePermission(role))) return
  bindingSaving.value = true; beginMarker({ id: row.id, title: row.title, role })
  try { await auditionApi.action(row.id, role === 'boss' ? 'bind-boss' : 'bind-provider', { account_id: id, expected_updated_at: row.updated_at }, true); if (disposed || revision !== auth.sessionRevision) return; clearMarker(); bindingVisible.value = false; feedback.success(`${roleLabel(role)}入口已更新，请重新复制链接`); tableRef.value?.load() }
  catch (error: any) { if (disposed || revision !== auth.sessionRevision) return; bindingVisible.value = false; if (error?.response?.status >= 400 && error.response.status < 500 && error.response.status !== 409) { clearMarker(); feedback.error(error.response.data?.msg || '绑定失败，请检查账号') } else { feedback.warning(error?.response?.status === 409 ? '链接已变化，请读取当前绑定' : '绑定结果未确认，请读取当前绑定'); tableRef.value?.load() } }
  finally { if (!disposed && revision === auth.sessionRevision) bindingSaving.value = false }
}
async function checkBinding() {
  const item = marker.value; if (!item || checking.value || bindingSaving.value) return
  const seq = ++sequence, revision = auth.sessionRevision; checking.value = true; checked.value = false; checkText.value = ''
  try { const res = await auditionApi.retrieve(item.id, true); if (!disposed && seq === sequence && revision === auth.sessionRevision && marker.value === item) { const state = roleBinding(res.data, item.role); checkText.value = `当前：${item.role === 'provider' ? providerLabel(state) : bindingLabel(state)} ${bossDetail(state)}；这是读取时快照。`; checked.value = true; tableRef.value?.load() } }
  catch (error: any) { if (!disposed && seq === sequence && revision === auth.sessionRevision && marker.value === item) { if (error?.response?.status === 404) { checkText.value = '当前链接不存在；原请求可能仍在处理。'; checked.value = true } else checkText.value = '读取失败，请重试。' } }
  finally { if (!disposed && seq === sequence && revision === auth.sessionRevision) checking.value = false }
}
async function clearAfterReview() { const item = marker.value, revision = auth.sessionRevision; if (!item || !checked.value || checking.value) return; if (!await askConfirm({ title: '解除待核对限制', description: '当前绑定是读取时快照，原请求仍可能处理。确认已核对并解除限制？', confirmText: '已核对，解除', cancelText: '继续核对' })) return; if (!disposed && revision === auth.sessionRevision && marker.value === item && checked.value) clearMarker() }
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch(() => auth.sessionRevision, () => { ++sequence; ++searchSequence; busy.value = false; saving.value = false; bindingSaving.value = false; checking.value = false; bindingVisible.value = false; dialogVisible.value = false; bossOptions.value = [] })
watch(bindingVisible, visible => { if (!visible) { ++searchSequence; searchLoading.value = false } })
onBeforeUnmount(() => { disposed = true; ++sequence; ++searchSequence })
</script>

<style scoped>
.au-page { display:grid; min-width:0; gap:12px; container:auditions / inline-size; }
.au-header { display:flex; flex-flow:row wrap; align-items:center; justify-content:space-between; gap:8px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.au-header h2 { margin:0; font-size:16px; font-weight:600; }
.au-header p,.au-hint { color:var(--muted-foreground); font-size:12px; line-height:1.5; }
.au-header p { margin-top:4px; }
.au-filters { display:flex; flex-wrap:wrap; align-items:end; gap:8px; padding:12px; background:var(--card); border:1px solid var(--border); border-radius:8px; }
.au-keyword { flex:1 1 200px; max-width:400px; }
.au-filters label,.au-form label,.au-search { min-width:0; display:grid; gap:4px; font-size:12px; }
.au-filters :deep(input),.au-filters :deep(select),.au-form :deep(input),.au-search :deep(input),.au-search :deep(select) { width:100%; min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.au-filters :deep([data-slot='native-select-wrapper']),.au-search :deep([data-slot='native-select-wrapper']) { width:100%; min-width:0; }
.au-filters :deep(select),.au-search :deep(select) { padding-right:32px; }
.au-records { min-width:0; background:var(--card); }
.au-records :deep(th),.au-records :deep(td) { white-space:normal; overflow-wrap:anywhere; padding:8px; }
.au-records :deep(th) { background:var(--muted); font-size:12px; }
.au-records :deep(td) { vertical-align:top; font-size:13px; }
.au-records small { display:block; margin-top:4px; font-size:12px; line-height:1.4; color:var(--muted-foreground); }
.au-remark,.au-details dd { white-space:pre-wrap; }
.au-actions { display:flex; flex-wrap:wrap; gap:4px; }
.au-entry .au-actions { margin-top:8px; }
.au-records .au-warning,.au-warning { color:var(--warning-foreground,var(--muted-foreground)); }
.au-dialog { max-height:calc(100dvh - 24px); overflow-y:auto; padding:16px; gap:12px; }
.au-dialog :deep([data-slot='dialog-title']) { padding-right:32px; }
.au-form { display:grid; gap:12px; }
.au-form small { color:var(--muted-foreground); }
.au-form .au-switch { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:4px 8px; border:1px solid var(--border); border-radius:8px; cursor:pointer; }
.au-details { display:grid; }
.au-details div { display:grid; grid-template-columns:64px minmax(0,1fr); gap:12px; padding:8px 0; border-bottom:1px solid var(--border); font-size:13px; }
.au-details dt { color:var(--muted-foreground); } .au-details dd { overflow-wrap:anywhere; margin:0; }
@container auditions (max-width:820px) {
  .au-page :deep(.table-scroll),.au-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .au-records { background:transparent; }
  .au-records :deep(table),.au-records :deep(tbody) { display:grid; }
  .au-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .au-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .au-records :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
  .au-records :deep(td) { padding:0; }
  .au-title { grid-column:1; grid-row:1; }
  .au-status { grid-column:2; grid-row:1; text-align:right; }
  .au-records .au-entry { padding-top:8px; border-top:1px solid var(--border); }
  .au-entry::before { content:attr(data-label); display:block; margin-bottom:4px; color:var(--muted-foreground); font-size:12px; }
  .au-command { grid-column:1/-1; }
  .au-page :deep(.pages) { justify-content:flex-start; }
}
@container auditions (max-width:560px) { .au-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@container auditions (max-width:440px) { .au-keyword { max-width:none; flex-basis:100%; } }
@media(pointer:coarse) {
  .au-page :deep([data-slot='button']),.au-dialog :deep([data-slot='button']) { min-height:44px; min-width:44px; }
  .au-filters :deep(input),.au-filters :deep(select),.au-form :deep(input),.au-search :deep(input),.au-search :deep(select),.au-form .au-switch { min-height:44px; }
}
</style>
