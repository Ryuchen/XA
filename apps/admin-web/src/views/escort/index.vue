<template>
<div class="es-page">
<div class="ap-page-head">
<p class="ap-page-intro">管理入驻陪玩师的资质、状态与业绩</p>
<div class="ap-page-head-actions">
<Button size="xs" v-if="auth.hasPerm('escort:edit')" @click="onCreate" :disabled="(!!saveReview)">创建陪玩账号</Button>
</div>
</div>
<Alert v-if="saveReview && !dialogVisible && !createVisible && !saving && !creating">
<AlertDescription>
<strong>{{ "陪玩资料保存结果待核对" }}</strong>
<p >{{ saveReview.id ? `档案 #${saveReview.id}` : `账号 ${saveReview.username}` }} 的请求可能仍在处理。请读取最新档案，并确认实际结果后再继续操作。</p>
<p v-if="reviewNotice" role="status">{{ reviewNotice }}</p>
<Button size="xs" @click="checkSaveReview" :disabled="(reviewLoading)">读取最新档案</Button>
<Button size="xs" v-if="reviewChecked" @click="finishSaveReview" :disabled="(reviewLoading)">已核对，解除限制</Button>
</AlertDescription>
</Alert>
<Alert v-if="disposeReview && !disposeVisible">
<AlertDescription>
<strong>{{ "奖罚结果待核对" }}</strong>
<p >{{ disposeReview.name }} · {{ disposeReview.payload.dispose_type === 'REWARD' ? '奖励' : '罚款' }} {{ amountToXaCoin(disposeReview.payload.amount) }} 币。原请求可能仍在处理。</p>
<p >继续同一请求：未执行时会记账，已完成时恢复原回执。</p>
<Button size="xs" @click="recoverDispose" :disabled="(disposing)">核对并继续原请求</Button>
<Button size="xs" v-if="auth.hasPerm('dispose:view') && disposeReview.accountId" @click="openDisposeRecords">查看奖罚记录</Button>
<Button size="xs" @click="finishDisposeReview" :disabled="(disposing)">已核对，解除限制</Button>
</AlertDescription>
</Alert>
<CrudTable ref="tableRef" :fetcher="escortApi.list" :query="query">
<template #toolbar="{ reload }">
<div class="es-search">
<Input v-model="query.keyword" placeholder="昵称/账号/手机号" style="width: 200px" @keyup.enter="reload" @clear="reload"   />
<Button v-if="query.keyword" size="xs" variant="outline" aria-label="清空搜索" @click="query.keyword='';reload()">清空</Button>
</div>
<NativeSelect v-model="query.status" style="width: 120px" @change="reload" aria-label="状态">
<NativeSelectOption value="">{{ "状态" }}</NativeSelectOption>
<NativeSelectOption v-for="(v, k) in ESCORT_STATUS" :key="k" :value="k">{{ v.label }}</NativeSelectOption>
</NativeSelect>
<NativeSelect v-model="query.is_verified" style="width: 120px" @change="reload" aria-label="认证">
<NativeSelectOption value="">{{ "认证" }}</NativeSelectOption>
<NativeSelectOption value="true">{{ "已认证" }}</NativeSelectOption>
<NativeSelectOption value="false">{{ "未认证" }}</NativeSelectOption>
</NativeSelect>
<Button size="xs" @click="reload">查询</Button>
<Select :model-value="query.game_category || '__all_games'" @update:open="value => { if(value) gameFilterSearch = '' }" @update:model-value="value => {query.game_category = value === '__all_games' ? '' : value;reload()}"><SelectTrigger size="sm" aria-label="可接游戏" style="width:160px"><SelectValue placeholder="可接游戏" /></SelectTrigger><SelectContent><Input v-model="gameFilterSearch" aria-label="搜索可接游戏" placeholder="搜索游戏" @keydown.stop /><SelectItem value="__all_games">全部游戏</SelectItem><SelectItem v-for="game in filteredGameOptions" :key="game.id" :value="game.id">{{ game.name }}</SelectItem></SelectContent></Select>
<QualificationFilters :query="query" @change="reload" />
<NativeSelect v-model="query.gender" style="width:100px" @change="reload" aria-label="性别">
<NativeSelectOption value="">{{ "性别" }}</NativeSelectOption>
<NativeSelectOption value="MALE">{{ "男陪" }}</NativeSelectOption>
<NativeSelectOption value="FEMALE">{{ "女陪" }}</NativeSelectOption>
</NativeSelect>
<NativeSelect v-model="query.pass_tier" style="width:110px" @change="reload" aria-label="通行证">
<NativeSelectOption value="">{{ "通行证" }}</NativeSelectOption>
<NativeSelectOption v-for="p in [{value:'BLACK',label:'黑金'},{value:'GOLD',label:'黄金'},{value:'SILVER',label:'白银'},{value:'BRONZE',label:'青铜'},{value:'NONE',label:'无通行证'}]" :key="p.value" :value="p.value">{{ p.label }}</NativeSelectOption>
</NativeSelect>
</template>
<template #mobile="{ rows }">
<div class="es-mobile-list" role="list" aria-label="陪玩档案列表">
<article v-for="row in rows" :key="row.id" class="es-mobile-card" role="listitem">
<div class="es-mobile-head">
<Avatar>
<AvatarImage :src="row.avatar_url" alt="" />
<AvatarFallback>{{ (row.display_name || row.username || '陪').slice(0, 1) }}</AvatarFallback>
</Avatar>
<div class="es-mobile-person">
<strong >{{ row.display_name || row.username || '姓名待核对' }}</strong>
<span >{{ row.username || '账号待核对' }} · {{ row.escort_no || `档案 #${row.id}` }}</span>
</div>
<Badge  variant="outline">{{ escortStatusLabel(row) }}</Badge>
</div>
<div class="es-mobile-facts">
<span >{{ row.is_verified === true ? '已认证' : row.is_verified === false ? '未认证' : '认证待核对' }}</span>
<span >{{ row.gender_display || GENDER[row.gender]?.label || '性别待核对' }}</span>
<span >{{ row.pass_tier_display || '通行证待核对' }}</span>
<Popover>
<PopoverTrigger as-child>
<Button size="icon-xs" variant="ghost" class="es-status-help" aria-label="接单状态说明">?</Button>
</PopoverTrigger>
<PopoverContent>{{ "档案状态不等于可接单；账号、认证、押金、档期、具体服务及名额均需核验。" }}</PopoverContent>
</Popover>
</div>
<div class="es-mobile-group">
<span >已配置游戏</span>
<div class="es-games">
<Badge v-for="name in row.game_category_names" :key="name" variant="outline">{{ name }}</Badge>
<span v-if="!row.game_category_names?.length" class="es-secondary">{{ Array.isArray(row.game_category_names) ? '暂无' : '待核对' }}</span>
</div>
</div>
<div class="es-mobile-group">
<span >接单资格</span>
<div class="es-games">
<Badge v-for="(q, index) in activeQualifications(row)" :key="q.id || index" variant="outline">{{ q.label }}</Badge>
<span v-if="!activeQualifications(row).length" class="es-secondary">{{ qualificationSummary(row) }}</span>
</div>
</div>
<dl class="es-mobile-values">
<div >
<dt >服务技能</dt>
<dd >{{ Array.isArray(row.service_items) ? `${row.service_items.length} 项` : '待核对' }}</dd>
</div>
<div >
<dt >完成订单</dt>
<dd >{{ row.completed_order_count ?? '待核对' }}</dd>
</div>
</dl>
<div class="es-mobile-actions">
<Button size="xs" v-if="auth.hasPerm('escort:edit')" @click="onEdit(row)" :disabled="(!!saveReview)">编辑</Button>
<DropdownMenu>
<DropdownMenuTrigger as-child>
<Button size="xs" >更多</Button>
</DropdownMenuTrigger>
<DropdownMenuContent align="end">
<DropdownMenuItem v-if="auth.hasPerm('escort:verify')" :disabled="verifying" @select="onVerify(row)">{{ row.is_verified ? '取消认证' : '认证' }}</DropdownMenuItem>
<DropdownMenuItem v-if="auth.hasPerm('escort:dispose')" :disabled="!!disposeReview" @select="onDispose(row)">奖罚</DropdownMenuItem>
<DropdownMenuItem v-if="auth.hasPerm('escort:deposit_refund')" @select="onDepositRefund(row)">退还押金</DropdownMenuItem>
<DropdownMenuItem @select="banTarget = row; banVisible = true">封禁记录</DropdownMenuItem>
</DropdownMenuContent>
</DropdownMenu>
</div>
</article>
</div>
</template>
<template #desktop="{ rows }">
<Table class="es-table">
<TableHeader>
<TableRow>
<TableHead style="min-width:70px">{{ "ID" }}</TableHead>
<TableHead style="min-width:180px">{{ "昵称" }}</TableHead>
<TableHead style="min-width:220px">{{ "服务范围" }}</TableHead>
<TableHead style="min-width:80px">{{ "性别" }}</TableHead>
<TableHead style="min-width:240px">{{ "接单资格" }}</TableHead>
<TableHead style="min-width:90px">{{ "推荐位" }}</TableHead>
<TableHead style="min-width:120px">{{ "账号" }}</TableHead>
<TableHead style="min-width:110px">{{ "编号" }}</TableHead>
<TableHead style="min-width:90px">{{ "城市" }}</TableHead>
<TableHead style="min-width:80px">{{ "时薪" }}</TableHead>
<TableHead style="min-width:70px">{{ "评分" }}</TableHead>
<TableHead style="min-width:80px">{{ "完成单" }}</TableHead>
<TableHead style="min-width:120px">{{ "抢单通行证" }}</TableHead>
<TableHead style="min-width:120px">{{ "累计奖/罚" }}</TableHead>
<TableHead style="min-width:90px">
<span >状态</span>
<Popover>
<PopoverTrigger as-child>
<Button size="icon-xs" variant="ghost" class="es-status-help" aria-label="接单状态说明">?</Button>
</PopoverTrigger>
<PopoverContent>{{ "档案状态不等于可接单；账号、认证、押金、档期、具体服务及名额均需核验。" }}</PopoverContent>
</Popover>
</TableHead>
<TableHead style="min-width:80px">{{ "认证" }}</TableHead>
<TableHead style="min-width:150px" class="es-action-cell">{{ "操作" }}</TableHead>
</TableRow>
</TableHeader>
<TableBody>
<TableRow v-for="row in rows" :key="row.id">
<TableCell style="min-width:70px">{{ row["id"] }}</TableCell>
<TableCell style="min-width:180px">
<div class="es-person">
<Avatar>
<AvatarImage :src="row.avatar_url" alt="" />
<AvatarFallback>{{ (row.display_name || row.username || '陪').slice(0, 1) }}</AvatarFallback>
</Avatar>
<div >
<strong >{{ row.display_name || row.username }}</strong>
<span class="es-secondary">{{ qualificationSummary(row) }}</span>
</div>
</div>
</TableCell>
<TableCell style="min-width:220px">
<div class="es-games">
<Badge v-for="name in row.game_category_names" :key="name" variant="outline">{{ name }}</Badge>
<span v-if="!row.game_category_names?.length" class="es-secondary">尚未配置游戏</span>
</div>
<Popover>
<PopoverTrigger as-child>
<Button size="xs" variant="link">
<span class="es-secondary">{{ row.service_items?.length || 0 }} 项服务技能</span>
</Button>
</PopoverTrigger>
<PopoverContent>{{ (row.service_item_names || []).join('、') || '请在编辑中配置可接服务项' }}</PopoverContent>
</Popover>
</TableCell>
<TableCell style="min-width:80px">
<Badge  variant="outline">{{ row.gender_display || GENDER[row.gender]?.label }}</Badge>
</TableCell>
<TableCell style="min-width:240px">
<Badge v-for="(q, index) in activeQualifications(row)" :key="q.id || index" style="margin:2px" variant="outline">{{ q.label }}</Badge>
<span v-if="!activeQualifications(row).length">{{ qualificationSummary(row) }}</span>
</TableCell>
<TableCell style="min-width:90px">{{ row.recommendation_slot ? "推荐位 " + row.recommendation_slot : "—" }}</TableCell>
<TableCell style="min-width:120px">{{ row["username"] }}</TableCell>
<TableCell style="min-width:110px">{{ row.escort_no || '-' }}</TableCell>
<TableCell style="min-width:90px">{{ row["city"] }}</TableCell>
<TableCell style="min-width:80px">{{ row.price_per_hour == null ? '—' : amountToXaCoin(row.price_per_hour) }}</TableCell>
<TableCell style="min-width:70px">{{ row["rating_avg"] }}</TableCell>
<TableCell style="min-width:80px">{{ row["completed_order_count"] }}</TableCell>
<TableCell style="min-width:120px">
<Badge  variant="outline">{{ row.pass_tier_display }}</Badge>
</TableCell>
<TableCell style="min-width:120px">
<span class="es-reward">+{{ amountToXaCoin(row.total_reward) }}币</span>
          /
          <span class="es-penalty">-{{ amountToXaCoin(row.total_penalty) }}币</span>
