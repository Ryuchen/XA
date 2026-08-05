/**
 * 陪玩端金额格式化出口。
 *
 * 实现集中在共享包 `@xa/money`（仓库内 packages/money），此处仅做转发，
 * 保证三端换算口径一致：10 个内部账务单位 = 1 兴安币。
 */
import { COIN_UNIT, formatXaCoin, toCoin, toRawAmount } from '@xa/money'

export { COIN_UNIT, formatXaCoin, toCoin, toRawAmount }
