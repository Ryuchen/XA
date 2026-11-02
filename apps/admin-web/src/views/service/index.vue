<template>
  <div class="sv-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理服务、礼物和价格配置</p>
      <div class="ap-page-head-actions">
        <Button v-if="auth.hasPerm('service:edit')" variant="outline" :disabled="!metadataReady" @click="openGameCatDialog">类目设置</Button>
        <Button v-if="auth.hasPerm('service:edit')" variant="outline" :disabled="!metadataReady" @click="openServiceCatDialog">分类设置</Button>
        <Button v-if="auth.hasPerm('service:edit')" :disabled="!metadataReady || !!saveReview" @click="onCreate">新增服务项</Button>
      </div>
    </div>

    <Alert v-if="dictLoading"><AlertDescription>正在读取商品配置…</AlertDescription></Alert><Alert v-else-if="dictFailed.length" variant="destructive"><AlertDescription>{{ dictFailed.join('、') }}读取失败，暂不能编辑商品 <Button variant="link" @click="loadDicts">重新读取</Button></AlertDescription></Alert>
    <Alert v-if="saveReview && !dialogVisible && !saving"><AlertTitle>商品保存结果待核对</AlertTitle><AlertDescription><p>「{{ saveReview.name }}」{{ saveReview.id ? `（ID ${saveReview.id}）` : '（新增）' }}的保存结果未确认，请查询商品并核实，请求可能仍在处理。同名商品可能不止一项，归档玩法的商品不会出现在此列表。</p><Button variant="outline" @click="locateSaveReview">查询商品</Button><Button v-if="saveReviewSearched" variant="outline" @click="finishSaveReview">已核对，解除限制</Button></AlertDescription></Alert>
    <p v-if="notice && !dialogVisible && !gameCatDialogVisible && !serviceCatDialogVisible" role="status">{{ notice }}</p>
    <div v-if="!dictFailed.includes('游戏类目')" class="catalog-strip">
      <button type="button" :aria-pressed="!query.game_category" :class="['catalog-tile', !query.game_category && 'catalog-tile--active']" @click="selectGame('')"><span>全部游戏</span><strong>{{ dictLoading ? '—' : gameCats.length }}</strong><small>按类目查看服务配置</small></button>
      <button v-for="cat in gameCats" :key="cat.id" type="button" :aria-pressed="query.game_category === cat.id" :class="['catalog-tile', query.game_category === cat.id && 'catalog-tile--active']" @click="selectGame(cat.id)">
        <span>{{ cat.name }} <Badge v-if="!cat.is_active" variant="outline">已停用</Badge></span><strong>{{ cat.active_service_count || 0 }} <small>展示项目</small></strong><small>{{ cat.service_count || 0 }} 项服务 · {{ cat.escort_count || 0 }} 位陪玩</small>
      </button>
    </div>
    <div class="sv-status-note"><Popover><PopoverTrigger as-child><Button variant="link">商品展示状态说明 ⓘ</Button></PopoverTrigger><PopoverContent>商品状态取决于商品、游戏类目、服务分类与玩法的启用情况。</PopoverContent></Popover></div>
    <SimplePagedList :key="auth.sessionRevision" ref="tableRef" :fetcher="serviceItemApi.list" :query="query">
      <template #toolbar="{ reload }"><div class="sv-filters">
        <Input v-model="query.keyword" aria-label="商品名称" placeholder="商品名称" maxlength="100" @keyup.enter="reload" @update:model-value="value => { if (value === '') reload() }" />
        <Input v-model="query.item_id" aria-label="商品编号" placeholder="商品编号" inputmode="numeric" maxlength="18" @keyup.enter="reload" @update:model-value="value => { if (value === '') reload() }" />
        <NativeSelect :model-value="String(query.gameplay)" aria-label="玩法" @update:model-value="value => { query.gameplay = value ? Number(value) : ''; reload() }"><NativeSelectOption value="">全部玩法</NativeSelectOption><NativeSelectOption v-for="g in gameplays" :key="g.id" :value="String(g.id)">{{ g.name }}</NativeSelectOption></NativeSelect>
        <NativeSelect v-model="query.platform" aria-label="平台" @update:model-value="reload"><NativeSelectOption value="">全部平台</NativeSelectOption><NativeSelectOption value="MOBILE">手游</NativeSelectOption><NativeSelectOption value="PC">端游</NativeSelectOption></NativeSelect>
        <NativeSelect v-model="query.security_level" aria-label="难度" @update:model-value="reload"><NativeSelectOption value="">全部难度</NativeSelectOption><NativeSelectOption value="CONFIDENTIAL">机密</NativeSelectOption><NativeSelectOption value="TOP_SECRET">绝密</NativeSelectOption></NativeSelect>
        <NativeSelect :model-value="String(query.game_category)" aria-label="游戏类目" @update:model-value="value => { query.game_category = value ? Number(value) : ''; reload() }"><NativeSelectOption value="">全部游戏</NativeSelectOption><NativeSelectOption v-for="c in gameCats" :key="c.id" :value="String(c.id)">{{ c.name }}</NativeSelectOption></NativeSelect>
        <NativeSelect :model-value="String(query.service_category)" aria-label="分类" @update:model-value="value => { query.service_category = value ? Number(value) : ''; reload() }"><NativeSelectOption value="">全部分类</NativeSelectOption><NativeSelectOption v-for="c in playCats" :key="c.id" :value="String(c.id)">陪玩 · {{ c.name }}</NativeSelectOption><NativeSelectOption v-for="c in giftCats" :key="c.id" :value="String(c.id)">礼物 · {{ c.name }}</NativeSelectOption></NativeSelect><Button @click="reload">查询</Button>
      </div></template>
      <template #default="{ rows }"><div class="sv-desktop-table"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>封面</TableHead><TableHead>玩法</TableHead><TableHead>规格</TableHead><TableHead>名称</TableHead><TableHead>游戏类目</TableHead><TableHead>分类</TableHead><TableHead>要求档位</TableHead><TableHead>价格</TableHead><TableHead>下单方式</TableHead><TableHead>抽成配置</TableHead><TableHead>排序</TableHead><TableHead>状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell>{{ row.id }}</TableCell><TableCell><img v-if="row.cover_url" :src="row.cover_url" alt="" class="sv-cover" /><span v-else>—</span></TableCell><TableCell>{{ row.gameplay_name || '—' }}</TableCell><TableCell>{{ serviceSpec(row) }}</TableCell><TableCell>{{ row.name }}</TableCell><TableCell>{{ row.game_category_name || '—' }}</TableCell><TableCell>{{ row.service_category_name || '—' }}</TableCell><TableCell>{{ row.provider_grade ? (qualificationMeta.scopes.flatMap((s: any) => s.grades).find((g: any) => g.value === row.provider_grade)?.label || row.provider_grade) : (row.required_level_name || '不限') }}</TableCell><TableCell>{{ amountToXaCoin(row.price) }} 币 / {{ row.price_unit || '份' }}</TableCell><TableCell>{{ row.purchase_mode === 'CONSULT' ? '客服确认' : row.purchase_mode === 'DIRECT' ? '直接下单' : '待核对' }}</TableCell><TableCell>{{ row.is_gift ? '礼物独立规则' : row.commission_override ? commissionLabel(row) : '沿用原自动规则' }}</TableCell><TableCell>{{ row.sort_order }}</TableCell><TableCell><Badge variant="outline">{{ row.is_active ? '上架' : '下架' }}</Badge></TableCell><TableCell><div class="sv-row-actions"><Button v-if="auth.hasPerm('service:edit')" variant="link" :disabled="!metadataReady || !!saveReview" @click="onEdit(row)">编辑</Button><Button v-if="auth.hasPerm('service:delete')" variant="link" @click="onDelete(row)">删除</Button></div></TableCell></TableRow></TableBody></Table></div>
      <div class="sv-mobile-list" role="list" aria-label="服务商品列表"><article v-for="row in rows" :key="row.id" class="sv-mobile-card" role="listitem"><div class="sv-mobile-head"><strong>{{ row.name }}</strong><Badge variant="outline">{{ row.is_active ? '商品上架' : '商品下架' }}</Badge></div><p class="sv-mobile-sub">ID {{ row.id }} · {{ row.game_category_name || '未设置游戏' }} · {{ row.service_category_name || '未设置分类' }}<template v-if="row.gameplay_name"> · {{ row.gameplay_name }}</template></p><dl class="sv-mobile-values"><div><dt>价格</dt><dd>{{ amountToXaCoin(row.price) }} 币 / {{ row.price_unit || '份' }}{{ row.billing_provider_count === 2 ? ' · 每人' : '' }}</dd></div><div><dt>人数</dt><dd>{{ row.billing_provider_count === 2 ? '双陪' : row.billing_provider_count === 1 ? '单陪' : '待核对' }}</dd></div><div><dt>规格</dt><dd>{{ serviceSpec(row) }}</dd></div><div><dt>下单方式</dt><dd>{{ row.purchase_mode === 'CONSULT' ? '客服确认' : row.purchase_mode === 'DIRECT' ? '直接下单' : '待核对' }}</dd></div><div><dt>抽成</dt><dd>{{ row.is_gift ? '礼物独立规则' : row.commission_override ? commissionLabel(row) : '自动规则' }}</dd></div></dl><div class="sv-mobile-actions"><Button v-if="auth.hasPerm('service:edit')" variant="outline" :disabled="!metadataReady || !!saveReview" @click="onEdit(row)">编辑</Button><Button v-if="auth.hasPerm('service:delete')" variant="outline" @click="onDelete(row)">删除</Button></div></article></div>
      </template>
    </SimplePagedList>
  </div>

  <Dialog :open="dialogVisible" @update:open="value => { if (!value) closeServiceDialog() }"><DialogContent class="sv-dict-dialog" :show-close-button="!contentUploading && !saving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { event.preventDefault(); closeServiceDialog() }"><DialogHeader><DialogTitle>{{ form.id ? '编辑服务项' : '新增服务项' }}</DialogTitle><DialogDescription>配置价格、接单资格、权益和前台展示。</DialogDescription></DialogHeader>
    <p v-if="notice" role="status">{{ notice }}</p>
    <fieldset class="sv-service-form" :disabled="saving || saveUnknown" :inert="saving || saveUnknown || undefined">
      <p v-if="draftDirty && !saveUnknown" class="sv-draft-status" role="status">有未保存的修改</p>
      <div class="sv-section-heading"><h3>基础与价格</h3><p>先填写名称、单价和下单方式</p></div>
      <fieldset class="sv-core-fields ap-field--full" :disabled="saving || saveUnknown">
        <label>名称<Input ref="serviceNameInput" v-model="form.name" maxlength="100" :aria-invalid="saveAttempted && !validServiceName" /><span v-if="saveAttempted && !validServiceName" class="sv-field-error" role="alert">请输入名称，最多 100 字</span></label>
        <label>价格（兴安币）<Input ref="servicePriceInput" v-model="priceCoin" inputmode="decimal" maxlength="12" :aria-invalid="saveAttempted && servicePriceRaw(priceCoin) === null" /><span v-if="saveAttempted && servicePriceRaw(priceCoin) === null" class="sv-field-error" role="alert">请输入大于 0 的价格，最多一位小数</span></label>
        <label>计价单位<Input v-model="form.price_unit" maxlength="20" placeholder="份 / 小时 / 人·小时 / 点" /></label>
        <label>套餐人数与计价<NativeSelect :model-value="String(form.billing_provider_count)" @update:model-value="value => form.billing_provider_count = Number(value)"><NativeSelectOption value="1">单陪：1 名陪玩</NativeSelectOption><NativeSelectOption value="2">双陪：每人价格 × 2，2 名陪玩</NativeSelectOption></NativeSelect></label>
        <label>下单方式<NativeSelect v-model="form.purchase_mode"><NativeSelectOption value="DIRECT">直接下单</NativeSelectOption><NativeSelectOption value="CONSULT">客服确认</NativeSelectOption></NativeSelect></label>
        <label>确认说明<Input v-model="form.purchase_note" maxlength="255" placeholder="预约、资格、人数或加购条件" /></label>
        <label class="sv-switch">附加费用<Switch v-model="form.is_addon" aria-label="附加费用" /><Popover><PopoverTrigger as-child><button type="button" class="sv-help" aria-label="附加费用说明">?</button></PopoverTrigger><PopoverContent>关联主套餐使用，不能独立下单，也不计入玩法起价。</PopoverContent></Popover></label>
        <label class="sv-switch">上架<Switch v-model="form.is_active" aria-label="上架" /></label><p v-if="form.catalog_source" class="tip">价目来源：{{ form.catalog_source }}</p>
        <div class="sv-section-heading sv-core-full"><h3>所属与接单</h3><p>选择分类、玩法和陪玩资格</p></div>
        <label>游戏类目<NativeSelect :model-value="String(form.game_category ?? '')" @update:model-value="value => form.game_category = value ? Number(value) : null"><NativeSelectOption value="">不选择</NativeSelectOption><NativeSelectOption v-for="c in gameCats" :key="c.id" :value="String(c.id)">{{ c.name }}</NativeSelectOption></NativeSelect></label>
        <label>分类<NativeSelect :model-value="String(form.service_category ?? '')" @update:model-value="value => form.service_category = value ? Number(value) : null"><NativeSelectOption value="">不选择</NativeSelectOption><NativeSelectOption v-for="c in playCats" :key="c.id" :value="String(c.id)">陪玩 · {{ c.name }}</NativeSelectOption><NativeSelectOption v-for="c in giftCats" :key="c.id" :value="String(c.id)">礼物 · {{ c.name }}</NativeSelectOption></NativeSelect></label>
        <label v-if="!form.provider_kind"><span>历史服务档位 <Popover><PopoverTrigger as-child><button type="button" class="sv-help" aria-label="历史服务档位说明">?</button></PopoverTrigger><PopoverContent>仅该档位及以上的陪玩可接单；留空表示不限档位。</PopoverContent></Popover></span><NativeSelect :model-value="String(form.required_level ?? '')" @update:model-value="value => form.required_level = value ? Number(value) : null"><NativeSelectOption value="">不限档位</NativeSelectOption><NativeSelectOption v-for="l in escortLevels" :key="l.id" :value="String(l.id)">{{ l.name }}</NativeSelectOption></NativeSelect></label>
        <label>接单方向<NativeSelect v-model="form.provider_kind"><NativeSelectOption value="">不选择</NativeSelectOption><NativeSelectOption v-for="k in qualificationMeta.kinds" :key="k.value" :value="k.value">{{ k.label }}</NativeSelectOption></NativeSelect></label>
        <label>所属玩法<NativeSelect :model-value="String(form.gameplay ?? '')" @update:model-value="value => form.gameplay = value ? Number(value) : null"><NativeSelectOption value="">普通服务 / 礼物</NativeSelectOption><NativeSelectOption v-for="g in gameplays" :key="g.id" :value="String(g.id)">{{ g.name }}</NativeSelectOption></NativeSelect></label>
        <template v-if="form.gameplay || form.provider_kind"><label>游戏平台<NativeSelect v-model="form.platform"><NativeSelectOption value="MOBILE">手游</NativeSelectOption><NativeSelectOption value="PC">端游</NativeSelectOption></NativeSelect></label><label>行动难度<NativeSelect v-model="form.security_level"><NativeSelectOption value="CONFIDENTIAL">机密</NativeSelectOption><NativeSelectOption value="TOP_SECRET">绝密</NativeSelectOption><NativeSelectOption value="ANY">不限难度</NativeSelectOption></NativeSelect></label></template>
        <label v-if="form.provider_kind">订单档位<NativeSelect v-model="form.provider_grade"><NativeSelectOption value="">请选择档位</NativeSelectOption><NativeSelectOption v-for="g in orderGrades" :key="g.value" :value="g.value">{{ g.label }}</NativeSelectOption></NativeSelect><span class="tip">{{ orderCommission == null ? '同一范围高档可接低档；其他范围需另行认证。' : '此订单档位默认抽成 ' + orderCommission + '%；开启自定义抽成后按自定义配置。' }}</span></label>
      </fieldset>
      <fieldset class="sv-core-fields" :disabled="saving || saveUnknown">
        <template v-if="giftCats.some(c => c.id === form.service_category)"><label>每件冠名天数<Input v-model.number="form.gift_naming_days" type="number" min="0" max="3650" step="1" /><span class="tip">每件按数量累加，0 表示无冠名权益</span></label><label>节日限定<Input v-model="form.gift_festival" maxlength="40" placeholder="留空为常规礼物" /></label><p v-if="form.gift_festival" class="sv-core-full tip">限定起步价：21 元 / 210 兴安币，低于此价格无法上架赠送</p><label>礼物开售时间<Input type="datetime-local" step="1" :model-value="localDateTime(form.gift_starts_at)" @update:model-value="value => form.gift_starts_at = toDateTime(String(value))" /></label><label>礼物停售时间<Input type="datetime-local" step="1" :model-value="localDateTime(form.gift_ends_at)" @update:model-value="value => form.gift_ends_at = toDateTime(String(value))" /></label></template>
        <div class="sv-section-heading sv-core-full"><h3>抽成与权益</h3><p>按商品适用范围配置</p></div>
        <label v-if="!isGift" class="sv-core-full">商品自定义抽成<Switch v-model="commissionEnabled" aria-label="商品自定义抽成" /><span class="tip">{{ commissionEnabled ? '只调整抽成，不改变接单资格及价格；活动命中时以派单试算为准。' : '沿用原有自动抽成规则及等级建议。' }}</span></label>
        <CommissionEditor v-if="commissionEnabled && !isGift" v-model:type="commissionType" v-model:rate="commissionRate" v-model:fixed="commissionFixed" :disabled="saving || saveUnknown" class="sv-core-full" />
        <Alert v-if="isGift" class="sv-core-full"><AlertDescription>{{ commissionEnabled ? '礼物使用独立抽成，当前自定义抽成不会保存；切回陪玩可恢复。' : '礼物沿用独立比例规则，不使用订单自定义抽成。' }}</AlertDescription></Alert><Alert v-else-if="!commissionEnabled && commissionLocked" class="sv-core-full"><AlertDescription>当前沿用订单档位或体验自动抽成规则；开启商品自定义抽成后可明确覆盖。</AlertDescription></Alert>
        <label>体验资格类型<NativeSelect v-model="form.experience_kind"><NativeSelectOption value="">普通服务</NativeSelectOption><NativeSelectOption value="NEW_FIRST">新客首单：每周期一次</NativeSelectOption><NativeSelectOption value="NEW_VALUE">新客超值：领券后可见</NativeSelectOption><NativeSelectOption value="DAILY">每日一单：每平台每天一次</NativeSelectOption><NativeSelectOption value="DAILY_VALUE">每日超值：领券后可见，每平台每天一次</NativeSelectOption></NativeSelect><span v-if="form.experience_kind" class="tip">按海报专价，不参加任何活动；默认新客体验不抽成、每日体验抽成10%，自定义抽成仅覆盖抽成。</span></label>
        <label>服务项目类型<NativeSelect v-model="form.service_type"><NativeSelectOption value="REGULAR">常规单</NativeSelectOption><NativeSelectOption value="ACTIVITY_LIMITED">活动限定单</NativeSelectOption><NativeSelectOption value="PROMOTIONAL">促销单</NativeSelectOption><NativeSelectOption value="COMPANION">陪玩单</NativeSelectOption><NativeSelectOption value="EXPERIENCE">体验单</NativeSelectOption></NativeSelect></label>
        <label class="sv-switch">可使用折扣券<Switch v-model="form.allows_discount_coupons" aria-label="可使用折扣券" /><Popover><PopoverTrigger as-child><button type="button" class="sv-help" aria-label="折扣券说明">?</button></PopoverTrigger><PopoverContent>折扣券按此项目设置校验，默认关闭。</PopoverContent></Popover></label>
        <label>VIP权益绑定类型<NativeSelect v-model="form.benefit_kind"><NativeSelectOption value="">无</NativeSelectOption><NativeSelectOption value="COMPANION_HOUR">对应档位1小时陪玩</NativeSelectOption><NativeSelectOption value="SAFEBOX_33">33安全箱代肝主套餐</NativeSelectOption></NativeSelect></label>
        <label>默认订单抽成规则<NativeSelect v-model="form.commission_policy"><NativeSelectOption value="LEGACY">原有商品抽成规则</NativeSelectOption><NativeSelectOption value="EXPERIENCE">体验单：低于100元抽3元，100元及以上抽10%</NativeSelectOption><NativeSelectOption value="COMPANION">陪玩单：统一20%</NativeSelectOption><NativeSelectOption value="REGULAR">常规单：按陪玩等级</NativeSelectOption><NativeSelectOption value="FUN">趣味单：按陪玩等级</NativeSelectOption></NativeSelect></label>
        <div class="sv-section-heading sv-core-full"><h3>玩法与展示</h3><p>填写玩法内容、图片和排序</p></div>
        <template v-if="form.gameplay || form.provider_kind"><label class="sv-core-full">履约规则<Textarea v-model="form.rules" :rows="4" placeholder="明确服务目标、完成条件与售后方式；留空沿用玩法规则" /></label><div v-if="isRoulette" class="sv-core-full">转盘目标<RoulettePoolEditor v-model="form.roulette_pool" /></div></template>
        <label>同款玩法<Input v-model="form.catalog_family" maxlength="100" placeholder="如：常规物资单" /></label><label>档位名称<Input v-model="form.catalog_variant" maxlength="150" placeholder="如：1档 · 保底866w" /></label>
        <div class="sv-core-full">价目下方玩法详情 · 富文本<CatalogEditor v-model="form.details_html" @uploading="contentUploading = $event" hint="填写当前价目的玩法文案与价目海报；留空使用所属玩法的通用详情。" /></div>
        <label class="sv-core-full">玩法简介广告<Textarea v-model="introductionImagesText" :rows="3" placeholder="每行一张已上传广告页地址，在玩法简介显示" /></label><label class="sv-core-full">玩法规则图<Textarea v-model="rulesImagesText" :rows="3" placeholder="每行一张已上传图片地址；与完整文字规则共同展示" /></label>
        <label>描述<Input v-model="form.description" /></label><label>封面URL<Input v-model="form.cover_url" /></label><label class="sv-core-full">图集<Textarea v-model="imagesText" :rows="2" placeholder="每行一个图片URL" /></label><label class="sv-core-full">卖点<Textarea v-model="highlightsText" :rows="2" placeholder="每行一个卖点文案" /></label><label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" /></label>
      </fieldset>
    </fieldset>
    <Alert v-if="saveError" :variant="saveUnknown ? 'default' : 'destructive'"><AlertDescription>{{ saveError }}</AlertDescription></Alert><DialogFooter><Button variant="outline" :disabled="contentUploading || saving" @click="closeServiceDialog()">{{ saveUnknown ? '关闭并核对' : '取消' }}</Button><Button :disabled="contentUploading || saving || !metadataReady || saveUnknown" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter>
    </DialogContent></Dialog>

  <Dialog :open="gameCatDialogVisible" @update:open="value => { if (!gcSaving) { gameCatDialogVisible = value; if (!value) onGameCatDialogClosed() } }"><DialogContent class="sv-dict-dialog" :show-close-button="!gcSaving" @interact-outside="event => { if (gcSaving) event.preventDefault() }" @escape-key-down="event => { if (gcSaving) event.preventDefault() }"><DialogHeader><DialogTitle>游戏类目设置</DialogTitle><DialogDescription>排序越小越靠前；停用后老板端目录和陪玩技能选项隐藏，已有订单与档案保留。</DialogDescription></DialogHeader>
    <p v-if="notice" role="status">{{ notice }}</p><div class="dict-head"><span class="dict-tip">选择类目编辑，或新增类目</span><Button :disabled="gcSaving" @click="startCreateGameCat">新增类目</Button></div><div class="gc-layout"><div class="gc-list"><button v-for="cat in gameCats" :key="cat.id" type="button" :disabled="gcSaving" :class="['gc-list-item', gcEditing.id === cat.id ? 'active' : '']" @click="startEditGameCat(cat)"><img v-if="cat.icon_url" :src="cat.icon_url" alt="" class="gc-list-icon" /><span v-else class="gc-list-icon gc-list-icon--empty">图</span><span class="gc-list-main"><span class="gc-list-name">{{ cat.name }}</span><span class="gc-list-remark">排序 {{ cat.sort_order }} · {{ cat.active_service_count || 0 }}/{{ cat.service_count || 0 }} 项展示 · {{ cat.escort_count || 0 }} 位陪玩</span></span><Badge variant="outline">{{ cat.is_active ? '启用' : '停用' }}</Badge></button><p v-if="!gameCats.length">暂无游戏类目</p></div>
      <div v-if="gcEditing.visible" class="gc-form"><div class="gc-form-title">{{ gcEditing.id ? '编辑类目' : '新增类目' }}</div><fieldset class="sv-dict-fields" :disabled="gcSaving"><label>图标<img v-if="gcIconPreview" :src="gcIconPreview" alt="图标预览" class="gc-icon-preview" /><input type="file" accept="image/*" @change="onGcIconChange" /><span class="tip">建议正方形图片，未上传则展示默认图标</span></label><label>名称<Input v-model="gcEditing.name" placeholder="如：王者荣耀" /></label><label>备注<Input v-model="gcEditing.remark" placeholder="可选" /></label><label>排序<Input v-model.number="gcEditing.sort_order" type="number" min="0" step="1" /></label><label class="sv-switch">启用<Switch v-model="gcEditing.is_active" aria-label="启用游戏类目" /></label></fieldset><div class="gc-form-footer"><Button v-if="gcEditing.id" variant="outline" :disabled="gcSaving" @click="deleteGameCat(gcEditing)">删除</Button><div class="gc-form-footer-right"><Button variant="outline" :disabled="gcSaving" @click="cancelGameCatEdit">取消</Button><Button :disabled="gcSaving" @click="saveGameCat">{{ gcSaving ? '保存中…' : '保存' }}</Button></div></div></div><div v-else class="gc-form gc-form--placeholder">从左侧选择类目进行编辑，或点击「新增类目」</div>
    </div></DialogContent></Dialog>
  <Dialog v-model:open="serviceCatDialogVisible"><DialogContent class="sv-dict-dialog"><DialogHeader><DialogTitle>分类设置</DialogTitle><DialogDescription>陪玩与礼物分类分别维护；礼物子类用于概览和促销范围匹配。</DialogDescription></DialogHeader><p v-if="notice" role="status">{{ notice }}</p><div class="sc-groups"><section v-for="group in scGroups" :key="group.key" class="sc-group"><div class="sc-group-head"><strong class="sc-group-title">{{ group.title }}</strong><Button variant="outline" @click="addServiceCatRow(group.isGift)">新增{{ group.title }}子类</Button></div><p v-if="!rowsOfGroup(group.isGift).length">暂无子类</p><article v-for="(row, index) in rowsOfGroup(group.isGift)" :key="row.id || `new-${group.key}-${index}`" class="sv-category-row"><div class="sv-category-fields"><label>子类名称<Input v-model="row.name" :placeholder="group.placeholder" /></label><label>订单类型<NativeSelect v-model="row.order_kind"><NativeSelectOption value="">其他</NativeSelectOption><NativeSelectOption value="GUARD">护航</NativeSelectOption><NativeSelectOption value="COMPANION">陪玩</NativeSelectOption><NativeSelectOption value="GRIND">代肝</NativeSelectOption><NativeSelectOption value="RANK">上分</NativeSelectOption></NativeSelect></label><label>备注<Input v-model="row.remark" /></label><label>排序<Input v-model.number="row.sort_order" type="number" min="0" step="1" /></label><label class="sv-switch">启用<Switch v-model="row.is_active" :aria-label="`${row.name || '新分类'}启用`" /></label></div><div class="sv-row-actions"><Button variant="outline" @click="saveServiceCat(row)">保存</Button><Button variant="outline" @click="deleteServiceCat(row)">删除</Button></div></article></section></div></DialogContent></Dialog>

  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import RoulettePoolEditor from '@/components/RoulettePoolEditor.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Switch } from '@xa/shadcn-vue/switch'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Popover, PopoverContent, PopoverTrigger } from '@xa/shadcn-vue/popover'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import CatalogEditor from '@/components/CatalogEditor.vue'
