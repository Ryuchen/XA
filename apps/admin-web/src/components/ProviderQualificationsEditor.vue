<template>
  <div ref="root" class="qual-editor">
    <div class="qual-intro">
      <span>按游戏和服务范围配置最高档位</span>
      <Popover><PopoverTrigger as-child><button type="button" class="qual-help" aria-label="接单资格说明">?</button></PopoverTrigger><PopoverContent class="qual-popover">同一游戏、方向、平台和难度只能配置一个最高档位；同范围可接低档，不同范围互不授权。参考时价仅用于展示和排序，订单价格以服务商品为准。</PopoverContent></Popover>
    </div>
    <Alert v-if="loadError"><AlertDescription>资格选项读取失败，暂不能保存已有资格 <Button variant="link" :disabled="loading" @click="loadOptions">重新读取</Button></AlertDescription></Alert>
    <section v-for="(row, index) in modelValue" :key="index" class="qual-card" :aria-label="`资格 ${index + 1}`">
      <div class="qual-card-head"><strong>资格 {{ index + 1 }}</strong><span>{{ row.is_active ? '已启用' : '已停用' }}</span></div>
      <div class="qual-grid">
        <label class="qual-field"><span>游戏</span><NativeSelect v-model="row.game_category" :disabled="disabled" :aria-label="`资格 ${index + 1} 游戏`"><NativeSelectOption v-for="g in games" :key="g.id" :value="g.id" :disabled="!g.is_active">{{ g.name }}{{ g.is_active ? '' : '（已停用）' }}</NativeSelectOption></NativeSelect></label>
        <label class="qual-field"><span>服务方向</span><NativeSelect v-model="row.provider_kind" :disabled="disabled || !ready" :aria-label="`资格 ${index + 1} 服务方向`" @update:model-value="normalize(row, index)"><NativeSelectOption v-if="!ready && row.provider_kind" :value="row.provider_kind">{{ kindLabels[row.provider_kind] || row.provider_kind }}</NativeSelectOption><NativeSelectOption v-for="k in options.kinds" :key="k.value" :value="k.value">{{ k.label }}</NativeSelectOption></NativeSelect></label>
        <label class="qual-field"><span>平台</span><NativeSelect v-model="row.platform" :disabled="disabled || !ready" :aria-label="`资格 ${index + 1} 平台`" @update:model-value="normalize(row, index)"><NativeSelectOption v-if="!ready && row.platform" :value="row.platform">{{ platformLabels[row.platform] || row.platform }}</NativeSelectOption><NativeSelectOption v-for="p in options.platforms" :key="p.value" :value="p.value">{{ p.label }}</NativeSelectOption></NativeSelect></label>
        <label class="qual-field"><span>难度</span><NativeSelect v-model="row.security_level" :disabled="disabled || !ready" :aria-label="`资格 ${index + 1} 难度`" @update:model-value="normalize(row, index)"><NativeSelectOption v-if="!ready && row.security_level" :value="row.security_level">{{ names[row.security_level] || row.security_level }}</NativeSelectOption><NativeSelectOption v-for="s in securities(row)" :key="s.value" :value="s.value">{{ s.label }}</NativeSelectOption></NativeSelect></label>
        <label class="qual-field"><span>最高档位</span><NativeSelect v-model="row.grade" :disabled="disabled || !ready" :aria-label="`资格 ${index + 1} 最高档位`"><NativeSelectOption v-if="!ready && row.grade" :value="row.grade">{{ row.grade_name || gradeLabels[row.grade] || row.grade }}</NativeSelectOption><NativeSelectOption v-for="g in scopeGrades(options,row)" :key="g.value" :value="g.value">{{ g.label }}</NativeSelectOption></NativeSelect></label>
        <label class="qual-field"><span>参考时价（币）</span><Input :model-value="row.price_per_hour == null ? '' : toCoin(row.price_per_hour)" type="number" :disabled="disabled" min="0" step="0.1" placeholder="选填" :aria-label="`资格 ${index + 1} 参考时价兴安币`" @update:model-value="row.price_per_hour = $event === '' ? null : toRawAmount($event)" /></label>
      </div>
      <p v-if="notices[index]" class="qual-note" role="status">{{ notices[index] }}</p>
      <p v-for="message in rowErrors[index]" :key="message" class="qual-error" role="alert">{{ message }}</p>
      <div class="qual-actions">
        <label class="qual-switch"><Switch v-model="row.is_active" :disabled="disabled" :aria-label="`资格 ${index + 1} 启用状态`" />启用</label>
        <Button variant="outline" :disabled="disabled" @click="confirmRemove(index)">移除</Button>
      </div>
    </section>
    <p v-if="!modelValue?.length" class="qual-note">尚未配置接单资格</p>
    <p v-if="attempted && modelValue.length && !ready" class="qual-error" role="alert">请先重新读取资格选项。</p>
    <p v-if="modelValue.length >= 80" class="qual-note">最多配置 80 条资格。</p>
    <p v-else-if="ready && !games.some(g => g.is_active)" class="qual-note">没有可用游戏，请先启用游戏。</p>
    <Button variant="outline" :disabled="disabled || !ready || modelValue.length >= 80 || !games.some(g => g.is_active)" @click="add">添加接单资格</Button>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { toCoin, toRawAmount } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Switch } from '@xa/shadcn-vue/switch'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { qualificationOptions, scopeGrades } from '@/utils/qualifications'