</TableCell>
<TableCell style="min-width:90px">
<Badge  variant="outline">{{ escortStatusLabel(row) }}</Badge>
</TableCell>
<TableCell style="min-width:80px">
<Badge  variant="outline">{{ row.is_verified ? '已认证' : '未认证' }}</Badge>
</TableCell>
<TableCell style="min-width:150px" class="es-action-cell">
<Button size="xs" v-if="auth.hasPerm('escort:edit')" @click="onEdit(row)" variant="link" :disabled="(!!saveReview)">编辑</Button>
<DropdownMenu>
<DropdownMenuTrigger as-child>
<Button size="xs"  variant="link">更多</Button>
</DropdownMenuTrigger>
<DropdownMenuContent align="end">
<DropdownMenuItem v-if="auth.hasPerm('escort:verify')" :disabled="verifying" @select="onVerify(row)">{{ row.is_verified ? '取消认证' : '认证' }}</DropdownMenuItem>
<DropdownMenuItem v-if="auth.hasPerm('escort:dispose')" :disabled="!!disposeReview" @select="onDispose(row)">奖罚</DropdownMenuItem>
<DropdownMenuItem v-if="auth.hasPerm('escort:deposit_refund')" @select="onDepositRefund(row)">退还押金</DropdownMenuItem>
<DropdownMenuItem @select="banTarget = row; banVisible = true">封禁记录</DropdownMenuItem>
</DropdownMenuContent>
</DropdownMenu>
</TableCell>
</TableRow>
</TableBody>
</Table>
</template>
</CrudTable>
</div>
<Dialog :open="dialogVisible" @update:open="value => { if(!value) closeEditDialog() }">
<DialogContent class="es-shadcn-dialog" style="--es-dialog-width:800px" :show-close-button="!saving" @interact-outside.prevent="closeEditDialog()" @escape-key-down.prevent="closeEditDialog()">
<DialogHeader>
<DialogTitle>{{ reviewViewing ? '核对最新陪玩资料' : '编辑陪玩资料' }}</DialogTitle>
<DialogDescription>核对陪玩资料和操作内容后提交。</DialogDescription>
</DialogHeader>
<fieldset :disabled="saving || reviewViewing || saveUnknown" :inert="saving || (saveUnknown && !reviewViewing) || undefined" class="es-form">
<p v-if="editDirty && !saveUnknown && !reviewViewing" class="es-draft-status" role="status">有未保存的修改</p>
<div class="es-form-section">
<p class="es-section-title">基础资料</p>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "昵称" }}</span>
<Input v-model="editForm.display_name"  aria-label="昵称" />
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "性别" }}</span>
<NativeSelect v-model="editForm.gender" style="width: 100%" aria-label="性别">
<NativeSelectOption v-for="(v, k) in GENDER" :key="k" :value="k">{{ v.label }}</NativeSelectOption>
</NativeSelect>
</div>
</div>
</div>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "城市" }}</span>
<Input v-model="editForm.city"  aria-label="城市" />
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "服务区" }}</span>
<Input v-model="editForm.service_area"  aria-label="服务区" />
</div>
</div>
</div>
<div  class="es-field ">
<span>{{ "下单页推荐位" }}</span>
<NativeSelect v-model="editForm.recommendation_slot" style="width: 100%" aria-label="下单页推荐位">
<NativeSelectOption value="">{{ "不推荐" }}</NativeSelectOption>
<NativeSelectOption v-for="slot in 4" :key="slot" :value="slot">{{ '推荐位 ' + slot }}</NativeSelectOption>
</NativeSelect>
<span class="es-secondary">每个位置只能配置一位陪玩；先清空原位置可替换人选。</span>
</div>
<div  class="es-field ap-field--full">
<span>{{ "简介" }}</span>
<Textarea v-model="editForm.bio" :rows="2" placeholder="选填"  aria-label="简介" />
</div>
</div>
<div class="es-form-section">
<p class="es-section-title">陪玩形象</p>
<div class="es-grid">
<div class="es-col" style="grid-column:span 8">
<div  class="es-field ap-field--full">
<span>{{ "头像" }}</span>
<label class="es-media-upload">
<div class="es-uploader es-uploader--img">
<img v-if="editPreview.avatar" :src="editPreview.avatar" class="es-thumb" />
<span v-else aria-hidden="true" class="es-media-icon">
<Plus  />
</span>
</div>
<Input type="file" accept="image/*" :disabled="saving || reviewViewing || saveUnknown" aria-label="头像" @change="(event: Event) => pickMedia(event, (f: any) => onPickFile('avatar', f))" />
</label>
</div>
</div>
<div class="es-col" style="grid-column:span 8">
<div  class="es-field ap-field--full">
<span>{{ "查外挂图" }}</span>
<label class="es-media-upload">
<div class="es-uploader es-uploader--img">
<PrivateImage v-if="editPreview.cheat_proof" :src="editPreview.cheat_proof" class="es-thumb" />
<span v-else aria-hidden="true" class="es-media-icon">
<Plus  />
</span>
</div>
<Input type="file" accept="image/*" :disabled="saving || reviewViewing || saveUnknown" aria-label="查外挂图" @change="(event: Event) => pickMedia(event, (f: any) => onPickFile('cheat_proof', f))" />
</label>
</div>
</div>
<div class="es-col" style="grid-column:span 8">
<div  class="es-field ap-field--full">
<span>{{ "视频介绍" }}</span>
<label class="es-media-upload">
<div class="es-uploader es-uploader--video">
<video v-if="editPreview.intro_video" :src="editPreview.intro_video" class="es-thumb" />
<span v-else aria-hidden="true" class="es-media-icon">
<VideoCamera  />
</span>
</div>
<Input type="file" accept="video/*" :disabled="saving || reviewViewing || saveUnknown" aria-label="视频介绍" @change="(event: Event) => pickMedia(event, (f: any) => onPickFile('intro_video', f))" />
</label>
<div v-if="editFiles.intro_video" class="tip">{{ editFiles.intro_video.name }}</div>
</div>
</div>
</div>
</div>
<div class="es-form-section">
<p class="es-section-title">业务档案</p>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "参考时价(币)" }}</span>
<NumberField :step-snapping="false" v-model="editHourlyCoins" :min="0" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="参考时价(币)" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "段位" }}</span>
<Input v-model="editForm.rank_tier"  aria-label="段位" />
</div>
</div>
</div>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "胜率(%)" }}</span>
<NumberField :step-snapping="false" v-model="editForm.win_rate" :min="0" :max="100" :step="1" :format-options="{minimumFractionDigits:2,maximumFractionDigits:2}">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="胜率(%)" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
</div>
<div class="es-col" style="grid-column:span 24">
<div  class="es-field ap-field--full">
<span>{{ "接单资格" }}</span>
<ProviderQualificationsEditor ref="editQualificationsRef" v-model="editForm.qualifications" :games="gameOptions" :disabled="saving || reviewViewing || saveUnknown" />
</div>
</div>
</div>
<div  class="es-field ">
<span>{{ "可接游戏" }}</span>
<Select v-model="editForm.game_categories" multiple>
<SelectTrigger size="sm" aria-label="可接游戏">
<SelectValue :placeholder='"选择该陪玩可接单的游戏（可多选）"' />
</SelectTrigger>
<SelectContent>
<SelectItem v-for="g in gameOptions" :key="g.id" :value="g.id" :disabled="!g.is_active">{{ g.name + (g.is_active ? '' : '（已停用）') }}</SelectItem>
</SelectContent>
</Select>
</div>
<div  class="es-field ap-field--full">
<span>{{ "可接服务项" }}</span>
<EscortSkillSelect v-model="editForm.service_items" :games="gameOptions" :items="serviceItemOptions" @games-selected="editForm.game_categories = [...new Set([...editForm.game_categories, ...$event])]" />
</div>
</div>
<div class="es-form-section">
<p class="es-section-title">押金与状态</p>
<div  class="es-field ">
<span>{{ "押金缴纳模式" }}</span>
<NativeSelect v-model="editForm.deposit_mode" style="width: 100%" :disabled="saving || reviewViewing || saveUnknown || editForm.deposit_paid > 0" aria-label="押金缴纳模式">
<NativeSelectOption value="CASH">{{ "现金缴纳" }}</NativeSelectOption>
<NativeSelectOption value="EARNINGS">{{ "打单抵扣" }}</NativeSelectOption>
</NativeSelect>
<div v-if="editForm.deposit_paid > 0" class="tip">已有押金，退还后才能切换模式。</div>
<div v-else-if="editForm.deposit_mode !== editOriginalDepositMode" class="tip">保存后按当前规则确定应缴押金。</div>
<div v-else class="tip">应缴金额以当前规则及档案为准。</div>
</div>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "当前应缴押金(币)" }}</span>
<Input :model-value="editForm.effective_deposit_required == null ? '待核对' : amountToXaCoin(editForm.effective_deposit_required)" readonly  aria-label="当前应缴押金(币)" />
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "已缴押金(币)" }}</span>
<Input :model-value="amountToXaCoin(editForm.deposit_paid)" readonly  aria-label="已缴押金(币)" />
</div>
</div>
</div>
<div  class="es-field ">
<span>{{ "状态" }}</span>
<NativeSelect v-model="editForm.status" style="width: 100%" aria-label="状态">
<NativeSelectOption v-for="(v, k) in ESCORT_STATUS" :key="k" :value="k">{{ v.label }}</NativeSelectOption>
</NativeSelect>
</div>
</div>
</fieldset>
<div class="es-dialog-footer">
<DialogFooter>
<Alert v-if="saveError && !reviewViewing">
<AlertDescription>
<strong>{{ saveError }}</strong>
</AlertDescription>
</Alert>
<Button size="xs" @click="closeEditDialog()" :disabled="(saving)">{{ reviewViewing ? '完成查看' : saveUnknown ? '关闭并核对' : '取消' }}</Button>
<Button size="xs" v-if="!reviewViewing" @click="onSave" :disabled="(saveUnknown) || (saving)">保存</Button>
</DialogFooter>
</div>
</DialogContent>
</Dialog>
<Dialog :open="createVisible" @update:open="value => { if(!value) closeCreateDialog() }">
<DialogContent class="es-shadcn-dialog" style="--es-dialog-width:800px" :show-close-button="!creating" @interact-outside.prevent="closeCreateDialog()" @escape-key-down.prevent="closeCreateDialog()">
<DialogHeader>
<DialogTitle>{{ "创建陪玩账号" }}</DialogTitle>
<DialogDescription>核对陪玩资料和操作内容后提交。</DialogDescription>
</DialogHeader>
<fieldset :disabled="creating || saveUnknown" :inert="creating || saveUnknown || undefined" class="es-form">
<p v-if="createDirty && !saveUnknown" class="es-draft-status" role="status">有未保存的内容</p>
<div class="es-form-section">
<p class="es-section-title">登录凭证</p>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "登录账号" }}</span>
<Input @blur="validateCreateField('username')" :aria-invalid="Boolean(createErrors.username)" v-model="createForm.username" placeholder="陪玩登录用账号"  aria-label="登录账号" />
<span :role="createErrors.username ? 'alert' : undefined" class="es-field-error">{{ createErrors.username || '\u00a0' }}</span>
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "登录密码" }}</span>
<div class="es-password">
<Input @blur="validateCreateField('password')" :aria-invalid="Boolean(createErrors.password)" v-model="createForm.password" placeholder="至少 6 位" :type="showCreatePassword ? 'text' : 'password'" aria-label="登录密码" />
<Button size="xs" type="button" variant="outline" :aria-pressed="showCreatePassword" @click="showCreatePassword=!showCreatePassword">{{ showCreatePassword ? '隐藏' : '显示' }}</Button>
</div>
<span :role="createErrors.password ? 'alert' : undefined" class="es-field-error">{{ createErrors.password || '\u00a0' }}</span>
</div>
</div>
</div>
</div>
<div class="es-form-section">
<p class="es-section-title">基础资料</p>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "昵称" }}</span>
<Input @blur="validateCreateField('display_name')" :aria-invalid="Boolean(createErrors.display_name)" v-model="createForm.display_name" placeholder="对外展示的陪玩昵称"  aria-label="昵称" />
<span :role="createErrors.display_name ? 'alert' : undefined" class="es-field-error">{{ createErrors.display_name || '\u00a0' }}</span>
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "性别" }}</span>
<NativeSelect v-model="createForm.gender" style="width: 100%" aria-label="性别">
<NativeSelectOption v-for="(v, k) in GENDER" :key="k" :value="k">{{ v.label }}</NativeSelectOption>
</NativeSelect>
</div>
</div>
</div>
<div class="es-grid">
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "手机号" }}</span>
<Input v-model="createForm.phone" placeholder="选填"  aria-label="手机号" />
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "城市" }}</span>
<Input v-model="createForm.city" placeholder="选填"  aria-label="城市" />
</div>
</div>
</div>
</div>
<div class="es-form-section">
<p class="es-section-title">陪玩形象</p>
<div class="es-grid">
<div class="es-col" style="grid-column:span 8">
<div  class="es-field ap-field--full">
<span>{{ "头像" }}</span>
<label class="es-media-upload">
<div class="es-uploader es-uploader--img">
<img v-if="createPreview.avatar" :src="createPreview.avatar" class="es-thumb" />
<span v-else aria-hidden="true" class="es-media-icon">
<Plus  />
</span>
</div>
<Input type="file" accept="image/*"  aria-label="头像" @change="(event: Event) => pickMedia(event, (f: any) => onPickCreateFile('avatar', f))" />
</label>
</div>
</div>
<div class="es-col" style="grid-column:span 8">
<div  class="es-field ap-field--full">
<span>{{ "查外挂图" }}</span>
<label class="es-media-upload">
<div class="es-uploader es-uploader--img">
<img v-if="createPreview.cheat_proof" :src="createPreview.cheat_proof" class="es-thumb" />
<span v-else aria-hidden="true" class="es-media-icon">
<Plus  />
</span>
</div>
<Input type="file" accept="image/*"  aria-label="查外挂图" @change="(event: Event) => pickMedia(event, (f: any) => onPickCreateFile('cheat_proof', f))" />
</label>
</div>
</div>
<div class="es-col" style="grid-column:span 8">
<div  class="es-field ap-field--full">
<span>{{ "视频介绍" }}</span>
<label class="es-media-upload">
<div class="es-uploader es-uploader--video">
<video v-if="createPreview.intro_video" :src="createPreview.intro_video" class="es-thumb" />
<span v-else aria-hidden="true" class="es-media-icon">
<VideoCamera  />
</span>
</div>
<Input type="file" accept="video/*"  aria-label="视频介绍" @change="(event: Event) => pickMedia(event, (f: any) => onPickCreateFile('intro_video', f))" />
</label>
<div v-if="createFiles.intro_video" class="tip">{{ createFiles.intro_video.name }}</div>
</div>
</div>
</div>
</div>
<div class="es-form-section">
<p class="es-section-title">计费与押金</p>
<div class="es-grid">
<div class="es-col" style="grid-column:span 24">
<div  class="es-field ap-field--full">
<span>{{ "接单资格" }}</span>
<ProviderQualificationsEditor ref="createQualificationsRef" v-model="createForm.qualifications" :games="gameOptions" :disabled="creating || saveUnknown" />
</div>
</div>
<div class="es-col" style="grid-column:span 12">
<div  class="es-field ">
<span>{{ "押金缴纳模式" }}</span>
<NativeSelect v-model="createForm.deposit_mode" style="width: 100%" aria-label="押金缴纳模式">
<NativeSelectOption value="CASH">{{ "现金缴纳" }}</NativeSelectOption>
<NativeSelectOption value="EARNINGS">{{ "打单抵扣" }}</NativeSelectOption>
</NativeSelect>
<div class="tip">创建后按当前规则及档案确定应缴押金。</div>
</div>
</div>
</div>
<div  class="es-field ">
<span>{{ "可接游戏" }}</span>
<Select v-model="createForm.game_categories" multiple>
<SelectTrigger size="sm" aria-label="可接游戏">
<SelectValue :placeholder='"选择该陪玩可接单的游戏（可多选）"' />
</SelectTrigger>
<SelectContent>
<SelectItem v-for="g in gameOptions" :key="g.id" :value="g.id" :disabled="!g.is_active">{{ g.name + (g.is_active ? '' : '（已停用）') }}</SelectItem>
</SelectContent>
</Select>
</div>
<div  class="es-field ap-field--full">
<span>{{ "可接服务项" }}</span>
<EscortSkillSelect v-model="createForm.service_items" :games="gameOptions" :items="serviceItemOptions" @games-selected="createForm.game_categories = [...new Set([...createForm.game_categories, ...$event])]" />
</div>
</div>
</fieldset>
<div class="es-dialog-footer">
<DialogFooter>
<Alert v-if="saveError">
<AlertDescription>
<strong>{{ saveError }}</strong>
</AlertDescription>
</Alert>
<Button size="xs" @click="closeCreateDialog()" :disabled="(creating)">{{ saveUnknown ? '关闭并核对' : '取消' }}</Button>
<Button size="xs" @click="onCreateSave" :disabled="(saveUnknown) || (creating)">创建</Button>
</DialogFooter>
</div>
</DialogContent>
</Dialog>
<Dialog :open="disposeVisible" @update:open="value => { if(!value) closeDispose() }">
<DialogContent class="es-shadcn-dialog" style="--es-dialog-width:480px" :show-close-button="!disposing" @interact-outside.prevent="closeDispose()" @escape-key-down.prevent="closeDispose()">
<DialogHeader>
<DialogTitle>{{ "奖励 / 罚款" }}</DialogTitle>
<DialogDescription>核对陪玩资料和操作内容后提交。</DialogDescription>
</DialogHeader>
<fieldset  class="es-form">
<div  class="es-field ">
<span>{{ "陪玩" }}</span>{{ disposeTarget?.display_name || disposeTarget?.username }} · {{ disposeTarget?.escort_no || '编号待核对' }} · {{ disposeTarget?.username || '账号待核对' }}</div>
<div  class="es-field ap-field--full">
<span>{{ "类型" }}</span>
<RadioGroup v-model="disposeType" :disabled="disposing || !!disposeReview" class="es-radio-group">
<label class="es-radio">
<RadioGroupItem value="REWARD" />奖励</label>
<label class="es-radio">
<RadioGroupItem value="PENALTY" />罚款</label>
</RadioGroup>
</div>
<div  class="es-field ">
<span>{{ "金额(兴安币)" }}</span>
<NumberField :step-snapping="false" v-model="disposeYuan" :min="0" :max="214748364.7" :disabled="disposing || !!disposeReview" :step="1" :format-options="{minimumFractionDigits:1,maximumFractionDigits:1}">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="金额(兴安币)" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
<div class="tip">{{ disposeType === 'REWARD' ? '奖励将增加陪玩余额' : '罚款将扣减陪玩余额（不可使余额为负）' }}</div>
</div>
<div  class="es-field ap-field--full">
<span>{{ "原因" }}</span>
<Textarea v-model="disposeReason" :rows="2" maxlength="255" placeholder="奖罚原因（可选）" :disabled="disposing || !!disposeReview"  aria-label="原因" />
</div>
</fieldset>
<Alert v-if="disposeReview">
<AlertDescription>
<strong>{{ "结果未确认，请关闭后核对原请求" }}</strong>
</AlertDescription>
</Alert>
<div class="es-dialog-footer">
<DialogFooter>
<Button size="xs" @click="closeDispose()" :disabled="(disposing)">{{ disposeReview ? '关闭' : '取消' }}</Button>
<Button size="xs" @click="onDisposeSave" :disabled="(!!disposeReview) || (disposing)">核对并提交</Button>
</DialogFooter>
</div>
</DialogContent>
</Dialog>
<account-ban-dialog v-model="banVisible" resource="escorts" :target-id="banTarget?.id" :target-name="banTarget?.display_name || banTarget?.username" @changed="tableRef?.load()" />
<Dialog :open="refundVisible" @update:open="value => { if(!value) refundVisible=false }">
<DialogContent class="es-shadcn-dialog" style="--es-dialog-width:480px" :show-close-button="true">
<DialogHeader>
<DialogTitle>{{ "退还押金" }}</DialogTitle>
<DialogDescription>核对陪玩资料和操作内容后提交。</DialogDescription>
</DialogHeader>
<fieldset  class="es-form">
<div  class="es-field ">
<span>{{ "陪玩" }}</span>{{ refundTarget?.display_name || refundTarget?.username }}</div>
<div  class="es-field ">
<span>{{ "当前已缴" }}</span>{{ amountToXaCoin(refundTarget?.deposit_paid || 0) }} 币</div>
<div  class="es-field ">
<span>{{ "退还金额(币)" }}</span>
<NumberField :step-snapping="false" v-model="refundCoins" :min="0.1" :max="Number(amountToXaCoin(refundTarget?.deposit_paid || 0))" :step="1" :format-options="{minimumFractionDigits:1,maximumFractionDigits:1}">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="退还金额(币)" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="es-field ap-field--full">
<span>{{ "退还原因" }}</span>
<Textarea v-model="refundReason" :rows="3" maxlength="200"  aria-label="退还原因" />
</div>
</fieldset>
<Alert >
<AlertDescription>
<strong>{{ "押金将退回陪玩钱包。外部打款需另走提现审核。" }}</strong>
</AlertDescription>
</Alert>
<div class="es-dialog-footer">
<DialogFooter>
<Button size="xs" @click="refundVisible = false">取消</Button>
<Button size="xs" @click="submitDepositRefund" :disabled="(refunding)">确认退回钱包</Button>
</DialogFooter>
</div>
</DialogContent>
</Dialog>
<ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>
<script setup lang="ts">
import ProviderQualificationsEditor from '@/components/ProviderQualificationsEditor.vue'
import QualificationFilters from '@/components/QualificationFilters.vue'
import PrivateImage from "@/components/PrivateImage.vue"
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import EscortSkillSelect from '@/components/EscortSkillSelect.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@xa/shadcn-vue/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { NumberField, NumberFieldContent, NumberFieldDecrement, NumberFieldIncrement, NumberFieldInput } from '@xa/shadcn-vue/number-field'
import { Avatar, AvatarFallback, AvatarImage } from '@xa/shadcn-vue/avatar'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@xa/shadcn-vue/dropdown-menu'
import { RadioGroup, RadioGroupItem } from '@xa/shadcn-vue/radio-group'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { showNotice } from '@/utils/notice'
import { Plus, Video as VideoCamera } from '@xa/shadcn-vue/icons'
import CrudTable from '@/components/CrudTable.vue'
import AccountBanDialog from '@/components/AccountBanDialog.vue'
import { escortApi, escortLevelApi, gameCategoryApi, serviceItemApi } from '@/api/modules'
import { request } from '@/api/request'
import { ESCORT_STATUS, GENDER } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const {open:confirmOpen,options:confirmOptions,ask:askConfirm,confirm:confirmYes,cancel:confirmNo}=useConfirmDialog()
async function confirmAction(description:string,title:string,options:any={}) { if(!await askConfirm({description,title,confirmText:options.confirmButtonText,cancelText:options.cancelButtonText}))throw new Error('cancel') }
async function alertAction(description:string,title:string) { await askConfirm({description,title,confirmText:'知道了',cancelText:'关闭'}) }
function successNotice(message:string) { showNotice(message,'success') }
const showCreatePassword=ref(false),gameFilterSearch=ref('')
const filteredGameOptions=computed(()=>gameOptions.value.filter(game=>game.name.toLocaleLowerCase().includes(gameFilterSearch.value.trim().toLocaleLowerCase())))
function pickMedia(event:Event,change:(file:{raw:File})=>void) { const input=event.target as HTMLInputElement;const raw=input.files?.[0];if(raw)change({raw});input.value='' }
const createErrors=reactive<Record<string,string>>({})
function validateCreateField(key:string) {
  const value=createForm[key];let error=''
  if(key==='username')error=typeof value!=='string'||!value.trim()?'请输入登录账号':value.length>150?'登录账号最多 150 字':''
  if(key==='password')error=!value?'请输入登录密码':String(value).length<6?'密码至少 6 位':''
  if(key==='display_name')error=!value?'请输入昵称':''
  createErrors[key]=error;return !error
}
function clearCreateValidation() { for(const key of Object.keys(createErrors))delete createErrors[key] }
function validateCreateForm() { const keys=['username','password','display_name'];return keys.map(validateCreateField).every(Boolean) }
const router = useRouter()
const tableRef = ref()
const verifying = ref(false)
let escortPageDisposed = false
onUnmounted(() => { escortPageDisposed = true })
watch(() => auth.sessionRevision, () => { verifying.value = false }, { flush: 'sync' })
const banVisible = ref(false)
const banTarget = ref<any>(null)
const refundVisible = ref(false)
const refundTarget = ref<any>(null)
const refundCoins = ref(0)
const refundReason = ref('')
const refundRequestId = ref('')
const refunding = ref(false)

