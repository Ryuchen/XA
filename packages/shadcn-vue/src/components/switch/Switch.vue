<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { SwitchRoot, SwitchThumb, useForwardPropsEmits, type SwitchRootEmits, type SwitchRootProps } from 'reka-ui'
import { reactiveOmit } from '@vueuse/core'
import { cn } from '../../utils'
const props = defineProps<SwitchRootProps & { class?: HTMLAttributes['class'] }>()
const emit = defineEmits<SwitchRootEmits>()
const forwarded = useForwardPropsEmits(reactiveOmit(props,'class'),emit)
</script>
<template><SwitchRoot v-bind="forwarded" data-slot="switch" :class="cn('peer inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent shadow-xs transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input',props.class)"><SwitchThumb data-slot="switch-thumb" class="pointer-events-none block size-4 rounded-full bg-white shadow-lg ring-0 transition-transform data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0" /></SwitchRoot></template>
