<template>
  <div ref="el" :style="{ width: '100%', height }" />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = withDefaults(defineProps<{ option: echarts.EChartsOption; height?: string }>(), {
  height: '300px',
})

const el = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

function resize() {
  chart?.resize()
}

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  chart.setOption(props.option)
  window.addEventListener('resize', resize)
})

watch(
  () => props.option,
  (opt) => chart?.setOption(opt, true),
  { deep: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
</script>
