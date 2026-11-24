from datetime import timedelta
from celery import shared_task
from stores.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import MemberProfile
from .services import broadcast


def enqueue_broadcast(pk):
    """Broker downtime must never turn a committed purchase into a failure."""
    import logging
    try:
        publish_broadcast.apply_async(args=[pk], retry=False)
    except Exception:
        logging.getLogger(__name__).exception('Broadcast %s awaits outbox retry', pk)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def publish_broadcast(pk):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from .models import Broadcast
    with transaction.atomic():
        row = Broadcast.objects.select_for_update().filter(pk=pk).first()
        if not row or row.notified_at:
            return False
        now = timezone.now()
        if not row.published_at:
            row.published_at = now
            row.save(update_fields=['published_at'])
    # Make the feed readable before waking clients. A crash between delivery and
    # acknowledgement may repeat this harmless invalidation, never the event.
    with transaction.atomic():
        row = Broadcast.objects.select_for_update().get(pk=pk)
        if row.notified_at:
            return False
        now = timezone.now()
        if row.is_visible and row.starts_at <= now < row.expires_at:
            layer = get_channel_layer()
            if layer:
                async_to_sync(layer.group_send)('club_bosses', {'type': 'club_feed_update', 'data': {'id': row.pk}})
        row.notified_at = now
        row.save(update_fields=['notified_at'])
    return True


@shared_task
def drain_broadcast_outbox():
    from .models import Broadcast
    ids = list(Broadcast.objects.filter(notified_at__isnull=True).order_by('id').values_list('id', flat=True)[:200])
    for pk in ids:
        enqueue_broadcast(pk)
    return len(ids)


@shared_task
def publish_birthdays():
    import logging
    from club_accounts.models import ClubAccount
    from wallet.services import WalletAddressingError, get_wallet
    today = timezone.localdate()
    profiles = MemberProfile.objects.filter(birthday__month=today.month, birthday__day=today.day,
        birthday_public=True, account__is_active=True, account__can_login=True,
        account__can_view=True, account__license_membership_active=True,
        account__account_type='BOSS').select_related('account')
    count = 0
    for profile in profiles.iterator():
        original_cycle = profile.account.business_cycle
        try:
            with transaction.atomic():
                # Match retirement's wallet → account → member-profile lock order.
                get_wallet(account_id=profile.account_id, for_update=True, create=False)
                account = ClubAccount.objects.select_for_update().filter(pk=profile.account_id,
                    account_type='BOSS', is_active=True, can_login=True, can_view=True,
                    license_membership_active=True, business_cycle=original_cycle).first()
                if not account:
                    continue
                current = MemberProfile.objects.select_for_update().filter(pk=profile.pk, account=account,
                    birthday_public=True, birthday__month=today.month, birthday__day=today.day).first()
                if not current or timezone.localdate() != today:
                    continue
                broadcast('BIRTHDAY', f'birthday:{account.pk}:{original_cycle}:{today.year}', account=account)
                count += 1
        except WalletAddressingError:
            # Keep an invalid account visible in logs without blocking the
            # remaining eligible birthdays in this store's hourly scan.
            logging.getLogger(__name__).exception('Birthday wallet identity requires review: account=%s', profile.account_id)
    return count


@shared_task
def grant_member_coupons():
    from club_accounts.models import ClubAccount
    from coupons.benefits import grant_periodic
    from .models import VipTier
    accounts = ClubAccount.objects.filter(account_type='BOSS', is_active=True,
        boss_type_id__in=VipTier.objects.filter(active=True).values('boss_type_id'))
    count = 0
    import logging
    for account in accounts.iterator():
        try:
            grant_periodic(account)
            count += 1
        except Exception:
            logging.getLogger(__name__).exception('Membership coupon grant failed for account %s; next scan retries', account.pk)
    return count


