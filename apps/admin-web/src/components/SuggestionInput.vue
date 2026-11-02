<script setup lang="ts">
import { onBeforeUnmount, ref, useId, watch } from 'vue'
import { Input } from '@xa/shadcn-vue/input'
const props = defineProps<{ modelValue: string; fetchSuggestions: (keyword: string) => Promise<string[]>; label: string; placeholder: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; select: []; clear: [] }>()
const id = `suggest-${useId()}`, suggestions = ref<string[]>([])
let sequence = 0, timer: ReturnType<typeof setTimeout> | undefined
watch(() => props.modelValue, value => {
  sequence++; if (timer) clearTimeout(timer)
  if (!value) { suggestions.value = []; return }
  const current = sequence
  timer = setTimeout(async () => { const result = await props.fetchSuggestions(value); if (current === sequence) suggestions.value = result }, 250)
})
function update(value: string | number) { const next = String(value); emit('update:modelValue', next); if (!next) emit('clear') }
function changed(event: Event) { const value = (event.target as HTMLInputElement).value; if (suggestions.value.includes(value)) { emit('update:modelValue', value); emit('select') } }
onBeforeUnmount(() => { sequence++; if (timer) clearTimeout(timer) })
</script>
<template><div class="suggestion-input"><Input :model-value="modelValue" :list="id" :placeholder="placeholder" :aria-label="label" @update:model-value="update" @change="changed" /><datalist :id="id"><option v-for="item in suggestions" :key="item" :value="item" /></datalist></div></template>
<style scoped>.suggestion-input { width:150px; }</style>
