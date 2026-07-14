// 后端账务值统一换算为兴安币展示。
export const formatXaCoin = (amount: number): string => {
  const coins = (amount || 0) / 10;
  return Number.isInteger(coins) ? coins.toFixed(0) : coins.toFixed(1);
};

export const formatSales = (sales: number): string => {
  if (sales >= 10000) {
    return (sales / 10000).toFixed(1) + '万';
  }
  return sales.toString();
};
