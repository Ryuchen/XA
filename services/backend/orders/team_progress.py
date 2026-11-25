"""Read-only progress for the two-person screenshot handoff."""
from collections import defaultdict

from django.conf import settings
from django.db.models import F

from stores.context import current_store
from stores.db import transaction
from wallet.models import ProviderReport
from .models import Order, OrderProvider


def formal_transfer_display_enabled():
    if not getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False):
        return False
    store = current_store()
    connection = transaction.get_connection()
    return bool(store and connection.vendor == 'mysql' and store.database_alias == connection.alias)


def prepare_team_progress(orders, *, for_display=False):
    """Batch screenshots and active seats for progress and fulfilment."""
    candidates = [order for order in orders if order.escort_mode == 'DOUBLE'
                  and order.requires_boss_selection and order.status in ('GRABBED', 'IN_SERVICE')]
    if not candidates:
        return
    formal_display = for_display and formal_transfer_display_enabled()
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
        paid_orders, refunded_orders = set(), set()
        if formal_display:
            for order in chunk:
                if order.status != Order.Status.IN_SERVICE or order.is_gift_snapshot or order.source_order_id:
                    continue
                if order.payment_status == Order.PaymentStatus.PAID and len(assignments[order.pk]) == 2:
                    paid_orders.add(order.pk)
                if order.payment_status == Order.PaymentStatus.PARTIALLY_REFUNDED and len(assignments[order.pk]) == 1:
                    refunded_orders.add(order.pk)
        formal_orders = paid_orders | refunded_orders
        scoped_transfer = points_transfer_enabled()
        if scoped_transfer or formal_orders:
            from .models import OrderOriginalShareTransfer, OrderParticipationSegment
            transfer_candidates = ids if scoped_transfer else formal_orders
            segment_orders = set(OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order_id__in=transfer_candidates,
                previous_segment__attribution__financial_coverage__source='WALLET_POINTS')
                .values_list('previous_segment__attribution__order_id', flat=True))
            for row in OrderParticipationSegment.objects.filter(
                    attribution__order_id__in=segment_orders, attribution__status='ACTIVE',
                    released_at__isnull=True).values('attribution__order_id', 'provider_account_id',
                                                    'started_at', 'completion_image'):
                active_segments[(row['attribution__order_id'], row['provider_account_id'])].append(row)
        stopped = defaultdict(set)
        scoped_orders = {order.pk for order in chunk if order.pk in segment_orders and order.pk in refunded_orders
                         and order.refund_records.count() == 1}
        formal_progress_orders = (paid_orders & segment_orders) | scoped_orders
        if scoped_orders:
            from .models import PointsNegotiationPostingShare
            # Display durable assignments and the recorded termination. The
            # completion endpoint still verifies the entire refund under lock.
            shares = PointsNegotiationPostingShare.objects.filter(
                assignment__order_id__in=scoped_orders, account_id=F('assignment__provider_account_id'),
                posting__points_refund__refund__order_id=F('assignment__order_id'),
                posting__points_refund__redemption__order_id=F('assignment__order_id'),
                posting__points_refund__is_final=False,
                posting__termination_in_service=True, posting__completed_by_negotiation=False,
                posting__termination_processed_at__isnull=False,
                assignment__released_at=F('posting__accepted_at'))
            for order_id, account_id in shares.values_list('assignment__order_id', 'account_id'):
                stopped[order_id].add(account_id)
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
            use_formal_display = order.pk in formal_progress_orders
            progress_attr = '_formal_transfer_progress' if use_formal_display else '_team_progress'
            # A verified scoped termination may leave exactly one original
            # team member completing the order; ordinary team flows need two.
            continuation = False
            if len(people) == 1 and order.status == 'IN_SERVICE':
                if use_formal_display:
                    continuation = len(stopped[order.pk]) == 1 and people[0]['provider_account_id'] not in stopped[order.pk]
                else:
                    from .ordinary_transfer_continuation import applies
                    if applies(order):
                        from rest_framework.exceptions import ValidationError
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
                setattr(order, progress_attr, None)
                continue
            stage = 'ENTRY' if order.status == 'GRABBED' else 'COMPLETION'
            field = 'entry_image' if stage == 'ENTRY' else 'completion_image'
            if stage == 'COMPLETION' and order.pk in segment_orders:
                if any(len(active_segments[(order.pk, row['provider_account_id'])]) != 1 for row in people):
                    setattr(order, progress_attr, None)
                    continue
                members = []
                for row in people:
                    segment = active_segments[(order.pk, row['provider_account_id'])][0]
                    members.append({'id': row['provider_account_id'], 'nickname': row['provider_name_snapshot'] or '陪玩',
                                    'submitted': bool(segment['started_at'] and segment['completion_image'])
                                                 and not row['team_completion_pending']})
                setattr(order, progress_attr, {'stage': stage, 'members': members,
                    'submitted_count': sum(member['submitted'] for member in members), 'total_count': len(members)})
                continue
            members = [{'id': row['provider_account_id'], 'nickname': row['provider_name_snapshot'] or '陪玩',
                        'submitted': bool(reports.get((order.pk, row['provider_id'], row['provider_account_id']), {}).get(field))
                                     and not (stage == 'COMPLETION' and row['team_completion_pending'])}
                       for row in people]
            setattr(order, progress_attr, {'stage': stage, 'members': members,
                'submitted_count': sum(member['submitted'] for member in members), 'total_count': len(members)})


def team_progress(order, *, for_display=False):
    if not hasattr(order, '_team_progress') and not (for_display and hasattr(order, '_formal_transfer_progress')):
        prepare_team_progress([order], for_display=for_display)
    if for_display and hasattr(order, '_formal_transfer_progress'):
        return order._formal_transfer_progress
    return getattr(order, '_team_progress', None)
