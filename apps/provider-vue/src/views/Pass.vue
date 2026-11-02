<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { formatXaCoin } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api } from '../lib/session'
import { fundRequest } from '../lib/fund'
type Product = {tier:string;name:string;daily_price:number;delay_seconds:number}
type Data = {balance:number;active_tier:string;active_tier_display:string;expires_at:string | null;current_delay_seconds:number;no_pass_delay_seconds:number;products:Product[];history:{id:number;tier_display:string;days:number;paid_amount:number;starts_at:string;expires_at:string}[]}
const router = useRouter(), data = ref<Data | null>(null), selected = ref<Product | null>(null), loading = ref(true), busy = ref(false), error = ref(''), notice = ref(''), issue = ref('')
async function load() { loading.value = true; error.value = ''; try { data.value = await api<Data>('/users/provider-pass/') } catch (cause) { error.value = cause instanceof Error ? cause.message : '通行证加载失败' } finally { loading.value = false } }
async function buy() {
  const product = selected.value; if (!product || busy.value || issue.value) return
  busy.value = true
  try { data.value = await fundRequest<Data>('/users/provider-pass/',{tier:product.tier,days:1,expected_daily_price:product.daily_price}); selected.value = null; notice.value = '采购成功' }
  catch (cause) { const failure = cause as {code?:number;message?:string}; if (!failure.code || failure.code >= 500 || failure.code === 409 || failure.code === 429) issue.value = '采购结果未确认，请刷新通行证及钱包流水后核对。'; else { selected.value = null; notice.value = failure.message || '采购失败' } }
  finally { await load(); busy.value = false }
}
onMounted(() => { void load() })
function waitsForExpiry(tier: string) { return !!data.value?.active_tier && data.value.active_tier !== tier }
function visibility(seconds: number) { return seconds === 0 ? '发布后立即可见' : '发布 ' + seconds + ' 秒后可见' }
</script>
<template>
  <main class="page stack pass-page">
    <header class="pass-heading">
      <div><p class="muted">兴安电竞 · 陪玩端</p><h1>接单通行证</h1></div>
      <Button size="xs" variant="outline" @click="router.push('/orders')">返回订单</Button>
    </header>
    <Alert v-if="notice" role="status" class="notice"><AlertDescription>{{ notice }}</AlertDescription></Alert>
    <Alert v-if="error" variant="destructive" role="alert">
      <AlertTitle>通行证加载失败</AlertTitle>
      <AlertDescription>{{ error }} <Button size="xs" variant="outline" @click="load">重新加载</Button></AlertDescription>
    </Alert>
    <div v-else-if="loading" class="pass-grid" aria-label="正在读取通行证"><Skeleton v-for="n in 2" :key="n" class="pass-skeleton" /></div>
    <template v-else-if="data">
      <Card class="panel summary">
        <div class="status-head"><div><p class="caption">当前通行证</p><h2>{{ data.active_tier_display }}</h2></div><Badge :variant="data.active_tier ? 'secondary' : 'outline'">{{ data.active_tier ? '生效中' : '未开通' }}</Badge></div>
        <div v-if="data.active_tier" class="expiry"><span v-if="data.expires_at">有效至 {{ new Date(data.expires_at).toLocaleString() }}</span><Button size="xs" variant="outline" :disabled="busy || loading" @click="load">刷新状态</Button></div>
        <dl class="status-numbers">
          <div><dt>公共订单可见时间</dt><dd>{{ visibility(data.current_delay_seconds) }}</dd></div>
          <div><dt>钱包余额</dt><dd>{{ formatXaCoin(data.balance) }} <small>兴安币</small></dd></div>
        </dl>
      </Card>
      <section aria-labelledby="pass-options" class="options">
        <div class="section-heading"><h2 id="pass-options">通行证档位</h2><p class="muted">按日采购，每次 1 天。{{ data.active_tier ? '当前通行证到期前，只可续费同一档位。' : '无通行证时，公共订单' + visibility(data.no_pass_delay_seconds) + '。' }}</p></div>
        <div class="pass-grid">
          <Card v-for="product in data.products" :key="product.tier" class="panel product" :data-current="data.active_tier === product.tier">
            <div class="product-heading"><h3>{{ product.name }}</h3><Badge v-if="data.active_tier === product.tier" variant="secondary">当前档位</Badge></div>
            <p class="product-delay">公共订单{{ visibility(product.delay_seconds) }}</p>
            <div class="offer"><p class="price">{{ formatXaCoin(product.daily_price) }} <small>币 / 天</small></p><Button size="xs" :variant="data.active_tier === product.tier ? 'default' : 'outline'" :disabled="busy || waitsForExpiry(product.tier)" @click="issue = ''; selected = product">{{ waitsForExpiry(product.tier) ? '到期后可选' : data.active_tier === product.tier ? '续费' : '采购' }}</Button></div>
          </Card>
        </div>
      </section>
      <Card class="panel history">
        <h2>采购记录</h2>
        <p v-if="!data.history.length" class="muted">暂无采购记录</p>
        <div v-for="row in data.history" :key="row.id" class="record">
          <div><h3>{{ row.tier_display }}</h3><p class="muted">{{ new Date(row.starts_at).toLocaleString() }} 至 {{ new Date(row.expires_at).toLocaleString() }}</p></div>
          <p class="amount">{{ formatXaCoin(row.paid_amount) }} 币</p>
        </div>
      </Card>
    </template>
    <Dialog :open="!!selected" @update:open="value => { if (!value && !busy) selected = null }">
      <DialogContent class="pass-dialog" :show-close-button="!busy">
        <DialogHeader><DialogTitle>{{ data?.active_tier === selected?.tier ? '续费' : '采购' }}{{ selected?.name }}</DialogTitle><DialogDescription>请核对档位与金额，确认后从钱包扣除。</DialogDescription></DialogHeader>
        <dl v-if="selected" class="bill">
          <div><dt>有效期</dt><dd>1 天</dd></div><div><dt>公共订单</dt><dd>{{ visibility(selected.delay_seconds) }}</dd></div>
          <div class="total"><dt>扣款金额</dt><dd>{{ formatXaCoin(selected.daily_price) }} 兴安币</dd></div>
        </dl>
        <p v-if="issue" role="alert" class="error">{{ issue }}</p>
        <DialogFooter><Button size="xs" variant="outline" :disabled="busy" @click="selected = null">取消</Button><Button size="xs" :disabled="busy || !!issue" @click="buy">{{ busy ? '采购中…' : '确认采购' }}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </main>
