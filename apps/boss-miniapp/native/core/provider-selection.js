function normalizeProviderIds(value) {
  const values = Array.isArray(value) ? value : String(value || '').split(',');
  return [...new Set(values.map(item => Number(item && typeof item === 'object' ? item.id : item))
    .filter(id => Number.isSafeInteger(id) && id > 0))];
}

function providerLimit(service) { return Number(service && service.billing_provider_count) === 2 ? 2 : 1; }

function toggleProviders(providers, provider, limit) {
  const selected = Array.isArray(providers) ? providers : [];
  const id = normalizeProviderIds([provider])[0];
  if (!id) return { providers: selected, error: '未找到陪玩信息，请刷新后重试' };
  if (selected.some(item => Number(item.id) === id)) return { providers: selected.filter(item => Number(item.id) !== id), error: '' };
  if (!provider.can_order) return { providers: selected, error: provider.order_unavailable_reason || '该陪玩暂不可接单，请更换陪玩' };
  if (limit === 1) return { providers: [provider], error: '' };
  if (selected.length >= limit) return { providers: selected, error: '最多选择' + limit + '位陪玩，请先取消一位再更换' };
  return { providers: [...selected, provider], error: '' };
}

function selectionReady(providers, limit) { return providers.length === 0 || providers.length === limit; }

module.exports = { normalizeProviderIds, providerLimit, toggleProviders, selectionReady };
