<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Switch } from '@xa/shadcn-vue/switch'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { achievementApi } from '@/api/modules'
import { ACHIEVEMENT_METRIC } from '@/utils/dict'
import { useAuthStore } from '@/stores/auth'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'

const auth = useAuthStore(), tableRef = ref(), dialogVisible = ref(false), saving = ref(false), deleting = ref(false)
const canManage = computed(() => auth.hasPerm('achievement:edit') || auth.hasPerm('achievement:delete'))
const notice = ref(''), formNotice = ref(''), form = reactive<any>({}), targetInput = ref<number | string>(1)
watch(() => form.metric, (value, previous) => { if (dialogVisible.value && previous && value !== previous) { targetInput.value = ''; formNotice.value = '指标已更改，请填写对应的目标值' } }, { flush: 'sync' })
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
function onCreate() { Object.assign(form, { id: 0, code: '', title: '', desc: '', icon: '', metric: 'orders', sort_order: 0, is_active: true }); targetInput.value = 1; formNotice.value = ''; dialogVisible.value = true }
function onEdit(row: any) { Object.assign(form, row); targetInput.value = row.metric === 'amount' ? Number(amountToXaCoin(row.target)) : row.target; formNotice.value = ''; dialogVisible.value = true }
async function onSave() {
  if (saving.value || !auth.hasPerm('achievement:edit')) return
  if (!form.code?.trim() || !form.title?.trim()) { formNotice.value = '请填写标识和名称'; return }
  const target = Number(targetInput.value)
  if (targetInput.value === '' || !Number.isFinite(target) || target < 0 || (form.metric === 'amount' ? Math.abs(target * 10 - Math.round(target * 10)) > 1e-8 : !Number.isInteger(target))) { formNotice.value = form.metric === 'amount' ? '消费目标须不小于 0，最多一位小数' : '订单目标须为不小于 0 的整数'; return }
  if (!Number.isInteger(form.sort_order) || form.sort_order < 0) { formNotice.value = '排序须为不小于 0 的整数'; return }
  saving.value = true; formNotice.value = ''
  try {
    const payload = { code: form.code, title: form.title, desc: form.desc, icon: form.icon, metric: form.metric, target: form.metric === 'amount' ? xaCoinToAmount(target) : target, sort_order: form.sort_order, is_active: form.is_active }
    if (form.id) await achievementApi.update(form.id, payload); else await achievementApi.create(payload)
    notice.value = '保存成功'; dialogVisible.value = false; tableRef.value.reload()
  } catch (error) { formNotice.value = error instanceof Error ? error.message : '保存失败，请重试' }
  finally { saving.value = false }
}
async function onDelete(row: any) {
  if (deleting.value || !auth.hasPerm('achievement:delete')) return
  if (!await askConfirm({ title: '删除成就', description: `确定删除成就「${row.title}」吗？`, confirmText: '删除', cancelText: '取消' })) return
  deleting.value = true
  try { await achievementApi.remove(row.id); notice.value = '已删除'; tableRef.value.reload() }
  catch (error) { notice.value = error instanceof Error ? error.message : '删除失败，请重试' }
  finally { deleting.value = false }
}
</script>
<template>
  <div class="achievements-page">
    <Card class="achievements-card">
      <header class="heading">
        <div><h2>成就规则</h2><p class="muted">按完成订单数或累计消费解锁。</p></div>
        <Button v-if="auth.hasPerm('achievement:edit')" size="xs" @click="onCreate">新增成就</Button>
      </header>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <SimplePagedList ref="tableRef" :fetcher="achievementApi.list">
        <template #default="{ rows }">
          <Table class="achievement-table">
            <TableHeader><TableRow>
              <TableHead>成就</TableHead><TableHead>标识</TableHead><TableHead>解锁目标</TableHead>
              <TableHead>排序</TableHead><TableHead>状态</TableHead><TableHead v-if="canManage">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell class="name-cell">
                <h3>{{ row.title }}</h3><p v-if="row.desc" class="description">{{ row.desc }}</p>
                <p class="meta">ID {{ row.id }}<template v-if="row.icon"> · 图标 {{ row.icon }}</template></p>
              </TableCell>
              <TableCell class="code-cell" data-label="标识"><span>{{ row.code }}</span></TableCell>
              <TableCell class="target-cell">
                <span class="meta">{{ row.metric === 'amount' ? '累计消费' : row.metric_display }}</span>
                <div class="target"><strong>{{ row.metric === 'amount' ? amountToXaCoin(row.target) : row.target }}</strong><span>{{ row.metric === 'amount' ? '兴安币' : '单' }}</span></div>
              </TableCell>
              <TableCell class="order-cell" data-label="排序">{{ row.sort_order }}</TableCell>
              <TableCell class="state-cell" data-label="状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
              <TableCell v-if="canManage" class="action-cell"><div class="actions">
                <Button v-if="auth.hasPerm('achievement:edit')" size="xs" variant="outline" @click="onEdit(row)">编辑</Button>
                <Button v-if="auth.hasPerm('achievement:delete')" size="xs" variant="outline" :disabled="deleting" @click="onDelete(row)">删除</Button>
              </div></TableCell>
            </TableRow></TableBody>
          </Table>
        </template>
      </SimplePagedList>
    </Card>
    <Dialog :open="dialogVisible" @update:open="value => { if (!saving) dialogVisible = value }">
      <DialogContent class="achievement-dialog" :show-close-button="!saving">
        <DialogHeader>
          <DialogTitle>{{ form.id ? '编辑成就' : '新增成就' }}</DialogTitle>
          <DialogDescription>设置老板成就的展示信息和解锁条件。</DialogDescription>
        </DialogHeader>
        <div class="editor">
          <fieldset><legend>展示信息</legend>
            <div class="field-grid">
              <label>名称<Input v-model="form.title" :disabled="saving" maxlength="50" /></label>
              <label>标识<Input v-model="form.code" :disabled="!!form.id || saving" maxlength="50" placeholder="如 first_order" /></label>
              <label class="full">描述<Input v-model="form.desc" :disabled="saving" maxlength="100" /></label>
              <label>图标<Input v-model="form.icon" :disabled="saving" maxlength="20" /></label>
              <label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" :disabled="saving" /></label>
            </div>
          </fieldset>
          <fieldset><legend>解锁条件</legend>
            <div class="field-grid">
              <label>指标<NativeSelect v-model="form.metric" aria-label="指标" :disabled="saving">
                <NativeSelectOption v-for="(item, key) in ACHIEVEMENT_METRIC" :key="key" :value="key">{{ item.label }}</NativeSelectOption>
              </NativeSelect></label>
              <label>{{ form.metric === 'amount' ? '消费兴安币' : '完成订单数' }}<Input v-model.number="targetInput" type="number" min="0" :step="form.metric === 'amount' ? '0.1' : '1'" :disabled="saving" /></label>
            </div>
          </fieldset>
          <label class="enabled"><span>启用</span><Switch v-model="form.is_active" aria-label="启用" :disabled="saving" /></label>
          <p v-if="formNotice" role="alert" class="feedback">{{ formNotice }}</p>
        </div>
        <DialogFooter>
          <Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">取消</Button>
          <Button size="xs" :disabled="saving" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<style scoped>
