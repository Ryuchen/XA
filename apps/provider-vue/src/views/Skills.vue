<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { formatXaCoin } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { Input } from '@xa/shadcn-vue/input'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api } from '../lib/session'

type Item = { id:number;name:string;price:number;price_unit:string;required_level_name:string;platform:string;platform_name:string;security_level:string;security_level_name:string;qualified?:boolean;provider_requirement?:{label:string} }
type Group = {game_category_id:number;game_category_name:string;items:Item[]}
const router = useRouter(), groups = ref<Group[]>([]), selected = ref<Set<number>>(new Set()), saved = ref<Set<number>>(new Set())
const loading = ref(true), saving = ref(false), error = ref(''), notice = ref(''), keyword = ref(''), game = ref(''), platform = ref(''), difficulty = ref(''), selectedOnly = ref(false), limit = ref(24), clearDialog = ref(false), refreshDialog = ref(false)
let sequence = 0, alive = true
const dirty = computed(() => selected.value.size !== saved.value.size || [...selected.value].some(id => !saved.value.has(id)))
const offered = computed(() => new Set(groups.value.flatMap(group => group.items.map(item => item.id))))
const retired = computed(() => [...selected.value].filter(id => !offered.value.has(id)))
const selectedGames = computed(() => groups.value.filter(group => group.items.some(item => selected.value.has(item.id))).length)
const visible = computed(() => groups.value.filter(group => !game.value || String(group.game_category_id) === game.value).map(group => ({...group,items:group.items.filter(item => `${group.game_category_name} ${item.name}`.includes(keyword.value.trim()) && (!platform.value || item.platform === platform.value) && (!difficulty.value || item.security_level === difficulty.value) && (!selectedOnly.value || selected.value.has(item.id)))})).filter(group => group.items.length))
async function load() {
  if (saving.value) return
  const current = ++sequence; loading.value = true; error.value = ''
  try {
    const [options,profile] = await Promise.all([api<Group[]>('/users/escorts/skill-options/'),api<{service_item_ids:number[]}>('/users/escorts/me/')])
    if (!alive || current !== sequence) return
    groups.value = options; selected.value = new Set(profile.service_item_ids || []); saved.value = new Set(selected.value); game.value = ''
  } catch (cause) { if (alive && current === sequence) error.value = cause instanceof Error ? cause.message : '加载失败' }
  finally { if (alive && current === sequence) loading.value = false }
}
function toggle(id:number) { if (saving.value) return; const next = new Set(selected.value); next.has(id) ? next.delete(id) : next.add(id); selected.value = next }
async function submit() {
  if (saving.value || loading.value || !!error.value || !dirty.value || retired.value.length) return
  clearDialog.value = false; saving.value = true; notice.value = ''
  try { const result = await api<{service_item_ids:number[]}>('/users/escorts/me/',{method:'PATCH',data:{service_items:[...selected.value]}}); selected.value = new Set(result.service_item_ids || []); saved.value = new Set(selected.value); notice.value = '技能已同步' }
  catch (cause) { notice.value = cause instanceof Error ? cause.message : '保存失败' } finally { saving.value = false }
}
function save() { if (!selected.value.size) clearDialog.value = true; else void submit() }
function refresh() { if (dirty.value) refreshDialog.value = true; else void load() }
onMounted(() => { void load() })
onUnmounted(() => { alive = false; sequence++ })
</script>
<template>
  <main class="page stack skills-page"><header class="skills-header"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>我的技能</h1></div><div class="actions"><Button size="xs" variant="outline" :disabled="saving" @click="refresh">刷新</Button><Button size="xs" variant="outline" @click="router.push('/mine')">返回个人中心</Button></div></header>
    <Alert v-if="notice" role="status" class="notice"><AlertDescription>{{ notice }}</AlertDescription></Alert><div v-if="loading" class="loading-grid" aria-label="正在加载服务目录"><Skeleton v-for="n in 3" :key="n" class="skill-skeleton"/></div><Alert v-else-if="error" variant="destructive" role="alert"><AlertTitle>服务目录加载失败</AlertTitle><AlertDescription>{{ error }} <Button size="xs" variant="outline" @click="load">重新加载</Button></AlertDescription></Alert>
    <template v-else><Card class="panel summary-card"><div class="selection-summary"><strong>{{ selected.size - retired.length }}<span>项技能</span></strong><strong>{{ selectedGames }}<span>款游戏</span></strong></div><p class="muted">保存后同步老板选人列表与后台档案，接单仍需满足认证、押金、档期和具体资格要求。</p></Card><Alert v-if="retired.length"><AlertTitle>请先移除失效项目</AlertTitle><AlertDescription>已选的 {{ retired.length }} 项服务已停用或调整，请移除后保存。 <Button size="xs" variant="outline" :disabled="saving" @click="selected = new Set([...selected].filter(id => offered.has(id)))">移除失效项目</Button></AlertDescription></Alert>
      <Card class="panel filter-card"><label class="search-field"><span>查找服务</span><Input v-model="keyword" placeholder="搜索游戏或服务名称" @input="limit = 24" /></label><div class="filters"><label><span>游戏</span><NativeSelect v-model="game" @update:model-value="limit = 24"><NativeSelectOption value="">全部游戏</NativeSelectOption><NativeSelectOption v-for="group in groups" :key="group.game_category_id" :value="String(group.game_category_id)">{{ group.game_category_name }}</NativeSelectOption></NativeSelect></label><label><span>平台</span><NativeSelect v-model="platform" @update:model-value="limit = 24"><NativeSelectOption value="">全部平台</NativeSelectOption><NativeSelectOption value="MOBILE">手游</NativeSelectOption><NativeSelectOption value="PC">端游</NativeSelectOption></NativeSelect></label><label><span>难度</span><NativeSelect v-model="difficulty" @update:model-value="limit = 24"><NativeSelectOption value="">全部难度</NativeSelectOption><NativeSelectOption value="CONFIDENTIAL">机密</NativeSelectOption><NativeSelectOption value="TOP_SECRET">绝密</NativeSelectOption><NativeSelectOption value="ANY">不限难度</NativeSelectOption></NativeSelect></label><Button size="xs" :variant="selectedOnly ? 'default' : 'outline'" :aria-pressed="selectedOnly" @click="selectedOnly = !selectedOnly; limit = 24">只看已选</Button></div></Card>
      <div class="save-bar"><div><strong>{{ dirty ? '有未保存的修改' : '已与服务档案同步' }}</strong><p class="muted">套餐售价不代表陪玩实得收入</p></div><Button size="xs" :disabled="saving || !dirty || !!retired.length" @click="save">{{ saving ? '保存中…' : '保存技能' }}</Button></div><Card v-if="!visible.length" class="panel empty-card"><p>{{ groups.length ? '没有匹配的服务，试试其他名称或游戏。' : '暂无开放的游戏服务，请联系运营配置。' }}</p></Card>
      <Card v-for="group in visible" :key="group.game_category_id" class="panel stack group-card"><div class="group-heading"><h2>{{ group.game_category_name }}</h2><Badge variant="secondary">{{ group.items.filter(item => selected.has(item.id)).length }} / {{ group.items.length }} 已选</Badge></div><div class="items"><label v-for="item in group.items.slice(0,limit)" :key="item.id" :for="`skill-${item.id}`" class="skill" :data-selected="selected.has(item.id)" :data-disabled="saving || (item.qualified === false && !selected.has(item.id))"><span class="skill-heading"><strong>{{ item.name }}</strong><Checkbox :id="`skill-${item.id}`" :model-value="selected.has(item.id)" :disabled="saving || (item.qualified === false && !selected.has(item.id))" :aria-label="item.name" @update:model-value="toggle(item.id)" /></span><span class="muted">{{ [item.platform_name,item.security_level_name].filter(Boolean).join(' · ') || '通用服务' }}</span><span class="skill-price">{{ formatXaCoin(item.price) }} <small>币 / {{ item.price_unit || '份' }}</small></span><span class="skill-requirement">{{ item.provider_requirement?.label ? `${item.provider_requirement.label} · ${item.qualified ? '资格已满足' : '需运营认证'}` : item.required_level_name ? `${item.required_level_name}及以上可接` : '历史服务规则' }}</span></label></div><div v-if="group.items.length > limit"><Button size="xs" variant="outline" @click="limit += 24">继续查看（还有 {{ group.items.length - limit }} 项）</Button></div></Card>
    </template>
    <Dialog v-model:open="clearDialog"><DialogContent class="skills-dialog"><DialogHeader><DialogTitle>清空服务技能</DialogTitle><DialogDescription>清空后，你将不再出现在具体游戏服务的选人列表，也无法接取这些服务的新订单。</DialogDescription></DialogHeader><DialogFooter><Button size="xs" variant="outline" @click="clearDialog = false">取消</Button><Button size="xs" @click="submit">确认清空</Button></DialogFooter></DialogContent></Dialog><Dialog v-model:open="refreshDialog"><DialogContent class="skills-dialog"><DialogHeader><DialogTitle>重新读取技能</DialogTitle><DialogDescription>当前有未保存的选择，刷新将放弃这些修改。</DialogDescription></DialogHeader><DialogFooter><Button size="xs" variant="outline" @click="refreshDialog = false">取消</Button><Button size="xs" @click="refreshDialog = false; load()">确认刷新</Button></DialogFooter></DialogContent></Dialog>
  </main>
