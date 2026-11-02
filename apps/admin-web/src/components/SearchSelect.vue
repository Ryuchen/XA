<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Input } from '@xa/shadcn-vue/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@xa/shadcn-vue/select'
const props=defineProps<{modelValue?:string|number;options:{value:string|number;label:string}[];label:string;placeholder:string;loading?:boolean;disabled?:boolean;clearable?:boolean;remote?:boolean}>()
const emit=defineEmits<{'update:modelValue':[value:string|number|undefined];change:[value:string|number|undefined];search:[keyword:string]}>()
const keyword=ref('')
const selectedLabel=computed(()=>props.options.find(item=>item.value===props.modelValue)?.label)
const visibleOptions=computed(()=>props.remote?props.options:props.options.filter(item=>item.label.toLocaleLowerCase().includes(keyword.value.trim().toLocaleLowerCase())))
let timer:ReturnType<typeof setTimeout>|undefined
function search(value:string|number){keyword.value=String(value);if(timer)clearTimeout(timer);if(props.remote)timer=setTimeout(()=>emit('search',keyword.value),200)}
function changed(value:unknown){const next=value==='__clear'||!(typeof value==='string'||typeof value==='number')?undefined:value;emit('update:modelValue',next);emit('change',next)}
function opened(value:boolean){if(value){keyword.value='';if(props.remote)emit('search','')}}
onBeforeUnmount(()=>{if(timer)clearTimeout(timer)})
</script>
<template>
<Select :model-value="modelValue ?? ''" :disabled="disabled" @update:model-value="changed" @update:open="opened"><SelectTrigger size="sm" :aria-label="label"><SelectValue :placeholder="placeholder">{{selectedLabel || placeholder}}</SelectValue></SelectTrigger><SelectContent><Input :model-value="keyword" :aria-label="'搜索'+label" :placeholder="placeholder" @update:model-value="search" @keydown.stop /><p v-if="loading" role="status" class="search-state">搜索中…</p><SelectItem v-if="clearable" value="__clear">清空选择</SelectItem><SelectItem v-for="item in visibleOptions" :key="item.value" :value="item.value">{{item.label}}</SelectItem><p v-if="!loading && !visibleOptions.length" class="search-state">{{remote && !keyword ? '输入关键词搜索' : '暂无匹配结果'}}</p></SelectContent></Select>
</template>
<style scoped>.search-state{padding:8px;font-size:12px;color:var(--muted-foreground)}</style>
