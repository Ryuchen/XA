<template>
  <div class="sv-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">维护游戏类目与服务分类字典，配置分类下的项目 / 礼物、单价与抽成</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('service:edit')" @click="openGameCatDialog">类目设置</el-button>
        <el-button v-if="auth.hasPerm('service:edit')" @click="openServiceCatDialog">分类设置</el-button>
        <el-button v-if="auth.hasPerm('service:edit')" type="primary" @click="onCreate">新增服务项</el-button>
      </div>
    </div>

    <div class="catalog-strip">
      <button type="button" :class="['catalog-tile', !query.game_category && 'catalog-tile--active']" @click="selectGame('')"><span>全部游戏</span><strong>{{ gameCats.length }}</strong><small>按类目查看服务配置</small></button>
      <button v-for="cat in gameCats" :key="cat.id" type="button" :class="['catalog-tile', query.game_category === cat.id && 'catalog-tile--active']" @click="selectGame(cat.id)">
        <span>{{ cat.name }} <el-tag v-if="!cat.is_active" size="small" type="info">已停用</el-tag></span><strong>{{ cat.active_service_count || 0 }} <small>展示项目</small></strong><small>{{ cat.service_count || 0 }} 项服务 · {{ cat.escort_count || 0 }} 位陪玩</small>
      </button>
    </div>
    <crud-table ref="tableRef" :fetcher="serviceItemApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-select v-model="query.gameplay" placeholder="玩法" clearable style="width:140px" @change="reload"><el-option v-for="g in gameplays" :key="g.id" :label="g.name" :value="g.id" /></el-select>
        <el-select v-model="query.platform" placeholder="平台" clearable style="width:110px" @change="reload"><el-option label="手游" value="MOBILE" /><el-option label="端游" value="PC" /></el-select>
        <el-select v-model="query.security_level" placeholder="难度" clearable style="width:110px" @change="reload"><el-option label="机密" value="CONFIDENTIAL" /><el-option label="绝密" value="TOP_SECRET" /></el-select>
        <el-select v-model="query.game_category" placeholder="游戏类目" clearable style="width: 150px" @change="reload">
          <el-option v-for="c in gameCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
        <el-select v-model="query.service_category" placeholder="分类" clearable style="width: 150px" @change="reload">
          <el-option-group label="陪玩">
            <el-option v-for="c in playCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
          <el-option-group label="礼物">
            <el-option v-for="c in giftCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
        </el-select>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="封面" width="80">
        <template #default="{ row }">
          <el-image v-if="row.cover_url" :src="row.cover_url" fit="cover" style="width: 44px; height: 44px; border-radius: 6px" />
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="gameplay_name" label="玩法" width="110" />
      <el-table-column label="规格" width="120"><template #default="{ row }">{{ row.platform ? (row.platform === 'MOBILE' ? '手游' : '端游') + ' / ' + (row.security_level === 'CONFIDENTIAL' ? '机密' : row.security_level === 'TOP_SECRET' ? '绝密' : row.security_level === 'ANY' ? '不限难度' : '待确认') : '—' }}</template></el-table-column>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="游戏类目" width="120">
        <template #default="{ row }">
          <span v-if="row.game_category_name">{{ row.game_category_name }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="分类" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.service_category_name" effect="light" round>{{ row.service_category_name }}</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="要求档位" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.provider_grade || row.required_level_name" type="warning" effect="light" round>{{ row.provider_grade ? (qualificationMeta.scopes.flatMap((s: any) => s.grades).find((g: any) => g.value === row.provider_grade)?.label || row.provider_grade) : row.required_level_name }}</el-tag>
          <span v-else class="muted">不限</span>
        </template>
      </el-table-column>
      <el-table-column label="价格" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.price) }} 币 / {{ row.price_unit || '份' }}</template>
      </el-table-column>
      <el-table-column label="下单方式" width="110"><template #default="{ row }"><el-tag :type="row.purchase_mode === 'CONSULT' ? 'warning' : 'success'">{{ row.purchase_mode === 'CONSULT' ? '客服确认' : '直接下单' }}</el-tag></template></el-table-column>
      <el-table-column label="抽成配置" min-width="140">
        <template #default="{ row }">{{ row.is_gift ? '礼物独立规则' : row.commission_override ? commissionLabel(row) : '沿用原自动规则' }}</template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '上架' : '下架' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('service:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('service:delete')" link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog :show-close="!contentUploading" :close-on-click-modal="!contentUploading" :close-on-press-escape="!contentUploading" v-model="dialogVisible" :title="form.id ? '编辑服务项' : '新增服务项'" width="880px" class="ap-modal">
    <el-form :model="form"  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="接单方向"><el-select v-model="form.provider_kind" clearable placeholder="选择护航、陪玩、代肝或上分"><el-option v-for="k in qualificationMeta.kinds" :key="k.value" :value="k.value" :label="k.label" /></el-select></el-form-item>
      <el-form-item v-if="form.provider_kind" label="订单档位"><el-select v-model="form.provider_grade" placeholder="请先选择平台及难度"><el-option v-for="g in orderGrades" :key="g.value" :value="g.value" :label="g.label" /></el-select><div class="tip">{{ orderCommission == null ? '同一范围高档可接低档；其他范围需另行认证。' : '此订单档位默认抽成 ' + orderCommission + '%；开启自定义抽成后按自定义配置。' }}</div></el-form-item>
      <el-form-item label="所属玩法"><el-select v-model="form.gameplay" clearable placeholder="不选则作为普通服务 / 礼物"><el-option v-for="g in gameplays" :key="g.id" :label="g.name" :value="g.id" /></el-select></el-form-item>
      <template v-if="form.gameplay || form.provider_kind">
        <el-form-item label="游戏平台" class="ap-field--full"><el-radio-group v-model="form.platform"><el-radio value="MOBILE">手游</el-radio><el-radio value="PC">端游</el-radio></el-radio-group></el-form-item>
        <el-form-item label="行动难度" class="ap-field--full"><el-radio-group v-model="form.security_level"><el-radio value="CONFIDENTIAL">机密</el-radio><el-radio value="TOP_SECRET">绝密</el-radio><el-radio value="ANY">不限难度</el-radio></el-radio-group></el-form-item>
        <el-form-item label="履约规则" class="ap-field--full"><el-input v-model="form.rules" type="textarea" :rows="4" placeholder="明确服务目标、完成条件与售后方式；留空沿用玩法规则" /></el-form-item>
        <el-form-item v-if="isRoulette" label="转盘目标" class="ap-field--full"><RoulettePoolEditor v-model="form.roulette_pool" /></el-form-item>
      </template>
      <template v-if="giftCats.some(c => c.id === form.service_category)">
        <el-form-item label="每件冠名天数"><el-input-number v-model="form.gift_naming_days" :min="0" :max="3650"/><span class="muted">每件按数量累加，0 表示无冠名权益</span></el-form-item>
        <el-form-item label="节日限定"><el-input v-model="form.gift_festival" maxlength="40" placeholder="留空为常规礼物，如：中秋限定"/></el-form-item>
        <el-form-item v-if="form.gift_festival" label="限定起步价"><span>21 元 / 210 兴安币，低于此价格无法上架赠送</span></el-form-item>
        <el-form-item label="礼物开售时间"><el-date-picker v-model="form.gift_starts_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable/></el-form-item>
        <el-form-item label="礼物停售时间"><el-date-picker v-model="form.gift_ends_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable/></el-form-item>
      </template>
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="同款玩法"><el-input v-model="form.catalog_family" maxlength="100" placeholder="如：常规物资单；同一游戏和目录下合并为一张卡片" /></el-form-item>
      <el-form-item label="档位名称"><el-input v-model="form.catalog_variant" maxlength="150" placeholder="如：1档 · 保底866w" /></el-form-item>
      <el-form-item label="价目下方玩法详情 · 富文本" class="ap-field--full"><CatalogEditor v-model="form.details_html" @uploading="contentUploading = $event" hint="填写当前价目的玩法文案与价目海报；留空使用所属玩法的通用详情。" /></el-form-item>
      <el-form-item label="玩法简介广告" class="ap-field--full"><el-input :model-value="(form.introduction_images || []).join('\n')" @update:model-value="form.introduction_images = $event.split('\n').filter(Boolean)" type="textarea" :rows="3" placeholder="每行一张已上传广告页地址，在玩法简介显示" /></el-form-item>
      <el-form-item label="玩法规则图" class="ap-field--full"><el-input :model-value="(form.rules_images || []).join('\n')" @update:model-value="form.rules_images = $event.split('\n').filter(Boolean)" type="textarea" :rows="3" placeholder="每行一张已上传图片地址；与完整文字规则共同展示" /></el-form-item><el-form-item label="描述"><el-input v-model="form.description" /></el-form-item>
      <el-form-item label="游戏类目">
        <el-select v-model="form.game_category" placeholder="选择游戏类目" clearable style="width: 100%">
          <el-option v-for="c in gameCats" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="分类">
        <el-select v-model="form.service_category" placeholder="选择分类" clearable style="width: 100%">
          <el-option-group label="陪玩">
            <el-option v-for="c in playCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
          <el-option-group label="礼物">
            <el-option v-for="c in giftCats" :key="c.id" :label="c.name" :value="c.id" />
          </el-option-group>
        </el-select>
      </el-form-item>
      <el-form-item v-if="!form.provider_kind" label="历史服务档位">
        <el-select v-model="form.required_level" placeholder="不限档位" clearable style="width: 100%">
          <el-option v-for="l in escortLevels" :key="l.id" :label="l.name" :value="l.id" />
        </el-select>
        <div class="tip">仅该档位及以上的陪玩可接单；留空表示不限档位</div>
      </el-form-item>
      <el-form-item label="价格(兴安币)"><el-input-number v-model="priceCoin" :min="0" :precision="1" /></el-form-item>
      <el-form-item label="计价单位"><el-input v-model="form.price_unit" maxlength="20" placeholder="份 / 小时 / 人·小时 / 点" /></el-form-item>
      <el-form-item label="套餐人数与计价"><el-select v-model="form.billing_provider_count"><el-option :value="1" label="单陪：1 名陪玩" /><el-option :value="2" label="双陪：每人价格 × 2，2 名陪玩" /></el-select></el-form-item>
      <el-form-item label="下单方式" class="ap-field--full"><el-radio-group v-model="form.purchase_mode"><el-radio value="DIRECT">直接下单</el-radio><el-radio value="CONSULT">客服确认</el-radio></el-radio-group></el-form-item>
      <el-form-item label="确认说明"><el-input v-model="form.purchase_note" maxlength="255" placeholder="预约、资格、人数或加购条件" /></el-form-item>
      <el-form-item label="附加费用"><el-switch v-model="form.is_addon" /><span class="tip">关联主套餐使用，不能独立下单，也不计入玩法起价</span></el-form-item>
      <el-form-item v-if="form.catalog_source" label="价目来源"><span class="tip">{{ form.catalog_source }}</span></el-form-item>
      <el-form-item v-if="!isGift" label="商品自定义抽成"><el-switch v-model="commissionEnabled" /><div class="tip">{{ commissionEnabled ? '只调整抽成，不改变接单资格及价格；活动命中时以派单试算为准。' : '沿用原有自动抽成规则及等级建议。' }}</div></el-form-item>
      <CommissionEditor v-if="commissionEnabled && !isGift" v-model:type="commissionType" v-model:rate="commissionRate" v-model:fixed="commissionFixed" />
      <el-alert v-if="isGift" class="ap-field--full" type="info" :closable="false" title="礼物沿用独立比例规则，不使用订单自定义抽成。" />
      <el-alert v-else-if="!commissionEnabled && commissionLocked" class="ap-field--full" type="info" :closable="false" title="当前沿用订单档位或体验自动抽成规则；开启商品自定义抽成后可明确覆盖。" />
      <el-form-item label="体验资格类型"><el-select v-model="form.experience_kind"><el-option value="" label="普通服务"/><el-option value="NEW_FIRST" label="新客首单：每周期一次"/><el-option value="NEW_VALUE" label="新客超值：领券后可见"/><el-option value="DAILY" label="每日一单：每平台每天一次"/><el-option value="DAILY_VALUE" label="每日超值：领券后可见，每平台每天一次"/></el-select><span v-if="form.experience_kind" class="tip">按海报专价，不参加任何活动；默认新客体验不抽成、每日体验抽成10%，自定义抽成仅覆盖抽成。</span></el-form-item>
      <el-form-item label="服务项目类型"><el-select v-model="form.service_type"><el-option value="REGULAR" label="常规单"/><el-option value="ACTIVITY_LIMITED" label="活动限定单"/><el-option value="PROMOTIONAL" label="促销单"/><el-option value="COMPANION" label="陪玩单"/><el-option value="EXPERIENCE" label="体验单"/></el-select></el-form-item>
      <el-form-item label="可使用折扣券"><el-switch v-model="form.allows_discount_coupons"/><span class="tip">折扣券统一按此项目设置校验；默认关闭</span></el-form-item>
      <el-form-item label="VIP权益绑定类型"><el-select v-model="form.benefit_kind"><el-option value="" label="无"/><el-option value="COMPANION_HOUR" label="对应档位1小时陪玩"/><el-option value="SAFEBOX_33" label="33安全箱代肝主套餐"/></el-select></el-form-item>
      <el-form-item label="默认订单抽成规则">
        <el-select v-model="form.commission_policy" style="width: 100%">
          <el-option value="LEGACY" label="原有商品抽成规则" />
          <el-option value="EXPERIENCE" label="体验单：低于100元抽3元，100元及以上抽10%" />
          <el-option value="COMPANION" label="陪玩单：统一20%" />
          <el-option value="REGULAR" label="常规单：按陪玩等级" />
          <el-option value="FUN" label="趣味单：按陪玩等级" />
        </el-select>
      </el-form-item>
      <el-form-item label="封面URL"><el-input v-model="form.cover_url" /></el-form-item>
      <el-form-item label="图集" class="ap-field--full">
        <el-input v-model="imagesText" type="textarea" :rows="2" placeholder="每行一个图片URL" />
      </el-form-item>
      <el-form-item label="卖点" class="ap-field--full">
        <el-input v-model="highlightsText" type="textarea" :rows="2" placeholder="每行一个卖点文案" />
      </el-form-item>
      <el-form-item label="排序"><el-input-number v-model="form.sort_order" :min="0" /></el-form-item>
      <el-form-item label="上架"><el-switch v-model="form.is_active" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="contentUploading" @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="contentUploading" @click="onSave">保存</el-button>
    </template>
  </el-dialog>

  <!-- 游戏类目设置 -->
  <el-dialog v-model="gameCatDialogVisible" title="游戏类目设置" width="880px" @closed="onGameCatDialogClosed" class="ap-modal">
    <div class="dict-head">
      <span class="dict-tip">排序越小越靠前；停用后从老板端目录和陪玩技能选项隐藏，已有订单与档案保留。</span>
      <el-button size="small" type="primary" @click="startCreateGameCat">新增类目</el-button>
    </div>
    <div class="gc-layout">
      <div class="gc-list">
        <div
          v-for="cat in gameCats"
          :key="cat.id"
          :class="['gc-list-item', gcEditing.id === cat.id ? 'active' : '']"
          @click="startEditGameCat(cat)"
        >
          <el-image v-if="cat.icon_url" :src="cat.icon_url" fit="contain" class="gc-list-icon" />
          <div v-else class="gc-list-icon gc-list-icon--empty"><el-icon><Picture /></el-icon></div>
          <div class="gc-list-main">
            <span class="gc-list-name">{{ cat.name }}</span>
            <span class="gc-list-remark">排序 {{ cat.sort_order }} · {{ cat.active_service_count || 0 }}/{{ cat.service_count || 0 }} 项展示 · {{ cat.escort_count || 0 }} 位陪玩</span>
          </div>
          <el-tag :type="cat.is_active ? 'success' : 'info'" effect="light" round size="small">
            {{ cat.is_active ? '启用' : '停用' }}
          </el-tag>
        </div>
        <el-empty v-if="gameCats.length === 0" description="暂无游戏类目" :image-size="60" />
      </div>

      <div v-if="gcEditing.visible" class="gc-form">
        <div class="gc-form-title">{{ gcEditing.id ? '编辑类目' : '新增类目' }}</div>
        <el-form :model="gcEditing"  class="ap-modal-form ap-form-grid" label-position="top">
          <el-form-item label="图标" class="ap-field--full">
            <div class="gc-icon-edit">
              <el-image v-if="gcIconPreview" :src="gcIconPreview" fit="contain" class="gc-icon-preview" />
              <div v-else class="gc-icon-preview gc-icon-preview--empty"><el-icon :size="24"><Picture /></el-icon></div>
              <div class="gc-icon-ops">
                <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="onGcIconChange">
                  <el-button size="small">选择图标</el-button>
                </el-upload>
                <span class="tip">建议正方形图片，未上传则展示默认图标</span>
              </div>
            </div>
          </el-form-item>
          <el-form-item label="名称">
            <el-input v-model="gcEditing.name" placeholder="如：王者荣耀" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="gcEditing.remark" placeholder="可选" />
          </el-form-item>
          <el-form-item label="排序">
            <el-input-number v-model="gcEditing.sort_order" :min="0" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="gcEditing.is_active" />
          </el-form-item>
        </el-form>
        <div class="gc-form-footer">
          <el-button
            v-if="gcEditing.id"
            type="danger"
            plain
            @click="deleteGameCat(gcEditing)"
          >删除</el-button>
          <div class="gc-form-footer-right">
            <el-button @click="cancelGameCatEdit">取消</el-button>
            <el-button type="primary" :loading="gcSaving" @click="saveGameCat">保存</el-button>
          </div>
        </div>
      </div>
      <div v-else class="gc-form gc-form--placeholder">
        <el-empty description="从左侧选择类目进行编辑，或点击「新增类目」" :image-size="70" />
      </div>
    </div>
  </el-dialog>

  <!-- 服务分类设置：两级结构（顶层 陪玩/礼物 固定，组内维护子类） -->
  <el-dialog v-model="serviceCatDialogVisible" title="分类设置" width="840px" class="ap-modal">
    <div class="dict-head">
      <span class="dict-tip">分类分「陪玩」「礼物」两大类，各自维护子类；「礼物」子类用于概览拆分与促销范围匹配</span>
    </div>
    <div class="sc-groups">
      <div v-for="group in scGroups" :key="group.key" class="sc-group">
        <div class="sc-group-head">
          <span class="sc-group-title">{{ group.title }}</span>
          <el-button size="small" type="primary" plain @click="addServiceCatRow(group.isGift)">新增{{ group.title }}子类</el-button>
        </div>
        <el-table :data="rowsOfGroup(group.isGift)" size="small" border>
          <el-table-column label="子类名称" min-width="130">
            <template #default="{ row }"><el-input v-model="row.name" size="small" :placeholder="group.placeholder" /></template>
          </el-table-column>
          <el-table-column label="订单类型" min-width="140"><template #default="{row}"><el-select v-model="row.order_kind"><el-option value="" label="其他"/><el-option value="GUARD" label="护航"/><el-option value="COMPANION" label="陪玩"/><el-option value="GRIND" label="代肝"/><el-option value="RANK" label="上分"/></el-select></template></el-table-column>
          <el-table-column label="备注" min-width="130">
            <template #default="{ row }"><el-input v-model="row.remark" size="small" /></template>
          </el-table-column>
          <el-table-column label="排序" width="90">
            <template #default="{ row }"><el-input-number v-model="row.sort_order" :min="0" size="small" controls-position="right" style="width: 76px" /></template>
          </el-table-column>
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-switch v-model="row.is_active" size="small" /></template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="saveServiceCat(row)">保存</el-button>
              <el-button link type="danger" size="small" @click="deleteServiceCat(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>暂无子类，点击上方按钮新增</template>
        </el-table>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import RoulettePoolEditor from '@/components/RoulettePoolEditor.vue'
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import { Picture } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import CatalogEditor from '@/components/CatalogEditor.vue'
import CommissionEditor from '@/components/CommissionEditor.vue'
import { gameplayApi, serviceItemApi, gameCategoryApi, serviceCategoryApi, escortLevelApi } from '@/api/modules'
import { qualificationOptions,scopeGrades } from '@/utils/qualifications'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { commissionError, commissionLabel, commissionPayload, readCommission, type CommissionType } from '@/utils/commission'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const route = useRoute()
const query = reactive<Record<string, any>>({ game_category: '', service_category: '', gameplay: Number(route.query.gameplay) || '', platform: '', security_level: '' })
const gameplays = ref<any[]>([])
const qualificationMeta = ref<any>({ kinds: [], scopes: [] })
onMounted(async () => { qualificationMeta.value = await qualificationOptions() })

