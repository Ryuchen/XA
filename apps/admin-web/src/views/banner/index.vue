<template>
  <div class="bn-page">
    <Card class="bn-panel">
    <header class="bn-heading">
      <div><h2>首页轮播</h2><p class="tip">目标状态以最近一次读取为准。</p></div>
      <div class="bn-actions">
        <Button size="xs" variant="outline" @click="load">刷新状态</Button>
        <Button size="xs" v-if="auth.hasPerm('banner:edit')" @click="onCreate">新增轮播</Button>
      </div>
    </header>
    <p v-if="notice && !dialogVisible" role="status" class="bn-notice">{{ notice }}</p>

    <Alert v-if="listError" variant="destructive"><AlertDescription>{{ listError }} <Button size="xs" variant="link" @click="load">重新加载</Button></AlertDescription></Alert>
    <p v-if="loading" role="status">正在加载轮播图…</p>
    <template v-else-if="!listError">
      <p class="tip">共 {{ total }} 条</p>
      <p v-if="!rows.length" class="bn-empty">暂无轮播图</p>
      <div v-else class="bn-table" role="region" aria-label="轮播列表">
        <Table>
          <TableHeader><TableRow>
            <TableHead>轮播图</TableHead><TableHead>跳转目标</TableHead><TableHead>排序</TableHead>
            <TableHead>状态</TableHead><TableHead v-if="canManage">操作</TableHead>
          </TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id">
            <TableCell class="banner-info">
              <div class="bn-identity">
                <img v-if="row.image_url" :src="row.image_url" alt="" class="bn-thumbnail" />
                <div><h3>{{ row.title || '未填写标题' }}</h3><p class="tip">ID {{ row.id }}</p></div>
              </div>
            </TableCell>
            <TableCell class="banner-target">
              <span>{{ row.link_type_display }}</span><p v-if="row.link_value" class="tip">目标 {{ row.link_value }}</p>
              <Popover v-if="row.link_available === false">
                <PopoverTrigger as-child><Button size="xs" variant="link" class="bn-warning">目标不可用</Button></PopoverTrigger>
                <PopoverContent class="bn-warning-help">目标已停用、下架或尚未开放。老板点击时会收到提示。请更换目标或停用轮播。商品还可能受老板资格限制。</PopoverContent>
              </Popover>
            </TableCell>
            <TableCell class="banner-order" data-label="排序">{{ row.sort_order }}</TableCell>
            <TableCell class="banner-state" data-label="状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
            <TableCell v-if="canManage" class="banner-actions"><div class="bn-actions">
              <Button size="xs" v-if="auth.hasPerm('banner:edit')" variant="outline" @click="onEdit(row)">编辑</Button>
              <Button size="xs" v-if="auth.hasPerm('banner:delete')" variant="outline" :disabled="deleting" @click="onDelete(row)">删除</Button>
            </div></TableCell>
          </TableRow></TableBody>
        </Table>
      </div>
      <div v-if="total" class="bn-pages"><span>第 {{ page }} / {{ pageCount }} 页</span><NativeSelect :model-value="pageSize" aria-label="每页条数" @update:model-value="changeSize"><NativeSelectOption v-for="size in ['10','20','50','100']" :key="size" :value="size">{{ size }} 条/页</NativeSelectOption></NativeSelect><Button size="xs" variant="outline" :disabled="page <= 1" @click="changePage(page - 1)">上一页</Button><Button size="xs" variant="outline" :disabled="page >= pageCount" @click="changePage(page + 1)">下一页</Button></div>
    </template>
    </Card>
  </div>

  <Dialog :open="dialogVisible" @update:open="value => { if (!saving) dialogVisible = value }">
    <DialogContent :show-close-button="!saving" class="bn-dialog">
    <DialogHeader><DialogTitle>{{ form.id ? '编辑轮播' : '新增轮播' }}</DialogTitle><DialogDescription>设置首页图片和跳转目标。</DialogDescription></DialogHeader>
    <Alert v-if="form.link_available === false && form.link_type === editedTarget.type && form.link_value === editedTarget.value"><AlertTitle>当前目标不可用</AlertTitle><AlertDescription>请更换目标或停用轮播。老板的商品资格以实际账号为准。</AlertDescription></Alert>
    <Alert v-if="targetFailures.length"><AlertTitle>部分目标加载失败</AlertTitle><AlertDescription>
      <p>{{ targetFailures.join('、') }}暂未加载，请重试；已有目标仍可保留。</p>
      <Button size="xs" variant="outline" :disabled="targetsLoading" @click="loadTargets">{{ targetsLoading ? '加载中…' : '重新加载目标' }}</Button>
    </AlertDescription></Alert>
    <div class="bn-form">
      <label>图片<img v-if="previewUrl" :src="previewUrl" alt="轮播图预览" class="bn-preview" /><input aria-label="上传轮播图片" type="file" accept="image/*" :disabled="saving" @change="onFileChange" /><span class="tip">必须为 {{ IMG_W }}×{{ IMG_H }} 像素</span></label>
      <label>标题<Input maxlength="100" v-model="form.title" :disabled="saving" /></label>
      <label>跳转类型<NativeSelect aria-label="跳转类型" v-model="form.link_type" :disabled="saving"><NativeSelectOption v-for="(item, key) in LINK_TYPE" :key="key" :value="key">{{ item.label }}</NativeSelectOption></NativeSelect></label>
      <div v-if="['PRODUCT','ANNOUNCEMENT','GAMEPLAY','CONTENT','PAGE'].includes(form.link_type)" class="bn-target-field">
        <label>搜索跳转目标<Input v-model="targetSearch" placeholder="按名称搜索" :disabled="saving" /></label>
        <label>跳转目标<NativeSelect aria-label="跳转目标" v-model="form.link_value" :disabled="saving"><NativeSelectOption value="">请选择</NativeSelectOption><NativeSelectOption v-if="form.link_value && !targetOptions.some(item => String(item.id) === String(form.link_value))" :value="String(form.link_value)">当前目标 #{{ form.link_value }}（未加载）</NativeSelectOption><NativeSelectOption v-for="item in filteredTargets" :key="item.id" :value="String(item.id)">{{ item.name || item.title }}{{ item.is_active === false ? '（已停用）' : '' }}</NativeSelectOption></NativeSelect></label>
      </div>
      <label v-if="form.link_type === 'URL'">外部链接<Input v-model="form.link_value" placeholder="https://" :disabled="saving" /><span class="tip">老板端点击后复制链接，可在浏览器打开。</span></label>
      <label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" :disabled="saving" /></label>
      <label class="bn-switch">启用<Switch v-model="form.is_active" aria-label="启用" :disabled="saving" /></label>
    </div>
    <p v-if="notice" role="status" class="bn-notice">{{ notice }}</p>
    <DialogFooter>
      <Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">取消</Button>
      <Button size="xs" :disabled="saving" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button>
    </DialogFooter>
    </DialogContent>
  </Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Switch } from '@xa/shadcn-vue/switch'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Alert, AlertTitle, AlertDescription } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { bannerApi, serviceItemApi, announcementApi, gameplayApi, clubContentApi } from '@/api/modules'