const props = defineProps<{ modelValue: any[]; games: any[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: any[]] }>()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const root = ref<HTMLElement>()
const options = ref<any>({ kinds: [], platforms: [], scopes: [] })
const loading = ref(false)
const loadError = ref(false)
const attempted = ref(false)
const notices = ref<string[]>([])
const ready = computed(() => !loading.value && !loadError.value && options.value.scopes.length > 0)

async function loadOptions() {
  if (loading.value) return
  loading.value = true; loadError.value = false
  try {
    const result = await qualificationOptions()
    if (!Array.isArray(result?.scopes) || !result.scopes.length) throw new Error('资格选项为空')
    options.value = result
  } catch { loadError.value = true } finally { loading.value = false }
}
onMounted(loadOptions)

const names: Record<string, string> = { ANY: '不限难度', CONFIDENTIAL: '机密', TOP_SECRET: '绝密' }
const kindLabels: Record<string, string> = { GUARD: '护航', COMPANION: '陪玩', GRIND: '代肝', RANK: '上分' }
const platformLabels: Record<string, string> = { PC: '端游', MOBILE: '手游' }
const gradeLabels: Record<string, string> = { ENTERTAINMENT: '娱乐', TECHNICAL: '技术', MASTER: '大师', KING: '王者', LEGEND: '传奇', GLORY: '荣耀', STAR: '明星', OPEN: '不分档' }
const securities = (row: any) => [...new Set<string>(options.value.scopes.filter((s: any) => s.provider_kind === row.provider_kind && s.platform === row.platform).map((s: any) => s.security_level))].map(value => ({ value, label: names[value] || value }))
function normalize(row: any, index: number) {
  const previousSecurity = row.security_level, previousGrade = row.grade
  const choices = securities(row)
  if (!choices.some(s => s.value === row.security_level)) row.security_level = choices[0]?.value || ''
  if (!scopeGrades(options.value, row).some((g: any) => g.value === row.grade)) row.grade = scopeGrades(options.value, row)[0]?.value || ''
  notices.value[index] = previousSecurity !== row.security_level || previousGrade !== row.grade ? '范围变化后已调整难度或档位，请核对。' : ''
}
const rowErrors = computed(() => {
  const keys = props.modelValue.map(row => [row.game_category, row.provider_kind, row.platform, row.security_level].map(String).join('|'))
  return props.modelValue.map((row, index) => {
    const messages: string[] = []
    const game = props.games.find(g => g.id === row.game_category)
    if (keys.indexOf(keys[index]) !== keys.lastIndexOf(keys[index])) messages.push('该游戏、方向、平台和难度已配置，请调整或移除重复项。')
    if (attempted.value && !game) messages.push('请选择有效游戏。')
    if (game && !game.is_active && row.is_active) messages.push('游戏已停用，请停用该资格或选择其他游戏。')
    if (attempted.value && ready.value && !scopeGrades(options.value, row).some((g: any) => g.value === row.grade)) messages.push('请选择该方向、平台和难度支持的档位。')
    return messages
  })
})
function add() {
  if (props.disabled || !ready.value || props.modelValue.length >= 80) return
  const game = props.games.find(g => g.is_active)
  if (!game) return
  emit('update:modelValue', [...props.modelValue, { game_category: game.id, provider_kind: 'COMPANION', platform: 'PC', security_level: 'ANY', grade: 'ENTERTAINMENT', price_per_hour: null, is_active: true }])
}
function remove(index: number) {
  if (props.disabled) return
  notices.value.splice(index, 1)
  emit('update:modelValue', props.modelValue.filter((_, n) => n !== index))
}
async function confirmRemove(index:number) {
  const item = props.modelValue[index]
  if (props.disabled || !item) return
  if (await askConfirm({title:'移除资格',description:'移除这条资格？保存资料后生效。',confirmText:'移除',cancelText:'保留'})) {
    const current = props.modelValue.indexOf(item)
    if (current >= 0) remove(current)
  }
}
function validate() {
  attempted.value = true
  const valid = props.modelValue.length <= 80 && (!props.modelValue.length || ready.value) && rowErrors.value.every(messages => !messages.length)
  if (!valid) void nextTick(() => root.value?.querySelector<HTMLElement>('.qual-error, [role="alert"]')?.scrollIntoView({ block: 'nearest' }))
  return valid
}
function reset() { attempted.value = false; notices.value = [] }
defineExpose({ validate, reset })
</script>

<style scoped>
.qual-editor { width: 100%; min-width: 0; }
.qual-intro { display: flex; align-items: center; gap: 4px; color: var(--muted-foreground); font-size: 13px; }
.qual-help { width: var(--control-height-sm); height: var(--control-height-sm); border: 0; border-radius: 50%; color: var(--primary); background: transparent; cursor: pointer; }
.qual-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.qual-card { padding: 14px; margin: 0 0 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.qual-card-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.qual-card-head span, .qual-field > span { color: var(--muted-foreground); font-size: 12px; }
.qual-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.qual-field { display: grid; min-width: 0; gap: 5px; }
.qual-field :deep([data-slot='native-select-wrapper']) { width: 100%; }
.qual-popover { width:280px; background:var(--card); color:var(--foreground); font-size:13px; line-height:1.6; }
.qual-switch { display:flex; align-items:center; gap:8px; }
.qual-note, .qual-error { font-size: 12px; line-height: 1.6; margin: 8px 0; }
.qual-note { color: var(--muted-foreground); }
.qual-error { color: var(--destructive); }
.qual-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 10px; }
@media (min-width: 1100px) { .qual-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
</style>
