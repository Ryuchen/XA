"""Tenant-local, independently retryable settlement of transferred service shares."""
import hashlib
import json
from dataclasses import asdict

from django.conf import settings
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest, RefundRecoveryLine
from .segment_settlement_plan import build_segment_settlement_plan
from .settlement import SettlementError


def enabled(order=None):
    from .segment_reports import enabled as reports_enabled
    from .principal_segments import enabled_for_order
    return bool(getattr(settings, 'SEGMENT_SETTLEMENT_ENABLED', False)
                and (reports_enabled() or (order is not None and enabled_for_order(order))))


def completion_transfer_testing_enabled(order=None):
    """Limit COMPLETION transfer settlement to the disposable integration path."""
    if not (getattr(settings, 'ORDINARY_COMPLETION_TRANSFER_INTERNAL_TESTING', False)
            and getattr(settings, 'SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING', False)
            and enabled(order)):
        return False
    from .points_checkout_test import require_isolated_checkout
    from rest_framework.exceptions import ValidationError
    try:
        require_isolated_checkout(allow_default_principal=True, existing_order=order)
    except ValidationError:
        return False
    return True


def completion_transfer_order_eligible(order):
    """The first isolated slice accepts only an unrefunded principal-only service."""
    if (not completion_transfer_testing_enabled(order) or order.is_gift_snapshot or order.source_order_id
            or order.addon_intents.exists() or order.refund_records.exists()):
        return False
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        return False
    sources = list(order.funding_allocations.values_list('kind', 'amount', 'refunded_amount'))
    return bool(sources and sum(amount for _, amount, _ in sources) == order.amount
                and all(kind == 'PREPAID' and not refunded for kind, _, refunded in sources))


def protection_enabled():
    # Fail closed even if an operator disables a prerequisite but forgets to
    # turn off this flag: previously posted receipts must remain protected.
    return bool(getattr(settings, 'SEGMENT_SETTLEMENT_ENABLED', False)
                or getattr(settings, 'SEGMENT_SETTLEMENT_ENFORCE_ENABLED', False))


def _ordinary_transfer_refund(order):
    from .models import OrderTransferNegotiationEvidence
    from .negotiation_zero import marked_execution_id
    from .models import OrdinaryPostRefundTransferRevision
    zero_id = marked_execution_id(order)
    zero_before_transfer = bool(zero_id and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
        snapshot__zero_pretransfer_execution_id=zero_id).exists())
    positive_before_transfer = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
        snapshot__positive_pretransfer_execution_id__isnull=False).exists()
    return (zero_before_transfer or positive_before_transfer or OrderTransferNegotiationEvidence.objects.filter(order=order, execution__zero_order=order,
                execution__termination_processed_at__isnull=False).exists() or any(isinstance(row.benefit_effects, dict)
               and row.benefit_effects.get('ordinary_transfer_negotiation_version') == 1
               for row in order.refund_records.all()))


def _post_refund_transfer_version(order):
    from .models import OrdinaryPostRefundTransferRevision, OrdinaryTransferFollowupRound
    revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
    last_refund = order.refund_records.order_by('-created_at', '-pk').first()
    from .models import OrderTransferNegotiationEvidence
    first = OrderTransferNegotiationEvidence.objects.filter(order=order).select_related('execution').first()
    if not first and revision and (revision.snapshot.get('zero_pretransfer_execution_id')
                                   or revision.snapshot.get('positive_pretransfer_execution_id')):
        last_round = OrdinaryTransferFollowupRound.objects.filter(order=order,
            execution__termination_processed_at__isnull=False).select_related('execution').order_by('-sequence').first()
        return not last_round or revision.created_at > last_round.execution.termination_processed_at
    baseline = last_refund.created_at if last_refund else (first.execution.termination_processed_at if first else None)
    last_round = (OrdinaryTransferFollowupRound.objects.filter(order=order, execution__isnull=False)
                  .select_related('execution').order_by('-sequence').first())
    return bool(revision and baseline and revision.created_at > baseline
                and (not last_round or (last_round.execution.termination_processed_at
                                         and revision.created_at > last_round.execution.termination_processed_at)))


def reject_legacy_refund(order):
    """New credited shares cannot be interpreted using the old split ledger."""
    from .models import OrderOriginalShareTransfer, OrderPlatformSubsidy
    if (OrderPlatformSubsidy.objects.filter(order=order).exists()
            and (OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
                 or OrderSegmentSettlementManifest.objects.filter(order=order).exists())):
        from .refunds import RefundError
        raise RefundError('安全箱逐段退款与补贴核算待核对', code='SEGMENT_SETTLEMENT_REFUND_REVIEW')
    if not protection_enabled():
        return
    captured_service = OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order,
        previous_segment__attribution__financial_coverage__isnull=False,
        previous_segment__started_at__isnull=False).exists()
    if captured_service or OrderSegmentSettlementManifest.objects.filter(order=order).exists():
        from .refunds import RefundError
        raise RefundError('本单逐段退款分摊及资金处理待核对', code='SEGMENT_SETTLEMENT_REFUND_REVIEW')


def _terms(order, plan, *, points_funding=None, ordinary_transfer_funding=None):
    terms = {'deal_amount': plan.deal_amount, 'segments': [asdict(row) for row in plan.segments],
        'people': [{'account_id':p.account_id, 'segment_ids':list(p.segment_ids), 'income':p.income} for p in plan.persons],
        'inviter_income':plan.inviter_income, 'store_income':plan.store_income,
        'fixed_fee_shortfall':plan.fixed_fee_shortfall, 'buyer_account_id':order.customer_account_id,
        'inviter_account_id':order.inviter_account_id, 'inviter_id':order.inviter_id,
        'inviter_rate':order.inviter_commission_rate_snapshot, 'deposit_percent':order.service_deposit_percent,
        'aftersale_hours':order.service_aftersale_hours, 'freeze_hours':order.service_freeze_hours,
        'completed_at':order.completed_at.isoformat() if order.completed_at else None}
    if points_funding is not None:
        terms['points_funding'] = points_funding
        if 'third_addon_rounding' in points_funding:
            terms['points_addon_rounding'] = [
                {'refund_id': refund_id, 'amount': amount}
                for refund_id, amount in points_funding['third_addon_rounding']]
        from .models import PointsNegotiationPosting
        postings = list(PointsNegotiationPosting.objects.filter(
            points_refund__redemption__order=order).order_by('created_at', 'pk'))
        if postings:
            terms['points_refund_rounding'] = [
                {'posting_id': posting.pk, 'amount': posting.rounding_to_store_units}
                for posting in postings]
    if ordinary_transfer_funding is not None:
        terms['ordinary_transfer_funding'] = ordinary_transfer_funding
        from django.db.models import Q
        from .models import NegotiationExecution
        execution = NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
            termination_processed_at__isnull=False).order_by('pk').first()
        if not execution:
            raise SettlementError('转单退款执行凭证缺失')
        terms['ordinary_transfer_rounding'] = execution.rounding_to_store_units
        if 'addon_rounding' in ordinary_transfer_funding:
            terms['ordinary_addon_rounding'] = [
                {'refund_id': refund_id, 'amount': amount}
                for refund_id, amount in ordinary_transfer_funding.get('addon_rounding', [])]
        if (ordinary_transfer_funding.get('followup_round_id')
                or ordinary_transfer_funding.get('post_refund_revision_id')):
            from .models import OrdinaryTransferFollowupRound
            terms['ordinary_followup_rounding'] = [
                {'round_id': row.pk, 'amount': row.execution.rounding_to_store_units}
                for row in OrdinaryTransferFollowupRound.objects.filter(order=order, execution__isnull=False)
                    .select_related('execution').order_by('sequence', 'pk')]
    return terms


def _deduct_addon_refunds(plan, combined):
    """Keep earned segment evidence, but pay each person their unpaid net."""
    if not combined or not combined.main_deductions:
        return plan
    from dataclasses import replace
    deductions = dict(combined.main_deductions)
    if not set(deductions).issubset({person.account_id for person in plan.persons}):
        raise SettlementError('加购退款承担人不属于主单履约人员')
    people = []
    for person in plan.persons:
        amount = deductions.get(person.account_id, 0)
        if amount > person.income:
            raise SettlementError('加购退款扣减超过本人主单收益')
        people.append(replace(person, income=person.income - amount))
    return replace(plan, persons=tuple(people))


def _points_transfer_terms(order):
    """Prove the full shared funding before any individual is credited."""
    from .points_transfer_refund_access import permits, verification_scope
    if permits(order, phase='settlement') and order.refund_records.exists():
        with verification_scope(order):
            return _points_transfer_terms_verified(order)
    return _points_transfer_terms_verified(order)