import { LINK_TYPE } from '@/utils/dict'
import { useAuthStore } from '@/stores/auth'

const IMG_W = 750
const IMG_H = 320

const auth = useAuthStore()
const canManage = computed(() => auth.hasPerm('banner:edit') || auth.hasPerm('banner:delete'))
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const deleting = ref(false)
const rows = ref<any[]>([]), total = ref(0), page = ref(1), pageSize = ref('10'), loading = ref(false), listError = ref('')
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / Number(pageSize.value))))
let loadSequence = 0, alive = true
async function load() {
  const sequence = ++loadSequence; loading.value = true; listError.value = ''
  try { const result = await bannerApi.list({ page: page.value, page_size: Number(pageSize.value) }); if (alive && sequence === loadSequence) { rows.value = result.data.list || []; total.value = result.data.total || 0 } }
  catch { if (alive && sequence === loadSequence) { rows.value = []; total.value = 0; listError.value = '轮播列表加载失败，请重试' } }
  finally { if (alive && sequence === loadSequence) loading.value = false }
}
function reload() { page.value = 1; void load() }
function changePage(next: number) { if (!loading.value && next >= 1 && next <= pageCount.value) { page.value = next; void load() } }
function changeSize(value: unknown) { const size = String(value); if (['10','20','50','100'].includes(size)) { pageSize.value = size; reload() } }
onMounted(() => { void load() }); onUnmounted(() => { alive = false; loadSequence++ })