</template>
<style scoped>
.skills-page { --compact-control-height:24px; gap:16px; } .skills-header,.save-bar,.group-heading,.skill-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; } .skills-header { margin-bottom:8px; flex-wrap:wrap; } .skills-header>div { min-width:0; }
h1 { margin:4px 0 0; font-size:24px; line-height:1.35; letter-spacing:-.5px; } h2 { margin:0; font-size:16px; font-weight:600; line-height:1.5; } p { margin:0; } p,h2,strong { overflow-wrap:anywhere; }
.skills-page .panel { padding:16px; border-radius:16px; gap:12px; min-width:0; box-shadow:0 1px 4px rgb(15 23 42 / 3%); } .actions { display:flex; flex-wrap:wrap; gap:8px; }
.selection-summary { display:flex; gap:24px; } .selection-summary strong { font-size:28px; font-weight:600; font-variant-numeric:tabular-nums; line-height:1.3; } .selection-summary span { margin-left:8px; font-size:12px; font-weight:400; color:var(--muted-foreground); }
.search-field,.filters label { display:grid; gap:8px; min-width:0; font-size:12px; color:var(--muted-foreground); } .filters { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)) auto; align-items:end; gap:12px; } .filters :deep([data-slot='native-select-wrapper']) { width:100%; } .skills-page :deep(input),.skills-page :deep(select) { height:28px; min-height:28px; padding:4px 8px; font-size:12px; color:var(--foreground); } .skills-page :deep(select) { padding-right:28px; }
.save-bar { padding:4px; } .save-bar>div { min-width:0; } .save-bar strong { font-size:14px; } .save-bar p { margin-top:4px; font-size:12px; } .save-bar>button { flex-shrink:0; }
.group-heading { flex-wrap:wrap; } .items,.loading-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr)); gap:8px; } .skill { display:grid; gap:8px; text-align:left; padding:12px; border:1px solid var(--border); border-radius:12px; background:var(--card); color:inherit; cursor:pointer; min-width:0; }
.skill-heading { align-items:start; gap:12px; font-size:14px; } .skill-heading :deep([data-slot='checkbox']) { width:16px; height:16px; min-width:16px; min-height:16px; padding:0; border:1px solid var(--input); border-radius:4px; background:var(--card); color:var(--primary-foreground); margin-top:4px; }
.skill-heading :deep([data-slot='checkbox'][data-state='checked']) { background:var(--primary); border-color:var(--primary); } .skill[data-selected=true] { border-color:var(--primary); background:var(--secondary); } .skill[data-disabled=true] { opacity:.6; cursor:not-allowed; } .skill:focus-within { outline:2px solid var(--primary); outline-offset:2px; }
.skill-price { font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; } .skill-price small { font-size:12px; font-weight:400; color:var(--muted-foreground); } .skill-requirement { padding-top:8px; border-top:1px solid var(--border); color:var(--muted-foreground); font-size:12px; line-height:1.5; overflow-wrap:anywhere; }
.notice { background:var(--secondary); color:var(--primary); } .notice :deep([data-slot='alert-description']) { color:inherit; } .skills-page :deep([data-slot='alert-description']) { display:flex; align-items:center; flex-wrap:wrap; gap:8px; } .skill-skeleton { height:160px; }
.skills-page :deep([data-slot='button']), :global(.skills-dialog [data-slot='button']) { min-height:var(--compact-control-height,24px); border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font-family:inherit; font-size:12px; box-shadow:none; }
.skills-page :deep([data-slot='button'][data-variant='outline']), :global(.skills-dialog [data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); } .skills-page :deep([data-slot='button']:focus-visible), :global(.skills-dialog [data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:4px; }
:global(.skills-dialog) { --compact-control-height:24px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; border-color:var(--border); border-radius:16px; padding:16px; max-height:calc(100dvh - 32px); overflow-y:auto; }
:global(.skills-dialog [data-slot='dialog-close']) { display:grid; place-items:center; width:44px; height:44px; right:4px; top:4px; padding:12px; border:0; border-radius:12px; background:transparent; color:var(--muted-foreground); } :global(.skills-dialog [data-slot='dialog-title']) { padding-right:32px; font-size:16px; }
@media(max-width:600px) { .skills-page { gap:12px; } .skills-header { gap:12px; margin-bottom:4px; } .skills-header .actions { margin-left:auto; } .filters { grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; } .filters>button { min-height:44px; } .items { grid-template-columns:minmax(0,1fr); } .selection-summary { gap:16px; } }
@media(max-width:600px),(pointer:coarse) { .skills-page :deep([data-slot='button']),:global(.skills-dialog [data-slot='button']) { min-width:44px; } .skills-page { --compact-control-height:44px; } .skills-page :deep(input),.skills-page :deep(select) { height:44px; min-height:44px; font-size:14px; } :global(.skills-dialog) { --compact-control-height:44px; } }
@media(prefers-reduced-motion:reduce) { .skills-page :deep(*) { transition:none; animation:none; } }
</style>