def _points_transfer_terms_verified(order):
    from .points_funding_plan import PointsFundingPlanError
    from .points_funding_evidence import (VerifiedOrderFundingWithAllocations, verify_order_funding,
                                          verified_order_allocations_locked)
    if order.refund_records.exists():
        if order.refund_records.count() > 3:
            from .points_transfer_refund_access import permits as transfer_permits
            if not any(transfer_permits(order, phase=phase) for phase in ('refund', 'settlement')):
                raise SettlementError('多轮退款逐段结算尚未开放')
            from .points_round_settlement_plan import prepare_locked
            plan, terms = prepare_locked(order.pk, allow_finalized=bool(order.settled_at))
            return plan, terms['points_funding']
        if order.refund_records.count() == 3:
            from .points_transfer_refund_access import permits as transfer_permits
            if not any(transfer_permits(order, phase=phase) for phase in ('refund', 'settlement')):
                raise SettlementError('第三轮退款逐段结算尚未开放')
            from .points_third_refund_settlement_plan import prepare_locked
            plan, terms = prepare_locked(order.pk, allow_finalized=bool(order.settled_at))
            return plan, terms['points_funding']
        if order.refund_records.count() == 2:
            from .points_transfer_refund_access import permits as transfer_permits
            if not (any(transfer_permits(order, phase=phase) for phase in ('refund', 'settlement'))
                    or getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False)):
                raise SettlementError('转单追加退款后的逐人结算尚未开放')
            from .points_transfer_followup_settlement_plan import build_locked
            from .points_transfer_followup_settlement import prepare_locked
            plan, _ = build_locked(order.pk, allow_recorded=True, allow_finalized=bool(order.settled_at))
            try:
                verified = verify_order_funding(order.pk, allow_finalized=bool(order.settled_at),
                                                allow_followup_recorded=True)
            except PointsFundingPlanError as exc:
                raise SettlementError('追加退款后的补资凭证待核对') from exc
            if not verified.fully_funded:
                return plan, None
            plan, terms, _ = prepare_locked(order.pk, allow_recorded=True, allow_finalized=bool(order.settled_at))
            return plan, terms['points_funding']
        from rest_framework.exceptions import ValidationError
        from .models import OrderPointsFundingRelease, PointsNegotiationPosting, PointsPostRefundTransferRelease
        from .points_transfer_refunded_plan import build_refunded_plan
        from .points_transfer_refund_evidence import verify_transfer_revisions_after_refund_locked
        try:
            plan, funding = build_refunded_plan(order)
            verified = verify_order_funding(order.pk, allow_finalized=True)
            allocations = verified_order_allocations_locked(order)
            posting = PointsNegotiationPosting.objects.get(points_refund__redemption__order=order)
            transferred = verify_transfer_revisions_after_refund_locked(order.pk, posting.participants,
                refund_id=posting.points_refund.refund_id)
        except (PointsFundingPlanError, ValidationError, PointsNegotiationPosting.DoesNotExist) as exc:
            raise SettlementError('转单退款后的补资或收益凭证待核对') from exc
        if not verified.fully_funded:
            return plan, None
        from .models import PointsPostRefundTransferRevision, OrderAddonPayment
        version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
        addon_history = (version.snapshot.get('addon_history') if version else
                         getattr(getattr(posting, 'funding_release_evidence', None), 'addon_funding_snapshot', None))
        addon_boundary = version.transfer.created_at if version else posting.created_at
        if version:
            from .points_transfer_addon_events import later_runs
            try:
                later = [row for row in later_runs(order, posting) if row.created_at > version.created_at]
            except ValidationError as exc:
                raise SettlementError('转单后的加购资金版本待核对') from exc
            if later:
                from django.utils.dateparse import parse_datetime
                latest_snapshot = later[-1].refunded_main_snapshot
                if (not isinstance(latest_snapshot, dict)
                        or latest_snapshot.get('transfer_revision_id') != version.pk):
                    raise SettlementError('转单后的加购资金来源待核对')
                addon_history = latest_snapshot.get('addon_history')
                addon_boundary = parse_datetime(latest_snapshot.get('captured_at', ''))
                if not isinstance(addon_history, list) or not addon_boundary:
                    raise SettlementError('加购资金版本时间待核对')
        if not version:
            from .points_transfer_addon_events import later_runs
            try:
                later = later_runs(order, posting)
            except ValidationError as exc:
                raise SettlementError('主单退款后的加购资金版本待核对') from exc
            if later:
                from django.utils.dateparse import parse_datetime
                latest_snapshot = later[-1].refunded_main_snapshot
                if (not isinstance(latest_snapshot, dict)
                        or latest_snapshot.get('transfer_origin_posting_id') != posting.pk):
                    raise SettlementError('主单退款后的加购资金来源待核对')
                addon_history = latest_snapshot.get('addon_history')
                addon_boundary = parse_datetime(latest_snapshot.get('captured_at', ''))
                if not addon_boundary:
                    raise SettlementError('加购资金版本时间待核对')
        addon_parts = []
        addon_totals = {}
        addon_refunds = []
        if addon_history:
            from .addon_payment import verify_receipt, verify_order_debit_links
            from .addon_handover import assert_no_pending_handover, current_responsibilities
            from .addon_completion import verify_accepted
            verify_order_debit_links(order, table_exists=True, lock=True)
            boundary = addon_boundary
            eligible = {(segment_id, person.account_id) for person in plan.persons for segment_id in person.segment_ids}
            history_ids = [item.get('payment_id') for item in addon_history if isinstance(item, dict)]
            payments = list(OrderAddonPayment.objects.select_for_update().select_related('intent', 'buyer_transaction__wallet')
                            .filter(intent__order=order, buyer_transaction__created_at__lt=boundary).order_by('pk'))
            if (len(history_ids) != len(addon_history) or len(set(history_ids)) != len(history_ids)
                    or set(history_ids) != {payment.pk for payment in payments}):
                raise SettlementError('积分转单加购付款历史不完整')
            by_payment = {payment.pk: payment for payment in payments}
            keys = ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
                    'provider_units', 'inviter_units', 'recovery_units')
            addon_totals = {key: 0 for key in keys}
            for expected in addon_history:
                payment = by_payment[expected['payment_id']]
                verify_receipt(payment)
                assert_no_pending_handover(payment)
                refunds = list(payment.refund_records.order_by('created_at', 'pk'))
                if any(item.created_at >= boundary for item in refunds):
                    raise SettlementError('积分转单加购退款时序待核对')
                addon_refunds.extend(refunds)
                handover, responsibilities = current_responsibilities(payment, lock=True)
                if ((not responsibilities and
                        (expected['paid_units'] != 0 or expected['provider_units'] != 0
                         or sum(item.amount for item in refunds) != payment.intent.amount))
                        or sum(part['income'] for part in responsibilities) != expected['provider_units']
                        or sum(part['basis'] for part in responsibilities) != expected['paid_units']
                        or (handover.pk if handover else None) != expected['handover_id']):
                    raise SettlementError('积分转单加购收益与资金版本不一致')
                for part in responsibilities:
                    if (part['provider_id'] in posting.proposal.affected_accounts
                            and (not posting.proposal.affected_segment_ids
                                 or part['segment_id'] in posting.proposal.affected_segment_ids)
                            and part['is_current']):
                        verify_accepted(order, payment, posting, part)
                    if (part['segment_id'], part['provider_id']) not in eligible:
                        raise SettlementError('积分转单加购不属于可结算履约段')
                    addon_parts.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                        'provider_account_id': part['provider_id'], 'provider_income_units': part['income']})
                for key in keys:
                    addon_totals[key] += expected[key]
            # Multiple confirmed handovers can retain separate shares for
            # the same person on the same segment.  One settlement row owns
            # that payment/segment, so aggregate only those exact shares.
            merged_parts = {}
            for part in addon_parts:
                key = (part['payment_id'], part['segment_id'], part['provider_account_id'])
                if key not in merged_parts:
                    merged_parts[key] = dict(part)
                else:
                    merged_parts[key]['provider_income_units'] += part['provider_income_units']
            addon_parts = list(merged_parts.values())
        from .addon_funding_release import addon_release_totals
        addon_released = addon_release_totals(allocations)
        items = []
        for row in allocations:
            refunded = sum(OrderPointsFundingRelease.objects.filter(allocation=row).values_list('amount', flat=True))
            post_refund = sum(PointsPostRefundTransferRelease.objects.filter(allocation=row).values_list('amount', flat=True))
            net = row.amount - transferred.get(row.pk, 0) - refunded - post_refund - addon_released.get(row.pk, 0)
            if net < 0:
                raise SettlementError('转单退款后的补资释放超过原分配')
            items.append({'id': row.pk, 'receipt_id': row.receipt_id, 'amount': net})
        calculation = asdict(funding)
        for key in ('allocated_units', 'release_units', 'remaining_units'):
            calculation.pop(key)
        calculation['additional_store_funding_units'] = funding.required_units
        terms = {'calculation': calculation, 'allocations': items, 'refund_posting_id': posting.pk}
        if addon_history:
            from .addon_main_segment_deduction import verified_deductions
            deductions = verified_deductions(order, before=addon_boundary)
            terms.update(addons=addon_parts, addon_totals=addon_totals,
                         addon_refund_ids=[item.pk for item in addon_refunds],
                         main_segment_deductions=[[key, value] for key, value in sorted(deductions.items())])
        if (verified.required != funding.required_units or verified.allocated != sum(row['amount'] for row in items)
                or funding.net_principal_units + funding.planned_provider_external_units + verified.allocated
                    != sum(person.income for person in plan.persons)
                       + sum(part['provider_income_units'] for part in addon_parts)
                       + plan.inviter_income + plan.store_income):
            raise SettlementError('转单退款后的补资与收益不守恒')
        return plan, terms
    from .points_transfer_funding import plan_points_transfer
    from rest_framework.exceptions import ValidationError
    try:
        calculated = plan_points_transfer(order.pk, allow_recorded=True)
        from .addon_settlement import combined_terms
        combined = combined_terms(order, calculated.funding)
        if combined:
            from .points_transfer_refund_access import permits as transfer_permits
            if not (transfer_permits(order, phase='settlement')
                    or getattr(settings, 'SERVICE_POINTS_TRANSFER_ADDON_SETTLEMENT_TESTING', False)):
                raise SettlementError('积分转单加购结算尚未开放')
            from .models import OrderAddonSettlement
            if OrderAddonSettlement.objects.filter(payment_id__in=combined.addon_payment_ids).exists():
                raise SettlementError('加购已有其他结算记录，请核对')
        verified = verify_order_funding(order.pk, allow_finalized=True, include_net_allocations=bool(combined))
        if isinstance(verified, VerifiedOrderFundingWithAllocations):
            net_allocations = verified.net_allocations
        else:
            # Keep the independent release calculation for legacy funding
            # branches without itemized proof, including orders without add-ons.
            allocations = verified_order_allocations_locked(order)
            from .points_transfer_revision import revision_tables_exist, verify_revisions_locked
            from .models import OrderPointsTransferFundingRevision
            released = (verify_revisions_locked(order)[0] if revision_tables_exist()
                        and OrderPointsTransferFundingRevision.objects.filter(order=order).exists() else {})
            addon_released = {}
            if combined:
                from .addon_funding_release import addon_release_totals
                from .points_addon_receipt_usage import reuse_history_locked
                with reuse_history_locked(order, {row.receipt_id for row in allocations}):
                    addon_released = addon_release_totals(allocations)
            net_allocations = tuple((row.pk, row.receipt_id,
                row.amount - released.get(row.pk, 0) - addon_released.get(row.pk, 0)) for row in allocations)
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError(str(exc)) from exc
    if not verified.fully_funded:
        return calculated.segments, None
    funding = calculated.funding
    uses = [{'id': allocation_id, 'receipt_id': receipt_id, 'amount': amount}
            for allocation_id, receipt_id, amount in net_allocations]
    if combined:
        from dataclasses import replace
        parts = [asdict(part) for part in combined.addon_parts]
        from .models import OrderAddonRefund, OrderAddonRefundEarningDeduction
        handover_refunds = OrderAddonRefund.objects.filter(order=order,
            payment__handovers__status='ACCEPTED').distinct().select_related('payment', 'proposal')
        refund_payments = {refund.payment_id: (refund, refund.payment) for refund in handover_refunds}
        cross_deductions = OrderAddonRefundEarningDeduction.objects.filter(refund__in=handover_refunds,
            payment__intent__order=order, payment__handovers__status='ACCEPTED').exclude(payment_id=F('refund__payment_id'))
        for deduction in cross_deductions.select_related('refund__order', 'payment__intent').order_by('pk').distinct():
            refund_payments.setdefault(deduction.payment_id, (deduction.refund, deduction.payment))
        for addon_refund, payment in refund_payments.values():
            from .points_addon_handover_refund_facts import settlement_parts
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                verified_parts = settlement_parts(addon_refund, payment=payment)
            except (DjangoValidationError, ValidationError) as exc:
                raise SettlementError('接替后加购退款逐段收益待核对') from exc
            expected = {(row['segment_id'], row['provider_account_id']): row['provider_income_units']
                        for row in verified_parts if row['provider_income_units']}
            actual = {(row['segment_id'], row['provider_account_id']): row['provider_income_units']
                      for row in parts if row['payment_id'] == payment.pk
                      and row['provider_income_units']}
            if expected != actual:
                raise SettlementError('接替后加购退款与结算分段不一致')
        eligible = {(segment_id, person.account_id) for person in calculated.segments.persons
                    for segment_id in person.segment_ids}
        if any((part['segment_id'], part['provider_account_id']) not in eligible for part in parts):
            raise SettlementError('加购收益不属于可结算的履约段')
        plan = replace(_deduct_addon_refunds(calculated.segments, combined),
            inviter_income=funding.inviter_income_units + combined.addon_inviter_income_units,
            store_income=combined.store_income_units)
        if (any(item['amount'] < 0 for item in uses) or verified.required != combined.additional_store_funding_units
                or verified.allocated != sum(item['amount'] for item in uses)
                or funding.principal_units + combined.addon_principal_units
                    + combined.addon_provider_recovery_units + verified.allocated
                    != sum(person.income for person in plan.persons) + sum(part['provider_income_units'] for part in parts)
                       + plan.inviter_income + plan.store_income):
            raise SettlementError('积分转单及加购补资与收益不守恒')
        calculation = asdict(funding)
        calculation.update(additional_store_funding_units=combined.additional_store_funding_units,
            addon_principal_units=combined.addon_principal_units,
            addon_provider_recovery_units=combined.addon_provider_recovery_units)
        from .models import OrderAddonRefund
        refund_ids = list(OrderAddonRefund.objects.filter(payment_id__in=combined.addon_payment_ids)
                          .order_by('pk').values_list('pk', flat=True))
        return plan, {'calculation': calculation, 'allocations': uses, 'addons': parts,
            'addon_totals': {'paid_units': combined.addon_paid_units, 'principal_units': combined.addon_principal_units,
                'gift_units': combined.addon_gift_units, 'compensation_units': combined.addon_compensation_units,
                'provider_units': combined.addon_provider_income_units, 'inviter_units': combined.addon_inviter_income_units,
                'recovery_units': combined.addon_provider_recovery_units},
            'addon_refund_ids': refund_ids,
            'main_deductions': [list(item) for item in combined.main_deductions]}
    terms = {'calculation': asdict(funding), 'allocations': uses}
    if (verified.required != funding.additional_store_funding_units
            or verified.allocated != sum(item['amount'] for item in uses)
            or funding.principal_units + verified.allocated != funding.provider_income_units
                + funding.inviter_income_units + funding.store_income_units):
        raise SettlementError('积分订单补资与收益不守恒')
    return calculated.segments, terms


