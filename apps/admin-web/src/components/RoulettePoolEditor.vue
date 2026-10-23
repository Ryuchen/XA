<template>
  <div class="pool">
    <el-alert title="每单确定 1 个服务目标。权重越大概率越高；概率会向老板公开，保存后仅影响新订单。" type="info" :closable="false" />
    <div v-for="(item, index) in model" :key="index" class="target">
      <div class="line"><el-input v-model="item.name" placeholder="服务目标名称" maxlength="40" /><el-input-number v-model="item.weight" :min="1" :max="10000" :precision="0" controls-position="right" /><span>{{ total ? (item.weight * 100 / total).toFixed(2) : '0' }}%</span><el-button link type="danger" :disabled="model.length <= 2" @click="model.splice(index, 1)">移除</el-button></div>
      <el-input v-model="item.description" type="textarea" :rows="2" maxlength="300" placeholder="履约目标与结单条件，例如：完成约定任务后提交截图审核" />
    </div>
    <el-button :disabled="model.length >= 8" @click="model.push({ name: '', description: '', weight: 1 })">添加目标（{{ model.length }}/8）</el-button>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
const model = defineModel<Array<{ name: string; description: string; weight: number }>>({ required: true })
const total = computed(() => model.value.reduce((sum, item) => sum + (item.weight || 0), 0))
</script>
<style scoped>
.pool { width:100%; }.target { margin:16px 0; }.line { display:flex; gap:8px; margin-bottom:8px; align-items:center; }.line .el-input-number { width:110px; flex-shrink:0; }.line span { min-width:55px; font-size:12px; }
@media (max-width: 720px) {
  .line { flex-wrap: wrap; }
  .line > .el-input { flex-basis: 100%; }
  .line .el-input-number { flex: 1 1 110px; }
  .line .el-button { margin-left: auto; }
}
</style>
