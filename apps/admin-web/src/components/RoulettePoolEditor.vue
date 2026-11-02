<template>
  <div class="pool">
    <Alert><AlertDescription>每单确定 1 个服务目标。权重越大概率越高；概率会向老板公开，保存后仅影响新订单。</AlertDescription></Alert>
    <div v-for="(item, index) in model" :key="index" class="target">
      <div class="line"><Input v-model="item.name" placeholder="服务目标名称" maxlength="40" /><Input v-model.number="item.weight" class="weight" type="number" min="1" max="10000" step="1" aria-label="目标权重" /><span>{{ total ? (item.weight * 100 / total).toFixed(2) : '0' }}%</span><Button variant="outline" :disabled="model.length <= 2" @click="model.splice(index, 1)">移除</Button></div>
      <Textarea v-model="item.description" :rows="2" maxlength="300" placeholder="履约目标与结单条件，例如：完成约定任务后提交截图审核" />
    </div>
    <Button variant="outline" :disabled="model.length >= 8" @click="model.push({ name: '', description: '', weight: 1 })">添加目标（{{ model.length }}/8）</Button>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
const model = defineModel<Array<{ name: string; description: string; weight: number }>>({ required: true })
const total = computed(() => model.value.reduce((sum, item) => sum + (item.weight || 0), 0))
</script>
<style scoped>
.pool { width:100%; }.target { margin:16px 0; }.line { display:flex; gap:8px; margin-bottom:8px; align-items:center; }.line .weight { width:110px; flex-shrink:0; }.line span { min-width:55px; font-size:12px; }
@media (max-width: 720px) {
  .line { flex-wrap: wrap; }
  .line > input:not(.weight) { flex-basis: 100%; }
  .line .weight { flex: 1 1 110px; }
  .line button { margin-left: auto; }
}
</style>
