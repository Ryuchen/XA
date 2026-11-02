<template>
  <div class="commission-editor ap-field--full">
    <div class="commission-field"><span>抽成方式</span><div class="commission-options" role="group" aria-label="抽成方式"><Button :variant="type === 'PERCENT' ? 'default' : 'outline'" :aria-pressed="type === 'PERCENT'" :disabled="disabled" @click="type = 'PERCENT'">百分比</Button><Button :variant="type === 'FIXED' ? 'default' : 'outline'" :aria-pressed="type === 'FIXED'" :disabled="disabled" @click="type = 'FIXED'">固定金额</Button></div></div>
    <label class="commission-field">{{ type === 'FIXED' ? '整单固定抽成（兴安币）' : '抽成比例（%）' }}
      <Input v-if="type === 'FIXED'" v-model.number="fixed" type="number" :disabled="disabled" min="0" max="214748364.7" step="0.1" @blur="touched = true" />
      <Input v-else v-model.number="rate" type="number" :disabled="disabled" min="0" max="100" step="1" @blur="touched = true" />
      <span v-if="touched && error" class="commission-error">{{ error }}</span>
      <p class="commission-hint">{{ hint || (type === 'FIXED' ? '整笔订单仅扣一次，双陪共同分摊；支持 1 位小数，0 表示不抽成。' : '0 表示不抽成，100 表示抽取全部份额。') }}</p>
    </label>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
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
.commission-field { display:grid; align-content:start; gap:8px; font-size:13px; }
.commission-options { display:flex; gap:8px; }
.commission-error { color:var(--destructive); font-size:12px; }
.commission-hint { margin: 6px 0 0; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
@media (max-width: 640px) { .commission-editor { grid-template-columns: minmax(0, 1fr); } }
</style>
