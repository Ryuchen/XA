<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { Input } from '@xa/shadcn-vue/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { api } from '../lib/session'

type Method = 'WECHAT' | 'ALIPAY' | 'BANK'
type Account = { id: number; payee_method: Method; payee_account: string; payee_name: string; version: number }
type Editor = { method: Method; id: number; version: number }
const methods: { value: Method; label: string; placeholder: string }[] = [{ value:'WECHAT', label:'微信', placeholder:'请输入微信号' }, { value:'ALIPAY', label:'支付宝', placeholder:'请输入支付宝账号' }, { value:'BANK', label:'银行卡', placeholder:'请输入银行卡号' }]
const route = useRoute(), router = useRouter(), accounts = ref<Account[]>([]), loading = ref(true), busy = ref(false)
const error = ref(''), notice = ref(''), editorError = ref(''), conflict = ref(false)
const editor = ref<Editor | null>(null), removing = ref<Account | null>(null), account = ref(''), name = ref('')
let sequence = 0, alive = true, openedQuery = false
const selectedMethod = computed(() => methods.find(item => item.value === editor.value?.method))
const editOpen = computed({ get: () => !!editor.value, set: value => { if (!value && !busy.value) editor.value = null } })
const removeOpen = computed({ get: () => !!removing.value, set: value => { if (!value && !busy.value) removing.value = null } })
function edit(method: Method, rows = accounts.value) {
  if (busy.value) return
  const row = rows.find(item => item.payee_method === method)
  editor.value = { method, id: row?.id || 0, version: row?.version || 0 }; account.value = row?.payee_account || ''; name.value = row?.payee_name || ''; editorError.value = ''; conflict.value = false
}
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''
  try {
    const data = await api<Account[]>('/wallet/withdraw-accounts/')
    if (!alive || current !== sequence) return
    accounts.value = data
    const method = methods.find(item => item.value === route.query.method)
    if (!openedQuery && method) { openedQuery = true; edit(method.value, data) }
  } catch (cause) { if (alive && current === sequence) error.value = cause instanceof Error ? cause.message : '加载失败，请重试' }
  finally { if (alive && current === sequence) loading.value = false }
}
async function save() {
  const target = editor.value
  if (!target || busy.value || conflict.value || loading.value || !account.value.trim() || !name.value.trim()) return
  busy.value = true; editorError.value = ''
  try {
    const data = await api<Account>(`/wallet/withdraw-accounts/${target.method}/`, { method:'PUT', data:{ payee_account: account.value.trim(), payee_name: name.value.trim(), expected_id: target.id, expected_version: target.version } })
    if (!alive) return
    sequence++; loading.value = false; accounts.value = [...accounts.value.filter(row => row.payee_method !== target.method), data]; editor.value = null; notice.value = '收款账户已保存'
  } catch (cause) {
    if (!alive) return
    const failure = cause as { errorCode?: string; message?: string; status?: number }
    if (failure.errorCode === 'PAYEE_CHANGED' || !failure.status || failure.status >= 500) { conflict.value = true; await load() }
    editorError.value = failure.errorCode === 'PAYEE_CHANGED' ? '账户已变化，请重新载入最新资料' : failure.status && failure.status < 500 ? failure.message || '保存失败' : '保存结果未确认，请重新载入最新账户核对'
  } finally { if (alive) busy.value = false }
}
async function unbind() {
  const target = removing.value
  if (!target || busy.value) return
  busy.value = true
  try {
    await api(`/wallet/withdraw-accounts/${target.payee_method}/`, { method:'DELETE', data:{ expected_id:target.id, expected_version:target.version } })
    if (!alive) return
    sequence++; loading.value = false; accounts.value = accounts.value.filter(row => row.id !== target.id); notice.value = '已解绑'; removing.value = null
  } catch (cause) { if (alive) { removing.value = null; notice.value = cause instanceof Error ? cause.message : '解绑结果未确认，请刷新核对'; await load() } }
  finally { if (alive) busy.value = false }
}
onMounted(() => { void load() })
onUnmounted(() => { alive = false; sequence++ })
</script>

