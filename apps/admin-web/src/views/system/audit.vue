<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Card } from '@xa/shadcn-vue/card'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { auditApi } from '@/api/modules'
import { formatDateTime } from '@/utils/format'

type Row = {id:number;operator_name:string;method:string;resource:string;object_id:string | number | null;path:string;status_code:number;ip:string;created_at:string;request_body:unknown}
const query = reactive({keyword:'',method:'',start_date:'',end_date:''}), applied = ref({...query})
const rows = ref<Row[]>([]), total = ref(0), page = ref(1), pageSize = ref('10'), loading = ref(false), failed = ref(false), current = ref<Row | null>(null)
let sequence = 0, alive = true
const pages = computed(() => Math.max(1,Math.ceil(total.value / Number(pageSize.value))))
const detailOpen = computed({get:() => !!current.value,set:value => { if (!value) current.value = null }})
async function load() {
  const request = ++sequence; loading.value = true; failed.value = false
  try { const result = await auditApi.list({...applied.value,page:page.value,page_size:Number(pageSize.value)}); if (!alive || request !== sequence) return; rows.value = result.data.list || []; total.value = result.data.total || 0 }
  catch (error:any) { if (!alive || request !== sequence) return; if (error?.response?.status === 404 && page.value > 1) { page.value = 1; await load(); return }; rows.value = []; total.value = 0; failed.value = true }
  finally { if (alive && request === sequence) loading.value = false }
}
function search() { applied.value = {...query}; page.value = 1; void load() }
function reset() { Object.assign(query,{keyword:'',method:'',start_date:'',end_date:''}); search() }
function changePage(next:number) { if (loading.value || next < 1 || next > pages.value) return; page.value = next; void load() }
function changeSize(value:unknown) { const size = String(value); if (!['10','20','50','100'].includes(size)) return; pageSize.value = size; page.value = 1; void load() }
function prettyBody(body:unknown) { try { return JSON.stringify(body ?? {},null,2) } catch { return String(body) } }
onMounted(() => { void load() }); onUnmounted(() => { alive = false; sequence++ })
</script>
<template>
  <div class="audit-page">
    <Card class="audit-card">
      <header class="audit-heading">
        <h2>筛选请求记录</h2>
        <Popover>
          <PopoverTrigger as-child><Button size="xs" variant="outline" aria-label="查看记录范围说明">记录范围</Button></PopoverTrigger>
          <PopoverContent class="audit-explainer">仅记录后台写请求中 HTTP 响应码小于 400 且成功保存的记录。HTTP 400 及以上的请求不在此列表；没有记录不能证明操作未发生。响应码也不代表业务结果。</PopoverContent>
        </Popover>
      </header>
      <form class="audit-filters" @submit.prevent="search">
        <label class="keyword">关键词<Input v-model="query.keyword" placeholder="操作人、路径或资源" aria-label="搜索操作人、请求路径或资源" /></label>
        <label>请求方法<NativeSelect v-model="query.method" aria-label="请求方法">
          <NativeSelectOption value="">全部方法</NativeSelectOption>
          <NativeSelectOption v-for="method in ['POST','PUT','PATCH','DELETE']" :key="method" :value="method">{{ method }}</NativeSelectOption>
        </NativeSelect></label>
        <label>开始日期<Input v-model="query.start_date" type="date" /></label>
        <label>结束日期<Input v-model="query.end_date" type="date" /></label>
        <div class="audit-actions">
          <Button size="xs" type="submit" :disabled="loading">查询</Button>
          <Button size="xs" variant="outline" type="button" :disabled="loading" @click="reset">清除筛选</Button>
        </div>
      </form>
    </Card>

    <Card class="audit-card">
      <header class="audit-heading">
        <h2>后台请求记录</h2>
        <p v-if="!failed && !loading" class="muted">共 {{ total.toLocaleString() }} 条 · 本页 {{ rows.length }} 条</p>
      </header>
      <div v-if="failed" role="alert" class="audit-feedback">
        数据加载失败，请重试
        <Button size="xs" variant="outline" @click="load">重新加载</Button>
      </div>
      <p v-else-if="loading" role="status" class="audit-feedback">正在加载记录…</p>
      <p v-else-if="!rows.length" class="audit-feedback">暂无符合条件的记录</p>
      <template v-else>
        <div class="audit-table" role="region" aria-label="后台请求记录" tabindex="0">
          <Table>
            <TableHeader><TableRow>
              <TableHead>ID</TableHead><TableHead>操作人</TableHead><TableHead>方法</TableHead>
              <TableHead>资源</TableHead><TableHead>对象 ID</TableHead><TableHead>请求路径</TableHead>
              <TableHead>HTTP 状态码</TableHead><TableHead>来源 IP</TableHead><TableHead>时间</TableHead><TableHead>操作</TableHead>
            </TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell>{{ row.id }}</TableCell><TableCell>{{ row.operator_name || '未知操作人' }}</TableCell>
              <TableCell><Badge variant="outline">{{ row.method }}</Badge></TableCell>
              <TableCell>{{ row.resource || '—' }}</TableCell><TableCell>{{ row.object_id ?? '—' }}</TableCell>
              <TableCell class="request-path">{{ row.path }}</TableCell><TableCell>{{ row.status_code }}</TableCell>
              <TableCell>{{ row.ip || '—' }}</TableCell><TableCell>{{ formatDateTime(row.created_at) }}</TableCell>
              <TableCell><Button size="xs" variant="outline" @click="current = row">详情</Button></TableCell>
            </TableRow></TableBody>
          </Table>
        </div>
        <ul class="audit-list" aria-label="后台请求记录">
          <li v-for="row in rows" :key="row.id">
            <div class="audit-heading"><strong>{{ row.operator_name || '未知操作人' }}</strong><Badge variant="outline">{{ row.method }}</Badge></div>
            <p class="request-path">{{ row.path }}</p>
            <p class="muted">{{ row.resource || '未记录资源' }}<template v-if="row.object_id !== null && row.object_id !== ''"> · 对象 #{{ row.object_id }}</template></p>
            <div class="card-foot">
              <div class="card-meta"><span>{{ formatDateTime(row.created_at) }}</span><span>HTTP {{ row.status_code }}</span></div>
              <Button size="xs" variant="outline" @click="current = row">查看详情</Button>
            </div>
          </li>
        </ul>
      </template>
      <nav v-if="!loading && !failed && total" class="audit-pagination" aria-label="日志翻页">
        <span class="muted">第 {{ page }} / {{ pages }} 页</span>
        <NativeSelect :model-value="pageSize" aria-label="每页条数" @update:model-value="changeSize">
          <NativeSelectOption v-for="size in ['10','20','50','100']" :key="size" :value="size">{{ size }} 条/页</NativeSelectOption>
        </NativeSelect>
        <Button size="xs" variant="outline" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</Button>
        <Button size="xs" variant="outline" :disabled="page >= pages || loading" @click="changePage(page + 1)">下一页</Button>
      </nav>
    </Card>
    <Dialog v-model:open="detailOpen">
      <DialogContent class="audit-dialog">
        <DialogHeader><DialogTitle>审计详情</DialogTitle><DialogDescription>后台请求记录 #{{ current?.id }}</DialogDescription></DialogHeader>
        <dl v-if="current" class="audit-details">
          <div><dt>操作人</dt><dd>{{ current.operator_name || '—' }}</dd></div>
          <div><dt>方法</dt><dd>{{ current.method }}</dd></div>
          <div><dt>资源</dt><dd>{{ current.resource || '—' }}</dd></div>
          <div><dt>对象 ID</dt><dd>{{ current.object_id ?? '—' }}</dd></div>
          <div><dt>请求路径</dt><dd class="request-path">{{ current.path }}</dd></div>
          <div><dt>HTTP 状态码</dt><dd>{{ current.status_code }}</dd></div>
          <div><dt>来源 IP</dt><dd>{{ current.ip || '—' }}</dd></div>
          <div><dt>时间</dt><dd>{{ formatDateTime(current.created_at) }}</dd></div>
          <div class="details-body"><dt>请求参数</dt><dd><pre>{{ prettyBody(current.request_body) }}</pre></dd></div>
        </dl>
      </DialogContent>
    </Dialog>
  </div>
