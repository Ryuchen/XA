"""Review a narrowly provable original achievement License decision."""
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.db import transaction



def _resolve_addon_locked(order, event, *, request_id, actor_id, outcome, reason):
    """Resolve an add-on event from its own paid and confirmed source."""
    from orders.models import Order, OrderAddonConsumption, OrderSegmentSettlementManifest
    from wallet.models import Transaction
    from .models import AchievementLicenseReview, EarnedAchievement
    source_id = event.event_key.removeprefix('addon:').removesuffix(':achievement')
    if not source_id.isdigit() or event.event_key != f'addon:{int(source_id)}:achievement':
        raise ValidationError('加购成就来源待核对')
    addon = (OrderAddonConsumption.objects.select_for_update().select_related(
        'payment__intent', 'payment__buyer_transaction__wallet').filter(pk=int(source_id)).first())
    evidence = event.license_evidence
    if (not addon or outcome not in ('ALLOW', 'DENY') or event.outcome != 'UNKNOWN'
            or not isinstance(evidence, dict) or evidence.get('status') not in (
                'CONTROL_UNAVAILABLE', 'LICENSE_CONFIGURATION_INVALID', 'STORE_CONTEXT_UNAVAILABLE')
            or order.is_gift_snapshot or addon.payment.intent.order_id != order.pk
            or order.customer_account_id != event.account_id or addon.account_id != event.account_id
            or order.business_cycle != event.business_cycle or addon.business_cycle != event.business_cycle
            or not order.settled_at or order.settled_at > addon.confirmed_at
            or addon.confirmed_at > event.decided_at or addon.payment.paid_at > addon.confirmed_at):
        raise ValidationError('加购成就原授权或实销事实待核对')
    payment, debit = addon.payment, addon.payment.buyer_transaction
    if (payment.intent.buyer_id != event.account_id or addon.paid_units != payment.intent.amount
            or debit.wallet.account_id != event.account_id or debit.order_id is not None
            or debit.tx_type != Transaction.TxType.PAY or debit.status != Transaction.Status.SUCCESS
            or debit.amount != -payment.intent.amount or debit.reversal_of_id
            or debit.balance_after != debit.balance_before + debit.amount):
        raise ValidationError('加购成就原付款流水待核对')
    if outcome == 'DENY':
        return AchievementLicenseReview.objects.create(event=event, request_id=request_id,
            platform_actor_id=actor_id, outcome=outcome, reason=reason, granted_ids=[])

    from orders.financial_lock import lock_existing_order_money
    lock_existing_order_money()
    from orders.addon_payment import verify_receipt
    verify_receipt(payment)
    # The immutable aggregate has no source list. Prove the first add-on's
    # original aggregate directly; do not infer later or concurrent sales.
    if (addon.refunded_units or payment.refund_records.exists()
            or addon.paid_units <= 0 or event.metrics != {'count': 1, 'amount': addon.paid_units}
            or (order.funding_benefits_resolved_at and order.funding_benefits_resolved_at <= event.decided_at)
            or OrderAddonConsumption.objects.filter(account_id=event.account_id,
                business_cycle=event.business_cycle).exclude(pk=addon.pk).exists()
            or Order.objects.filter(customer_account_id=event.account_id, business_cycle=event.business_cycle,
                is_gift_snapshot=False).exclude(pk=order.pk).filter(
                    Q(consumption_confirmed_at__isnull=False) | Q(funding_benefits_resolved_at__isnull=False)).exists()):
        raise ValidationError('加购原成就汇总或退款事实待核对')
    manifest = OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at=order.settled_at).first()
    if manifest:
        from orders.segment_settlement import verify_finalized
        from orders.settlement import SettlementError
        try:
            verify_finalized(order, manifest)
        except SettlementError as exc:
            raise ValidationError('加购逐段结算凭证待核对') from exc
    else:
        from orders.points_settlement_test import verified_order_settlement_display
        if verified_order_settlement_display(order) != 'SETTLED':
            raise ValidationError('加购原收益结算凭证待核对')
    from wallet.services import get_wallet
    if not get_wallet(account_id=event.account_id, for_update=True, create=False):
        raise ValidationError('原老板钱包不存在')
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().get(pk=event.account_id)
    if account.account_type != 'BOSS' or account.business_cycle != event.business_cycle:
        raise ValidationError('原老板业务周期已变化，继续待核对')
    from users.models import Achievement
    eligible = [{'id': item.pk, 'metric': item.metric, 'target': item.target}
        for item in Achievement.objects.filter(is_active=True)
        if (addon.paid_units if item.metric == 'amount' else 1) >= item.target]
    if (not isinstance(event.eligible, list)
            or any(not isinstance(v, dict) or set(v) != {'id', 'metric', 'target'}
                   or type(v['id']) is not int for v in event.eligible)
            or sorted(event.eligible, key=lambda v: v['id']) != sorted(eligible, key=lambda v: v['id'])):
        raise ValidationError('原成就规则快照已变化，请继续核对')
    granted = []
    for item in eligible:
        _, created = EarnedAchievement.objects.get_or_create(account=account, business_cycle=event.business_cycle,
            achievement_id=item['id'], defaults={'source_event': event})
        if created:
            granted.append(item['id'])
    return AchievementLicenseReview.objects.create(event=event, request_id=request_id,
        platform_actor_id=actor_id, outcome=outcome, reason=reason, granted_ids=granted)