import CommissionEditor from '@/components/CommissionEditor.vue'
import { gameplayApi, serviceItemApi, gameCategoryApi, serviceCategoryApi, escortLevelApi } from '@/api/modules'
import { request } from '@/api/request'
import { qualificationOptions,scopeGrades } from '@/utils/qualifications'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { commissionError, commissionLabel, commissionPayload, readCommission, type CommissionType } from '@/utils/commission'
import { useAuthStore } from '@/stores/auth'

const notice = ref('')
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const auth = useAuthStore()
const tableRef = ref()
const route = useRoute()
const query = reactive<Record<string, any>>({ game_category: '', service_category: '', gameplay: Number(route.query.gameplay) || '', platform: '', security_level: '', keyword: '', item_id: '' })
const gameplays = ref<any[]>([])
const qualificationMeta = ref<any>({ kinds: [], scopes: [] })

const gameCats = ref<any[]>([])
function selectGame(id: number | string) { query.game_category = id; tableRef.value?.reload() }
const serviceCats = ref<any[]>([])
const escortLevels = ref<any[]>([])
const dictLoading = ref(true)
const dictLoaded = ref(false)
const dictFailed = ref<string[]>([])
const metadataReady = computed(() => dictLoaded.value && !dictLoading.value && !dictFailed.value.length)
let dictSequence = 0
let pageDisposed = false
let pageMounted = false

