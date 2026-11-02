<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { formatRmb } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { RadioGroup, RadioGroupItem } from '@xa/shadcn-vue/radio-group'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@xa/shadcn-vue/tabs'
import { Input } from '@xa/shadcn-vue/input'

type Field = 'id_front'|'id_back'|'deposit_proof'
type Form = {username:string;password:string;phone:string;real_name:string;id_number:string;display_name:string;deposit_mode:'CASH'|'EARNINGS'}
type Rules = {cash_target:number;earnings_target:number;earnings_multiplier:number;deposit_percent:number;settlement_percent:number;hold_hours:number}
type Result = {status:'PENDING'|'APPROVED'|'REJECTED';status_display:string;display_name:string;review_note:string;deposit_mode:'CASH'|'EARNINGS';deposit_required?:number;level_name?:string}
type Envelope<T> = {code:number;msg?:string;data?:T | {fields?:Record<string,string[]>}}
const router = useRouter(), form = ref<Form>({username:'',password:'',phone:'',real_name:'',id_number:'',display_name:'',deposit_mode:'CASH'}), confirmation = ref(''), photos = ref<Partial<Record<Field,File>>>({})
const rules = ref<Rules | null>(null), ruleError = ref(''), error = ref(''), errors = ref<Record<string,string>>({}), busy = ref(false), mode = ref<'apply'|'status'>('apply'), resubmit = ref(false), result = ref<Result | null>(null)
const labels:Record<Field,string> = {id_front:'身份证正面',id_back:'身份证反面',deposit_proof:'押金交付凭证'}
const requiredPhotos = computed<Field[]>(() => form.value.deposit_mode === 'CASH' ? ['id_front','id_back','deposit_proof'] : ['id_front','id_back'])
const depositModes = computed(() => rules.value ? [
  {key:'CASH',title:'现金缴纳',amount:rules.value.cash_target,description:'提交凭证，由管理人员核实现金到账。'},
  {key:'EARNINGS',title:'单抵双倍',amount:rules.value.earnings_target,description:'申请时无需提交现金押金凭证。'},
] : [])
function changeMode(value: unknown) {
  if (busy.value || (value !== 'apply' && value !== 'status')) return
  mode.value = value
  error.value = ''
  errors.value = {}
}
function beginResubmission() {
  if (result.value?.status !== 'REJECTED') return
  form.value.display_name = result.value.display_name
  form.value.deposit_mode = result.value.deposit_mode
  resubmit.value = true
  changeMode('apply')
}
watch([() => form.value.username, () => form.value.password], () => {
  result.value = null
  if (mode.value === 'status') resubmit.value = false
})
let alive = true
async function request<T>(path:string, body?:BodyInit, json = false):Promise<T> {
  const response = await fetch('/api/provider-registration/'+path,{method:body ? 'POST' : 'GET',body,headers:json ? {'Content-Type':'application/json'} : undefined})
  const data = await response.json() as Envelope<T>
  if (!response.ok || data.code !== 0 || !data.data) { const fields = (data.data as {fields?:Record<string,string[]>} | undefined)?.fields; throw Object.assign(new Error(data.msg || '提交失败，请重试'),{fields:fields ? Object.fromEntries(Object.entries(fields).map(([key,value]) => [key,value.join('；')])) : {}}) }
  return data.data as T
}
async function loadRules() { ruleError.value = ''; try { const value = await request<Rules>('rules/'); if (alive) rules.value = value } catch { if (alive) ruleError.value = '押金规则加载失败，请重试' } }
function choose(field:Field,event:Event) {
  const element = event.target as HTMLInputElement, file = element.files?.[0]; element.value = ''
  if (!file) return
  if (!['image/jpeg','image/png','image/webp'].includes(file.type)) { errors.value[field] = '请选择 JPG、PNG 或 WebP 照片'; return }
  if (file.size > 5*1024*1024) { errors.value[field] = '照片不能超过 5MB'; return }
  photos.value = {...photos.value,[field]:file}; delete errors.value[field]
}
function validate() {
  const f = form.value, e:Record<string,string> = {}
  if (!/^[a-zA-Z0-9_]{4,32}$/.test(f.username.trim())) e.username = '账号需为 4 至 32 位字母、数字或下划线'
  if (f.password.length < 8 || f.password.length > 128) e.password = '密码需为 8 至 128 位'
  if (mode.value === 'apply') {
    if (confirmation.value !== f.password) e.confirm_password = '两次输入的密码不一致'
    if (!/^1[3-9]\d{9}$/.test(f.phone.trim())) e.phone = '请填写有效手机号'
    if (f.real_name.trim().length < 2) e.real_name = '请填写真实姓名'
    if (!/^[1-9]\d{16}[\dXx]$/.test(f.id_number.trim())) e.id_number = '请填写 18 位身份证号码'
    if (!f.display_name.trim()) e.display_name = '请填写结单昵称'
    for (const field of requiredPhotos.value) if (!photos.value[field]) e[field] = '请上传这张照片'
  }
  errors.value = e; return !Object.keys(e).length
}
async function submit() {
  if (busy.value || (mode.value === 'apply' && !rules.value) || !validate()) return
  busy.value = true; error.value = ''
  try {
    let next:Result
    if (mode.value === 'status') {
      result.value = null
      next = await request<Result>('status/',JSON.stringify({username:form.value.username.trim(),password:form.value.password}),true)
    }
    else {
      const f = form.value, data = new FormData(), values = {...f,username:f.username.trim(),phone:f.phone.trim(),real_name:f.real_name.trim(),id_number:f.id_number.trim().toUpperCase(),display_name:f.display_name.trim()}
      Object.entries(values).forEach(([key,value]) => data.append(key,value))
      for (const field of requiredPhotos.value) if (photos.value[field]) data.append(field,photos.value[field]!)
      next = await request<Result>(resubmit.value ? 'resubmit/' : '',data)
      photos.value = {}; form.value = {username:f.username,password:f.password,phone:'',real_name:'',id_number:'',display_name:'',deposit_mode:'CASH'}; confirmation.value = ''; mode.value = 'status'; resubmit.value = false
    }
    if (alive) result.value = next
  } catch (cause) { if (alive) { const failure = cause as {message?:string;fields?:Record<string,string>}; error.value = failure.message || '提交失败，请检查网络后重试'; errors.value = failure.fields || {} } }
  finally { if (alive) busy.value = false }
}
onMounted(() => { void loadRules() }); onUnmounted(() => { alive = false })
</script>
<template>
  <main class="page stack register">
    <header class="register-heading">
      <div><p class="muted">兴安电竞 · 陪玩端</p><h1>{{ mode === 'status' ? '申请审核进度' : resubmit ? '补充注册资料' : '申请成为陪玩' }}</h1></div>
      <Button size="xs" variant="outline" @click="router.push('/login')">返回登录</Button>
    </header>
    <Tabs :model-value="mode" class="register-tabs" @update:model-value="changeMode">
      <TabsList aria-label="注册与查询"><TabsTrigger value="apply" :disabled="busy">填写申请</TabsTrigger><TabsTrigger value="status" :disabled="busy">查询审核</TabsTrigger></TabsList>
      <TabsContent :key="mode" :value="mode">
        <form class="register-form" @submit.prevent="submit">
          <Card class="panel account-card">
            <div><h2>{{ mode === 'status' ? '查询凭据' : '登录信息' }}</h2><p class="muted">{{ mode === 'status' ? '使用注册时的账号和密码查询审核进度。' : '请保存账号和密码。审核通过后，用此账号登录并绑定验证器。' }}</p></div>
            <div class="fields">
              <label>登录账号<Input v-model="form.username" maxlength="32" placeholder="4 至 32 位字母、数字或下划线" :disabled="busy || (resubmit && mode === 'apply')" autocomplete="username" :aria-invalid="!!errors.username" /><small v-if="errors.username" class="error">{{ errors.username }}</small></label>
              <label>登录密码<Input v-model="form.password" type="password" maxlength="128" placeholder="至少 8 位" :disabled="busy" :autocomplete="mode === 'status' ? 'current-password' : 'new-password'" :aria-invalid="!!errors.password" /><small v-if="errors.password" class="error">{{ errors.password }}</small></label>
              <template v-if="mode === 'apply'">
                <label>确认密码<Input v-model="confirmation" type="password" maxlength="128" :disabled="busy" autocomplete="new-password" :aria-invalid="!!errors.confirm_password" /><small v-if="errors.confirm_password" class="error">{{ errors.confirm_password }}</small></label>
                <label>手机号<Input v-model="form.phone" type="tel" maxlength="11" :disabled="busy" autocomplete="tel" :aria-invalid="!!errors.phone" /><small v-if="errors.phone" class="error">{{ errors.phone }}</small></label>
              </template>
            </div>
          </Card>
          <template v-if="mode === 'apply'">
            <Card class="panel">
              <div><h2>实名资料</h2><p class="muted">真实姓名与证件一致，结单昵称用于接单和报单展示。</p></div>
              <div class="fields"><label>真实姓名<Input v-model="form.real_name" maxlength="50" :disabled="busy" :aria-invalid="!!errors.real_name" /><small v-if="errors.real_name" class="error">{{ errors.real_name }}</small></label>
                <label>身份证号码<Input v-model="form.id_number" maxlength="18" :disabled="busy" :aria-invalid="!!errors.id_number" /><small v-if="errors.id_number" class="error">{{ errors.id_number }}</small></label>
                <label>结单昵称<Input v-model="form.display_name" maxlength="50" :disabled="busy" :aria-invalid="!!errors.display_name" /><small v-if="errors.display_name" class="error">{{ errors.display_name }}</small></label>
              </div>
            </Card>
            <Card class="panel">
              <h2>押金方式</h2>
              <Alert v-if="ruleError" variant="destructive" role="alert"><AlertDescription>{{ ruleError }} <Button size="xs" variant="outline" @click="loadRules">重试加载</Button></AlertDescription></Alert>
              <template v-else-if="rules">
                <RadioGroup v-model="form.deposit_mode" aria-label="押金方式" class="modes" :disabled="busy">
                  <label v-for="item in depositModes" :key="item.key" :for="'deposit-mode-'+item.key" class="mode" :data-selected="form.deposit_mode === item.key">
                    <span class="mode-heading"><strong>{{ item.title }}</strong><RadioGroupItem :id="'deposit-mode-'+item.key" :value="item.key" :aria-label="item.title" /></span>
                    <span class="target">{{ formatRmb(item.amount) }} <small>元</small></span><span class="muted">{{ item.description }}</span>
                  </label>
                </RadioGroup>
                <p class="muted">单抵目标为现金押金的 {{ rules.earnings_multiplier }} 倍。<template v-if="rules.hold_hours > 0">押金未缴足时，净收益按 {{ rules.deposit_percent }}% 抵押金、{{ rules.settlement_percent }}% 结款，抵满后全部结款。当前新订单记录 {{ rules.hold_hours }} 小时冻结。</template><template v-else>当前新订单不启用收益冻结，结算时不会自动抵扣押金。历史订单按创建时记录的冻结时长处理。</template></p>
              </template>
              <p v-else role="status" class="muted">正在读取押金规则…</p>
            </Card>
            <Card class="panel">
              <div><h2>上传审核材料</h2><p class="muted">仅限审核人员查看。支持 JPG、PNG、WebP，每张最多 5 MB。</p></div>
              <div class="photos"><div v-for="field in requiredPhotos" :key="field" class="photo">
                <div><strong>{{ labels[field] }}</strong><p class="muted">{{ photos[field]?.name || '未选择照片' }}</p><small v-if="errors[field]" class="error">{{ errors[field] }}</small></div>
                <Button as="label" size="xs" variant="outline" class="file-button">{{ photos[field] ? '更换' : '选择照片' }}<input type="file" :aria-label="'选择'+labels[field]" accept="image/jpeg,image/png,image/webp" :disabled="busy" @change="choose(field,$event)" /></Button>
              </div></div>
              <p class="muted">{{ form.deposit_mode === 'CASH' ? '现金押金凭证须由管理人员核实实际到账。' : '单抵申请不登记现金到账。' }}提交后请等待人工审核。</p>
            </Card>
          </template>
          <Alert v-if="error" variant="destructive" role="alert"><AlertDescription>{{ error }}</AlertDescription></Alert>
          <Card v-if="mode === 'status' && result" class="panel status-card" aria-live="polite">
            <div class="status-head"><h2>{{ result.display_name }}</h2><Badge variant="secondary">{{ result.status_display }}</Badge></div>
            <p class="muted">{{ result.status === 'PENDING' ? '资料已提交，请等待管理人员审核。' : result.status === 'APPROVED' ? '账号已开通，请返回登录并完成验证器绑定。' : '请按审核意见补充资料，再重新提交。' }}</p>
            <dl class="status-facts"><div><dt>押金方式</dt><dd>{{ result.deposit_mode === 'CASH' ? '现金缴纳' : result.deposit_mode === 'EARNINGS' ? '单抵双倍' : '待核对' }}</dd></div><div v-if="typeof result.deposit_required === 'number'"><dt>应缴押金</dt><dd>{{ formatRmb(result.deposit_required) }} 元</dd></div><div v-if="result.level_name && result.status === 'APPROVED'"><dt>开通等级</dt><dd>{{ result.level_name }}</dd></div></dl>
            <p v-if="result.review_note" class="review-note">审核意见 · {{ result.review_note }}</p>
            <div v-if="result.status === 'APPROVED'"><Button size="xs" variant="outline" @click="router.push('/login')">前往登录</Button></div>
            <div v-if="result.status === 'REJECTED'"><Button size="xs" variant="outline" @click="beginResubmission">补充资料并重新提交</Button></div>
          </Card>
          <div><Button size="xs" type="submit" class="register-submit" :disabled="busy || (mode === 'apply' && !rules)">{{ busy ? '正在提交…' : mode === 'status' ? '查询进度' : resubmit ? '重新提交审核' : '提交审核' }}</Button></div>
        </form>
      </TabsContent>
    </Tabs>
  </main>
