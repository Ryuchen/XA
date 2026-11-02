<template>
  <div class="ci-page">
    <Card class="ci-header"><h2>消费签到</h2><p>规则、累计签到礼物和老板进度按自然月核对。</p></Card>

    <Card class="ci-rule-card">
      <div class="ci-section-head"><h3>月度签到规则</h3><Badge variant="outline">{{ auth.hasPerm('checkin:edit') ? '可编辑' : '只读' }}</Badge></div>
      <Alert v-if="ruleError" role="alert" variant="destructive"><AlertDescription>{{ ruleError }}{{ ruleLoaded ? '。以下保留上次读取的配置。' : '' }} <Button size="xs" variant="outline" @click="loadRule">重新读取</Button></AlertDescription></Alert>
      <p v-if="!ruleLoaded && !ruleError" class="hint" role="status">正在读取月度规则…</p>
      <template v-if="ruleLoaded">
      <dl v-if="!auth.hasPerm('checkin:edit')" class="rule-summary">
        <div><dt>当日签到门槛</dt><dd>{{ dailySpendCoin }} 币</dd></div>
        <div><dt>补签卡消费门槛</dt><dd>{{ cardSpendCoin }} 币</dd></div>
        <div><dt>补签卡库存上限</dt><dd>{{ rule.max_makeup_cards }} 张/月</dd></div>
        <div><dt>全勤奖励名称</dt><dd>{{ rule.full_attendance_reward_name || '未填写' }}</dd></div>
        <div><dt>KOOK Tag 标识</dt><dd>{{ rule.full_attendance_tag_code || '未填写' }}</dd></div>
        <div><dt>全勤奖励说明</dt><dd>{{ rule.full_attendance_reward_desc || '未填写' }}</dd></div>
      </dl>
      <div v-else class="ci-fields">
        <label>当日签到门槛（兴安币）<Input v-model.number="dailySpendCoin" type="number" min="1" step="0.1" :disabled="!!ruleError || savingRule" /></label>
        <label>补签卡消费门槛（兴安币）<Input v-model.number="cardSpendCoin" type="number" min="1" step="0.1" :disabled="!!ruleError || savingRule" /></label>
        <label>补签卡库存上限（张/月）<Input v-model.number="rule.max_makeup_cards" type="number" min="1" max="31" step="1" :disabled="!!ruleError || savingRule" /></label>
        <label>全勤奖励名称<Input v-model="rule.full_attendance_reward_name" :disabled="!!ruleError || savingRule" /></label>
        <label>KOOK Tag 标识<Input v-model="rule.full_attendance_tag_code" :disabled="!!ruleError || savingRule" /></label>
        <label>全勤奖励说明<Input v-model="rule.full_attendance_reward_desc" :disabled="!!ruleError || savingRule" /></label>
      </div>
        <p class="hint">每日消费达到补签门槛可自动获得 1 张补签卡，库存以本月上限为准。签到奖励的兴安币自动入账。</p>
        <div class="ci-actions"><Button size="xs" v-if="auth.hasPerm('checkin:edit')" :disabled="savingRule || !!ruleError" @click="saveRule">{{ savingRule ? '保存中…' : '保存签到规则' }}</Button><span v-if="ruleNotice" role="status">{{ ruleNotice }}</span></div>
      </template>
    </Card>

    <section class="ci-section" aria-label="签到礼物配置">
      <div class="ci-section-head"><div><h3>签到礼物配置</h3><p class="hint">礼物按老板本月累计签到的第 N 天发放。</p></div><Button size="xs" v-if="auth.hasPerm('checkin:edit')" variant="outline" @click="onCreateGift">新增礼物</Button></div>
      <p v-if="giftListNotice" role="status" class="ci-notice">{{ giftListNotice }}</p>
      <SimplePagedList ref="giftTable" :fetcher="checkinGiftApi.list"><template #default="{ rows }">
        <div class="ci-records"><Table>
          <TableHeader><TableRow><TableHead>累计天数 / 礼物</TableHead><TableHead>礼物说明</TableHead><TableHead>兴安币奖励</TableHead><TableHead>状态</TableHead><TableHead v-if="auth.hasPerm('checkin:edit')">操作</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id">
            <TableCell class="ci-name"><strong><span aria-hidden="true">{{ row.icon || '🎁' }}</span> {{ row.name }}</strong><small>累计第 {{ row.checkin_day }} 天</small></TableCell>
            <TableCell class="ci-desc">{{ row.description || '未填写说明' }}</TableCell>
            <TableCell class="ci-reward"><span>奖励 {{ amountToXaCoin(row.reward_amount) }} 币</span></TableCell>
            <TableCell class="ci-status"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
            <TableCell v-if="auth.hasPerm('checkin:edit')" class="ci-command"><Button size="xs" variant="outline" @click="onEditGift(row)">编辑</Button></TableCell>
          </TableRow></TableBody>
        </Table></div>
      </template></SimplePagedList>
    </section>

    <section class="ci-section" aria-label="月度签到记录">
      <div><h3>月度签到记录</h3><p class="hint">全勤记录表示老板已获得标签资格，客服需在 KOOK 中核实发放。</p></div>
      <SimplePagedList :fetcher="checkinProgressApi.list" :query="progressQuery">
        <template #toolbar="{ reload }"><div class="ci-filter"><label>年份<Input v-model.number="progressQuery.year" type="number" min="2024" max="2100" @change="validYear(); reload()" /></label><label>月份<NativeSelect :model-value="String(progressQuery.month)" @update:model-value="value => { progressQuery.month = Number(value); reload() }"><NativeSelectOption v-for="m in 12" :key="m" :value="String(m)">{{ m }}月</NativeSelectOption></NativeSelect></label></div></template>
        <template #default="{ rows }"><div class="ci-records"><Table>
          <TableHeader><TableRow><TableHead>老板昵称 / 账号</TableHead><TableHead>统计月度</TableHead><TableHead>签到与补签卡</TableHead><TableHead>全勤资格 / KOOK Tag</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id">
            <TableCell class="ci-name"><strong>{{ row.nickname || '未填写昵称' }}</strong><small>{{ row.username || '账号待核对' }}</small></TableCell>
            <TableCell class="ci-month">{{ row.year }}-{{ String(row.month).padStart(2,'0') }}</TableCell>
            <TableCell class="ci-progress">已签到 {{ row.checked_count }} 天<small>补签卡 {{ row.makeup_cards }} 张</small></TableCell>
            <TableCell class="ci-qualification"><Badge :variant="row.full_attendance_awarded ? 'secondary' : 'outline'">{{ row.full_attendance_awarded ? '已达成全勤' : '未达成全勤' }}</Badge><small v-if="row.full_attendance_awarded && row.full_attendance_reward_name">{{ row.full_attendance_reward_name }}</small><small>KOOK Tag {{ row.full_attendance_tag_code || '—' }}</small></TableCell>
          </TableRow></TableBody>
        </Table></div></template>
      </SimplePagedList>
    </section>
  </div>

  <Dialog :open="giftVisible" @update:open="value => { if (!savingGift) giftVisible = value }"><DialogContent :show-close-button="!savingGift" class="ci-dialog"><DialogHeader><DialogTitle>{{ giftForm.id ? '编辑签到礼物' : '新增签到礼物' }}</DialogTitle><DialogDescription>设置累计签到天数和奖励。</DialogDescription></DialogHeader>
    <div class="ci-dialog-body"><div class="ci-fields"><label>签到第 N 天<Input v-model.number="giftForm.checkin_day" type="number" min="1" max="31" step="1" :disabled="savingGift" /></label><label>礼物名称<Input v-model="giftForm.name" :disabled="savingGift" /></label><label>礼物图标<Input v-model="giftForm.icon" placeholder="支持 Emoji，如 🎁" :disabled="savingGift" /></label><label>礼物说明<Textarea v-model="giftForm.description" :rows="3" :disabled="savingGift" /></label><label>奖励兴安币<Input v-model.number="giftCoin" type="number" min="0" step="0.1" :disabled="savingGift" /></label><label for="checkin-gift-enabled" class="ci-switch">启用<Switch id="checkin-gift-enabled" v-model="giftForm.is_active" aria-label="启用" :disabled="savingGift" /></label></div><p v-if="giftNotice" role="alert">{{ giftNotice }}</p></div>
    <DialogFooter><Button size="xs" variant="outline" :disabled="savingGift" @click="giftVisible = false">取消</Button><Button size="xs" :disabled="savingGift" @click="saveGift">{{ savingGift ? '保存中…' : '保存' }}</Button></DialogFooter></DialogContent></Dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Switch } from '@xa/shadcn-vue/switch'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { checkinConfigApi, checkinGiftApi, checkinProgressApi } from '@/api/modules'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const giftTable = ref()
