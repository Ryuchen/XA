<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Switch } from '@xa/shadcn-vue/switch'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { roleApi, permissionApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

interface PermGroup { group: string; label: string; permissions: [string, string][] }
const auth = useAuthStore(), tableRef = ref(), groups = ref<PermGroup[]>([]), permissionsLoaded = ref(false), permissionsLoading = ref(false), permissionsError = ref(false)
const dialogVisible = ref(false), saving = ref(false), deleting = ref(false), notice = ref(''), formNotice = ref('')
const form = reactive<any>({ permissions: [] })
const canManage = computed(() => auth.hasPerm('role:edit') || auth.hasPerm('role:delete'))
const protectedRole = computed(() => Boolean(form.id) && form.code === 'super_admin')
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
async function loadPermissions() { permissionsLoading.value = true; permissionsError.value = false; try { const res = await permissionApi.tree() as any; groups.value = res.data || []; permissionsLoaded.value = true } catch { permissionsError.value = true } finally { permissionsLoading.value = false } }
onMounted(loadPermissions)
function blank() { return { id: 0, name: '', code: '', description: '', sort_order: 0, is_active: true, permissions: [] as string[] } }
function selectedInGroup(group: PermGroup) { return group.permissions.filter(([code]) => form.permissions.includes(code)).length }
function groupState(group: PermGroup) { const count = selectedInGroup(group); return count === 0 ? false : count === group.permissions.length ? true : 'indeterminate' }
function toggleGroup(group: PermGroup, checked: boolean) { const codes = group.permissions.map(p => p[0]); form.permissions = checked ? Array.from(new Set([...form.permissions, ...codes])) : form.permissions.filter((code: string) => !codes.includes(code)) }
function togglePermission(code: string, checked: boolean) { form.permissions = checked ? Array.from(new Set([...form.permissions, code])) : form.permissions.filter((item: string) => item !== code) }
function onCreate() { Object.assign(form, blank()); formNotice.value = ''; dialogVisible.value = true }
function onEdit(row: any) { Object.assign(form, { ...blank(), ...row, permissions: [...(row.permissions || [])] }); formNotice.value = ''; dialogVisible.value = true }
async function onSave() {
  if (saving.value || !permissionsLoaded.value || !auth.hasPerm('role:edit')) return
  if (!form.name?.trim() || !form.code?.trim()) { formNotice.value = '请填写角色名称和标识'; return }
  if (!Number.isInteger(form.sort_order) || form.sort_order < 0) { formNotice.value = '排序须为非负整数'; return }
  saving.value = true; formNotice.value = ''
  try { const payload = { name: form.name, code: form.code, description: form.description, sort_order: form.sort_order, is_active: form.is_active, permissions: form.permissions }; if (form.id) await roleApi.update(form.id, payload); else await roleApi.create(payload); notice.value = '保存成功'; dialogVisible.value = false; tableRef.value.reload() }
  catch (error) { formNotice.value = error instanceof Error ? error.message : '保存失败，请重试' }
  finally { saving.value = false }
}
async function onDelete(row: any) {
  if (deleting.value || row.code === 'super_admin' || row.member_count > 0 || !auth.hasPerm('role:delete')) return
  if (!await askConfirm({ title: '删除角色', description: `确定删除角色「${row.name}」吗？`, confirmText: '删除', cancelText: '取消' })) return
  deleting.value = true
  try { await roleApi.remove(row.id); notice.value = '已删除'; tableRef.value.reload() } catch (error) { notice.value = error instanceof Error ? error.message : '删除失败，请重试' } finally { deleting.value = false }
}
</script>
<template>
  <div class="role-page">
    <Card class="role-panel">
      <header class="role-heading">
        <div><h2>后台角色</h2><p class="muted">查看角色的功能权限和成员数。</p></div>
        <Button v-if="auth.hasPerm('role:edit')" size="xs" @click="onCreate">新增角色</Button>
      </header>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <SimplePagedList ref="tableRef" :fetcher="roleApi.list">
        <template #default="{ rows }">
          <Table>
            <TableHeader><TableRow>
              <TableHead>角色名称</TableHead><TableHead>标识</TableHead><TableHead>权限数</TableHead>
              <TableHead>成员数</TableHead><TableHead>状态</TableHead><TableHead v-if="canManage">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell class="role-name"><h3>{{ row.name }}</h3><p v-if="row.description" class="description">{{ row.description }}</p><p class="muted">ID {{ row.id }}</p></TableCell>
              <TableCell class="role-code" data-label="标识"><span>{{ row.code }}</span></TableCell>
              <TableCell data-label="权限数">{{ row.permissions?.length || 0 }}</TableCell>
              <TableCell data-label="成员数">{{ row.member_count }}</TableCell>
              <TableCell data-label="状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
              <TableCell v-if="canManage" class="role-actions"><div class="actions">
                <Button v-if="auth.hasPerm('role:edit')" size="xs" variant="outline" @click="onEdit(row)">编辑</Button>
                <Button v-if="auth.hasPerm('role:delete')" size="xs" variant="outline" :disabled="deleting || row.code === 'super_admin' || row.member_count > 0" @click="onDelete(row)">删除</Button>
              </div>
              <p v-if="auth.hasPerm('role:delete') && row.code === 'super_admin'" class="muted">内置角色，无法删除</p>
              <p v-else-if="auth.hasPerm('role:delete') && row.member_count > 0" class="muted">仍有成员，无法删除</p>
              </TableCell>
            </TableRow></TableBody>
          </Table>
        </template>
      </SimplePagedList>
    </Card>

    <Dialog :open="dialogVisible" @update:open="value => { if (!saving) dialogVisible = value }">
      <DialogContent :show-close-button="!saving" class="role-dialog">
        <DialogHeader><DialogTitle>{{ form.id ? '编辑角色' : '新增角色' }}</DialogTitle><DialogDescription>配置角色信息和功能权限。</DialogDescription></DialogHeader>
        <div class="editor">
          <div class="fields">
            <label>角色名称<Input v-model="form.name" :disabled="saving" /></label>
            <label>标识<Input v-model="form.code" :disabled="!!form.id || saving" placeholder="如 operator" /></label>
            <label class="full">描述<Input v-model="form.description" :disabled="saving" /></label>
            <label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" :disabled="saving" /></label>
            <label class="enabled"><span>启用</span><Switch v-model="form.is_active" aria-label="启用" :disabled="saving || protectedRole" /></label>
          </div>
          <div class="permission-heading"><h3>功能权限</h3><span class="muted">已选 {{ form.permissions.length }} 项</span></div>
          <p v-if="protectedRole" class="feedback">内置超级管理员角色保持启用，其权限受保护。</p>
          <Alert v-if="permissionsError" variant="destructive"><AlertDescription>权限读取失败 <Button size="xs" variant="outline" @click="loadPermissions">重新读取</Button></AlertDescription></Alert>
          <p v-else-if="permissionsLoading" role="status">正在读取权限…</p>
          <div class="perm-tree">
            <details v-for="(group,index) in groups" :key="group.group" class="perm-group" :open="index === 0">
              <summary><span>{{ group.label }}</span><span class="group-count">已选 {{ selectedInGroup(group) }} / {{ group.permissions.length }}</span></summary>
              <div class="group-content">
                <label class="group-checkbox">
                  <Checkbox :model-value="groupState(group)" :disabled="saving || protectedRole" @update:model-value="value => toggleGroup(group, value === true)" />
                  全选{{ group.label }}权限
                </label>
                <div class="perm-items">
                  <label v-for="permission in group.permissions" :key="permission[0]">
                    <Checkbox :model-value="form.permissions.includes(permission[0])" :disabled="saving || protectedRole" @update:model-value="value => togglePermission(permission[0], value === true)" />
                    <span>{{ permission[1] }}</span>
                  </label>
                </div>
              </div>
            </details>
          </div>
          <p v-if="formNotice" role="alert" class="feedback">{{ formNotice }}</p>
        </div>
        <DialogFooter>
          <Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">取消</Button>
          <Button size="xs" :disabled="saving || !permissionsLoaded" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<style scoped>
.role-page { min-width:0; container:roles / inline-size; }
.role-panel { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.role-heading,.permission-heading,.actions,.enabled,.perm-items label,.group-checkbox { display:flex; align-items:center; gap:8px; }
.role-heading,.permission-heading,.enabled { justify-content:space-between; } .actions { flex-wrap:wrap; }
h2,h3,p { margin:0; } h2 { font-size:16px; line-height:24px; font-weight:600; } h3 { font-size:14px; line-height:20px; font-weight:600; }
.muted,.group-count,.description,.feedback,.fields label,.perm-tree { font-size:12px; line-height:16px; }
.muted,.description,.group-count { color:var(--muted-foreground); }
.role-name p,.role-actions p { margin-top:4px; }
.description { white-space:pre-wrap; overflow-wrap:anywhere; }
.feedback { padding:8px 12px; border-radius:8px; background:var(--muted); overflow-wrap:anywhere; }
.role-panel :deep(th),.role-panel :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.role-panel :deep(th) { white-space:nowrap; }
.role-code span { font:12px/16px ui-monospace,monospace; }
.role-page :deep(select),.fields :deep(input) { min-height:28px; height:28px; padding-block:4px; font-size:12px; }
:global(.role-dialog) { width:min(640px,calc(100vw - 24px)); max-height:calc(100dvh - 32px); display:flex; flex-direction:column; overflow:hidden; }
.editor { min-height:0; min-width:0; display:grid; gap:12px; overflow:auto; }
.fields,.fields label,.perm-tree,.group-content,.perm-items { display:grid; min-width:0; gap:4px; }
.fields { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; align-items:end; }
.fields .enabled { display:flex; min-height:28px; padding:4px 8px; border:1px solid var(--border); border-radius:8px; }
.full { grid-column:1/-1; }
.perm-group { min-width:0; padding:0 8px; border:1px solid var(--border); border-radius:8px; }
.perm-group summary { display:list-item; min-height:28px; align-content:center; padding:4px 0; cursor:pointer; font-size:13px; line-height:20px; font-weight:600; }
.perm-group summary:focus-visible { outline:2px solid var(--ring); outline-offset:2px; }
.group-count { margin-left:8px; font-weight:400; }
.group-content { gap:4px; padding:4px 0 8px; border-top:1px solid var(--border); }
.group-checkbox { font-weight:600; }
.group-checkbox :deep([data-state='indeterminate'][data-slot='checkbox']) { display:grid; place-items:center; border-color:var(--primary); }
.group-checkbox :deep([data-state='indeterminate'][data-slot='checkbox'])::before { content:''; width:8px; height:2px; background:var(--primary); }
.group-checkbox :deep([data-state='indeterminate'] svg) { display:none; }
.perm-items { grid-template-columns:repeat(2,minmax(0,1fr)); }
.perm-items label,.group-checkbox { min-width:0; padding:4px; overflow-wrap:anywhere; }
@container roles (max-width:800px) {
 .role-panel { padding:12px; }
 .role-panel :deep(.paged-list) { min-width:0; grid-template-columns:minmax(0,1fr); }
 .role-panel :deep(.table-scroll),.role-panel :deep([data-slot='table-container']) { overflow:visible; border:0; }
 .role-panel :deep(table),.role-panel :deep(tbody) { display:grid; }
 .role-panel :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
 .role-panel :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .role-panel :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; min-width:0; gap:4px 8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
 .role-panel :deep(td) { padding:0; }
 .role-panel :deep(td[data-label]) { display:flex; align-items:baseline; flex-wrap:wrap; gap:4px; font-size:12px; }
 .role-panel :deep(td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
 .role-name,.role-code,.role-actions { grid-column:1/-1; }
 .role-name,.role-actions { margin-block:4px; }
 .role-panel :deep(.pages) { justify-content:flex-start; }
}
@container roles (max-width:480px) { .role-panel :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(max-width:480px) { .fields { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .role-page :deep(button),.role-page :deep(select),.fields :deep(input),.fields .enabled,.perm-items label,.group-checkbox,.perm-group summary { min-height:44px; } .role-page :deep(button) { min-width:44px; } }
</style>
