<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Switch } from '@xa/shadcn-vue/switch'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { adminApi, roleApi } from '@/api/modules'
import { amountToXaCoin } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore(), tableRef = ref(), query = reactive({ keyword: '' })
const roles = ref<any[]>([]), rolesLoaded = ref(false), rolesLoading = ref(false), rolesError = ref(false)
const dialogVisible = ref(false), saving = ref(false), deleting = ref(false), notice = ref(''), formNotice = ref(''), form = reactive<any>({})
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
async function loadRoles() { rolesLoading.value = true; rolesError.value = false; try { const res = await roleApi.allOptions() as any; roles.value = res.data?.list || []; rolesLoaded.value = true } catch { rolesError.value = true } finally { rolesLoading.value = false } }
onMounted(loadRoles)
function blank() { return { id: 0, username: '', password: '', nickname: '', phone: '', remark: '', roles: [] as number[], is_active: true, is_superuser: false } }
function onCreate() { Object.assign(form, blank()); formNotice.value = ''; dialogVisible.value = true }
function onEdit(row: any) { Object.assign(form, { ...blank(), ...row, phone: row.phone ?? '', roles: [...(row.role_ids || [])] }); formNotice.value = ''; dialogVisible.value = true }
function toggleRole(id: number, checked: boolean) { form.roles = checked ? [...new Set([...form.roles, id])] : form.roles.filter((item: number) => item !== id) }
async function onSave() {
  if (saving.value || !rolesLoaded.value || !auth.hasPerm('admin:edit')) return
  if (!form.roles?.length) { formNotice.value = '请选择职位'; return }
  if (!form.id && (!form.username || !form.password)) { formNotice.value = '请填写账号和密码'; return }
  saving.value = true; formNotice.value = ''
  try { const payload = { nickname: form.nickname, phone: form.phone, remark: form.remark, roles: form.roles }; if (form.id) await adminApi.update(form.id, { ...payload, is_active: form.is_active }); else await adminApi.create({ ...payload, username: form.username, password: form.password }); notice.value = '保存成功'; dialogVisible.value = false; form.password = ''; tableRef.value.reload() }
  catch (error) {
    const failure = error as {response?: {data?: {msg?: string}}; msg?: string; message?: string}
    formNotice.value = failure.response?.data?.msg || failure.msg || failure.message || '保存失败，请重试'
  }
  finally { saving.value = false }
}
async function onDelete(row: any) {
  if (deleting.value || row.is_superuser || !auth.hasPerm('admin:edit')) return
  if (!await askConfirm({ title: '删除客服', description: `确定删除客服「${row.nickname || row.username}」吗？`, confirmText: '删除', cancelText: '取消' })) return
  deleting.value = true
  try { await adminApi.remove(row.id); notice.value = '已删除'; tableRef.value.reload() } catch (error) { notice.value = error instanceof Error ? error.message : '删除失败，请重试' } finally { deleting.value = false }
}
</script>
<template>
  <div class="staff-page">
    <Card class="staff-panel">
      <header class="heading"><div><h2>客服账号</h2><p class="muted">查看职位分配、在职状态和今日派单金额。</p></div>
        <Button v-if="auth.hasPerm('admin:edit')" size="xs" @click="onCreate">新增客服</Button>
      </header>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <SimplePagedList ref="tableRef" :fetcher="adminApi.list" :query="query">
        <template #toolbar="{ reload }">
          <form class="filter" @submit.prevent="reload()">
            <label>搜索账号<Input v-model="query.keyword" aria-label="搜索账号、昵称或手机号" placeholder="账号、昵称或手机号" /></label>
            <Button size="xs" type="submit">查询</Button>
          </form>
        </template>
        <template #default="{ rows }">
          <Table>
            <TableHeader><TableRow><TableHead>客服</TableHead><TableHead>职位</TableHead><TableHead>手机号</TableHead>
              <TableHead>今日派单额</TableHead><TableHead>超管</TableHead><TableHead>在职状态</TableHead><TableHead v-if="auth.hasPerm('admin:edit')">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell class="staff-identity"><h3>{{ row.nickname || row.username }}</h3><p class="muted">账号 {{ row.username }} · ID {{ row.id }}</p><p v-if="row.remark" class="remark">{{ row.remark }}</p></TableCell>
              <TableCell class="staff-roles" data-label="职位"><div class="badges"><Badge v-for="name in row.role_names" :key="name" variant="outline">{{ name }}</Badge><span v-if="!row.role_names?.length">—</span></div></TableCell>
              <TableCell class="staff-phone" data-label="手机号">{{ row.phone || '—' }}</TableCell>
              <TableCell data-label="今日派单额"><span>{{ amountToXaCoin(row.today_dispatch_amount) }} 兴安币</span></TableCell>
              <TableCell data-label="超管">{{ row.is_superuser ? '超管' : '否' }}</TableCell>
              <TableCell data-label="在职状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '在职' : '离职' }}</Badge></TableCell>
              <TableCell v-if="auth.hasPerm('admin:edit')" class="staff-actions"><div class="actions">
                <Button size="xs" variant="outline" @click="onEdit(row)">编辑</Button>
                <Button size="xs" variant="outline" :disabled="deleting || row.is_superuser" @click="onDelete(row)">删除</Button>
              </div></TableCell>
            </TableRow></TableBody>
          </Table>
        </template>
      </SimplePagedList>
    </Card>

    <Dialog :open="dialogVisible" @update:open="value => { if (!saving) { dialogVisible = value; if (!value) form.password = '' } }">
      <DialogContent class="staff-dialog" :show-close-button="!saving">
        <DialogHeader><DialogTitle>{{ form.id ? '编辑客服' : '新增客服' }}</DialogTitle><DialogDescription>设置账号资料、职位和在职状态。</DialogDescription></DialogHeader>
        <div class="editor">
          <div class="fields">
            <label>账号<Input v-model="form.username" maxlength="150" :disabled="!!form.id || saving" /></label>
            <label v-if="!form.id">密码<Input v-model="form.password" type="password" maxlength="128" autocomplete="new-password" :disabled="saving" /></label>
            <label>昵称<Input v-model="form.nickname" maxlength="50" :disabled="saving" /></label>
            <label>手机号<Input v-model="form.phone" maxlength="20" :disabled="saving" /></label>
            <label class="full">备注<Textarea v-model="form.remark" :rows="2" maxlength="255" placeholder="选填" :disabled="saving" /></label>
          </div>
          <section class="roles-block">
            <div class="role-heading"><h3>职位</h3><span class="muted">已选 {{ form.roles.length }} 项</span></div>
            <p v-if="form.is_superuser" class="muted">超级管理员的职位和在职状态受保护。</p>
            <Alert v-if="rolesError" variant="destructive"><AlertDescription>职位读取失败 <Button size="xs" variant="outline" @click="loadRoles">重新读取</Button></AlertDescription></Alert>
            <p v-else-if="rolesLoading" role="status">正在读取职位…</p>
            <div class="role-options">
              <label v-for="role in roles" :key="role.id"><Checkbox :model-value="(form.roles || []).includes(role.id)" :disabled="saving || form.is_superuser" @update:model-value="value => toggleRole(role.id, value === true)" /><span>{{ role.name }}</span></label>
            </div>
          </section>
          <label v-if="form.id" class="active-control"><span>在职状态</span><Switch v-model="form.is_active" aria-label="在职状态" :disabled="saving || form.is_superuser" /></label>
          <p v-if="formNotice" role="alert" class="feedback">{{ formNotice }}</p>
        </div>
        <DialogFooter>
          <Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false; form.password = ''">取消</Button>
          <Button size="xs" :disabled="saving || !rolesLoaded" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<style scoped>
