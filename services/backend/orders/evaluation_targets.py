"""Final service participants who can receive an order evaluation."""


def evaluation_targets(order):
    members = getattr(order, 'active_participants', None)
    if members is None:
        prefetched = getattr(order, '_prefetched_objects_cache', {}).get('providers')
        members = (sorted((member for member in prefetched if member.released_at is None), key=lambda member: member.pk)
                   if prefetched is not None else list(order.providers.filter(released_at__isnull=True).order_by('pk')))
    else:
        members = [member for member in members if member.released_at is None]
    targets = [{
        'provider_account_id': member.provider_account_id,
        'provider_legacy_id': member.provider_id,
        'name': member.provider_name_snapshot or '陪玩',
    } for member in members if member.provider_id]
    if not targets and order.provider_id and not order.providers.exists():
        targets = [{'provider_account_id': order.provider_account_id,
                    'provider_legacy_id': order.provider_id,
                    'name': order.provider_name_snapshot or '陪玩'}]
    return targets


def evaluation_provider_name(evaluation):
    """Use the same participant name as the boss's order detail."""
    for target in evaluation_targets(evaluation.order):
        if target['provider_legacy_id'] == evaluation.provider_id:
            return target['name']
    provider = evaluation.provider_account if evaluation.provider_account_id else evaluation.provider
    return (provider.nickname or provider.username) if provider else '陪玩'