const gameCats = ref<any[]>([])
function selectGame(id: number | string) { query.game_category = id; tableRef.value?.load() }
const serviceCats = ref<any[]>([])
const escortLevels = ref<any[]>([])

// 分类按顶层归属派生：陪玩(is_gift=false) / 礼物(is_gift=true)
const playCats = computed(() => serviceCats.value.filter((c) => !c.is_gift))
const giftCats = computed(() => serviceCats.value.filter((c) => c.is_gift))

async function loadDicts() {
  const [g, s, l, gp] = await Promise.all([

    gameCategoryApi.all() as any,
    serviceCategoryApi.all() as any,
    escortLevelApi.allOptions() as any,
    gameplayApi.all() as any,
  ])
  gameplays.value = gp.data?.list || []
  gameCats.value = g.data?.list || []
  serviceCats.value = s.data?.list || []
  escortLevels.value = l.data?.list || []
}

onMounted(loadDicts)

const dialogVisible = ref(false)
const saving = ref(false)
const contentUploading = ref(false)
const priceCoin = ref(0)
const commissionRate = ref<number | undefined>(undefined)
const commissionType = ref<CommissionType>('PERCENT')
const commissionFixed = ref<number | undefined>(0)
const commissionEnabled = ref(false)
const imagesText = ref('')
const highlightsText = ref('')
const form = reactive<any>({ roulette_pool: [] })
const isGift = computed(() => Boolean(form.service_category && (serviceCats.value.find(c => c.id === form.service_category)?.is_gift ?? form.is_gift)))
watch(isGift, gift => { if (gift) commissionEnabled.value = false })
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

