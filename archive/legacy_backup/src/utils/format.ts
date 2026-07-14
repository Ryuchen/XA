export const formatMoney = (amount: number): string => {
  return amount.toFixed(2);
};

export const formatSales = (sales: number): string => {
  if (sales >= 10000) {
    return (sales / 10000).toFixed(1) + '万';
  }
  return sales.toString();
};