.staff-page { min-width:0; container:staff / inline-size; }
.staff-panel { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.heading,.role-heading,.actions,.badges,.active-control,.role-options label,.filter { display:flex; align-items:center; gap:8px; }
.heading,.role-heading,.active-control { justify-content:space-between; } .actions,.badges { flex-wrap:wrap; }
h2,h3,p { margin:0; } h2 { font-size:16px; line-height:24px; font-weight:600; } h3 { font-size:14px; line-height:20px; font-weight:600; }
.muted,.remark,.feedback,.fields label,.filter label,.active-control,.role-options { font-size:12px; line-height:16px; }
.muted,.remark { color:var(--muted-foreground); } .staff-identity p { margin-top:4px; }
.remark { white-space:pre-wrap; overflow-wrap:anywhere; }
.feedback { padding:8px 12px; border-radius:8px; background:var(--muted); overflow-wrap:anywhere; }
.filter { align-items:end; }
.filter label { width:min(260px,100%); display:grid; gap:4px; min-width:0; }
.staff-panel :deep(input),.staff-panel :deep(select),.fields :deep(input) { min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.staff-panel :deep(th),.staff-panel :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.staff-panel :deep(th) { white-space:nowrap; }
.badges { gap:4px; } .badges :deep([data-slot='badge']) { max-width:100%; white-space:normal; text-align:left; }
:global(.staff-dialog) { width:min(640px,calc(100vw - 24px)); max-height:calc(100dvh - 32px); display:flex; flex-direction:column; overflow:hidden; }
.editor,.fields,.fields label,.roles-block,.role-options { display:grid; min-width:0; gap:4px; }
.editor { min-height:0; overflow:auto; gap:12px; }
.fields,.role-options { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.full { grid-column:1/-1; }
.role-options label,.active-control { min-width:0; min-height:28px; padding:4px 8px; border:1px solid var(--border); border-radius:8px; overflow-wrap:anywhere; }
.role-options label:has([data-state='checked']) { background:var(--muted); }
.roles-block { gap:8px; }
@container staff (max-width:900px) {
 .staff-panel { padding:12px; } .staff-panel :deep(.paged-list) { min-width:0; grid-template-columns:minmax(0,1fr); }
 .staff-panel :deep(.table-scroll),.staff-panel :deep([data-slot='table-container']) { overflow:visible; border:0; }
 .staff-panel :deep(table),.staff-panel :deep(tbody) { display:grid; }
 .staff-panel :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
 .staff-panel :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .staff-panel :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; min-width:0; gap:4px 8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
 .staff-panel :deep(td) { padding:0; }
 .staff-panel :deep(td[data-label]) { display:flex; align-items:baseline; flex-wrap:wrap; gap:4px; font-size:12px; }
 .staff-panel :deep(td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
 .staff-identity,.staff-roles,.staff-phone,.staff-actions { grid-column:1/-1; } .staff-identity { margin-bottom:4px; } .staff-actions { margin-top:4px; }
 .staff-panel :deep(.pages) { justify-content:flex-start; }
}
@container staff (max-width:480px) { .staff-panel :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(max-width:480px) { .fields { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .staff-panel :deep(button),.staff-panel :deep(input),.staff-panel :deep(select),.fields :deep(input),.role-options label,.active-control { min-height:44px; } .staff-panel :deep(button) { min-width:44px; } }
</style>
