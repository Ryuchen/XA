export const formatXaCoin = (amount: number): string => {
  const coins = (amount || 0) / 10;
  return Number.isInteger(coins) ? coins.toFixed(0) : coins.toFixed(1);
};
