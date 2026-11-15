"""Isolated-test verification for service-points fulfillment reports."""
from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from .models import Order, OrderProvider, OrderSegmentFinancialSnapshot
from .points_checkout_test import report_enabled
from .points_funding_plan import PointsFundingPlanError, plan_verified_order


def verified_report_snapshot(order, account_id, provider_id, *, allow_finalized=False):
    if not report_enabled():
        raise PointsFundingPlanError('积分订单报单及收益结算尚未接通')
    from .points_business_access import configured, permits
    if configured() and not any(permits(order, phase=phase, kind=kind)
                                for kind in ('service', 'addon') for phase in ('report', 'refund', 'settlement')):
        from .points_transfer_refund_access import permits as transfer_permits
        if not any(transfer_permits(order, phase=phase) for phase in ('report', 'refund', 'settlement')):
            raise PointsFundingPlanError('积分报单上下文与订单不一致')
    if (order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL or order.service_clock_version is None
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100
            or not account_id or not provider_id):
        raise PointsFundingPlanError('订单报单时限或陪玩身份待核对')
    with transaction.atomic():
        if order.refund_records.exists():
            from rest_framework.exceptions import ValidationError
            from .models import PointsNegotiationPosting
            from .points_negotiation_termination import enabled, verify_termination_locked
            if not enabled():
                raise PointsFundingPlanError('积分退款后的报单尚未接通')
            order = Order.objects.select_for_update().get(pk=order.pk)
            from .financial_lock import lock_order_money
            lock_order_money()
            posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
            try:
                refund_count = order.refund_records.count()
                if refund_count == 2:
                    from .points_followup_settlement import enabled as followup_enabled
                    from .points_followup_termination import verify_followup_termination_locked
                    if not followup_enabled():
                        raise ValidationError('后续积分退款报单尚未开放')
                    posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
                        proposal__points_terms__prior_refund__isnull=False).first()
                    if not posting:
                        raise ValidationError('后续积分退款凭证缺失')
                    posting = verify_followup_termination_locked(posting)
                elif not posting or refund_count != 1:
                    raise ValidationError('积分协商退款凭证缺失')
                else:
                    posting = verify_termination_locked(posting)
                if not isinstance(posting.participants, list):
                    # Transferred orders retain one record per participation,
                    # so a legacy person-level report cannot choose a single
                    # snapshot. Their segment report is handled separately.
                    raise ValidationError('转单退款须使用逐段报单')
                evidence = [item for item in posting.participants
                            if item['account_id'] == account_id and item['provider_id'] == provider_id]
                if len(evidence) != 1:
                    raise ValidationError('报单人不属于已核验履约范围')
                return OrderSegmentFinancialSnapshot.objects.get(pk=evidence[0]['snapshot_id'])
            except (ValidationError, OrderSegmentFinancialSnapshot.DoesNotExist) as exc:
                raise PointsFundingPlanError('积分退款后的报单凭证待核对') from exc
        plan_verified_order(order.pk, allow_finalized=allow_finalized)
        if not LegacyAccountMap.objects.filter(account_id=account_id, legacy_user_id=provider_id).exists():
            raise PointsFundingPlanError('报单人账号归属待核对')
        if not OrderProvider.objects.filter(order_id=order.pk, provider_id=provider_id,
                                            provider_account_id=account_id, released_at__isnull=True).exists():
            raise PointsFundingPlanError('报单人订单份额待核对')
        snapshot = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order_id=order.pk,
            segment__provider_account_id=account_id, segment__released_at__isnull=True).first()
        if not snapshot:
            raise PointsFundingPlanError('陪玩收益快照待核对')
        return snapshot
