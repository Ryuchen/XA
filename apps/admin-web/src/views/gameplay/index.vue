<template>
  <div class="gameplay-page">
    <Card class="gameplay-panel">
      <header class="heading">
        <div><h2>玩法目录</h2><p class="muted">商品按玩法、平台与难度组织，每个玩法可设置独立封面。</p></div>
        <Button v-if="auth.hasPerm('service:edit')" size="xs" @click="open()">新增玩法</Button>
      </header>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <SimplePagedList ref="table" :fetcher="fetchGameplays">
        <template #toolbar="{ reload }">
          <div class="status-filter" role="group" aria-label="玩法归档状态">
            <Button size="xs" :variant="!archived ? 'default' : 'outline'" :aria-pressed="!archived" @click="archived = false; reload()">当前玩法</Button>
            <Button size="xs" :variant="archived ? 'default' : 'outline'" :aria-pressed="archived" @click="archived = true; reload()">已归档</Button>
          </div>
        </template>
        <template #default="{ rows }">
          <Table>
            <TableHeader><TableRow>
              <TableHead>玩法与封面</TableHead><TableHead>类型</TableHead><TableHead>商品数</TableHead>
              <TableHead>排序</TableHead><TableHead>状态</TableHead><TableHead v-if="!archived || auth.hasPerm('service:edit')">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell class="identity-cell">
                <div class="identity"><div class="cover" :style="{background:themes[row.theme]?.color || 'var(--ap-cover-custom)'}">
                  <img v-if="row.cover_url" :src="row.cover_url" :alt="row.name + '封面'"/><span v-else aria-hidden="true">{{ themes[row.theme]?.symbol || '+' }}</span>
                </div><div><h3>{{ row.name }}</h3><p v-if="row.subtitle" class="muted">{{ row.subtitle }}</p></div></div>
              </TableCell>
              <TableCell data-label="类型">{{ row.kind === 'ROULETTE' ? '转盘单' : '普通玩法' }}</TableCell>
              <TableCell data-label="商品数">{{ row.service_count }}</TableCell>
              <TableCell data-label="排序">{{ row.sort_order }}</TableCell>
              <TableCell data-label="状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
              <TableCell v-if="!row.is_archived || auth.hasPerm('service:edit')" class="action-cell"><div class="actions">
                <Button v-if="row.is_archived && auth.hasPerm('service:edit')" size="xs" variant="outline" :disabled="updating" @click="restore(row)">恢复</Button>
                <template v-else-if="!row.is_archived">
                  <Button size="xs" variant="outline" @click="router.push({path:'/service-items',query:{gameplay:row.id}})">配置商品</Button>
                  <Button v-if="auth.hasPerm('service:edit')" size="xs" variant="outline" @click="open(row)">编辑</Button>
                  <Button v-if="auth.hasPerm('service:delete')" size="xs" variant="outline" :disabled="updating" @click="remove(row)">归档</Button>
                </template>
              </div></TableCell>
            </TableRow></TableBody>
          </Table>
        </template>
      </SimplePagedList>
    </Card>
    <Dialog :open="visible" @update:open="value => { if (!contentUploading && !saving) visible = value }"><DialogContent :show-close-button="!contentUploading && !saving" class="gameplay-dialog"><DialogHeader><DialogTitle>{{ form.id ? '编辑玩法' : '新增玩法' }}</DialogTitle><DialogDescription>设置玩法入口、规则及通用详情。</DialogDescription></DialogHeader>
      <div class="gameplay-form"><label>玩法名称<Input v-model="form.name" maxlength="40" :disabled="saving" /></label><label>唯一标识<Input v-model="form.code" placeholder="英文、数字与连字符，例如 roulette" maxlength="40" :disabled="saving" /></label><fieldset class="gameplay-full"><legend>玩法类型</legend>
        <RadioGroup v-model="form.kind" class="type-options" aria-label="玩法类型" :disabled="form.service_count > 0 || saving">
          <label><RadioGroupItem value="STANDARD" />普通玩法</label><label><RadioGroupItem value="ROULETTE" />转盘单</label>
        </RadioGroup>
        <p v-if="form.service_count > 0" class="muted">已有商品的玩法不能更改类型。</p>
      </fieldset><label>一句话介绍<Input v-model="form.subtitle" maxlength="100" :disabled="saving" /></label><label>封面主题<NativeSelect aria-label="封面主题" v-model="form.theme" :disabled="saving"><NativeSelectOption v-for="(item, key) in themes" :key="key" :value="key">{{ item.name }}</NativeSelectOption></NativeSelect></label><label class="gameplay-full">自定义封面<Input v-model="form.cover_url" placeholder="https://… 留空使用内置主题封面" :disabled="saving" /></label><label class="gameplay-full">玩法规则<Textarea v-model="form.rules" :rows="5" maxlength="5000" :disabled="saving" /></label><div class="gameplay-full"><p>玩法详情 · 富文本</p><CatalogEditor v-model="form.details_html" @uploading="contentUploading = $event" hint="作为所属商品的通用玩法详情；各商品可以独立编辑自己的价目海报和文案。" /></div><label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" :disabled="saving" /></label><label class="gameplay-switch">启用<Switch v-model="form.is_active" aria-label="启用" :disabled="saving" /><span class="hint">停用后隐藏入口并阻止新下单，历史订单按快照继续履约。</span></label></div>
      <Alert v-if="form.kind === 'ROULETTE'"><AlertDescription>在各商品中配置 2–8 个服务目标与概率权重。每单仅一个目标，余额支付成功时确定，报单审核后结算。</AlertDescription></Alert><p v-if="formNotice" role="alert">{{ formNotice }}</p><DialogFooter><Button size="xs" variant="outline" :disabled="contentUploading || saving" @click="visible = false">取消</Button><Button size="xs" :disabled="contentUploading || saving" @click="save">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter>
    </DialogContent></Dialog>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Card } from '@xa/shadcn-vue/card'
