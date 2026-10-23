<template>
  <div v-loading="loading">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="config">
      <p>北京时间 · 当前{{ modes[config.mode] || '常规' }}时段；修改配置保留当前窗口计数。</p>
      <el-form label-width="120px" :disabled="!editable || saving">
        <el-form-item label="峰谷限流"><el-switch v-model="config.enabled" /></el-form-item>
        <el-form-item label="自主下单"><el-switch v-model="config.self_order_enabled" /></el-form-item>
        <el-form-item label="波峰时段"><el-input-number v-model="config.peak_start" :min="0" :max="23" /> 至 <el-input-number v-model="config.peak_end" :min="0" :max="24" /></el-form-item>
        <el-form-item label="波谷时段"><el-input-number v-model="config.valley_start" :min="0" :max="23" /> 至 <el-input-number v-model="config.valley_end" :min="0" :max="24" /></el-form-item>
      </el-form>
      <el-table :data="rows" border>
        <el-table-column label="时段" width="90"><template #default="{ row }">{{ modes[row.mode] }}</template></el-table-column>
        <el-table-column label="操作" width="110"><template #default="{ row }">{{ actions[row.action] }}</template></el-table-column>
        <el-table-column v-for="field in fields" :key="field.key" :label="field.label" min-width="180">
          <template #default="{ row }"><el-input-number v-model="row.values[field.key]" :min="1" :max="field.max" :disabled="!editable || saving" /></template>
        </el-table-column>
      </el-table>
      <p>最近 {{ config.metrics?.window_minutes || 15 }} 分钟流量</p>
      <el-table :data="usage" border>
        <el-table-column prop="label" label="操作" /><el-table-column prop="admitted" label="放行请求" /><el-table-column prop="limited" label="限流请求" />
      </el-table>
      <p>陪玩客户端上报 {{ config.metrics?.network_reports || 0 }} 次，平均请求耗时 {{ config.metrics?.average_latency_ms || 0 }}ms，平均抖动 {{ config.metrics?.average_jitter_ms || 0 }}ms，网络失败 {{ config.metrics?.network_failure_percent || 0 }}%。</p>
      <el-alert title="网络数据由客户端采样上报，含服务端处理耗时；用于诊断，不参与抢单资格或排序。多实例部署需共用 DJANGO_CACHE_URL 指定的 Redis。" type="info" :closable="false" />
      <div class="traffic-actions"><el-button :disabled="saving" @click="load">刷新配置与监控</el-button><el-button v-if="editable" type="primary" :loading="saving" @click="save">保存配置</el-button></div>
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const editable = computed(() => auth.hasPerm('order:traffic'))
const config = ref<any>(null), loading = ref(false), saving = ref(false), error = ref('')
const modes: Record<string, string> = { peak: '波峰', normal: '常规', valley: '波谷' }
const actions: Record<string, string> = { create: '自主下单', grab: '抢单 / 申请', pool: '抢单池刷新', quote: '报价' }
const fields = [{ key: 'account_per_minute', label: '每账号 / 分钟', max: 10000 }, { key: 'global_per_minute', label: '全站 / 分钟', max: 1000000 }, { key: 'global_per_second', label: '全站 / 秒', max: 100000 }]
const rows = computed(() => config.value ? Object.keys(modes).flatMap(mode => Object.keys(actions).map(action => ({ mode, action, values: config.value.limits[mode][action] }))) : [])
const usage = computed(() => Object.keys(actions).map(action => ({ label: actions[action], admitted: config.value?.metrics?.[action + '_admitted'] || 0, limited: config.value?.metrics?.[action + '_limited'] || 0 })))
async function load() {
  loading.value = true; error.value = ''
  try { const res = await request.get('/order-traffic/') as any; config.value = res.data }
  catch (e: any) { error.value = e?.msg || e?.message || '配置加载失败' }
  finally { loading.value = false }
}
async function save() {
  if (!editable.value || saving.value) return
  saving.value = true
  try { await request.put('/order-traffic/', config.value); ElMessage.success('订单流量配置已保存'); await load() }
  catch (e: any) { error.value = e?.msg || e?.message || '保存失败，请刷新配置后重试' }
  finally { saving.value = false }
}
onMounted(load)
</script>
<style scoped>.traffic-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 20px; }</style>