function onDepositRefund(row: any) {
  if (!row.deposit_paid) { showNotice('该陪玩没有可退还的押金'); return }
  refundTarget.value = row
  refundCoins.value = Number(amountToXaCoin(row.deposit_paid))
  refundReason.value = ''
  refundRequestId.value = crypto.randomUUID()
  refundVisible.value = true
}

async function submitDepositRefund() {
  if (refunding.value || !refundTarget.value) return
  if (!refundReason.value.trim()) { showNotice('请填写退还原因'); return }
  const amount = xaCoinToAmount(refundCoins.value)
  if (amount <= 0 || amount > refundTarget.value.deposit_paid) { showNotice('退还金额须大于零且不超过已缴押金'); return }
  refunding.value = true
  try {
    const result = await escortApi.action(refundTarget.value.id, 'deposit-refund', {
      amount, reason: refundReason.value.trim(), client_request_id: refundRequestId.value,
    })
    refundVisible.value = false
    successNotice('押金已退回陪玩钱包')
    tableRef.value.load()
    if (result.data.active_order_count) {
      await alertAction(`该陪玩还有 ${result.data.active_order_count} 笔在途订单，请跟进服务及押金要求。`, '在途订单提醒')
    }
  } finally { refunding.value = false }
}
const query = reactive<Record<string, any>>({ keyword: '', status: '', is_verified: '', game_category: '', provider_kind:'', platform:'', security_level:'', grade:'', gender:'', pass_tier:'' })