function serviceSpec(row: any) {
  if (!row.platform && !row.security_level) return '—'
  const platform = row.platform === 'MOBILE' ? '手游' : row.platform === 'PC' ? '端游' : '平台待核对'
  const level = row.security_level === 'CONFIDENTIAL' ? '机密' : row.security_level === 'TOP_SECRET' ? '绝密' : row.security_level === 'ANY' ? '不限难度' : '难度待核对'
  return `${platform} / ${level}`
}

// 分类按顶层归属派生：陪玩(is_gift=false) / 礼物(is_gift=true)
const playCats = computed(() => serviceCats.value.filter((c) => !c.is_gift))
const giftCats = computed(() => serviceCats.value.filter((c) => c.is_gift))

async function loadDicts() {
  const sequence = ++dictSequence, revision = auth.sessionRevision
  dictLoading.value = true
  const results = await Promise.allSettled([
    gameCategoryApi.all(), serviceCategoryApi.all(), escortLevelApi.allOptions(), gameplayApi.all(), qualificationOptions(),
  ])
  if (pageDisposed || sequence !== dictSequence || revision !== auth.sessionRevision || !auth.hasPerm('service:view')) return
  const names = ['游戏类目', '服务分类', '陪玩等级', '玩法', '接单资格']
  const lists = [gameCats, serviceCats, escortLevels, gameplays]
  const failed: string[] = []
  for (let i = 0; i < 4; i++) {
    const result = results[i]
    const list = result.status === 'fulfilled' ? (result.value as any)?.data?.list : null
    if (Array.isArray(list)) lists[i].value = list
    else { lists[i].value = []; failed.push(names[i]) }
  }
  const qualifications = results[4]
  if (qualifications.status === 'fulfilled' && Array.isArray((qualifications.value as any)?.kinds) && Array.isArray((qualifications.value as any)?.scopes)) qualificationMeta.value = qualifications.value
  else { qualificationMeta.value = { kinds: [], scopes: [] }; failed.push(names[4]) }
  dictFailed.value = failed
  dictLoaded.value = true
  dictLoading.value = false
}

