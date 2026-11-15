const waveFormatter = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
})

/** 浪花只以整数展示；内部金额精度和人民币格式不受此函数影响。 */
export function formatWaveAmount(value: number | string) {
  const numeric = typeof value === 'number' ? value : Number(value.replace(/,/g, ''))
  return Number.isFinite(numeric) ? waveFormatter.format(Math.round(numeric)) : String(value)
}
