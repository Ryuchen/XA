<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Switch } from '@xa/shadcn-vue/switch'
import { Badge } from '@xa/shadcn-vue/badge'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { announcementApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

type Announcement = {id:number;title:string;content:string;is_pinned:boolean;sort_order:number;is_active:boolean}
const auth = useAuthStore(), rows = ref<Announcement[]>([]), total = ref(0), page = ref(1), pageSize = ref('10'), loading = ref(false), saving = ref(false), error = ref(''), notice = ref(''), dialogVisible = ref(false)
const editorNotice = ref('')
const form = reactive<Announcement>({id:0,title:'',content:'',is_pinned:false,sort_order:0,is_active:true})
const { open:confirmOpen, options:confirmOptions, ask:askConfirm, confirm:confirmYes, cancel:confirmNo } = useConfirmDialog()
let sequence = 0, alive = true
const canManage = computed(() => auth.hasPerm('announcement:edit') || auth.hasPerm('announcement:delete'))
const pages = computed(() => Math.max(1,Math.ceil(total.value / Number(pageSize.value))))
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  try { const result = await announcementApi.list({page:page.value,page_size:Number(pageSize.value)}); if (alive && current === sequence) { rows.value = result.data.list || []; total.value = result.data.total || 0 } }
  catch (cause:any) { if (!alive || current !== sequence) return; if (cause?.response?.status === 404 && page.value > 1) { page.value = 1; await load(); return }; rows.value = []; total.value = 0; error.value = '公告加载失败，请重试' }
  finally { if (alive && current === sequence) loading.value = false }
}
function create() { editorNotice.value = ''; Object.assign(form,{id:0,title:'',content:'',is_pinned:false,sort_order:0,is_active:true}); dialogVisible.value = true }
function edit(row:Announcement) { editorNotice.value = ''; Object.assign(form,row); dialogVisible.value = true }
async function save() {
  if (saving.value || !auth.hasPerm('announcement:edit')) return
  if (!form.title.trim()) { editorNotice.value = '请填写标题'; return }
  saving.value = true; editorNotice.value = ''; notice.value = ''
  try { const payload = {title:form.title,content:form.content,is_pinned:form.is_pinned,sort_order:form.sort_order,is_active:form.is_active}; if (form.id) await announcementApi.update(form.id,payload); else await announcementApi.create(payload); notice.value = '保存成功'; dialogVisible.value = false; page.value = 1; await load() }
  catch (cause) { editorNotice.value = cause instanceof Error ? cause.message : '保存失败，请重试' }
  finally { saving.value = false }
}
async function remove(row:Announcement) {
  if (!auth.hasPerm('announcement:delete') || !await askConfirm({title:'删除公告',description:`确定删除公告「${row.title}」吗？`,confirmText:'删除',cancelText:'取消'})) return
  try { await announcementApi.remove(row.id); notice.value = '已删除'; await load() } catch (cause) { notice.value = cause instanceof Error ? cause.message : '删除失败，请重试' }
}
function changePage(next:number) { if (!loading.value && next >= 1 && next <= pages.value) { page.value = next; void load() } }
function changeSize(value:unknown) { const size = String(value); if (['10','20','50','100'].includes(size)) { pageSize.value = size; page.value = 1; void load() } }
onMounted(() => { void load() }); onUnmounted(() => { alive = false; sequence++ })
</script>
<template>
  <div class="announcement-page">
    <Card class="announcements-card">
      <header class="heading">
        <div><h2>平台公告</h2><p v-if="!loading && !error" class="muted">共 {{ total }} 条</p></div>
        <Button v-if="auth.hasPerm('announcement:edit')" size="xs" @click="create">新增公告</Button>
      </header>
      <p v-if="notice && !dialogVisible" role="status" class="feedback">{{ notice }}</p>
      <div v-if="error" role="alert" class="feedback">{{ error }} <Button size="xs" variant="outline" @click="load">重新加载</Button></div>
      <p v-else-if="loading" role="status" class="muted">正在加载公告…</p>
      <p v-else-if="!rows.length" class="empty">暂无公告</p>
      <template v-else>
        <div class="records" role="region" aria-label="公告列表">
          <Table>
            <TableHeader><TableRow>
              <TableHead>ID</TableHead><TableHead>公告标题与正文</TableHead><TableHead>置顶</TableHead>
              <TableHead>排序</TableHead><TableHead>状态</TableHead><TableHead v-if="canManage">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell class="record-id" data-label="ID">{{ row.id }}</TableCell>
              <TableCell class="record-copy">
                <h3>{{ row.title }}</h3>
                <details v-if="row.content" class="content"><summary>查看正文原文</summary><p>{{ row.content }}</p></details>
                <span v-else class="muted">暂无正文</span>
              </TableCell>
              <TableCell class="record-pin" data-label="置顶"><Badge v-if="row.is_pinned" variant="secondary">置顶</Badge><span v-else class="muted">否</span></TableCell>
              <TableCell class="record-order" data-label="排序">{{ row.sort_order }}</TableCell>
              <TableCell class="record-status" data-label="状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
              <TableCell v-if="canManage" class="record-actions"><div class="actions">
                <Button v-if="auth.hasPerm('announcement:edit')" size="xs" variant="outline" @click="edit(row)">编辑</Button>
                <Button v-if="auth.hasPerm('announcement:delete')" size="xs" variant="outline" @click="remove(row)">删除</Button>
              </div></TableCell>
            </TableRow></TableBody>
          </Table>
        </div>
        <nav class="pagination" aria-label="公告翻页">
          <span class="muted">第 {{ page }} / {{ pages }} 页</span>
          <NativeSelect :model-value="pageSize" aria-label="每页条数" @update:model-value="changeSize">
            <NativeSelectOption v-for="size in ['10','20','50','100']" :key="size" :value="size">{{ size }} 条/页</NativeSelectOption>
          </NativeSelect>
          <Button size="xs" variant="outline" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</Button>
          <Button size="xs" variant="outline" :disabled="page >= pages || loading" @click="changePage(page + 1)">下一页</Button>
        </nav>
      </template>
    </Card>

    <Dialog v-model:open="dialogVisible">
      <DialogContent class="announcement-editor">
        <DialogHeader>
          <DialogTitle>{{ form.id ? '编辑公告' : '新增公告' }}</DialogTitle>
          <DialogDescription>设置正文、排序和老板端展示状态。</DialogDescription>
        </DialogHeader>
        <div class="editor-fields">
          <label class="field">标题<Input v-model="form.title" :disabled="saving" /></label>
          <label class="field">内容<Textarea v-model="form.content" :rows="5" :disabled="saving" /></label>
          <label class="field">排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" :disabled="saving" /></label>
          <div class="options">
            <label class="option"><span>置顶</span><Switch v-model="form.is_pinned" aria-label="置顶" :disabled="saving" /></label>
            <label class="option"><span>启用</span><Switch v-model="form.is_active" aria-label="启用" :disabled="saving" /></label>
          </div>
          <p v-if="editorNotice" role="alert" class="feedback">{{ editorNotice }}</p>
        </div>
        <DialogFooter>
          <Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">取消</Button>
          <Button size="xs" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<style scoped>
