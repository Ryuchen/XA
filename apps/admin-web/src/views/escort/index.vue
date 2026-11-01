<template>
  <div class="es-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理入驻陪玩师的资质、状态与业绩</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('escort:edit')" type="primary" :disabled="!!saveReview" @click="onCreate">创建陪玩账号</el-button>
      </div>
    </div>

    <el-alert v-if="saveReview && !dialogVisible && !createVisible && !saving && !creating" title="陪玩资料保存结果待核对" type="warning" :closable="false" show-icon>
      <p>{{ saveReview.id ? `档案 #${saveReview.id}` : `账号 ${saveReview.username}` }} 的请求可能仍在处理。请读取最新档案，并确认实际结果后再继续操作。</p>
      <p v-if="reviewNotice" role="status">{{ reviewNotice }}</p>
      <el-button :loading="reviewLoading" @click="checkSaveReview">读取最新档案</el-button>
      <el-button v-if="reviewChecked" :disabled="reviewLoading" @click="finishSaveReview">已核对，解除限制</el-button>
    </el-alert>
    <el-alert v-if="disposeReview && !disposeVisible" title="奖罚结果待核对" type="warning" :closable="false" show-icon>
      <p>{{ disposeReview.name }} · {{ disposeReview.payload.dispose_type === 'REWARD' ? '奖励' : '罚款' }} {{ amountToXaCoin(disposeReview.payload.amount) }} 币。原请求可能仍在处理。</p>
      <p>继续同一请求：未执行时会记账，已完成时恢复原回执。</p>
      <el-button size="small" :loading="disposing" @click="recoverDispose">核对并继续原请求</el-button>
      <el-button v-if="auth.hasPerm('dispose:view') && disposeReview.accountId" size="small" @click="openDisposeRecords">查看奖罚记录</el-button>
      <el-button size="small" :disabled="disposing" @click="finishDisposeReview">已核对，解除限制</el-button>
    </el-alert>

    <crud-table ref="tableRef" :fetcher="escortApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="昵称/账号/手机号"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="状态" clearable style="width: 120px" @change="reload">
          <el-option v-for="(v, k) in ESCORT_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.is_verified" placeholder="认证" clearable style="width: 120px" @change="reload">
          <el-option label="已认证" value="true" />
          <el-option label="未认证" value="false" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
        <el-select v-model="query.game_category" placeholder="可接游戏" clearable filterable style="width:160px" @change="reload">
          <el-option v-for="game in gameOptions" :key="game.id" :label="game.name" :value="game.id" />
        </el-select>
        <QualificationFilters :query="query" @change="reload" />
        <el-select v-model="query.gender" placeholder="性别" clearable style="width:100px" @change="reload"><el-option value="MALE" label="男陪"/><el-option value="FEMALE" label="女陪"/></el-select>
        <el-select v-model="query.pass_tier" placeholder="通行证" clearable style="width:110px" @change="reload"><el-option v-for="p in [{value:'BLACK',label:'黑金'},{value:'GOLD',label:'黄金'},{value:'SILVER',label:'白银'},{value:'BRONZE',label:'青铜'},{value:'NONE',label:'无通行证'}]" :key="p.value" :value="p.value" :label="p.label" /></el-select>
      </template>

      <template #mobile="{ rows }">
        <div class="es-mobile-list" role="list" aria-label="陪玩档案列表">
          <article v-for="row in rows" :key="row.id" class="es-mobile-card" role="listitem">
            <div class="es-mobile-head">
              <el-avatar :src="row.avatar_url" :size="40">{{ (row.display_name || row.username || '陪').slice(0, 1) }}</el-avatar>
              <div class="es-mobile-person"><strong>{{ row.display_name || row.username || '姓名待核对' }}</strong><span>{{ row.username || '账号待核对' }} · {{ row.escort_no || `档案 #${row.id}` }}</span></div>
              <el-tag :type="ESCORT_STATUS[row.status]?.type || 'info'" effect="light" round>{{ escortStatusLabel(row) }}</el-tag>
            </div>
            <div class="es-mobile-facts">
              <span>{{ row.is_verified === true ? '已认证' : row.is_verified === false ? '未认证' : '认证待核对' }}</span><span>{{ row.gender_display || GENDER[row.gender]?.label || '性别待核对' }}</span><span>{{ row.pass_tier_display || '通行证待核对' }}</span>
              <el-tooltip content="档案状态不等于可接单；账号、认证、押金、档期、具体服务及名额均需核验。" :trigger="['hover', 'focus', 'click']" placement="top"><button type="button" class="es-status-help" aria-label="接单状态说明">?</button></el-tooltip>
            </div>
            <div class="es-mobile-group"><span>已配置游戏</span><div class="es-games"><el-tag v-for="name in row.game_category_names" :key="name" size="small" effect="plain">{{ name }}</el-tag><span v-if="!row.game_category_names?.length" class="es-secondary">{{ Array.isArray(row.game_category_names) ? '暂无' : '待核对' }}</span></div></div>
            <div class="es-mobile-group"><span>接单资格</span><div class="es-games"><el-tag v-for="(q, index) in activeQualifications(row)" :key="q.id || index" size="small">{{ q.label }}</el-tag><span v-if="!activeQualifications(row).length" class="es-secondary">{{ qualificationSummary(row) }}</span></div></div>
            <dl class="es-mobile-values"><div><dt>服务技能</dt><dd>{{ Array.isArray(row.service_items) ? `${row.service_items.length} 项` : '待核对' }}</dd></div><div><dt>完成订单</dt><dd>{{ row.completed_order_count ?? '待核对' }}</dd></div></dl>
            <div class="es-mobile-actions">
              <el-button v-if="auth.hasPerm('escort:edit')" :disabled="!!saveReview" @click="onEdit(row)">编辑</el-button>
              <el-dropdown trigger="click"><el-button>更多</el-button><template #dropdown><el-dropdown-menu>
                <el-dropdown-item v-if="auth.hasPerm('escort:verify')" :disabled="verifying" @click="onVerify(row)">{{ row.is_verified ? '取消认证' : '认证' }}</el-dropdown-item>
                  <el-dropdown-item v-if="auth.hasPerm('escort:dispose')" :disabled="!!disposeReview" @click="onDispose(row)">奖罚</el-dropdown-item>
                <el-dropdown-item v-if="auth.hasPerm('escort:deposit_refund')" @click="onDepositRefund(row)">退还押金</el-dropdown-item>
                <el-dropdown-item @click="banTarget = row; banVisible = true">封禁记录</el-dropdown-item>
              </el-dropdown-menu></template></el-dropdown>
            </div>
          </article>
        </div>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="昵称" min-width="180">
        <template #default="{ row }"><div class="es-person"><el-avatar :src="row.avatar_url" :size="36">{{ (row.display_name || row.username || '陪').slice(0, 1) }}</el-avatar><div><strong>{{ row.display_name || row.username }}</strong><span class="es-secondary">{{ qualificationSummary(row) }}</span></div></div></template>
      </el-table-column>
      <el-table-column label="服务范围" min-width="220">
        <template #default="{ row }"><div class="es-games"><el-tag v-for="name in row.game_category_names" :key="name" size="small" effect="plain">{{ name }}</el-tag><span v-if="!row.game_category_names?.length" class="es-secondary">尚未配置游戏</span></div><el-tooltip :content="(row.service_item_names || []).join('、') || '请在编辑中配置可接服务项'" placement="top"><span class="es-secondary">{{ row.service_items?.length || 0 }} 项服务技能</span></el-tooltip></template>
      </el-table-column>
      <el-table-column label="性别" width="80">
        <template #default="{ row }">
          <el-tag :type="GENDER[row.gender]?.type" effect="light" round>{{ row.gender_display || GENDER[row.gender]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="接单资格" min-width="240"><template #default="{ row }"><el-tag v-for="(q, index) in activeQualifications(row)" :key="q.id || index" size="small" style="margin:2px">{{ q.label }}</el-tag><span v-if="!activeQualifications(row).length">{{ qualificationSummary(row) }}</span></template></el-table-column>
      <el-table-column label="推荐位" width="90"><template #default="{ row }">{{ row.recommendation_slot ? "推荐位 " + row.recommendation_slot : "—" }}</template></el-table-column>
      <el-table-column prop="username" label="账号" min-width="120" />
      <el-table-column prop="escort_no" label="编号" width="110">
        <template #default="{ row }">{{ row.escort_no || '-' }}</template>
      </el-table-column>
      <el-table-column prop="city" label="城市" width="90" />
      <el-table-column label="时薪" width="80">
        <template #default="{ row }">{{ row.price_per_hour == null ? '—' : amountToXaCoin(row.price_per_hour) }}</template>
      </el-table-column>
      <el-table-column prop="rating_avg" label="评分" width="70" />
      <el-table-column prop="completed_order_count" label="完成单" width="80" />
      <el-table-column label="抢单通行证" width="120">
        <template #default="{ row }">
          <el-tag :type="row.active_pass_tier === 'BLACK' ? 'info' : row.active_pass_tier ? 'warning' : 'info'" effect="light" round>
            {{ row.pass_tier_display }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="累计奖/罚" width="120">
        <template #default="{ row }">
          <span class="es-reward">+{{ amountToXaCoin(row.total_reward) }}币</span>
          /
          <span class="es-penalty">-{{ amountToXaCoin(row.total_penalty) }}币</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #header><span>状态</span><el-tooltip content="档案状态不等于可接单；账号、认证、押金、档期、具体服务及名额均需核验。" :trigger="['hover', 'focus', 'click']" placement="top"><button type="button" class="es-status-help" aria-label="接单状态说明">?</button></el-tooltip></template>
        <template #default="{ row }">
          <el-tag :type="ESCORT_STATUS[row.status]?.type || 'info'" effect="light" round>{{ escortStatusLabel(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="认证" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_verified ? 'success' : 'info'" effect="light" round>{{ row.is_verified ? '已认证' : '未认证' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('escort:edit')" link type="primary" :disabled="!!saveReview" @click="onEdit(row)">编辑</el-button>
          <el-dropdown trigger="click" class="es-more">
            <el-button link>更多</el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item v-if="auth.hasPerm('escort:verify')" :disabled="verifying" @click="onVerify(row)">{{ row.is_verified ? '取消认证' : '认证' }}</el-dropdown-item>
                <el-dropdown-item v-if="auth.hasPerm('escort:dispose')" :disabled="!!disposeReview" @click="onDispose(row)">奖罚</el-dropdown-item>
              <el-dropdown-item v-if="auth.hasPerm('escort:deposit_refund')" @click="onDepositRefund(row)">退还押金</el-dropdown-item>
              <el-dropdown-item @click="banTarget = row; banVisible = true">封禁记录</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="reviewViewing ? '核对最新陪玩资料' : '编辑陪玩资料'" width="800px" class="es-dialog ap-modal" :before-close="closeEditDialog" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <el-form :model="editForm" :disabled="saving || reviewViewing || saveUnknown" :inert="saving || (saveUnknown && !reviewViewing) || undefined" class="ap-modal-form ap-form-grid" label-position="top">
      <p v-if="editDirty && !saveUnknown && !reviewViewing" class="es-draft-status" role="status">有未保存的修改</p>
      <div class="es-form-section">
        <p class="es-section-title">基础资料</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="昵称"><el-input v-model="editForm.display_name" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="性别">
              <el-select v-model="editForm.gender" style="width: 100%">
                <el-option v-for="(v, k) in GENDER" :key="k" :label="v.label" :value="k" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="城市"><el-input v-model="editForm.city" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="服务区"><el-input v-model="editForm.service_area" /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="下单页推荐位"><el-select v-model="editForm.recommendation_slot" clearable placeholder="不推荐" style="width: 100%"><el-option v-for="slot in 4" :key="slot" :label="'推荐位 ' + slot" :value="slot" /></el-select><span class="es-secondary">每个位置只能配置一位陪玩；先清空原位置可替换人选。</span></el-form-item>
        <el-form-item label="简介" class="ap-field--full"><el-input v-model="editForm.bio" type="textarea" :rows="2" placeholder="选填" /></el-form-item>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">陪玩形象</p>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="头像" label-position="top" class="ap-field--full">
              <el-upload class="es-media-upload" :disabled="saving || reviewViewing || saveUnknown"
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickFile('avatar', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <img v-if="editPreview.avatar" :src="editPreview.avatar" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="查外挂图" label-position="top" class="ap-field--full">
              <el-upload class="es-media-upload" :disabled="saving || reviewViewing || saveUnknown"
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickFile('cheat_proof', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <PrivateImage v-if="editPreview.cheat_proof" :src="editPreview.cheat_proof" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="视频介绍" label-position="top" class="ap-field--full">
              <el-upload class="es-media-upload" :disabled="saving || reviewViewing || saveUnknown"
                :show-file-list="false"
                accept="video/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickFile('intro_video', f)"
              >
                <div class="es-uploader es-uploader--video">
                  <video v-if="editPreview.intro_video" :src="editPreview.intro_video" class="es-thumb" />
                  <el-icon v-else><VideoCamera /></el-icon>
                </div>
              </el-upload>
              <div v-if="editFiles.intro_video" class="tip">{{ editFiles.intro_video.name }}</div>
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">业务档案</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="参考时价(币)"><el-input-number v-model="editHourlyCoins" :min="0" style="width: 100%" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="段位"><el-input v-model="editForm.rank_tier" /></el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="胜率(%)"><el-input-number v-model="editForm.win_rate" :min="0" :max="100" :precision="2" style="width: 100%" /></el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="接单资格" class="ap-field--full"><ProviderQualificationsEditor ref="editQualificationsRef" v-model="editForm.qualifications" :games="gameOptions" :disabled="saving || reviewViewing || saveUnknown" /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="可接游戏">
          <el-select
            v-model="editForm.game_categories"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择该陪玩可接单的游戏（可多选）"
            style="width: 100%"
          >
            <el-option v-for="g in gameOptions" :key="g.id" :label="g.name + (g.is_active ? '' : '（已停用）')" :value="g.id" :disabled="!g.is_active" />
          </el-select>
        </el-form-item>
        <el-form-item label="可接服务项" class="ap-field--full">
          <EscortSkillSelect v-model="editForm.service_items" :games="gameOptions" :items="serviceItemOptions"
            @games-selected="editForm.game_categories = [...new Set([...editForm.game_categories, ...$event])]" />
        </el-form-item>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">押金与状态</p>
        <el-form-item label="押金缴纳模式">
          <el-select v-model="editForm.deposit_mode" style="width: 100%" :disabled="saving || reviewViewing || saveUnknown || editForm.deposit_paid > 0">
            <el-option value="CASH" label="现金缴纳" />
            <el-option value="EARNINGS" label="打单抵扣" />
          </el-select>
          <div v-if="editForm.deposit_paid > 0" class="tip">已有押金，退还后才能切换模式。</div>
          <div v-else-if="editForm.deposit_mode !== editOriginalDepositMode" class="tip">保存后按当前规则确定应缴押金。</div>
          <div v-else class="tip">应缴金额以当前规则及档案为准。</div>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="当前应缴押金(币)"><el-input :model-value="editForm.effective_deposit_required == null ? '待核对' : amountToXaCoin(editForm.effective_deposit_required)" readonly /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="已缴押金(币)"><el-input :model-value="amountToXaCoin(editForm.deposit_paid)" readonly /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option v-for="(v, k) in ESCORT_STATUS" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-alert v-if="saveError && !reviewViewing" :title="saveError" :type="saveUnknown ? 'warning' : 'error'" :closable="false" show-icon />
      <el-button :disabled="saving" @click="closeEditDialog()">{{ reviewViewing ? '完成查看' : saveUnknown ? '关闭并核对' : '取消' }}</el-button>
      <el-button v-if="!reviewViewing" type="primary" :loading="saving" :disabled="saveUnknown" @click="onSave">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="createVisible" title="创建陪玩账号" width="800px" class="es-dialog ap-modal" :before-close="closeCreateDialog" :close-on-click-modal="!creating" :close-on-press-escape="!creating" :show-close="!creating">
    <el-form :model="createForm" :rules="createRules" ref="createFormRef" :disabled="creating || saveUnknown" :inert="creating || saveUnknown || undefined" class="ap-modal-form ap-form-grid" label-position="top">
      <p v-if="createDirty && !saveUnknown" class="es-draft-status" role="status">有未保存的内容</p>
      <div class="es-form-section">
        <p class="es-section-title">登录凭证</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="登录账号" prop="username">
              <el-input v-model="createForm.username" placeholder="陪玩登录用账号" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="登录密码" prop="password">
              <el-input v-model="createForm.password" type="password" show-password placeholder="至少 6 位" />
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">基础资料</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="昵称" prop="display_name">
              <el-input v-model="createForm.display_name" placeholder="对外展示的陪玩昵称" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="性别">
              <el-select v-model="createForm.gender" style="width: 100%">
                <el-option v-for="(v, k) in GENDER" :key="k" :label="v.label" :value="k" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="手机号">
              <el-input v-model="createForm.phone" placeholder="选填" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="城市">
              <el-input v-model="createForm.city" placeholder="选填" />
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">陪玩形象</p>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="头像" label-position="top" class="ap-field--full">
              <el-upload class="es-media-upload"
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickCreateFile('avatar', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <img v-if="createPreview.avatar" :src="createPreview.avatar" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="查外挂图" label-position="top" class="ap-field--full">
              <el-upload class="es-media-upload"
                :show-file-list="false"
                accept="image/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickCreateFile('cheat_proof', f)"
              >
                <div class="es-uploader es-uploader--img">
                  <img v-if="createPreview.cheat_proof" :src="createPreview.cheat_proof" class="es-thumb" />
                  <el-icon v-else><Plus /></el-icon>
                </div>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="视频介绍" label-position="top" class="ap-field--full">
              <el-upload class="es-media-upload"
                :show-file-list="false"
                accept="video/*"
                :auto-upload="false"
                :on-change="(f: any) => onPickCreateFile('intro_video', f)"
              >
                <div class="es-uploader es-uploader--video">
                  <video v-if="createPreview.intro_video" :src="createPreview.intro_video" class="es-thumb" />
                  <el-icon v-else><VideoCamera /></el-icon>
                </div>
              </el-upload>
              <div v-if="createFiles.intro_video" class="tip">{{ createFiles.intro_video.name }}</div>
            </el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="es-form-section">
        <p class="es-section-title">计费与押金</p>
        <el-row :gutter="16">
          <el-col :span="24">
            <el-form-item label="接单资格" class="ap-field--full"><ProviderQualificationsEditor ref="createQualificationsRef" v-model="createForm.qualifications" :games="gameOptions" :disabled="creating || saveUnknown" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="押金缴纳模式">
              <el-select v-model="createForm.deposit_mode" style="width: 100%"><el-option value="CASH" label="现金缴纳" /><el-option value="EARNINGS" label="打单抵扣" /></el-select>
              <div class="tip">创建后按当前规则及档案确定应缴押金。</div>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="可接游戏">
          <el-select
            v-model="createForm.game_categories"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择该陪玩可接单的游戏（可多选）"
            style="width: 100%"
          >
            <el-option v-for="g in gameOptions" :key="g.id" :label="g.name + (g.is_active ? '' : '（已停用）')" :value="g.id" :disabled="!g.is_active" />
          </el-select>
        </el-form-item>
        <el-form-item label="可接服务项" class="ap-field--full">
          <EscortSkillSelect v-model="createForm.service_items" :games="gameOptions" :items="serviceItemOptions"
            @games-selected="createForm.game_categories = [...new Set([...createForm.game_categories, ...$event])]" />
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-alert v-if="saveError" :title="saveError" :type="saveUnknown ? 'warning' : 'error'" :closable="false" show-icon />
      <el-button :disabled="creating" @click="closeCreateDialog()">{{ saveUnknown ? '关闭并核对' : '取消' }}</el-button>
      <el-button type="primary" :loading="creating" :disabled="saveUnknown" @click="onCreateSave">创建</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="disposeVisible" title="奖励 / 罚款" width="480px" class="ap-modal"
    :before-close="closeDispose" :close-on-click-modal="!disposing" :close-on-press-escape="!disposing" :show-close="!disposing">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="陪玩">{{ disposeTarget?.display_name || disposeTarget?.username }} · {{ disposeTarget?.escort_no || '编号待核对' }} · {{ disposeTarget?.username || '账号待核对' }}</el-form-item>
      <el-form-item label="类型" class="ap-field--full">
        <el-radio-group v-model="disposeType" :disabled="disposing || !!disposeReview">
          <el-radio label="REWARD">奖励</el-radio>
          <el-radio label="PENALTY">罚款</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="金额(兴安币)">
        <el-input-number v-model="disposeYuan" :min="0" :max="214748364.7" :precision="1" :step="1" :disabled="disposing || !!disposeReview" style="width: 100%" />
        <div class="tip">{{ disposeType === 'REWARD' ? '奖励将增加陪玩余额' : '罚款将扣减陪玩余额（不可使余额为负）' }}</div>
      </el-form-item>
      <el-form-item label="原因" class="ap-field--full">
        <el-input v-model="disposeReason" type="textarea" :rows="2" maxlength="255" show-word-limit placeholder="奖罚原因（可选）" :disabled="disposing || !!disposeReview" />
      </el-form-item>
    </el-form>
    <el-alert v-if="disposeReview" title="结果未确认，请关闭后核对原请求" type="warning" :closable="false" show-icon />
    <template #footer>
      <el-button :disabled="disposing" @click="closeDispose()">{{ disposeReview ? '关闭' : '取消' }}</el-button>
      <el-button type="primary" :loading="disposing" :disabled="!!disposeReview" @click="onDisposeSave">核对并提交</el-button>
    </template>
  </el-dialog>
  <account-ban-dialog v-model="banVisible" resource="escorts" :target-id="banTarget?.id" :target-name="banTarget?.display_name || banTarget?.username" @changed="tableRef?.load()" />
  <el-dialog v-model="refundVisible" title="退还押金" width="480px" class="ap-modal">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="陪玩">{{ refundTarget?.display_name || refundTarget?.username }}</el-form-item>
      <el-form-item label="当前已缴">{{ amountToXaCoin(refundTarget?.deposit_paid || 0) }} 币</el-form-item>
      <el-form-item label="退还金额(币)" required><el-input-number v-model="refundCoins" :min="0.1" :max="Number(amountToXaCoin(refundTarget?.deposit_paid || 0))" :precision="1" /></el-form-item>
      <el-form-item label="退还原因" required class="ap-field--full"><el-input v-model="refundReason" type="textarea" :rows="3" maxlength="200" show-word-limit /></el-form-item>
    </el-form>
    <el-alert title="押金将退回陪玩钱包。外部打款需另走提现审核。" type="info" :closable="false" />
    <template #footer>
      <el-button @click="refundVisible = false">取消</el-button>
      <el-button type="primary" :loading="refunding" @click="submitDepositRefund">确认退回钱包</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import ProviderQualificationsEditor from '@/components/ProviderQualificationsEditor.vue'
import QualificationFilters from '@/components/QualificationFilters.vue'
import PrivateImage from "@/components/PrivateImage.vue"
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import EscortSkillSelect from '@/components/EscortSkillSelect.vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Plus, VideoCamera } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import AccountBanDialog from '@/components/AccountBanDialog.vue'
import { escortApi, escortLevelApi, gameCategoryApi, serviceItemApi } from '@/api/modules'
import { request } from '@/api/request'
import { ESCORT_STATUS, GENDER } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
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
  if (!row.deposit_paid) { ElMessage.info('该陪玩没有可退还的押金'); return }
  refundTarget.value = row
  refundCoins.value = Number(amountToXaCoin(row.deposit_paid))
  refundReason.value = ''
  refundRequestId.value = crypto.randomUUID()
  refundVisible.value = true
}

async function submitDepositRefund() {
  if (refunding.value || !refundTarget.value) return
  if (!refundReason.value.trim()) { ElMessage.warning('请填写退还原因'); return }
  const amount = xaCoinToAmount(refundCoins.value)
  if (amount <= 0 || amount > refundTarget.value.deposit_paid) { ElMessage.warning('退还金额须大于零且不超过已缴押金'); return }
  refunding.value = true
  try {
    const result = await escortApi.action(refundTarget.value.id, 'deposit-refund', {
      amount, reason: refundReason.value.trim(), client_request_id: refundRequestId.value,
    })
    refundVisible.value = false
    ElMessage.success('押金已退回陪玩钱包')
    tableRef.value.load()
    if (result.data.active_order_count) {
      await ElMessageBox.alert(`该陪玩还有 ${result.data.active_order_count} 笔在途订单，请跟进服务及押金要求。`, '在途订单提醒')
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
  // DRF DecimalField 序列化为字符串，el-input-number 需要 Number
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
    ElMessage.success('保存成功')
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
      await ElMessageBox.confirm(next
        ? `确认认证「${name}」？接单仍需满足账号、押金、档期、服务资格和名额要求。`
        : `确认取消「${name}」的认证？取消后该陪玩不能接新单。`, next ? '确认认证' : '确认取消认证', {
        type: 'warning', confirmButtonText: next ? '确认认证' : '取消认证', cancelButtonText: '返回',
      })
    } catch { return }
    if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:verify')) return
    try {
      await escortApi.action(targetId, 'verify', { is_verified: next }, true)
      if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:verify')) return
      ElMessage.success(next ? '认证已更新' : '认证已取消')
    } catch (error: any) {
      if (escortPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('escort:verify')) return
      const body = error?.response?.data || error, status = error?.response?.status
      const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
      ElMessage.warning(rejected ? `认证未更新：${body?.msg || '请求被拒绝'}` : '认证结果未确认，请刷新列表核对后再操作')
    }
    if (!escortPageDisposed && revision === auth.sessionRevision) tableRef.value?.reload()
  } finally { if (revision === auth.sessionRevision) verifying.value = false }
}

// ---------------- 创建陪玩账号 ----------------
const createVisible = ref(false)
const creating = ref(false)
const createFormRef = ref<FormInstance>()
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
const createRules: FormRules = {
  username: [
    { required: true, message: '请输入登录账号', trigger: 'blur' },
    { max: 150, message: '登录账号最多 150 字', trigger: 'blur' },
    { validator: (_rule, value, callback) => typeof value === 'string' && value.trim() ? callback() : callback(new Error('请输入登录账号')), trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入登录密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  display_name: [{ required: true, message: '请输入昵称', trigger: 'blur' }],
}

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
      ElMessage.success('陪玩账号创建成功')
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
    await ElMessageBox.confirm('关闭后未保存的陪玩资料将丢失。', '放弃修改？', {
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
  if (disposing.value) { ElMessage.warning('正在处理奖罚，请稍候'); return false }
  if (saving.value || creating.value) { ElMessage.warning('正在保存，请稍候'); return false }
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
    await ElMessageBox.confirm('请确认已核对档案和请求处理情况；请求可能仍在处理中。', '解除保存限制', {
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
    ElMessage.warning('金额须为 0.1 币的整数倍，且大于 0')
    return
  }
  const row = disposeTarget.value, revision = auth.sessionRevision
  const marker: DisposeReview = { profileId: row.id, accountId: row.user || null, name: `${row.display_name || row.username || '陪玩'} · ${row.escort_no || '编号待核对'} · ${row.username || '账号待核对'}`,
    payload: { dispose_type: disposeType.value, amount, reason: disposeReason.value.trim(), client_request_id: crypto.randomUUID() } }
  disposing.value = true
  try {
    try { await ElMessageBox.confirm(`陪玩：${marker.name}\n类型：${marker.payload.dispose_type === 'REWARD' ? '奖励' : '罚款'}\n金额：${amountToXaCoin(amount)} 币\n原因：${marker.payload.reason || '未填写'}`, '确认奖罚', { type: 'warning', customClass: 'es-dispose-confirm', confirmButtonText: '确认记账', cancelButtonText: '返回修改' }) } catch { return }
    if (escortPageDisposed || revision !== auth.sessionRevision || !disposeVisible.value || !auth.hasPerm('escort:dispose')) return
    beginDisposeReview(marker)
    await executeDispose(marker, revision)
  } finally {
    if (revision === auth.sessionRevision) disposing.value = false
  }
}
async function executeDispose(marker: DisposeReview, revision: number) {
  try {
    await escortApi.action(marker.profileId, 'dispose', marker.payload, true)
    if (escortPageDisposed || revision !== auth.sessionRevision || disposeReview.value !== marker) return
    clearDisposeReview(); disposeVisible.value = false
    ElMessage.success('奖罚已记账'); tableRef.value?.load()
  } catch (error: any) {
    if (escortPageDisposed || revision !== auth.sessionRevision || disposeReview.value !== marker || error?.code === 'SESSION_CHANGED') return
    const body = error?.response?.data || error, status = error?.response?.status
    if (body?.errorCode === 'FUND_ALREADY_PROCESSED' && body?.fundResult?.data?.record?.id) {
      clearDisposeReview(); disposeVisible.value = false
      ElMessage.success('已恢复原奖罚回执，未重复记账'); tableRef.value?.load()
    } else if ((!status || status < 500) && status !== 408 && body?.code === 400) {
      clearDisposeReview(); ElMessage.error(body?.msg || '奖罚未记账，请核对输入')
    } else if (status === 403 || status === 404) {
      clearDisposeReview(); ElMessage.error(body?.msg || '奖罚未记账，请检查权限或陪玩账号')
    } else {
      ElMessage.warning('奖罚结果未确认，请核对原请求')
    }
  }
}
async function recoverDispose() {
  const marker = disposeReview.value, revision = auth.sessionRevision
  if (!marker || disposing.value || !auth.hasPerm('escort:dispose')) return
  disposing.value = true
  try {
    try { await ElMessageBox.confirm('若原请求未执行，将按原对象、类型和金额记账；已完成则恢复回执，不重复记账。', '继续原请求', { type: 'warning', confirmButtonText: '确认继续', cancelButtonText: '返回' }) } catch { return }
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
  try { await ElMessageBox.confirm('请先核对奖罚记录和请求处理情况；原请求可能仍在处理。', '解除奖罚限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对' }) } catch { return }
  if (!escortPageDisposed && revision === auth.sessionRevision && disposeReview.value === marker) clearDisposeReview()
}
</script>

<style scoped lang="scss">
:global(.es-dispose-confirm .el-message-box__message p) { white-space: pre-line; overflow-wrap: anywhere; line-height: 1.8; }
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
.es-media-upload, .es-media-upload :deep(.el-upload) { width: 100%; }
.es-uploader:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.es-uploader .el-icon {
  font-size: 24px;
}
.es-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
