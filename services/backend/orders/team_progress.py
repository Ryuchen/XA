"""Read-only progress for the two-person screenshot handoff."""
from collections import defaultdict

from wallet.models import ProviderReport
from .models import OrderProvider


def prepare_team_progress(orders):
    """Batch the same evidence that gates the two-person status transition."""
    candidates = [order for order in orders if order.escort_mode == 'DOUBLE'
                  and order.requires_boss_selection and order.status in ('GRABBED', 'IN_SERVICE')]
    for start in range(0, len(candidates), 200):
        chunk = candidates[start:start + 200]
        ids = [order.pk for order in chunk]
        assignments = defaultdict(list)
        for row in OrderProvider.objects.filter(order_id__in=ids, released_at__isnull=True).order_by('pk').values(
                'order_id', 'provider_id', 'provider_account_id', 'provider_name_snapshot', 'team_completion_pending'):
            assignments[row['order_id']].append(row)
        report_rows = list(ProviderReport.objects.filter(order_id__in=ids).values(
            'order_id', 'provider_id', 'provider_account_id', 'entry_image', 'completion_image'))
        from .points_transfer_capture import enabled as points_transfer_enabled
        segment_orders, active_segments = set(), defaultdict(list)
        if points_transfer_enabled():
            from .models import OrderOriginalShareTransfer, OrderParticipationSegment
            segment_orders = set(OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order_id__in=ids,
                previous_segment__attribution__financial_coverage__source='WALLET_POINTS')
                .values_list('previous_segment__attribution__order_id', flat=True))
            for row in OrderParticipationSegment.objects.filter(
                    attribution__order_id__in=segment_orders, attribution__status='ACTIVE',
                    released_at__isnull=True).values('attribution__order_id', 'provider_account_id',
                                                    'started_at', 'completion_image'):
                active_segments[(row['attribution__order_id'], row['provider_account_id'])].append(row)
        from club_accounts.models import LegacyAccountMap
        legacy_ids = {row['provider_id'] for row in report_rows if not row['provider_account_id']}
        legacy_accounts = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=legacy_ids).values_list('legacy_user_id', 'account_id'))
        reports = {(row['order_id'], row['provider_id'], row['provider_account_id']): row
                   for row in report_rows if row['provider_account_id']}
        for row in report_rows:
            account_id = legacy_accounts.get(row['provider_id']) if not row['provider_account_id'] else None
            if account_id:
                reports.setdefault((row['order_id'], row['provider_id'], account_id), row)
        for order in chunk:
            people = assignments[order.pk]
            # A verified scoped termination may leave exactly one original
            # team member completing the order; ordinary team flows need two.
            continuation = False
            if len(people) == 1 and order.status == 'IN_SERVICE':
                from .models import Order
                from .ordinary_transfer_continuation import applies
                if applies(order):
                    from rest_framework.exceptions import ValidationError
                    from stores.db import transaction
                    from .ordinary_transfer_continuation import verify_locked
                    try:
                        with transaction.atomic():
                            locked_order = Order.objects.select_for_update().get(pk=order.pk)
                            continuation = bool(verify_locked(locked_order))
                    except (ValidationError, ValueError):
                        continuation = False
                else:
                    from .negotiation_termination import continuing_team
                    continuation = bool(continuing_team(order))
            if ((len(people) != 2 and not continuation) or not all(row['provider_account_id'] for row in people)
                    or len({row['provider_account_id'] for row in people}) != len(people)):
                order._team_progress = None
                continue
            stage = 'ENTRY' if order.status == 'GRABBED' else 'COMPLETION'
            field = 'entry_image' if stage == 'ENTRY' else 'completion_image'
            if stage == 'COMPLETION' and order.pk in segment_orders:
                if any(len(active_segments[(order.pk, row['provider_account_id'])]) != 1 for row in people):
                    order._team_progress = None
                    continue
                members = []
                for row in people:
                    segment = active_segments[(order.pk, row['provider_account_id'])][0]
                    members.append({'id': row['provider_account_id'], 'nickname': row['provider_name_snapshot'] or '陪玩',
                                    'submitted': bool(segment['started_at'] and segment['completion_image'])
                                                 and not row['team_completion_pending']})
                order._team_progress = {'stage': stage, 'members': members,
                                        'submitted_count': sum(member['submitted'] for member in members),
                                        'total_count': len(members)}
                continue
            members = [{'id': row['provider_account_id'], 'nickname': row['provider_name_snapshot'] or '陪玩',
                        'submitted': bool(reports.get((order.pk, row['provider_id'], row['provider_account_id']), {}).get(field))
                                     and not (stage == 'COMPLETION' and row['team_completion_pending'])}
                       for row in people]
            order._team_progress = {'stage': stage, 'members': members,
                                    'submitted_count': sum(member['submitted'] for member in members),
                                    'total_count': len(members)}


def team_progress(order):
    if not hasattr(order, '_team_progress'):
        prepare_team_progress([order])
    return getattr(order, '_team_progress', None)