def _resolve_snapshot_locked(order, event, *, request_id, actor_id, outcome, reason):
    from .models import AchievementLicenseReview, EarnedAchievement
    evidence = event.license_evidence
    if (outcome not in ('ALLOW', 'DENY') or event.outcome != 'UNKNOWN'
            or order.is_gift_snapshot or order.customer_account_id != event.account_id
            or order.business_cycle != event.business_cycle or not isinstance(evidence, dict)
            or evidence.get('status') not in ('CONTROL_UNAVAILABLE', 'LICENSE_CONFIGURATION_INVALID',
                                               'STORE_CONTEXT_UNAVAILABLE')):
        raise ValidationError('成就原授权事实待核对')
    if outcome == 'DENY':
        # Existing source-specific denial checks retain their original rules.
        return None
    from orders.financial_lock import lock_existing_order_money
    lock_existing_order_money()
    from .achievement_sales_snapshot import verify_locked
    eligible = verify_locked(event)
    data = event.sales_snapshot
    if event.event_key == f'order:{order.pk}:achievement':
        excluded = data.get('excluded_source')
        if (not any(item['order_id'] == order.pk for item in data['mains'])
                and (not isinstance(excluded, dict) or excluded.get('order_id') != order.pk)):
            raise ValidationError('成就事件的原主单实销未纳入快照')
    elif event.event_key.startswith('addon:'):
        if not any(event.event_key == f"addon:{item['consumption_id']}:achievement"
                   and item['order_id'] == order.pk for item in data['addons']):
            raise ValidationError('成就事件的原加购实销未纳入快照')
    else:
        raise ValidationError('成就原事件来源待核对')
    from wallet.services import get_wallet
    if not get_wallet(account_id=event.account_id, for_update=True, create=False):
        raise ValidationError('原老板钱包不存在')
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().get(pk=event.account_id)
    if account.account_type != 'BOSS' or account.business_cycle != event.business_cycle:
        raise ValidationError('原老板业务周期已变化，继续待核对')
    from users.models import Achievement
    ids = [item['id'] for item in eligible]
    if Achievement.objects.filter(pk__in=ids).count() != len(ids):
        raise ValidationError('原成就规则已删除，继续待核对')
    granted = []
    for achievement_id in ids:
        _, created = EarnedAchievement.objects.get_or_create(account=account, business_cycle=event.business_cycle,
            achievement_id=achievement_id, defaults={'source_event': event})
        if created:
            granted.append(achievement_id)
    return AchievementLicenseReview.objects.create(event=event, request_id=request_id,
        platform_actor_id=actor_id, outcome=outcome, reason=reason, granted_ids=granted)

