"""Resolve order participants by business account, with legacy-order fallback."""


def owns_order_party(order, request, party):
    account = getattr(request, 'account', None)
    legacy = getattr(request, 'legacy_user', None)
    account_id = getattr(order, f'{party}_account_id')
    if account_id is not None:
        return bool(account and account.pk == account_id)
    return bool(legacy and legacy.pk == getattr(order, f'{party}_id'))
