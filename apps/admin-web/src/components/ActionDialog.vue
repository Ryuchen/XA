<script setup lang="ts">
import { defineComponent, h, type VNode } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
const props=defineProps<{open:boolean;title:string;mode:'confirm'|'prompt';message:string|VNode;input:string;error:string;options:{confirmButtonText?:string;cancelButtonText?:string;closeOnClickModal?:boolean;closeOnPressEscape?:boolean;inputPlaceholder?:string}}>()
const emit=defineEmits<{'update:input':[value:string];accept:[];cancel:[]}>()
const Message=defineComponent({setup:()=>()=>typeof props.message==='string'?h('p',{style:'white-space:pre-line'},props.message):props.message})
</script>
<template>
  <Dialog :open="open" @update:open="value=>{if(!value)emit('cancel')}"><DialogContent class="action-dialog" @interact-outside="event=>{if(options.closeOnClickModal===false)event.preventDefault()}" @escape-key-down="event=>{if(options.closeOnPressEscape===false)event.preventDefault()}">
    <DialogHeader><DialogTitle>{{title}}</DialogTitle><DialogDescription as="div"><Message /></DialogDescription></DialogHeader>
    <label v-if="mode==='prompt'" class="action-input">{{options.inputPlaceholder || '说明'}}<Textarea :model-value="input" :placeholder="options.inputPlaceholder" :aria-invalid="!!error" @update:model-value="value=>emit('update:input',String(value))" /></label>
    <p v-if="error" role="alert" class="action-error">{{error}}</p><DialogFooter><Button size="xs" variant="outline" @click="emit('cancel')">{{options.cancelButtonText || '取消'}}</Button><Button size="xs" @click="emit('accept')">{{options.confirmButtonText || '确定'}}</Button></DialogFooter>
  </DialogContent></Dialog>
</template>
<style scoped>
.action-input{display:grid;gap:8px}.action-error{color:var(--destructive)}
:global(.action-dialog){max-width:min(560px,calc(100vw - 24px));max-height:calc(100dvh - 24px);overflow-y:auto;}
</style>