const savingRule = ref(false)
const ruleLoaded = ref(false), ruleError = ref(''), ruleNotice = ref(''), giftNotice = ref('')
const giftListNotice = ref('')
const savingGift = ref(false)
const giftVisible = ref(false)
const dailySpendCoin = ref(188)
const cardSpendCoin = ref(388)
const giftCoin = ref(0)
const now = new Date()
const progressQuery = reactive({ year: now.getFullYear(), month: now.getMonth() + 1 })
function validYear() { const value = Number(progressQuery.year); progressQuery.year = Number.isFinite(value) ? Math.max(2024, Math.min(2100, Math.round(value))) : now.getFullYear() }
const rule = reactive<any>({ max_makeup_cards: 3, full_attendance_reward_name: 'KOOK专属Tag', full_attendance_reward_desc: '', full_attendance_tag_code: '' })
const giftForm = reactive<any>({})

async function loadRule() {
  ruleError.value = ''
  try {
    const res = await checkinConfigApi.get()
    Object.assign(rule, res.data)
    dailySpendCoin.value = Number(amountToXaCoin(rule.daily_spend_required))
    cardSpendCoin.value = Number(amountToXaCoin(rule.makeup_card_spend_required))
    ruleLoaded.value = true
  } catch { ruleError.value = '签到规则读取失败，请重试' }
}

