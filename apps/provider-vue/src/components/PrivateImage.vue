<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../lib/session'
const props = defineProps<{ src:string; alt:string }>()
const url = ref(props.src), attempted = ref(false)
let generation = 0
watch(() => props.src, value => { generation++; url.value = value; attempted.value = false })
async function renew() {
  if (attempted.value) return
  attempted.value = true; const current = generation, ticket = props.src.match(/\/api\/media\/private\/([^/?]+)\/?$/)?.[1]
  if (!ticket) return
  try { const result = await api<{url:string}>('/media/renew/', { method:'POST', data:{ticket} }); if (current === generation) url.value = result.url } catch { /* user can refresh the report */ }
}
</script>
<template><a :href="url" target="_blank" rel="noopener"><img :src="url" :alt="alt" @error="renew" /></a></template>
