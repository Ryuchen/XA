"""Proof for a first refund followed by a two-seat add-on handover."""
from contextlib import contextmanager
from contextvars import ContextVar
from django.core.exceptions import ValidationError

_accepting = ContextVar('ordinary_team_handover_accepting', default=None)
_posting_refund = ContextVar('ordinary_team_posting_refund', default=None)


@contextmanager
def accepting(handover):
    token = _accepting.set(handover.pk)
    try:
        yield
    finally:
        _accepting.reset(token)


@contextmanager
def posting_refund(refund):
    token = _posting_refund.set((refund.pk, refund.order_id))
    try:
        yield
    finally:
        _posting_refund.reset(token)


def is_posting_refund(refund):
    return _posting_refund.get() == (refund.pk, refund.order_id)


def verify(handover):
    if handover.previous_id:
        from .ordinary_team_later_handover import verify as verify_later
        return verify_later(handover)
    from console.models import AdminAuditLog
    from .addon_handover import _income
    from .addon_payment import verify_receipt
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .models import NegotiationExecution, OrderPointsFundingAllocation, OrderAddonFundingReleaseRun
    from .ordinary_refund_receipt_evidence import verified_shares

    payment, order = handover.payment, handover.payment.intent.order
    verify_receipt(payment)
    executions = list(NegotiationExecution.objects.filter(refund__order=order,
        refund__created_at__lt=handover.created_at).select_related('refund__order').order_by('pk')[:2])
    if not executions:
        from .negotiation_zero import marked_execution_id
        zero_id = marked_execution_id(order)
        if zero_id:
            executions = list(NegotiationExecution.objects.filter(pk=zero_id, zero_order=order,
                termination_processed_at__lt=handover.created_at).select_related('zero_order', 'proposal'))
    if (len(executions) != 1 or not handover.accepted_at or handover.status != 'ACCEPTED'
            or not executions[0].termination_processed_at
            or executions[0].termination_processed_at > handover.created_at
            or payment.buyer_transaction.created_at >= handover.created_at
            or handover.previous_id
            or order.providers.filter(settled_at__lte=handover.accepted_at).exists()):
        raise ValidationError('协商退款后加购接替时序或补资范围异常')
    if handover.version != 1:
        from .ordinary_pending_handover_refund import verify_retry
        if (payment.handovers.filter(version__lt=handover.version, status='ACCEPTED').exists()
                or not verify_retry(payment, before_version=handover.version, before=handover.created_at)):
            raise ValidationError('此前失效的加购接替凭证异常')
    first = executions[0]
    if first.zero_order_id and not first.refund_id:
        from .ordinary_zero_followup_evidence import verified_first
        shares = verified_first(first, boundary=handover.created_at)
        main_at = first.termination_processed_at
    else:
        shares = verified_shares(first)
        main_at = first.refund.created_at
    prior = list(order.addon_refunds.filter(created_at__lte=handover.accepted_at).order_by('created_at', 'pk'))
    if prior:
        before_main = [item for item in prior if item.created_at < main_at]
        after_main = [item for item in prior if item.created_at > main_at]
        if len(before_main) + len(after_main) != len(prior):
            raise ValidationError('接替前退款事件时序异常')
        for item in before_main:
            previous_run = OrderAddonFundingReleaseRun.objects.filter(refund=item).first()
            if (previous_run and (not previous_run.ordinary_prestart_snapshot or not previous_run.ordinary_prestart_hash)):
                raise ValidationError('接替前加购退款释放来源尚未接通')
        if before_main:
            from .ordinary_multi_prior_addon_facts import verify as verify_prior
            verify_prior(order, executions[0].created_at)
        from .ordinary_addon_after_main_release import _digest
        for item in after_main:
            previous_run = OrderAddonFundingReleaseRun.objects.filter(refund=item).first()
            # A refund already completed before this proposal does not need
            # an invalidation of a proposal that did not yet exist.  Its real
            # funding version and posted participant evidence are replayed by
            # the subsequent funding plan under the order and receipt locks.
            if (item.created_at >= handover.created_at
                    or not previous_run or previous_run.created_at >= handover.created_at
                    or not isinstance(previous_run.ordinary_refund_snapshot, dict)
                    or previous_run.ordinary_refund_snapshot.get('kind') != 'ordinary_addon_after_main_refund_v1'
                    or previous_run.ordinary_refund_hash != _digest(previous_run, previous_run.ordinary_refund_snapshot)):
                raise ValidationError('接替前追加退款资金版本异常')
    basis_before = payment.intent.amount - sum(item.amount for item in prior if item.payment_id == payment.pk)
    income_before = payment.provider_income - sum(payment.refund_earning_deductions.filter(
        refund__created_at__lt=handover.accepted_at).values_list('amount', flat=True))
    source, target = handover.source_segment, handover.target_segment
    if (len(shares) != 1 or shares[0].account_id != source.provider_account_id
            or source.pk != payment.intent.segment_id or source.attribution.order_id != order.pk
            or target.attribution.order_id != order.pk or source.provider_account_id == target.provider_account_id
            or not source.started_at or not target.started_at or target.started_at > handover.created_at
            or (target.released_at and target.released_at < handover.accepted_at)
            or basis_before <= 0 or income_before < 0 or handover.source_basis_before != basis_before
            or handover.retained_basis + handover.transferred_basis != basis_before
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('加购接替履约或收益份额异常')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                (target.provider_account_id, 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, accepted) for a, role, accepted, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('加购接替三方确认异常')
    try:
        target_commission = _commission(target, allow_refund_review=True, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('加购接替抽成快照异常') from exc
    expected_rows = []
    if handover.retained_basis:
        full_income = _income(basis_before, payment.intent.commission_snapshot)
        retained_income = (_income(handover.retained_basis, payment.intent.commission_snapshot)
            if income_before == full_income else income_before * handover.retained_basis // basis_before)
        expected_rows.append((source.pk, handover.retained_basis,
            retained_income, payment.intent.commission_snapshot, False))
    expected_rows.append((target.pk, handover.transferred_basis,
        _income(handover.transferred_basis, target_commission), target_commission, True))
    rows = list(handover.shares.order_by('position'))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] != expected_rows
            or [row.position for row in rows] != list(range(len(rows)))):
        raise ValidationError('加购接替逐段收益异常')
    audit = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/', method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audit) != 1 or audit[0].operator_account_id != handover.created_by_id or audit[0].request_body != body:
        raise ValidationError('加购接替审计异常')
    return handover


def permits(order):
    from rest_framework.exceptions import ValidationError as RestValidationError
    from .points_funding_plan import PointsFundingPlanError
    from .ordinary_followup_capability import production_enabled
    from .models import NegotiationExecution, OrderOriginalShareTransfer, OrderPointsFundingAllocation
    from .points_service_payment import needs_integrated_processing
    if (not production_enabled() or not order or needs_integrated_processing(order)
            or order.is_gift_snapshot or order.source_order_id or order.required_provider_count != 2
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return False
    executions = list(NegotiationExecution.objects.filter(refund__order=order,
        termination_processed_at__isnull=False).select_related('refund__order').order_by('pk')[:2])
    if len(executions) != 1 or order.refund_records.count() != 1:
        return False
    if order.addon_refunds.filter(created_at__lt=executions[0].refund.created_at).exists():
        try:
            from .ordinary_multi_prior_addon_facts import verify as verify_prior
            verify_prior(order, executions[0].created_at)
        except (ValidationError, RestValidationError, PointsFundingPlanError, ValueError):
            return False
    from .models import OrderAddonHandover, OrderAddonFundingReleaseRun
    for handover in OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED').select_related(
            'payment__intent__order', 'source_segment__attribution', 'target_segment__attribution'):
        try:
            pre_main = handover.accepted_at < executions[0].refund.created_at
            if pre_main:
                from .ordinary_team_pre_main_handover import verify as verify_pre_main
                verify_pre_main(handover)
            else:
                verify(handover)
            funded = OrderPointsFundingAllocation.objects.filter(service_order=order,
                created_at__lte=handover.accepted_at).exists()
            if funded and _accepting.get() != handover.pk:
                run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
                if pre_main:
                    if not run or not run.ordinary_prestart_snapshot or not run.ordinary_prestart_hash:
                        return False
                    from .addon_funding_release import verify_run
                    verify_run(run)
                else:
                    from .ordinary_refund_addon_payment_release import HANDOVER_KIND, verify as verify_run
                    if not run or not isinstance(run.ordinary_refund_snapshot, dict) or run.ordinary_refund_snapshot.get('kind') != HANDOVER_KIND:
                        return False
                    verify_run(run)
        except (ValidationError, RestValidationError, PointsFundingPlanError, ValueError):
            return False
    for refund in order.addon_refunds.select_related('payment', 'proposal'):
        if refund.created_at < executions[0].refund.created_at:
            continue
        handover = refund.payment.handovers.filter(status='ACCEPTED',
                                                    accepted_at__lt=refund.created_at).first()
        people = list(refund.proposal.participants.all())
        accounts = ({handover.source_segment.provider_account_id, handover.target_segment.provider_account_id}
                    if handover else {refund.payment.intent.provider_id})
        if (not people or len(people) > 2
                or not {row.account_id for row in people}.issubset(accounts)):
            return False
    if OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING').exists():
        return not order.providers.filter(settled_at__isnull=False).exists()
    return True
