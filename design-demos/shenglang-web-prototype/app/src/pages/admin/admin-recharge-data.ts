import { formatWaveAmount } from '@/lib/format'

export type AdminRechargeRole = 'CUSTOMER' | 'PROVIDER'

export interface AdminRechargeRecord {
  id: number
  walletId: number
  accountId: number
  userRole: AdminRechargeRole
  username: string
  nickname: string
  phone: string
  bossNo: string
  bossTypeName: string | null
  amount: number
  giftAmount: number
  totalAmount: number
  tradeNo: string
  proofImageUrl: string | null
  remark: string
  operatorName: string
  createdAt: string
}

function demoProof(recordId: number, tradeNo: string, paid: string, gift: string) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="1280" viewBox="0 0 960 1280"><rect width="960" height="1280" fill="#f4f7fb"/><rect x="54" y="54" width="852" height="1172" rx="32" fill="#fff" stroke="#d8e1ee" stroke-width="2"/><rect x="54" y="54" width="852" height="168" rx="32" fill="#10285b"/><path d="M54 190h852v32H54z" fill="#10285b"/><text x="104" y="134" font-family="Arial,sans-serif" font-size="34" font-weight="700" fill="#fff">声浪电竞 · 示例收款凭证</text><text x="104" y="180" font-family="Arial,sans-serif" font-size="20" fill="#bcd8ff">RECHARGE RECEIPT / DEMO ONLY</text><rect x="104" y="278" width="128" height="36" rx="18" fill="#e7efff"/><text x="128" y="303" font-family="Arial,sans-serif" font-size="18" fill="#2563eb">示例数据</text><text x="104" y="390" font-family="Arial,sans-serif" font-size="22" fill="#64748b">实际收款（人民币）</text><text x="104" y="466" font-family="Arial,sans-serif" font-size="62" font-weight="700" fill="#10285b">¥ ${paid}</text><line x1="104" y1="526" x2="856" y2="526" stroke="#e2e8f0"/><text x="104" y="594" font-family="Arial,sans-serif" font-size="21" fill="#64748b">充值赠送</text><text x="856" y="594" text-anchor="end" font-family="Arial,sans-serif" font-size="28" fill="#0f172a">${formatWaveAmount(gift)} 浪花</text><text x="104" y="682" font-family="Arial,sans-serif" font-size="21" fill="#64748b">收款流水号</text><text x="104" y="730" font-family="monospace" font-size="23" fill="#0f172a">${tradeNo}</text><text x="104" y="818" font-family="Arial,sans-serif" font-size="21" fill="#64748b">充值记录编号</text><text x="856" y="818" text-anchor="end" font-family="monospace" font-size="24" fill="#0f172a">#${recordId}</text><rect x="104" y="894" width="752" height="140" rx="20" fill="#f5f8fd"/><text x="136" y="950" font-family="Arial,sans-serif" font-size="22" fill="#334155">原型凭证 · 不对应任何真实收款</text><text x="136" y="994" font-family="Arial,sans-serif" font-size="18" fill="#64748b">仅用于核对凭证缩略图、预览与异常状态。</text><text x="104" y="1148" font-family="Arial,sans-serif" font-size="18" fill="#94a3b8">SHENGLANG ESPORTS · PROTOTYPE</text></svg>`
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`
}

export const initialAdminRecharges: AdminRechargeRecord[] = [
  {
    id: 5008,
    walletId: 1001,
    accountId: 101,
    userRole: 'CUSTOMER',
    username: 'boss_chen',
    nickname: '陈默',
    phone: '13800001001',
    bossNo: 'B1001',
    bossTypeName: '星海老板',
    amount: 50000,
    giftAmount: 6000,
    totalAmount: 56000,
    tradeNo: 'DEMO-PAY-20261104-5008',
    proofImageUrl: demoProof(5008, 'DEMO-PAY-20261104-5008', '500.00', '600'),
    remark: '线下收款已核对，实充与赠送分别入账。',
    operatorName: '客服岚',
    createdAt: '2026-11-04 16:42',
  },
  {
    id: 5007,
    walletId: 2001,
    accountId: 201,
    userRole: 'PROVIDER',
    username: 'provider_lan',
    nickname: '小岚',
    phone: '13900002001',
    bossNo: '',
    bossTypeName: null,
    amount: 20000,
    giftAmount: 2000,
    totalAmount: 22000,
    tradeNo: 'DEMO-PAY-20261104-5007',
    proofImageUrl: demoProof(5007, 'DEMO-PAY-20261104-5007', '200.00', '200'),
    remark: '陪玩账户充值，已核验收款账户。',
    operatorName: '运营管理员',
    createdAt: '2026-11-04 14:18',
  },
  {
    id: 5006,
    walletId: 1002,
    accountId: 102,
    userRole: 'CUSTOMER',
    username: 'boss_luo',
    nickname: '洛川',
    phone: '13800001002',
    bossNo: 'B1002',
    bossTypeName: '逐浪老板',
    amount: 30000,
    giftAmount: 3000,
    totalAmount: 33000,
    tradeNo: 'DEMO-PAY-20261104-5006',
    proofImageUrl: demoProof(5006, 'DEMO-PAY-20261104-5006', '300.00', '300'),
    remark: '按活动档位赠送，活动规则已复核。',
    operatorName: '客服岚',
    createdAt: '2026-11-04 11:06',
  },
  {
    id: 5005,
    walletId: 1003,
    accountId: 103,
    userRole: 'CUSTOMER',
    username: 'boss_xu',
    nickname: '许星河',
    phone: '13800001003',
    bossNo: 'B1003',
    bossTypeName: '听澜老板',
    amount: 10000,
    giftAmount: 0,
    totalAmount: 10000,
    tradeNo: 'DEMO-PAY-20261103-5005',
    proofImageUrl: demoProof(5005, 'DEMO-PAY-20261103-5005', '100.00', '0'),
    remark: '仅实充，无赠送。',
    operatorName: '值班客服',
    createdAt: '2026-11-03 21:32',
  },
  {
    id: 5004,
    walletId: 1004,
    accountId: 104,
    userRole: 'CUSTOMER',
    username: 'boss_zhou',
    nickname: '周眠',
    phone: '13800001004',
    bossNo: 'B1004',
    bossTypeName: '初识老板',
    amount: 10000,
    giftAmount: 500,
    totalAmount: 10500,
    tradeNo: 'DEMO-PAY-20261103-5004',
    proofImageUrl: null,
    remark: '历史记录未留存凭证，需要时联系原经办人补充。',
    operatorName: '值班客服',
    createdAt: '2026-11-03 18:09',
  },
  {
    id: 5003,
    walletId: 1001,
    accountId: 101,
    userRole: 'CUSTOMER',
    username: 'boss_chen',
    nickname: '陈默',
    phone: '13800001001',
    bossNo: 'B1001',
    bossTypeName: '星海老板',
    amount: 20000,
    giftAmount: 2000,
    totalAmount: 22000,
    tradeNo: 'DEMO-PAY-20261103-5003',
    proofImageUrl: demoProof(5003, 'DEMO-PAY-20261103-5003', '200.00', '200'),
    remark: '已完成到账复核。',
    operatorName: '客服岚',
    createdAt: '2026-11-03 16:42',
  },
  {
    id: 5002,
    walletId: 1005,
    accountId: 105,
    userRole: 'CUSTOMER',
    username: 'boss_legacy',
    nickname: '历史停用账户',
    phone: '13800001005',
    bossNo: 'B1005',
    bossTypeName: '初识老板',
    amount: 5000,
    giftAmount: 0,
    totalAmount: 5000,
    tradeNo: 'DEMO-PAY-20261031-5002',
    proofImageUrl: demoProof(5002, 'DEMO-PAY-20261031-5002', '50.00', '0'),
    remark: '停用前历史记录，仅供审计查看。',
    operatorName: '运营管理员',
    createdAt: '2026-10-31 14:08',
  },
  {
    id: 5001,
    walletId: 1006,
    accountId: 106,
    userRole: 'CUSTOMER',
    username: 'mountain_guest',
    nickname: '山间来客',
    phone: '13800001006',
    bossNo: 'B1006',
    bossTypeName: '初识老板',
    amount: 8000,
    giftAmount: 0,
    totalAmount: 8000,
    tradeNo: '',
    proofImageUrl: null,
    remark: '历史线下充值未登记第三方流水号，按原经办记录核对。',
    operatorName: '运营管理员',
    createdAt: '2026-10-28 10:16',
  },
]
