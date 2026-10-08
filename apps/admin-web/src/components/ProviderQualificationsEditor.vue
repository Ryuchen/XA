<template>
  <div class="qual-editor">
    <p class="qual-note">每个游戏、服务方向、平台和难度配置一个最高档位。同范围内可接低档；不同范围互不授权。</p>
    <div v-for="(row, index) in modelValue" :key="index" class="qual-row">
      <el-select v-model="row.game_category" placeholder="游戏" aria-label="资格游戏"><el-option v-for="g in games" :key="g.id" :value="g.id" :label="g.name" :disabled="!g.is_active" /></el-select>
      <el-select v-model="row.provider_kind" placeholder="方向" aria-label="服务方向" @change="normalize(row)"><el-option v-for="k in options.kinds" :key="k.value" :value="k.value" :label="k.label" /></el-select>
      <el-select v-model="row.platform" placeholder="平台" aria-label="游戏平台" @change="normalize(row)"><el-option v-for="p in options.platforms" :key="p.value" :value="p.value" :label="p.label" /></el-select>
      <el-select v-model="row.security_level" placeholder="难度" aria-label="行动难度" @change="normalize(row)"><el-option v-for="s in securities(row)" :key="s.value" :value="s.value" :label="s.label" /></el-select>
      <el-select v-model="row.grade" placeholder="最高档位" aria-label="最高接单档位"><el-option v-for="g in scopeGrades(options,row)" :key="g.value" :value="g.value" :label="g.label" /></el-select>
      <el-input-number :model-value="row.price_per_hour == null ? undefined : row.price_per_hour / 10" :min="0" :precision="1" placeholder="时价(币)" aria-label="参考时价兴安币" @update:model-value="row.price_per_hour = $event == null ? null : Math.round($event * 10)" />
      <el-switch v-model="row.is_active" active-text="启用" />
      <el-button type="danger" text @click="remove(index)">移除</el-button>
    </div>
    <el-empty v-if="!modelValue?.length" description="尚未配置接单资格" :image-size="48" />
    <el-button :disabled="!options.scopes?.length" @click="add">添加接单资格</el-button>
    <p class="qual-note">参考时价用于展示和排序；订单价格与抽成按服务项目的订单档位执行。通行证另行配置。</p>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { qualificationOptions, scopeGrades } from '@/utils/qualifications'
const props = defineProps<{ modelValue: any[]; games: any[] }>()
const emit = defineEmits(['update:modelValue'])
const options = ref<any>({ kinds: [], platforms: [], scopes: [] })
onMounted(async () => { options.value = await qualificationOptions() })
const names: Record<string,string> = { ANY: '不限难度', CONFIDENTIAL: '机密', TOP_SECRET: '绝密' }
const securities = (row: any) => [...new Set<string>(options.value.scopes.filter((s: any) => s.provider_kind === row.provider_kind && s.platform === row.platform).map((s: any) => s.security_level))].map(value => ({value,label:names[value]}))
function normalize(row: any) { const choices=securities(row); if (!choices.some(s => s.value === row.security_level)) row.security_level=choices[0]?.value || ''; if (!scopeGrades(options.value,row).some((g: any) => g.value === row.grade)) row.grade=scopeGrades(options.value,row)[0]?.value || '' }
function add() { emit('update:modelValue',[...(props.modelValue || []),{ game_category:props.games.find(g => g.is_active)?.id, provider_kind:'COMPANION', platform:'PC', security_level:'ANY', grade:'ENTERTAINMENT', price_per_hour:null, is_active:true }]) }
function remove(i: number) { emit('update:modelValue',props.modelValue.filter((_,n) => n!==i)) }
</script>
<style scoped>
.qual-editor{width:100%;min-width:0}.qual-note{font-size:12px;color:var(--el-text-color-secondary);line-height:1.6;margin:8px 0}.qual-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding:12px 0;border-bottom:1px solid var(--el-border-color-lighter)}.qual-row .el-select,.qual-row .el-input-number{width:100%}@media(min-width:1100px){.qual-row{grid-template-columns:repeat(4,minmax(0,1fr))}}
</style>
