<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { api } from '../lib/session'
type Contact = {name:string;wechat_id:string;wecom_service_url:string;qrcode_url:string;tips:string;customer_reference:string}
const router = useRouter(), card = ref<Contact | null>(null), loading = ref(true), error = ref(''), notice = ref('')
async function load() { loading.value = true; error.value = ''; try { card.value = await api<Contact | null>('/support/contact-card/') } catch (cause) { error.value = cause instanceof Error ? cause.message : '客服信息加载失败' } finally { loading.value = false } }
async function copy(value:string) { try { await navigator.clipboard.writeText(value); notice.value = '已复制' } catch { notice.value = '复制失败，请手动复制页面信息' } }
onMounted(() => { void load() })
</script>
<template>
  <main class="page stack support-page">
    <header class="support-header"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>客服与帮助</h1></div><Button size="xs" variant="outline" @click="router.push('/wallet')">返回钱包</Button></header>
    <Card class="panel support-intro"><div class="intro-copy"><span class="support-symbol" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 13v-2a8 8 0 0 1 16 0v2M4 12H3v6h3v-6H4Zm16 0h1v6h-3v-6h2ZM20 18c0 3-3 3-6 3"/></svg></span><div><h2>客服充值与钱包咨询</h2><p class="muted">带上账号信息，方便客服核对处理。</p></div></div><Button size="xs" @click="router.push('/support-chat')">站内咨询客服</Button></Card>
    <Alert v-if="notice" role="status" class="copy-notice"><AlertDescription>{{ notice }}</AlertDescription></Alert>
    <div v-if="loading" class="support-loading" aria-label="正在加载客服信息"><Skeleton class="contact-skeleton"/><Skeleton class="contact-skeleton"/></div>
    <Alert v-else-if="error" variant="destructive" role="alert"><AlertTitle>客服信息加载失败</AlertTitle><AlertDescription><span>{{ error }}</span><Button size="xs" variant="outline" @click="load">重新加载</Button></AlertDescription></Alert>
    <div v-else-if="card" class="support-grid">
      <Card class="panel account-card"><div class="section-heading"><h2>核对账号信息</h2><Badge variant="secondary">陪玩账号</Badge></div><dl><dt>打手编号</dt><dd class="reference">{{ card.customer_reference }}</dd></dl><Button size="xs" variant="outline" @click="copy(`打手编号：${card.customer_reference}，咨询充值/钱包事项`)">复制账号信息给客服</Button><p class="muted">复制后，可发给客服说明充值或钱包事项。</p></Card>
      <Card class="panel contact-card"><div class="section-heading"><h2>接待客服</h2><Badge variant="secondary">{{ card.name }}</Badge></div>
        <div v-if="card.wechat_id" class="contact-method"><div><h3>客服微信</h3><p>{{ card.wechat_id }}</p></div><Button size="xs" variant="outline" @click="copy(card.wechat_id)">复制微信</Button></div>
        <div v-if="card.wecom_service_url" class="contact-method wecom-method"><div><h3>企业微信客服</h3><p class="url">{{ card.wecom_service_url }}</p></div><Button size="xs" variant="outline" @click="copy(card.wecom_service_url)">复制客服链接</Button></div>
        <figure v-if="card.qrcode_url" class="qr-area"><img :src="card.qrcode_url" alt="客服二维码" class="qr"/><figcaption>客服二维码</figcaption></figure>
        <p v-if="card.tips" class="contact-tips">{{ card.tips }}</p>
        <p v-if="!card.qrcode_url && !card.wechat_id && !card.wecom_service_url" class="muted">客服联系方式尚未配置，请联系俱乐部管理员补充。</p>
      </Card>
    </div>
    <Card v-else class="panel empty-card"><h2>暂未配置接待客服</h2><p class="muted">请联系俱乐部管理员补充客服信息。</p></Card>
    <Card class="panel recharge-note"><h2>充值须知</h2><p>先与客服确认收款方式和金额，付款后由客服凭收款流水在后台充值。1 元人民币兑换 10 兴安币，到账后可缴押金或购买通行证。</p></Card>
  </main>