function activeQualifications(row: any): any[] {
  return Array.isArray(row.qualifications) ? row.qualifications.filter((q: any) => q.is_active === true) : []
}
function qualificationSummary(row: any): string {
  if (!Array.isArray(row.qualifications)) return '资格待核对'
  if (!row.qualifications.length) return '资格未配置'
  const active = activeQualifications(row).length
  return active ? `${active} 项启用资格` : '资格均已停用'
}
function escortStatusLabel(row: any): string {
  return ESCORT_STATUS[row.status]?.label || '状态待核对'
}

const dialogVisible = ref(false)
const saving = ref(false)
const saveError = ref('')
const saveUnknown = ref(false)
const reviewViewing = ref(false)
const saveReview = ref<{ id: number | null; username: string } | null>(null)
const reviewChecked = ref(false)
const reviewNotice = ref('')
const reviewLoading = ref(false)
let reviewOwnerKey = ''
const editForm = reactive<any>({})
const editQualificationsRef = ref<InstanceType<typeof ProviderQualificationsEditor>>()
const createQualificationsRef = ref<InstanceType<typeof ProviderQualificationsEditor>>()
const editOriginalDepositMode = ref('')
const editHourlyCoins = ref<number>(0)
const levelOptions = ref<any[]>([])
const gameOptions = ref<any[]>([])
const serviceItemOptions = ref<any[]>([])