</template>
<style scoped>
.register { --target:24px; --field:28px; max-width:760px; gap:12px; }
.register-heading,.mode-heading,.photo,.status-head { display:flex; align-items:center; gap:8px; justify-content:space-between; }
.register-heading { flex-wrap:wrap; }
h1,h2,p { margin:0; } h1 { margin-top:4px; font-size:24px; line-height:1.3; } h2 { font-size:16px; line-height:1.5; } h2+p { margin-top:4px; }
.register-tabs,.register-form { display:grid; min-width:0; gap:12px; }
.register :deep([data-slot='tabs-list']) { display:inline-flex; justify-self:start; width:auto; height:auto; gap:4px; padding:4px; background:var(--secondary); border-radius:8px; }
.register :deep([data-slot='tabs-trigger']) { min-height:var(--target); min-width:44px; padding:0 12px; border:1px solid transparent; background:transparent; color:var(--muted-foreground); font:inherit; font-size:12px; }
.register :deep([data-slot='tabs-trigger'][data-state='active']) { background:var(--card); border-color:var(--border); color:var(--primary); }
.register .panel { padding:16px; gap:12px; min-width:0; }
.account-card { background:linear-gradient(115deg,var(--secondary),var(--card) 56%); }
.fields,.modes,.status-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.fields label { display:grid; align-content:start; gap:4px; min-width:0; font-size:12px; }
.fields :deep(input) { width:100%; min-width:0; height:var(--field); min-height:var(--field); padding:4px 8px; background:var(--card); border:1px solid var(--input); font:inherit; font-size:13px; }
.register .error { overflow-wrap:anywhere; font-size:12px; }
.mode { display:grid; align-content:start; gap:4px; padding:8px; background:var(--card); border:1px solid var(--border); border-radius:8px; cursor:pointer; min-width:0; }
.mode[data-selected=true] { background:var(--secondary); border-color:var(--primary); }
.mode-heading { gap:4px; font-size:13px; } .target { font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; } .target small { font-weight:400; font-size:12px; }
.mode :deep([data-slot='radio-group-item']) { width:16px; height:16px; min-width:16px; min-height:16px; padding:0; border:1px solid var(--input); border-radius:50%; color:var(--primary); background:var(--card); }
.mode:focus-within,.file-button:focus-within { outline:2px solid var(--primary); outline-offset:2px; }
.photo { padding:8px 0; } .photo + .photo { border-top:1px solid var(--border); } .photo>div { min-width:0; } .photo strong,.status-facts dd { font-size:13px; } .photo p,.review-note,.status-head h2 { overflow-wrap:anywhere; }
.file-button { position:relative; overflow:hidden; flex-shrink:0; cursor:pointer; }
.file-button input { cursor:pointer; position:absolute; inset:0; width:100%; height:100%; opacity:0; }
.status-facts { gap:8px; margin:0; } .status-facts dt { font-size:12px; color:var(--muted-foreground); } .status-facts dd { margin:4px 0 0; }
.review-note { white-space:pre-wrap; font-size:13px; }
.register :deep([data-slot='button']) { min-height:var(--target); padding-inline:8px; border:1px solid transparent; background:var(--primary); color:var(--primary-foreground); font:inherit; font-size:12px; }
.register :deep([data-slot='button'][data-variant='outline']) { background:var(--card); color:var(--foreground); border-color:var(--border); }
@media(max-width:520px) { .register .panel { padding:12px; } .fields { grid-template-columns:minmax(0,1fr); } .modes { gap:8px; } }
@media(pointer:coarse),(max-width:600px) { .register { --target:44px; --field:44px; } .register :deep([data-slot='button']) { min-width:44px; } .fields :deep(input) { font-size:16px; } }
</style>