</template>
<style scoped>
.support-page { gap:16px; } .support-header,.section-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; } .support-header { margin-bottom:8px; } .support-header>div { min-width:0; } .support-header>button { flex-shrink:0; }
h1 { margin:4px 0 0; font-size:24px; line-height:1.35; letter-spacing:-.5px; } h2 { margin:0; font-size:16px; font-weight:600; line-height:1.5; } h3 { margin:0; font-size:12px; font-weight:400; color:var(--muted-foreground); } p,dl,dd { margin:0; } p,dd,h2 { overflow-wrap:anywhere; }
.support-page .panel { padding:16px; gap:16px; border-radius:16px; min-width:0; box-shadow:0 1px 4px rgb(15 23 42 / 3%); }
.support-page .support-intro { flex-direction:row; align-items:center; justify-content:space-between; gap:16px; } .intro-copy { display:flex; align-items:center; gap:16px; min-width:0; } .intro-copy p { margin-top:4px; } .support-symbol { display:grid; place-items:center; width:48px; height:48px; flex:0 0 48px; border-radius:16px; background:var(--secondary); color:var(--primary); } .support-symbol svg { width:24px; height:24px; }
.support-grid,.support-loading { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; align-items:start; } .section-heading { flex-wrap:wrap; } .section-heading :deep([data-slot='badge']) { max-width:100%; white-space:normal; overflow-wrap:anywhere; }
.account-card dl { display:grid; gap:8px; padding:16px; border-radius:12px; background:var(--secondary); } .account-card dt { color:var(--muted-foreground); font-size:12px; } .reference { font-size:24px; line-height:1.4; font-weight:600; font-variant-numeric:tabular-nums; } .account-card>button { align-self:start; max-width:100%; white-space:normal; }
.contact-method { display:flex; align-items:center; justify-content:space-between; gap:12px; padding-top:16px; border-top:1px solid var(--border); } .contact-method>div { min-width:0; } .contact-method p { margin-top:4px; font-size:14px; } .contact-method>button { flex-shrink:0; } .wecom-method { align-items:flex-start; flex-wrap:wrap; } .wecom-method>div { flex-basis:100%; } .url { overflow-wrap:anywhere; }
.qr-area { display:grid; justify-items:center; gap:8px; margin:0; padding-top:16px; border-top:1px solid var(--border); } .qr { display:block; width:160px; height:160px; object-fit:contain; background:#fff; border-radius:8px; } .qr-area figcaption { font-size:12px; color:var(--muted-foreground); }
.contact-tips { padding:12px; border-radius:8px; background:var(--secondary); color:var(--muted-foreground); font-size:13px; line-height:1.6; }
.support-page .recharge-note { gap:8px; background:transparent; box-shadow:none; } .recharge-note p { font-size:13px; line-height:1.7; color:var(--muted-foreground); } .contact-skeleton { height:240px; border-radius:16px; }
.copy-notice { background:var(--secondary); color:var(--primary); } .copy-notice :deep([data-slot='alert-description']) { color:inherit; } .support-page :deep([data-slot='alert-description']) { display:flex; align-items:center; flex-wrap:wrap; gap:12px; }
.support-page :deep([data-slot='button']) { min-height:var(--compact-control-height,24px); border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font-family:inherit; font-size:12px; box-shadow:none; }
.support-page :deep([data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); } .support-page :deep([data-slot='button']:focus-visible) { outline:2px solid var(--primary); outline-offset:4px; }
@media(max-width:600px) { .support-page { gap:12px; } .support-header { gap:8px; margin-bottom:4px; } .support-page .panel { padding:16px; border-radius:16px; } .support-page .support-intro { align-items:stretch; flex-direction:column; gap:16px; } .intro-copy { gap:12px; } .support-grid,.support-loading { grid-template-columns:minmax(0,1fr); gap:12px; } .support-intro>button { align-self:start; } .contact-method { flex-wrap:wrap; } }
.support-page { --compact-control-height:24px; } .support-page .panel { gap:12px; }
@media(max-width:600px),(pointer:coarse) { .support-page { --compact-control-height:44px; } }
@media(prefers-reduced-motion:reduce) { .support-page :deep(*) { transition:none; animation:none; } }
</style>
