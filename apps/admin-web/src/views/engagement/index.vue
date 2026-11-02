<template>
<div class="operations">
<div class="ap-page-head">
<p class="ap-page-intro">管理积分兑换、奖品交付与会员权益，配置前台活动和运营内容。</p>
<Button size="xs" @click="load" :disabled="(loading)">刷新</Button>
</div>
<nav class="operations-sections" aria-label="积分礼品功能分类">
<button v-for="section in visibleSections" :key="section.key" type="button" :aria-pressed="sectionKey === section.key" :class="{ 'is-active': sectionKey === section.key }" @click="selectSection(section.key)">
<strong >{{ section.label }}</strong>
<span >{{ section.description }}</span>
</button>
</nav>
<section class="operations-workspace" :aria-label="activeSection.label">
<Tabs v-model="tab">
<TabsList class="engagement-tabs">
<TabsTrigger v-for="item in visibleTabs" :key="item.key" :value="item.key">{{ item.label }}</TabsTrigger>
</TabsList>
</Tabs>
<div v-if="tab === 'kook'" class="external-form">
<Alert >
<AlertDescription>
<strong>{{ kook.connected ? 'KOOK自动积分运行中' : 'KOOK自动积分连接状态待核实' }}</strong>
</AlertDescription>
</Alert>
<p >{{kook.rules}}</p>
<p >语音频道：{{kook.channel_count}} 个；已绑定：{{kook.binding_count}} 人，其中陪玩 {{kook.provider_count}} 人。</p>
<p >状态：{{kook.status}}；最近心跳：{{kook.heartbeat_at || '暂无'}}</p>
<p >会员在积分页获取本人绑定码，在KOOK签到大厅发送绑定码，随后发送“签到”。自动计分开启后，人工KOOK记分关闭。</p>
</div>
<Alert v-if="loadFailed" variant="destructive">
<AlertDescription>
<strong>{{ "数据加载失败，请点击刷新重试" }}</strong>
</AlertDescription>
</Alert>
<div v-if="tab === 'external'" class="external-form">
<Alert >
<AlertDescription>
<strong>{{ "录入已核对的真实记录。同一老板、同一天、同一来源只入账一次。小程序签到不冒充 KOOK 签到。" }}</strong>
</AlertDescription>
</Alert>
<fieldset >
<div  class="engagement-field ">
<span>{{ "老板业务账户 ID" }}</span>
<NumberField :step-snapping="false" v-model="external.account_id" :min="1" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="老板业务账户 ID" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "积分来源" }}</span>
<NativeSelect v-model="external.kind" aria-label="积分来源">
<NativeSelectOption value="KOOK_CHECKIN">{{ "KOOK 每日签到（5 分）" }}</NativeSelectOption>
<NativeSelectOption value="KOOK_ONLINE">{{ "KOOK 在线时长（每 2 小时 1 分）" }}</NativeSelectOption>
</NativeSelect>
</div>
<div  class="engagement-field ">
<span>{{ "日期" }}</span>
<Input v-model="external.day" type="date" aria-label="日期" />
</div>
<div v-if="external.kind === 'KOOK_ONLINE'" class="engagement-field ">
<span>{{ "当天在线秒数" }}</span>
<NumberField :step-snapping="false" v-model="external.online_seconds" :min="1" :max="86400" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="当天在线秒数" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "来源记录编号" }}</span>
<Input v-model="external.source_id" maxlength="100" aria-label="来源记录编号" />
</div>
<Button size="xs" @click="recordExternal" :disabled="(saving)">核对并入账</Button>
</fieldset>
</div>
<fieldset v-else-if="tab === 'settings'" class="external-form" :disabled="!canEdit || saving || loading || loadFailed">
<Alert >
<AlertDescription>
<strong>{{ "仅消费积分，充值本身不加分；已完成订单的折后实付累计 VIP，取消和退款不计入，VIP 折扣与折扣券取优不叠加。" }}</strong>
</AlertDescription>
</Alert>
<div  class="engagement-field ">
<span>{{ "消费积分" }}</span>
<Switch v-model="settings.points_enabled" aria-label="消费积分" />
</div>
<div  class="engagement-field ">
<span>{{ "生日消费倍率" }}</span>
<NumberField :step-snapping="false" v-model="settings.birthday_multiplier" :min="1" :max="10" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="生日消费倍率" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "VIP 自动升级" }}</span>
<Switch v-model="settings.vip_enabled" aria-label="VIP 自动升级" />
</div>
<div  class="engagement-field ">
<span>{{ "当前赛季编号" }}</span>
<Input v-model="settings.season_code" placeholder="例如 S8；更换编号后自动发放新赛季券" aria-label="当前赛季编号" />
</div>
<div  class="engagement-field ">
<span>{{ "赛季截止时间" }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(settings.season_ends_at)" @update:model-value="value => { settings.season_ends_at = dateIso(value) }" aria-label="赛季截止时间" />
</div>
<Button size="xs" @click="saveSettings" :disabled="(saving)">保存运营设置</Button>
</fieldset>
<template v-else-if="tab !== 'kook'">
<div class="toolbar">
<div class="toolbar-title">
<h2 >{{ labels[tab] }}</h2>
<span >共 {{total}} 条记录</span>
</div>
<Button size="xs" v-if="canEdit && ['rewards','content','lotteries','packages','giftBenefits'].includes(tab)" @click="edit()">新增{{labels[tab]}}</Button>
</div>
<div v-if="tab === 'claims'" class="claim-filters">
<Input v-model="claimSearch" placeholder="权益、TAG、账号或记录 ID" aria-label="搜索权益交付记录" @keyup.enter="load" /><Button v-if="claimSearch" variant="outline" size="xs" aria-label="清空权益搜索" @click="claimSearch='';load()">清空</Button>
<NativeSelect v-model="claimFilterAccountType" aria-label="申请人身份" @change="load" >
<NativeSelectOption value="">{{ "全部身份" }}</NativeSelectOption>
<NativeSelectOption value="BOSS">{{ "老板" }}</NativeSelectOption>
<NativeSelectOption value="PROVIDER">{{ "陪玩" }}</NativeSelectOption>
</NativeSelect>
<NativeSelect v-model="claimFilterKind" aria-label="权益类型" @change="load" >
<NativeSelectOption value="">{{ "全部类型" }}</NativeSelectOption>
<NativeSelectOption v-for="(label,key) in rewardKinds" :key="key" :value="key">{{ label }}</NativeSelectOption>
</NativeSelect>
<NativeSelect v-model="claimFilterStatus" aria-label="交付状态" @change="load" >
<NativeSelectOption value="">{{ "全部状态" }}</NativeSelectOption>
<NativeSelectOption value="PENDING">{{ "待处理" }}</NativeSelectOption>
<NativeSelectOption value="FULFILLING">{{ "交付中" }}</NativeSelectOption>
<NativeSelectOption value="COMPLETED">{{ "已完成" }}</NativeSelectOption>
<NativeSelectOption value="CANCELLED">{{ "已取消" }}</NativeSelectOption>
</NativeSelect>
<NativeSelect v-model="claimFilterSource" aria-label="权益来源" @change="load" >
<NativeSelectOption value="">{{ "全部来源" }}</NativeSelectOption>
<NativeSelectOption value="GIFT">{{ "赠礼权益" }}</NativeSelectOption>
<NativeSelectOption value="VIP">{{ "会员权益" }}</NativeSelectOption>
<NativeSelectOption value="REDEMPTION">{{ "积分兑换" }}</NativeSelectOption>
<NativeSelectOption value="LOTTERY">{{ "转盘奖品" }}</NativeSelectOption>
</NativeSelect>
<label class="engagement-check">
<Checkbox v-model="claimTagReady" @change="changeTagReadyFilter" @update:model-value="changeTagReadyFilter" />TAG 待审核</label>
<label class="engagement-check">
<Checkbox v-model="claimNeedsTagReview" @change="changeTagReviewFilter" @update:model-value="changeTagReviewFilter" />TAG 待复核</label>
<Button @click="load" size="xs">搜索</Button>
</div>
<div v-if="tab === 'claims'" class="claim-mobile-list" >
<article v-for="row in rows" :key="row.id" class="claim-mobile-card">
<div class="claim-card-top">
<strong >{{row.name_snapshot}}</strong>
<Badge  variant="outline">{{row.status_label}}</Badge>
</div>
<div >{{claimAccountType(row.account_type)}} · {{row.account_name || row.account}} · {{claimSource(row.source)}}</div>
<div v-if="row.kind_snapshot === 'TAG'" class="claim-tag-preview">
<span >TAG：{{row.details?.tag || '待填写'}}</span>
<Badge  variant="outline">{{tagReviewLabel(row)}}</Badge>
</div>
<div class="claim-card-meta">记录 #{{row.id}}<span v-if="row.source_order"> · 订单 #{{row.source_order}}</span>
</div>
<Button @click="edit(row)" size="xs" :disabled="(!!claimOpeningId) || (claimOpeningId === row.id)">{{['COMPLETED','CANCELLED'].includes(row.status) ? '查看详情' : '处理交付'}}</Button>
</article>
<p v-if="!loading && !rows.length" class="empty-state">{{ "暂无交付记录" }}</p>
</div>
<div :class="{ 'claim-desktop-table': tab === 'claims' }" class="engagement-table" :aria-busy="loading">
<Table>
<TableHeader>
<TableRow>
<TableHead >{{ "ID" }}</TableHead>
<template v-if="tab === 'namings'">
<TableHead >{{ "收礼陪玩" }}</TableHead>
<TableHead >{{ "赠礼老板" }}</TableHead>
<TableHead >{{ "冠名文字" }}</TableHead>
<TableHead >{{ "累计到期时间" }}</TableHead>
</template>
<TableHead v-if="tab === 'rewards'">{{ "礼物" }}</TableHead>
<TableHead v-else-if="tab !== 'namings'">{{ tab === 'feed' ? '播报内容' : '名称' }}</TableHead>
<TableHead v-if="tab === 'rewards'">{{ "兑换积分" }}</TableHead>
<TableHead v-if="tab === 'rewards'">{{ "库存" }}</TableHead>
<TableHead v-if="tab === 'rewards'">{{ "交付方式" }}</TableHead>
<TableHead v-if="tab === 'claims'">{{ "申请人 / 状态" }}</TableHead>
<TableHead v-if="tab === 'claims'">{{ "TAG / 审核" }}</TableHead>
<TableHead v-if="tab === 'claims'">{{ "来源" }}</TableHead>
<TableHead v-if="tab === 'lotteries'">{{ "每次积分 / 概率合计" }}</TableHead>
<TableHead v-if="tab === 'lotteries'">{{ "连抽与保底" }}</TableHead>
<TableHead v-if="tab === 'rules'">{{ "模板" }}</TableHead>
<TableHead v-if="tab === 'tiers'">{{ "累计消费门槛" }}</TableHead>
<TableHead v-if="tab === 'packages'">{{ "赠送 / 积分倍率" }}</TableHead>
<TableHead v-if="tab !== 'claims' && tab !== 'packages' && tab !== 'tiers'">{{ "状态" }}</TableHead>
<TableHead >{{ "操作" }}</TableHead>
</TableRow>
</TableHeader>
<TableBody>
<TableRow v-for="(row,index) in rows" :key="row.id || index">
<TableCell >{{ row["id"] }}</TableCell>
<template v-if="tab === 'namings'">
<TableCell >{{ row["provider_name"] }}</TableCell>
<TableCell >{{ row["sponsor_name"] }}</TableCell>
<TableCell >{{ row["title"] }}</TableCell>
<TableCell >{{ row["expires_at"] }}</TableCell>
</template>
<TableCell v-if="tab === 'rewards'">
<div class="gift-name">
<img v-if="row.image_url" :src="media(row.image_url)" alt="" />
<span >{{row.name}}</span>
</div>
</TableCell>
<TableCell v-else-if="tab !== 'namings'">{{row.name || row.title || row.name_snapshot || kindLabel(row.kind) || (formatRmb(row.amount) + ' 元档位')}}<span v-if="tab === 'feed'">{{row.content}}</span>
</TableCell>
<TableCell v-if="tab === 'rewards'">
<span class="numeric">{{Number(row.point_cost).toLocaleString('zh-CN')}}</span>
</TableCell>
<TableCell v-if="tab === 'rewards'">{{row.stock === null ? '不限' : row.stock}}</TableCell>
<TableCell v-if="tab === 'rewards'">{{ row["kind_label"] }}</TableCell>
<TableCell v-if="tab === 'claims'">{{claimAccountType(row.account_type)}} · {{row.account_name || row.account}} · {{row.status_label}}</TableCell>
<TableCell v-if="tab === 'claims'">
<span v-if="row.kind_snapshot === 'TAG'">{{row.details?.tag || '待填写'}} · {{tagReviewLabel(row)}}</span>
<span v-else>—</span>
</TableCell>
<TableCell v-if="tab === 'claims'">{{claimSource(row.source)}}<span v-if="row.source_order"> · 订单 #{{row.source_order}}</span>
</TableCell>
<TableCell v-if="tab === 'lotteries'">{{row.point_cost}} / {{row.prizes.reduce((n:number,p:any)=>n+p.probability,0)}} 万分点</TableCell>
<TableCell v-if="tab === 'lotteries'">
<div >十连 {{row.point_cost * 10}} 积分 · 至少1份非积分奖</div>
<span class="hint" v-if="row.pity?.enabled">68体验单 · 最多240抽必得</span>
<span class="hint" v-else>未配置68体验单保底奖</span>
</TableCell>
<TableCell v-if="tab === 'rules'">{{ row["template"] }}</TableCell>
<TableCell v-if="tab === 'tiers'">¥{{formatRmb(row.consumption_threshold)}} · {{row.discount_rate / 10}} 折</TableCell>
<TableCell v-if="tab === 'packages'">¥{{formatRmb(row.gift_amount)}} / {{row.points_rate / POINTS_RATE_SCALE}} 倍</TableCell>
<TableCell v-if="tab !== 'claims' && tab !== 'packages' && tab !== 'tiers'">
<Badge  variant="outline">{{active(row) ? '启用' : '停用'}}</Badge>
</TableCell>
<TableCell >
<Button size="xs" v-if="canEdit || tab === 'claims'" @click="edit(row)" variant="link" :disabled="(tab === 'claims' && !!claimOpeningId) || (tab === 'claims' && claimOpeningId === row.id)">{{tab === 'claims' ? (['COMPLETED','CANCELLED'].includes(row.status) ? '查看详情' : '处理交付') : '配置'}}</Button>
</TableCell>
</TableRow>
<TableRow v-if="!(rows).length">
<TableCell :colspan="20" class="empty-state">暂无记录</TableCell>
</TableRow>
</TableBody>
</Table>
</div>
<div v-if="total > pageSize" class="pagination-wrap">
<Pagination v-model:page="page" :items-per-page="pageSize" :total="total" :sibling-count="1" @update:page="fetchRows">
<PaginationContent v-slot="{ items }">
<PaginationPrevious size="xs" aria-label="上一页">上一页</PaginationPrevious>
<template v-for="(item,index) in items" :key="index">
<PaginationItem v-if="item.type === 'page'" :value="item.value" :is-active="item.value === page">{{ item.value }}</PaginationItem>
<PaginationEllipsis v-else />
</template>
<PaginationNext size="xs" aria-label="下一页">下一页</PaginationNext>
</PaginationContent>
</Pagination>
</div>
</template>
</section>
<Dialog v-model:open="dialog">
<DialogContent class="engagement-dialog" @interact-outside.prevent>
<DialogHeader>
<DialogTitle>{{ (form.id ? '配置' : '新增') + labels[tab] }}</DialogTitle>
<DialogDescription>核对积分、奖品与权益配置后保存。</DialogDescription>
</DialogHeader>
<fieldset :disabled="saving" class="engagement-form">
<template v-if="tab === 'rewards'">
<div  class="engagement-field ">
<span>{{ "名称" }}</span>
<Input v-model="form.name" maxlength="100" aria-label="名称" />
</div>
<div  class="engagement-field ">
<span>{{ "商品标识" }}</span>
<Input v-model="form.code" :disabled="!!form.id" placeholder="例如 gift-mooncake" aria-label="商品标识" />
</div>
<div  class="engagement-field ">
<span>{{ "VIP补差类别" }}</span>
<NativeSelect v-model="form.vip_quota_kind" aria-label="VIP补差类别">
<NativeSelectOption value="">{{ "按权益项目（TAG和冠名自动归类）" }}</NativeSelectOption>
<NativeSelectOption value="FRIEND">{{ "好友位：各档总额补差" }}</NativeSelectOption>
<NativeSelectOption value="BIRTHDAY_GIFT">{{ "生日礼物：VIP4起每年生日交付一次" }}</NativeSelectOption>
</NativeSelect>
</div>
<div  class="engagement-field ">
<span>{{ "交付方式" }}</span>
<NativeSelect v-model="form.kind" aria-label="交付方式">
<NativeSelectOption v-for="(label,key) in rewardKinds" :key="key" :value="key">{{ label }}</NativeSelectOption>
</NativeSelect>
</div>
<div  class="engagement-field ap-field--full">
<span>{{ "商品图片" }}</span>
<Input v-model="form.image_url" aria-label="商品图片" />
<input type="file" accept="image/png,image/jpeg,image/webp" @change="upload" />
</div>
<div  class="engagement-field ap-field--full">
<span>{{ "说明" }}</span>
<Textarea v-model="form.description" :rows="3" aria-label="说明" />
</div>
<div  class="engagement-field ">
<span>{{ "适用会员" }}</span>
<NativeSelect v-model="form.audience" aria-label="适用会员">
<NativeSelectOption value="BOSS">{{ "老板" }}</NativeSelectOption>
<NativeSelectOption value="PROVIDER">{{ "陪玩" }}</NativeSelectOption>
<NativeSelectOption value="ALL">{{ "全部会员" }}</NativeSelectOption>
</NativeSelect>
</div>
<div  class="engagement-field ">
<span>{{ "兑换积分" }}</span>
<NumberField :step-snapping="false" v-model="form.point_cost" :min="0" :step="1" :format-options="{ minimumFractionDigits: 0, maximumFractionDigits: 0 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="兑换积分" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "在积分商城展示" }}</span>
<Switch v-model="form.in_shop" aria-label="在积分商城展示" />
</div>
<div  class="engagement-field ">
<span>{{ "库存" }}</span>
<Switch v-model="unlimited" aria-label="库存" />
<span>{{ "不限库存" }}</span>
<NumberField :step-snapping="false" v-if="!unlimited" v-model="form.stock" :min="0" :step="1" :format-options="{ minimumFractionDigits: 0, maximumFractionDigits: 0 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="库存" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div v-if="form.kind === 'POINTS'" class="engagement-field ">
<span>{{ "奖励积分" }}</span>
<NumberField :step-snapping="false" v-model="form.point_value" :min="1" :step="1" :format-options="{ minimumFractionDigits: 0, maximumFractionDigits: 0 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="奖励积分" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div v-if="form.kind === 'BALANCE'" class="engagement-field ">
<span>{{ "到账兴安币" }}</span>
<NumberField :step-snapping="false" v-model="balanceCoins" :min="0.1" :step="1" :format-options="{ minimumFractionDigits: 1, maximumFractionDigits: 1 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="到账兴安币" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div v-if="['TAG','NAMING'].includes(form.kind)" class="engagement-field ">
<span>{{ "有效天数" }}</span>
<NumberField :step-snapping="false" v-model="form.duration_days" :min="0" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="有效天数" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
<span class="hint">0 表示未设到期日</span>
</div>
<div v-if="['TAG','NAMING'].includes(form.kind)" class="engagement-field ">
<span>{{ "有效月数" }}</span>
<NumberField :step-snapping="false" v-model="form.duration_months" :min="0" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="有效月数" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
<span class="hint">自然月；设置后优先于天数</span>
</div>
<div v-if="form.kind === 'TAG'" class="engagement-field ">
<span>{{ "高亮标签" }}</span>
<Switch v-model="form.highlighted" aria-label="高亮标签" />
</div>
<div  class="engagement-field ">
<span>{{ "启用" }}</span>
<Switch v-model="form.is_active" aria-label="启用" />
</div>
</template>
<template v-if="tab === 'lotteries'">
<div  class="engagement-field ">
<span>{{ "活动名称" }}</span>
<Input v-model="form.name" aria-label="活动名称" />
</div>
<div  class="engagement-field ">
<span>{{ "每次消耗积分" }}</span>
<NumberField :step-snapping="false" v-model="form.point_cost" :min="1" :step="1" :format-options="{ minimumFractionDigits: 0, maximumFractionDigits: 0 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="每次消耗积分" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<Alert class="ap-field--full">
<AlertDescription>
<strong>{{ "十连至少1份非积分奖，消耗10倍积分" }}</strong>
<p>{{ "前9抽若全为积分奖，第10抽从基础概率大于0的非积分奖中按原权重抽取。68体验单也算非积分奖，240抽保底优先。十连兜底会提高实际获得非积分奖的机会，基础概率公示保持不变。" }}</p>
</AlertDescription>
</Alert>
<Alert v-if="!hasTenGuaranteeCandidates" class="ap-field--full">
<AlertDescription>
<strong>{{ "十连暂不可用：请为至少1个非积分奖配置大于0的基础概率" }}</strong>
<p>{{ "概率为0的68体验单只参与240抽保底，不作为十连兜底候选。单抽仍按完整奖池校验。" }}</p>
</AlertDescription>
</Alert>
<Alert class="ap-field--full">
<AlertDescription>
<strong>{{ hasExperiencePity ? '68体验单：最多240抽必得，提前抽中立即重置' : '奖池尚未加入68体验单，未启用240抽保底' }}</strong>
<p>{{ "本活动单抽、十连共用240抽进度；十连在第240抽出奖后，剩余抽数继续累计。奖品须启用且库存充足。" }}</p>
</AlertDescription>
</Alert>
<template v-if="form.id">
<div  class="engagement-field ">
<span>{{ "每天次数上限" }}</span>
<NumberField :step-snapping="false" v-model="form.daily_limit" :min="0" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="每天次数上限" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
<span class="hint">0 不限</span>
</div>
<div  class="engagement-field ">
<span>{{ "活动开始" }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(form.starts_at)" @update:model-value="value => { form.starts_at = dateIso(value) }" aria-label="活动开始" />
</div>
<div  class="engagement-field ">
<span>{{ "活动结束" }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(form.ends_at)" @update:model-value="value => { form.ends_at = dateIso(value) }" aria-label="活动结束" />
</div>
<Alert >
<AlertDescription>
<strong>{{ '概率合计：'+ probabilityTotal +' / 10000；1 = 0.01%，100 = 1%。启用时必须恰好为 10000。' }}</strong>
</AlertDescription>
</Alert>
<div  class="engagement-table" :aria-busy="loading">
<Table>
<TableHeader>
<TableRow>
<TableHead >{{ "奖品" }}</TableHead>
<TableHead >{{ "中奖概率（万分点）" }}</TableHead>
<TableHead >
</TableHead>
</TableRow>
</TableHeader>
<TableBody>
<TableRow v-for="(row,index) in form.prizes" :key="row.id || index">
<TableCell >
<Select v-model="row.reward" @update:open="value => { if(value) rewardSearch = '' }"><SelectTrigger size="sm" ><SelectValue placeholder="选择权益" /></SelectTrigger><SelectContent><Input v-model="rewardSearch" aria-label="搜索权益" placeholder="搜索权益名称" @keydown.stop />
<SelectItem v-for="reward in searchRewards(rewards)" :key="reward.id" :value="reward.id">{{ reward.name }}</SelectItem>
</SelectContent></Select>
</TableCell>
<TableCell >
<NumberField :step-snapping="false" v-model="row.probability" :min="0" :max="10000" :step="1" :format-options="{ minimumFractionDigits: 0, maximumFractionDigits: 0 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput  />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</TableCell>
<TableCell >
<Button size="xs" @click="form.prizes.splice(index,1)" variant="link">移除</Button>
</TableCell>
</TableRow>
<TableRow v-if="!(form.prizes).length">
<TableCell :colspan="20" class="empty-state">暂无记录</TableCell>
</TableRow>
</TableBody>
</Table>
</div>
<Button size="xs" @click="form.prizes.push({reward:null,probability:0})">添加奖项</Button>
<div  class="engagement-field ">
<span>{{ "启用转盘" }}</span>
<Switch v-model="form.enabled" :disabled="probabilityTotal !== 10000" aria-label="启用转盘" />
</div>
</template>
<div  class="engagement-field ap-field--full">
<span>{{ "参与说明" }}</span>
<Textarea v-model="form.rules" :rows="4" aria-label="参与说明" />
</div>
</template>
<template v-if="tab === 'giftBenefits'">
<div  class="engagement-field ">
<span>{{ "规则名称" }}</span>
<Input v-model="form.name" aria-label="规则名称" />
</div>
<div  class="engagement-field ">
<span>{{ "附送权益" }}</span>
<Select v-model="form.reward" @update:open="value => { if(value) rewardSearch = '' }"><SelectTrigger size="sm" aria-label="附送权益"><SelectValue placeholder="选择权益" /></SelectTrigger><SelectContent><Input v-model="rewardSearch" aria-label="搜索权益" placeholder="搜索权益名称" @keydown.stop />
<SelectItem v-for="reward in searchRewards(rewards.filter(r=>['NAMING','TAG','SERVICE'].includes(r.kind)))" :key="reward.id" :value="reward.id">{{ reward.name }}</SelectItem>
</SelectContent></Select>
</div>
<div  class="engagement-field ">
<span>{{ "赠礼门槛（元）" }}</span>
<NumberField :step-snapping="false" v-model="thresholdYuan" :min="0.01" :step="1" :format-options="{ minimumFractionDigits: 2, maximumFractionDigits: 2 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="赠礼门槛（元）" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "包含等于门槛" }}</span>
<Switch v-model="form.inclusive" aria-label="包含等于门槛" />
</div>
<div  class="engagement-field ">
<span>{{ "累计活动金额" }}</span>
<Switch v-model="form.cumulative" aria-label="累计活动金额" />
<span class="hint">关闭时按本次赠送总额</span>
</div>
<div  class="engagement-field ">
<span>{{ "礼物分类 ID" }}</span>
<NumberField :step-snapping="false" v-model="form.category" :min="1" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="礼物分类 ID" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
<span class="hint">留空为所有礼物；分类 ID 见服务管理</span>
</div>
<div  class="engagement-field ">
<span>{{ "每人仅领一次" }}</span>
<Switch v-model="form.once_per_account" aria-label="每人仅领一次" />
</div>
<div  class="engagement-field ">
<span>{{ "开始时间" }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(form.starts_at)" @update:model-value="value => { form.starts_at = dateIso(value) }" aria-label="开始时间" />
</div>
<div  class="engagement-field ">
<span>{{ "结束时间" }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(form.ends_at)" @update:model-value="value => { form.ends_at = dateIso(value) }" aria-label="结束时间" />
</div>
<div  class="engagement-field ">
<span>{{ "启用规则" }}</span>
<Switch v-model="form.enabled" aria-label="启用规则" />
</div>
</template>
<template v-if="tab === 'claims'">
<Alert v-if="claimConflict" class="claim-feedback">
<AlertDescription>
<strong>{{ claimConflict }}</strong>
</AlertDescription>
</Alert>
<dl class="claim-details">
<div >
<dt>{{ "奖品" }}</dt>
<dd>{{form.name_snapshot}}</dd>
</div>
<div >
<dt>{{ "申请人" }}</dt>
<dd>{{claimAccountType(form.account_type)}} · {{form.account_name || form.account}}</dd>
</div>
<div >
<dt>{{ "当前状态" }}</dt>
<dd>{{form.status_label}}</dd>
</div>
<div v-if="['NAMING','TAG'].includes(form.kind_snapshot) || form.duration_days || form.duration_months">
<dt>{{ "权益时长" }}</dt>
<dd>{{claimTerms(form)}}</dd>
</div>
<div v-if="form.kind_snapshot === 'TAG'">
<dt>{{ "展示类型" }}</dt>
<dd>{{form.highlighted ? '高亮 TAG' : '普通 TAG'}}</dd>
</div>
<div >
<dt>{{ "来源" }}</dt>
<dd>{{claimSource(form.source)}}<span v-if="form.source_order"> · 订单 #{{form.source_order}}</span>
</dd>
</div>
<div v-if="form.delivery_note">
<dt>{{ "处理说明" }}</dt>
<dd>{{form.delivery_note}}</dd>
</div>
<div v-for="(value,key) in form.details" :key="key">
<dt>{{ detailLabels[String(key)] || String(key) }}</dt>
<dd>{{value}}</dd>
</div>
</dl>
<div v-if="!['COMPLETED','CANCELLED'].includes(form.status)" class="engagement-field ">
<span>{{ "处理结果" }}</span>
<NativeSelect v-model="claimStatus" aria-label="处理结果">
<NativeSelectOption value="FULFILLING" :disabled="form.kind_snapshot === 'TAG' && form.tag_revision_required">{{ "交付中" }}</NativeSelectOption>
<NativeSelectOption v-if="form.status === 'FULFILLING' || (form.status === 'PENDING' && form.kind_snapshot === 'TAG')" value="PENDING">{{ form.kind_snapshot === 'TAG' ? '退回修改标签' : '退回补充资料' }}</NativeSelectOption>
<NativeSelectOption value="COMPLETED">{{ "已完成交付" }}</NativeSelectOption>
<NativeSelectOption v-if="form.source === 'REDEMPTION'" value="CANCELLED">{{ "取消并退回积分" }}</NativeSelectOption>
</NativeSelect>
</div>
<Alert v-if="form.kind_snapshot === 'TAG' && form.status === 'COMPLETED' && !form.tag_reviewed">
<AlertDescription>
<strong>{{ form.tag_suspension_reason ? `标签已暂停展示：${form.tag_suspension_reason}` : '历史标签尚无审核记录，暂不展示；请核对内容后复核。' }}</strong>
</AlertDescription>
</Alert>
<div v-if="form.kind_snapshot === 'TAG' && form.status === 'COMPLETED' && !form.tag_reviewed" class="engagement-field ap-field--full">
<span>{{ "标签复核" }}</span>
<label class="engagement-check">
<Checkbox v-model="claimTagReviewed" />已审核标签内容符合俱乐部要求及相关规定</label>
<Button @click="reviewHistoricalTag" size="xs" :disabled="(!claimTagReviewed) || (saving)">确认审核</Button>
</div>
<div v-if="form.kind_snapshot === 'TAG' && form.status === 'COMPLETED' && form.tag_reviewed" class="engagement-field ap-field--full">
<span>{{ "标签管理" }}</span>
<Button @click="suspendTag" variant="outline" size="xs" :disabled="(saving)">暂停展示</Button>
</div>
<div v-if="form.kind_snapshot === 'TAG' && claimStatus === 'COMPLETED' && !['COMPLETED','CANCELLED'].includes(form.status)" class="engagement-field ap-field--full">
<span>{{ "标签审核" }}</span>
<label class="engagement-check">
<Checkbox v-model="claimTagReviewed" />已审核标签内容符合俱乐部要求及相关规定</label>
</div>
<Alert class="claim-feedback" v-if="claimStatus === 'CANCELLED' && !['COMPLETED','CANCELLED'].includes(form.status)">
<AlertDescription>
<strong>{{ `取消后退回 ${form.points_spent} 积分，并恢复商品库存。` }}</strong>
</AlertDescription>
</Alert>
<div v-if="!['COMPLETED','CANCELLED'].includes(form.status)" class="engagement-field ap-field--full">
<span>{{ "交付凭证 / 说明" }}</span>
<Textarea v-model="claimNote" maxlength="255" placeholder="快递单号、交付记录编号、退回补充资料要求或取消原因" aria-label="交付凭证 / 说明" />
</div>
</template>
<template v-if="tab === 'namings'">
<div  class="engagement-field ">
<span>{{ "冠名文字" }}</span>
<Input v-model="form.title" maxlength="30" aria-label="冠名文字" />
</div>
<div  class="engagement-field ">
<span>{{ "列表展示" }}</span>
<Switch v-model="form.is_visible" aria-label="列表展示" />
</div>
<div  class="engagement-field ">
<span>{{ "累计到期" }}</span>{{form.expires_at}}</div>
<Alert >
<AlertDescription>
<strong>{{ "时长来自已付款礼物，修改展示文案不会增加或扣减冠名时长。" }}</strong>
</AlertDescription>
</Alert>
</template>
<template v-if="tab === 'rules'">
<div  class="engagement-field ">
<span>{{ "播报事件" }}</span>{{kindLabel(form.kind)}}</div>
<div  class="engagement-field ap-field--full">
<span>{{ "内容模板" }}</span>
<Textarea v-model="form.template" :rows="3" maxlength="255" aria-label="内容模板" />
<span class="hint">可用：{nickname} 老板昵称、{reward} 奖品、{level} 等级、{provider} 陪玩</span>
</div>
<div  class="engagement-field ">
<span>{{ "展示时长（小时）" }}</span>
<NumberField :step-snapping="false" v-model="form.visible_hours" :min="1" :max="8760" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="展示时长（小时）" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "昵称脱敏" }}</span>
<Switch v-model="form.mask_nickname" aria-label="昵称脱敏" />
</div>
<div  class="engagement-field ">
<span>{{ "启用" }}</span>
<Switch v-model="form.enabled" aria-label="启用" />
</div>
</template>
<template v-if="tab === 'feed'">
<Alert >
<AlertDescription>
<strong>{{ form.content }}</strong>
</AlertDescription>
</Alert>
<div  class="engagement-field ">
<span>{{ "前台显示" }}</span>
<Switch v-model="form.is_visible" aria-label="前台显示" />
</div>
<div  class="engagement-field ">
<span>{{ "展示截止" }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(form.expires_at)" @update:model-value="value => { form.expires_at = dateIso(value) }" aria-label="展示截止" />
</div>
</template>
<template v-if="tab === 'content'">
<Alert >
<AlertDescription>
<strong>{{ "入场弹窗自动展示近期活动和下单须知；首页热点仅展示最新活动消息。已结束活动保留在活动列表，按上架时间倒序，详情只显示海报。停用可隐藏活动。" }}</strong>
</AlertDescription>
</Alert>
<div  class="engagement-field ">
<span>{{ "标题" }}</span>
<Input v-model="form.title" aria-label="标题" />
</div>
<div  class="engagement-field ">
<span>{{ "内容标识" }}</span>
<Input v-model="form.code" :disabled="!!form.id" aria-label="内容标识" />
</div>
<div  class="engagement-field ">
<span>{{ "展示位置" }}</span>
<NativeSelect v-model="form.kind" aria-label="展示位置">
<NativeSelectOption v-for="(label,key) in contentKinds" :key="key" :value="key">{{ label }}</NativeSelectOption>
</NativeSelect>
</div>
<div  class="engagement-field ap-field--full">
<span>{{ "海报" }}</span>
<Input v-model="form.image_url" aria-label="海报" />
<input type="file" accept="image/png,image/jpeg,image/webp" @change="upload" />
</div>
<div v-if="form.kind !== 'HOT'" class="engagement-field ap-field--full">
<span>{{ "正文" }}</span>
<Textarea v-model="form.content" :rows="8" placeholder="支持 p、h2、strong、ul、li、img 等富文本标签。可仅上传海报。" aria-label="正文" />
</div>
<div  class="engagement-field ">
<span>{{ "上架时间" }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(form.starts_at)" @update:model-value="value => { form.starts_at = dateIso(value) }" aria-label="上架时间" />
</div>
<div  class="engagement-field ">
<span>{{ form.kind === 'HOT' ? '活动结束时间' : '下架时间' }}</span>
<Input  type="datetime-local" step="1" :model-value="localDateInput(form.ends_at)" @update:model-value="value => { form.ends_at = dateIso(value) }" :aria-label="form.kind === 'HOT' ? '活动结束时间' : '下架时间'" />
</div>
<div  class="engagement-field ">
<span>{{ "启用" }}</span>
<Switch v-model="form.is_active" aria-label="启用" />
</div>
</template>
<template v-if="tab === 'tiers'">
<div  class="engagement-field ">
<span>{{ "等级" }}</span>{{form.name}}</div>
<div  class="engagement-field ">
<span>{{ "累计确认实销门槛（元）" }}</span>
<NumberField :step-snapping="false" v-model="thresholdYuan" :min="0" :step="1" :format-options="{ minimumFractionDigits: 2, maximumFractionDigits: 2 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="累计确认实销门槛（元）" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "每月折扣券" }}</span>
<span >VIP2/3/4/5为九折1/2/4/10张，VIP6八五折10张；每张最高优惠100元，月中升级补差。</span>
</div>
<div  class="engagement-field ">
<span>{{ "安全箱权益" }}</span>
<span >VIP1至VIP5共1张永久券，未用券随级别升折扣；VIP6有效报单满72小时、审核通过且无客诉后补一张。</span>
</div>
<div v-for="binding in [{key:'companion_service_ids',label:'1小时陪玩绑定'},{key:'safe_box_service_ids',label:'33安全箱代肝主套餐绑定'}]" :key="binding.key" class="engagement-field ">
<span>{{ binding.label }}</span>
<Select v-model="form[binding.key]" multiple @update:open="value => { if (value) serviceSearch = '' }">
<SelectTrigger size="sm" :aria-label="binding.label">
<SelectValue placeholder="请选择经核对的套餐" />
</SelectTrigger>
<SelectContent><Input v-model="serviceSearch" aria-label="搜索套餐" placeholder="搜索套餐名称" @keydown.stop />
<SelectItem v-for="service in searchServices" :key="service.id" :value="service.id">{{ `${service.game} · ${service.name} · ${service.price/10} 兴安币/${service.unit || '份'} · #${service.id}` }}</SelectItem>
</SelectContent>
</Select>
</div>
<div  class="engagement-field ap-field--full">
<span>{{ "升级权益总额（按类别补差）" }}</span>
<div >
<div  class="engagement-table" :aria-busy="loading">
<Table>
<TableHeader>
<TableRow>
<TableHead >{{ "权益" }}</TableHead>
<TableHead >{{ "数量" }}</TableHead>
<TableHead >
</TableHead>
</TableRow>
</TableHeader>
<TableBody>
<TableRow v-for="(row,index) in form.upgrade_rewards || []" :key="row.id || index">
<TableCell >
<Select v-model="row.reward" @update:open="value => { if(value) rewardSearch = '' }"><SelectTrigger size="sm" ><SelectValue placeholder="选择权益" /></SelectTrigger><SelectContent><Input v-model="rewardSearch" aria-label="搜索权益" placeholder="搜索权益名称" @keydown.stop />
<SelectItem v-for="reward in searchRewards(rewards.filter(r=>!r.in_shop && ['SERVICE','NAMING','TAG'].includes(r.kind)))" :key="reward.id" :value="reward.id">{{ reward.name }}</SelectItem>
</SelectContent></Select>
</TableCell>
<TableCell >
<NumberField :step-snapping="false" v-model="row.quantity" :min="1" :max="100" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput  />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</TableCell>
<TableCell >
<Button size="xs" @click="form.upgrade_rewards.splice(index,1)" variant="link">移除</Button>
</TableCell>
</TableRow>
<TableRow v-if="!(form.upgrade_rewards || []).length">
<TableCell :colspan="20" class="empty-state">暂无记录</TableCell>
</TableRow>
</TableBody>
</Table>
</div>
<Button size="xs" @click="(form.upgrade_rewards ||= []).push({reward:null,quantity:1})">添加权益</Button>
</div>
</div>
<div v-for="(_,key) in form.benefits" :key="key" class="engagement-field ">
<span>{{ String(key) }}</span>
<Input v-model="form.benefits[key]" :aria-label="String(key)" />
</div>
<div  class="engagement-field ">
<span>{{ "启用" }}</span>
<Switch v-model="form.active" aria-label="启用" />
</div>
</template>
<template v-if="tab === 'packages'">
<div  class="engagement-field ">
<span>{{ "实付充值（元）" }}</span>
<NumberField :step-snapping="false" v-model="thresholdYuan" :min="0.01" :step="1" :format-options="{ minimumFractionDigits: 2, maximumFractionDigits: 2 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="实付充值（元）" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "赠送余额（元）" }}</span>
<NumberField :step-snapping="false" v-model="giftYuan" :min="0" :step="1" :format-options="{ minimumFractionDigits: 2, maximumFractionDigits: 2 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="赠送余额（元）" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<div  class="engagement-field ">
<span>{{ "消费积分倍率" }}</span>
<NumberField :step-snapping="false" v-model="pointsMultiplier" :min="1" :max="10" :step="0.1" :format-options="{ minimumFractionDigits: 2, maximumFractionDigits: 2 }">
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="消费积分倍率" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
<Alert >
<AlertDescription>
<strong>{{ "只在消费此充值批次余额时计算积分；充值本身不加积分。修改后仅影响新的充值记录。" }}</strong>
</AlertDescription>
</Alert>
</template>
<div v-if="['rewards','rules','feed','content'].includes(tab) && !(tab === 'content' && form.kind === 'HOT')" class="engagement-field ">
<span>{{ "展示排序" }}</span>
<NumberField :step-snapping="false" v-model="form.sort_order" :step="1" >
<NumberFieldContent>
<NumberFieldDecrement />
<NumberFieldInput aria-label="展示排序" />
<NumberFieldIncrement />
</NumberFieldContent>
</NumberField>
</div>
</fieldset>
<DialogFooter>
<Button size="xs" @click="dialog=false">关闭</Button>
<Button size="xs" @click="save" :disabled="(tab === 'claims' && ['COMPLETED','CANCELLED'].includes(form.status)) || (saving)">保存</Button>
</DialogFooter>
</DialogContent>
</Dialog>
<ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
<Dialog :open="pauseOpen" @update:open="value => { if (!value) finishPause(false) }">
  <DialogContent><DialogHeader><DialogTitle>暂停展示标签</DialogTitle><DialogDescription>暂停后标签不再展示，权益记录保留并进入待复核。</DialogDescription></DialogHeader>
    <label class="engagement-field">暂停原因<Textarea v-model="pauseReason" placeholder="填写暂停原因（2 至 255 字）" maxlength="255" /></label>
    <p v-if="pauseError" role="alert">{{ pauseError }}</p>
    <DialogFooter><Button size="xs" variant="outline" @click="finishPause(false)">返回</Button><Button size="xs" @click="finishPause(true)">确认暂停</Button></DialogFooter>
  </DialogContent>
</Dialog>
</div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Switch } from '@xa/shadcn-vue/switch'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@xa/shadcn-vue/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { NumberField, NumberFieldContent, NumberFieldDecrement, NumberFieldIncrement, NumberFieldInput } from '@xa/shadcn-vue/number-field'
import { Tabs, TabsList, TabsTrigger } from '@xa/shadcn-vue/tabs'
import { Pagination, PaginationContent, PaginationEllipsis, PaginationItem, PaginationNext, PaginationPrevious } from '@xa/shadcn-vue/pagination'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { showNotice } from '@/utils/notice'
import { formatRmb, rmbToRawAmount, toCoin, toRawAmount } from '@xa/money'
const POINTS_RATE_SCALE = 100
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { useRoute } from 'vue-router'
import { adminWs } from '@/utils/ws'
const auth=useAuthStore(),tab=ref('rewards'),rows=ref<any[]>([]),total=ref(0),page=ref(1),loading=ref(false),saving=ref(false),dialog=ref(false),form=ref<any>({}),rewards=ref<any[]>([]),unlimited=ref(true),balanceCoins=ref(0),thresholdYuan=ref(0),giftYuan=ref(0),pointsMultiplier=ref(1),claimStatus=ref('FULFILLING'),claimNote=ref(''),claimTagReviewed=ref(false)
const route=useRoute()
const rewardSearch=ref(''),serviceSearch=ref('')
function searchRewards(options:any[]) { const keyword=rewardSearch.value.trim().toLocaleLowerCase();return options.filter(item=>item.name.toLocaleLowerCase().includes(keyword)) }
const searchServices=computed(()=>companionServices.value.filter(item=>(item.game+' '+item.name+' '+item.id).toLocaleLowerCase().includes(serviceSearch.value.trim().toLocaleLowerCase())))
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
function successNotice(message:string) { showNotice(message, 'success') }
async function confirmAction(description:string,title:string,options:any={}) { if (!await askConfirm({description,title,confirmText:options.confirmButtonText,cancelText:options.cancelButtonText})) throw new Error('cancel') }
function localDateInput(value:unknown) { if (!value) return ''; const d=new Date(String(value)); if (Number.isNaN(d.getTime())) return ''; return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,19) }
function dateIso(value:unknown) { if (!value) return null; const d=new Date(String(value)); return Number.isNaN(d.getTime()) ? null : d.toISOString() }
const pauseOpen=ref(false),pauseReason=ref(''),pauseError=ref('')
let pauseResolve:((value:string|null)=>void)|null=null
function askPauseReason():Promise<string|null> { if(pauseResolve)return Promise.resolve(null);pauseReason.value='';pauseError.value='';pauseOpen.value=true;return new Promise(resolve=>{pauseResolve=resolve}) }
function finishPause(confirmed:boolean) { if(confirmed&&(pauseReason.value.trim().length<2||pauseReason.value.trim().length>255)){pauseError.value='请输入 2 至 255 字';return}const resolve=pauseResolve;pauseResolve=null;pauseOpen.value=false;resolve?.(confirmed?pauseReason.value.trim():null) }
onBeforeUnmount(()=>finishPause(false))
const claimSearch=ref(''),claimFilterStatus=ref(''),claimFilterSource=ref(''),claimFilterAccountType=ref(''),claimFilterKind=ref(''),claimNeedsTagReview=ref(false),claimTagReady=ref(false)
const claimOpeningId=ref<number|null>(null)
const claimConflict=ref('')
let claimOpenRevision=0
const claimAccountType=(kind:string)=>({BOSS:'老板',PROVIDER:'陪玩'} as Record<string,string>)[kind] || '账号'
const tagReviewLabel=(claim:any)=>claim.status==='CANCELLED'?'已取消':claim.status==='COMPLETED'?(claim.tag_reviewed?'已审核':claim.tag_revision_submitted?'已修改待复核':claim.tag_suspension_reason?'暂停展示':'待复核'):(claim.tag_revision_required?'待修改':claim.details?.tag?'待审核':'待填写')
const claimSource=(source:string)=>({GIFT:'赠礼权益',VIP:'会员权益',REDEMPTION:'积分兑换',LOTTERY:'转盘奖品'} as Record<string,string>)[source] || source || '其他'
function claimTerms(claim:any) {
  const parts=[]
  if (claim.duration_months) parts.push(`${claim.duration_months} 个月`)
  if (claim.duration_days) parts.push(`${claim.duration_days} 天`)
  return parts.join(' + ') || '无固定期限'
}
const pageSize = 20
const kook=ref<any>({})
const loadFailed = ref(false)
const sectionKey = ref('points')
const sections: { key: string; label: string; description: string; tabs: { key: string; label: string; perm?: string }[] }[] = [
  { key: 'points', label: '积分与礼品', description: '兑换商品、转盘与奖品交付', tabs: [
    { key: 'kook', label: 'KOOK自动积分' }, { key: 'rewards', label: '积分好礼' }, { key: 'lotteries', label: '幸运转盘' },
    { key: 'claims', label: '奖品交付', perm: 'engagement:fulfill' }, { key: 'external', label: 'KOOK 积分核对', perm: 'engagement:points' },
  ] },
  { key: 'benefits', label: '会员权益', description: '赠礼、冠名与 VIP 权益', tabs: [
    { key: 'giftBenefits', label: '赠礼附送权益' }, { key: 'namings', label: '陪玩冠名' },
    { key: 'tiers', label: 'VIP 权益' }, { key: 'packages', label: '预存档位' },
  ] },
  { key: 'content', label: '运营内容', description: '前台内容、播报与运营设置', tabs: [
    { key: 'content', label: '首页与权益内容' }, { key: 'rules', label: '播报规则' },
    { key: 'feed', label: '播报记录' }, { key: 'settings', label: '运营设置' },
  ] },
]
const permittedTabs = (section: typeof sections[number]) => section.tabs.filter(item => auth.hasPerm(item.perm || 'engagement:view'))
const visibleSections = computed(() => sections.filter(section => permittedTabs(section).length > 0))
const activeSection = computed(() => visibleSections.value.find(section => section.key === sectionKey.value) || visibleSections.value[0])
const visibleTabs = computed(() => activeSection.value ? permittedTabs(activeSection.value) : [])
function selectSection(key: string) {
  if (sectionKey.value === key) return
  sectionKey.value = key
  tab.value = visibleTabs.value[0].key
}
watch(tab, () => { claimOpenRevision++; claimOpeningId.value=null; void load() })
const endpoints:Record<string,string>={namings:'provider-namings',giftBenefits:'gift-benefits',rewards:'club-rewards',lotteries:'lotteries',claims:'club-claims',rules:'broadcast-rules',feed:'broadcasts',content:'club-content',tiers:'vip-tiers',packages:'recharge-packages'}
const labels:Record<string,string>={namings:'陪玩冠名',giftBenefits:'赠礼附送权益',rewards:'积分好礼',lotteries:'幸运转盘',claims:'奖品交付',rules:'播报规则',feed:'播报记录',content:'运营内容',tiers:'VIP 权益',packages:'预存档位'}
const rewardKinds={PHYSICAL:'实物寄送',VIRTUAL:'游戏内交付',SERVICE:'服务权益',NAMING:'冠名权益',TAG:'自定义 TAG',BALANCE:'余额兑换',POINTS:'积分'}
const contentKinds={RIGHTS:'老板权益',HOT:'俱乐部活动',ORDER_RULES:'下单须知',PROVIDER_RULES:'打手保障',AFTERSALE_RULES:'售后标准',POINT_RULES:'积分规则'}
const broadcastKinds:Record<string,string>={GIFT:'礼物单播报',LOTTERY:'转盘中奖播报',NAMING:'冠名播报',RECHARGE:'充值播报',UPGRADE:'升级播报',REVIEW:'好评广播',BIRTHDAY:'生日祝福',LOTTERY_SERVICE:'抽中体验单',LOTTERY_NAMING:'抽中冠名',LOTTERY_TAG:'抽中 TAG'}
const detailLabels:Record<string,string>={recipient:'收件人',phone:'联系电话',address:'收货地址',game_uid:'游戏 UID',note:'补充说明',tag:'指定 TAG',provider_name:'冠名陪玩'}
const external=reactive({account_id:undefined as number|undefined,kind:'KOOK_CHECKIN',day:'',online_seconds:7200,source_id:''})
const canEdit=computed(()=>auth.hasPerm('engagement:edit')),probabilityTotal=computed(()=>form.value.prizes?.reduce((n:number,p:any)=>n+(Number(p.probability)||0),0)||0)
const hasExperiencePity=computed(()=>form.value.prizes?.some((prize:any)=>rewards.value.some(reward=>reward.id===prize.reward && reward.code==='lottery-experience-68')) || false)
const hasTenGuaranteeCandidates=computed(()=>form.value.prizes?.some((prize:any)=>Number(prize.probability)>0 && rewards.value.some(reward=>reward.id===prize.reward && reward.kind!=='POINTS')) || false)
const active=(row:any)=>row.is_active??row.enabled??row.is_visible??true,kindLabel=(kind:string)=>broadcastKinds[kind]||'',media=(path:string)=>path.startsWith('/media/')?new URL(import.meta.env.VITE_API_BASE||'/api/admin',location.origin).origin+path:path
const settings=ref<any>({}),companionServices=ref<any[]>([])
async function saveSettings(){saving.value=true;try{await request({url:'/club-config/',method:'put',data:settings.value});successNotice('已保存')}finally{saving.value=false}}
let seq=0
let claimRefreshTimer: ReturnType<typeof setTimeout> | null = null
let claimSubscriptions: Array<() => void> = []
function refreshClaimsSoon() {
  if (tab.value !== 'claims' || document.hidden) return
  if (claimRefreshTimer) clearTimeout(claimRefreshTimer)
  claimRefreshTimer=setTimeout(() => { claimRefreshTimer=null; if (tab.value==='claims') void fetchRows() }, 500)
}
function resetTagFilters(){claimFilterStatus.value='';claimFilterAccountType.value='';claimFilterKind.value='';claimFilterSource.value='';claimSearch.value=''}
function changeTagReviewFilter(){if(claimNeedsTagReview.value){claimTagReady.value=false;resetTagFilters()}void load()}
function changeTagReadyFilter(){if(claimTagReady.value){claimNeedsTagReview.value=false;resetTagFilters()}void load()}
async function load(){page.value=1;await fetchRows()}
async function fetchRows() {
  const key = tab.value, version = ++seq
  loadFailed.value = false
  loading.value = key !== 'external'
  if (key === 'external') return
  try {
    if (key === 'kook') {
      const res=await request({url:'/kook-points-status/'})
      if(version===seq)kook.value=res.data
    } else if (key === 'settings') {
      const res = await request({ url: '/club-config/' })
      if (version === seq) settings.value = res.data
    } else {
      const res = await request({ url: '/' + endpoints[key] + '/', params: { page: page.value, page_size: pageSize, ...(key === 'claims' ? { status: claimFilterStatus.value, source: claimFilterSource.value, kind: claimFilterKind.value, account_type: claimFilterAccountType.value, needs_tag_review: claimNeedsTagReview.value ? '1' : '', tag_ready_for_review: claimTagReady.value ? '1' : '', search: claimSearch.value.trim() } : {}) } })
      if (version === seq) { rows.value = res.data.list; total.value = res.data.total }
    }
  } catch {
    if (version === seq) { rows.value = []; total.value = 0; loadFailed.value = true }
  } finally {
    if (version === seq) loading.value = false
  }
}
async function edit(row?:any){
  claimConflict.value=''
  if (tab.value === 'claims' && row) {
    if (claimOpeningId.value) return
    const revision=++claimOpenRevision, session=auth.sessionRevision
    claimOpeningId.value=row.id
    try {
      const result=await request({url:`/club-claims/${row.id}/`})
      if (revision!==claimOpenRevision || session!==auth.sessionRevision || tab.value!=='claims') return
      row=result.data
    } catch { return }
    finally { if (revision===claimOpenRevision) claimOpeningId.value=null }
  }
  form.value=row?JSON.parse(JSON.stringify(row)):
{name:'',audience:'BOSS',title:'',code:'',kind:tab.value==='content'?'HOT':'PHYSICAL',description:'',image_url:'',point_cost:tab.value==='lotteries'?100:0,point_value:0,balance_amount:0,stock:null,in_shop:true,is_active:false,enabled:false,duration_days:0,duration_months:0,highlighted:false,sort_order:0,content:'',starts_at:null,ends_at:null,rules:'',prizes:[],active:true,threshold:0,inclusive:false,cumulative:false,once_per_account:false,category:null};unlimited.value=form.value.stock===null;balanceCoins.value=toCoin(form.value.balance_amount);thresholdYuan.value=Number(formatRmb(form.value.consumption_threshold??form.value.recharge_threshold??form.value.amount??form.value.threshold??0));giftYuan.value=Number(formatRmb(form.value.gift_amount));pointsMultiplier.value=(form.value.points_rate??POINTS_RATE_SCALE)/POINTS_RATE_SCALE;claimStatus.value=(form.value.status==='FULFILLING' || form.value.tag_revision_required)?'COMPLETED':'FULFILLING';claimNote.value='';claimTagReviewed.value=false;if(['lotteries','tiers','giftBenefits'].includes(tab.value)){const res=await request({url:'/club-rewards/',params:{page_size:500}});rewards.value=res.data.list}if(tab.value==='tiers'){const res=await request({url:'/vip-tiers/service-options/'});companionServices.value=res.data}dialog.value=true}
async function suspendTag(){
  if(saving.value)return
  let reason=''
  const result=await askPauseReason(); if(result===null)return; reason=result
  saving.value=true
  try {await request({url:`/club-claims/${form.value.id}/suspend-tag/`,method:'post',data:{reason,expected_tag:form.value.details?.tag}});dialog.value=false;successNotice('标签已暂停展示');await load()}
  finally {saving.value=false}
}
async function reviewHistoricalTag(){
  if(!claimTagReviewed.value||saving.value)return
  saving.value=true
  try {
    await request({url:`/club-claims/${form.value.id}/review-tag/`,method:'post',data:{tag_reviewed:true,expected_tag:form.value.details?.tag}})
    dialog.value=false;successNotice('标签审核通过');await load()
  } finally {saving.value=false}
}
async function save(){if(tab.value==='claims' && claimStatus.value==='CANCELLED'){try{await confirmAction(`取消后将退回 ${form.value.points_spent} 积分并恢复库存。确认取消这项兑换吗？`,'确认取消兑换',{type:'warning',confirmButtonText:'确认取消',cancelButtonText:'返回'})}catch{return}}saving.value=true;try{const data={...form.value};if(tab.value==='rewards'){data.stock=unlimited.value?null:data.stock;data.balance_amount=toRawAmount(balanceCoins.value)}if(tab.value==='giftBenefits')data.threshold=rmbToRawAmount(thresholdYuan.value);if(tab.value==='tiers'){data.consumption_threshold=rmbToRawAmount(thresholdYuan.value);delete data.recharge_threshold;}if(tab.value==='packages'){data.amount=rmbToRawAmount(thresholdYuan.value);data.gift_amount=rmbToRawAmount(giftYuan.value);data.points_rate=Math.round(pointsMultiplier.value*POINTS_RATE_SCALE)}const base='/'+endpoints[tab.value]+'/';if(tab.value==='claims'){
    try { await request({url:base+data.id+'/process/',method:'post',silent:true,data:{status:claimStatus.value,note:claimNote.value,tag_reviewed:claimTagReviewed.value,expected_details:data.details || {},expected_status:data.status,expected_terms:{duration_days:data.duration_days,duration_months:data.duration_months,highlighted:data.highlighted}}}) }
    catch (error:any) {
      if (['CLAIM_DETAILS_CHANGED','CLAIM_STATUS_CHANGED','CLAIM_TERMS_CHANGED'].includes(error?.response?.data?.errorCode)) {
        claimTagReviewed.value=false;claimConflict.value=error.response.data.errorCode==='CLAIM_STATUS_CHANGED'?'交付状态已变化，请核对最新处理结果。':error.response.data.errorCode==='CLAIM_TERMS_CHANGED'?'权益规格已变化，请核对最新时长和类型。':'交付资料已更新，请核对最新内容后重新提交。'
        try { const latest=await request({url:base+data.id+'/'}); form.value=latest.data }
        catch { dialog.value=false; showNotice('最新资料加载失败，请重新打开记录'); }
      } else if (error?.code!=='SESSION_CHANGED') showNotice(error?.response?.data?.msg || error?.msg || '处理失败，请重试')
      return
    }
  }else if(tab.value==='lotteries'&&data.id)await request({url:base+data.id+'/configure/',method:'post',data});else await request({url:base+(data.id?data.id+'/':''),method:data.id?'patch':'post',data});dialog.value=false;successNotice('已保存');await load()}finally{saving.value=false}}
async function upload(event:Event){const file=(event.target as HTMLInputElement).files?.[0];if(!file)return;const data=new FormData();data.append('image',file);const res=await request({url:'/club-content-image/',method:'post',data});form.value.image_url=res.data.url}
async function recordExternal(){saving.value=true;try{await request({url:'/external-points/',method:'post',data:external});successNotice('积分已入账')}finally{saving.value=false}}
onMounted(() => {
  claimSubscriptions=[adminWs.on('claim_queue_update', refreshClaimsSoon), adminWs.on('connected', refreshClaimsSoon)]
  adminWs.connect()
  document.addEventListener('visibilitychange', refreshClaimsSoon)
  const section = visibleSections.value[0]
  if (!section) return
  if (route.query.tab==='claims' && auth.hasPerm('engagement:fulfill')) {
    sectionKey.value='points'
    claimNeedsTagReview.value=route.query.needs_tag_review==='1'
    claimTagReady.value=route.query.tag_ready_for_review==='1'
    claimFilterStatus.value=['PENDING','FULFILLING','COMPLETED','CANCELLED'].includes(String(route.query.status)) ? String(route.query.status) : ''
    if (tab.value!=='claims') { tab.value='claims'; return }
  }
  if (!visibleSections.value.some(item => item.key === sectionKey.value)) sectionKey.value = section.key
  if (!visibleTabs.value.some(item => item.key === tab.value)) tab.value = visibleTabs.value[0].key
  else void load()
})
onBeforeUnmount(() => { if (claimRefreshTimer) clearTimeout(claimRefreshTimer); document.removeEventListener('visibilitychange', refreshClaimsSoon); claimSubscriptions.forEach(off=>off()) })
</script>
<style scoped>
.engagement-form { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap:16px; }
fieldset { border:0; padding:0; margin:0; min-width:0; display:grid; gap:16px; }
.engagement-field { display:grid; gap:8px; min-width:0; font-size:13px; }
.ap-field--full { grid-column:1 / -1; }
.engagement-check { display:inline-flex; align-items:center; gap:8px; min-height:32px; font-size:13px; }
:global(.engagement-dialog) { max-width:min(880px,calc(100vw - 24px)); max-height:calc(100dvh - 24px); overflow-y:auto; }
.engagement-table { min-width:0; overflow:auto; }
.engagement-table :deep(table) { min-width:640px; }
.engagement-tabs { flex-wrap:wrap; height:auto; margin:8px 0; }
.claim-details { display:grid; gap:8px; grid-column:1 / -1; }
.claim-details > div { display:grid; grid-template-columns:100px minmax(0,1fr); gap:12px; }
.claim-details dt { color:var(--muted-foreground); }
.claim-details dd { margin:0; overflow-wrap:anywhere; }
.empty-state { padding:24px; text-align:center; color:var(--muted-foreground); }
@media(max-width:720px){.engagement-form{grid-template-columns:1fr;}}

.operations-sections { display: flex; gap: 8px; margin-bottom: 20px; padding: 8px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.operations-sections button { flex: 1; min-width: 0; text-align: left; padding: 12px 16px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--foreground); cursor: pointer; }
.operations-sections button:hover { background: var(--secondary); }
.operations-sections button.is-active { background: color-mix(in srgb, var(--primary) 7%, var(--card)); border-color: color-mix(in srgb, var(--primary) 25%, var(--border)); }
.operations-sections strong { display: block; font-size: 15px; font-weight: 600; }
.operations-sections .is-active strong { color: var(--primary); }
.operations-sections span { display: block; margin-top: 4px; color: var(--muted-foreground); font-size: 12px; }
.operations-workspace { padding: 8px 24px 24px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); min-width: 0; }


.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 24px 0 16px; }
.toolbar-title { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.toolbar h2 { margin: 0; font-size: 16px; font-weight: 600; }
.toolbar-title span { font-size: 12px; color: var(--muted-foreground); }
.claim-filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }
.claim-filters :deep([data-slot="input"]) { width: 200px; }
.claim-filters :deep([data-slot="native-select-wrapper"]) { width: 120px; }
@keyframes claim-feedback-enter { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
.claim-feedback { animation: claim-feedback-enter 180ms cubic-bezier(.25,.8,.25,1); }
@media (prefers-reduced-motion: reduce) { .claim-feedback { animation: none; } }
.claim-tag-preview { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; overflow-wrap:anywhere; }
.claim-mobile-list { display: none; }
.gift-name { display: flex; align-items: center; gap: 12px; }
.gift-name img { flex: 0 0 40px; width: 40px; height: 40px; object-fit: contain; background: var(--secondary); border: 1px solid var(--border); border-radius: 8px; }
.numeric { font-weight: 600; font-variant-numeric: tabular-nums; }
.hint { font-size: 12px; color: var(--muted-foreground); margin: 0 12px; }
.external-form { max-width: 720px; padding: 16px 0; }
.external-form fieldset { margin-top:24px; }
[data-slot="alert"] { margin-bottom:16px; }
[data-slot="input"], :deep([data-slot="native-select-wrapper"]) { width:100%; }
input[type=file] { margin-top: 12px; max-width: 100%; }


@media (max-width: 720px) {
  .operations-sections { gap: 4px; margin-bottom: 12px; }
  .operations-sections button { padding: 12px 8px; text-align: center; }
  .operations-sections strong { font-size: 13px; }
  .operations-sections span { display: none; }
  .operations-workspace { padding: 4px 12px 16px; }
  .toolbar { margin: 16px 0; flex-wrap: wrap; }
  .toolbar-title { gap: 8px; }
  .claim-filters :deep([data-slot="input"]) { width: 100%; }
  .claim-filters :deep([data-slot="native-select-wrapper"]) { flex: 1 1 0; min-width: 100px; }
  .claim-desktop-table { display: none; }
  .claim-mobile-list { display: grid; gap: 8px; }
  .claim-mobile-card { display: grid; gap: 8px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); font-size: 12px; }
  .claim-card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
  .claim-card-top strong { min-width: 0; overflow-wrap: anywhere; font-size: 14px; }
  .claim-card-meta { color: var(--muted-foreground); }
  .claim-mobile-card button { justify-self: start; margin: 0; }
}
</style>
