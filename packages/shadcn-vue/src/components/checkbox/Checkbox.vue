<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { CheckboxIndicator, CheckboxRoot, useForwardPropsEmits, type CheckboxRootEmits, type CheckboxRootProps } from 'reka-ui'
import { Check } from '@lucide/vue'
import { reactiveOmit } from '@vueuse/core'
import { cn } from '../../utils'
const props = defineProps<CheckboxRootProps & {class?:HTMLAttributes['class']}>()
const emit = defineEmits<CheckboxRootEmits>()
const forwarded = useForwardPropsEmits(reactiveOmit(props,'class'),emit)
</script>
<template><CheckboxRoot v-bind="forwarded" data-slot="checkbox" :class="cn('peer size-4 shrink-0 rounded-sm border border-input shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground',props.class)"><CheckboxIndicator class="flex items-center justify-center"><Check class="size-3.5" /></CheckboxIndicator></CheckboxRoot></template>