@shared_task
def retry_addon_vip_recalculations():
    """Revisit reviewed growth after the store's VIP authorization recovers."""
    import logging
    from club_accounts.models import ClubAccount
    from .models import LicenseAddonBenefitsReview, LicenseRefundVipReview, LicenseGiftVipReview, RetirementAudit
    addon_ids = set(LicenseAddonBenefitsReview.objects.filter(vip_recalculation_pending=True)
                    .values_list('consumption__account_id', flat=True))
    refund_ids = set(LicenseRefundVipReview.objects.filter(vip_recalculation_pending=True)
                     .values_list('decision__account_id', flat=True))
    gift_ids = set(LicenseGiftVipReview.objects.filter(vip_recalculation_pending=True)
                   .values_list('decision__account_id', flat=True))
    count = 0
    for account_id in sorted(addon_ids | refund_ids | gift_ids):
        try:
            selected = None
            with transaction.atomic():
                from wallet.services import get_wallet
                if not get_wallet(account_id=account_id, for_update=True, create=False):
                    raise ValueError('会员审核对应的钱包不存在')
                account = ClubAccount.objects.select_for_update().get(pk=account_id)
                if account.account_type != 'BOSS':
                    raise ValueError('会员审核对应的账号不是老板')
                closed_cycles = RetirementAudit.objects.filter(account_id=account_id,
                    business_cycle__lt=account.business_cycle).values_list('business_cycle', flat=True)
                addons = LicenseAddonBenefitsReview.objects.filter(consumption__account_id=account_id,
                    vip_recalculation_pending=True)
                refunds = LicenseRefundVipReview.objects.filter(decision__account_id=account_id,
                    vip_recalculation_pending=True)
                gifts = LicenseGiftVipReview.objects.filter(decision__account_id=account_id,
                    vip_recalculation_pending=True)
                addons.filter(consumption__business_cycle__in=closed_cycles).update(vip_recalculation_pending=False)
                refunds.filter(decision__business_cycle__in=closed_cycles).update(vip_recalculation_pending=False)
                gifts.filter(decision__business_cycle__in=closed_cycles).update(vip_recalculation_pending=False)
                # Recalculation aggregates the whole cycle. With several
                # unresolved reviews, one review cannot prove the others.
                candidates = ([('addon', r) for r in addons.filter(
                    consumption__business_cycle=account.business_cycle)[:2]]
                    + [('refund', r) for r in refunds.filter(
                        decision__business_cycle=account.business_cycle)[:2]]
                    + [('gift', r) for r in gifts.filter(
                        decision__business_cycle=account.business_cycle)[:2]])
                if len(candidates) == 1:
                    selected = candidates[0]
                elif (any(kind == 'addon' for kind, _ in candidates)
                        and all(kind in ('addon', 'refund') for kind, _ in candidates)):
                    order_ids = {(row.consumption.payment.intent.order_id if kind == 'addon'
                                  else row.decision.order_id) for kind, row in candidates}
                    if len(order_ids) == 1:
                        selected = ('addon_batch', order_ids.pop())
            if selected:
                kind, review = selected
                # Do not hold the wallet lock while acquiring the order lock.
                # The existing review entry point rechecks the source, locks
                # and cycle in its own store transaction.
                with transaction.atomic():
                    if kind == 'addon_batch':
                        from .license_addon_batch import retry_same_order
                        retry_same_order(account_id, review)
                    elif kind == 'addon':
                        from .license_addon_review import resolve_locked
                        resolve_locked(review.consumption_id, request_id=review.request_id,
                            actor_id=review.platform_actor_id, vip_outcome=review.vip_outcome,
                            points_outcome=review.points_outcome, reason=review.reason)
                    elif kind == 'refund':
                        from .license_refund_vip_review import resolve_locked
                        resolve_locked(review.decision_id, request_id=review.request_id,
                            actor_id=review.platform_actor_id, outcome=review.outcome, reason=review.reason)
                    else:
                        from .license_gift_vip_review import resolve_locked
                        resolve_locked(review.decision_id, request_id=review.request_id,
                            actor_id=review.platform_actor_id, outcome=review.outcome, reason=review.reason)
            if not LicenseAddonBenefitsReview.objects.filter(consumption__account_id=account_id,
                    vip_recalculation_pending=True).exists() and not LicenseRefundVipReview.objects.filter(
                    decision__account_id=account_id, vip_recalculation_pending=True).exists() and not LicenseGiftVipReview.objects.filter(
                    decision__account_id=account_id, vip_recalculation_pending=True).exists():
                count += 1
        except Exception:
            logging.getLogger(__name__).exception('Reviewed add-on VIP recalculation failed for account %s; next scan retries', account_id)
    return count


@shared_task
def process_funded_order(order_id):
    """Recheck one store's order after its funding operation is committed."""
    if type(order_id) is not int or order_id <= 0:
        raise ValueError('订单编号无效')
    from .confirmation import process_order
    return process_order(order_id)


