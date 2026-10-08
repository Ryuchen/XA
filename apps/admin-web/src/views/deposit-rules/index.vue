<template>
  <div class="rules-page">
    <p class="ap-page-intro">单抵押金为现金押金的两倍。按订单净收益分配抵押金和结款，达到应缴押金后停止抵扣。</p>
    <el-card shadow="never" v-loading="loading">
      <el-alert v-if="error" :title="error" type="error" :closable="false"><el-button link @click="load">重新加载</el-button></el-alert>
      <template v-else-if="rules">
        <div class="targets"><div><span>现金押金</span><strong>{{ rules.cash_target / 100 }} 元</strong></div><div><span>单抵押金</span><strong>{{ rules.earnings_target / 100 }} 元</strong><small>现金押金的 {{ rules.earnings_multiplier }} 倍</small></div></div>
        <el-form label-position="top" class="rule-form">
          <el-form-item label="净收益抵押金比例">
            <el-input-number v-model="percent" :min="0" :max="100" :precision="0" :step="10" controls-position="right" :disabled="saving || !auth.hasPerm('deposit_rule:edit')" />
            <span class="unit">%</span>
          </el-form-item>
          <div class="split"><span>抵押金 {{ percent ?? 0 }}%</span><span>结款 {{ 100 - (percent ?? 0) }}%</span></div>
          <p class="example">每 100 元净收益，{{ percent ?? 0 }} 元抵押金，{{ 100 - (percent ?? 0) }} 元结款。剩余应缴押金不足时，仅扣剩余金额，余款全部结款。</p>
          <p class="example">{{ rules.hold_hours ? '结款部分按现有规则冻结 ' + rules.hold_hours + ' 小时后解冻。' : '结款部分按现有订单规则入账。' }}比例更新仅影响之后生成的结算，已冻结记录保留原金额。</p>
          <el-button v-if="auth.hasPerm('deposit_rule:edit')" type="primary" :loading="saving" :disabled="loading || percent === undefined || percent === rules.deposit_percent" @click="save">保存比例</el-button>
        </el-form>
      </template>
    </el-card>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { depositRulesApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const rules = ref<{ cash_target:number; earnings_target:number; earnings_multiplier:number; deposit_percent:number; settlement_percent:number; hold_hours:number }>()
const percent = ref<number>()
const loading = ref(false)
const saving = ref(false)
const error = ref('')
async function load() {
  loading.value = true; error.value = ''
  try { rules.value = (await depositRulesApi.get()).data; percent.value = rules.value.deposit_percent }
  catch (failure) { error.value = failure instanceof Error ? failure.message : '规则读取失败' }
  finally { loading.value = false }
}
async function save() {
  if (saving.value || percent.value === undefined) return
  saving.value = true; error.value = ''
  try { rules.value = (await depositRulesApi.update(percent.value)).data; percent.value = rules.value.deposit_percent; ElMessage.success('比例已保存，对之后的收益结算生效') }
  catch (failure) { error.value = failure instanceof Error ? failure.message : '保存失败，请重新加载规则后重试' }
  finally { saving.value = false }
}
onMounted(load)
</script>
<style scoped lang="scss">
.rules-page { max-width:760px; }.targets { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:24px; padding-bottom:24px; margin-bottom:24px; border-bottom:1px solid var(--el-border-color-lighter); }
.targets div { display:flex; flex-direction:column; gap:8px; }.targets span,.targets small { color:var(--el-text-color-secondary); font-size:13px; }.targets strong { font-size:28px; font-weight:600; }
.rule-form { max-width:520px; }.unit { margin-left:10px; }.split { display:flex; flex-wrap:wrap; gap:24px; font-weight:600; color:var(--el-color-primary); }.example { font-size:13px; line-height:1.8; color:var(--el-text-color-secondary); margin:16px 0; }
@media(max-width:480px) { .targets { gap:16px; }.targets strong { font-size:24px; } }
</style>