type MediaKey = 'avatar' | 'intro_video' | 'cheat_proof'
const editFiles = reactive<Record<MediaKey, File | null>>({ avatar: null, intro_video: null, cheat_proof: null })
const editPreview = reactive<Record<MediaKey, string>>({ avatar: '', intro_video: '', cheat_proof: '' })

function onPickFile(key: MediaKey, file: any) {
  const raw: File = file.raw
  editFiles[key] = raw
  editPreview[key] = URL.createObjectURL(raw)
}

async function ensureLevels() {
  if (levelOptions.value.length) return
  const res = (await escortLevelApi.allOptions({ is_active: 'true' })) as any
  levelOptions.value = res.data?.list || []
}

async function ensureGames() {
  const res = (await gameCategoryApi.allOptions()) as any
  gameOptions.value = res.data?.list || []
}

async function ensureServiceItems() {
  const res = (await serviceItemApi.allOptions({ for_skills: 'true' })) as any
  serviceItemOptions.value = res.data?.list || []
}
onMounted(ensureGames)

async function onEdit(row: any, forReview = false) {
  if ((saveReview.value && !forReview) || saving.value || creating.value) return false
  const revision = auth.sessionRevision
  await Promise.all([ensureLevels(), ensureGames(), ensureServiceItems()])
  if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:edit')) return false
  Object.assign(editForm, row)
  editForm.qualifications = JSON.parse(JSON.stringify(row.qualifications || []))
  // DRF DecimalField 序列化为字符串，数字控件使用 Number
  editForm.win_rate = row.win_rate != null ? Number(row.win_rate) : 0
  // 后端 game_categories 返回 id 数组，多选组件直接绑定；拷贝一份避免污染表格行
  editForm.game_categories = Array.isArray(row.game_categories) ? [...row.game_categories] : []
  editForm.service_items = Array.isArray(row.service_items) ? [...row.service_items] : []
  editHourlyCoins.value = Number(amountToXaCoin(row.price_per_hour || 0))
  editOriginalDepositMode.value = row.deposit_mode
  editFiles.avatar = editFiles.intro_video = editFiles.cheat_proof = null
  editPreview.avatar = row.avatar_url || ''
  editPreview.intro_video = row.intro_video_url || ''
  editPreview.cheat_proof = row.cheat_proof_url || ''
  reviewViewing.value = forReview
  if (!forReview) { saveError.value = ''; saveUnknown.value = false }
  editQualificationsRef.value?.reset()
  editBaseline.value = editSignature()
  dialogVisible.value = true
  return true
}

