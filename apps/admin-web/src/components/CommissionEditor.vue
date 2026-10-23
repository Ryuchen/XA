<template>
  <div class="commission-editor ap-field--full">
    <el-form-item label="抽成方式">
      <el-radio-group v-model="type" :disabled="disabled" aria-label="抽成方式">
        <el-radio-button value="PERCENT">百分比</el-radio-button>
        <el-radio-button value="FIXED">固定金额</el-radio-button>
      </el-radio-group>
    </el-form-item>
    <el-form-item :label="type === 'FIXED' ? '整单固定抽成（兴安币）' : '抽成比例（%）'" :error="touched ? error : ''">
      <el-input-number v-if="type === 'FIXED'" v-model="fixed" :disabled="disabled" :min="0" :max="214748364.7" :precision="1" :step="0.1" controls-position="right" @blur="touched = true" />
      <el-input-number v-else v-model="rate" :disabled="disabled" :min="0" :max="100" :precision="0" :step="1" controls-position="right" @blur="touched = true" />
      <p class="commission-hint">{{ hint || (type === 'FIXED' ? '整笔订单仅扣一次，双陪共同分摊；支持 1 位小数，0 表示不抽成。' : '0 表示不抽成，100 表示抽取全部份额。') }}</p>
    </el-form-item>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { commissionError, type CommissionType } from '@/utils/commission'

defineProps<{ disabled?: boolean; hint?: string }>()
const type = defineModel<CommissionType>('type', { default: 'PERCENT' })
const rate = defineModel<number | undefined>('rate', { default: 0 })
const fixed = defineModel<number | undefined>('fixed', { default: 0 })
const touched = ref(false)
const error = computed(() => commissionError(type.value, rate.value, fixed.value))
watch(type, () => { touched.value = false })
</script>

<style scoped>
.commission-editor { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.commission-editor :deep(.el-input-number) { width: 100%; }
.commission-hint { margin: 6px 0 0; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
@media (max-width: 640px) { .commission-editor { grid-template-columns: minmax(0, 1fr); } }
</style>