</template>

<style scoped>
.audit-page { container:audit / inline-size; display:grid; min-width:0; gap:12px; color:var(--foreground); }
.audit-card { min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.audit-page h2,.audit-page p,.audit-list,.audit-details,.audit-details dd,.audit-details pre { margin:0; }
.audit-heading,.audit-actions,.audit-pagination,.card-foot,.card-meta { display:flex; align-items:center; gap:8px; }
.audit-heading,.card-foot { justify-content:space-between; }
.audit-heading h2 { font-size:16px; line-height:24px; font-weight:600; }
.muted,.card-meta,.audit-filters label,.audit-details dt { font-size:12px; line-height:16px; color:var(--muted-foreground); }
.audit-filters { display:grid; grid-template-columns:minmax(160px,1.5fr) minmax(100px,.8fr) repeat(2,minmax(120px,1fr)) auto; align-items:end; gap:8px; }
.audit-filters label { display:grid; gap:4px; min-width:0; }
.audit-filters :deep([data-slot='native-select-wrapper']),.audit-filters :deep(input) { width:100%; min-width:0; }
.audit-page :deep(input),.audit-page :deep(select) { height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.audit-actions,.audit-pagination,.card-meta { flex-wrap:wrap; }
.audit-feedback { padding:12px; display:flex; align-items:center; gap:8px; color:var(--muted-foreground); border-radius:8px; background:var(--muted); }
.audit-table { overflow:auto; min-width:0; border:1px solid var(--border); border-radius:8px; }
.audit-table :deep(th),.audit-table :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.audit-table :deep(th) { white-space:nowrap; }
.request-path,.audit-list,.audit-details dd { min-width:0; overflow-wrap:anywhere; }
.request-path,.audit-details pre { font-family:ui-monospace,monospace; font-size:12px; line-height:16px; }
.audit-list { display:none; list-style:none; padding:0; gap:8px; }
.audit-list li { display:grid; min-width:0; align-content:start; gap:4px; padding:12px; border:1px solid var(--border); border-radius:8px; }
.card-foot { margin-top:4px; }
.card-meta { gap:4px 8px; }
.audit-pagination { justify-content:flex-end; }
.audit-explainer { max-width:calc(100vw - 32px); font-size:13px; line-height:20px; }
.audit-details { display:grid; max-height:min(65vh,560px); overflow:auto; gap:0; }
.audit-details div { display:grid; grid-template-columns:80px minmax(0,1fr); align-items:baseline; gap:8px; padding:8px 0; border-top:1px solid var(--border); }
.audit-details dd { font-size:13px; line-height:20px; }
.audit-details .details-body { grid-template-columns:minmax(0,1fr); }
.audit-details pre { padding:8px; border-radius:4px; background:var(--muted); white-space:pre-wrap; overflow-wrap:anywhere; }
@container audit (max-width:940px) {
  .audit-table { display:none; }
  .audit-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
  .audit-filters { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .audit-actions { grid-column:1/-1; }
}
@container audit (max-width:540px) {
  .audit-card { padding:12px; }
  .audit-list { grid-template-columns:minmax(0,1fr); }
  .audit-pagination { justify-content:flex-start; }
}
@container audit (max-width:340px) { .audit-filters { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .audit-page :deep(input),.audit-page :deep(select),.audit-page :deep(button) { min-height:44px; } .audit-page :deep(button) { min-width:44px; } }
</style>