const dialogVisible = ref(false)
const notice = ref('')
const saving = ref(false)
const previewUrl = ref('')
const pickedFile = ref<File | null>(null)
const form = reactive<any>({})
const editedTarget = reactive({ type: '', value: '' })
const serviceOptions = ref<any[]>([])
const announcementOptions = ref<any[]>([])
const gameplayOptions = ref<any[]>([])
const contentOptions = ref<any[]>([])
const targetFailures = ref<string[]>([])
const targetsLoading = ref(false)
const pageOptions = [{id:'catalog',name:'服务商城'},{id:'companions',name:'陪玩列表'},{id:'gifts',name:'礼物单'},{id:'coupons',name:'领券中心'},{id:'checkin',name:'每日签到'},{id:'points',name:'积分好礼'},{id:'membership',name:'会员中心'},{id:'support',name:'联系客服'}]
const targetSearch = ref('')
const targetOptions = computed<any[]>(() => ({ PRODUCT: serviceOptions.value, ANNOUNCEMENT: announcementOptions.value, GAMEPLAY: gameplayOptions.value, CONTENT: contentOptions.value, PAGE: pageOptions } as Record<string, any[]>)[form.link_type] || [])
const filteredTargets = computed(() => { const search = targetSearch.value.trim().toLowerCase(); return targetOptions.value.filter(item => String(item.id) === String(form.link_value) || String(item.name || item.title || '').toLowerCase().includes(search)) })

watch(() => form.link_type, (_type, oldType) => {
  targetSearch.value = ''
  if (dialogVisible.value && oldType) form.link_value = ''
}, { flush: 'sync' })

async function loadTargets() {
  if (targetsLoading.value) return
  targetsLoading.value = true
  try {
    const targets = await Promise.allSettled([
      serviceItemApi.allOptions({ is_active: '' }, true), announcementApi.allOptions({ is_active: '' }, true),
      gameplayApi.allOptions({ is_active: 'true' }, true), clubContentApi.allOptions({ is_active: 'true' }, true),
    ])
    const rows = (i: number) => targets[i].status === 'fulfilled' ? (targets[i] as PromiseFulfilledResult<any>).value.data.list || [] : []
    targetFailures.value = ['商品', '公告', '玩法', '活动与权益'].filter((_, index) => targets[index].status === 'rejected')
    serviceOptions.value = rows(0)
    announcementOptions.value = rows(1)
    gameplayOptions.value = rows(2)
    contentOptions.value = rows(3)
  } finally { targetsLoading.value = false }
}

function blank() {
  return { id: 0, title: '', link_type: 'NONE', link_value: '', sort_order: 0, is_active: true }
}

async function onCreate() {
  notice.value = ''
  await loadTargets()
  Object.assign(form, blank())
  Object.assign(editedTarget, { type: '', value: '' })
  releasePreview()
  targetSearch.value = ''
  previewUrl.value = ''
  pickedFile.value = null
  dialogVisible.value = true
}

async function onEdit(row: any) {
  notice.value = ''
  try {
    const [latest] = await Promise.all([bannerApi.retrieve(row.id), loadTargets()])
    const current = latest.data
    Object.assign(form, current)
    Object.assign(editedTarget, { type: current.link_type, value: current.link_value })
    releasePreview()
    targetSearch.value = ''
    previewUrl.value = current.image_url || ''
    pickedFile.value = null
    dialogVisible.value = true
  } catch {
    notice.value = '未能读取该轮播图，请刷新状态后重试。'
  }
}

function checkDimension(file: File): Promise<boolean> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img.width === IMG_W && img.height === IMG_H)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(false)
    }
    img.src = url
  })
}

function releasePreview() { if (previewUrl.value.startsWith('blob:')) URL.revokeObjectURL(previewUrl.value) }
onUnmounted(releasePreview)

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const raw = input.files?.[0]
  input.value = ''
  if (!raw) return
  const ok = await checkDimension(raw)
  if (!ok) {
    notice.value = `图片尺寸必须为 ${IMG_W}×${IMG_H} 像素`
    return
  }
  releasePreview()
  pickedFile.value = raw
  previewUrl.value = URL.createObjectURL(raw)
  notice.value = ''
}

