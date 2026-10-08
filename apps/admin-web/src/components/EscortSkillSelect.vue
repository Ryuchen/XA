<template>
  <el-select :model-value="modelValue" multiple filterable collapse-tags collapse-tags-tooltip placeholder="按游戏选择可接服务" style="width:100%" @update:model-value="change">
    <el-option-group v-for="group in groups" :key="group.id" :label="group.name">
      <el-option v-for="item in group.items" :key="item.id" :value="item.id" :label="label(item)" />
    </el-option-group>
    <el-option v-for="id in unavailable" :key="id" :value="id" :label="`已停用或调整的服务 #${id}`" disabled />
  </el-select>
  <div class="skill-hint">已选 {{ modelValue.length }} 项。服务所属游戏会自动关联；停用项目保留档案，可点击标签移除。</div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { amountToXaCoin } from '@/utils/format'
const props = defineProps<{ modelValue: number[]; games: any[]; items: any[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: number[]]; 'games-selected': [value: number[]] }>()
const groups = computed(() => props.games.map(game => ({ ...game, items: props.items.filter(item => item.game_category_id === game.id) })).filter(game => game.items.length))
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
</script>
<style scoped>
.skill-hint { color: var(--muted-foreground); font-size: 12px; line-height: 1.6; margin-top: 6px; }
</style>