async function onSave() {
  if (saving.value || saveReview.value || saveUnknown.value || reviewViewing.value || !auth.hasPerm('escort:edit')) return
  if (!editQualificationsRef.value?.validate()) return
  const revision = auth.sessionRevision
  saveError.value = ''
  saving.value = true
  try {
    const base: Record<string, any> = {
      qualifications: editForm.qualifications || [],
      display_name: editForm.display_name,
      gender: editForm.gender,
      recommendation_slot: editForm.recommendation_slot || null,
      city: editForm.city ?? '',
      service_area: editForm.service_area ?? '',
      price_per_hour: xaCoinToAmount(editHourlyCoins.value),
      rank_tier: editForm.rank_tier ?? '',
      win_rate: editForm.win_rate,
      level: editForm.level ?? null,
      ...(editForm.deposit_mode !== editOriginalDepositMode ? { deposit_mode: editForm.deposit_mode } : {}),
      status: editForm.status,
      bio: editForm.bio ?? '',
      game_categories: Array.isArray(editForm.game_categories) ? editForm.game_categories : [],
      service_items: Array.isArray(editForm.service_items) ? editForm.service_items : [],
    }
    const hasFile = editFiles.avatar || editFiles.intro_video || editFiles.cheat_proof
    let payload: any = base
    if (hasFile) {
      // multipart 无法表达 null，level 为空时不提交以保留原值
      const fd = new FormData()
      Object.entries(base).forEach(([k, v]) => {
        if (k === 'level' && (v === null || v === '')) return
        if (k === 'game_categories') return // 数组需逐值 append，见下方
        if (k === 'service_items') return // 数组需逐值 append，见下方
        if (v === null || v === undefined) return
        fd.append(k, k === 'qualifications' ? JSON.stringify(v) : String(v))
      })
      // multipart 表达数组：同名字段多次 append，DRF 会解析为列表；空数组显式清空关联
      base.game_categories.forEach((id: number) => fd.append('game_categories', String(id)))
      base.service_items.forEach((id: number) => fd.append('service_items', String(id)))
      if (!base.game_categories.length) fd.append('game_categories', '[]')
      if (!base.service_items.length) fd.append('service_items', '[]')
      ;(['avatar', 'intro_video', 'cheat_proof'] as MediaKey[]).forEach((k) => {
        if (editFiles[k]) fd.append(k, editFiles[k] as File)
      })
      payload = fd
    }
    beginSaveReview(editForm.id, '')
    await request({ url: `/escorts/${editForm.id}/`, method: 'patch', data: payload, silent: true })
    if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:edit')) return
    clearSaveReview()
    editBaseline.value = null
    successNotice('保存成功')
    dialogVisible.value = false
    tableRef.value.load()
  } catch (error: any) {
    if (!escortPageDisposed && revision === auth.sessionRevision) handleSaveFailure(error)
  } finally {
    if (revision === auth.sessionRevision) saving.value = false
  }
}

async function onVerify(row: any) {
  if (verifying.value || !auth.hasPerm('escort:verify') || typeof row.is_verified !== 'boolean') return
  const revision = auth.sessionRevision, targetId = row.id, next = !row.is_verified
  const name = row.display_name || row.username || `档案 #${targetId}`
  verifying.value = true
  try {
    try {
      await confirmAction(next
        ? `确认认证「${name}」？接单仍需满足账号、押金、档期、服务资格和名额要求。`
        : `确认取消「${name}」的认证？取消后该陪玩不能接新单。`, next ? '确认认证' : '确认取消认证', {
        type: 'warning', confirmButtonText: next ? '确认认证' : '取消认证', cancelButtonText: '返回',
      })
    } catch { return }
    if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:verify')) return
    try {
      await escortApi.action(targetId, 'verify', { is_verified: next }, true)
      if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:verify')) return
      successNotice(next ? '认证已更新' : '认证已取消')
    } catch (error: any) {
      if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:verify')) return
      const body = error?.response?.data || error, status = error?.response?.status
      const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
      showNotice(rejected ? `认证未更新：${body?.msg || '请求被拒绝'}` : '认证结果未确认，请刷新列表核对后再操作')
    }
    if (!escortPageDisposed && revision === auth.sessionRevision) tableRef.value?.reload()
  } finally { if (revision === auth.sessionRevision) verifying.value = false }
}

// ---------------- 创建陪玩账号 ----------------
const createVisible = ref(false)
const creating = ref(false)
const createFormRef = ref({validate:async()=>validateCreateForm(),clearValidate:clearCreateValidation})
const createDepositYuan = ref<number>(0)
const defaultCreateForm = () => ({
  qualifications: [] as any[],
  username: '',
  password: '',
  display_name: '',
  gender: 'UNKNOWN',
  phone: '',
  city: '',
  deposit_mode: 'CASH',
  level: null as number | null,
  game_categories: [] as number[],
  service_items: [] as number[],
})
const createForm = reactive<Record<string, any>>(defaultCreateForm())
const createFiles = reactive<Record<MediaKey, File | null>>({ avatar: null, intro_video: null, cheat_proof: null })
const createPreview = reactive<Record<MediaKey, string>>({ avatar: '', intro_video: '', cheat_proof: '' })


function onPickCreateFile(key: MediaKey, file: any) {
  const raw: File = file.raw
  createFiles[key] = raw
  createPreview[key] = URL.createObjectURL(raw)
}

async function onCreate() {
  if (saveReview.value || creating.value || saving.value) return
  const revision = auth.sessionRevision
  await Promise.all([ensureLevels(), ensureGames(), ensureServiceItems()])
  if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:edit')) return
  Object.assign(createForm, defaultCreateForm())
  createDepositYuan.value = 0
  showCreatePassword.value = false
  createFiles.avatar = createFiles.intro_video = createFiles.cheat_proof = null
  createPreview.avatar = createPreview.intro_video = createPreview.cheat_proof = ''
  saveError.value = ''; saveUnknown.value = false
  createQualificationsRef.value?.reset()
  createBaseline.value = createSignature()
  createVisible.value = true
  createFormRef.value?.clearValidate()
}

