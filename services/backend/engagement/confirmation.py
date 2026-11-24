"""Report maturity and financial/benefit processing, safe to retry."""
from django.conf import settings
from datetime import timedelta
from stores.db import transaction
from django.utils import timezone
from orders.models import Order
from orders.notifier import notify_order_update
from wallet.models import ProviderReport
from .models import ConsumptionConfirmation


def notify_committed_order(order_id):
    """Send the latest order state after the surrounding transaction commits."""
    def send():
        order = Order.objects.filter(pk=order_id).first()
        if order:
            notify_order_update(order)
    transaction.on_commit(send)


def refresh_report_clock(order):
    previous_clock = (order.report_ready_at, order.consumption_ready_at)
    from orders.services import required_report_providers
    required = required_report_providers(order)
    reports = list(order.provider_reports.filter(provider_id__in=required))
    ready = None
    if required and None not in required and len(reports) == len(required) and all(
        row.submitted_at and row.status in ('PENDING', 'APPROVED') for row in reports):
        ready = max(row.submitted_at for row in reports)
    order.report_ready_at = ready
    freeze_hours = order.service_freeze_hours if order.service_freeze_hours is not None else 72
    order.consumption_ready_at = ready + timedelta(hours=freeze_hours) if ready else None
    order.save(update_fields=['report_ready_at', 'consumption_ready_at'])
    if previous_clock != (order.report_ready_at, order.consumption_ready_at):
        # Order screens only consume order updates; report updates are private
        # to the provider and reviewers. Read after commit so all recipients
        # see the final committed state, including any settlement in this txn.
        notify_committed_order(order.pk)
    return order.consumption_ready_at


def aftersale_deadline(order):
    snapshot = order.gift_aftersale_hours if order.is_gift_snapshot else order.service_aftersale_hours
    # Historical orders have no verified configuration snapshot; their old
    # completion-based deadline was 72 hours. Never use today's store config.
    hours = snapshot if snapshot is not None else 72
    return order.completed_at + timedelta(hours=hours) if order.completed_at else None


def refund_within_aftersale_window(order, *, now=None):
    """Completed orders need a valid deadline or a timely unresolved case."""
    status = order.status
    # The transition service sets CANCELLED in memory before its refund side
    # effect runs. Consult the still-locked persisted state in that case.
    if status == Order.Status.CANCELLED and order.pk:
        status = Order.objects.filter(pk=order.pk).values_list('status', flat=True).first() or status
    if status != Order.Status.COMPLETED:
        return True
    deadline = aftersale_deadline(order)
    if deadline is None:
        return False
    now = now or timezone.now()
    return now < deadline or order.aftersales.filter(status='OPEN', created_at__lt=deadline).exists()


def aftersale_wait_elapsed(order):
    deadline = aftersale_deadline(order)
    return bool(deadline and timezone.now() >= deadline)


def settlement_ready(order):
    return aftersale_wait_elapsed(order) and not order.aftersales.filter(status='OPEN').exists()


def service_reports_mature(order, *, now=None):
    """All effective submissions have reached their order-snapshot freeze deadline."""
    if order.service_clock_version is None:
        return True  # Historical settlements keep their recorded clock behavior.
    if order.service_freeze_hours is None or order.service_deposit_percent is None:
        return False
    from orders.services import required_report_providers
    required = required_report_providers(order)
    if not required or None in required:
        return False
    reports = list(order.provider_reports.filter(provider_id__in=required))
    if len(reports) != len(required) or any(row.status != 'APPROVED' or not row.submitted_at for row in reports):
        return False
    now = now or timezone.now()
    return all(row.submitted_at + timedelta(hours=order.service_freeze_hours) <= now for row in reports)


def process_order(order_id):
    lifecycle = None
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        from .settlement_lifecycle import capture
        lifecycle = capture(order_id)
    with transaction.atomic():
        if lifecycle is not None:
            from .settlement_lifecycle import lock
            lock(lifecycle)
        return _process_order_with_scopes(order_id, lifecycle=lifecycle)


