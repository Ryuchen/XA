<template>
  <div>
    <div class="ap-page-head"><p class="ap-page-intro">首页每个玩法对应一张封面。商品按「玩法 → 手游 / 端游 → 机密 / 绝密」组织。</p><el-button v-if="auth.hasPerm('service:edit')" type="primary" @click="open()">新增玩法</el-button></div>
    <crud-table ref="table" :fetcher="fetchGameplays">
      <template #toolbar>
        <el-radio-group v-model="archived" class="gameplay-status-switch" aria-label="玩法归档状态" @change="table.reload()">
          <el-radio-button :value="false">当前玩法</el-radio-button>
          <el-radio-button :value="true">已归档</el-radio-button>
        </el-radio-group>
      </template>
      <el-table-column label="封面" width="180"><template #default="{ row }"><div class="cover" :style="{ background: themes[row.theme]?.color || '#51435c' }"><img v-if="row.cover_url" :src="row.cover_url" /><span>{{ themes[row.theme]?.symbol || '+' }}</span><strong>{{ row.name }}</strong></div></template></el-table-column>
      <el-table-column prop="name" label="玩法" width="120" />
      <el-table-column prop="subtitle" label="介绍" min-width="180" />
      <el-table-column label="类型" width="100"><template #default="{ row }"><el-tag :type="row.kind === 'ROULETTE' ? 'warning' : 'info'">{{ row.kind === 'ROULETTE' ? '转盘单' : '普通玩法' }}</el-tag></template></el-table-column>
      <el-table-column prop="service_count" label="商品数" width="90" />
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="操作" width="220"><template #default="{ row }"><el-button v-if="row.is_archived && auth.hasPerm('service:edit')" link type="primary" @click="restore(row)">恢复</el-button><template v-else-if="!row.is_archived"><el-button link type="primary" @click="router.push({ path: '/service-items', query: { gameplay: row.id } })">配置商品</el-button><el-button v-if="auth.hasPerm('service:edit')" link @click="open(row)">编辑</el-button><el-button v-if="auth.hasPerm('service:delete')" link type="danger" @click="remove(row)">归档</el-button></template></template></el-table-column>
    </crud-table>
    <el-dialog :show-close="!contentUploading" :close-on-click-modal="!contentUploading" :close-on-press-escape="!contentUploading" v-model="visible" :title="form.id ? '编辑玩法' : '新增玩法'" width="720px" class="ap-modal">
      <el-form  class="ap-modal-form ap-form-grid" label-position="top">
        <el-form-item label="玩法名称"><el-input v-model="form.name" maxlength="40" /></el-form-item>
        <el-form-item label="唯一标识"><el-input v-model="form.code" placeholder="英文、数字与连字符，例如 roulette" maxlength="40" /></el-form-item>
        <el-form-item label="玩法类型" class="ap-field--full"><el-radio-group v-model="form.kind" :disabled="form.service_count > 0"><el-radio value="STANDARD">普通玩法</el-radio><el-radio value="ROULETTE">转盘单</el-radio></el-radio-group></el-form-item>
        <el-form-item label="一句话介绍"><el-input v-model="form.subtitle" maxlength="100" /></el-form-item>
        <el-form-item label="封面主题"><el-select v-model="form.theme"><el-option v-for="(item, key) in themes" :key="key" :value="key" :label="item.name" /></el-select></el-form-item>
        <el-form-item label="自定义封面"><el-input v-model="form.cover_url" placeholder="https://… 留空使用内置主题封面" /></el-form-item>
        <el-form-item label="玩法规则" class="ap-field--full"><el-input v-model="form.rules" type="textarea" :rows="5" maxlength="5000" show-word-limit /></el-form-item>
        <el-form-item label="玩法详情 · 富文本" class="ap-field--full"><CatalogEditor v-model="form.details_html" @uploading="contentUploading = $event" hint="作为所属商品的通用玩法详情；各商品可以独立编辑自己的价目海报和文案。" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="form.sort_order" :min="0" :precision="0" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.is_active" /><span class="hint">停用后隐藏入口并阻止新下单，历史订单按快照继续履约。</span></el-form-item>
      </el-form>
      <el-alert v-if="form.kind === 'ROULETTE'" title="在各商品中配置 2–8 个服务目标与概率权重。每单仅一个目标，余额支付成功时确定，报单审核后结算。" type="info" :closable="false" />
      <template #footer><el-button :disabled="contentUploading" @click="visible = false">取消</el-button><el-button type="primary" :disabled="contentUploading" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import CatalogEditor from '@/components/CatalogEditor.vue'
import { gameplayApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore(), router = useRouter(), table = ref(), visible = ref(false), saving = ref(false)
const form = reactive<any>({})
const archived = ref(false)
const fetchGameplays = (params?: Record<string, any>) => gameplayApi.list({ ...params, is_archived: archived.value })
const contentUploading = ref(false)
const themes: Record<string, { name: string; color: string; symbol: string }> = {
  roulette: { name: '转盘 · 赤金', color: '#a85032', symbol: '✦' }, experience: { name: '体验 · 青绿', color: '#41776b', symbol: '01' }, companion: { name: '陪玩 · 紫罗兰', color: '#595988', symbol: '∞' }, fun: { name: '趣味 · 玫瑰', color: '#8e526a', symbol: '✧' }, mission: { name: '任务 · 蔚蓝', color: '#3a668d', symbol: '✓' }, extraction: { name: '带出 · 琥珀', color: '#8a7238', symbol: '↗' }, prison: { name: '监狱 · 青灰', color: '#49666d', symbol: '▥' }, custom: { name: '定制 · 暮紫', color: '#76617e', symbol: '+' },
}
function open(row?: any) { Object.assign(form, { id: 0, name: '', code: '', kind: 'STANDARD', subtitle: '', theme: 'custom', cover_url: '', rules: '', details_html: '', sort_order: 0, is_active: true, service_count: 0 }, row || {}); visible.value = true }
async function save() { if (saving.value) return; if (!form.name.trim() || !/^[a-zA-Z0-9_-]+$/.test(form.code)) { ElMessage.warning('请填写名称与有效的英文标识'); return }; saving.value = true; try { const { id, service_count, ...data } = form; if (id) await gameplayApi.update(id, data); else await gameplayApi.create(data); visible.value = false; ElMessage.success('玩法已保存'); table.value.reload() } finally { saving.value = false } }
async function remove(row: any) { await ElMessageBox.confirm(`归档玩法「${row.name}」？归档后隐藏玩法及其商品，历史订单保留，可在已归档列表恢复。`, '归档玩法', { type: 'warning' }); await gameplayApi.remove(row.id); table.value.reload() }
async function restore(row: any) { await gameplayApi.action(row.id, 'restore'); ElMessage.success('已恢复，启用前请核对所属商品'); table.value.reload() }
</script>
<style scoped>
.gameplay-status-switch {
  display: inline-flex;
  flex-wrap: nowrap;
  gap: var(--space-1);
  max-width: 100%;
  padding: var(--space-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--secondary);
}
.gameplay-status-switch :deep(.el-radio-button .el-radio-button__inner) {
  min-width: 104px;
  border: 1px solid transparent;
  outline: none;
  border-radius: var(--control-radius);
  background: transparent;
  color: var(--xa-color-text-secondary);
  font-weight: 500;
  white-space: nowrap;
  box-shadow: none;
  transition: background-color var(--xa-motion-fast), color var(--xa-motion-fast),
    border-color var(--xa-motion-fast);
}
.gameplay-status-switch :deep(.el-radio-button:not(.is-active):hover .el-radio-button__inner) {
  background: var(--card);
  color: var(--foreground);
}
.gameplay-status-switch :deep(.el-radio-button.is-active .el-radio-button__original-radio:not(:disabled) + .el-radio-button__inner) {
  border-color: color-mix(in srgb, var(--primary) 20%, var(--border));
  background: var(--card);
  color: color-mix(in srgb, var(--primary) 80%, var(--foreground));
  font-weight: 600;
  box-shadow: var(--shadow-2xs);
}
.gameplay-status-switch :deep(.el-radio-button__original-radio:focus-visible + .el-radio-button__inner) {
  outline: 2px solid var(--ring);
  outline-offset: 1px;
}
@media (max-width: 720px) {
  .gameplay-status-switch { width: 100%; }
  .gameplay-status-switch :deep(.el-radio-button) { flex: 1; min-width: 0; }
  .gameplay-status-switch :deep(.el-radio-button .el-radio-button__inner) { width: 100%; min-width: 0; }
}
.cover { width:146px; height:88px; border-radius:10px; color:#fff; overflow:hidden; position:relative; }
.cover img { position:absolute; width:100%; height:100%; object-fit:cover; }
.cover span { position:absolute; right:16px; top:8px; font-size:42px; opacity:.55; }
.cover strong { position:absolute; left:12px; bottom:10px; text-shadow:0 1px 4px #333; }
.hint { font-size:12px; color:var(--el-text-color-secondary); line-height:1.6; margin-left:8px; }
</style>
