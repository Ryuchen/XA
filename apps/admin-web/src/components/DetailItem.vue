<script setup lang="ts">
defineProps<{ label?: string }>()
</script>
<template><div class="detail-item"><dt><slot name="label">{{ label }}</slot></dt><dd><slot /></dd></div></template>
<style scoped>.detail-item { display: grid; grid-template-columns: minmax(96px, 35%) minmax(0, 1fr); border-bottom: 1px solid var(--border); } .detail-item:last-child { border-bottom: 0; } dt, dd { min-width: 0; margin: 0; padding: 8px 12px; overflow-wrap: anywhere; } dt { background: var(--muted); color: var(--muted-foreground); } dd { border-left: 1px solid var(--border); }</style>
