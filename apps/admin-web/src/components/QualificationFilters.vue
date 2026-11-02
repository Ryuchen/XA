<template>
  <NativeSelect v-model="query.provider_kind" aria-label="服务方向" @update:model-value="change"><NativeSelectOption value="">服务方向</NativeSelectOption><NativeSelectOption v-for="k in options.kinds" :key="k.value" :value="k.value">{{ k.label }}</NativeSelectOption></NativeSelect>
  <NativeSelect v-model="query.platform" aria-label="端手游" @update:model-value="change"><NativeSelectOption value="">端手游</NativeSelectOption><NativeSelectOption v-for="p in options.platforms" :key="p.value" :value="p.value">{{ p.label }}</NativeSelectOption></NativeSelect>
  <NativeSelect v-model="query.security_level" aria-label="难度" @update:model-value="change"><NativeSelectOption value="">难度</NativeSelectOption><NativeSelectOption v-if="query.provider_kind !== 'GUARD'" value="ANY">不限难度</NativeSelectOption><NativeSelectOption v-if="!query.provider_kind || query.provider_kind === 'GUARD'" value="CONFIDENTIAL">机密</NativeSelectOption><NativeSelectOption v-if="!query.provider_kind || query.provider_kind === 'GUARD'" value="TOP_SECRET">绝密</NativeSelectOption></NativeSelect>
  <NativeSelect v-model="query.grade" aria-label="最高档位" :disabled="!grades.length" @update:model-value="emit('change')"><NativeSelectOption value="">最高档位</NativeSelectOption><NativeSelectOption v-for="g in grades" :key="g.value" :value="g.value">{{ g.label }}</NativeSelectOption></NativeSelect>
</template>
<script setup lang="ts">
import { computed,ref,onMounted } from 'vue'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { qualificationOptions,scopeGrades } from '@/utils/qualifications'
const props=defineProps<{query:Record<string,any>}>();const emit=defineEmits(['change'])
const options=ref<any>({kinds:[],platforms:[],scopes:[]});onMounted(async()=>{options.value=await qualificationOptions()})
const grades=computed(()=>scopeGrades(options.value,props.query))
function change(){props.query.grade='';if(props.query.provider_kind && props.query.provider_kind!=='GUARD')props.query.security_level='ANY';emit('change')}
</script>