async function onCreateSave() {
  if (!createFormRef.value || creating.value || saveReview.value || saveUnknown.value || !auth.hasPerm('escort:edit')) return
  const revision = auth.sessionRevision
  creating.value = true
  try {
      const valid = await createFormRef.value.validate().catch(() => false)
      if (!valid || escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:edit')) return
      if (!createQualificationsRef.value?.validate()) return
      saveError.value = ''
      const fd = new FormData()
      fd.append('qualifications', JSON.stringify(createForm.qualifications || []))
      fd.append('username', createForm.username.trim())
      fd.append('password', createForm.password)
      fd.append('display_name', createForm.display_name.trim())
      fd.append('gender', createForm.gender)
      fd.append('phone', createForm.phone.trim())
      fd.append('city', createForm.city.trim())
      fd.append('deposit_required', String(xaCoinToAmount(createDepositYuan.value)))
      fd.append('deposit_mode', createForm.deposit_mode)
      if (createForm.level != null) fd.append('level', String(createForm.level))
      // 可接游戏：multipart 数组同名多次 append，后端 .set() 建立 n:n 关联
      ;(createForm.game_categories as number[]).forEach((id) => fd.append('game_categories', String(id)))
      ;(createForm.service_items as number[]).forEach((id) => fd.append('service_items', String(id)))
      ;(['avatar', 'intro_video', 'cheat_proof'] as MediaKey[]).forEach((k) => {
        if (createFiles[k]) fd.append(k, createFiles[k] as File)
      })
      beginSaveReview(null, createForm.username.trim())
      await request({ url: '/escorts/', method: 'post', data: fd, silent: true })
      if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:edit')) return
      clearSaveReview()
      createBaseline.value = null
      successNotice('陪玩账号创建成功')
      createVisible.value = false
      createForm.password = ''
      tableRef.value.load()
  } catch (error: any) {
    if (!escortPageDisposed && revision === auth.sessionRevision) handleSaveFailure(error)
  } finally {
    if (revision === auth.sessionRevision) creating.value = false
  }
}

const editBaseline = ref<string | null>(null)
const createBaseline = ref<string | null>(null)
const editFields = ['display_name', 'gender', 'city', 'service_area', 'recommendation_slot', 'bio', 'rank_tier', 'win_rate', 'qualifications', 'game_categories', 'service_items', 'deposit_mode', 'status']
function editSignature() {
  return JSON.stringify({ fields: editFields.map(key => editForm[key] ?? null), hourly: editHourlyCoins.value, files: (Object.keys(editFiles) as MediaKey[]).map(key => Boolean(editFiles[key])) })
}
function createSignature() {
  return JSON.stringify({ fields: createForm, files: (Object.keys(createFiles) as MediaKey[]).map(key => Boolean(createFiles[key])) })
}
const editDirty = computed(() => editBaseline.value !== null && editSignature() !== editBaseline.value)
const createDirty = computed(() => createBaseline.value !== null && createSignature() !== createBaseline.value)
let discardPromptOpen = false
async function confirmDiscard(dirty: boolean) {
  if (!dirty || saveUnknown.value || reviewViewing.value || !auth.hasPerm('escort:edit')) return true
  if (discardPromptOpen) return false
  discardPromptOpen = true
  try {
    await confirmAction('关闭后未保存的陪玩资料将丢失。', '放弃修改？', {
      type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑',
    })
    return true
  } catch { return false } finally { discardPromptOpen = false }
}
async function closeEditDialog(done?: () => void) {
  if (saving.value || !dialogVisible.value) return
  const revision = auth.sessionRevision
  if (!await confirmDiscard(editDirty.value) || escortPageDisposed || revision !== auth.sessionRevision || !dialogVisible.value) return
  editBaseline.value = null
  reviewViewing.value = false
  if (done) done(); else dialogVisible.value = false
}
async function closeCreateDialog(done?: () => void) {
  if (creating.value || !createVisible.value) return
  const revision = auth.sessionRevision
  if (!await confirmDiscard(createDirty.value) || escortPageDisposed || revision !== auth.sessionRevision || !createVisible.value) return
  createBaseline.value = null
  createForm.password = ''
  if (done) done(); else createVisible.value = false
}
onBeforeRouteLeave(async () => {
  if (disposing.value) { showNotice('正在处理奖罚，请稍候'); return false }
  if (saving.value || creating.value) { showNotice('正在保存，请稍候'); return false }
  if (dialogVisible.value) return confirmDiscard(editDirty.value)
  if (createVisible.value) return confirmDiscard(createDirty.value)
  return true
})
function onEscortBeforeUnload(event: BeforeUnloadEvent) {
  if (auth.hasPerm('escort:edit') && ((dialogVisible.value && (saving.value || (editDirty.value && !saveUnknown.value && !reviewViewing.value))) || (createVisible.value && (creating.value || (createDirty.value && !saveUnknown.value))))) {
    event.preventDefault(); event.returnValue = ''
  }
}
onMounted(() => window.addEventListener('beforeunload', onEscortBeforeUnload))
onUnmounted(() => { window.removeEventListener('beforeunload', onEscortBeforeUnload); createForm.password = '' })
watch(() => auth.sessionRevision, () => {
  dialogVisible.value = false; createVisible.value = false
  editBaseline.value = null; createBaseline.value = null
  createForm.password = ''; saving.value = false; creating.value = false
  saveError.value = ''; saveUnknown.value = false; reviewViewing.value = false
}, { flush: 'sync' })

function saveReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_escort_save_review_${id}` : ''
}
function restoreSaveReview() {
  const key = saveReviewKey()
  if (key === reviewOwnerKey) return
  reviewOwnerKey = key; saveReview.value = null; reviewChecked.value = false; reviewNotice.value = ''; reviewLoading.value = false
  if (!key) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if ((marker?.id === null && typeof marker.username === 'string' && marker.username.length > 0 && marker.username.length <= 150) ||
        (Number.isSafeInteger(marker?.id) && marker.id > 0 && marker.username === '')) saveReview.value = marker
  } catch { /* Keep this page usable if storage is unavailable. */ }
}
function beginSaveReview(id: number | null, username: string) {
  saveReview.value = { id, username }; reviewChecked.value = false; reviewNotice.value = ''
  const key = saveReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id, username })) } catch { /* The current page still retains the marker. */ }
}
function clearSaveReview() {
  const key = saveReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  saveReview.value = null; reviewChecked.value = false; reviewNotice.value = ''
}
function handleSaveFailure(error: any) {
  if (!saveReview.value) {
    saveError.value = '未能准备保存请求，请检查资料后重试。'
    return
  }
  const body = error?.response?.data || error, status = error?.response?.status
  const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
  if (rejected) {
    clearSaveReview()
    saveError.value = `保存未完成：${typeof body?.msg === 'string' && body.msg ? body.msg : '请求被拒绝，请核对输入和权限'}`
  } else {
    saveUnknown.value = true
    saveError.value = '保存结果未确认，请关闭并读取最新档案核对。'
  }
}
async function checkSaveReview() {
  const marker = saveReview.value, revision = auth.sessionRevision
  if (!marker || reviewLoading.value || !auth.hasPerm('escort:edit')) return
  reviewLoading.value = true; reviewChecked.value = false; reviewNotice.value = ''
  try {
    let row: any
    if (marker.id) row = (await escortApi.retrieve(marker.id, true)).data
    else {
      const result = await request<any>({ url: '/escorts/', method: 'get', params: { account_username: marker.username, page: 1, page_size: 1 }, silent: true })
      row = result.data?.list?.find((item: any) => item.username === marker.username)
    }
    if (escortPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker) return
    if (!row) {
      reviewChecked.value = true
      reviewNotice.value = '未找到该账号。请求可能仍在处理，请确认处理情况后再解除限制。'
    } else if (await onEdit(row, true)) {
      if (escortPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker) return
      reviewChecked.value = true
      reviewNotice.value = '已读取最新档案；请核对实际内容和请求处理情况。'
    }
  } catch (error: any) {
    if (escortPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker) return
    if (marker.id && error?.response?.status === 404) {
      reviewChecked.value = true
      reviewNotice.value = '未找到该档案。请求可能仍在处理，请确认处理情况后再解除限制。'
    } else reviewNotice.value = '读取失败，请稍后重新读取。'
  } finally { if (revision === auth.sessionRevision) reviewLoading.value = false }
}
async function finishSaveReview() {
  const marker = saveReview.value, revision = auth.sessionRevision
  if (!marker || !reviewChecked.value || reviewLoading.value) return
  try {
    await confirmAction('请确认已核对档案和请求处理情况；请求可能仍在处理中。', '解除保存限制', {
      type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对',
    })
  } catch { return }
  if (!escortPageDisposed && revision === auth.sessionRevision && saveReview.value === marker && reviewChecked.value) { clearSaveReview(); saveUnknown.value = false }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreSaveReview, { immediate: true, flush: 'sync' })

const disposeVisible = ref(false)
const disposing = ref(false)
const current = ref<any>(null)
const disposeTarget = ref<any>(null)
const disposeType = ref<'REWARD' | 'PENALTY'>('REWARD')
const disposeYuan = ref<number>(0)
const disposeReason = ref('')
type DisposeReview = { profileId: number; accountId: number | null; name: string; payload: { dispose_type: 'REWARD' | 'PENALTY'; amount: number; reason: string; client_request_id: string } }
const disposeReview = ref<DisposeReview | null>(null)
let disposeReviewOwner = ''

function disposeReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_escort_dispose_review_${id}` : ''
}
function restoreDisposeReview() {
  const key = disposeReviewKey()
  if (key === disposeReviewOwner) return
  disposeReviewOwner = key; disposeReview.value = null
  if (!key) return
  try {
    const row = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(row?.profileId) && row.profileId > 0 && typeof row.name === 'string'
      && (row.accountId === null || (Number.isSafeInteger(row.accountId) && row.accountId > 0))
      && ['REWARD', 'PENALTY'].includes(row.payload?.dispose_type)
      && Number.isSafeInteger(row.payload?.amount) && row.payload.amount > 0 && row.payload.amount <= 2147483647
      && typeof row.payload?.reason === 'string' && row.payload.reason.length <= 255
      && typeof row.payload?.client_request_id === 'string' && row.payload.client_request_id.length > 0 && row.payload.client_request_id.length <= 64) disposeReview.value = row
  } catch { /* In-page guard remains if storage is unavailable. */ }
}
function beginDisposeReview(marker: DisposeReview) {
  disposeReview.value = marker
  const key = disposeReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify(marker)) } catch { /* In-page guard remains. */ }
}
function clearDisposeReview() {
  const key = disposeReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  disposeReview.value = null
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreDisposeReview, { immediate: true, flush: 'sync' })
watch(() => auth.sessionRevision, () => { disposeVisible.value = false; disposeTarget.value = null; disposing.value = false }, { flush: 'sync' })