async function saveRule() {
  if (savingRule.value || !ruleLoaded.value || !auth.hasPerm('checkin:edit')) return
  if (!validCoin(dailySpendCoin.value, 1) || !validCoin(cardSpendCoin.value, 1) || cardSpendCoin.value < dailySpendCoin.value) { ruleNotice.value = '门槛须至少为 1 币、最多一位小数，补签门槛不能低于签到门槛'; return }
  if (!Number.isInteger(rule.max_makeup_cards) || rule.max_makeup_cards < 1 || rule.max_makeup_cards > 31) { ruleNotice.value = '补签卡上限须为 1 至 31 的整数'; return }
  ruleNotice.value = ''
  savingRule.value = true
  try {
    await checkinConfigApi.update({
      ...rule,
      daily_spend_required: xaCoinToAmount(dailySpendCoin.value),
      makeup_card_spend_required: xaCoinToAmount(cardSpendCoin.value),
    })
    ruleNotice.value = '签到规则已保存'
    await loadRule()
  } catch (error) { ruleNotice.value = error instanceof Error ? error.message : '保存失败，请重试' }
  finally { savingRule.value = false }
}

function validCoin(value: number, min: number) { return typeof value === 'number' && Number.isFinite(value) && value >= min && Math.abs(value * 10 - Math.round(value * 10)) < 1e-8 }

function onCreateGift() {
  giftNotice.value = ''
  Object.assign(giftForm, { id: 0, checkin_day: 1, name: '', description: '', icon: '🎁', is_active: true })
  giftCoin.value = 0
  giftVisible.value = true
}

function onEditGift(row: any) {
  giftNotice.value = ''
  Object.assign(giftForm, row)
  giftCoin.value = Number(amountToXaCoin(row.reward_amount))
  giftVisible.value = true
}

async function saveGift() {
  if (savingGift.value || !auth.hasPerm('checkin:edit')) return
  if (!giftForm.name?.trim()) { giftNotice.value = '请填写礼物名称'; return }
  if (!Number.isInteger(giftForm.checkin_day) || giftForm.checkin_day < 1 || giftForm.checkin_day > 31) { giftNotice.value = '签到天数须为 1 至 31 的整数'; return }
  if (!validCoin(giftCoin.value, 0)) { giftNotice.value = '奖励须不小于 0，最多一位小数'; return }
  giftNotice.value = ''
  savingGift.value = true
  try {
    const payload = { ...giftForm, reward_amount: xaCoinToAmount(giftCoin.value) }
    if (giftForm.id) await checkinGiftApi.update(giftForm.id, payload)
    else await checkinGiftApi.create(payload)
    giftListNotice.value = '签到礼物已保存'
    giftVisible.value = false
    giftTable.value.reload()
  } catch (error) { giftNotice.value = error instanceof Error ? error.message : '保存失败，请重试' }
  finally { savingGift.value = false }
}