def _ordinary_transfer_terms(order):
    """Reserve the whole ordinary transfer before releasing any participant."""
    from dataclasses import replace
    from .points_funding_evidence import verify_order_funding, verified_order_allocations_locked
    from .points_funding_plan import PointsFundingPlanError
    from .service_funding_plan import plan_verified_ordinary_order
    from rest_framework.exceptions import ValidationError

    try:
        funding = plan_verified_ordinary_order(order.pk, allow_finalized=bool(order.settled_at))
        from .addon_settlement import combined_terms
        combined = combined_terms(order, funding)
        if combined:
            from .models import OrderAddonSettlement
            if OrderAddonSettlement.objects.filter(payment_id__in=combined.addon_payment_ids).exists():
                raise SettlementError('加购已有其他结算记录，请核对')
        verified = verify_order_funding(order.pk, allow_finalized=bool(order.settled_at))
        allocations = verified_order_allocations_locked(order)
        from .addon_funding_release import addon_release_totals
        released = addon_release_totals(allocations)
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError('普通转单资金凭证待核对') from exc
    plan = build_segment_settlement_plan(order, allow_recorded=True)
    addon_parts = [asdict(part) for part in combined.addon_parts] if combined else []
    eligible_segments = {(segment_id, person.account_id) for person in plan.persons for segment_id in person.segment_ids}
    if any((part['segment_id'], part['provider_account_id']) not in eligible_segments for part in addon_parts):
        raise SettlementError('加购收益不属于可结算的履约段')
    inviter = funding.inviter_income_units + (combined.addon_inviter_income_units if combined else 0)
    store = combined.store_income_units if combined else funding.store_income_units
    principal = funding.principal_units + (combined.addon_principal_units if combined else 0)
    recovery = combined.addon_provider_recovery_units if combined else 0
    required = combined.additional_store_funding_units if combined else funding.additional_store_funding_units
    plan = replace(_deduct_addon_refunds(plan, combined), inviter_income=inviter, store_income=store)
    if not verified.fully_funded:
        return plan, None
    uses = [{'id': row.pk, 'receipt_id': row.receipt_id, 'amount': row.amount - released.get(row.pk, 0)}
            for row in allocations if row.amount > released.get(row.pk, 0)]
    if (verified.required != required
            or verified.allocated != sum(row['amount'] for row in uses)
            or principal + recovery + verified.allocated != sum(person.income for person in plan.persons)
                + sum(part['provider_income_units'] for part in addon_parts) + plan.inviter_income + plan.store_income):
        raise SettlementError('普通转单补资与收益不守恒')
    calculation = asdict(funding)
    calculation.update(principal_units=principal, additional_store_funding_units=required,
                       addon_provider_recovery_units=recovery)
    addon_totals = ({'paid_units': combined.addon_paid_units, 'principal_units': combined.addon_principal_units,
                     'gift_units': combined.addon_gift_units, 'compensation_units': combined.addon_compensation_units,
                     'provider_units': combined.addon_provider_income_units, 'inviter_units': combined.addon_inviter_income_units,
                     'recovery_units': combined.addon_provider_recovery_units}
                    if combined else {})
    from .models import OrderAddonRefund
    refund_ids = list(OrderAddonRefund.objects.filter(payment_id__in=combined.addon_payment_ids)
                      .order_by('pk').values_list('pk', flat=True)) if combined else []
    return plan, {'calculation': calculation, 'allocations': uses, 'addons': addon_parts,
                  'addon_totals': addon_totals, 'addon_refund_ids': refund_ids,
                  'main_deductions': [list(item) for item in combined.main_deductions] if combined else []}


