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
import { bossTypeApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

type BossType = { id: number; name: string; discount_rate: number; color: string; remark: string; sort_order: number; is_active: boolean }
const blank = (): BossType => ({ id: 0, name: '', discount_rate: 100, color: '', remark: '', sort_order: 0, is_active: true })
const auth = useAuthStore()
const rows = ref<BossType[]>([]), total = ref(0), page = ref(1), pageSize = ref('10')
const loading = ref(false), saving = ref(false), deleting = ref(false), error = ref(''), notice = ref(''), formNotice = ref(''), dialogVisible = ref(false)
const form = reactive<BossType>(blank())
const canManage = computed(() => auth.hasPerm('boss_type:edit') || auth.hasPerm('boss_type:delete'))
const pages = computed(() => Math.max(1, Math.ceil(total.value / Number(pageSize.value))))
const colorValue = computed(() => /^#[0-9a-f]{6}$/i.test(form.color || '') ? form.color : '#2563eb')
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
let sequence = 0, alive = true
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  try {
    const result = await bossTypeApi.list({ page: page.value, page_size: Number(pageSize.value) })
    if (alive && current === sequence) { rows.value = result.data.list || []; total.value = result.data.total || 0 }
  } catch (cause: any) {
    if (!alive || current !== sequence) return
    if (cause?.response?.status === 404 && page.value > 1) { page.value = 1; await load(); return }
    rows.value = []; total.value = 0; error.value = '老板分级加载失败，请重试'
  } finally { if (alive && current === sequence) loading.value = false }
}
function create() { Object.assign(form, blank()); formNotice.value = ''; dialogVisible.value = true }
function edit(row: BossType) { Object.assign(form, row); formNotice.value = ''; dialogVisible.value = true }
async function save() {
  if (saving.value || !auth.hasPerm('boss_type:edit')) return
  if (!form.name.trim()) { formNotice.value = '请填写分级名称'; return }
  if (!Number.isFinite(Number(form.discount_rate)) || Number(form.discount_rate) < 1 || Number(form.discount_rate) > 100) { formNotice.value = '折扣率须在 1% 至 100% 之间'; return }
  if (!Number.isFinite(Number(form.sort_order)) || Number(form.sort_order) < 0) { formNotice.value = '排序不能小于 0'; return }
  saving.value = true; formNotice.value = ''
  try {
    const payload = { name: form.name, discount_rate: Number(form.discount_rate), color: form.color || '', remark: form.remark, sort_order: Number(form.sort_order), is_active: form.is_active }
    if (form.id) await bossTypeApi.update(form.id, payload); else await bossTypeApi.create(payload)
    notice.value = '保存成功'; dialogVisible.value = false; page.value = 1; await load()
  } catch (cause) { formNotice.value = cause instanceof Error ? cause.message : '保存失败，请重试' }
  finally { saving.value = false }
}
async function remove(row: BossType) {
  if (deleting.value || !auth.hasPerm('boss_type:delete')) return
  if (!await askConfirm({ title: '删除老板分级', description: `确定删除分级「${row.name}」吗？`, confirmText: '删除', cancelText: '取消' })) return
  deleting.value = true
  try { await bossTypeApi.remove(row.id); notice.value = '已删除'; page.value = 1; await load() }
  catch (cause) { notice.value = cause instanceof Error ? cause.message : '删除失败，请重试' }
  finally { deleting.value = false }
}
function changePage(next: number) { if (!loading.value && next >= 1 && next <= pages.value) { page.value = next; void load() } }
function changeSize(value: unknown) { const size = String(value); if (['10', '20', '50', '100'].includes(size)) { pageSize.value = size; page.value = 1; void load() } }
onMounted(() => { void load() }); onUnmounted(() => { alive = false; sequence++ })
</script>

<template>
  <div class="boss-types">
    <Card class="list-panel">
      <header class="heading">
        <div><h2>老板分级</h2><p class="muted">配置分级折扣，100% 表示此分级不打折。</p></div>
        <Button v-if="auth.hasPerm('boss_type:edit')" size="xs" @click="create">新增分级</Button>
      </header>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <p v-if="error" role="alert" class="feedback">{{ error }} <Button size="xs" variant="outline" @click="load">重新加载</Button></p>
      <p v-else-if="loading" role="status" class="muted">正在加载分级…</p>
      <template v-else>
        <p class="muted">共 {{ total }} 条</p>
        <p v-if="!rows.length" class="feedback">暂无老板分级</p>
        <div v-else class="records" role="region" aria-label="老板分级列表">
          <Table>
            <TableHeader><TableRow>
              <TableHead>分级名称</TableHead><TableHead>折扣率</TableHead><TableHead>标识色</TableHead>
              <TableHead>排序</TableHead><TableHead>状态</TableHead><TableHead v-if="canManage">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell class="record-name"><h3>{{ row.name }}</h3><p v-if="row.remark" class="remark">{{ row.remark }}</p><p class="muted">ID {{ row.id }}</p></TableCell>
              <TableCell class="record-rate" data-label="折扣率"><strong>{{ row.discount_rate }}%</strong></TableCell>
              <TableCell class="record-color" data-label="标识色"><span v-if="row.color" class="swatch"><i aria-hidden="true" :style="{ backgroundColor: row.color }" />{{ row.color }}</span><span v-else class="muted">未设置</span></TableCell>
              <TableCell class="record-order" data-label="排序">{{ row.sort_order }}</TableCell>
              <TableCell class="record-state" data-label="状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
              <TableCell v-if="canManage" class="record-actions"><div class="actions">
                <Button v-if="auth.hasPerm('boss_type:edit')" size="xs" variant="outline" @click="edit(row)">编辑</Button>
                <Button v-if="auth.hasPerm('boss_type:delete')" size="xs" variant="outline" :disabled="deleting" @click="remove(row)">删除</Button>
              </div></TableCell>
            </TableRow></TableBody>
          </Table>
        </div>
        <nav v-if="total" class="pagination" aria-label="分级翻页">
          <span class="muted">第 {{ page }} / {{ pages }} 页</span>
          <NativeSelect :model-value="pageSize" aria-label="每页条数" @update:model-value="changeSize">
            <NativeSelectOption v-for="size in ['10','20','50','100']" :key="size" :value="size">{{ size }} 条/页</NativeSelectOption>
          </NativeSelect>
          <Button size="xs" variant="outline" :disabled="page <= 1" @click="changePage(page - 1)">上一页</Button>
          <Button size="xs" variant="outline" :disabled="page >= pages" @click="changePage(page + 1)">下一页</Button>
        </nav>
      </template>
    </Card>

    <Dialog :open="dialogVisible" @update:open="value => { if (!saving) dialogVisible = value }">
      <DialogContent :show-close-button="!saving">
        <DialogHeader><DialogTitle>{{ form.id ? '编辑老板分级' : '新增老板分级' }}</DialogTitle><DialogDescription>分级折扣率为 90% 时按九折计算。</DialogDescription></DialogHeader>
        <div class="editor">
          <label class="field">分级名称<Input v-model="form.name" maxlength="50" placeholder="如：黄金老板" :disabled="saving" /></label>
          <div class="field-grid">
            <label class="field">折扣率（%）<Input v-model.number="form.discount_rate" type="number" min="1" max="100" :disabled="saving" /></label>
            <label class="field">排序<Input v-model.number="form.sort_order" type="number" min="0" :disabled="saving" /></label>
          </div>
          <fieldset class="color-field">
            <legend>标识色</legend>
            <div class="color-controls">
              <input type="color" :value="colorValue" :disabled="saving" aria-label="选择标识色" @input="form.color = ($event.target as HTMLInputElement).value" />
              <Input v-model="form.color" aria-label="标识色值" maxlength="9" placeholder="留空则不标记" :disabled="saving" />
              <Button size="xs" variant="outline" :disabled="saving || !form.color" @click="form.color = ''">清除</Button>
            </div>
            <p class="muted">用于工作台卡片和徽章。</p>
          </fieldset>
          <label class="field">备注<Textarea v-model="form.remark" maxlength="255" :rows="2" placeholder="选填" :disabled="saving" /></label>
          <label class="option"><span>启用</span><Switch v-model="form.is_active" aria-label="启用" :disabled="saving" /></label>
          <p v-if="formNotice" role="alert" class="feedback">{{ formNotice }}</p>
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
.boss-types { min-width:0; container:boss-types / inline-size; }
.list-panel { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.heading,.actions,.pagination,.swatch,.option,.feedback { display:flex; align-items:center; gap:8px; }
.heading,.option { justify-content:space-between; }
h2,h3,p,fieldset { margin:0; } h2 { font-size:16px; line-height:24px; font-weight:600; } h3 { font-size:14px; line-height:20px; font-weight:600; }
.muted,.remark,.feedback,.field,.option,.color-field legend { font-size:12px; line-height:16px; }
.muted,.remark { color:var(--muted-foreground); }
.record-name .muted,.record-name .remark { margin-top:4px; }
.remark { white-space:pre-wrap; overflow-wrap:anywhere; }
.feedback { flex-wrap:wrap; border-radius:8px; background:var(--muted); padding:8px 12px; overflow-wrap:anywhere; }
.records { min-width:0; border:1px solid var(--border); border-radius:8px; }
.records :deep(th),.records :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.records :deep(th) { white-space:nowrap; }
.record-rate strong { font-size:16px; line-height:20px; font-variant-numeric:tabular-nums; }
.swatch { display:inline-flex; font-size:12px; gap:4px; }
.swatch i { flex:none; width:12px; height:12px; border:1px solid var(--border); border-radius:4px; }
.actions,.pagination { flex-wrap:wrap; } .pagination { justify-content:flex-end; }
.pagination :deep(select),.editor :deep(input:not([type=color])) { min-height:28px; height:28px; font-size:12px; padding-block:4px; }
.editor { min-width:0; max-height:min(60vh,540px); overflow:auto; display:grid; gap:12px; }
.field,.field-grid,.color-controls { display:grid; min-width:0; gap:4px; }
.field-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.color-field { min-width:0; padding:0; border:0; }
.color-field legend { margin-bottom:4px; }
.color-controls { grid-template-columns:28px minmax(0,1fr) auto; align-items:center; gap:8px; }
.color-controls input[type=color] { border:1px solid var(--input); border-radius:4px; background:var(--card); width:28px; height:28px; padding:2px; }
.color-field .muted { margin-top:4px; }
.option { padding:4px 8px; border:1px solid var(--border); border-radius:8px; }
@container boss-types (max-width:800px) {
  .list-panel { padding:12px; }
  .records,.records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .records :deep(table),.records :deep(tbody) { display:grid; }
  .records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .records :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; min-width:0; gap:4px 8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
  .records :deep(td) { padding:0; }
  .records :deep(td[data-label]) { display:flex; align-items:baseline; flex-wrap:wrap; gap:4px; font-size:12px; }
  .records :deep(td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
  .record-name,.record-actions { grid-column:1/-1; }
  .record-name { margin-bottom:4px; } .record-actions { margin-top:4px; }
  .pagination { justify-content:flex-start; }
}
@container boss-types (max-width:480px) { .records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) {
 .boss-types :deep(button),.pagination :deep(select),.editor :deep(input:not([type=color])),.color-controls :deep(button),.option { min-height:44px; }
 .boss-types :deep(button),.color-controls :deep(button) { min-width:44px; }
 .color-controls { grid-template-columns:44px minmax(0,1fr) auto; } .color-controls input[type=color] { width:44px; height:44px; }
}
</style>
