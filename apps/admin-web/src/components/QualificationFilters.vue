<template>
  <el-select v-model="query.provider_kind" placeholder="服务方向" clearable style="width:110px" @change="change"><el-option v-for="k in options.kinds" :key="k.value" :value="k.value" :label="k.label" /></el-select>
  <el-select v-model="query.platform" placeholder="端手游" clearable style="width:100px" @change="change"><el-option v-for="p in options.platforms" :key="p.value" :value="p.value" :label="p.label" /></el-select>
  <el-select v-model="query.security_level" placeholder="难度" clearable style="width:110px" @change="change"><el-option v-if="query.provider_kind !== 'GUARD'" value="ANY" label="不限难度"/><el-option v-if="!query.provider_kind || query.provider_kind === 'GUARD'" value="CONFIDENTIAL" label="机密"/><el-option v-if="!query.provider_kind || query.provider_kind === 'GUARD'" value="TOP_SECRET" label="绝密"/></el-select>
  <el-select v-model="query.grade" placeholder="最高档位" clearable :disabled="!grades.length" style="width:110px" @change="emit('change')"><el-option v-for="g in grades" :key="g.value" :value="g.value" :label="g.label" /></el-select>
</template>
<script setup lang="ts">
import { computed,ref,onMounted } from 'vue'
import { qualificationOptions,scopeGrades } from '@/utils/qualifications'
const props=defineProps<{query:Record<string,any>}>();const emit=defineEmits(['change'])
const options=ref<any>({kinds:[],platforms:[],scopes:[]});onMounted(async()=>{options.value=await qualificationOptions()})
const grades=computed(()=>scopeGrades(options.value,props.query))
function change(){props.query.grade='';if(props.query.provider_kind && props.query.provider_kind!=='GUARD')props.query.security_level='ANY';emit('change')}
</script>
