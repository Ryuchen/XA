/**
 * XA 共享金额工具的类型声明（运行时实现见同目录 index.js）。
 *
 * 金额口径：内部账务单位整数，10 个账务单位 = 1 兴安币。
 * 换算只在「展示 / 输入」边界发生，业务代码禁止写裸的 /10 *10 /100 *100。
 */

/** 1 兴安币对应的内部账务单位数量。 */
export declare const COIN_UNIT: number;
export declare const RMB_TO_COIN_RATE: number;
/** 人民币元文本转人民币分；仅接受非负、最多两位小数，非法或不安全整数返回 0。 */
export declare function parseRmbFen(yuan: number | string | null | undefined): number;
/** 人民币分整数显示为两位小数的元；不使用兴安币汇率。 */
export declare function formatRmbFen(fen: number | null | undefined): string;
export declare function rmbToRawAmount(yuan: number | string | null | undefined): number;
export declare function formatRmb(amount: number | null | undefined): string;

/**
 * 内部账务单位 → 兴安币（浮点）。
 * @param amount 内部账务单位整数；非法输入按 0 处理。
 */
export declare function toCoin(amount: number | null | undefined): number;

/**
 * 兴安币 → 内部账务单位（四舍五入取整）。
 * @param coins 用户输入的兴安币数值（字符串或数字均可）；非法输入按 0 处理。
 */
export declare function toRawAmount(coins: number | string | null | undefined): number;

/**
 * 内部账务单位 → 兴安币展示字符串。
 * 整数不带小数，否则保留 1 位小数（如 125 → "12.5"）。
 */
export declare function formatXaCoin(amount: number | null | undefined): string;
