<script setup lang="ts">
import { TooltipContent, TooltipPortal, TooltipProvider, TooltipRoot, TooltipTrigger } from 'reka-ui'
defineProps<{ text: string; disabled?: boolean }>()
</script>
<template>
  <TooltipProvider :delay-duration="300"><TooltipRoot><TooltipTrigger as-child><slot /></TooltipTrigger><TooltipPortal><TooltipContent v-if="!disabled" side="right" :side-offset="8" class="xa-tooltip">{{ text }}</TooltipContent></TooltipPortal></TooltipRoot></TooltipProvider>
</template>
<style>
.xa-tooltip { z-index:10000; max-width:240px; border:1px solid var(--border); border-radius:6px; padding:4px 8px; background:var(--card); color:var(--foreground); font-size:12px; box-shadow:0 4px 16px rgb(0 0 0 / 12%); }
</style>