def resolve_locked(event_id, *, request_id, actor_id, outcome, reason):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('成就授权核对须在门店事务内执行')
    from orders.models import Order, OrderAddonConsumption, OrderOriginalShareTransfer
    from wallet.models import Transaction
    from .models import AchievementLicenseEvent, AchievementLicenseReview, EarnedAchievement
    initial = AchievementLicenseEvent.objects.filter(pk=event_id).first()
    if not initial:
        raise ValidationError('成就授权事件不存在')
    order = Order.objects.select_for_update().get(pk=initial.order_id)
    event = AchievementLicenseEvent.objects.select_for_update().get(pk=event_id)
    review = AchievementLicenseReview.objects.select_for_update().filter(event=event).first()
    if review:
        if review.request_id != request_id or review.outcome != outcome or review.reason != reason:
            raise ValidationError('该事件已核对，请查看原结果')
        return review
    if AchievementLicenseReview.objects.filter(request_id=request_id).exists():
        raise ValidationError('请求编号已用于其他成就核对')
    if outcome == 'ALLOW':
        from stores.certificate_admission import require_original_certificate
        require_original_certificate(event.license_evidence)
    if event.sales_snapshot and outcome == 'ALLOW':
        # A third-round points transfer requires the same finalized manifest
        # and funding verifier as settlement, not merely a sales snapshot.
        from orders.points_service_payment import needs_integrated_processing
        if (needs_integrated_processing(order) and order.refund_records.count() >= 3
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
            if not order.settled_at:
                raise ValidationError('第三轮退款订单尚未最终结算')
            from orders.points_transfer_refund_access import after_transfer, verification_scope
            from orders.models import OrderSegmentSettlementManifest
            from orders.segment_settlement import verify_finalized
            from orders.settlement import SettlementError
            with after_transfer(order.pk, phase='settlement') as locked:
                with verification_scope(locked):
                    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(
                        order=locked, finalized_at=locked.settled_at).first()
                    if not manifest:
                        raise ValidationError('第三轮退款最终结算清单缺失')
                    try:
                        verify_finalized(locked, manifest)
                    except SettlementError as exc:
                        raise ValidationError('第三轮退款最终结算凭证待核对') from exc
                    return _resolve_snapshot_locked(locked, event, request_id=request_id, actor_id=actor_id,
                                                    outcome=outcome, reason=reason)
        return _resolve_snapshot_locked(order, event, request_id=request_id, actor_id=actor_id,
                                        outcome=outcome, reason=reason)
    if event.event_key.startswith('addon:'):
        return _resolve_addon_locked(order, event, request_id=request_id, actor_id=actor_id,
                                     outcome=outcome, reason=reason)
    evidence = event.license_evidence
    if (outcome not in ('ALLOW', 'DENY') or event.outcome != 'UNKNOWN'
            or event.event_key != f'order:{order.pk}:achievement' or order.is_gift_snapshot
            or order.customer_account_id != event.account_id or order.business_cycle != event.business_cycle
            or not order.funding_benefits_resolved_at or not order.settled_at
            or order.funding_benefits_resolved_at > event.decided_at
            or not isinstance(evidence, dict) or evidence.get('status') not in
                ('CONTROL_UNAVAILABLE', 'LICENSE_CONFIGURATION_INVALID', 'STORE_CONTEXT_UNAVAILABLE')
            or not Transaction.objects.filter(order=order, wallet__account_id=event.account_id,
                tx_type='PAY', status='SUCCESS', amount=-order.amount, created_at__lte=event.decided_at).exists()):
        raise ValidationError('原成就授权或付款事实待核对')
    if outcome == 'DENY':
        return AchievementLicenseReview.objects.create(event=event, request_id=request_id,
            platform_actor_id=actor_id, outcome=outcome, reason=reason, granted_ids=[])
    # Only the first unrefunded ordinary order can currently prove the entire
    # historical aggregate without a persisted itemized historical manifest.
    from orders.financial_lock import lock_existing_order_money
    lock_existing_order_money()
    from orders.points_service_payment import needs_integrated_processing
    if (order.amount <= 0 or event.metrics != {'count': 1, 'amount': order.amount}
            or order.payment_status != 'PAID' or order.refund_records.exists()
            or order.addon_intents.exists() or order.source_order_id
            or order.status_logs.filter(action='TRANSFER').exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or needs_integrated_processing(order)):
        raise ValidationError('成就原实销汇总或后续资金变化待核对')
    from .license_points_review import _verify_award_sources
    _verify_award_sources(order)
    from orders.points_settlement_test import verified_order_settlement_display
    if verified_order_settlement_display(order) != 'SETTLED':
        raise ValidationError('原订单结算凭证待核对')
    from wallet.services import get_wallet
    if not get_wallet(account_id=event.account_id, for_update=True, create=False):
        raise ValidationError('原老板钱包不存在')
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().get(pk=event.account_id)
    if account.account_type != 'BOSS' or account.business_cycle != event.business_cycle:
        raise ValidationError('原老板业务周期已变化，继续待核对')
    other = Order.objects.filter(customer_account_id=account.pk, business_cycle=event.business_cycle,
        is_gift_snapshot=False).exclude(pk=order.pk).filter(
            Q(consumption_confirmed_at__isnull=False) | Q(funding_benefits_resolved_at__isnull=False))
    if other.exists() or OrderAddonConsumption.objects.filter(account_id=account.pk,
            business_cycle=event.business_cycle).exists():
        raise ValidationError('存在其他实销，原成就汇总不能直接证明')
    from users.models import Achievement
    eligible = [{'id': item.pk, 'metric': item.metric, 'target': item.target}
        for item in Achievement.objects.filter(is_active=True)
        if (order.amount if item.metric == 'amount' else 1) >= item.target]
    if (not isinstance(event.eligible, list)
            or any(not isinstance(v, dict) or set(v) != {'id', 'metric', 'target'}
                   or type(v['id']) is not int for v in event.eligible)
            or sorted(event.eligible, key=lambda v: v['id']) != sorted(eligible, key=lambda v: v['id'])):
        raise ValidationError('原成就规则快照已变化，请继续核对')
    granted = []
    for item in eligible:
        _, created = EarnedAchievement.objects.get_or_create(account=account, business_cycle=event.business_cycle,
            achievement_id=item['id'], defaults={'source_event': event})
        if created:
            granted.append(item['id'])
    return AchievementLicenseReview.objects.create(event=event, request_id=request_id,
        platform_actor_id=actor_id, outcome=outcome, reason=reason, granted_ids=granted)