</template>
<style scoped>
.pass-page { --compact-control-height:24px; gap:16px; }
.pass-heading,.status-head,.product-heading,.offer,.record { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.pass-heading { margin-bottom:8px; } .pass-heading button,.offer button,.status-head :deep([data-slot='badge']) { flex-shrink:0; }
h1,h2,h3,p,dl,dd { margin:0; overflow-wrap:anywhere; } h1 { margin-top:4px; font-size:24px; line-height:1.35; letter-spacing:-.5px; }
h2 { font-size:16px; line-height:1.5; } h3 { font-size:14px; line-height:1.5; } .caption,dt,small,.expiry { color:var(--muted-foreground); font-size:12px; font-weight:400; }
.pass-page .panel { border-radius:16px; padding:16px; gap:12px; min-width:0; box-shadow:0 1px 4px rgb(15 23 42 / 3%); }
.summary h2 { margin-top:4px; font-size:24px; line-height:1.35; } .summary .expiry { margin-top:-4px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; } .status-numbers { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; padding-top:12px; border-top:1px solid var(--border); }
.status-numbers dd { margin-top:4px; font-size:16px; font-weight:600; } .status-numbers dd,.price,.amount { font-variant-numeric:tabular-nums; }
.options,.section-heading { display:grid; gap:8px; } .section-heading { gap:4px; } .pass-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.product-heading { flex-wrap:wrap; } .product-heading h3 { font-size:16px; } .product-delay { color:var(--muted-foreground); font-size:12px; } .product[data-current=true] { border-color:var(--primary); }
.offer { flex-wrap:wrap; padding-top:12px; border-top:1px solid var(--border); margin-top:auto; } .price { font-size:20px; font-weight:600; } .record { align-items:start; flex-wrap:wrap; border-top:1px solid var(--border); padding-top:12px; }
.record .muted { margin-top:4px; } .record>div { flex:1 1 160px; min-width:0; } .amount { font-size:14px; font-weight:600; }
.pass-skeleton { height:128px; border-radius:16px; } .notice { background:var(--secondary); color:var(--primary); } .notice :deep([data-slot='alert-description']) { color:inherit; } .pass-page :deep([data-slot='alert-description']) { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.pass-page :deep([data-slot='button']),:global(.pass-dialog [data-slot='button']) { min-height:var(--compact-control-height,24px); border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font:inherit; font-size:12px; box-shadow:none; } .pass-page :deep([data-slot='button'][data-variant='outline']),:global(.pass-dialog [data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); } .pass-page :deep([data-slot='button']:focus-visible),:global(.pass-dialog [data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:4px; }
:global(.pass-dialog) { --compact-control-height:24px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; max-height:calc(100dvh - 32px); overflow-y:auto; border-color:var(--border); border-radius:16px; padding:16px; } :global(.pass-dialog [data-slot='dialog-close']) { display:grid; place-items:center; width:44px; height:44px; right:4px; top:4px; padding:12px; border:0; border-radius:12px; background:transparent; color:var(--muted-foreground); } :global(.pass-dialog [data-slot='dialog-title']) { padding-right:32px; font-size:16px; }
.bill { padding:12px; display:grid; gap:12px; border-radius:12px; background:var(--secondary); } .bill>div { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:8px; } .bill dd { font-size:14px; } .bill .total { border-top:1px solid var(--border); padding-top:12px; } .bill .total dd { color:var(--primary); font-weight:600; }
@media(max-width:600px) { .pass-page { gap:12px; } .pass-grid { grid-template-columns:minmax(0,1fr); gap:8px; } .pass-heading { margin-bottom:4px; } }
@media(max-width:360px) { .status-numbers { grid-template-columns:minmax(0,1.25fr) minmax(0,1fr); gap:8px; } .status-numbers dd { font-size:14px; } }
@media(max-width:600px),(pointer:coarse) { .pass-page :deep([data-slot='button']),:global(.pass-dialog [data-slot='button']) { min-width:44px; } .pass-page { --compact-control-height:44px; } :global(.pass-dialog) { --compact-control-height:44px; } }
@media(prefers-reduced-motion:reduce) { .pass-page :deep(*) { transition:none; animation:none; } }
</style>