import { RadioGroup, RadioGroupItem } from '@xa/shadcn-vue/radio-group'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Switch } from '@xa/shadcn-vue/switch'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Badge } from '@xa/shadcn-vue/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import CatalogEditor from '@/components/CatalogEditor.vue'
import { gameplayApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore(), router = useRouter(), table = ref(), visible = ref(false), saving = ref(false)
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const notice = ref(''), formNotice = ref(''), updating = ref(false)
const form = reactive<any>({})
const archived = ref(false)
const fetchGameplays = (params?: Record<string, any>) => gameplayApi.list({ ...params, is_archived: archived.value })
const contentUploading = ref(false)
const themes: Record<string, { name: string; color: string; symbol: string }> = {
  roulette: { name: '转盘', color: 'var(--ap-cover-roulette)', symbol: '✦' }, experience: { name: '体验', color: 'var(--ap-cover-experience)', symbol: '01' }, companion: { name: '陪玩', color: 'var(--ap-cover-companion)', symbol: '∞' }, fun: { name: '趣味', color: 'var(--ap-cover-fun)', symbol: '✧' }, mission: { name: '任务', color: 'var(--ap-cover-mission)', symbol: '✓' }, extraction: { name: '带出', color: 'var(--ap-cover-extraction)', symbol: '↗' }, prison: { name: '监狱', color: 'var(--ap-cover-prison)', symbol: '▥' }, custom: { name: '定制', color: 'var(--ap-cover-custom)', symbol: '+' },
}
function open(row?: any) { Object.assign(form, { id: 0, name: '', code: '', kind: 'STANDARD', subtitle: '', theme: 'custom', cover_url: '', rules: '', details_html: '', sort_order: 0, is_active: true, service_count: 0 }, row || {}); formNotice.value = ''; visible.value = true }
async function save() {
  if (saving.value || contentUploading.value || !auth.hasPerm('service:edit')) return
  if (!form.name.trim() || !/^[a-zA-Z0-9_-]+$/.test(form.code)) { formNotice.value = '请填写名称与有效的英文标识'; return }
  if (!Number.isInteger(form.sort_order) || form.sort_order < 0) { formNotice.value = '排序须为非负整数'; return }
  saving.value = true; formNotice.value = ''
  try { const { id, service_count, ...data } = form; if (id) await gameplayApi.update(id, data); else await gameplayApi.create(data); visible.value = false; notice.value = '玩法已保存'; table.value.reload() }
  catch (error) { formNotice.value = error instanceof Error ? error.message : '保存失败，请重试' }
  finally { saving.value = false }
}
async function remove(row: any) {
  if (updating.value || !auth.hasPerm('service:delete')) return
  if (!await askConfirm({ title: '归档玩法', description: `归档玩法「${row.name}」？归档后隐藏玩法及其商品，历史订单保留，可在已归档列表恢复。`, confirmText: '归档', cancelText: '取消' })) return
  updating.value = true
  try { await gameplayApi.remove(row.id); notice.value = '已归档'; table.value.reload() } catch (error) { notice.value = error instanceof Error ? error.message : '归档失败，请重试' } finally { updating.value = false }
}
async function restore(row: any) {
  if (updating.value || !auth.hasPerm('service:edit')) return
  updating.value = true
  try { await gameplayApi.action(row.id, 'restore'); notice.value = '已恢复，启用前请核对所属商品'; table.value.reload() } catch (error) { notice.value = error instanceof Error ? error.message : '恢复失败，请重试' } finally { updating.value = false }
}
</script>
<style scoped>
.gameplay-page { min-width:0; container:gameplay / inline-size; }
.gameplay-panel { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.heading,.actions,.status-filter,.identity,.type-options,.type-options label { display:flex; align-items:center; gap:8px; }
.heading { justify-content:space-between; } .actions,.status-filter,.type-options { flex-wrap:wrap; }
h2,h3,p { margin:0; } h2 { font-size:16px; line-height:24px; font-weight:600; } h3 { font-size:14px; line-height:20px; font-weight:600; }
.muted,.feedback,.hint,.gameplay-form label { font-size:12px; line-height:16px; } .muted,.hint { color:var(--muted-foreground); } .muted { margin-top:4px; }
.feedback { padding:8px 12px; border-radius:8px; background:var(--muted); overflow-wrap:anywhere; }
.identity>div { min-width:0; } .identity h3,.identity p { overflow-wrap:anywhere; }
.cover { display:grid; width:80px; aspect-ratio:5/3; flex:none; place-items:center; overflow:hidden; border-radius:8px; color:white; font-size:24px; }
.cover img { width:100%; height:100%; object-fit:cover; }
.gameplay-panel :deep(th),.gameplay-panel :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.gameplay-panel :deep(th) { white-space:nowrap; }
.gameplay-page :deep(select),.gameplay-form :deep(input:not([type=file])),.gameplay-form :deep(select) { min-height:28px; height:28px; font-size:12px; padding-block:4px; }
:global(.gameplay-dialog) { width:min(720px,calc(100vw - 24px)); max-height:calc(100dvh - 32px); overflow:auto; }
.gameplay-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px 8px; }
.gameplay-form label { display:grid; min-width:0; gap:4px; }
.gameplay-form :deep([data-slot='native-select-wrapper']) { width:100%; }
.gameplay-full { grid-column:1/-1; }
.gameplay-form fieldset { min-width:0; margin:0; padding:0; border:0; }
.gameplay-form legend { margin-bottom:4px; font-size:12px; }
.type-options label,.gameplay-form .gameplay-switch { display:flex; align-items:center; gap:8px; }
.type-options label { min-height:28px; padding:4px 8px; border:1px solid var(--border); border-radius:8px; }
.gameplay-form .gameplay-switch { flex-wrap:wrap; }
@container gameplay (max-width:840px) {
 .gameplay-panel { padding:12px; }
 .gameplay-panel :deep(.paged-list) { min-width:0; grid-template-columns:minmax(0,1fr); }
 .gameplay-panel :deep(.table-scroll),.gameplay-panel :deep([data-slot='table-container']) { border:0; overflow:visible; }
 .gameplay-panel :deep(table),.gameplay-panel :deep(tbody) { display:grid; }
 .gameplay-panel :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
 .gameplay-panel :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .gameplay-panel :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; min-width:0; gap:4px 8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
 .gameplay-panel :deep(td) { padding:0; }
 .gameplay-panel :deep(td[data-label]) { display:flex; align-items:baseline; gap:4px; font-size:12px; }
 .gameplay-panel :deep(td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
 .identity-cell,.action-cell { grid-column:1/-1; } .identity-cell { margin-bottom:4px; } .action-cell { margin-top:4px; }
 .gameplay-panel :deep(.pages) { justify-content:flex-start; }
}
@container gameplay (max-width:480px) { .gameplay-panel :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(max-width:600px) { .gameplay-form { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .type-options label,.gameplay-form .gameplay-switch,.gameplay-form :deep(select),.gameplay-form :deep(input:not([type=file])),.gameplay-page :deep(button),.gameplay-page :deep(select) { min-height:44px; } .gameplay-page :deep(button) { min-width:44px; } }
</style>
