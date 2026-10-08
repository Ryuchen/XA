<template>
  <div class="operations">
    <div class="ap-page-head"><p class="ap-page-intro">管理积分兑换、奖品交付与会员权益，配置前台活动和运营内容。</p><el-button :loading="loading" @click="load">刷新</el-button></div>
    <nav class="operations-sections" aria-label="积分礼品功能分类">
      <button v-for="section in sections" :key="section.key" type="button"
        :aria-pressed="sectionKey === section.key" :class="{ 'is-active': sectionKey === section.key }"
        @click="selectSection(section.key)">
        <strong>{{ section.label }}</strong><span>{{ section.description }}</span>
      </button>
    </nav>
    <section class="operations-workspace" :aria-label="activeSection.label">
    <el-tabs v-model="tab">
      <el-tab-pane v-for="item in visibleTabs" :key="item.key" :label="item.label" :name="item.key"/>
    </el-tabs>
    <div v-if="tab === 'kook'" class="external-form"><el-alert :title="kook.connected ? 'KOOK自动积分运行中' : 'KOOK自动积分连接状态待核实'" :type="kook.connected ? 'success' : 'warning'" :closable="false"/><p>{{kook.rules}}</p><p>语音频道：{{kook.channel_count}} 个；已绑定：{{kook.binding_count}} 人，其中陪玩 {{kook.provider_count}} 人。</p><p>状态：{{kook.status}}；最近心跳：{{kook.heartbeat_at || '暂无'}}</p><p>会员在积分页获取本人绑定码，在KOOK签到大厅发送绑定码，随后发送“签到”。自动计分开启后，人工KOOK记分关闭。</p></div>
    <el-alert v-if="loadFailed" title="数据加载失败，请点击刷新重试" type="error" :closable="false"/>
    <div v-if="tab === 'external'" class="external-form"><el-alert title="录入已核对的真实记录。同一老板、同一天、同一来源只入账一次。小程序签到不冒充 KOOK 签到。" :closable="false"/><el-form label-width="130px"><el-form-item label="老板业务账户 ID"><el-input-number v-model="external.account_id" :min="1"/></el-form-item><el-form-item label="积分来源"><el-select v-model="external.kind"><el-option label="KOOK 每日签到（5 分）" value="KOOK_CHECKIN"/><el-option label="KOOK 在线时长（每 2 小时 1 分）" value="KOOK_ONLINE"/></el-select></el-form-item><el-form-item label="日期"><el-date-picker v-model="external.day" value-format="YYYY-MM-DD"/></el-form-item><el-form-item v-if="external.kind === 'KOOK_ONLINE'" label="当天在线秒数"><el-input-number v-model="external.online_seconds" :min="1" :max="86400"/></el-form-item><el-form-item label="来源记录编号"><el-input v-model="external.source_id" maxlength="100"/></el-form-item><el-button type="primary" :loading="saving" @click="recordExternal">核对并入账</el-button></el-form></div>
    <el-form v-else-if="tab === 'settings'" label-width="160px" class="external-form" v-loading="loading" :disabled="!canEdit || saving || loading || loadFailed"><el-alert title="仅消费积分，充值本身不加分；已完成订单的折后实付累计 VIP，取消和退款不计入，VIP 折扣与折扣券取优不叠加。" :closable="false"/><el-form-item label="消费积分"><el-switch v-model="settings.points_enabled"/></el-form-item><el-form-item label="生日消费倍率"><el-input-number v-model="settings.birthday_multiplier" :min="1" :max="10"/></el-form-item><el-form-item label="VIP 自动升级"><el-switch v-model="settings.vip_enabled"/></el-form-item><el-form-item label="当前赛季编号"><el-input v-model="settings.season_code" placeholder="例如 S8；更换编号后自动发放新赛季券"/></el-form-item><el-form-item label="赛季截止时间"><el-date-picker v-model="settings.season_ends_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ"/></el-form-item><el-button type="primary" :loading="saving" @click="saveSettings">保存运营设置</el-button></el-form><template v-else-if="tab !== 'kook'">
      <div class="toolbar"><div class="toolbar-title"><h2>{{ labels[tab] }}</h2><span>共 {{total}} 条记录</span></div><el-button v-if="canEdit && ['rewards','content','lotteries','packages','giftBenefits'].includes(tab)" type="primary" @click="edit()">新增{{labels[tab]}}</el-button></div>
      <el-table :data="rows" v-loading="loading" row-key="id">
        <el-table-column prop="id" label="ID" width="70"/><template v-if="tab === 'namings'"><el-table-column prop="provider_name" label="收礼陪玩"/><el-table-column prop="sponsor_name" label="赠礼老板"/><el-table-column prop="title" label="冠名文字"/><el-table-column prop="expires_at" label="累计到期时间" min-width="190"/></template>
        <el-table-column v-if="tab === 'rewards'" label="礼物" min-width="230"><template #default="{row}"><div class="gift-name"><img v-if="row.image_url" :src="media(row.image_url)" alt=""/><span>{{row.name}}</span></div></template></el-table-column>
        <el-table-column v-else-if="tab !== 'namings'" :label="tab === 'feed' ? '播报内容' : '名称'" min-width="240"><template #default="{row}">{{row.name || row.title || row.name_snapshot || kindLabel(row.kind) || (formatRmb(row.amount) + ' 元档位')}}<span v-if="tab === 'feed'">{{row.content}}</span></template></el-table-column>
        <el-table-column v-if="tab === 'rewards'" label="兑换积分" min-width="140" align="right"><template #default="{row}"><span class="numeric">{{Number(row.point_cost).toLocaleString('zh-CN')}}</span></template></el-table-column>
        <el-table-column v-if="tab === 'rewards'" label="库存" width="120" align="right"><template #default="{row}">{{row.stock === null ? '不限' : row.stock}}</template></el-table-column>
        <el-table-column v-if="tab === 'rewards'" prop="kind_label" label="交付方式" width="130"/>
        <el-table-column v-if="tab === 'claims'" label="老板 / 状态" min-width="180"><template #default="{row}">{{row.account_name || row.account}} · {{row.status_label}}</template></el-table-column>
        <el-table-column v-if="tab === 'lotteries'" label="每次积分 / 概率合计" min-width="180"><template #default="{row}">{{row.point_cost}} / {{row.prizes.reduce((n:number,p:any)=>n+p.probability,0)}} 万分点</template></el-table-column>
        <el-table-column v-if="tab === 'rules'" prop="template" label="模板" min-width="320"/>
        <el-table-column v-if="tab === 'tiers'" label="累计消费门槛" min-width="160"><template #default="{row}">¥{{formatRmb(row.consumption_threshold)}} · {{row.discount_rate / 10}} 折</template></el-table-column>
        <el-table-column v-if="tab === 'packages'" label="赠送 / 积分倍率" min-width="180"><template #default="{row}">¥{{formatRmb(row.gift_amount)}} / {{row.points_rate / POINTS_RATE_SCALE}} 倍</template></el-table-column>
        <el-table-column v-if="tab !== 'claims' && tab !== 'packages' && tab !== 'tiers'" label="状态" width="100"><template #default="{row}"><el-tag :type="active(row) ? 'success' : 'info'">{{active(row) ? '启用' : '停用'}}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="130" fixed="right"><template #default="{row}"><el-button v-if="canEdit || tab === 'claims'" link type="primary" @click="edit(row)">{{tab === 'claims' ? '处理交付' : '配置'}}</el-button></template></el-table-column>
      </el-table>
      <div v-if="total > pageSize" class="pagination-wrap"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" :pager-count="5" layout="prev, pager, next" background @current-change="fetchRows"/></div>
    </template>
    </section>
    <el-dialog v-model="dialog" :title="(form.id ? '配置' : '新增') + labels[tab]" width="880px" :close-on-click-modal="false" class="ap-modal">
      <el-form  :disabled="saving" class="ap-modal-form ap-form-grid" label-position="top">
        <template v-if="tab === 'rewards'">
          <el-form-item label="名称"><el-input v-model="form.name" maxlength="100"/></el-form-item><el-form-item label="商品标识"><el-input v-model="form.code" :disabled="!!form.id" placeholder="例如 gift-mooncake"/></el-form-item>
          <el-form-item label="VIP补差类别"><el-select v-model="form.vip_quota_kind"><el-option value="" label="按权益项目（TAG和冠名自动归类）"/><el-option value="FRIEND" label="好友位：各档总额补差"/><el-option value="BIRTHDAY_GIFT" label="生日礼物：VIP4起每年生日交付一次"/></el-select></el-form-item><el-form-item label="交付方式"><el-select v-model="form.kind"><el-option v-for="(label,key) in rewardKinds" :key="key" :value="key" :label="label"/></el-select></el-form-item>
          <el-form-item label="商品图片" class="ap-field--full"><el-input v-model="form.image_url"/><input type="file" accept="image/png,image/jpeg,image/webp" @change="upload"/></el-form-item>
          <el-form-item label="说明" class="ap-field--full"><el-input v-model="form.description" type="textarea" :rows="3"/></el-form-item>
          <el-form-item label="适用会员"><el-select v-model="form.audience"><el-option label="老板" value="BOSS"/><el-option label="陪玩" value="PROVIDER"/><el-option label="全部会员" value="ALL"/></el-select></el-form-item>
          <el-form-item label="兑换积分"><el-input-number v-model="form.point_cost" :min="0" :precision="0"/></el-form-item>
          <el-form-item label="在积分商城展示"><el-switch v-model="form.in_shop"/></el-form-item>
          <el-form-item label="库存"><el-switch v-model="unlimited" active-text="不限库存"/><el-input-number v-if="!unlimited" v-model="form.stock" :min="0" :precision="0"/></el-form-item>
          <el-form-item v-if="form.kind === 'POINTS'" label="奖励积分"><el-input-number v-model="form.point_value" :min="1" :precision="0"/></el-form-item>
          <el-form-item v-if="form.kind === 'BALANCE'" label="到账兴安币"><el-input-number v-model="balanceCoins" :min="0.1" :precision="1"/></el-form-item>
          <el-form-item v-if="['TAG','NAMING'].includes(form.kind)" label="有效天数"><el-input-number v-model="form.duration_days" :min="0"/><span class="hint">0 表示未设到期日</span></el-form-item>
          <el-form-item v-if="['TAG','NAMING'].includes(form.kind)" label="有效月数"><el-input-number v-model="form.duration_months" :min="0"/><span class="hint">自然月；设置后优先于天数</span></el-form-item><el-form-item v-if="form.kind === 'TAG'" label="高亮标签"><el-switch v-model="form.highlighted"/></el-form-item><el-form-item label="启用"><el-switch v-model="form.is_active"/></el-form-item>
        </template>
        <template v-if="tab === 'lotteries'">
          <el-form-item label="活动名称"><el-input v-model="form.name"/></el-form-item><el-form-item label="每次消耗积分"><el-input-number v-model="form.point_cost" :min="1" :precision="0"/></el-form-item>
          <template v-if="form.id"><el-form-item label="每天次数上限"><el-input-number v-model="form.daily_limit" :min="0"/><span class="hint">0 不限</span></el-form-item><el-form-item label="活动开始"><el-date-picker v-model="form.starts_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable/></el-form-item><el-form-item label="活动结束"><el-date-picker v-model="form.ends_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable/></el-form-item>
            <el-alert :title="'概率合计：'+ probabilityTotal +' / 10000；1 = 0.01%，100 = 1%。启用时必须恰好为 10000。'" :type="probabilityTotal === 10000 ? 'success' : 'warning'" :closable="false"/>
            <el-table :data="form.prizes"><el-table-column label="奖品" min-width="240"><template #default="{row}"><el-select v-model="row.reward" filterable><el-option v-for="reward in rewards" :key="reward.id" :value="reward.id" :label="reward.name"/></el-select></template></el-table-column><el-table-column label="中奖概率（万分点）" width="230"><template #default="{row}"><el-input-number v-model="row.probability" :min="0" :max="10000" :precision="0"/></template></el-table-column><el-table-column width="70"><template #default="{$index}"><el-button link @click="form.prizes.splice($index,1)">移除</el-button></template></el-table-column></el-table>
            <el-button @click="form.prizes.push({reward:null,probability:0})">添加奖项</el-button><el-form-item label="启用转盘"><el-switch v-model="form.enabled" :disabled="probabilityTotal !== 10000"/></el-form-item>
          </template><el-form-item label="参与说明" class="ap-field--full"><el-input v-model="form.rules" type="textarea" :rows="4"/></el-form-item>
        </template>
        <template v-if="tab === 'giftBenefits'"><el-form-item label="规则名称"><el-input v-model="form.name"/></el-form-item><el-form-item label="附送权益"><el-select v-model="form.reward" filterable><el-option v-for="reward in rewards.filter(r=>['NAMING','TAG','SERVICE'].includes(r.kind))" :key="reward.id" :value="reward.id" :label="reward.name"/></el-select></el-form-item><el-form-item label="赠礼门槛（元）"><el-input-number v-model="thresholdYuan" :min="0.01" :precision="2"/></el-form-item><el-form-item label="包含等于门槛"><el-switch v-model="form.inclusive"/></el-form-item><el-form-item label="累计活动金额"><el-switch v-model="form.cumulative"/><span class="hint">关闭时按本次赠送总额</span></el-form-item><el-form-item label="礼物分类 ID"><el-input-number v-model="form.category" :min="1"/><span class="hint">留空为所有礼物；分类 ID 见服务管理</span></el-form-item><el-form-item label="每人仅领一次"><el-switch v-model="form.once_per_account"/></el-form-item><el-form-item label="开始时间"><el-date-picker v-model="form.starts_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ"/></el-form-item><el-form-item label="结束时间"><el-date-picker v-model="form.ends_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ"/></el-form-item><el-form-item label="启用规则"><el-switch v-model="form.enabled"/></el-form-item></template><template v-if="tab === 'claims'"><el-descriptions :column="1" border><el-descriptions-item label="奖品">{{form.name_snapshot}}</el-descriptions-item><el-descriptions-item label="老板">{{form.account_name || form.account}}</el-descriptions-item><el-descriptions-item label="当前状态">{{form.status_label}}</el-descriptions-item><el-descriptions-item v-for="(value,key) in form.details" :key="key" :label="detailLabels[String(key)] || String(key)">{{value}}</el-descriptions-item></el-descriptions><el-form-item label="处理结果"><el-select v-model="claimStatus"><el-option label="交付中" value="FULFILLING"/><el-option label="已完成交付" value="COMPLETED"/><el-option v-if="form.source === 'REDEMPTION'" label="取消并退回积分" value="CANCELLED"/></el-select></el-form-item><el-form-item label="交付凭证 / 说明" class="ap-field--full"><el-input v-model="claimNote" type="textarea" maxlength="255" placeholder="快递单号、交付记录编号或取消原因"/></el-form-item></template>
        <template v-if="tab === 'namings'"><el-form-item label="冠名文字"><el-input v-model="form.title" maxlength="30"/></el-form-item><el-form-item label="列表展示"><el-switch v-model="form.is_visible"/></el-form-item><el-form-item label="累计到期">{{form.expires_at}}</el-form-item><el-alert title="时长来自已付款礼物，修改展示文案不会增加或扣减冠名时长。" :closable="false"/></template><template v-if="tab === 'rules'"><el-form-item label="播报事件">{{kindLabel(form.kind)}}</el-form-item><el-form-item label="内容模板" class="ap-field--full"><el-input v-model="form.template" type="textarea" :rows="3" maxlength="255"/><span class="hint">可用：{nickname} 老板昵称、{reward} 奖品、{level} 等级、{provider} 陪玩</span></el-form-item><el-form-item label="展示时长（小时）"><el-input-number v-model="form.visible_hours" :min="1" :max="8760"/></el-form-item><el-form-item label="昵称脱敏"><el-switch v-model="form.mask_nickname"/></el-form-item><el-form-item label="启用"><el-switch v-model="form.enabled"/></el-form-item></template>
        <template v-if="tab === 'feed'"><el-alert :title="form.content" :closable="false"/><el-form-item label="前台显示"><el-switch v-model="form.is_visible"/></el-form-item><el-form-item label="展示截止"><el-date-picker v-model="form.expires_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ"/></el-form-item></template>
        <template v-if="tab === 'content'"><el-alert title="入场弹窗自动展示近期活动和下单须知；首页热点仅展示最新活动消息。已结束活动保留在活动列表，按上架时间倒序，详情只显示海报。停用可隐藏活动。" :closable="false"/><el-form-item label="标题"><el-input v-model="form.title"/></el-form-item><el-form-item label="内容标识"><el-input v-model="form.code" :disabled="!!form.id"/></el-form-item><el-form-item label="展示位置"><el-select v-model="form.kind"><el-option v-for="(label,key) in contentKinds" :key="key" :label="label" :value="key"/></el-select></el-form-item><el-form-item label="海报" class="ap-field--full"><el-input v-model="form.image_url"/><input type="file" accept="image/png,image/jpeg,image/webp" @change="upload"/></el-form-item><el-form-item v-if="form.kind !== 'HOT'" label="正文" class="ap-field--full"><el-input v-model="form.content" type="textarea" :rows="8" placeholder="支持 p、h2、strong、ul、li、img 等富文本标签。可仅上传海报。"/></el-form-item><el-form-item label="上架时间"><el-date-picker v-model="form.starts_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable/></el-form-item><el-form-item :label="form.kind === 'HOT' ? '活动结束时间' : '下架时间'"><el-date-picker v-model="form.ends_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable/></el-form-item><el-form-item label="启用"><el-switch v-model="form.is_active"/></el-form-item></template>
        <template v-if="tab === 'tiers'"><el-form-item label="等级">{{form.name}}</el-form-item><el-form-item label="累计确认实销门槛（元）"><el-input-number v-model="thresholdYuan" :min="0" :precision="2"/></el-form-item><el-form-item label="每月折扣券"><span>VIP2/3/4/5为九折1/2/4/10张，VIP6八五折10张；每张最高优惠100元，月中升级补差。</span></el-form-item><el-form-item label="安全箱权益"><span>VIP1至VIP5共1张永久券，未用券随级别升折扣；VIP6有效报单满72小时、审核通过且无客诉后补一张。</span></el-form-item><el-form-item v-for="binding in [{key:'companion_service_ids',label:'1小时陪玩绑定'},{key:'safe_box_service_ids',label:'33安全箱代肝主套餐绑定'}]" :key="binding.key" :label="binding.label"><el-select v-model="form[binding.key]" multiple filterable collapse-tags collapse-tags-tooltip placeholder="请选择经核对的套餐"><el-option v-for="service in companionServices" :key="service.id" :value="service.id" :label="`${service.game} · ${service.name} · ${service.price/10} 兴安币/${service.unit || '份'} · #${service.id}`"/></el-select></el-form-item><el-form-item label="升级权益总额（按类别补差）" class="ap-field--full"><div><el-table :data="form.upgrade_rewards || []"><el-table-column label="权益" min-width="250"><template #default="{row}"><el-select v-model="row.reward" filterable><el-option v-for="reward in rewards.filter(r=>!r.in_shop && ['SERVICE','NAMING','TAG'].includes(r.kind))" :key="reward.id" :value="reward.id" :label="reward.name"/></el-select></template></el-table-column><el-table-column label="数量" width="190"><template #default="{row}"><el-input-number v-model="row.quantity" :min="1" :max="100"/></template></el-table-column><el-table-column width="70"><template #default="{$index}"><el-button link @click="form.upgrade_rewards.splice($index,1)">移除</el-button></template></el-table-column></el-table><el-button @click="(form.upgrade_rewards ||= []).push({reward:null,quantity:1})">添加权益</el-button></div></el-form-item><el-form-item v-for="(_,key) in form.benefits" :key="key" :label="String(key)"><el-input v-model="form.benefits[key]"/></el-form-item><el-form-item label="启用"><el-switch v-model="form.active"/></el-form-item></template>
        <template v-if="tab === 'packages'"><el-form-item label="实付充值（元）"><el-input-number v-model="thresholdYuan" :min="0.01" :precision="2"/></el-form-item><el-form-item label="赠送余额（元）"><el-input-number v-model="giftYuan" :min="0" :precision="2"/></el-form-item><el-form-item label="消费积分倍率"><el-input-number v-model="pointsMultiplier" :min="1" :max="10" :precision="2" :step="0.1"/></el-form-item><el-alert title="只在消费此充值批次余额时计算积分；充值本身不加积分。修改后仅影响新的充值记录。" :closable="false"/></template>
        <el-form-item v-if="['rewards','rules','feed','content'].includes(tab) && !(tab === 'content' && form.kind === 'HOT')" label="展示排序"><el-input-number v-model="form.sort_order"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog=false">关闭</el-button><el-button type="primary" :loading="saving" @click="save" :disabled="tab === 'claims' && ['COMPLETED','CANCELLED'].includes(form.status)">保存</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { formatRmb, rmbToRawAmount, toCoin, toRawAmount } from '@xa/money'