def _hash(terms):
    return hashlib.sha256(json.dumps(terms, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _addon_contribution(terms):
    addon = terms.get('points_funding', {}).get('addon_totals', {})
    if not addon:
        history = terms.get('ordinary_transfer_funding', {}).get('addon_history', [])
        addon = {key: sum(row.get(key, 0) for row in history)
                 for key in ('principal_units', 'recovery_units', 'provider_units', 'inviter_units')}
    return max(0, addon.get('principal_units', 0) + addon.get('recovery_units', 0)
               - addon.get('provider_units', 0) - addon.get('inviter_units', 0))


def _report_versions(person):
    from .models import OrderSegmentReport
    rows = list(OrderSegmentReport.objects.filter(segment_id__in=person.segment_ids).select_related('segment').order_by('segment_id'))
    if len(rows) != len(person.segment_ids):
        raise SettlementError('本人报单版本缺失')
    result = []
    for row in rows:
        submission = row.submissions.filter(version=row.version, status='APPROVED').first()
        if (row.status != 'APPROVED' or not submission or not submission.submitted_at
                or not submission.reviewed_at or not submission.reviewer_id
                or submission.entry_image.name != row.segment.entry_image.name
                or submission.completion_image.name != row.segment.completion_image.name):
            raise SettlementError('本人已审核的报单版本缺失')
        evidence = [submission.entry_image.name, submission.completion_image.name,
                    *submission.result_images.order_by('pk').values_list('image', flat=True)]
        result.append({'segment_id':row.segment_id, 'version':row.version,
            'submitted_at':submission.submitted_at.isoformat(), 'reviewed_at':submission.reviewed_at.isoformat(),
            'reviewer_id':submission.reviewer_id, 'evidence_hash':_hash(evidence)})
    return result


def _valid_tx(tx, *, order, account_id, amount, kind):
    return bool(tx and tx.order_id == order.pk and tx.wallet.account_id == account_id
        and tx.tx_type == kind and tx.status == 'SUCCESS' and tx.amount == amount
        and tx.reversal_of_id is None and not tx.reversals.exists()
        and tx.balance_after == tx.balance_before + tx.amount)


def verified_person_credit(order, account_id):
    """Return (segmented order, amount) without exposing an old assignment amount."""
    if account_id:
        subsidy_manifest = OrderSegmentSettlementManifest.objects.filter(order=order, order__platform_subsidy__isnull=False).first()
        if subsidy_manifest:
            from .subsidy_segment_settlement import person_credit
            return True, person_credit(order, account_id)
    if not protection_enabled() or not account_id:
        return False, None
    manifest = OrderSegmentSettlementManifest.objects.filter(order=order).first()
    if not manifest:
        return False, None
    if manifest.plan_hash != _hash(manifest.plan) or not isinstance(manifest.plan, dict):
        return True, None
    row = OrderSegmentPersonSettlement.objects.filter(manifest=manifest, account_id=account_id).select_related(
        'income_transaction__wallet').first()
    expected = next((item for item in manifest.plan.get('people', []) if item.get('account_id') == account_id), None)
    if not row or not expected or row.amount != expected.get('income') or row.segment_ids != expected.get('segment_ids'):
        return True, None
    from types import SimpleNamespace
    try:
        if row.report_versions != _report_versions(SimpleNamespace(segment_ids=row.segment_ids)):
            return True, None
    except SettlementError:
        return True, None
    from wallet.models import Transaction
    if row.amount and not _valid_tx(row.income_transaction, order=order, account_id=account_id,
                                    amount=row.amount, kind=Transaction.TxType.INCOME):
        return True, None
    if not row.amount and row.income_transaction_id:
        return True, None
    return True, row.amount


def _validate_records(order, manifest, plan, *, finalized=False, snapshot_ids=None, subsidy_shares=None,
                      paid_addon_snapshot=None, paid_addon_cutoff=None):
    """Reject legacy, duplicate, altered or untraceable credits before more pay."""
    from wallet.models import Transaction
    rows = OrderSegmentPersonSettlement.objects.filter(manifest=manifest)
    if snapshot_ids is not None:
        # A subset is only evidence for historical credits. It must never be
        # mistaken for the complete settlement check used by the payout path.
        from .points_checkout_test import require_isolated_checkout
        from rest_framework.exceptions import ValidationError
        from .points_transfer_refund_access import permits
        formal_points = permits(order, phase='refund')
        if not formal_points:
            try:
                require_isolated_checkout()
            except ValidationError as exc:
                raise SettlementError('历史收益子集核对尚未开放') from exc
        if (not transaction.get_connection().in_atomic_block
                or (not formal_points and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING', False))
                or (formal_points and manifest.plan.get('points_funding', {}).get('addons')
                    and not paid_addon_snapshot)
                or (formal_points and ('points_funding' not in manifest.plan
                    or manifest.plan_hash != _hash(manifest.plan)))
                or manifest.order_id != order.pk
                or finalized or not isinstance(snapshot_ids, (list, tuple)) or not snapshot_ids
                or any(type(pk) is not int or pk <= 0 for pk in snapshot_ids)
                or len(set(snapshot_ids)) != len(snapshot_ids)):
            raise SettlementError('历史收益子集凭证无效')
        rows = rows.filter(pk__in=snapshot_ids)
    records = list(rows.select_related('income_transaction__wallet', 'deposit_debit__wallet', 'deposit_credit__wallet'))
    if snapshot_ids is not None and {row.pk for row in records} != set(snapshot_ids):
        raise SettlementError('历史收益记录缺失')
    people = {person.account_id:person for person in plan.persons}
    known = set()
    for row in records:
        person = people.get(row.account_id)
        if (not person or row.amount != person.income or row.segment_ids != list(person.segment_ids)
                or row.report_versions != _report_versions(person)):
            raise SettlementError('已结算人员的份额或报单版本发生变化')
        from django.utils.dateparse import parse_datetime
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        completed_at = order.completed_at
        if order.refund_records.exists() and 'points_funding' in manifest.plan:
            from .models import PointsNegotiationPosting
            posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
            if posting and isinstance(posting.participants, dict) and posting.termination_in_service and row.account_id in posting.proposal.affected_accounts:
                from datetime import timedelta
                selected = set(posting.proposal.affected_segment_ids or [])
                if not selected or set(row.segment_ids).issubset(selected):
                    completed_at = posting.accepted_at
                    deadline = posting.accepted_at + timedelta(hours=order.service_aftersale_hours)
        if 'ordinary_transfer_funding' in manifest.plan:
            from .models import NegotiationExecution
            ending = (NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
                shares__account_id=row.account_id,
                termination_processed_at__isnull=False).order_by('-accepted_at', '-pk').first())
            if ending and order.completed_at:
                from datetime import timedelta
                deadline = min(ending.accepted_at, order.completed_at) + timedelta(hours=order.service_aftersale_hours)
        reviewed = [parse_datetime(version['reviewed_at']) for version in row.report_versions]
        if (not row.settled_at or row.settled_at > timezone.now() or not completed_at
                or row.settled_at < completed_at or not deadline or row.settled_at < deadline
                or not person.freeze_until or row.settled_at < person.freeze_until
                or any(not value or value > row.settled_at for value in reviewed)):
            raise SettlementError('已结算人员的售后、报单或冻结时间异常')
        if row.amount:
            if not _valid_tx(row.income_transaction, order=order, account_id=row.account_id,
                             amount=row.amount, kind=Transaction.TxType.INCOME):
                raise SettlementError('已结算人员的收益流水异常')
            from wallet.refund_deposit import protection_enabled
            if protection_enabled():
                from wallet.models import RefundDepositDeficit, RefundDepositIncomeApplication
                from wallet.refund_deposit import _verify_application
                application = RefundDepositIncomeApplication.objects.filter(
                    source_transaction_id=row.income_transaction_id).select_related('profile').first()
                application_source = row.income_transaction
                if not application:
                    from wallet.models import IncomeHold
                    hold = IncomeHold.objects.select_related('release_transaction').filter(
                        income_transaction_id=row.income_transaction_id, released_at__isnull=False,
                        cancelled_at__isnull=True).first()
                    if hold and hold.release_transaction_id:
                        release = hold.release_transaction
                        if (hold.wallet_id != row.income_transaction.wallet_id or release.wallet_id != hold.wallet_id
                                or release.order_id != order.pk or release.tx_type != Transaction.TxType.EARNINGS_RELEASE
                                or release.status != Transaction.Status.SUCCESS
                                or release.amount != hold.amount - hold.recovered_amount
                                or release.balance_after != release.balance_before + release.amount
                                or release.reversal_of_id or release.reversals.exists()):
                            raise SettlementError('收益冻结释放流水异常')
                        application_source = release
                        application = RefundDepositIncomeApplication.objects.filter(
                            source_transaction=release).select_related('profile').first()
                if application:
                    if (application.profile.account_id != row.account_id
                            or application.profile.user_id != row.income_transaction.wallet.user_id):
                        raise SettlementError('收益补押金账户不一致')
                    try:
                        _verify_application(application, source=application_source, profile=application.profile)
                    except ValueError as exc:
                        raise SettlementError('收益补押金流水异常') from exc
                elif RefundDepositDeficit.objects.filter(account_id=row.account_id,
                        created_at__lte=row.income_transaction.created_at).exists():
                    from wallet.income_priority_evidence import pending_repayment_hold
                    if not pending_repayment_hold(row.income_transaction):
                        raise SettlementError('收益缺少退款押金补差记录')
            known.add(row.income_transaction_id)
        elif row.income_transaction_id:
            raise SettlementError('零收益人员存在异常收益流水')
        from wallet.refund_recovery_order import tracking_enabled
        if tracking_enabled():
            from wallet.models import RefundDepositRecoveryPermit
            permit = RefundDepositRecoveryPermit.objects.filter(zero_income_settlement=row).first()
            if permit and (row.amount != 0 or permit.account_id != row.account_id or permit.order_id != order.pk
                    or permit.profile.account_id != row.account_id or permit.close_reason != 'ZERO_INCOME_SETTLED'
                    or not permit.closed_at or permit.income_application_id):
                raise SettlementError('零收益补差机会凭证异常')
        if bool(row.deposit_debit_id) != bool(row.deposit_credit_id):
            raise SettlementError('押金流水缺少配对记录')
        if row.deposit_debit_id:
            from wallet.models import get_platform_wallet
            debit, credit = row.deposit_debit, row.deposit_credit
            if (debit.amount >= 0 or -debit.amount > row.amount or not _valid_tx(debit, order=order, account_id=row.account_id,
                    amount=debit.amount, kind=Transaction.TxType.DEPOSIT)
                    or credit.wallet_id != get_platform_wallet(for_update=True).pk
                    or credit.order_id != order.pk or credit.tx_type != Transaction.TxType.DEPOSIT_INCOME
                    or credit.amount != -debit.amount or credit.status != 'SUCCESS' or credit.reversal_of_id
                    or credit.reversals.exists() or credit.balance_after != credit.balance_before + credit.amount):
                raise SettlementError('已结算人员的押金配对流水异常')
    if snapshot_ids is None and (manifest.plan.get('points_funding', {}).get('addons')
                                 or manifest.plan.get('ordinary_transfer_funding', {}).get('addons')):
        from .segment_addon_settlement import validate_records
        validate_records(order, manifest, {row.account_id: row for row in records}, known)
    elif (snapshot_ids is None and manifest.plan.get('points_funding', {}).get('addon_refund_ids')
          and manifest.addon_settlements.exists()):
        raise SettlementError('全额退款加购存在多余逐段结算记录')
    if snapshot_ids is not None:
        if paid_addon_snapshot:
            from .points_transfer_paid_addons import verify
            from rest_framework.exceptions import ValidationError
            try:
                verify(order, paid_addon_snapshot, cutoff=paid_addon_cutoff)
            except ValidationError as exc:
                raise SettlementError('已付加购收益凭证异常') from exc
        return {row.account_id: row for row in records}
    if finalized:
        inviter = manifest.inviter_transaction
        if plan.inviter_income:
            if (not inviter or inviter.order_id != order.pk or inviter.tx_type != Transaction.TxType.INCOME
                    or inviter.status != 'SUCCESS' or inviter.amount != plan.inviter_income or inviter.reversal_of_id
                    or inviter.reversals.exists() or inviter.balance_after != inviter.balance_before + inviter.amount
                    or (order.inviter_account_id and inviter.wallet.account_id != order.inviter_account_id)
                    or (order.inviter_id and inviter.wallet.user_id != order.inviter_id)):
                raise SettlementError('已结算推荐佣金流水异常')
            known.add(inviter.pk)
            if inviter.wallet.account_id:
                from club_accounts.models import ClubAccount
                from engagement.models import PointFundingLot
                if ClubAccount.objects.filter(pk=inviter.wallet.account_id, account_type='BOSS').exists():
                    source_key = (f'points-segment-referral:{manifest.pk}'
                        if 'points_funding' in manifest.plan or 'ordinary_transfer_funding' in manifest.plan
                        else f'referral-income:{inviter.pk}')
                    lot = PointFundingLot.objects.filter(source_key=source_key).first()
                    if (not lot or lot.account_id != inviter.wallet.account_id or lot.kind != PointFundingLot.Kind.PREPAID
                            or lot.remaining > inviter.amount):
                        raise SettlementError('老板推荐佣金本金来源待核对')
        elif inviter:
            raise SettlementError('零推荐佣金存在异常流水')
        from wallet.models import get_platform_wallet
        store_tx = manifest.store_transaction
        rounding = manifest.plan.get('ordinary_transfer_rounding', 0)
        followup_rounding = manifest.plan.get('ordinary_followup_rounding', [])
        addon_rounding = manifest.plan.get('ordinary_addon_rounding', []) + manifest.plan.get('points_addon_rounding', [])
        points_rounding = manifest.plan.get('points_refund_rounding', [])
        if (type(rounding) is not int or not isinstance(followup_rounding, list)
                or not isinstance(addon_rounding, list) or not isinstance(points_rounding, list)
                or any(type(item.get('amount')) is not int or item['amount'] < 0 for item in addon_rounding)
                or any(type(item.get('amount')) is not int or item['amount'] < 0 for item in followup_rounding)
                or any(type(item.get('amount')) is not int or item['amount'] < 0 for item in points_rounding)):
            raise SettlementError('退款取整差额待核对')
        all_rounding = (rounding + sum(item['amount'] for item in followup_rounding)
                        + sum(item['amount'] for item in addon_rounding) + sum(item['amount'] for item in points_rounding))
        if not 0 <= all_rounding <= plan.store_income:
            raise SettlementError('退款取整差额待核对')
        store_amount = plan.store_income - all_rounding
        if store_amount:
            if (not store_tx or store_tx.order_id != order.pk or store_tx.wallet_id != get_platform_wallet(for_update=True).pk
                    or store_tx.tx_type != Transaction.TxType.SHOP_INCOME or store_tx.status != 'SUCCESS'
                    or store_tx.amount != store_amount or store_tx.reversal_of_id or store_tx.reversals.exists()
                    or store_tx.balance_after != store_tx.balance_before + store_tx.amount):
                raise SettlementError('已结算门店收入流水异常')
        elif store_tx:
            raise SettlementError('零门店收入存在异常流水')
        rounding_tx = manifest.rounding_transaction
        if rounding:
            if (not rounding_tx or rounding_tx.order_id != order.pk
                    or rounding_tx.wallet_id != get_platform_wallet(for_update=True).pk
                    or rounding_tx.tx_type != Transaction.TxType.SHOP_INCOME or rounding_tx.status != 'SUCCESS'
                    or rounding_tx.amount != rounding or rounding_tx.reversal_of_id or rounding_tx.reversals.exists()
                    or rounding_tx.balance_after != rounding_tx.balance_before + rounding_tx.amount):
                raise SettlementError('已结算退款取整流水异常')
        elif rounding_tx:
            raise SettlementError('零取整差额存在异常流水')
        from .models import PointsNegotiationPosting
        for item in points_rounding:
            posting = PointsNegotiationPosting.objects.select_related('rounding_transaction').get(
                pk=item['posting_id'], points_refund__redemption__order=order)
            tx = posting.rounding_transaction
            if posting.rounding_to_store_units != item['amount']:
                raise SettlementError('积分退款取整金额异常')
            if item['amount']:
                if (not tx or tx.order_id != order.pk or tx.wallet_id != get_platform_wallet(for_update=True).pk
                        or tx.tx_type != Transaction.TxType.SHOP_INCOME or tx.status != 'SUCCESS'
                        or tx.amount != item['amount'] or tx.reversal_of_id or tx.reversals.exists()
                        or tx.balance_after != tx.balance_before + tx.amount):
                    raise SettlementError('积分退款取整流水异常')
            elif tx:
                raise SettlementError('零积分退款取整差额存在异常流水')
        from .models import OrderAddonRefund
        for item in addon_rounding:
            refund = OrderAddonRefund.objects.select_related('rounding_transaction').get(pk=item['refund_id'], order=order)
            tx = refund.rounding_transaction
            if item['amount'] != refund.rounding_surplus:
                raise SettlementError('加购退款取整金额异常')
            if item['amount']:
                if (not tx or tx.order_id != order.pk or tx.wallet_id != get_platform_wallet(for_update=True).pk
                        or tx.tx_type != Transaction.TxType.SHOP_INCOME or tx.status != Transaction.Status.SUCCESS
                        or tx.amount != item['amount'] or tx.reversal_of_id or tx.reversals.exists()
                        or tx.balance_after != tx.balance_before + tx.amount):
                    raise SettlementError('加购退款取整流水异常')
            elif tx:
                raise SettlementError('零加购取整差额存在异常流水')
        from .models import OrdinaryTransferFollowupSettlementRevision
        for item in followup_rounding:
            revision = OrdinaryTransferFollowupSettlementRevision.objects.select_related('rounding_transaction').get(
                round_id=item['round_id'], round__order=order)
            tx = revision.rounding_transaction
            if item['amount']:
                if (not tx or tx.order_id != order.pk or tx.wallet_id != get_platform_wallet(for_update=True).pk
                        or tx.tx_type != Transaction.TxType.SHOP_INCOME or tx.status != Transaction.Status.SUCCESS
                        or tx.amount != item['amount'] or tx.reversal_of_id or tx.reversals.exists()
                        or tx.balance_after != tx.balance_before + tx.amount):
                    raise SettlementError('追加退款取整流水异常')
            elif tx:
                raise SettlementError('零取整差额存在异常流水')
    existing = set(order.transactions.filter(tx_type=Transaction.TxType.INCOME, status='SUCCESS',
        reversal_of__isnull=True).values_list('pk', flat=True))
    settled_assignments = set(order.providers.filter(settled_at__isnull=False).values_list('pk', flat=True))
    permitted_assignments = ({share.assignment_id for share in subsidy_shares if share.paid_at}
                             if subsidy_shares is not None else set())
    if existing != known or settled_assignments != permitted_assignments:
        raise SettlementError('订单存在未归属的历史收益或结算记录')
    shop_ids = set(order.transactions.filter(tx_type=Transaction.TxType.SHOP_INCOME, status='SUCCESS',
                                              reversal_of__isnull=True).values_list('pk', flat=True))
    expected_shop = ({pk for pk in (manifest.store_transaction_id, manifest.rounding_transaction_id) if pk}
                     if finalized else set())
    if finalized:
        from .models import OrdinaryTransferFollowupSettlementRevision
        expected_shop.update(OrdinaryTransferFollowupSettlementRevision.objects.filter(
            round__order=order, rounding_transaction__isnull=False).values_list('rounding_transaction_id', flat=True))
        addon_ids = [item['refund_id'] for item in manifest.plan.get('ordinary_addon_rounding', [])]
        expected_shop.update(order.addon_refunds.filter(pk__in=addon_ids, rounding_transaction__isnull=False)
                             .values_list('rounding_transaction_id', flat=True))
        from .models import PointsNegotiationPosting
        points_ids = [item['posting_id'] for item in manifest.plan.get('points_refund_rounding', [])]
        expected_shop.update(PointsNegotiationPosting.objects.filter(pk__in=points_ids,
            points_refund__redemption__order=order, rounding_transaction__isnull=False)
            .values_list('rounding_transaction_id', flat=True))
    if shop_ids != expected_shop:
        raise SettlementError('订单已有无法归属的门店收入流水')
    return {row.account_id:row for row in records}


def verify_finalized(order, manifest):
    """Reconcile finalized credits before confirming customer consumption."""
    if isinstance(manifest.plan, dict) and manifest.plan.get('kind') == 'PLATFORM_SUBSIDY':
        from .subsidy_segment_settlement import verify_locked
        return verify_locked(order, manifest, finalized=True)[0]
    if not order.settled_at or not manifest.finalized_at or order.settled_at != manifest.finalized_at:
        raise SettlementError('逐段结算完成时间不一致')
    from .segment_addon_settlement_revision import verify_revisions
    verify_revisions(manifest)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        from .points_checkout_test import settlement_enabled
        from .points_transfer_refund_access import permits
        formal = permits(order, phase='settlement') or permits(order, phase='refund')
        if not formal and (not getattr(settings, 'SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING', False) or not settlement_enabled()):
            raise SettlementError('积分逐段结算尚未开放')
        plan, points_funding = _points_transfer_terms(order)
        if points_funding is None:
            raise SettlementError('积分逐段结算补资不足')
        terms = _terms(order, plan, points_funding=points_funding)
    elif _ordinary_transfer_refund(order):
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        if not (ordinary_transfer_permits(order) or getattr(settings, 'ORDINARY_TRANSFER_REFUND_SETTLEMENT_INTERNAL_TESTING', False)):
            raise SettlementError('普通转单退款收益尚未开放')
        if _post_refund_transfer_version(order):
            from .ordinary_post_refund_settlement import build_locked
        elif not order.refund_records.exists() and not order.ordinary_transfer_followups.filter(execution__isnull=False).exists():
            from .ordinary_transfer_first_zero_settlement import build_locked
        elif order.ordinary_transfer_followups.filter(execution__isnull=False).exists():
            from .ordinary_transfer_followup_settlement import build_locked
        else:
            from .negotiation_transfer_settlement import build_locked
        plan, funding = build_locked(order)
        terms = _terms(order, plan, ordinary_transfer_funding=(funding if ordinary_transfer_permits(order) else None))
    elif getattr(settings, 'SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING', False):
        plan, funding = _ordinary_transfer_terms(order)
        if funding is None:
            raise SettlementError('普通转单补资不足')
        terms = _terms(order, plan, points_funding=funding)
    else:
        plan = build_segment_settlement_plan(order, allow_recorded=True)
        terms = _terms(order, plan)
    if (manifest.plan != terms or manifest.plan_hash != _hash(terms)
            or order.provider_income != sum(p.income for p in plan.persons)
            or order.inviter_commission != plan.inviter_income or order.shop_income != plan.store_income):
        raise SettlementError('逐段结算汇总与原快照不一致')
    if 'round_posting_ids' in terms.get('points_funding', {}):
        from .points_round_settlement_plan import verify_manifest_locked
        verify_manifest_locked(order, manifest, terms, finalized=True)
    elif 'third_posting_id' in terms.get('points_funding', {}):
        from .points_third_refund_settlement_plan import verify_manifest_locked
        verify_manifest_locked(order, manifest, terms, finalized=True)
    elif 'followup_posting_id' in terms.get('points_funding', {}):
        from .models import OrderPointsTransferFollowupFundingRevision
        from .points_transfer_followup_settlement import verify_manifest_locked
        evidence = OrderPointsTransferFollowupFundingRevision.objects.get(
            posting_id=terms['points_funding']['followup_posting_id'])
        verify_manifest_locked(order, manifest, terms, evidence.prior_manifest)
    expected_funding = terms.get('ordinary_transfer_funding', {}).get('required_units',
        terms.get('points_funding', {}).get('calculation', {}).get('additional_store_funding_units', 0))
    if order.settlement_store_funding != expected_funding:
        raise SettlementError('逐段结算的门店补资汇总不一致')
    if order.settlement_addon_contribution != _addon_contribution(terms):
        raise SettlementError('逐段结算的加购资金贡献不一致')
    records = _validate_records(order, manifest, plan, finalized=True)
    if len(records) != len(plan.persons):
        raise SettlementError('逐段结算人员记录不完整')
    if not plan.persons or any(p.freeze_until is None for p in plan.persons):
        raise SettlementError('逐段报单冻结时间待核对')
    if order.consumption_ready_at != max(p.freeze_until for p in plan.persons):
        raise SettlementError('逐段实销确认时间不一致')
    return plan


def _finalize(order, manifest, plan, *, trace_id):
    from wallet.models import Transaction
    from wallet.notifier import notify_wallet_update
    from .models import OrderSegmentReportSubmission
    from .services import _credit_wallet
    records = _validate_records(order, manifest, plan)
    if (len(records) != len(plan.persons) or any(person.blockers for person in plan.persons)
            or order.aftersales.filter(status='OPEN').exists()):
        return False
    submissions = list(OrderSegmentReportSubmission.objects.filter(
        report__segment_id__in=[segment for person in plan.persons for segment in person.segment_ids],
        status='APPROVED', version__in=[version['version'] for row in records.values() for version in row.report_versions]))
    # One exact approved version per segment, with each person's immutable
    # receipt checked above; do not infer the clock from legacy reports.
    expected = {(v['segment_id'], v['version']) for row in records.values() for v in row.report_versions}
    actual = {(s.report.segment_id, s.version) for s in submissions}
    if actual != expected or not submissions or any(p.freeze_until is None for p in plan.persons):
        raise SettlementError('逐段报单时间或版本待核对')
    inviter_tx = None
    if plan.inviter_income:
        inviter_tx = _credit_wallet(order=order, amount=plan.inviter_income, tx_type=Transaction.TxType.INCOME,
            remark=f'推荐分佣：{order.order_no}', event='order.settle.inviter', trace_id=trace_id,
            user_id=order.inviter_id, account_id=order.inviter_account_id,
            log_after_commit='points_funding' in manifest.plan)
        transaction.on_commit(lambda: notify_wallet_update(inviter_tx.wallet))
        if ('points_funding' in manifest.plan or 'ordinary_transfer_funding' in manifest.plan) and inviter_tx.wallet.account_id:
            from club_accounts.models import ClubAccount
            if ClubAccount.objects.filter(pk=inviter_tx.wallet.account_id, account_type='BOSS').exists():
                from engagement.models import PointFundingLot
                PointFundingLot.objects.create(account_id=inviter_tx.wallet.account_id,
                    source_key=f'points-segment-referral:{manifest.pk}',
                    kind=PointFundingLot.Kind.PREPAID, remaining=inviter_tx.amount)
        else:
            from .referral_evidence import record_referral_principal
            record_referral_principal(inviter_tx)
    rounding = manifest.plan.get('ordinary_transfer_rounding', 0)
    followup_rounding = manifest.plan.get('ordinary_followup_rounding', [])
    addon_rounding = manifest.plan.get('ordinary_addon_rounding', []) + manifest.plan.get('points_addon_rounding', [])
    points_rounding = manifest.plan.get('points_refund_rounding', [])
    if (type(rounding) is not int or not isinstance(followup_rounding, list)
            or not isinstance(addon_rounding, list) or not isinstance(points_rounding, list)):
        raise SettlementError('退款取整差额资金待核对')
    all_rounding = (rounding + sum(item['amount'] for item in followup_rounding)
                    + sum(item['amount'] for item in addon_rounding) + sum(item['amount'] for item in points_rounding))
    if not 0 <= all_rounding <= plan.store_income:
        raise SettlementError('退款取整差额资金待核对')
    store_tx = _credit_wallet(order=order, amount=plan.store_income - all_rounding, tx_type=Transaction.TxType.SHOP_INCOME,
        remark=f'平台收入：{order.order_no}', event='order.settle.platform', trace_id=trace_id, platform=True,
        log_after_commit='points_funding' in manifest.plan)
    rounding_tx = _credit_wallet(order=order, amount=rounding, tx_type=Transaction.TxType.SHOP_INCOME,
        remark=f'退款取整差额：{order.order_no}', event='order.settle.rounding', trace_id=trace_id, platform=True,
        log_after_commit='ordinary_transfer_funding' in manifest.plan)
    if points_rounding:
        from .models import PointsNegotiationPosting
        for item in points_rounding:
            posting = PointsNegotiationPosting.objects.select_for_update().get(
                pk=item['posting_id'], points_refund__redemption__order=order)
            if posting.rounding_transaction_id or posting.rounding_to_store_units != item['amount']:
                raise SettlementError('积分退款取整流水或金额异常')
            if item['amount']:
                posting.rounding_transaction = _credit_wallet(order=order, amount=item['amount'],
                    tx_type=Transaction.TxType.SHOP_INCOME, remark=f'积分退款取整差额：{order.order_no}',
                    event='order.settle.rounding', trace_id=trace_id, platform=True, log_after_commit=True)
                posting.save(update_fields=['rounding_transaction'])
    if addon_rounding:
        from .models import OrderAddonRefund
        for item in addon_rounding:
            refund = OrderAddonRefund.objects.select_for_update().get(pk=item['refund_id'], order=order)
            if refund.rounding_transaction_id or refund.rounding_surplus != item['amount']:
                raise SettlementError('加购退款取整流水或金额异常')
            refund.rounding_transaction = _credit_wallet(order=order, amount=item['amount'],
                tx_type=Transaction.TxType.SHOP_INCOME, remark=f'加购退款取整差额：{order.order_no}',
                event='order.settle.rounding', trace_id=trace_id, platform=True, log_after_commit=True)
            refund.save(update_fields=['rounding_transaction'])
    if followup_rounding:
        from .models import OrdinaryTransferFollowupSettlementRevision
        for item in followup_rounding:
            revision = OrdinaryTransferFollowupSettlementRevision.objects.select_for_update().get(
                round_id=item['round_id'], round__order=order)
            if revision.rounding_transaction_id:
                raise SettlementError('追加退款取整流水已存在')
            revision.rounding_transaction = _credit_wallet(order=order, amount=item['amount'],
                tx_type=Transaction.TxType.SHOP_INCOME, remark=f'退款取整差额：{order.order_no}',
                event='order.settle.rounding', trace_id=trace_id, platform=True, log_after_commit=True)
            revision.save(update_fields=['rounding_transaction'])
    now = timezone.now()
    manifest.inviter_transaction = inviter_tx; manifest.store_transaction = store_tx
    manifest.rounding_transaction = rounding_tx; manifest.finalized_at = now
    manifest.save(update_fields=['inviter_transaction', 'store_transaction', 'rounding_transaction', 'finalized_at'])
    order.provider_income = sum(p.income for p in plan.persons)
    order.inviter_commission = plan.inviter_income; order.shop_income = plan.store_income
    order.settlement_store_funding = manifest.plan.get('ordinary_transfer_funding', {}).get('required_units',
        manifest.plan.get('points_funding', {}).get('calculation', {}).get('additional_store_funding_units', 0))
    order.settlement_addon_contribution = _addon_contribution(manifest.plan)
    order.report_ready_at = max(s.submitted_at for s in submissions)
    order.consumption_ready_at = max(p.freeze_until for p in plan.persons)
    order.settled_at = now
    order.save(update_fields=['provider_income', 'inviter_commission', 'shop_income', 'settlement_store_funding', 'settlement_addon_contribution', 'report_ready_at',
                              'consumption_ready_at', 'settled_at'])
    return True


def settle_due_segment_people(order, *, operator=None, operator_account=None, trace_id=None):
    """Enter the independently gated MySQL phase before account locks."""
    from .points_service_payment import needs_integrated_processing
    from .models import OrderOriginalShareTransfer
    if (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).exists()):
        from .points_transfer_refund_access import after_transfer
        with after_transfer(order.pk, phase='settlement') as locked:
            # Completion can be staged in memory by the caller while the
            # already locked database row still says IN_SERVICE.
            if (locked.status != order.status and not (locked.status == locked.Status.IN_SERVICE
                    and order.status == order.Status.COMPLETED)):
                raise SettlementError('订单履约状态已变化，请重新核对')
            return _settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    return _settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)