onMounted(loadRule)
</script>

<style scoped>
.ci-page { min-width:0; display:grid; gap:12px; container:checkin / inline-size; }
.ci-header,.ci-rule-card,.ci-section { padding:16px; }
.ci-header { gap:4px; padding-block:12px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.ci-header h2,.ci-page h3 { font-size:16px; font-weight:600; }
.ci-header p,.hint { font-size:12px; line-height:1.5; color:var(--muted-foreground); }
.ci-rule-card,.ci-section { display:grid; align-content:start; min-width:0; gap:12px; }
.ci-section { border:1px solid var(--border); background:var(--card); border-radius:12px; }
.ci-section-head,.ci-actions,.ci-filter { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.ci-section-head { justify-content:space-between; }
.ci-section h3 + .hint { margin-top:4px; }
.rule-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px 12px; }
.rule-summary>div { min-width:0; } .rule-summary dt { font-size:12px; color:var(--muted-foreground); } .rule-summary dd { margin-top:4px; font-size:13px; overflow-wrap:anywhere; }
.ci-fields { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); align-items:start; gap:12px; }
.ci-fields label,.ci-filter label { display:grid; min-width:0; gap:4px; font-size:12px; }
.ci-fields :deep([data-slot='input']),.ci-filter :deep(input),.ci-filter :deep(select) { width:100%; min-width:0; min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.ci-filter label { width:96px; }
.ci-filter :deep([data-slot='native-select-wrapper']) { width:100%; }
.ci-filter :deep(select) { padding-right:32px; }
.ci-notice,.ci-actions span { font-size:13px; }
.ci-records { min-width:0; }
.ci-records :deep(th),.ci-records :deep(td) { padding:8px; white-space:normal; vertical-align:top; overflow-wrap:anywhere; }
.ci-records :deep(th) { font-size:12px; background:var(--muted); } .ci-records :deep(td) { font-size:13px; }
.ci-records small { display:block; margin-top:4px; font-size:12px; color:var(--muted-foreground); }
:global(.ci-dialog) { display:grid; grid-template-rows:auto minmax(0,1fr) auto; width:min(520px,calc(100vw - 24px)); max-width:calc(100vw - 24px); max-height:calc(100dvh - 24px); padding:16px; gap:12px; }
:global(.ci-dialog [data-slot='dialog-title']) { padding-right:32px; }
.ci-dialog-body { min-height:0; overflow:auto; overscroll-behavior:contain; }
.ci-dialog-body .ci-fields { grid-template-columns:repeat(2,minmax(0,1fr)); }
.ci-dialog-body .ci-switch { display:flex; min-height:28px; align-items:center; justify-content:space-between; padding:4px 8px; border:1px solid var(--border); border-radius:8px; cursor:pointer; }
.ci-dialog-body [role='alert'] { margin-top:8px; font-size:13px; color:var(--destructive); }
@container checkin (max-width:760px) {
  .ci-fields,.rule-summary { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .ci-rule-card,.ci-section { padding:12px; }
  .ci-page :deep(.table-scroll),.ci-records :deep([data-slot='table-container']) { border:0; overflow:visible; }
  .ci-records :deep(table),.ci-records :deep(tbody) { display:grid; }
  .ci-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .ci-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .ci-records :deep(tbody tr) { display:grid; grid-template-columns:minmax(0,1fr) auto; align-content:start; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
  .ci-records :deep(td) { padding:0; }
  .ci-name { grid-row:1; grid-column:1; }
  .ci-status,.ci-month { grid-row:1; grid-column:2; text-align:right; }
  .ci-reward,.ci-desc,.ci-progress,.ci-qualification { grid-column:1/-1; }
  .ci-page :deep(.pages) { justify-content:flex-start; }
}
@container checkin (max-width:480px) { .ci-fields,.ci-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(max-width:480px) { .ci-dialog-body .ci-fields { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) {
  .ci-page :deep([data-slot='button']),:global(.ci-dialog [data-slot='button']) { min-height:44px; min-width:44px; }
  .ci-fields :deep([data-slot='input']),.ci-filter :deep(input),.ci-filter :deep(select),.ci-dialog-body .ci-switch { min-height:44px; }
}
</style>