def _process_order_with_scopes(order_id, *, lifecycle):
    from orders.models import Order
    from orders.points_service_payment import needs_integrated_processing
    candidate = Order.objects.filter(pk=order_id).first()
    if (candidate and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and needs_integrated_processing(candidate)):
        if candidate.status != Order.Status.COMPLETED:
            return False
        from orders.models import OrderOriginalShareTransfer
        transferred = OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=candidate).exists()
        if transferred:
            from orders.points_transfer_refund_access import after_transfer
            with after_transfer(order_id, phase='settlement'):
                return _process_order(order_id, lifecycle=lifecycle)
        # The ordinary one- or two-provider points order needs the same
        # store-bound settlement capability even when it has no aftersale,
        # add-on or reassignment. Otherwise the maturity worker cannot enter
        # the real funding and settlement path for the common case.
        from orders.points_business_access import service_order
        with service_order(order_id, phase='settlement', addon=candidate.addon_intents.filter(payment__isnull=False).exists()):
            return _process_order(order_id, lifecycle=lifecycle)
    return _process_order(order_id, lifecycle=lifecycle)


def _confirm_refunded_addons(order, confirmed):
    """Resolve paid add-ons independently after verified main-order benefits."""
    from orders.addon_settlement import enabled as addon_enabled, has_paid_addons
    order.refresh_from_db(fields=['funding_benefits_resolved_at', 'settled_at'])
    if not order.funding_benefits_resolved_at:
        return confirmed
    if addon_enabled(order) and has_paid_addons(order):
        from orders.addon_consumption import confirm_addon_consumption
        return confirm_addon_consumption(order) or confirmed
    return confirmed


def _fully_refunded_points_transfer_addons(order, funding):
    """Identify a zero-net add-on after the finalized manifest has been verified."""
    from orders.points_transfer_refund_access import permits

    refund_ids = funding.get('addon_refund_ids')
    if (funding.get('addons') != [] or not isinstance(refund_ids, list) or not refund_ids
            or not permits(order, phase='settlement') or order.refund_records.exists()):
        return False
    totals = funding.get('addon_totals')
    return isinstance(totals, dict) and all(type(totals.get(key)) is int and totals[key] == 0 for key in (
        'paid_units', 'principal_units', 'gift_units', 'compensation_units', 'provider_units', 'inviter_units'))


