<template>
  <Popover><PopoverTrigger as-child><Button variant="outline" class="skill-trigger">按游戏选择可接服务 · 已选 {{ modelValue.length }} 项</Button></PopoverTrigger><PopoverContent class="skill-picker"><Input v-model="keyword" placeholder="搜索游戏或服务" aria-label="搜索游戏或服务" /><div class="skill-options"><section v-for="group in filtered" :key="group.id"><strong>{{ group.name }}</strong><label v-for="item in group.items" :key="item.id" class="skill-option"><Checkbox :model-value="modelValue.includes(item.id)" @update:model-value="value => toggle(item.id, value === true)" /><span>{{ label(item) }}</span></label></section><p v-if="!filtered.length" class="skill-hint">没有匹配的服务</p><section v-if="unavailable.length"><strong>已停用或调整</strong><label v-for="id in unavailable" :key="id" class="skill-option"><Checkbox :model-value="true" @update:model-value="value => toggle(id, value === true)" /><span>服务 #{{ id }}（取消选择可移除）</span></label></section></div></PopoverContent></Popover>
  <div class="skill-hint">已选 {{ modelValue.length }} 项。服务所属游戏会自动关联；停用项目保留档案，可点击标签移除。</div>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { amountToXaCoin } from '@/utils/format'
const props = defineProps<{ modelValue: number[]; games: any[]; items: any[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: number[]]; 'games-selected': [value: number[]] }>()
const groups = computed(() => props.games.map(game => ({ ...game, items: props.items.filter(item => item.game_category_id === game.id) })).filter(game => game.items.length))
const keyword = ref('')
const filtered = computed(() => groups.value.map(group => ({...group,items:group.items.filter((item:any) => `${group.name} ${label(item)}`.toLowerCase().includes(keyword.value.trim().toLowerCase()))})).filter(group => group.items.length))
const unavailable = computed(() => props.modelValue.filter(id => !props.items.some(item => item.id === id)))
function label(item: any) {
  const platform = ({ MOBILE: '手游', PC: '端游' } as Record<string, string>)[item.platform]
  const difficulty = ({ CONFIDENTIAL: '机密', TOP_SECRET: '绝密', ANY: '不限难度' } as Record<string, string>)[item.security_level]
  return [platform, difficulty, item.name, `${amountToXaCoin(item.price)}币/${item.price_unit || '份'}`].filter(Boolean).join(' · ')
}
function change(ids: number[]) {
  emit('update:modelValue', ids)
  emit('games-selected', [...new Set(props.items.filter(item => ids.includes(item.id)).map(item => item.game_category_id))])
}
function toggle(id:number, value:boolean) { change(value ? [...new Set([...props.modelValue,id])] : props.modelValue.filter(item => item !== id)) }
</script>
<style scoped>
.skill-hint { color: var(--muted-foreground); font-size: 12px; line-height: 1.6; margin-top: 6px; }
.skill-trigger { width:100%; justify-content:flex-start; }
.skill-picker { width:min(460px,calc(100vw - 32px)); background:var(--card); color:var(--foreground); }
.skill-options { max-height:320px; overflow:auto; display:grid; gap:12px; margin-top:8px; }
.skill-options section { display:grid; gap:8px; }
.skill-option { display:flex; gap:8px; align-items:flex-start; font-size:13px; }
</style>
