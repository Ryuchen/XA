<script setup lang="ts">
import { computed } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'

const props = withDefaults(defineProps<{ open:boolean; title:string; description:string; confirmText?:string; cancelText?:string; busy?:boolean }>(), { confirmText:'确认', cancelText:'取消', busy:false })
const emit = defineEmits<{ 'update:open':[value:boolean]; confirm:[]; cancel:[] }>()
const open = computed({ get:() => props.open, set:value => { if (!value && !props.busy) { emit('update:open',false); emit('cancel') } } })
function cancel() { if (!props.busy) { emit('update:open',false); emit('cancel') } }
function confirm() { if (!props.busy) emit('confirm') }
</script>
<template>
  <Dialog v-model:open="open"><DialogContent :show-close-button="!busy"><DialogHeader><DialogTitle>{{ title }}</DialogTitle><DialogDescription style="white-space: pre-line; overflow-wrap: anywhere">{{ description }}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" :disabled="busy" @click="cancel">{{ cancelText }}</Button><Button :disabled="busy" @click="confirm">{{ confirmText }}</Button></DialogFooter></DialogContent></Dialog>
</template>