@transaction.atomic
def _process_order(order_id, *, lifecycle=None):
    from orders.services import required_report_providers, settle_order
    from wallet.services import get_wallet
    from .services import update_vip
    from coupons.benefits import try_grant_periodic
    # Lock only the order before the cross-wallet mutex. A joined account row
    # would reverse the account/mutex order against refund VIP reconciliation.
    order = Order.objects.select_for_update().get(pk=order_id)
    if lifecycle is not None:
        from .settlement_lifecycle import verify
        verify(order_id, lifecycle)
    from orders.points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        from orders.points_checkout_test import settlement_enabled
        if settlement_enabled() and not order.settled_at:
            settle_order(order)
            order.refresh_from_db()
        from orders.points_checkout_test import points_consumption_enabled
        if not points_consumption_enabled() or not order.settled_at:
            return False
        # Receipt, funding and each actual income must remain provable before
        # the ordinary net-paid membership confirmation may run.
        from orders.models import OrderOriginalShareTransfer, OrderSegmentSettlementManifest
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        from orders.points_business_access import permits as points_permits
        if (order.refund_records.exists() and not transferred
                and any(points_permits(order, phase='settlement', kind=kind) for kind in ('service', 'addon'))):
            from .refunded_points_confirmation import confirm_refunded_order, enabled as refunded_enabled
            if not refunded_enabled():
                return False
            return _confirm_refunded_addons(order, confirm_refunded_order(order.pk))
        if transferred:
            manifest = OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at__isnull=False).first()
            if not manifest:
                return False
            from orders.segment_settlement import verify_finalized
            from orders.points_benefit_snapshot import verify_locked
            verify_finalized(order, manifest)
            verify_locked(order.points_redemption)
        else:
            from orders.points_settlement_test import verified_order_settlement_display
            if verified_order_settlement_display(order) != 'SETTLED':
                return False
        # Refunded points orders need their own net-paid and principal-only
        # membership confirmation. A verified payout alone is not that proof.
        if order.refund_records.exists():
            if transferred:
                from .transfer_refunded_points_confirmation import confirm_refunded_transfer, enabled as transfer_enabled
                if not transfer_enabled():
                    return False
                return _confirm_refunded_addons(order, confirm_refunded_transfer(order.pk))
            from .refunded_points_confirmation import confirm_refunded_order, enabled as refunded_enabled
            if not refunded_enabled():
                return False
            confirmed = confirm_refunded_order(order.pk)
            return _confirm_refunded_addons(order, confirmed)
    if order.status != 'COMPLETED' or not order.customer_account_id:
        return False
    negotiated = None
    if order.payment_status in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED'):
        from orders.negotiation_settlement import execution_for_order
        negotiated = execution_for_order(order)
    if order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED') and not negotiated:
        return False
    # Direct payment has no trusted settlement/benefit accounting yet. Even
    # a COMPLETION-mode order bypassing its usual transition cannot mature
    # consumption or issue VIP benefits until that integration exists.
    from orders.direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
        return False
    account = order.customer_account
    from orders.financial_lock import lock_order_money
    lock_order_money()
    get_wallet(account_id=account.pk, for_update=True)
    account.refresh_from_db(fields=['business_cycle', 'boss_type'])
    if order.business_cycle != account.business_cycle:
        return False
    from orders.segment_financial_capture import transfer_settlement_pending
    from orders.segment_settlement import enabled as segment_enabled, protection_enabled as segment_protected
    from orders.models import OrderOriginalShareTransfer, OrderSegmentFinancialCoverage, OrderSegmentSettlementManifest
    subsidy_segment_path = (OrderSegmentFinancialCoverage.objects.filter(attribution__order=order, source='PLATFORM_SUBSIDY').exists()
                            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
    # A persisted finalized manifest remains the authority for an in-flight
    # order even when the configuration for newly captured segments changes.
    segment_path = OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at__isnull=False).exists()
    segment_manifest_verified = False
    order_notification_scheduled = False
    if segment_protected():
        segment_path = (segment_path or OrderSegmentSettlementManifest.objects.filter(order=order).exists()
                        or transfer_settlement_pending(order))
    segment_path = segment_path or subsidy_segment_path
    if (negotiated and negotiated.refund_id and isinstance(negotiated.refund.benefit_effects, dict)
            and negotiated.refund.benefit_effects.get('ordinary_transfer_negotiation_version') == 1):
        from orders.negotiation_execution import verify_execution
        from orders.negotiation_termination import verify_termination_evidence
        verify_execution(negotiated)
        verify_termination_evidence(negotiated)
        segment_path, negotiated = True, None
    elif (negotiated and not negotiated.refund_id
          and (order.ordinary_transfer_followups.filter(execution=negotiated).exists()
               or hasattr(negotiated, 'transfer_evidence'))
          and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        # The latest round may close without another money refund. The
        # finalized segment evidence and the membership verifier below check
        # the earlier actual refunds and every later agreement.
        segment_path, negotiated = True, None
    if negotiated:
        previous_clock = (order.report_ready_at, order.consumption_ready_at)
        ready = refresh_report_clock(order)
        order_notification_scheduled = previous_clock != (order.report_ready_at, order.consumption_ready_at)
        required = required_report_providers(order)
        approved = set(order.provider_reports.filter(status='APPROVED').values_list('provider_id', flat=True))
        if not required or None in required:
            return False
        if not order.settled_at:
            paid = settle_order(order)
            if paid and not order_notification_scheduled:
                transaction.on_commit(lambda: notify_order_update(order))
                order_notification_scheduled = True
            if not order.settled_at:
                return False
        if not settlement_ready(order) or not required.issubset(approved):
            return False
        from orders.negotiation_settlement import verify_finalized
        verify_finalized(order, negotiated)
    elif segment_path:
        if not order.settled_at:
            from orders.subsidy_segment_settlement import enabled as subsidy_segment_enabled
            if not segment_enabled() and not (subsidy_segment_path and subsidy_segment_enabled()):
                return False
            paid = settle_order(order)
            if paid:
                transaction.on_commit(lambda: notify_order_update(order))
                order_notification_scheduled = True
        manifest = OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at__isnull=False).first()
        if not order.settled_at or not manifest or not settlement_ready(order):
            return False
        from orders.segment_settlement import verify_finalized
        verify_finalized(order, manifest)
        segment_manifest_verified = True
        ready = order.consumption_ready_at
        clock_changed = False
    else:
        previous_clock = (order.report_ready_at, order.consumption_ready_at)
        ready = refresh_report_clock(order)
        clock_changed = previous_clock != (order.report_ready_at, order.consumption_ready_at)
        # refresh_report_clock already schedules a post-commit update when it
        # changes. One final update also covers settlement and confirmation
        # occurring later in this same transaction.
        order_notification_scheduled = clock_changed
        required = required_report_providers(order)
        approved = set(order.provider_reports.filter(status='APPROVED').values_list('provider_id', flat=True))
        if not required or None in required or not aftersale_wait_elapsed(order):
            return False
        from orders.service_settlement import supports_partial_service_settlement
        partial = supports_partial_service_settlement(order) or (
                    order.funding_terms is not None and not order.is_gift_snapshot and not order.source_order_id)
        if partial and not order.settled_at:
            paid = settle_order(order)
            if paid and not order_notification_scheduled:
                transaction.on_commit(lambda: notify_order_update(order))
                order_notification_scheduled = True
            if not order.settled_at:
                return False
        if not settlement_ready(order):
            return False
        if not required.issubset(approved):
            return False
    previous_state = (order.settled_at, order.consumption_confirmed_at)

    def notify_changed_order():
        if not order_notification_scheduled and (order.settled_at, order.consumption_confirmed_at) != previous_state:
            transaction.on_commit(lambda: notify_order_update(order))

    # Legacy/non-partial orders keep their recorded settlement-clock rules;
    # confirmation below also requires the last effective report clock.
    if order.settlement_mode == 'REPORT_APPROVAL' and not order.settled_at:
        if not service_reports_mature(order):
            return False
        settle_order(order)
        if not order.settled_at:
            return False
    if order.is_gift_snapshot or not ready or ready > timezone.now():
        notify_changed_order()
        return False
    from orders.addon_settlement import enabled as addon_settlement_enabled, has_paid_addons
    if has_paid_addons(order):
        if segment_path:
            # The ordinary segment branch just verified this manifest under
            # the same locks. An alternate negotiated path has not done so.
            if not segment_manifest_verified:
                manifest = OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at__isnull=False).first()
                if not manifest:
                    return False
                from orders.segment_settlement import verify_finalized
                verify_finalized(order, manifest)
            terms = manifest.plan if isinstance(manifest.plan, dict) else {}
            funding = terms.get('ordinary_transfer_funding') or terms.get('points_funding') or {}
            if (not funding.get('addons') and not funding.get('addon_history')
                    and not _fully_refunded_points_transfer_addons(order, funding)):
                return False
        else:
            if not addon_settlement_enabled(order):
                return False
            from orders.points_settlement_test import verified_order_settlement_display
            if verified_order_settlement_display(order) != 'SETTLED':
                return False
        from orders.addon_consumption import confirm_addon_consumption
        confirm_addon_consumption(order)
    if order.funding_terms is not None:
        if order.funding_benefits_resolved_at:
            notify_changed_order()
            return False
        from django.db.models import Sum
        from .models import ClubOperationConfig, OrderFundingAllocation
        from .services import award_order_points, config
        config()
        operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
        order.funding_award_refunded_principal = (OrderFundingAllocation.objects.filter(
            order=order, kind='PREPAID').aggregate(value=Sum('refunded_amount'))['value'] or 0)
        decision = None
        from orders.points_business_access import permits as benefit_permits
        from orders.points_transfer_refund_access import permits as transfer_benefit_permits
        if (not order.refund_records.exists() and needs_integrated_processing(order)
                and (benefit_permits(order, phase='settlement', kind='service')
                     or benefit_permits(order, phase='settlement', kind='addon')
                     or transfer_benefit_permits(order, phase='settlement'))
                and order.funding_terms['vip_enabled'] and operation.vip_enabled):
            from orders.points_benefit_snapshot import verify_locked as verify_points_snapshot
            from orders.points_funding_evidence import verify_order_funding
            from .license_refund_vip_review import record_locked
            receipt = order.points_redemption
            verify_points_snapshot(receipt)
            funding = verify_order_funding(order.pk, allow_finalized=True)
            if not funding.fully_funded or receipt.paid_units != order.amount:
                return False
            decision = record_locked(order, paid_units=receipt.paid_units, refunded_units=0,
                payment_vip=True, confirmation_vip=True, decided_at=timezone.now())
        if (segment_path
                and not needs_integrated_processing(order) and not order.source_order_id
                and OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists()
                and order.funding_terms['vip_enabled'] and operation.vip_enabled):
            from .ordinary_transfer_vip_evidence import verify_locked
            from .license_refund_vip_review import record_locked
            from orders.refunds import refunded_amount
            verify_locked(order)
            decision = record_locked(order, paid_units=order.amount, refunded_units=refunded_amount(order),
                payment_vip=True, confirmation_vip=True, decided_at=timezone.now())
        if (has_paid_addons(order) and order.refund_records.count() <= 1
                and not needs_integrated_processing(order) and not order.source_order_id
                and not order.status_logs.filter(action='TRANSFER').exists()
                and not OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists()
                and order.funding_terms['vip_enabled'] and operation.vip_enabled):
            from .ordinary_addon_vip_evidence import verify_locked
            from .license_refund_vip_review import record_locked
            from orders.refunds import refunded_amount
            verify_locked(order)
            decision = record_locked(order, paid_units=order.amount, refunded_units=refunded_amount(order),
                payment_vip=True, confirmation_vip=True, decided_at=timezone.now())
        if (negotiated and negotiated.refund_id and order.refund_records.count() == 1
                and not needs_integrated_processing(order) and not has_paid_addons(order)
                and not order.source_order_id and not order.status_logs.filter(action='TRANSFER').exists()
                and not OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists()
                and order.funding_terms['vip_enabled'] and operation.vip_enabled):
            # The negotiation branch above has already verified the finalized
            # settlement. Preserve the License decision for its actual net
            # payment, separately from the returned principal used by points.
            from .license_refund_vip_review import record_locked
            from orders.refunds import refunded_amount
            decision = record_locked(order, paid_units=order.amount, refunded_units=refunded_amount(order),
                payment_vip=True, confirmation_vip=True, decided_at=timezone.now())
        if decision is None and order.funding_terms['vip_enabled'] and operation.vip_enabled:
            # Every actual confirmation needs its own License decision. More
            # complex orders may remain pending review, but must never become
            # VIP growth simply because no specialized verifier was selected.
            from .license_refund_vip_review import record_locked
            from orders.refunds import refunded_amount
            decision = record_locked(order, paid_units=order.amount, refunded_units=refunded_amount(order),
                payment_vip=True, confirmation_vip=True, decided_at=timezone.now())
        order.funding_benefits_resolved_at = decision.decided_at if decision else timezone.now()
        order.save(update_fields=['funding_award_refunded_principal', 'funding_benefits_resolved_at'])
        from .achievement_metrics import record_achievement_event
        record_achievement_event(order)
        # The payment and the confirmation must both fall in enabled VIP
        # periods. Record the decision even when disabled so a later re-enable
        # cannot retroactively grant this consumption or its points.
        if not order.funding_terms['vip_enabled'] or not operation.vip_enabled:
            notify_changed_order()
            return False
        if decision and decision.outcome != 'ALLOW':
            if decision.outcome == 'UNKNOWN':
                from .license_refund_vip_review import record_deferred_points
                principal = OrderFundingAllocation.objects.filter(order=order, kind='PREPAID').aggregate(
                    paid=Sum('amount'), returned=Sum('refunded_amount'))
                net_principal = (principal['paid'] or 0) - (principal['returned'] or 0)
                record_deferred_points(order, decision, principal=net_principal,
                    terms=order.funding_terms, points_enabled=operation.points_enabled)
            notify_changed_order()
            return False
        award_order_points(order, service_confirmed=True, operation=operation)
    else:
        from .models import ClubOperationConfig, DefaultPaymentPointsSnapshot
        from .services import award_order_points, config
        snapshot = DefaultPaymentPointsSnapshot.objects.filter(order=order).first()
        if snapshot is None:
            return False
        if order.funding_benefits_resolved_at:
            notify_changed_order()
            return False
        config()
        operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
        # The default pure-principal service has a complete payment snapshot.
        # Record the License decision at the same instant as confirmation so
        # later renewal cannot grant a previously unavailable benefit.
        reviewable = (not order.refund_records.exists() and not has_paid_addons(order)
                      and not order.status_logs.filter(action='TRANSFER').exists()
                      and not order.source_order_id
                      and not OrderOriginalShareTransfer.objects.filter(
                          previous_segment__attribution__order=order).exists()
                      and not Order.objects.filter(source_order=order, is_gift_snapshot=False).exists())
        decision = evidence = None
        deferred_principal = deferred_terms = None
        deferred_refunded = 0
        if reviewable and snapshot.vip_enabled and operation.vip_enabled:
            from .default_funding_refunds import verified_sources
            sources = verified_sources(order, snapshot)
            reviewable = bool(sources) and sum(item.amount for item in sources) == order.amount and all(
                item.kind == 'PREPAID' and not item.refunded_amount for item in sources)
        if reviewable and snapshot.vip_enabled and operation.vip_enabled:
            from .default_principal_evidence import verify_locked
            from .license_refund_vip_review import record_locked
            evidence = verify_locked(order)
            deferred_principal, deferred_terms = evidence.principal, evidence.membership_terms
            decided_at = timezone.now()
            decision = record_locked(order, paid_units=order.amount, refunded_units=0,
                payment_vip=snapshot.vip_enabled, confirmation_vip=operation.vip_enabled, decided_at=decided_at)
        elif (not order.source_order_id
              and not Order.objects.filter(source_order=order, is_gift_snapshot=False).exists()
              and ((segment_path and OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists())
                   or ((has_paid_addons(order) or order.refund_records.exists())
                       and not order.status_logs.filter(action='TRANSFER').exists()
                       and not OrderOriginalShareTransfer.objects.filter(
                           previous_segment__attribution__order=order).exists()))
              and snapshot.vip_enabled and operation.vip_enabled):
            # Settlement was checked above. Record the main order's separate
            # decision without undoing it on a control-plane outage.
            from .license_refund_vip_review import record_locked
            from .default_funding_refunds import verified_sources
            from orders.refunds import refunded_amount
            sources = verified_sources(order, snapshot)
            deferred_refunded = refunded_amount(order)
            deferred_principal = sum(row.amount - row.refunded_amount for row in sources if row.kind == 'PREPAID')
            deferred_terms = {'points_enabled': snapshot.points_enabled, 'points_rate': snapshot.points_rate,
                              'birthday_multiplier': snapshot.birthday_multiplier}
            # The authorization decision belongs to the actual entitlement
            # point, after waiting for the member's points row as well.
            from .services import locked_profile
            locked_profile(account)
            decision = record_locked(order, paid_units=order.amount, refunded_units=deferred_refunded,
                payment_vip=True, confirmation_vip=True, decided_at=timezone.now())
        if decision is None and snapshot.vip_enabled and operation.vip_enabled:
            from .license_refund_vip_review import record_locked
            from orders.refunds import refunded_amount
            deferred_refunded = refunded_amount(order)
            decision = record_locked(order, paid_units=order.amount, refunded_units=deferred_refunded,
                payment_vip=True, confirmation_vip=True, decided_at=timezone.now())
            if decision.outcome == 'UNKNOWN':
                # Save the authorization fact even if optional payment-source
                # proof cannot yet establish a safe amount for points.
                from .default_funding_refunds import verified_sources
                from rest_framework.exceptions import ValidationError
                try:
                    sources = verified_sources(order, snapshot)
                except ValidationError:
                    sources = None
                if sources is not None:
                    deferred_principal = sum(item.amount - item.refunded_amount for item in sources
                                             if item.kind == 'PREPAID')
                    deferred_terms = {'points_enabled': snapshot.points_enabled, 'points_rate': snapshot.points_rate,
                                      'birthday_multiplier': snapshot.birthday_multiplier}
                else:
                    decision.license_evidence = {**decision.license_evidence,
                        'points_source_pending': True, 'payment_snapshot_id': snapshot.pk}
                    decision.save(update_fields=['license_evidence'])
        if decision and order.addon_intents.exists() and not has_paid_addons(order):
            # Preserve the real draft-only state for a later refund review.
            decision.license_evidence = {**decision.license_evidence, 'unpaid_addon_intents': True}
            decision.save(update_fields=['license_evidence'])
        order.funding_benefits_resolved_at = timezone.now()
        if decision:
            order.funding_benefits_resolved_at = decision.decided_at
        order.save(update_fields=['funding_benefits_resolved_at'])
        from .achievement_metrics import record_achievement_event
        record_achievement_event(order)
        if not snapshot.vip_enabled or not operation.vip_enabled:
            notify_changed_order()
            return False
        if decision and decision.outcome != 'ALLOW':
            if decision.outcome == 'UNKNOWN':
                from .license_refund_vip_review import record_deferred_points
                if deferred_principal is not None and deferred_terms is not None:
                    # Growth uses total returned coin; points use returned
                    # principal only. Never substitute one for the other.
                    snapshot.awarded_refunded_principal = sum(
                        item.refunded_amount for item in sources if item.kind == 'PREPAID')
                    snapshot.save(update_fields=['awarded_refunded_principal'])
                    record_deferred_points(order, decision, principal=deferred_principal,
                                           terms=deferred_terms, points_enabled=operation.points_enabled)
            notify_changed_order()
            return False
        award_order_points(order, service_confirmed=True, operation=operation)
    from orders.refunds import refunded_amount
    row, new = ConsumptionConfirmation.objects.get_or_create(order=order, defaults={
        'account': account, 'business_cycle': order.business_cycle, 'amount': order.amount,'refunded_amount':refunded_amount(order)})
    if not order.consumption_confirmed_at:
        order.consumption_confirmed_at = row.confirmed_at
        order.save(update_fields=['consumption_confirmed_at'])
    update_vip(account, operation=operation)
    try_grant_periodic(account)
    notify_changed_order()
    return new
