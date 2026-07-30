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
let themeObserver: MutationObserver | null = null

const cssVarPattern = /^var\((--[\w-]+)(?:,\s*([^)]*))?\)$/

function resolveCssVariable(value: string) {
  const matched = value.trim().match(cssVarPattern)
  if (!matched) return value
  const resolved = getComputedStyle(document.documentElement).getPropertyValue(matched[1]).trim()
  return resolved || matched[2]?.trim() || value
}

function resolveThemeTokens<T>(value: T): T {
  if (typeof value === 'string') return resolveCssVariable(value) as T
  if (Array.isArray(value)) return value.map(resolveThemeTokens) as T
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([key, item]) => [key, resolveThemeTokens(item)]),
    ) as T
  }
  return value
}

function render(option = props.option) {
  chart?.setOption(resolveThemeTokens(option), true)
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value, isDarkMode() ? 'dark' : undefined)
  render()
  window.addEventListener('resize', resize)
  themeObserver = new MutationObserver(() => {
    if (!el.value) return
    chart?.dispose()
    chart = echarts.init(el.value, isDarkMode() ? 'dark' : undefined)
    render()
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

watch(
  () => props.option,
  (opt) => render(opt),
  { deep: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  themeObserver?.disconnect()
  themeObserver = null
  chart?.dispose()
  chart = null
})

function isDarkMode() {
  return document.documentElement.classList.contains('dark')
}
</script>