function onDispose(row: any) {
  if (disposing.value || disposeReview.value || !auth.hasPerm('escort:dispose')) return
  disposeTarget.value = row
  disposeType.value = 'REWARD'
  disposeYuan.value = 0
  disposeReason.value = ''
  disposeVisible.value = true
}
function closeDispose(done?: () => void) {
  if (disposing.value) return
  if (done) done(); else disposeVisible.value = false
}

async function onDisposeSave() {
  if (disposing.value || disposeReview.value || !disposeTarget.value || !auth.hasPerm('escort:dispose')) return
  const amount = xaCoinToAmount(disposeYuan.value)
  if (!Number.isSafeInteger(amount) || amount <= 0 || amount > 2147483647) {
    showNotice('金额须为 0.1 币的整数倍，且大于 0')
    return
  }
  const row = disposeTarget.value, revision = auth.sessionRevision
  const marker: DisposeReview = { profileId: row.id, accountId: row.user || null, name: `${row.display_name || row.username || '陪玩'} · ${row.escort_no || '编号待核对'} · ${row.username || '账号待核对'}`,
    payload: { dispose_type: disposeType.value, amount, reason: disposeReason.value.trim(), client_request_id: crypto.randomUUID() } }
  disposing.value = true
  try {
    try { await confirmAction(`陪玩：${marker.name}\n类型：${marker.payload.dispose_type === 'REWARD' ? '奖励' : '罚款'}\n金额：${amountToXaCoin(amount)} 币\n原因：${marker.payload.reason || '未填写'}`, '确认奖罚', { type: 'warning', customClass: 'es-dispose-confirm', confirmButtonText: '确认记账', cancelButtonText: '返回修改' }) } catch { return }
    if (escortPageDisposed || revision !== auth.sessionRevision || !disposeVisible.value || !auth.hasPerm('escort:dispose')) return
    beginDisposeReview(marker)
    await executeDispose(disposeReview.value!, revision)
  } finally {
    if (revision === auth.sessionRevision) disposing.value = false
  }
}
async function executeDispose(marker: DisposeReview, revision: number) {
  try {
    await escortApi.action(marker.profileId, 'dispose', marker.payload, true)
    if (escortPageDisposed || revision !== auth.sessionRevision || disposeReview.value !== marker) return
    clearDisposeReview(); disposeVisible.value = false
    successNotice('奖罚已记账'); tableRef.value?.load()
  } catch (error: any) {
    if (escortPageDisposed || revision !== auth.sessionRevision || disposeReview.value !== marker || error?.code === 'SESSION_CHANGED') return
    const body = error?.response?.data || error, status = error?.response?.status
    if (body?.errorCode === 'FUND_ALREADY_PROCESSED' && body?.fundResult?.data?.record?.id) {
      clearDisposeReview(); disposeVisible.value = false
      successNotice('已恢复原奖罚回执，未重复记账'); tableRef.value?.load()
    } else if ((!status || status < 500) && status !== 408 && body?.code === 400) {
      clearDisposeReview(); showNotice(body?.msg || '奖罚未记账，请核对输入')
    } else if (status === 403 || status === 404) {
      clearDisposeReview(); showNotice(body?.msg || '奖罚未记账，请检查权限或陪玩账号')
    } else {
      showNotice('奖罚结果未确认，请核对原请求')
    }
  }
}
async function recoverDispose() {
  const marker = disposeReview.value, revision = auth.sessionRevision
  if (!marker || disposing.value || !auth.hasPerm('escort:dispose')) return
  disposing.value = true
  try {
    try { await confirmAction('若原请求未执行，将按原对象、类型和金额记账；已完成则恢复回执，不重复记账。', '继续原请求', { type: 'warning', confirmButtonText: '确认继续', cancelButtonText: '返回' }) } catch { return }
    if (escortPageDisposed || revision !== auth.sessionRevision || disposeReview.value !== marker || !auth.hasPerm('escort:dispose')) return
    await executeDispose(marker, revision)
  } finally { if (revision === auth.sessionRevision) disposing.value = false }
}
function openDisposeRecords() {
  if (disposeReview.value?.accountId && auth.hasPerm('dispose:view')) void router.push({ path: '/dispose-records', query: { account_id: String(disposeReview.value.accountId) } })
}
async function finishDisposeReview() {
  const marker = disposeReview.value, revision = auth.sessionRevision
  if (!marker || disposing.value) return
  try { await confirmAction('请先核对奖罚记录和请求处理情况；原请求可能仍在处理。', '解除奖罚限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对' }) } catch { return }
  if (!escortPageDisposed && revision === auth.sessionRevision && disposeReview.value === marker) clearDisposeReview()
}
</script>
<style scoped lang="scss">
.es-form { display:grid; grid-template-columns:minmax(0,1fr); gap:16px; border:0; padding:0; margin:0; min-width:0; }
.es-grid { min-width:0; display:grid; grid-template-columns:repeat(24,minmax(0,1fr)); gap:16px; }
.es-col { min-width:0; }
.es-field { display:grid; gap:8px; min-width:0; margin-bottom:12px; font-size:13px; }
.es-form-section { min-width:0; }
.es-field-error { min-height:20px; color:var(--destructive); font-size:12px; }
.es-radio-group,.es-radio { display:flex; align-items:center; gap:12px; }
.es-password,.es-search { display:flex; align-items:center; gap:8px; min-width:0; }
.es-search { width:200px; }
.es-action-cell { position:sticky; right:0; z-index:1; background:var(--card); }
.es-table { min-width:1900px; }
.es-dialog-footer { display:grid; gap:12px; }
:global(.es-shadcn-dialog) { grid-template-columns:minmax(0,1fr); width:min(var(--es-dialog-width,800px),calc(100vw - 24px)); max-width:min(var(--es-dialog-width,800px),calc(100vw - 24px)); max-height:calc(100dvh - 24px); overflow-y:auto; }
@media(max-width:720px){.es-grid{grid-template-columns:minmax(0,1fr)}.es-grid>.es-col{grid-column:1 / -1!important;}.es-search{width:100%;}}


.es-mobile-list { display: grid; gap: 12px; }
.es-mobile-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.es-mobile-head { display: flex; align-items: center; gap: 10px; }
.es-mobile-person { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.es-mobile-person strong, .es-mobile-person span { overflow-wrap: anywhere; }
.es-mobile-person strong { color: var(--foreground); font-size: 15px; }
.es-mobile-person span { color: var(--muted-foreground); font-size: 12px; }
.es-mobile-facts { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 10px; color: var(--muted-foreground); font-size: 12px; }
.es-status-help { width: var(--control-height-sm); height: var(--control-height-sm); border: 0; border-radius: 50%; background: transparent; color: var(--primary); font-size: 15px; cursor: pointer; }
.es-status-help:hover { background: var(--accent); }
.es-status-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.es-mobile-group { display: grid; gap: 6px; padding: 10px 0; border-top: 1px solid var(--border); }
.es-mobile-group > span { color: var(--muted-foreground); font-size: 12px; }
.es-mobile-values { display: flex; flex-wrap: wrap; gap: 8px 20px; padding: 10px 0; margin: 0; border-top: 1px solid var(--border); }
.es-mobile-values > div { display: flex; gap: 8px; }
.es-mobile-values dt { color: var(--muted-foreground); }
.es-mobile-values dd { margin: 0; }
.es-mobile-actions { display: flex; gap: 8px; margin-top: 4px; }
.es-more { margin-left: 14px; vertical-align: middle; }
.es-person { display:flex; align-items:center; gap:10px; }
.es-secondary { display:block; color:var(--muted-foreground); font-size:12px; line-height:1.7; }
.es-games { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:4px; }
.es-draft-status { margin: 0; color: var(--muted-foreground); font-size: 12px; }

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}

.es-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.es-page :deep(.page-container) {
  padding: 0;
}

.es-reward {
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.es-penalty {
  color: var(--destructive);
  font-variant-numeric: tabular-nums;
}


.es-uploader {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  cursor: pointer;
  overflow: hidden;
  color: var(--muted-foreground);
  transition: border-color 0.2s;
  background: var(--muted);
}
.es-media-upload { width: 100%; }
.es-uploader:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.es-media-icon {
  font-size: 24px;
}
.es-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
