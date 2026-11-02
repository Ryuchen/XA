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
            # Without two distinct saved business identities, show no invented progress.
            if len(people) != 2 or not all(row['provider_account_id'] for row in people) or people[0]['provider_account_id'] == people[1]['provider_account_id']:
                order._team_progress = None
                continue
            stage = 'ENTRY' if order.status == 'GRABBED' else 'COMPLETION'
            field = 'entry_image' if stage == 'ENTRY' else 'completion_image'
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
