export type AdminGameplayKind = 'STANDARD' | 'ROULETTE'
export type AdminGameplayTheme = 'roulette' | 'experience' | 'companion' | 'fun' | 'mission' | 'extraction' | 'prison' | 'custom'

export interface AdminGameplayItem {
  id: number
  name: string
  code: string
  kind: AdminGameplayKind
  subtitle: string
  theme: AdminGameplayTheme
  coverUrl: string
  rules: string
  details: string
  sortOrder: number
  active: boolean
  archived: boolean
  serviceCount: number
}

export const gameplayThemes: Record<AdminGameplayTheme, { name: string; symbol: string; className: string }> = {
  roulette: { name: '转盘', symbol: '✦', className: 'from-violet-500 via-blue-500 to-sky-400' },
  experience: { name: '体验', symbol: '01', className: 'from-sky-400 via-blue-500 to-indigo-600' },
  companion: { name: '陪玩', symbol: '∞', className: 'from-blue-500 via-indigo-500 to-slate-700' },
  fun: { name: '趣味', symbol: '✧', className: 'from-cyan-400 via-sky-500 to-blue-600' },
  mission: { name: '任务', symbol: '✓', className: 'from-slate-700 via-blue-800 to-sky-500' },
  extraction: { name: '带出', symbol: '↗', className: 'from-blue-900 via-indigo-700 to-cyan-400' },
  prison: { name: '监狱', symbol: '▥', className: 'from-slate-800 via-slate-600 to-blue-500' },
  custom: { name: '定制', symbol: '+', className: 'from-slate-500 via-slate-700 to-blue-900' },
}

export const initialAdminGameplays: AdminGameplayItem[] = [
  {
    id: 1, name: '娱乐双排', code: 'companion-duo', kind: 'STANDARD', subtitle: '轻松沟通，快乐上分', theme: 'companion', coverUrl: '',
    rules: '下单前核对游戏区服与预计时长；两名陪玩共同履约，游戏中保持清晰沟通。',
    details: '适合希望轻松组队、边玩边交流的老板。\n\n下单后由客服确认两位陪玩的档期与游戏资料。',
    sortOrder: 10, active: true, archived: false, serviceCount: 2,
  },
  {
    id: 2, name: '技术提升', code: 'technical-rank', kind: 'STANDARD', subtitle: '对局复盘，技术提升', theme: 'mission', coverUrl: '',
    rules: '开局前确认目标段位与擅长位置；结束后由陪玩提交战绩与履约凭证。',
    details: '围绕复盘、节奏和实战配合展开。\n\n具体目标以所属商品和订单确认内容为准。',
    sortOrder: 20, active: true, archived: false, serviceCount: 2,
  },
  {
    id: 3, name: '周末盲盒', code: 'weekend-roulette', kind: 'ROULETTE', subtitle: '随机玩法，惊喜开局', theme: 'roulette', coverUrl: '',
    rules: '每单仅产生一个服务目标；余额支付成功时确定结果，报单审核后结算。',
    details: '每个所属商品需独立配置 2–8 个服务目标与概率权重。\n\n目标名称不可重复，权重为 1–10000 的整数。',
    sortOrder: 30, active: true, archived: false, serviceCount: 1,
  },
  {
    id: 4, name: '新客体验', code: 'first-experience', kind: 'STANDARD', subtitle: '低门槛认识合拍陪玩', theme: 'experience', coverUrl: '',
    rules: '体验资格与限购条件由所属商品决定；下单前需核对老板账号可用资格。',
    details: '面向新客的短时体验入口。\n\n本玩法只组织目录，具体价格、资格和套餐人数在商品内配置。',
    sortOrder: 40, active: true, archived: false, serviceCount: 0,
  },
  {
    id: 5, name: '撤离挑战', code: 'extraction-challenge', kind: 'STANDARD', subtitle: '目标导向的协作挑战', theme: 'extraction', coverUrl: '',
    rules: '停用期间不展示入口，也不接受新订单；已有订单继续按下单快照履约。',
    details: '用于演示停用玩法的目录状态。恢复展示前，应先核对所属商品、平台与难度。',
    sortOrder: 50, active: false, archived: false, serviceCount: 0,
  },
  {
    id: 6, name: '任务冲刺', code: 'mission-sprint', kind: 'STANDARD', subtitle: '明确任务，集中完成', theme: 'fun', coverUrl: '',
    rules: '任务范围、完成标准和所需局数以商品说明及订单确认结果为准。',
    details: '尚未关联商品的玩法示例，可在保存后继续配置服务商品。',
    sortOrder: 60, active: true, archived: false, serviceCount: 0,
  },
  {
    id: 7, name: '监狱风云', code: 'prison-break', kind: 'STANDARD', subtitle: '历史主题玩法', theme: 'prison', coverUrl: '',
    rules: '已归档玩法不在老板端展示，历史订单仍保留原玩法与商品快照。',
    details: '恢复后仍保持停用，需核对商品后再单独启用。', sortOrder: 70, active: false, archived: true, serviceCount: 1,
  },
  {
    id: 8, name: '旧版趣味厅', code: 'legacy-fun', kind: 'STANDARD', subtitle: '旧目录归档示例', theme: 'custom', coverUrl: '',
    rules: '归档记录仅供核对与恢复，不直接编辑。', details: '历史订单不受归档与恢复操作影响。',
    sortOrder: 80, active: false, archived: true, serviceCount: 1,
  },
]
