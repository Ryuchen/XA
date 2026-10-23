<template>
  <div class="es-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理入驻陪玩师的资质、状态与业绩</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('escort:edit')" type="primary" @click="onCreate">创建陪玩账号</el-button>
      </div>
    </div>

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

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="昵称" min-width="180">
        <template #default="{ row }"><div class="es-person"><el-avatar :src="row.avatar_url" :size="36">{{ (row.display_name || row.username || '陪').slice(0, 1) }}</el-avatar><div><strong>{{ row.display_name || row.username }}</strong><span class="es-secondary">{{ row.qualifications?.length ? row.qualifications.length + ' 项接单资格' : '资格待配置' }}</span></div></div></template>
      </el-table-column>
      <el-table-column label="服务范围" min-width="220">
        <template #default="{ row }"><div class="es-games"><el-tag v-for="name in row.game_category_names" :key="name" size="small" effect="plain">{{ name }}</el-tag><span v-if="!row.game_category_names?.length" class="es-secondary">尚未配置游戏</span></div><el-tooltip :content="(row.service_item_names || []).join('、') || '请在编辑中配置可接服务项'" placement="top"><span class="es-secondary">{{ row.service_items?.length || 0 }} 项服务技能</span></el-tooltip></template>
      </el-table-column>
      <el-table-column label="性别" width="80">
        <template #default="{ row }">
          <el-tag :type="GENDER[row.gender]?.type" effect="light" round>{{ row.gender_display || GENDER[row.gender]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="接单资格" min-width="240"><template #default="{ row }"><el-tag v-for="q in (row.qualifications || []).filter((q: any) => q.is_active)" :key="q.id" size="small" style="margin:2px">{{ q.label }}</el-tag><span v-if="!row.qualifications?.length">待配置</span></template></el-table-column>
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
        <template #default="{ row }">
          <el-tag :type="ESCORT_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="认证" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_verified ? 'success' : 'info'" effect="light" round>{{ row.is_verified ? '已认证' : '未认证' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('escort:edit')" link type="primary" @click="onEdit(row)">编辑</el-button>
          <el-dropdown trigger="click" class="es-more">
            <el-button link>更多</el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item v-if="auth.hasPerm('escort:verify')" @click="onVerify(row)">{{ row.is_verified ? '取消认证' : '认证' }}</el-dropdown-item>
              <el-dropdown-item v-if="auth.hasPerm('escort:dispose')" @click="onDispose(row)">奖罚</el-dropdown-item>
              <el-dropdown-item v-if="auth.hasPerm('escort:deposit_refund')" @click="onDepositRefund(row)">退还押金</el-dropdown-item>
              <el-dropdown-item @click="banTarget = row; banVisible = true">封禁记录</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" title="编辑陪玩资料" width="800px" class="es-dialog ap-modal">
    <el-form :model="editForm"   class="ap-modal-form ap-form-grid" label-position="top">
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
              <el-upload class="es-media-upload"
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
              <el-upload class="es-media-upload"
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
              <el-upload class="es-media-upload"
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
            <el-form-item label="接单资格" class="ap-field--full"><ProviderQualificationsEditor v-model="editForm.qualifications" :games="gameOptions" /></el-form-item>
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
          <el-select v-model="editForm.deposit_mode" style="width: 100%" :disabled="editForm.deposit_paid > 0">
            <el-option value="CASH" label="现金缴纳：200元" />
            <el-option value="EARNINGS" label="打单抵扣：400元" />
          </el-select>
          <div class="tip">抵扣模式须通过认证；净收益60%抵押金、余款冻结72小时。已有押金须先退还再切换。</div>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="应缴押金"><el-input :model-value="editForm.deposit_mode === 'EARNINGS' ? '400元' : '200元'" readonly /></el-form-item>
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
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="createVisible" title="创建陪玩账号" width="800px" class="es-dialog ap-modal">
    <el-form :model="createForm" :rules="createRules" ref="createFormRef"   class="ap-modal-form ap-form-grid" label-position="top">
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
            <el-form-item label="接单资格" class="ap-field--full"><ProviderQualificationsEditor v-model="createForm.qualifications" :games="gameOptions" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="押金缴纳模式">
              <el-select v-model="createForm.deposit_mode" style="width: 100%"><el-option value="CASH" label="现金缴纳：200元" /><el-option value="EARNINGS" label="打单抵扣：400元" /></el-select>
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
      <el-button @click="createVisible = false">取消</el-button>
      <el-button type="primary" :loading="creating" @click="onCreateSave">创建</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="disposeVisible" title="奖励 / 罚款" width="480px" class="ap-modal">
    <el-form  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="陪玩">{{ current?.display_name || current?.username }}</el-form-item>
      <el-form-item label="类型" class="ap-field--full">
        <el-radio-group v-model="disposeType">
          <el-radio label="REWARD">奖励</el-radio>
          <el-radio label="PENALTY">罚款</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="金额(兴安币)">
        <el-input-number v-model="disposeYuan" :min="0" :precision="2" :step="10" style="width: 100%" />
        <div class="tip">{{ disposeType === 'REWARD' ? '奖励将增加陪玩余额' : '罚款将扣减陪玩余额（不可使余额为负）' }}</div>
      </el-form-item>
      <el-form-item label="原因" class="ap-field--full">
        <el-input v-model="disposeReason" type="textarea" :rows="2" placeholder="奖罚原因" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="disposeVisible = false">取消</el-button>
      <el-button type="primary" :loading="disposing" @click="onDisposeSave">确定</el-button>
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
import { onMounted, reactive, ref } from 'vue'
import EscortSkillSelect from '@/components/EscortSkillSelect.vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Plus, VideoCamera } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import AccountBanDialog from '@/components/AccountBanDialog.vue'
import { escortApi, escortLevelApi, gameCategoryApi, serviceItemApi } from '@/api/modules'
import { ESCORT_STATUS, GENDER } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
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

const dialogVisible = ref(false)
const saving = ref(false)
const editForm = reactive<any>({})
const depositRequiredYuan = ref<number>(0)
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

async function onEdit(row: any) {
  await Promise.all([ensureLevels(), ensureGames(), ensureServiceItems()])
  Object.assign(editForm, row)
  editForm.qualifications = JSON.parse(JSON.stringify(row.qualifications || []))
  // DRF DecimalField 序列化为字符串，el-input-number 需要 Number
  editForm.win_rate = row.win_rate != null ? Number(row.win_rate) : 0
  // 后端 game_categories 返回 id 数组，多选组件直接绑定；拷贝一份避免污染表格行
  editForm.game_categories = Array.isArray(row.game_categories) ? [...row.game_categories] : []
  editForm.service_items = Array.isArray(row.service_items) ? [...row.service_items] : []
  editHourlyCoins.value = Number(amountToXaCoin(row.price_per_hour || 0))
  depositRequiredYuan.value = Number(amountToXaCoin(row.deposit_required))
  editFiles.avatar = editFiles.intro_video = editFiles.cheat_proof = null
  editPreview.avatar = row.avatar_url || ''
  editPreview.intro_video = row.intro_video_url || ''
  editPreview.cheat_proof = row.cheat_proof_url || ''
  dialogVisible.value = true
}

async function onSave() {
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
      deposit_required: xaCoinToAmount(depositRequiredYuan.value),
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
    await escortApi.update(editForm.id, payload)
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.load()
  } finally {
    saving.value = false
  }
}

async function onVerify(row: any) {
  await escortApi.action(row.id, 'verify', { is_verified: !row.is_verified })
  ElMessage.success('操作成功')
  tableRef.value.load()
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
  username: [{ required: true, message: '请输入登录账号', trigger: 'blur' }],
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
  await Promise.all([ensureLevels(), ensureGames(), ensureServiceItems()])
  Object.assign(createForm, defaultCreateForm())
  createDepositYuan.value = 0
  createFiles.avatar = createFiles.intro_video = createFiles.cheat_proof = null
  createPreview.avatar = createPreview.intro_video = createPreview.cheat_proof = ''
  createVisible.value = true
  createFormRef.value?.clearValidate()
}

async function onCreateSave() {
  if (!createFormRef.value) return
  await createFormRef.value.validate(async (valid) => {
    if (!valid) return
    creating.value = true
    try {
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
      await escortApi.create(fd)
      ElMessage.success('陪玩账号创建成功')
      createVisible.value = false
      tableRef.value.load()
    } finally {
      creating.value = false
    }
  })
}

const disposeVisible = ref(false)
const disposing = ref(false)
const current = ref<any>(null)
const disposeType = ref<'REWARD' | 'PENALTY'>('REWARD')
const disposeYuan = ref<number>(0)
const disposeReason = ref('')

function onDispose(row: any) {
  current.value = row
  disposeType.value = 'REWARD'
  disposeYuan.value = 0
  disposeReason.value = ''
  disposeVisible.value = true
}

async function onDisposeSave() {
  const amount = xaCoinToAmount(disposeYuan.value)
  if (amount <= 0) {
    ElMessage.warning('金额必须大于 0')
    return
  }
  disposing.value = true
  try {
    await escortApi.action(current.value.id, 'dispose', {
      dispose_type: disposeType.value,
      amount,
      reason: disposeReason.value,
    })
    ElMessage.success('操作成功')
    disposeVisible.value = false
    tableRef.value.load()
  } finally {
    disposing.value = false
  }
}
</script>

<style scoped lang="scss">
.es-more { margin-left: 14px; vertical-align: middle; }
.es-person { display:flex; align-items:center; gap:10px; }
.es-secondary { display:block; color:var(--muted-foreground); font-size:12px; line-height:1.7; }
.es-games { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:4px; }

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
