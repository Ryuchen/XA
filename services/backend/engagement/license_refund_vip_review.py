"""Immutable License growth decisions for already-settled refunded services."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import LicenseRefundVipDecision, LicenseRefundVipReview


def record_locked(order, *, paid_units, refunded_units, payment_vip, confirmation_vip, decided_at):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('会员授权决定须与订单确认同事务保存')
    if (type(paid_units) is not int or type(refunded_units) is not int
            or not 0 <= refunded_units <= paid_units):
        raise ValidationError('会员授权对应的退款净额无效')
    from stores.license_benefits import check_new_benefits
    outcome, evidence = check_new_benefits('VIP_GROWTH')
    return LicenseRefundVipDecision.objects.create(order=order, account_id=order.customer_account_id,
        business_cycle=order.business_cycle, paid_units=paid_units, refunded_units=refunded_units,
        payment_vip_enabled=payment_vip, confirmation_vip_enabled=confirmation_vip,
        outcome=outcome, license_evidence=evidence, decided_at=decided_at)


def verify_decision(order, *, paid_units, refunded_units, payment_vip, confirmation_vip, decided_at):
    decision = LicenseRefundVipDecision.objects.filter(order=order).first()
    if (not decision or decision.account_id != order.customer_account_id
            or decision.business_cycle != order.business_cycle or decision.paid_units != paid_units
            or decision.refunded_units != refunded_units or decision.payment_vip_enabled != payment_vip
            or decision.confirmation_vip_enabled != confirmation_vip or decision.decided_at != decided_at
            or decision.outcome not in ('ALLOW', 'DENY', 'UNKNOWN') or not isinstance(decision.license_evidence, dict)):
        raise ValidationError('退款会员授权决定凭证不一致')
    return decision


def record_deferred_points(order, decision, *, principal, terms, points_enabled):
    """Preserve a separate points decision when the VIP decision is unknown."""
    if (decision.outcome != 'UNKNOWN' or not decision.payment_vip_enabled
            or not decision.confirmation_vip_enabled or not terms['points_enabled'] or not points_enabled):
        return
    points = principal * terms['points_rate'] * terms['birthday_multiplier'] // 100
    if not points:
        return
    from stores.license_benefits import check_new_benefits
    from .models import LicenseConsumptionPointsDecision
    outcome, evidence = check_new_benefits()
    LicenseConsumptionPointsDecision.objects.create(order=order, account_id=order.customer_account_id,
        event_key=f'order:{order.pk}:points', points_amount=points,
        outcome='DENY' if outcome == 'DENY' else 'UNKNOWN',
        license_evidence={'reason': 'VIP_GROWTH_UNKNOWN', 'points_outcome': outcome,
                          'points_evidence': evidence, 'vip_decision_id': decision.pk})


def resolve_locked(decision_id, *, request_id, actor_id, outcome, reason, _batch_verify=False):
    """Record a platform review; original UNKNOWN evidence remains immutable."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('会员授权核对须在门店事务中执行')
    # A verified denial does not execute settlement, refund, or any benefit.
    # It must not depend on a later refund's optional money capability.
    if outcome == 'DENY':
        return _resolve_locked(decision_id, request_id=request_id, actor_id=actor_id,
                               outcome=outcome, reason=reason, _batch_verify=_batch_verify)
    from orders.models import PointsNegotiationPosting
    source = LicenseRefundVipDecision.objects.filter(pk=decision_id).first()
    case_id = (PointsNegotiationPosting.objects.filter(points_refund__redemption__order_id=source.order_id)
               .order_by('-points_refund__refund_id').values_list('proposal__case_id', flat=True).first()) if source else None
    if not case_id:
        if source:
            from orders.models import Order, OrderOriginalShareTransfer
            from orders.points_service_payment import needs_integrated_processing
            order = Order.objects.filter(pk=source.order_id).first()
            if (order and needs_integrated_processing(order) and not order.refund_records.exists()
                    and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                from orders.points_transfer_refund_access import after_transfer, verification_scope
                with after_transfer(order.pk, phase='settlement') as locked:
                    with verification_scope(locked):
                        return _resolve_locked(decision_id, request_id=request_id, actor_id=actor_id,
                                               outcome=outcome, reason=reason, _batch_verify=_batch_verify)
            if order and needs_integrated_processing(order) and not order.refund_records.exists():
                from orders.points_business_access import service_order
                with service_order(order.pk, phase='settlement', addon=order.addon_intents.filter(payment__isnull=False).exists()):
                    return _resolve_locked(decision_id, request_id=request_id, actor_id=actor_id,
                                           outcome=outcome, reason=reason, _batch_verify=_batch_verify)
        return _resolve_locked(decision_id, request_id=request_id, actor_id=actor_id, outcome=outcome, reason=reason, _batch_verify=_batch_verify)
    # A finalized transferred points order must be read under its settlement
    # capability. A refund-case capability alone does not authorize replay of
    # the settled manifest and its funding evidence.
    from orders.models import Order, OrderOriginalShareTransfer
    from orders.points_service_payment import needs_integrated_processing
    order = Order.objects.filter(pk=source.order_id).first() if source else None
    if (order and needs_integrated_processing(order) and order.settled_at
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        from orders.points_transfer_refund_access import after_transfer, verification_scope
        with after_transfer(order.pk, phase='settlement') as locked:
            with verification_scope(locked):
                return _resolve_locked(decision_id, request_id=request_id, actor_id=actor_id,
                                       outcome=outcome, reason=reason, _batch_verify=_batch_verify)
    from orders.points_business_access import case_refund_scope
    with case_refund_scope(case_id):
        return _resolve_locked(decision_id, request_id=request_id, actor_id=actor_id, outcome=outcome, reason=reason, _batch_verify=_batch_verify)


def _verify_original_denial(order, decision):
    """Validate the original UNKNOWN facts; never infer later money state."""
    from django.db.models import Sum
    from wallet.models import Transaction
    from .models import ConsumptionConfirmation, DefaultPaymentPointsSnapshot
    from .refunded_points_models import RefundedPointsConsumptionEvidence, TransferRefundPointsConsumptionEvidence
    evidence = decision.license_evidence
    if (not isinstance(evidence, dict)
            or evidence.get('status') not in ('CONTROL_UNAVAILABLE', 'LICENSE_CONFIGURATION_INVALID',
                                               'STORE_CONTEXT_UNAVAILABLE')
            or order.is_gift_snapshot or not order.completed_at or not order.settled_at
            or decision.paid_units != order.amount or not 0 <= decision.refunded_units <= decision.paid_units
            or order.funding_benefits_resolved_at != decision.decided_at
            or ConsumptionConfirmation.objects.filter(order=order).exists()):
        raise ValidationError('原会员授权或实销事实待核对')
    before = (order.refund_records.filter(created_at__lte=decision.decided_at)
              .aggregate(value=Sum('amount'))['value'] or 0)
    if before != decision.refunded_units:
        raise ValidationError('实销时退款基线与原记录不一致')
    if not Transaction.objects.filter(order=order, wallet__account_id=decision.account_id,
            tx_type=Transaction.TxType.PAY, status=Transaction.Status.SUCCESS,
            amount=-decision.paid_units, created_at__lte=decision.decided_at).exists():
        raise ValidationError('原主单付款流水待核对')
    if order.funding_terms is not None:
        if (not isinstance(order.funding_terms, dict)
                or order.funding_terms.get('vip_enabled') != decision.payment_vip_enabled):
            raise ValidationError('原付款会员开关快照待核对')
    else:
        snapshot = DefaultPaymentPointsSnapshot.objects.filter(order=order).first()
        if not snapshot or snapshot.vip_enabled != decision.payment_vip_enabled:
            raise ValidationError('原付款会员开关快照待核对')
    ordinary = RefundedPointsConsumptionEvidence.objects.filter(
        settlement__posting__points_refund__redemption__order=order).first()
    transferred = TransferRefundPointsConsumptionEvidence.objects.filter(manifest__order=order).first()
    if ordinary and transferred:
        raise ValidationError('原实销凭证存在冲突')
    if ordinary or transferred:
        row = ordinary or transferred
        if ordinary:
            from .refunded_points_confirmation import _digest
        else:
            from .transfer_refunded_points_confirmation import _digest
        if (row.evidence_hash != _digest(row) or row.account_id != decision.account_id
                or row.business_cycle != decision.business_cycle or row.paid_units != decision.paid_units
                or row.refunded_coin_units != decision.refunded_units or row.resolved_at != decision.decided_at
                or row.confirmation_vip_enabled != decision.confirmation_vip_enabled or row.vip_eligible
                or row.confirmation_id is not None):
            raise ValidationError('原退款实销摘要与授权决定不一致')


def _resolve_locked(decision_id, *, request_id, actor_id, outcome, reason, _batch_verify=False):
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise ValidationError('请填写会员授权核对原因')
    reason = reason.strip()
    from orders.models import Order
    source = LicenseRefundVipDecision.objects.filter(pk=decision_id).first()
    if not source:
        raise ValidationError('会员授权核对记录不存在')
    order = Order.objects.select_for_update().get(pk=source.order_id)
    decision = LicenseRefundVipDecision.objects.select_for_update().get(pk=decision_id)
    reviews = LicenseRefundVipReview.objects.filter(decision=decision)
    # The order lock serializes reviews of this decision. Leave the review
    # row unlocked while verifying settlement and waiting for the money and wallet locks.
    existing = reviews.first()
    if existing:
        if existing.request_id != request_id or existing.outcome != outcome or existing.reason != reason:
            raise ValidationError('该记录已核对，请查看原处理结果')
        if not existing.vip_recalculation_pending:
            return existing
        if existing.outcome != 'ALLOW' or not existing.vip_applied:
            raise ValidationError('会员授权审核与重算状态不一致')
    if (decision.outcome != 'UNKNOWN' or outcome not in ('ALLOW', 'DENY')
            or decision.account_id != order.customer_account_id or decision.business_cycle != order.business_cycle):
        raise ValidationError('会员授权核对凭证无效')
    if not existing and LicenseRefundVipReview.objects.filter(request_id=request_id).exists():
        raise ValidationError('请求编号已用于其他会员授权核对')
    if outcome == 'DENY':
        _verify_original_denial(order, decision)
        return LicenseRefundVipReview.objects.create(decision=decision, request_id=request_id,
            platform_actor_id=actor_id, outcome=outcome, reason=reason,
            vip_applied=False, vip_recalculation_pending=False)
    if existing is None:
        from stores.certificate_admission import require_original_certificate
        require_original_certificate(decision.license_evidence)
    current_refunded = decision.refunded_units
    # Verify the immutable original settlement and captured VIP switches.
    from .refunded_points_models import RefundedPointsConsumptionEvidence, TransferRefundPointsConsumptionEvidence
    row = RefundedPointsConsumptionEvidence.objects.filter(
        settlement__posting__points_refund__redemption__order=order).first()
    if row:
        from .refunded_points_confirmation import verify_evidence_locked
    else:
        row = TransferRefundPointsConsumptionEvidence.objects.filter(manifest__order=order).first()
        from .transfer_refunded_points_confirmation import verify_evidence_locked
    if row:
        verify_evidence_locked(row)
        if existing:
            # The original points-refund evidence proves its own snapshot,
            # not any new refund posted after that decision.
            from django.db.models import Sum
            current_refunded = order.refund_records.aggregate(value=Sum('amount'))['value'] or 0
            if current_refunded != decision.refunded_units:
                raise ValidationError('后续退款尚缺完整逐笔实销凭证，请先核对')
    elif order.funding_terms is not None and hasattr(order, 'points_redemption'):
        from orders.models import OrderSegmentSettlementManifest
        from orders.points_benefit_snapshot import verify_locked as verify_snapshot
        from orders.points_funding_evidence import verify_order_funding
        from orders.segment_settlement import verify_finalized
        from orders.models import OrderOriginalShareTransfer
        receipt = order.points_redemption
        manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(
            order=order, finalized_at=order.settled_at).first()
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        if (order.refund_records.exists() or not order.settled_at or (transferred and not manifest)
                or decision.paid_units != receipt.paid_units or decision.refunded_units
                or order.funding_benefits_resolved_at != decision.decided_at):
            raise ValidationError('积分转单会员授权凭证待核对')
        snapshot = verify_snapshot(receipt)
        if snapshot.terms['vip_enabled'] != decision.payment_vip_enabled:
            raise ValidationError('积分转单付款会员快照待核对')
        if transferred:
            verify_finalized(order, manifest)
        else:
            from orders.points_settlement_test import verified_order_settlement_display
            if verified_order_settlement_display(order) != 'SETTLED':
                raise ValidationError('积分服务结算凭证待核对')
        if not verify_order_funding(order.pk, allow_finalized=True).fully_funded:
            raise ValidationError('积分转单到账凭证待核对')
    elif order.funding_terms is not None:
        # Ordinary wallet payment with one closed, verified negotiation.
        from orders.financial_lock import lock_order_money
        from orders.models import OrderOriginalShareTransfer
        from orders.negotiation_settlement import execution_for_order, verify_finalized
        from orders.ordinary_funding_continuation import verified_refunded_continuation
        from orders.points_service_payment import needs_integrated_processing
        from orders.refunds import refunded_amount
        from .models import OrderFundingAllocation
        from django.db.models import Sum
        lock_order_money()
        has_addons = order.addon_intents.filter(payment__isnull=False).exists()
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        current_refunded = refunded_amount(order)
        if transferred:
            refunded_at_decision = (order.refund_records.filter(created_at__lte=decision.decided_at)
                                    .aggregate(value=Sum('amount'))['value'] or 0)
            if refunded_at_decision != decision.refunded_units:
                raise ValidationError('转单实销时的退款快照与原退款记录不一致')
        if (order.is_gift_snapshot or order.source_order_id or not order.settled_at or needs_integrated_processing(order)
                or (not transferred and not has_addons and order.refund_records.count() > 1)
                or (not transferred and order.status_logs.filter(action='TRANSFER').exists())
                or order.funding_benefits_resolved_at != decision.decided_at
                or order.funding_terms.get('vip_enabled') != decision.payment_vip_enabled
                or decision.paid_units != order.amount
                or not decision.refunded_units <= current_refunded <= decision.paid_units
                or (not transferred and decision.refunded_units != current_refunded)):
            raise ValidationError('普通退款会员授权凭证待核对')
        if transferred:
            from .ordinary_transfer_vip_evidence import verify_locked
            verify_locked(order)
        elif has_addons:
            from .ordinary_addon_vip_evidence import verify_locked
            verify_locked(order)
        elif not current_refunded:
            from .ordinary_funding_vip_evidence import verify_locked
            verify_locked(order, decision)
        else:
            execution = execution_for_order(order, lock=True)
            if not execution or not execution.refund_id:
                raise ValidationError('普通退款协商结算凭证缺失')
            with verified_refunded_continuation(order, read_only=True):
                verify_finalized(order, execution)
        sources = OrderFundingAllocation.objects.filter(order=order).aggregate(
            paid=Sum('amount'), returned=Sum('refunded_amount'))
        if sources['paid'] != order.amount or (sources['returned'] or 0) != current_refunded:
            raise ValidationError('普通退款付款来源不一致')
    else:
        # Narrow default-principal service: confirmation kept the original
        # payment snapshot even when growth authorization was unavailable.
        from orders.financial_lock import lock_order_money
        from orders.models import OrderOriginalShareTransfer
        from .default_funding_refunds import verified_sources
        from .models import DefaultPaymentPointsSnapshot
        lock_order_money()
        snapshot = DefaultPaymentPointsSnapshot.objects.select_for_update().filter(order=order).first()
        has_addons = order.addon_intents.filter(payment__isnull=False).exists()
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        if (not snapshot or order.is_gift_snapshot or order.source_order_id or not order.settled_at
                or order.funding_terms is not None
                or (not transferred and order.status_logs.filter(action='TRANSFER').exists())
                or order.funding_benefits_resolved_at != decision.decided_at
                or snapshot.vip_enabled != decision.payment_vip_enabled
                or snapshot.awarded_refunded_principal != decision.refunded_units
                or decision.paid_units != order.amount):
            raise ValidationError('普通服务会员授权凭证待核对')
        if transferred:
            from .default_transfer_vip_evidence import verify_locked
            current_refunded = verify_locked(order, decision)
        elif has_addons or decision.refunded_units or order.refund_records.exists():
            from .default_addon_vip_evidence import verify_locked
            _, current_refunded = verify_locked(order, after_decision=True, baseline=decision.refunded_units,
                unpaid_addon_only=(isinstance(decision.license_evidence, dict)
                                   and decision.license_evidence.get('unpaid_addon_intents') is True))
            if current_refunded < decision.refunded_units:
                raise ValidationError('主单退款金额小于实销时快照，请先核对')
        else:
            sources = verified_sources(order, snapshot)
            if sum(row.amount for row in sources) != order.amount or any(
                    row.kind != 'PREPAID' or row.refunded_amount for row in sources):
                raise ValidationError('普通服务本金付款凭证待核对')
    if existing:
        from wallet.services import get_wallet
        if not get_wallet(account_id=decision.account_id, for_update=True, create=False):
            raise ValidationError('原老板钱包不存在，业务周期待核对')
        existing = reviews.select_for_update().get(pk=existing.pk)
        if not existing.vip_recalculation_pending:
            return existing
        from club_accounts.models import ClubAccount
        account = ClubAccount.objects.select_for_update().get(pk=decision.account_id)
        if account.account_type != 'BOSS' or account.business_cycle != decision.business_cycle:
            raise ValidationError('原老板业务周期已变化，请先核对')
        if _batch_verify:
            return existing
        from .license_vip_pending import is_only_pending
        if not is_only_pending(account.pk, account.business_cycle, kind='refund', review_id=existing.pk):
            raise ValidationError('同周期存在多笔待核对会员成长，不能单笔重算')
        from .services import update_vip
        update_vip(account)
        existing.refresh_from_db(fields=['vip_recalculation_pending'])
        return existing
    from wallet.services import get_wallet
    if not get_wallet(account_id=decision.account_id, for_update=True, create=False):
        raise ValidationError('原老板钱包不存在，业务周期待核对')
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().get(pk=decision.account_id)
    if account.account_type != 'BOSS' or account.business_cycle < decision.business_cycle:
        raise ValidationError('原老板业务周期待核对')
    applied = bool(outcome == 'ALLOW' and decision.payment_vip_enabled and decision.confirmation_vip_enabled
                   and account.business_cycle == decision.business_cycle and decision.paid_units > current_refunded)
    return LicenseRefundVipReview.objects.create(decision=decision, request_id=request_id, platform_actor_id=actor_id,
        outcome=outcome, reason=reason, vip_applied=applied, vip_recalculation_pending=applied)