<template>
  <main class="page stack accounts-page">
    <header class="accounts-header"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>提现账户</h1></div><Button size="xs" variant="outline" :disabled="busy" @click="router.push('/wallet')">返回钱包</Button></header>
    <Card class="panel account-guidance"><h2>管理收款方式</h2><p class="muted">微信、支付宝、银行卡各可绑定一个。已提交申请的收款资料保持不变。</p></Card><Alert v-if="notice" role="status" class="notice"><AlertDescription>{{ notice }}</AlertDescription></Alert>
    <Alert v-if="error" variant="destructive" role="alert"><AlertTitle>收款账户加载失败</AlertTitle><AlertDescription>{{ error }} <Button size="xs" variant="outline" :disabled="busy" @click="load">重新加载</Button></AlertDescription></Alert><div v-if="loading" class="account-grid" aria-label="正在读取收款账户"><Skeleton v-for="n in 3" :key="n" class="account-skeleton"/></div>
    <div v-else-if="!error" class="account-grid"><Card v-for="method in methods" :key="method.value" class="panel account-card"><div class="account-heading"><h2>{{ method.label }}</h2><Badge :variant="accounts.some(row => row.payee_method === method.value) ? 'secondary' : 'outline'">{{ accounts.some(row => row.payee_method === method.value) ? '已绑定' : '未绑定' }}</Badge></div><div class="account-details"><template v-if="accounts.find(row => row.payee_method === method.value)"><p class="payee-name">{{ accounts.find(row => row.payee_method === method.value)?.payee_name }}</p><p class="masked-account">•••• {{ accounts.find(row => row.payee_method === method.value)?.payee_account.slice(-4) }}</p></template><p v-else class="muted">绑定后可在提现时选择</p></div><div class="actions"><Button size="xs" :variant="accounts.some(row => row.payee_method === method.value) ? 'outline' : 'default'" :disabled="busy" @click="edit(method.value)">{{ accounts.some(row => row.payee_method === method.value) ? '修改账户' : '绑定账户' }}</Button><Button size="xs" v-if="accounts.find(row => row.payee_method === method.value)" variant="ghost" :disabled="busy" @click="removing = accounts.find(row => row.payee_method === method.value) || null">解绑</Button></div></Card></div>
    <Dialog v-model:open="editOpen"><DialogContent class="accounts-dialog" :show-close-button="!busy"><DialogHeader><DialogTitle>{{ editor?.id ? '修改' : '绑定' }}{{ selectedMethod?.label }}账户</DialogTitle><DialogDescription>客服将按提现申请中的资料打款，请核对账号与姓名。</DialogDescription></DialogHeader><label class="field">收款账号<Input v-model="account" maxlength="100" :placeholder="selectedMethod?.placeholder" :disabled="busy || conflict" /></label><label class="field">收款人姓名<Input v-model="name" maxlength="50" placeholder="请输入真实姓名" :disabled="busy || conflict" /></label><p v-if="editorError" role="alert" class="error">{{ editorError }}</p><DialogFooter><Button size="xs" variant="outline" :disabled="busy" @click="editor = null">取消</Button><Button size="xs" v-if="conflict" :disabled="busy || loading" @click="editor && edit(editor.method)">重新载入最新账户</Button><Button size="xs" v-else :disabled="busy || loading || !account.trim() || !name.trim()" @click="save">{{ busy ? '保存中…' : '保存账户' }}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog v-model:open="removeOpen"><DialogContent class="accounts-dialog" :show-close-button="!busy"><DialogHeader><DialogTitle>解绑收款账户</DialogTitle><DialogDescription>解绑后可重新绑定。已提交的提现申请仍使用原收款资料。</DialogDescription></DialogHeader><div v-if="removing" class="remove-target"><strong>{{ methods.find(item => item.value === removing?.payee_method)?.label }}</strong><span>{{ removing.payee_name }}</span><span>•••• {{ removing.payee_account.slice(-4) }}</span></div><DialogFooter><Button size="xs" variant="outline" :disabled="busy" @click="removing = null">取消</Button><Button size="xs" :disabled="busy" @click="unbind">确认解绑</Button></DialogFooter></DialogContent></Dialog>
  </main>