.achievements-page { min-width:0; container:achievements / inline-size; }
.achievements-card { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; padding:16px; gap:12px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.heading,.actions,.enabled,.target { display:flex; align-items:center; gap:8px; }
.heading,.enabled { justify-content:space-between; }
.actions { flex-wrap:wrap; }
h2,h3,p,fieldset { margin:0; }
h2 { font-size:16px; line-height:24px; font-weight:600; } h3 { font-size:14px; line-height:20px; font-weight:600; }
.muted,.description,.meta,.target span,.field-grid label,.enabled,.feedback { font-size:12px; line-height:16px; }
.muted,.description,.meta,.target span { color:var(--muted-foreground); }
.description,.meta { display:block; margin-top:4px; overflow-wrap:anywhere; }
.target { align-items:baseline; gap:4px; margin-top:4px; font-variant-numeric:tabular-nums; }
.target strong { font-size:16px; line-height:20px; } .code-cell span { font:12px/16px ui-monospace,monospace; }
.achievements-page :deep(.achievement-table th),.achievements-page :deep(.achievement-table td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.achievements-page :deep(.achievement-table th) { white-space:nowrap; }
.achievements-page :deep(select),.field-grid :deep(select),.field-grid :deep(input) { min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.editor { display:grid; gap:12px; max-height:min(58vh,540px); overflow:auto; min-width:0; }
.editor fieldset { min-width:0; padding:0; border:0; }
.editor legend { margin-bottom:8px; font-size:14px; font-weight:600; }
.field-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.field-grid label { display:grid; min-width:0; gap:4px; }
.field-grid :deep([data-slot='native-select-wrapper']) { width:100%; }
.full { grid-column:1/-1; }
.enabled,.feedback { padding:8px; border-radius:8px; }
.enabled { border:1px solid var(--border); } .feedback { background:var(--muted); overflow-wrap:anywhere; }
@container achievements (max-width:800px) {
  .achievements-card { padding:12px; }
  .achievements-page :deep(.table-scroll),.achievements-page :deep([data-slot='table-container']) { border:0; overflow:visible; }
  .achievements-page :deep(.achievement-table),.achievements-page :deep(.achievement-table tbody) { display:grid; }
  .achievements-page :deep(.achievement-table thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .achievements-page :deep(.achievement-table tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .achievements-page :deep(.achievement-table tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; min-width:0; gap:4px 8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
  .achievements-page :deep(.achievement-table td) { padding:0; }
  .achievements-page :deep(.achievement-table td[data-label]) { display:flex; align-items:baseline; gap:4px; font-size:12px; }
  .achievements-page :deep(.achievement-table td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
  .name-cell,.code-cell,.target-cell,.action-cell { grid-column:1/-1; }
  .target-cell { display:flex; align-items:baseline; gap:8px; margin:4px 0; padding:4px 0!important; border-block:1px solid var(--border); }
  .target-cell .meta,.target-cell .target { margin:0; }
  .action-cell { margin-top:4px; }
  .achievements-page :deep(.paged-list) { min-width:0; grid-template-columns:minmax(0,1fr); }
  .achievements-page :deep(.pages) { justify-content:flex-start; }
}
@container achievements (max-width:480px) { .achievements-page :deep(.achievement-table tbody) { grid-template-columns:minmax(0,1fr); } }
@media(max-width:540px) { .field-grid { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .achievements-page :deep(select),.field-grid :deep(input),.field-grid :deep(select),.enabled { min-height:44px; } }
</style>
