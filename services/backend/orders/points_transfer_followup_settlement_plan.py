"""Rebuild settlement readiness after two transferred-points refunds."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import Order, OrderPointsTransferFollowupFundingRevision
from .points_checkout_test import require_isolated_checkout
from .settlement import SettlementError


def build_locked(order_id, *, now=None, allow_recorded=False, allow_finalized=False):
    from .points_transfer_refund_access import permits
    formal = permits(phase='refund') or permits(phase='settlement')
    try:
        if not formal:
            require_isolated_checkout()
    except ValidationError as exc:
        raise SettlementError('转单追加退款结算核对尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block or type(order_id) is not int or order_id <= 0
            or (not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING', False))
            or (not formal and allow_recorded and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False))
            or (allow_finalized and not allow_recorded)):
        raise SettlementError('转单追加退款结算核对尚未开放')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if formal and (not order or not any(permits(order, phase=phase) for phase in ('refund', 'settlement'))):
        raise SettlementError('转单追加退款结算范围尚未开放')
    if (not order or order.status != Order.Status.COMPLETED or not order.completed_at
            or (order.settled_at and not allow_finalized)
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.refund_records.count() != 2):
        raise SettlementError('转单追加退款的结算状态待核对')
    revisions = list(OrderPointsTransferFollowupFundingRevision.objects.select_for_update().filter(
        posting__points_refund__redemption__order=order).select_related('posting', 'first_posting')[:2])
    if len(revisions) != 1:
        raise SettlementError('转单追加退款的资金凭证缺失或重复')
    revision = revisions[0]
    from .points_transfer_refund_access import has_paid_addon_before
    if formal and has_paid_addon_before(order, revision.posting.created_at):
        from .points_transfer_followup_scope import addon_snapshot, addon_chain_before
        supported = addon_snapshot(order, revision.first_posting)
        if supported is None:
            supported = addon_chain_before(order, revision.first_posting, revision.posting.created_at,
                                           pending_snapshot=revision.pending_handover_snapshot)
        if not supported:
            raise SettlementError('追加退款加购结算范围尚未开放')
    from .points_transfer_followup_termination import verify_locked as verify_termination
    from .points_transfer_followup_revision import verify_locked as verify_revision, _verify_followup_revision_facts_locked
    from .points_transfer_refund_evidence import (reconstruct_funding_locked, _verify_facts_locked,
                                                  _reconstruct_from_verified_facts)
    from .points_transfer_refunded_plan import build_from_verified_evidence
    try:
        if allow_recorded:
            funding, _ = _verify_followup_revision_facts_locked(revision)
            second = revision.posting
        else:
            second = verify_termination(revision.posting)
            funding, _ = verify_revision(revision)
        first = revision.first_posting
        from .points_transfer_followup_addon_version import latest_run_locked
        latest = latest_run_locked(order, revision)
        if latest:
            from .points_negotiation_preview import RefundedFunding
            funding = RefundedFunding(**latest.points_followup_snapshot['funding'])
        facts = second.participants['facts']
        if allow_recorded:
            _, facts, _ = _verify_facts_locked(order.pk, second.participants, boundary=second.created_at)
            history = _reconstruct_from_verified_facts(order.pk, facts,
                refund_id=first.points_refund.refund_id, through_transfer_id=facts['transfers'][-1]['id'])
        else:
            history = reconstruct_funding_locked(order.pk, second.participants,
                refund_id=first.points_refund.refund_id, through_transfer_id=facts['transfers'][-1]['id'],
                boundary=second.created_at)
        # A later transfer has its own real funding version; its segments
        # replace the second-refund boundary only after that version verifies.
        # The public transfer gate remains closed until receipt replay and
        # nonzero releases are connected.
        from .models import PointsFollowupTransferRevision
        post_transfer = PointsFollowupTransferRevision.objects.filter(order=order).first()
        if post_transfer and latest and latest.created_at < post_transfer.created_at:
            latest = None
        if post_transfer:
            from .points_followup_transfer_revision import available_after_transfer_locked, unresolved_pending_locked
            if unresolved_pending_locked(post_transfer):
                raise SettlementError('转出加购尚未完成三方确认接替')
            from .points_negotiation_preview import RefundedFunding
            if latest:
                from .points_followup_transfer_revision import verify_locked
                verify_locked(post_transfer)
                if (latest.points_followup_snapshot.get('post_followup_transfer_id') != post_transfer.pk
                        or latest.points_followup_snapshot.get('post_followup_transfer_hash') != post_transfer.evidence_hash):
                    raise SettlementError('后续加购没有绑定真实转单版本')
            else:
                available = available_after_transfer_locked(post_transfer)
                from dataclasses import replace
                frozen_funding = RefundedFunding(**post_transfer.snapshot['funding'])
                if available < 0:
                    raise SettlementError('二次退款后转单释放超过原到账')
                funding = replace(frozen_funding, allocated_units=available, release_units=0,
                                  remaining_units=max(frozen_funding.required_units - available, 0))
            facts = post_transfer.snapshot['facts']
            history = _reconstruct_from_verified_facts(order.pk, facts,
                refund_id=first.points_refund.refund_id, through_transfer_id=post_transfer.transfer_id)
            if funding.remaining_units:
                raise SettlementError('二次退款后转单的整单补资尚未足额')
        plan_funding = funding
        from .points_transfer_followup_scope import addon_snapshot, addon_chain_before
        had_addons = has_paid_addon_before(order, second.created_at)
        addon_history = addon_snapshot(order, first) if had_addons else None
        if not addon_history and had_addons:
            addon_history = addon_chain_before(order, first, second.created_at,
                                               pending_snapshot=revision.pending_handover_snapshot)
        if latest:
            addon_history = latest.points_followup_snapshot['addon_history']
        elif post_transfer:
            addon_history = post_transfer.snapshot.get('addon_history')
        if addon_history:
            from dataclasses import replace
            addon_income = sum(item['provider_units'] for item in addon_history)
            if addon_income > funding.provider_income_units:
                raise SettlementError('加购收益超过整单收益')
            plan_funding = replace(funding, provider_income_units=funding.provider_income_units - addon_income)
        plan, _ = build_from_verified_evidence(order, facts, history, [first, second], plan_funding, now=now)
        return plan, funding
    except (ValidationError, KeyError, IndexError, TypeError) as exc:
        raise SettlementError('转单追加退款的履约或资金凭证待核对') from exc