onMounted(() => { pageMounted = true; window.addEventListener('beforeunload', onServiceBeforeUnload); if (auth.hasPerm('service:view')) void loadDicts() })
onUnmounted(() => { pageDisposed = true; ++dictSequence; window.removeEventListener('beforeunload', onServiceBeforeUnload) })
watch([() => auth.sessionRevision, () => auth.profile?.id, () => auth.hasPerm('service:view')], () => {
  ++dictSequence
  dictLoaded.value = false
  dictLoading.value = false
  dictFailed.value = []
  gameCats.value = []; serviceCats.value = []; escortLevels.value = []; gameplays.value = []
  qualificationMeta.value = { kinds: [], scopes: [] }
  dialogVisible.value = false
  saving.value = false
  if (pageMounted && auth.hasPerm('service:view')) void loadDicts()
}, { flush: 'sync' })

const dialogVisible = ref(false)
const saving = ref(false)
const contentUploading = ref(false)
const priceCoin = ref('')
const saveError = ref('')
const saveUnknown = ref(false)
const saveAttempted = ref(false)
const serviceNameInput = ref<any>()
const servicePriceInput = ref<any>()
const saveReview = ref<{ id: number | null; name: string } | null>(null)
const saveReviewSearched = ref(false)
let saveReviewOwnerKey = ''
const commissionRate = ref<number | undefined>(undefined)
const commissionType = ref<CommissionType>('PERCENT')
const commissionFixed = ref<number | undefined>(0)
const commissionEnabled = ref(false)
const introductionImagesText = ref('')
const rulesImagesText = ref('')
const imagesText = ref('')
const highlightsText = ref('')
const form = reactive<any>({ roulette_pool: [] })
const initialDraft = ref<string | null>(null)
const draftFields = ['id', 'experience_kind', 'benefit_kind', 'service_type', 'allows_discount_coupons', 'commission_policy', 'billing_provider_count', 'gift_naming_days', 'gift_festival', 'gift_starts_at', 'gift_ends_at', 'catalog_family', 'catalog_variant', 'price_unit', 'purchase_mode', 'purchase_note', 'is_addon', 'provider_kind', 'provider_grade', 'gameplay', 'platform', 'security_level', 'rules', 'details_html', 'rules_images', 'introduction_images', 'roulette_pool', 'name', 'description', 'game_category', 'service_category', 'required_level', 'cover_url', 'sort_order', 'is_active']
function draftSignature() {
  return JSON.stringify({ fields: draftFields.map(key => form[key] ?? null), price: priceCoin.value, commissionEnabled: commissionEnabled.value, commissionType: commissionType.value, commissionRate: commissionRate.value ?? null, commissionFixed: commissionFixed.value ?? null, introductionImages: introductionImagesText.value, rulesImages: rulesImagesText.value, images: imagesText.value, highlights: highlightsText.value })
}
const draftDirty = computed(() => initialDraft.value !== null && draftSignature() !== initialDraft.value)
let discardPromptOpen = false
async function confirmDiscardDraft() {
  if (!draftDirty.value || saveUnknown.value || !auth.hasPerm('service:edit')) return true
  if (discardPromptOpen) return false
  discardPromptOpen = true
  try { return await askConfirm({ title: '放弃修改？', description: '关闭后未保存的商品修改将丢失。', confirmText: '放弃修改', cancelText: '继续编辑' }) }
  finally { discardPromptOpen = false }
}
async function closeServiceDialog(done?: () => void) {
  if (saving.value || contentUploading.value || !dialogVisible.value) return
  const revision = auth.sessionRevision
  if (!await confirmDiscardDraft() || pageDisposed || revision !== auth.sessionRevision || !dialogVisible.value) return
  if (done) done()
  else dialogVisible.value = false
}
onBeforeRouteLeave(async () => {
  if (!dialogVisible.value) return true
  if (saving.value || contentUploading.value) { notice.value = '正在保存或上传，请稍候'; return false }
  return confirmDiscardDraft()
})
function onServiceBeforeUnload(event: BeforeUnloadEvent) {
  if (dialogVisible.value && auth.hasPerm('service:edit') && (saving.value || contentUploading.value || (draftDirty.value && !saveUnknown.value))) {
    event.preventDefault(); event.returnValue = ''
  }
}
function localDateTime(value: string | null | undefined) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
function toDateTime(value: string) { if (!value) return null; const date = new Date(value); return Number.isNaN(date.getTime()) ? null : date.toISOString() }
const validServiceName = computed(() => typeof form.name === 'string' && form.name.trim().length > 0 && form.name.trim().length <= 100)
function servicePriceRaw(value: unknown): number | null {
  const text = String(value ?? '').trim()
  if (!/^(?:0|[1-9][0-9]*)(?:\.[0-9])?$/.test(text)) return null
  const raw = xaCoinToAmount(text)
  return Number.isSafeInteger(raw) && raw > 0 && raw <= 2147483647 ? raw : null
}
function saveReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_service_save_review_${id}` : ''
}
function restoreSaveReview() {
  const key = saveReviewKey()
  if (key === saveReviewOwnerKey) return
  saveReviewOwnerKey = key
  saveReview.value = null
  saveReviewSearched.value = false
  if (!key) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if ((marker?.id === null || (Number.isSafeInteger(marker?.id) && marker.id > 0)) && typeof marker.name === 'string' && marker.name.length > 0 && marker.name.length <= 100) saveReview.value = marker
  } catch { /* Current page still guards an unresolved request. */ }
}
function beginSaveReview(id: number | null, name: string) {
  saveReview.value = { id, name }; saveReviewSearched.value = false
  const key = saveReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id, name })) } catch { /* Keep the page guarded. */ }
}
function clearSaveReview() {
  const key = saveReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage can be unavailable. */ }
  saveReview.value = null; saveReviewSearched.value = false
}
function locateSaveReview() {
  const review = saveReview.value
  if (!review || saving.value) return
  Object.assign(query, { game_category: '', service_category: '', gameplay: '', platform: '', security_level: '', item_id: review.id ? String(review.id) : '', keyword: review.id ? '' : review.name })
  saveReviewSearched.value = true
  tableRef.value?.reload()
}
async function finishSaveReview() {
  const review = saveReview.value, revision = auth.sessionRevision
  if (!review || !saveReviewSearched.value || saving.value) return
  if (!await askConfirm({ title: '解除保存限制', description: '请确认已核对商品及请求处理情况。同名或归档商品需另行核实，请求可能仍在处理。', confirmText: '已核对，解除限制', cancelText: '继续核对' })) return
  if (!pageDisposed && revision === auth.sessionRevision && saveReview.value === review && saveReviewSearched.value) clearSaveReview()
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreSaveReview, { immediate: true, flush: 'sync' })
const isGift = computed(() => Boolean(form.service_category && (serviceCats.value.find(c => c.id === form.service_category)?.is_gift ?? form.is_gift)))
watch(() => form.experience_kind, kind => { if (kind) form.service_type = 'EXPERIENCE' })
const orderGrades = computed(() => scopeGrades(qualificationMeta.value,form))
const orderCommission = computed(() => orderGrades.value.find((g: any) => g.value === form.provider_grade)?.commission_rate)
const commissionLocked = computed(() => orderCommission.value != null || Boolean(form.experience_kind) || ['EXPERIENCE', 'COMPANION'].includes(form.commission_policy))
watch(() => [form.provider_kind,form.platform,form.security_level], () => { if (form.provider_kind && form.provider_kind !== 'GUARD') form.security_level='ANY'; if (form.provider_kind && !orderGrades.value.some((g: any) => g.value === form.provider_grade)) form.provider_grade='' })
const isRoulette = computed(() => gameplays.value.find(item => item.id === form.gameplay)?.kind === 'ROULETTE')
watch(isRoulette, value => { if (value && !form.roulette_pool?.length) form.roulette_pool = [{ name: '', description: '', weight: 1 }, { name: '', description: '', weight: 1 }] })

function blank() {
  return { id: 0, experience_kind: '', benefit_kind: '', service_type: 'REGULAR', allows_discount_coupons: false, commission_policy: 'LEGACY', billing_provider_count: 1, gift_naming_days: 0, gift_festival: '', gift_starts_at: null, gift_ends_at: null, catalog_family: '', catalog_variant: '', price_unit: '份', purchase_mode: 'DIRECT', purchase_note: '', is_addon: false, catalog_source: '', catalog_key: null, provider_kind: '', provider_grade: '', gameplay: query.gameplay || null, platform: '', security_level: '', rules: '', details_html: '', rules_images: [], introduction_images: [], roulette_pool: [], name: '', description: '', game_category: null, service_category: null, required_level: null, cover_url: '', sort_order: 0, is_active: true }
}

function toLines(arr: any): string {
  return Array.isArray(arr) ? arr.join('\n') : ''
}

function fromLines(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean)
}

async function onCreate() {
  if (!metadataReady.value || saveReview.value) return
  notice.value = ''
  initialDraft.value = null
  Object.assign(form, blank())
  if (isRoulette.value) form.roulette_pool = [{ name: '', description: '', weight: 1 }, { name: '', description: '', weight: 1 }]
  priceCoin.value = ''
  saveError.value = ''; saveUnknown.value = false; saveAttempted.value = false
  commissionRate.value = 0
  commissionType.value = 'PERCENT'
  commissionFixed.value = 0
  commissionEnabled.value = false
  introductionImagesText.value = ''
  rulesImagesText.value = ''
  imagesText.value = ''
  highlightsText.value = ''
  dialogVisible.value = true
  const revision = auth.sessionRevision
  await nextTick()
  if (!pageDisposed && revision === auth.sessionRevision && dialogVisible.value && !form.id) initialDraft.value = draftSignature()
}

async function onEdit(row: any) {
  if (!metadataReady.value || saveReview.value) return
  notice.value = ''
  initialDraft.value = null
  Object.assign(form, JSON.parse(JSON.stringify(row)))
  priceCoin.value = amountToXaCoin(row.price)
  saveError.value = ''; saveUnknown.value = false; saveAttempted.value = false
  const commission = readCommission(row)
  commissionType.value = commission.type
  commissionRate.value = commission.rate
  commissionFixed.value = commission.fixed
  commissionEnabled.value = row.commission_override === true
  introductionImagesText.value = toLines(row.introduction_images)
  rulesImagesText.value = toLines(row.rules_images)
  imagesText.value = toLines(row.images)
  highlightsText.value = toLines(row.highlights)
  dialogVisible.value = true
  const revision = auth.sessionRevision, itemId = row.id
  await nextTick()
  if (!pageDisposed && revision === auth.sessionRevision && dialogVisible.value && form.id === itemId) initialDraft.value = draftSignature()
}

async function onSave() {
  if (!metadataReady.value || saving.value || contentUploading.value || saveReview.value || saveUnknown.value || !auth.hasPerm('service:edit')) return
  saveError.value = ''
  saveAttempted.value = true
  const price = servicePriceRaw(priceCoin.value)
  if (!validServiceName.value) { await nextTick(); serviceNameInput.value?.$el?.focus(); return }
  if (price === null) { await nextTick(); servicePriceInput.value?.$el?.focus(); return }
  const overrideEnabled = commissionEnabled.value && !isGift.value
  if (form.provider_kind && !form.provider_grade) { notice.value = '请配置有效的订单档位'; return }
  if (overrideEnabled) {
    const error = commissionError(commissionType.value, commissionRate.value, commissionFixed.value)
    if (error) { notice.value = error; return }
  }
  const revision = auth.sessionRevision
  const itemId = form.id || null
  beginSaveReview(itemId, form.name.trim())
  saving.value = true
  try {
    const payload = {
      provider_kind: form.provider_kind || '', provider_grade: form.provider_kind ? form.provider_grade : '',
      gameplay: form.gameplay || null, platform: form.gameplay || form.provider_kind ? form.platform : '', security_level: form.gameplay || form.provider_kind ? form.security_level : '',
      rules: form.rules, details_html: form.details_html || '', roulette_pool: isRoulette.value ? form.roulette_pool : [],
      name: form.name.trim(),
      gift_naming_days: form.gift_naming_days || 0, gift_festival: form.gift_festival || '', gift_starts_at: form.gift_starts_at || null, gift_ends_at: form.gift_ends_at || null,
      description: form.description,
      game_category: form.game_category || null,
      service_category: form.service_category || null,
      required_level: form.provider_kind ? null : form.required_level || null,
      price,
      price_unit: form.price_unit || '份', purchase_mode: form.purchase_mode || 'DIRECT', purchase_note: form.purchase_note || '', is_addon: !!form.is_addon,
      billing_provider_count: form.billing_provider_count || 1,
      catalog_family: form.catalog_family || '', catalog_variant: form.catalog_variant || '',
      ...(overrideEnabled ? commissionPayload(commissionType.value, commissionRate.value, commissionFixed.value) : {}),
      commission_override: overrideEnabled,
      commission_policy: form.commission_policy || 'LEGACY', experience_kind: form.experience_kind || '', benefit_kind: form.benefit_kind || '', service_type: form.service_type || 'REGULAR', allows_discount_coupons: !!form.allows_discount_coupons,
      cover_url: form.cover_url,
      rules_images: fromLines(rulesImagesText.value),
      introduction_images: fromLines(introductionImagesText.value),
      images: fromLines(imagesText.value),
      highlights: fromLines(highlightsText.value),
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    await request({ url: itemId ? `/service-items/${itemId}/` : '/service-items/', method: itemId ? 'patch' : 'post', data: payload, silent: true })
    if (pageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('service:edit') || !dialogVisible.value) return
    clearSaveReview()
    initialDraft.value = null
    notice.value = '保存成功'
    dialogVisible.value = false
    tableRef.value.reload()
  } catch (error: any) {
    if (!pageDisposed && revision === auth.sessionRevision && auth.hasPerm('service:edit') && dialogVisible.value) {
      const body = error?.response?.data || error
      const status = error?.response?.status
      const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
      if (rejected) {
        clearSaveReview()
        saveError.value = `保存未完成：${typeof body?.msg === 'string' && body.msg ? body.msg : '请求被拒绝，请核对输入和权限'}`
      } else {
        saveUnknown.value = true
        saveError.value = '保存结果未确认。请关闭并核对商品后再操作。'
      }
    }
  } finally {
    if (revision === auth.sessionRevision) saving.value = false
  }
}

async function onDelete(row: any) {
  if (!await askConfirm({ title: '删除服务项', description: `确定删除服务项「${row.name}」吗？`, confirmText: '删除' })) return
  await serviceItemApi.remove(row.id)
  notice.value = '已删除'
  tableRef.value.reload()
}

// ---------------- 游戏类目字典 ----------------
const gameCatDialogVisible = ref(false)
const gcSaving = ref(false)
const gcIconPreview = ref('')
const gcIconFile = ref<File | null>(null)
const gcEditing = reactive<any>({ visible: false, id: 0, name: '', remark: '', sort_order: 0, is_active: true })

function openGameCatDialog() {
  if (!metadataReady.value) return
  notice.value = ''
  resetGameCatEdit()
  gameCatDialogVisible.value = true
}

function resetGameCatEdit() {
  Object.assign(gcEditing, { visible: false, id: 0, name: '', remark: '', sort_order: 0, is_active: true })
  gcIconPreview.value = ''
  gcIconFile.value = null
}

function startCreateGameCat() {
  Object.assign(gcEditing, { visible: true, id: 0, name: '', remark: '', sort_order: 0, is_active: true })
  gcIconPreview.value = ''
  gcIconFile.value = null
}

function startEditGameCat(cat: any) {
  Object.assign(gcEditing, {
    visible: true,
    id: cat.id,
    name: cat.name,
    remark: cat.remark,
    sort_order: cat.sort_order,
    is_active: cat.is_active,
  })
  gcIconPreview.value = cat.icon_url || ''
  gcIconFile.value = null
}

function cancelGameCatEdit() {
  resetGameCatEdit()
}

function onGameCatDialogClosed() {
  resetGameCatEdit()
}

function onGcIconChange(event: Event) {
  const raw = (event.target as HTMLInputElement).files?.[0]
  if (!raw) return
  gcIconFile.value = raw
  gcIconPreview.value = URL.createObjectURL(raw)
}

async function saveGameCat() {
  if (!gcEditing.name?.trim()) { notice.value = '请填写名称'; return }
  gcSaving.value = true
  try {
    const fd = new FormData()
    fd.append('name', gcEditing.name.trim())
    fd.append('remark', gcEditing.remark || '')
    fd.append('sort_order', String(gcEditing.sort_order))
    fd.append('is_active', String(gcEditing.is_active))
    if (gcIconFile.value) fd.append('icon', gcIconFile.value)
    if (gcEditing.id) await gameCategoryApi.update(gcEditing.id, fd)
    else await gameCategoryApi.create(fd)
    notice.value = '已保存'
    await loadDicts()
    resetGameCatEdit()
  } finally {
    gcSaving.value = false
  }
}

async function deleteGameCat(cat: any) {
  if (!cat.id) { resetGameCatEdit(); return }
  if (!await askConfirm({ title: '删除类目', description: `确定删除类目「${cat.name}」吗？`, confirmText: '删除' })) return
  await gameCategoryApi.remove(cat.id)
  notice.value = '已删除'
  await loadDicts()
  resetGameCatEdit()
}

// ---------------- 服务分类字典（两级：陪玩/礼物 顶层固定，组内维护子类） ----------------
const serviceCatDialogVisible = ref(false)
const serviceCatRows = ref<any[]>([])

const scGroups = [
  { key: 'play', title: '陪玩', isGift: false, placeholder: '如：排位' },
  { key: 'gift', title: '礼物', isGift: true, placeholder: '如：限定' },
]

function rowsOfGroup(isGift: boolean) {
  return serviceCatRows.value.filter((r) => !!r.is_gift === isGift)
}

function openServiceCatDialog() {
  if (!metadataReady.value) return
  notice.value = ''
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
  serviceCatDialogVisible.value = true
}

function addServiceCatRow(isGift: boolean) {
  serviceCatRows.value.unshift({ id: 0, name: '', is_gift: isGift, order_kind: '', remark: '', sort_order: 0, is_active: true })
}

async function saveServiceCat(row: any) {
  if (!row.name?.trim()) { notice.value = '请填写名称'; return }
  const payload = { name: row.name, is_gift: row.is_gift, order_kind: row.order_kind || '', remark: row.remark, sort_order: row.sort_order, is_active: row.is_active }
  if (row.id) await serviceCategoryApi.update(row.id, payload)
  else await serviceCategoryApi.create(payload)
  notice.value = '已保存'
  await loadDicts()
  if (!dictFailed.value.includes('服务分类')) serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
}

async function deleteServiceCat(row: any) {
  if (!row.id) { serviceCatRows.value = serviceCatRows.value.filter((r) => r !== row); return }
  if (!await askConfirm({ title: '删除分类', description: `确定删除分类「${row.name}」吗？`, confirmText: '删除' })) return
  await serviceCategoryApi.remove(row.id)
  notice.value = '已删除'
  await loadDicts()
  if (!dictFailed.value.includes('服务分类')) serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
}
</script>

<style scoped lang="scss">
:global(.sv-dict-dialog) { width: min(880px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); overflow-y: auto; }
.sv-service-form { display: grid; gap: 16px; min-width: 0; border: 0; padding: 0; margin: 0; }
.sv-core-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; border: 0; padding: 0; margin: 0; min-width: 0; }
.sv-core-fields > label { display: grid; gap: 4px; min-width: 0; }
.sv-core-full { grid-column: 1 / -1; }
.sv-field-error { color: var(--destructive); font-size: 12px; }
@media (max-width: 480px) { .sv-service-form { display: grid; gap: 16px; min-width: 0; border: 0; padding: 0; margin: 0; }
.sv-core-fields { grid-template-columns: 1fr; } }
.sv-dict-fields, .sv-category-fields { display: grid; gap: 8px; border: 0; padding: 0; margin: 0; min-width: 0; }
.sv-dict-fields label, .sv-category-fields label { display: grid; gap: 4px; min-width: 0; }
.sv-category-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.sv-category-row { display: grid; gap: 8px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; }
.sv-switch { display: flex !important; align-items: center; gap: 8px; }
@media (max-width: 480px) { .sv-category-fields { grid-template-columns: 1fr; } }
.sv-filters, .sv-row-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.sv-filters > * { flex: 1; min-width: 120px; }
.sv-filters > button { flex: none; }
.sv-cover { width: 44px; height: 44px; object-fit: cover; border-radius: 4px; }
.sv-status-note { color: var(--muted-foreground); font-size: 12px; }
.sv-mobile-list { display: none; }
@media (max-width: 720px) { .sv-desktop-table { display: none; } .sv-mobile-list { display: grid; } }
.catalog-strip { display:flex; gap:12px; overflow-x:auto; padding-bottom:14px; margin-bottom:12px; }
.catalog-tile { flex:0 0 200px; display:flex; flex-direction:column; align-items:flex-start; gap:8px; padding:18px; background:var(--card); color:var(--foreground); border:1px solid var(--border); border-radius:14px; cursor:pointer; text-align:left; }
.catalog-tile > span { font-weight:600; }
.catalog-tile strong { font-size:25px; font-weight:650; }
.catalog-tile small { font-size:12px; color:var(--muted-foreground); font-weight:400; }
.catalog-tile--active { border-color:var(--primary); box-shadow:inset 0 0 0 1px var(--primary); }
.catalog-tile--active > span { color:var(--primary); }

.sv-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.sv-page :deep(.page-container) {
  padding: 0;
}
.sv-mobile-list { gap: 12px; }
.sv-mobile-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.sv-mobile-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.sv-mobile-head strong { min-width: 0; overflow-wrap: anywhere; color: var(--foreground); font-size: 15px; }
.sv-mobile-sub { margin: 10px 0 12px; color: var(--muted-foreground); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.sv-mobile-values { display: grid; gap: 8px; margin: 0; padding: 12px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.sv-mobile-values > div { display: flex; justify-content: space-between; gap: 12px; }
.sv-mobile-values dt { color: var(--muted-foreground); }
.sv-mobile-values dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.sv-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.sv-save-error { margin-bottom: 12px; text-align: left; }
.sv-draft-status { margin: 0; color: var(--muted-foreground); font-size: 12px; }
.sv-section-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; padding: 14px 0 8px; border-bottom: 1px solid var(--border); }
.sv-section-heading:first-child { padding-top: 0; }
.sv-section-heading h3 { margin: 0; color: var(--foreground); font-size: 15px; font-weight: 650; }
.sv-section-heading p { margin: 0; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
.sv-help { display: inline-flex; align-items: center; justify-content: center; width: var(--control-height-sm); height: var(--control-height-sm); margin-left: 4px; border: 0; border-radius: 50%; background: transparent; color: var(--primary); cursor: pointer; font-size: 15px; font-weight: 650; }
.sv-help:hover { background: var(--accent); }
.sv-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}
.muted {
  color: var(--muted-foreground);
}
.dict-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
.dict-tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
.gc-layout {
  display: flex;
  gap: 16px;
  align-items: stretch;
}
.gc-list {
  width: 320px;
  flex-shrink: 0;
  max-height: 420px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px;
}
.gc-list-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
}
.gc-list-item:hover {
  background: var(--secondary);
}
.gc-list-item.active {
  background: color-mix(in srgb, var(--primary) 10%, var(--card));
}
.gc-list-icon {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  flex-shrink: 0;
  background: var(--accent);
}
.gc-list-icon--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted-foreground);
}
.gc-list-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.gc-list-name {
  font-size: 14px;
  color: var(--foreground);
}
.gc-list-remark {
  font-size: 12px;
  color: var(--muted-foreground);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gc-form {
  container: admin-modal / inline-size;
  flex: 1;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
}
.gc-form--placeholder {
  align-items: center;
  justify-content: center;
}
.gc-form-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 16px;
}
.gc-icon-edit {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
}
.gc-icon-preview {
  width: 64px;
  height: 64px;
  border-radius: 8px;
  flex-shrink: 0;
  border: 1px solid var(--border);
  background: var(--accent);
}
.gc-icon-preview--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted-foreground);
}
.gc-icon-ops {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  min-width: 0;
  padding-top: 2px;
}
.gc-icon-ops .tip {
  font-size: 12px;
  line-height: 1.4;
  color: var(--muted-foreground);
}
.gc-form-footer {
  margin-top: auto;
  padding-top: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.gc-form-footer-right {
  margin-left: auto;
}
.sc-groups {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.sc-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 8px;
}

@media (max-width: 720px) {
  .gc-layout { flex-direction: column; }
  .gc-list { width: 100%; max-height: 200px; }
  .gc-form { flex: auto; }
  .gc-form-footer, .gc-form-footer-right { display: flex; flex-wrap: wrap; gap: 10px; }
}
.sc-group-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--foreground);
}
</style>