@shared_task
def confirm_due_consumption():
    from orders.models import Order
    from django.db.models import Exists, OuterRef
    from orders.models import OrderAddonPayment
    from .confirmation import process_order
    from orders.segment_settlement import protection_enabled
    report_filter = (Q(service_clock_version__isnull=False, provider_reports__status='APPROVED', provider_reports__submitted_at__isnull=False)
                     | Q(service_clock_version__isnull=True, report_ready_at__isnull=False))
    if protection_enabled():
        report_filter |= Q(original_attribution__segments__segment_report__status='APPROVED')
    from orders.subsidy_segment_settlement import enabled as subsidy_segment_enabled
    if subsidy_segment_enabled():
        report_filter |= Q(original_attribution__financial_coverage__source='PLATFORM_SUBSIDY',
                           original_attribution__segments__segment_report__status='APPROVED')
    payment_filter = Q(payment_status__in=['PAID', 'PARTIALLY_REFUNDED'])
    # A zero-net negotiated order can still owe retained provider income and
    # must reach final settlement. Discovery runs outside the order-scoped
    # capability, so do not use enabled() here: the locked processor grants
    # the tenant capability and verifies funding and reports before posting.
    payment_filter |= Q(payment_status='REFUNDED', settled_at__isnull=True,
                        points_redemption__refunds__negotiation_posting__termination_processed_at__isnull=False)
    # Ordinary wallet orders may also be fully refunded after a negotiated
    # service termination. Their provider's retained or zero income and
    # deposit-recovery records still need the normal funded settlement path.
    # This only discovers candidates; the locked processor rechecks the
    # negotiation, original funding sources, receipts and individual reports.
    payment_filter |= Q(payment_status='REFUNDED', settled_at__isnull=True,
                        refund_records__negotiation_execution__termination_processed_at__isnull=False)
    # A formal points order can finish with zero retained main payment after
    # its refunds and settlement. It still needs the immutable consumption /
    # License decision; the locked processor verifies the actual stage and
    # never awards growth or points for a zero net amount.
    payment_filter |= Q(payment_status='REFUNDED', settled_at__isnull=False,
                        funding_benefits_resolved_at__isnull=True,
                        points_redemption__refunds__negotiation_posting__termination_processed_at__isnull=False)
    pending_addon = OrderAddonPayment.objects.filter(intent__order_id=OuterRef('pk'), consumption__isnull=True)
    payment_filter |= Q(payment_status='REFUNDED', settled_at__isnull=False, _pending_addon=True)
    # A final benefits decision can leave consumption unconfirmed. Only
    # revisit the main order until both settlement and that decision exist.
    ids = (Order.objects.annotate(_pending_addon=Exists(pending_addon)).filter(payment_filter, status='COMPLETED',
        customer_account__isnull=False).filter(
            (Q(is_gift_snapshot=False, consumption_confirmed_at__isnull=True)
             & (Q(settled_at__isnull=True) | Q(funding_benefits_resolved_at__isnull=True)))
            | Q(is_gift_snapshot=False, _pending_addon=True)
            | Q(settlement_mode='REPORT_APPROVAL', settled_at__isnull=True))
        .filter(completed_at__lte=timezone.now()).filter(report_filter)
        .order_by('pk').values_list('pk', flat=True).distinct().iterator(chunk_size=200))
    count = 0
    import logging
    for pk in ids:
        try:
            count += int(process_order(pk))
        except Exception:
            logging.getLogger(__name__).exception('Consumption confirmation failed for order %s; next scan retries', pk)
    return count


@shared_task
def retry_completion_subsidies():
    """Retry completed free-service orders whose platform funding was short."""
    from stores.conf import settings
    if not getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
        return 0
    from orders.models import Order
    from orders.services import settle_order
    from orders.notifier import notify_order_update
    import logging
    count = 0
    ids = Order.objects.filter(platform_subsidy__isnull=False, status='COMPLETED',
        payment_status='PAID', settled_at__isnull=True
    ).order_by('completed_at', 'pk').values_list('pk', flat=True).iterator(chunk_size=200)
    for order_id in ids:
        try:
            lifecycle = None
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from .settlement_lifecycle import capture
                lifecycle = capture(order_id)
            with transaction.atomic():
                if lifecycle is not None:
                    from .settlement_lifecycle import lock, verify
                    lock(lifecycle)
                    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                        from console.payroll_event_barrier import lock_payment_refund_events
                        lock_payment_refund_events()
                order = Order.objects.select_for_update().get(pk=order_id)
                if order.status != 'COMPLETED' or order.payment_status != 'PAID' or order.settled_at:
                    continue
                if lifecycle is not None:
                    from orders.financial_lock import lock_order_money
                    lock_order_money()
                    verify(order_id, lifecycle)
                paid = settle_order(order)
                if order.settled_at:
                    count += 1
                if paid or order.settled_at:
                    transaction.on_commit(lambda order=order: notify_order_update(order))
        except Exception:
            logging.getLogger(__name__).exception('Platform subsidy settlement retry failed for order %s', order_id)
    terminated = Order.objects.filter(status='CANCELLED', payment_status='REFUNDED', settled_at__isnull=True,
        refund_records__benefit_effects__subsidy_negotiated=True).order_by('pk').values_list('pk', flat=True).distinct()
    for order_id in terminated.iterator(chunk_size=200):
        try:
            from orders.subsidies import settle_terminated_subsidy
            if settle_terminated_subsidy(order_id):
                count += 1
                order = Order.objects.get(pk=order_id)
                transaction.on_commit(lambda order=order: notify_order_update(order))
        except Exception:
            logging.getLogger(__name__).exception('Terminated subsidy settlement retry failed for order %s', order_id)
    return count