.announcement-page { min-width:0; container:announcements / inline-size; }
.announcements-card { display:grid; min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.heading,.actions,.pagination,.feedback,.option { display:flex; gap:8px; align-items:center; }
.heading,.option { justify-content:space-between; }
h2,h3,p { margin:0; } h2 { font-size:16px; line-height:24px; } h3 { font-size:14px; line-height:20px; font-weight:600; overflow-wrap:anywhere; }
.muted,.pagination,.content summary,.field,.option,.empty,.feedback { font-size:12px; line-height:16px; }
.muted,.empty { color:var(--muted-foreground); }
.feedback,.empty { padding:8px 12px; border-radius:8px; background:var(--muted); color:var(--foreground); overflow-wrap:anywhere; }
.records { min-width:0; border:1px solid var(--border); border-radius:8px; }
.records :deep(th),.records :deep(td) { padding:8px; white-space:normal; }
.records :deep(th:not(:nth-child(2))) { white-space:nowrap; }
.records :deep(td.record-copy) { min-width:0; max-width:560px; }
.content { color:var(--foreground); }
.content summary { display:list-item; width:fit-content; margin-top:4px; cursor:pointer; color:var(--primary); }
.content summary:focus-visible { outline:2px solid var(--ring); outline-offset:2px; }
.content p { padding:8px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; font-size:13px; line-height:20px; }
.actions,.pagination { flex-wrap:wrap; } .pagination { justify-content:flex-end; }
.pagination :deep(select) { height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.editor-fields { display:grid; gap:12px; min-width:0; max-height:60vh; overflow:auto; }
.field { display:grid; gap:4px; min-width:0; color:var(--foreground); }
.field :deep(input) { min-height:28px; height:28px; }
.options { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.option { min-width:0; padding:4px 8px; border:1px solid var(--border); border-radius:8px; }
@container announcements (max-width:760px) {
  .announcements-card { padding:12px; }
  .records,.records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .records :deep(table),.records :deep(tbody) { display:grid; }
  .records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .records :deep(tbody tr) { display:grid; min-width:0; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; gap:4px 12px; padding:12px; border:1px solid var(--border); border-radius:8px; }
  .records :deep(td) { padding:0; }
  .records :deep(td[data-label]) { display:flex; align-items:baseline; gap:4px; font-size:12px; }
  .records :deep(td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
  .record-copy { grid-column:1/-1; grid-row:1; padding-bottom:4px !important; }
  .record-actions { grid-column:1/-1; margin-top:4px; }
  .pagination { justify-content:flex-start; }
}
@container announcements (max-width:480px) { .records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) {
  .announcement-page :deep([data-slot='button']),.pagination :deep(select),.field :deep(input),.option,.content summary { min-height:44px; }
  .announcement-page :deep([data-slot='button']),.content summary { min-width:44px; }
  .content summary { line-height:44px; margin-top:0; }
}
</style>