def _settle_due_segment_people(order, *, operator=None, operator_account=None, trace_id=None):
    """Caller locks order first. Each invocation posts only currently due people."""
    from .addon_payment import guard_main_settlement
    from .segment_addon_settlement import enabled as addon_segment_enabled
    from .addon_settlement import has_paid_addons
    from .points_service_payment import needs_integrated_processing
    points_order = needs_integrated_processing(order)
    supported_addon = (addon_segment_enabled() and has_paid_addons(order) and not order.refund_records.exists()
        and (getattr(settings, 'SERVICE_POINTS_TRANSFER_ADDON_SETTLEMENT_TESTING', False) if points_order
             else getattr(settings, 'SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING', False)))
    if points_order and has_paid_addons(order):
        from .points_transfer_refund_access import permits as points_transfer_permits
        supported_addon = supported_addon or points_transfer_permits(order, phase='settlement')
    if not points_order and has_paid_addons(order) and not order.refund_records.exists():
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        supported_addon = supported_addon or ordinary_transfer_permits(order)
    if not points_order and _ordinary_transfer_refund(order):
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        supported_addon = supported_addon or (has_paid_addons(order) and ordinary_transfer_permits(order))
    if not supported_addon:
        guard_main_settlement(order)
    if points_order:
        from .points_checkout_test import settlement_enabled
        from .points_transfer_capture import enabled as points_transfer_enabled
        from .points_transfer_refund_access import permits
        formal = permits(order, phase='settlement')
        if not formal and (not getattr(settings, 'SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING', False)
                or not settlement_enabled() or not points_transfer_enabled()):
            raise SettlementError('积分订单收益结算尚未开放，请联系客服核对')
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('逐段结算必须在订单事务内执行')
    if not enabled(order) or order.settled_at:
        return []
    if points_order and order.refund_records.exists() and order.status != order.Status.COMPLETED:
        return []
    if (order.settlement_mode != order.SettlementMode.REPORT_APPROVAL
            and not (order.settlement_mode == order.SettlementMode.COMPLETION
                     and completion_transfer_order_eligible(order))):
        raise SettlementError('逐段结算要求报单审核模式')
    if not order.customer_account_id or type(order.service_deposit_percent) is not int or not 0 <= order.service_deposit_percent <= 100:
        raise SettlementError('老板账户或押金比例快照待核对')
    from .financial_lock import lock_order_money
    from .services import _credit_wallet, refresh_escort_status
    from wallet.models import Transaction, get_platform_wallet
    from wallet.services import get_wallet
    from wallet.income import hold_provider_income
    from club_accounts.models import LegacyAccountMap
    from users.models import EscortProfile
    from common.logging_utils import new_trace_id
    lock_order_money()
    customer = get_wallet(account_id=order.customer_account_id, user_id=order.customer_id, for_update=True)
    payments = list(order.transactions.filter(tx_type=Transaction.TxType.PAY, status='SUCCESS',
        reversal_of__isnull=True).order_by('pk'))
    if (len(payments) != 1 or payments[0].wallet_id != customer.pk or payments[0].amount != -order.amount
            or payments[0].reversals.exists() or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise SettlementError('订单付款流水不完整，请先核对')
    points_funding = None
    if points_order:
        plan, points_funding = _points_transfer_terms(order)
        if points_funding is None:
            return []
    elif _ordinary_transfer_refund(order):
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        if not (ordinary_transfer_permits(order) or getattr(settings, 'ORDINARY_TRANSFER_REFUND_SETTLEMENT_INTERNAL_TESTING', False)):
            raise SettlementError('普通转单退款收益尚未开放')
        if _post_refund_transfer_version(order):
            from .ordinary_post_refund_settlement import build_locked
        elif not order.refund_records.exists() and not order.ordinary_transfer_followups.filter(execution__isnull=False).exists():
            from .ordinary_transfer_first_zero_settlement import build_locked
        elif order.ordinary_transfer_followups.filter(execution__isnull=False).exists():
            from .ordinary_transfer_followup_settlement import build_locked
        else:
            from .negotiation_transfer_settlement import build_locked
        plan, ordinary_funding = build_locked(order)
    elif (getattr(settings, 'SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING', False)
          or (not points_order and supported_addon)):
        plan, points_funding = _ordinary_transfer_terms(order)
        if points_funding is None:
            return []
    else:
        if order.funding_allocations.filter(kind__in=['GIFT', 'COMPENSATION'], amount__gt=0).exists():
            raise SettlementError('赠送或赔付支付的转单须先核对门店补资')
        plan = build_segment_settlement_plan(order, allow_recorded=True)
    if not points_order:
        if _ordinary_transfer_refund(order):
            source = (ordinary_funding['net_principal_units'] + ordinary_funding['planned_provider_external_units']
                      + ordinary_funding['allocated_units'] - ordinary_funding['release_units'])
        elif points_funding is not None:
            source = points_funding['calculation']['principal_units'] + points_funding['calculation'].get(
                'addon_provider_recovery_units', 0) + sum(item['amount'] for item in points_funding['allocations'])
        else:
            source = plan.deal_amount
        addon_source = ordinary_funding if _ordinary_transfer_refund(order) else (points_funding or {})
        addon_income = sum(item['provider_income_units'] for item in addon_source.get('addons', []))
        if sum(p.income for p in plan.persons) + addon_income + plan.inviter_income + plan.store_income != source:
            raise SettlementError('逐段收益与资金来源不守恒')
    terms = _terms(order, plan, points_funding=points_funding,
        ordinary_transfer_funding=(ordinary_funding if _ordinary_transfer_refund(order)
                                   and ordinary_transfer_permits(order) else None))
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if manifest:
        from .segment_addon_settlement_revision import verify_revisions
        verify_revisions(manifest)
        if manifest.finalized_at:
            raise SettlementError('已有逐段结算快照与当前订单不一致')
        if manifest.plan != terms or manifest.plan_hash != _hash(terms):
            if 'round_posting_ids' in terms.get('points_funding', {}):
                from .points_round_paid_settlement import transition_locked
                records = transition_locked(order, manifest, plan, terms)
            elif 'third_posting_id' in terms.get('points_funding', {}):
                raise SettlementError('第三轮退款前已有结算清单，须先核对原收益')
            else:
                from .models import PointsZeroFollowupTermination
                zero = PointsZeroFollowupTermination.objects.filter(order=order, terminated_at__isnull=False).first()
                from .models import PointsVoluntaryPaidPaymentEvidence
                old_addons = manifest.plan.get('points_funding', {}).get('addons', [])
                new_addons = terms.get('points_funding', {}).get('addons', [])
                old_payments = {row.get('payment_id') for row in old_addons if isinstance(row, dict)} if isinstance(old_addons, list) else set()
                new_payments = {row.get('payment_id') for row in new_addons if isinstance(row, dict)} if isinstance(new_addons, list) else set()
                added_payments = new_payments - old_payments
                payment_revision = bool(points_order and added_payments and
                    PointsVoluntaryPaidPaymentEvidence.objects.filter(manifest=manifest,
                        payment_id__in=added_payments).count() == len(added_payments))
                if points_order and not payment_revision:
                    from .points_voluntary_paid_evidence import pending_payment_evidence
                    payment_revision = bool(pending_payment_evidence(manifest))
                if payment_revision:
                    from .points_voluntary_paid_evidence import transition_payment_locked
                    records = transition_payment_locked(order, manifest, plan, terms)
                elif (points_order and order.status == Order.Status.COMPLETED and order.completed_at
                        and manifest.plan.get('completed_at') is None
                        and manifest.paid_handover_revisions.exists()):
                    from .points_followup_paid_handover import transition_completion_locked
                    records = transition_completion_locked(order, manifest, plan, terms)
                elif points_order and manifest.paid_handover_events.filter(settlement_revision__isnull=True).exists():
                    # A real accepted handover advances its manifest in the same
                    # transaction. Missing revisions are incomplete evidence.
                    raise SettlementError('已付接替缺少事务内清单版本')
                elif (points_order and zero and isinstance(zero.funding_snapshot, dict)
                        and zero.funding_snapshot.get('kind') == 'TRANSFER_ZERO_V1'
                        and zero.funding_snapshot.get('paid_snapshot')):
                    from .models import PointsTransferZeroSettlementRevision
                    if PointsTransferZeroSettlementRevision.objects.filter(termination=zero, manifest=manifest).exists():
                        from .points_transfer_zero_addon_settlement import transition_locked as transition_addon
                        records = transition_addon(order, manifest, plan, terms, zero)
                    else:
                        from .points_transfer_zero_settlement import transition_locked
                        records = transition_locked(order, manifest, plan, terms)
                elif terms.get('ordinary_transfer_funding', {}).get('post_refund_revision_id'):
                    from .ordinary_post_refund_transfer import transition_settlement_locked
                    try:
                        records = transition_settlement_locked(order, manifest, plan, terms)
                    except ValidationError as exc:
                        raise SettlementError('后续转单结算版本待核对') from exc
                elif (terms.get('ordinary_transfer_funding', {}).get('followup_round_id')
                        and _ordinary_transfer_refund(order)):
                    from .ordinary_transfer_followup_revision import transition_locked
                    records = transition_locked(order, manifest, plan, terms)
                elif _ordinary_transfer_refund(order):
                    from .models import OrderTransferNegotiationEvidence
                    first = (OrderTransferNegotiationEvidence.objects.select_for_update()
                             .select_related('execution__proposal').filter(order=order).first())
                    if not first or not first.execution.proposal.ordinary_paid_settlement_snapshot:
                        raise SettlementError('首轮退款的已结算清单凭证缺失')
                    from .ordinary_first_paid_settlement import transition_locked
                    try:
                        records = transition_locked(order, manifest, plan, terms, first.execution)
                    except ValidationError as exc:
                        raise SettlementError('首轮退款的已结算清单待核对') from exc
                elif not order.refund_records.exists() and terms.get('points_funding', {}).get('addons') is not None:
                    from .segment_addon_settlement_revision import transition_locked
                    records = transition_locked(order, manifest, plan, terms)
                elif not points_order or not order.refund_records.exists():
                    raise SettlementError('已有逐段结算快照与当前订单不一致')
                elif 'followup_posting_id' in terms.get('points_funding', {}):
                    from .models import PointsVoluntaryPaidRefundEvidence
                    old_ids = manifest.plan.get('points_funding', {}).get('addon_refund_ids', [])
                    new_ids = terms.get('points_funding', {}).get('addon_refund_ids', [])
                    paid_refunds = (set(new_ids) - set(old_ids)
                                    if isinstance(old_ids, list) and isinstance(new_ids, list) else set())
                    if paid_refunds and PointsVoluntaryPaidRefundEvidence.objects.filter(
                            manifest=manifest, refund_id__in=paid_refunds).count() == len(paid_refunds):
                        from .points_voluntary_paid_evidence import transition_locked
                        records = transition_locked(order, manifest, plan, terms)
                    else:
                        from .points_transfer_followup_settlement import transition_locked
                        records, plan = transition_locked(order, manifest, plan, terms)
                else:
                    from .models import PointsVoluntaryPaidRefundEvidence
                    old_ids = manifest.plan.get('points_funding', {}).get('addon_refund_ids', [])
                    new_ids = terms.get('points_funding', {}).get('addon_refund_ids', [])
                    paid_refunds = (set(new_ids) - set(old_ids)
                                    if isinstance(old_ids, list) and isinstance(new_ids, list) else set())
                    if paid_refunds and PointsVoluntaryPaidRefundEvidence.objects.filter(
                            manifest=manifest, refund_id__in=paid_refunds).count() == len(paid_refunds):
                        from .points_voluntary_paid_evidence import transition_locked
                    else:
                        from .points_transfer_settlement_revision import transition_locked
                    records = transition_locked(order, manifest, plan, terms)
        else:
            if 'round_posting_ids' in terms.get('points_funding', {}):
                from .points_round_settlement_plan import verify_manifest_locked
                verify_manifest_locked(order, manifest, terms)
            elif 'third_posting_id' in terms.get('points_funding', {}):
                from .points_third_refund_settlement_plan import verify_manifest_locked
                verify_manifest_locked(order, manifest, terms)
            elif 'followup_posting_id' in terms.get('points_funding', {}):
                from .models import OrderPointsTransferFollowupFundingRevision
                from .points_transfer_followup_settlement import verify_manifest_locked
                evidence = OrderPointsTransferFollowupFundingRevision.objects.get(
                    posting_id=terms['points_funding']['followup_posting_id'])
                verify_manifest_locked(order, manifest, terms, evidence.prior_manifest)
            records = _validate_records(order, manifest, plan)
    else:
        if (order.providers.filter(settled_at__isnull=False).exists() or order.transactions.filter(
                tx_type__in=(Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME),
                status='SUCCESS', reversal_of__isnull=True).exists()):
            raise SettlementError('订单存在未归属的历史收益或结算记录')
        records = {}
    due = [p for p in plan.persons if p.account_id not in records and not p.blockers]
    if not due:
        if manifest:
            _finalize(order, manifest, plan, trace_id=trace_id or new_trace_id())
        return []
    mappings = {row.account_id:row.legacy_user_id for row in LegacyAccountMap.objects.filter(
        account_id__in=[p.account_id for p in due])}
    if len(mappings) != len(due) or len(set(mappings.values())) != len(mappings):
        raise SettlementError('陪玩业务账号与历史身份映射待核对')
    profiles = {p.account_id:p for p in EscortProfile.objects.select_for_update().filter(
        account_id__in=mappings).order_by('user_id')}
    if len(profiles) != len(mappings) or any(profiles[k].user_id != v for k,v in mappings.items()):
        raise SettlementError('陪玩资料与业务账号映射待核对')
    from wallet.refund_deposit import enabled as deficit_enabled
    from wallet.models import RefundDepositDeficit
    for profile in profiles.values():
        if profile.deposit_mode != 'EARNINGS' and profile.deposit_paid < profile.effective_deposit_required:
            gap = profile.effective_deposit_required - profile.deposit_paid
            recorded = (RefundDepositDeficit.objects.filter(profile=profile).aggregate(v=Sum('remaining'))['v'] or 0
                        if deficit_enabled() else 0)
            if recorded < gap:
                raise SettlementError('现金押金不足，补足情况待核对')
    # The current legacy refund path can consume deposit without recording
    # the dedicated deficit required by the PRD. Do not accidentally release
    # any income for such people until that deficit flow is migrated.
    old_recoveries = RefundRecoveryLine.objects.filter(source='DEPOSIT').filter(
        Q(wallet__account_id__in=mappings) | Q(wallet__user_id__in=mappings.values()))
    if deficit_enabled():
        # Direct deposit charges have no provider-wallet transaction.  A NULL
        # in a NOT IN subquery would hide every unregistered legacy recovery.
        registered = RefundDepositDeficit.objects.filter(account_id__in=mappings,
            source_provider_transaction__isnull=False).values('source_provider_transaction_id')
        old_recoveries = old_recoveries.exclude(reversal_transaction_id__in=registered)
    if old_recoveries.exists():
        raise SettlementError('陪玩存在历史退款押金扣减，补足情况待核对')
    if not manifest:
        manifest = OrderSegmentSettlementManifest.objects.create(order=order, plan=terms, plan_hash=_hash(terms))
        if 'round_posting_ids' in terms.get('points_funding', {}):
            from .points_round_settlement_plan import verify_manifest_locked
            verify_manifest_locked(order, manifest, terms)
        elif 'third_posting_id' in terms.get('points_funding', {}):
            from .points_third_refund_settlement_plan import verify_manifest_locked
            verify_manifest_locked(order, manifest, terms)
        elif 'followup_posting_id' in terms.get('points_funding', {}):
            from .points_transfer_followup_settlement import verify_manifest_locked
            verify_manifest_locked(order, manifest, terms, None)
    now = timezone.now(); trace_id = trace_id or new_trace_id()
    settled_users, settled_accounts = [], []
    for person in due:
        user_id, profile = mappings[person.account_id], profiles[person.account_id]
        income = _credit_wallet(order=order, amount=person.income, tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}', event='order.settle.segment', trace_id=trace_id,
            user_id=user_id, account_id=person.account_id, log_after_commit=points_order)
        deposit_before = profile.deposit_paid
        hold = hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                                    apply_without_hold=True)
        deficit_repayment = 0
        if deficit_enabled() and income:
            from wallet.models import RefundDepositIncomeApplication
            application = RefundDepositIncomeApplication.objects.filter(source_transaction=income).first()
            if not application:
                from wallet.income_priority_evidence import pending_repayment_hold
                if not hold or not pending_repayment_hold(income):
                    raise SettlementError('收益补押金处理记录缺失')
            else:
                deficit_repayment = application.applied_amount
        deposit = profile.deposit_paid - deposit_before - deficit_repayment
        debit = credit = None
        if deposit:
            platform = get_platform_wallet(for_update=True)
            debit = Transaction.objects.filter(order=order, wallet_id=income.wallet_id, pk__gt=income.pk,
                tx_type=Transaction.TxType.DEPOSIT, amount=-deposit, reversal_of__isnull=True).order_by('-pk').first()
            credit = Transaction.objects.filter(order=order, wallet=platform, pk__gt=income.pk,
                tx_type=Transaction.TxType.DEPOSIT_INCOME, amount=deposit, reversal_of__isnull=True).order_by('-pk').first()
            if not debit or not credit:
                raise SettlementError('押金配对流水缺失')
        settlement_record = OrderSegmentPersonSettlement.objects.create(manifest=manifest, account_id=person.account_id,
            amount=person.income, segment_ids=list(person.segment_ids), report_versions=_report_versions(person),
            income_transaction=income, deposit_debit=debit, deposit_credit=credit, settled_at=now)
        addon_funding = manifest.plan.get('points_funding') or manifest.plan.get('ordinary_transfer_funding') or {}
        if addon_funding.get('addons'):
            from .segment_addon_settlement import settle_person
            settle_person(order, manifest, settlement_record, profile, now=now, trace_id=trace_id)
        if person.income == 0:
            from wallet.refund_recovery_order import close_for_zero_settlement
            close_for_zero_settlement(profile, settlement_record)
        settled_users.append(user_id); settled_accounts.append(person.account_id)
    EscortProfile.objects.filter(user_id__in=settled_users).update(completed_order_count=F('completed_order_count') + 1)
    refresh_escort_status(provider_ids=settled_users, account_ids=settled_accounts, exclude_order_ids=[order.pk])
    _finalize(order, manifest, plan, trace_id=trace_id)
    return settled_users
