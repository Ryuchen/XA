"""One store-local sales basis for achievements and member tags."""
from django.db.models import Count, F, Q, Subquery, Sum

from orders.models import Order
from orders.refunds import with_net_amount


def record_achievement_event(order, *, addon=None):
    """Record the original real-sales and License decision atomically."""
    from stores.db import transaction
    with transaction.atomic():
        from club_accounts.models import ClubAccount
        from wallet.services import get_wallet
        from users.models import Achievement
        from .models import AchievementLicenseEvent, EarnedAchievement
        if not get_wallet(account_id=order.customer_account_id, for_update=True, create=False):
            raise ValueError('成就实销老板钱包不存在')
        account = ClubAccount.objects.select_for_update().get(pk=order.customer_account_id)
        if account.account_type != 'BOSS' or account.business_cycle != order.business_cycle:
            raise ValueError('成就实销业务周期不一致')
        if addon is None:
            if not order.funding_benefits_resolved_at or not order.settled_at or order.is_gift_snapshot:
                raise ValueError('成就主单实销事实缺失')
            key = f'order:{order.pk}:achievement'
        else:
            if (addon.account_id != account.pk or addon.business_cycle != account.business_cycle
                    or addon.payment.intent.order_id != order.pk or not order.settled_at):
                raise ValueError('成就加购实销事实缺失')
            key = f'addon:{addon.pk}:achievement'
        if AchievementLicenseEvent.objects.filter(event_key=key).exists():
            return
        metrics = confirmed_service_metrics(account)
        rules = [{'id': item.pk, 'metric': item.metric, 'target': item.target}
                 for item in Achievement.objects.filter(is_active=True).order_by('pk')]
        eligible = [item for item in rules
                    if metrics['amount' if item['metric'] == 'amount' else 'count'] >= item['target']]
        from stores.license_benefits import check_new_benefits
        outcome, evidence = check_new_benefits('ACHIEVEMENTS_RANKINGS')
        snapshot = None
        snapshot_hash = ''
        if outcome == 'UNKNOWN':
            from .achievement_sales_snapshot import capture, digest, metrics as snapshot_metrics
            from django.db import DatabaseError
            from rest_framework.exceptions import ValidationError
            try:
                # A failed evidence read must roll back its own savepoint,
                # not the settlement that gave rise to this UNKNOWN decision.
                with transaction.atomic():
                    candidate = capture(account, rules=rules, source_order=order if addon is None else None)
                    if snapshot_metrics(candidate) == metrics:
                        snapshot, snapshot_hash = candidate, digest(candidate)
            except (DatabaseError, ValidationError, ValueError, TypeError, KeyError):
                # Preserve the original decision and metrics below, without
                # inferring a missing sales snapshot from later state.
                import logging
                logging.getLogger(__name__).exception('成就原实销快照待核对，订单 %s', order.pk)
        event = AchievementLicenseEvent.objects.create(event_key=key, order=order, account=account,
            business_cycle=account.business_cycle, outcome=outcome, license_evidence=evidence,
            metrics=metrics, eligible=eligible, sales_snapshot=snapshot, sales_snapshot_hash=snapshot_hash)
        if outcome == 'ALLOW':
            for item in eligible:
                EarnedAchievement.objects.get_or_create(account=account, business_cycle=account.business_cycle,
                    achievement_id=item['id'], defaults={'source_event': event})


def confirmed_service_metrics(account):
    """Count confirmed net sales even when VIP benefit issuance was disabled."""
    from orders.models import OrderAddonConsumption

    orders = Order.objects.filter(customer_account=account, business_cycle=account.business_cycle,
                                  is_gift_snapshot=False)
    # Both timestamps are written only after the service settlement and its
    # required waiting periods; VIP can be disabled at the latter decision.
    confirmed = (Q(consumption_confirmed_at__isnull=False) | Q(funding_benefits_resolved_at__isnull=False))
    main = orders.filter(confirmed, settled_at__isnull=False,
                         payment_status__in=['PAID', 'PARTIALLY_REFUNDED'])
    main_amount = with_net_amount(main).aggregate(amount=Sum('_net_paid_amount'))['amount'] or 0
    addons = OrderAddonConsumption.objects.filter(account=account, business_cycle=account.business_cycle,
        payment__intent__order__customer_account=account,
        payment__intent__order__business_cycle=account.business_cycle,
        payment__intent__order__is_gift_snapshot=False)
    addon_amount = addons.aggregate(amount=Sum(F('paid_units') - F('refunded_units')))['amount'] or 0
    retained_addons = addons.filter(paid_units__gt=F('refunded_units')).values('payment__intent__order_id')
    count = orders.filter(Q(pk__in=Subquery(main.values('pk'))) |
                          Q(pk__in=Subquery(retained_addons))).aggregate(count=Count('pk'))['count'] or 0
    return {'amount': main_amount + addon_amount, 'count': count}