const POINTS_RATE_SCALE = 100
import { request } from '@/api/request'
import { useAuthStore } from '@/stores/auth'
const auth=useAuthStore(),tab=ref('rewards'),rows=ref<any[]>([]),total=ref(0),page=ref(1),loading=ref(false),saving=ref(false),dialog=ref(false),form=ref<any>({}),rewards=ref<any[]>([]),unlimited=ref(true),balanceCoins=ref(0),thresholdYuan=ref(0),giftYuan=ref(0),pointsMultiplier=ref(1),claimStatus=ref('FULFILLING'),claimNote=ref('')
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
const activeSection = computed(() => sections.find(section => section.key === sectionKey.value)!)
const visibleTabs = computed(() => activeSection.value.tabs.filter(item => !item.perm || auth.hasPerm(item.perm)))
function selectSection(key: string) {
  if (sectionKey.value === key) return
  sectionKey.value = key
  tab.value = visibleTabs.value[0].key
}
watch(tab, load)
const endpoints:Record<string,string>={namings:'provider-namings',giftBenefits:'gift-benefits',rewards:'club-rewards',lotteries:'lotteries',claims:'club-claims',rules:'broadcast-rules',feed:'broadcasts',content:'club-content',tiers:'vip-tiers',packages:'recharge-packages'}
const labels:Record<string,string>={namings:'陪玩冠名',giftBenefits:'赠礼附送权益',rewards:'积分好礼',lotteries:'幸运转盘',claims:'奖品交付',rules:'播报规则',feed:'播报记录',content:'运营内容',tiers:'VIP 权益',packages:'预存档位'}
const rewardKinds={PHYSICAL:'实物寄送',VIRTUAL:'游戏内交付',SERVICE:'服务权益',NAMING:'冠名权益',TAG:'自定义 TAG',BALANCE:'余额兑换',POINTS:'积分'}
const contentKinds={RIGHTS:'老板权益',HOT:'俱乐部活动',ORDER_RULES:'下单须知',PROVIDER_RULES:'打手保障',AFTERSALE_RULES:'售后标准',POINT_RULES:'积分规则'}
const broadcastKinds:Record<string,string>={GIFT:'礼物单播报',LOTTERY:'转盘中奖播报',NAMING:'冠名播报',RECHARGE:'充值播报',UPGRADE:'升级播报',REVIEW:'好评广播',BIRTHDAY:'生日祝福',LOTTERY_SERVICE:'抽中体验单',LOTTERY_NAMING:'抽中冠名',LOTTERY_TAG:'抽中 TAG'}
const detailLabels:Record<string,string>={recipient:'收件人',phone:'联系电话',address:'收货地址',game_uid:'游戏 UID',note:'补充说明',tag:'指定 TAG',provider_name:'冠名陪玩'}
const external=reactive({account_id:undefined as number|undefined,kind:'KOOK_CHECKIN',day:'',online_seconds:7200,source_id:''})
const canEdit=computed(()=>auth.hasPerm('engagement:edit')),probabilityTotal=computed(()=>form.value.prizes?.reduce((n:number,p:any)=>n+(Number(p.probability)||0),0)||0)
const active=(row:any)=>row.is_active??row.enabled??row.is_visible??true,kindLabel=(kind:string)=>broadcastKinds[kind]||'',media=(path:string)=>path.startsWith('/media/')?new URL(import.meta.env.VITE_API_BASE||'/api/admin',location.origin).origin+path:path
const settings=ref<any>({}),companionServices=ref<any[]>([])
async function saveSettings(){saving.value=true;try{await request({url:'/club-config/',method:'put',data:settings.value});ElMessage.success('已保存')}finally{saving.value=false}}
let seq=0
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
      const res = await request({ url: '/' + endpoints[key] + '/', params: { page: page.value, page_size: pageSize } })
      if (version === seq) { rows.value = res.data.list; total.value = res.data.total }
    }
  } catch {
    if (version === seq) { rows.value = []; total.value = 0; loadFailed.value = true }
  } finally {
    if (version === seq) loading.value = false
  }
}
async function edit(row?:any){form.value=row?JSON.parse(JSON.stringify(row)):{name:'',audience:'BOSS',title:'',code:'',kind:tab.value==='content'?'HOT':'PHYSICAL',description:'',image_url:'',point_cost:tab.value==='lotteries'?100:0,point_value:0,balance_amount:0,stock:null,in_shop:true,is_active:false,enabled:false,duration_days:0,duration_months:0,highlighted:false,sort_order:0,content:'',starts_at:null,ends_at:null,rules:'',prizes:[],active:true,threshold:0,inclusive:false,cumulative:false,once_per_account:false,category:null};unlimited.value=form.value.stock===null;balanceCoins.value=toCoin(form.value.balance_amount);thresholdYuan.value=Number(formatRmb(form.value.consumption_threshold??form.value.recharge_threshold??form.value.amount??form.value.threshold??0));giftYuan.value=Number(formatRmb(form.value.gift_amount));pointsMultiplier.value=(form.value.points_rate??POINTS_RATE_SCALE)/POINTS_RATE_SCALE;claimStatus.value='FULFILLING';claimNote.value='';if(['lotteries','tiers','giftBenefits'].includes(tab.value)){const res=await request({url:'/club-rewards/',params:{page_size:500}});rewards.value=res.data.list}if(tab.value==='tiers'){const res=await request({url:'/vip-tiers/service-options/'});companionServices.value=res.data}dialog.value=true}
async function save(){saving.value=true;try{const data={...form.value};if(tab.value==='rewards'){data.stock=unlimited.value?null:data.stock;data.balance_amount=toRawAmount(balanceCoins.value)}if(tab.value==='giftBenefits')data.threshold=rmbToRawAmount(thresholdYuan.value);if(tab.value==='tiers'){data.consumption_threshold=rmbToRawAmount(thresholdYuan.value);delete data.recharge_threshold;}if(tab.value==='packages'){data.amount=rmbToRawAmount(thresholdYuan.value);data.gift_amount=rmbToRawAmount(giftYuan.value);data.points_rate=Math.round(pointsMultiplier.value*POINTS_RATE_SCALE)}const base='/'+endpoints[tab.value]+'/';if(tab.value==='claims')await request({url:base+data.id+'/process/',method:'post',data:{status:claimStatus.value,note:claimNote.value}});else if(tab.value==='lotteries'&&data.id)await request({url:base+data.id+'/configure/',method:'post',data});else await request({url:base+(data.id?data.id+'/':''),method:data.id?'patch':'post',data});dialog.value=false;ElMessage.success('已保存');await load()}finally{saving.value=false}}
async function upload(event:Event){const file=(event.target as HTMLInputElement).files?.[0];if(!file)return;const data=new FormData();data.append('image',file);const res=await request({url:'/club-content-image/',method:'post',data});form.value.image_url=res.data.url}
async function recordExternal(){saving.value=true;try{await request({url:'/external-points/',method:'post',data:external});ElMessage.success('积分已入账')}finally{saving.value=false}}
onMounted(load)
</script>
<style scoped>
.operations-sections { display: flex; gap: 8px; margin-bottom: 20px; padding: 6px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.operations-sections button { flex: 1; min-width: 0; text-align: left; padding: 14px 18px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--foreground); cursor: pointer; }
.operations-sections button:hover { background: var(--secondary); }
.operations-sections button.is-active { background: color-mix(in srgb, var(--primary) 7%, var(--card)); border-color: color-mix(in srgb, var(--primary) 25%, var(--border)); }
.operations-sections strong { display: block; font-size: 15px; font-weight: 600; }
.operations-sections .is-active strong { color: var(--primary); }
.operations-sections span { display: block; margin-top: 5px; color: var(--muted-foreground); font-size: 12px; }
.operations-workspace { padding: 8px 24px 24px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); min-width: 0; }
.operations-workspace :deep(.el-tabs__item) { height: 52px; }
.operations-workspace :deep(.el-tabs__nav-wrap::after) { height: 1px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 22px 0 18px; }
.toolbar-title { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.toolbar h2 { margin: 0; font-size: 16px; font-weight: 600; }
.toolbar-title span { font-size: 12px; color: var(--muted-foreground); }
.gift-name { display: flex; align-items: center; gap: 12px; }
.gift-name img { flex: 0 0 40px; width: 40px; height: 40px; object-fit: contain; background: var(--secondary); border: 1px solid var(--border); border-radius: 8px; }
.numeric { font-weight: 600; font-variant-numeric: tabular-nums; }
.hint { font-size: 12px; color: var(--muted-foreground); margin: 0 12px; }
.external-form { max-width: 720px; padding: 16px 0; }
.external-form .el-form { margin-top: 24px; }
.el-alert { margin-bottom: 20px; }
.el-input, .el-select { width: 100%; }
input[type=file] { margin-top: 12px; max-width: 100%; }
.external-form .el-input-number { margin-right: 8px; }
.el-descriptions { margin-bottom: 24px; }
@media (max-width: 720px) {
  .operations-sections { gap: 4px; margin-bottom: 12px; }
  .operations-sections button { padding: 12px 6px; text-align: center; }
  .operations-sections strong { font-size: 13px; }
  .operations-sections span { display: none; }
  .operations-workspace { padding: 4px 12px 16px; }
  .toolbar { margin: 14px 0; flex-wrap: wrap; }
  .toolbar-title { gap: 8px; }
}
</style>