</template>
<style scoped>
.accounts-page { --compact-control-height:24px; gap:16px; } .accounts-header,.account-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; } .accounts-header { margin-bottom:8px; } .accounts-header>div { min-width:0; } .accounts-header>button { flex-shrink:0; }
h1 { margin:4px 0 0; font-size:24px; line-height:1.35; letter-spacing:-.5px; } h2 { margin:0; font-size:16px; line-height:1.5; font-weight:600; } p { margin:0; overflow-wrap:anywhere; }
.accounts-page .panel { padding:16px; border-radius:16px; gap:12px; min-width:0; box-shadow:0 1px 4px rgb(15 23 42 / 3%); } .account-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; } .account-heading { flex-wrap:wrap; } .account-heading :deep([data-slot='badge']) { border-color:var(--border); }
.account-details { display:flex; flex-direction:column; justify-content:center; gap:8px; min-height:88px; padding:16px 0; } .payee-name { font-size:16px; font-weight:500; } .masked-account { font-size:20px; color:var(--muted-foreground); font-variant-numeric:tabular-nums; }
.actions { display:flex; flex-wrap:wrap; gap:8px; padding-top:12px; border-top:1px solid var(--border); margin-top:auto; } .account-skeleton { height:216px; border-radius:16px; }
.notice { background:var(--secondary); color:var(--primary); } .notice :deep([data-slot='alert-description']) { color:inherit; } .accounts-page :deep([data-slot='alert-description']) { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.accounts-page :deep([data-slot='button']), :global(.accounts-dialog [data-slot='button']) { min-height:var(--compact-control-height,24px); border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font-family:inherit; font-size:12px; box-shadow:none; }
.accounts-page :deep([data-slot='button'][data-variant='outline']), :global(.accounts-dialog [data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); } .accounts-page :deep([data-slot='button'][data-variant='ghost']) { background:transparent; color:var(--muted-foreground); }
.accounts-page :deep([data-slot='button']:focus-visible), :global(.accounts-dialog [data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:4px; }
:global(.accounts-dialog) { --compact-control-height:24px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; border-color:var(--border); border-radius:16px; padding:16px; max-height:calc(100dvh - 32px); overflow-y:auto; }
:global(.accounts-dialog [data-slot='dialog-close']) { display:grid; place-items:center; width:44px; height:44px; right:4px; top:4px; padding:12px; border:0; border-radius:12px; background:transparent; color:var(--muted-foreground); } :global(.accounts-dialog [data-slot='dialog-title']) { padding-right:32px; font-size:16px; }
:global(.accounts-dialog input) { min-height:28px; height:28px; padding:4px 8px; font-size:12px; } :global(.accounts-dialog .remove-target) { display:grid; gap:8px; padding:12px; border-radius:12px; background:var(--secondary); font-size:14px; overflow-wrap:anywhere; }
@media(max-width:800px) { .account-grid { grid-template-columns:minmax(0,1fr); gap:12px; } .account-details { min-height:0; padding:8px 0; } }
@media(max-width:600px) { .accounts-page { gap:12px; } .accounts-header { gap:8px; margin-bottom:4px; } }
@media(max-width:600px),(pointer:coarse) { .accounts-page :deep([data-slot='button']),:global(.accounts-dialog [data-slot='button']) { min-width:44px; } .accounts-page { --compact-control-height:44px; } :global(.accounts-dialog) { --compact-control-height:44px; } :global(.accounts-dialog input) { min-height:44px; height:44px; font-size:14px; } }
@media(prefers-reduced-motion:reduce) { .accounts-page :deep(*) { transition:none; animation:none; } }
</style>