function onCreate() {
  Object.assign(form, blank())
  if (isRoulette.value) form.roulette_pool = [{ name: '', description: '', weight: 1 }, { name: '', description: '', weight: 1 }]
  priceCoin.value = 0
  commissionRate.value = 0
  commissionType.value = 'PERCENT'
  commissionFixed.value = 0
  commissionEnabled.value = false
  imagesText.value = ''
  highlightsText.value = ''
  dialogVisible.value = true
}

function onEdit(row: any) {
  Object.assign(form, JSON.parse(JSON.stringify(row)))
  priceCoin.value = Number(amountToXaCoin(row.price))
  const commission = readCommission(row)
  commissionType.value = commission.type
  commissionRate.value = commission.rate
  commissionFixed.value = commission.fixed
  commissionEnabled.value = row.commission_override === true
  imagesText.value = toLines(row.images)
  highlightsText.value = toLines(row.highlights)
  dialogVisible.value = true
}

async function onSave() {
  const overrideEnabled = commissionEnabled.value && !isGift.value
  if (form.provider_kind && !form.provider_grade) { ElMessage.warning('请配置有效的订单档位'); return }
  if (!form.name) { ElMessage.warning('请填写名称'); return }
  if (overrideEnabled) {
    const error = commissionError(commissionType.value, commissionRate.value, commissionFixed.value)
    if (error) { ElMessage.warning(error); return }
  }
  saving.value = true
  try {
    const payload = {
      provider_kind: form.provider_kind || '', provider_grade: form.provider_kind ? form.provider_grade : '',
      gameplay: form.gameplay || null, platform: form.gameplay || form.provider_kind ? form.platform : '', security_level: form.gameplay || form.provider_kind ? form.security_level : '',
      rules: form.rules, details_html: form.details_html || '', roulette_pool: isRoulette.value ? form.roulette_pool : [],
      name: form.name,
      gift_naming_days: form.gift_naming_days || 0, gift_festival: form.gift_festival || '', gift_starts_at: form.gift_starts_at || null, gift_ends_at: form.gift_ends_at || null,
      description: form.description,
      game_category: form.game_category || null,
      service_category: form.service_category || null,
      required_level: form.provider_kind ? null : form.required_level || null,
      price: xaCoinToAmount(priceCoin.value),
      price_unit: form.price_unit || '份', purchase_mode: form.purchase_mode || 'DIRECT', purchase_note: form.purchase_note || '', is_addon: !!form.is_addon,
      billing_provider_count: form.billing_provider_count || 1,
      catalog_family: form.catalog_family || '', catalog_variant: form.catalog_variant || '',
      ...(overrideEnabled ? commissionPayload(commissionType.value, commissionRate.value, commissionFixed.value) : {}),
      commission_override: overrideEnabled,
      commission_policy: form.commission_policy || 'LEGACY', experience_kind: form.experience_kind || '', benefit_kind: form.benefit_kind || '', service_type: form.service_type || 'REGULAR', allows_discount_coupons: !!form.allows_discount_coupons,
      cover_url: form.cover_url,
      rules_images: form.rules_images || [],
      introduction_images: form.introduction_images || [],
      images: fromLines(imagesText.value),
      highlights: fromLines(highlightsText.value),
      sort_order: form.sort_order,
      is_active: form.is_active,
    }
    if (form.id) {
      await serviceItemApi.update(form.id, payload)
    } else {
      await serviceItemApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: any) {
  await ElMessageBox.confirm(`确定删除服务项「${row.name}」吗？`, '提示', { type: 'warning' })
  await serviceItemApi.remove(row.id)
  ElMessage.success('已删除')
  tableRef.value.reload()
}

// ---------------- 游戏类目字典 ----------------
const gameCatDialogVisible = ref(false)
const gcSaving = ref(false)
const gcIconPreview = ref('')
const gcIconFile = ref<File | null>(null)
const gcEditing = reactive<any>({ visible: false, id: 0, name: '', remark: '', sort_order: 0, is_active: true })

function openGameCatDialog() {
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

function onGcIconChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  gcIconFile.value = raw
  gcIconPreview.value = URL.createObjectURL(raw)
}

async function saveGameCat() {
  if (!gcEditing.name?.trim()) { ElMessage.warning('请填写名称'); return }
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
    ElMessage.success('已保存')
    await loadDicts()
    resetGameCatEdit()
  } finally {
    gcSaving.value = false
  }
}

async function deleteGameCat(cat: any) {
  if (!cat.id) { resetGameCatEdit(); return }
  await ElMessageBox.confirm(`确定删除类目「${cat.name}」吗？`, '提示', { type: 'warning' })
  await gameCategoryApi.remove(cat.id)
  ElMessage.success('已删除')
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
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
  serviceCatDialogVisible.value = true
}

function addServiceCatRow(isGift: boolean) {
  serviceCatRows.value.unshift({ id: 0, name: '', is_gift: isGift, remark: '', sort_order: 0, is_active: true })
}

async function saveServiceCat(row: any) {
  if (!row.name?.trim()) { ElMessage.warning('请填写名称'); return }
  const payload = { name: row.name, is_gift: row.is_gift, order_kind: row.order_kind || '', remark: row.remark, sort_order: row.sort_order, is_active: row.is_active }
  if (row.id) await serviceCategoryApi.update(row.id, payload)
  else await serviceCategoryApi.create(payload)
  ElMessage.success('已保存')
  await loadDicts()
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
}

async function deleteServiceCat(row: any) {
  if (!row.id) { serviceCatRows.value = serviceCatRows.value.filter((r) => r !== row); return }
  await ElMessageBox.confirm(`确定删除分类「${row.name}」吗？`, '提示', { type: 'warning' })
  await serviceCategoryApi.remove(row.id)
  ElMessage.success('已删除')
  await loadDicts()
  serviceCatRows.value = serviceCats.value.map((c) => ({ ...c }))
}
</script>

<style scoped lang="scss">
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
  border: 1px solid var(--el-border-color-lighter);
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
  background: var(--el-fill-color-light);
}
.gc-list-item.active {
  background: var(--el-color-primary-light-9);
}
.gc-list-icon {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  flex-shrink: 0;
  background: var(--el-fill-color-lighter);
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
  color: var(--el-text-color-primary);
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
  border: 1px solid var(--el-border-color-lighter);
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
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-lighter);
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
  .gc-form-footer .el-button + .el-button { margin-left: 0; }
}
.sc-group-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
</style>