async function onSave() {
  if (saving.value) return
  if (!form.id && !pickedFile.value) { notice.value = '请上传图片'; return }
  if (form.link_type !== 'NONE' && !form.link_value) { notice.value = '请选择跳转目标或填写链接'; return }
  if (!Number.isInteger(form.sort_order) || form.sort_order < 0) { notice.value = '排序须为不小于 0 的整数'; return }
  saving.value = true
  try {
    const fd = new FormData()
    if (pickedFile.value) fd.append('image', pickedFile.value)
    fd.append('title', form.title || '')
    fd.append('link_type', form.link_type)
    fd.append('link_value', form.link_value || '')
    fd.append('sort_order', String(form.sort_order))
    fd.append('is_active', String(form.is_active))
    const saved = form.id ? await bannerApi.update(form.id, fd) : await bannerApi.create(fd)
    if (saved.data?.is_active && saved.data?.link_available === false) notice.value = '已保存，但目标当前不可用；请更换目标或停用轮播'
    else notice.value = '保存成功'
    dialogVisible.value = false
    reload()
  } catch (error) {
    notice.value = error instanceof Error ? error.message : '保存失败，请重试'
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  if (deleting.value || !auth.hasPerm('banner:delete')) return
  if (!await askConfirm({ title: '删除轮播图', description: '确定删除该轮播图吗？删除后老板端将不再显示。', confirmText: '删除', cancelText: '取消' })) return
  deleting.value = true
  try {
    await bannerApi.remove(row.id)
    notice.value = '已删除'
    reload()
  } catch (error) { notice.value = error instanceof Error ? error.message : '删除失败，请重试' }
  finally { deleting.value = false }
}
</script>

<style scoped>
.bn-page { min-width:0; container:banners / inline-size; }
.bn-panel { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.bn-heading,.bn-actions,.bn-identity,.bn-pages,.bn-switch { display:flex; align-items:center; gap:8px; }
.bn-heading,.bn-switch { justify-content:space-between; }
.bn-heading,.bn-actions,.bn-pages { flex-wrap:wrap; }
h2,h3,p { margin:0; }
h2 { font-size:16px; line-height:24px; font-weight:600; } h3 { font-size:14px; line-height:20px; font-weight:600; }
.tip,.bn-pages,.bn-form label,.bn-notice,.bn-empty { font-size:12px; line-height:16px; }
.tip,.bn-pages,.bn-empty { color:var(--muted-foreground); }
.tip { display:block; margin-top:4px; overflow-wrap:anywhere; }
.bn-notice,.bn-empty { padding:8px 12px; border-radius:8px; background:var(--muted); color:var(--foreground); }
.bn-pages { justify-content:flex-end; }
.bn-pages :deep(select),.bn-form :deep(input:not([type=file])),.bn-form :deep(select) { height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.bn-table { min-width:0; border:1px solid var(--border); border-radius:8px; }
.bn-table :deep(th),.bn-table :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.bn-table :deep(th) { white-space:nowrap; }
.bn-identity { align-items:center; }
.bn-identity>div { min-width:0; }
.bn-thumbnail { width:96px; aspect-ratio:750/320; border:1px solid var(--border); object-fit:contain; background:var(--muted); border-radius:4px; flex:none; }
.bn-warning { color:var(--destructive); padding-inline:0; }
.bn-warning-help { max-width:calc(100vw - 32px); font-size:13px; line-height:20px; }
.bn-form,.bn-target-field,.bn-form label { display:grid; gap:4px; min-width:0; }
.bn-form { gap:12px; } .bn-target-field { gap:8px; }
.bn-form :deep([data-slot='native-select-wrapper']) { width:100%; }
.bn-form label.bn-switch { display:flex; padding:4px 8px; border:1px solid var(--border); border-radius:8px; }
.bn-form input[type=file] { width:100%; min-width:0; max-width:100%; }
.bn-preview { display:block; width:min(180px,100%); height:auto; aspect-ratio:750/320; object-fit:contain; border:1px solid var(--border); border-radius:4px; }
:global(.bn-dialog) { width:min(560px,calc(100vw - 24px)); max-height:calc(100dvh - 32px); overflow:auto; }
@container banners (max-width:800px) {
 .bn-panel { padding:12px; }
 .bn-table,.bn-table :deep([data-slot='table-container']) { border:0; overflow:visible; }
 .bn-table :deep(table),.bn-table :deep(tbody) { display:grid; }
 .bn-table :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
 .bn-table :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .bn-table :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; min-width:0; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
 .bn-table :deep(td) { padding:0; }
 .bn-table :deep(td[data-label]) { display:flex; align-items:baseline; gap:4px; font-size:12px; }
 .bn-table :deep(td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
 .banner-info,.banner-target,.banner-actions { grid-column:1/-1; }
 .banner-target { padding-block:4px !important; border-block:1px solid var(--border); }
 .bn-pages { justify-content:flex-start; }
}
@container banners (max-width:480px) { .bn-table :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) {
 .bn-page :deep(button),.bn-pages :deep(select),.bn-form :deep(input),.bn-form :deep(select),.bn-form label.bn-switch { min-height:44px; }
 .bn-page :deep(button) { min-width:44px; }
}
</style>
