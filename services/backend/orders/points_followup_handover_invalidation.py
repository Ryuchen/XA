"""Facts for an add-on refund which invalidates a pending follow-up handover.

Call capture only under the order-money lock.  This module does not authorize
the refund or make a pending proposal eligible to be superseded.
"""
import hashlib
import json
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def other_pending_payment_id(order, refund_payment_id):
    from .models import OrderAddonHandover
    from .points_transfer_refund_access import permits
    if order.refund_records.count() != 2 or not permits(order, phase='refund'):
        return None
    rows = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                .values_list('payment_id', flat=True))
    return frozenset(rows) if rows and len(rows) == len(set(rows)) else None


def may_refund_pending_target(order, payment):
    from .models import OrderAddonHandover
    from .points_transfer_refund_access import permits
    from .points_voluntary_handover_event import entry_matches
    from .points_transfer_addon_liability import first_refund_active
    voluntary = order.refund_records.count() in (1, 2) and entry_matches(order, payment.pk)
    first = first_refund_active(order, payment.pk)
    if not permits(order, phase='refund') or (order.refund_records.count() != 2 and not voluntary and not first):
        return False
    rows = list(OrderAddonHandover.objects.filter(payment=payment, status='PENDING'))
    if len(rows) != 1:
        return False
    if (voluntary and rows[0].kind != 'VOLUNTARY') or (first and rows[0].kind != 'STANDARD'):
        return False
    if rows[0].previous_id:
        return payment.handovers.filter(status='ACCEPTED', pk=rows[0].previous_id).exists()
    return not payment.handovers.exclude(status__in=('REJECTED', 'PENDING', 'SUPERSEDED')).exists()


def permits_locked(order, pending, refund_payment):
    """The caller already holds order and money locks and a pinned refund preflight."""
    from stores.db import transaction
    from .models import OrderAddonHandover
    from .points_transfer_refund_access import permits
    from .points_transfer_followup_addon_version import _preflight
    from .points_voluntary_handover_event import entry_matches
    from .points_transfer_addon_liability import first_refund_active, first_refund_pending_allowed
    state = _preflight.get()
    pending_rows = list(OrderAddonHandover.objects.filter(payment__intent__order=order,
        status='PENDING').values_list('pk', 'payment_id'))
    voluntary = bool(order.refund_records.count() in (1, 2) and pending.kind == 'VOLUNTARY'
        and entry_matches(order, refund_payment.pk))
    paid_target = False
    if (pending.kind == 'STANDARD' and state
            and state[:2] == (transaction.get_connection().alias, order.pk)
            and state[4:] == ('refund', refund_payment.pk)
            and order.providers.filter(settled_at__isnull=False).exists()):
        from .models import OrderSegmentSettlementManifest
        from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
        from .segment_addon_settlement_revision import verify_revisions
        manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
        paid = _prior_settlement(order)
        if (manifest and paid and not manifest.finalized_at and paid.get('manifest_id') == manifest.pk
                and not {pending.source_segment.provider_account_id, pending.target_segment.provider_account_id}
                    .intersection(item['account_id'] for item in paid['people'])):
            verify_revisions(manifest)
            _verify_prior_settlement(order, paid)
            if pending.payment_id != refund_payment.pk:
                from .points_transfer_followup_handover_scope import source_locked
                source_locked(order, pending.payment, pending.target_segment.provider_account_id,
                              pending_handover_id=pending.pk)
            paid_target = True
    first = (first_refund_active(order, refund_payment.pk) and first_refund_pending_allowed(order, pending.payment_id)
             and pending.kind == 'STANDARD' and not order.settled_at and pending.status == 'PENDING'
             and (pending.pk, pending.payment_id) in pending_rows
             and len({row[1] for row in pending_rows}) == len(pending_rows))
    return bool(transaction.get_connection().in_atomic_block and permits(order, phase='refund') and first) or bool(transaction.get_connection().in_atomic_block and permits(order, phase='refund')
        and voluntary and not order.settled_at
        and pending.status == 'PENDING' and (pending.pk, pending.payment_id) in pending_rows
        and len({row[1] for row in pending_rows}) == len(pending_rows)) or bool(transaction.get_connection().in_atomic_block and permits(order, phase='refund')
        and state and state[:2] == (transaction.get_connection().alias, order.pk)
        and state[4:] == ('refund', refund_payment.pk) and order.refund_records.count() == 2
        and not order.settled_at and (paid_target or not order.providers.filter(settled_at__isnull=False).exists())
        and pending.status == 'PENDING' and pending.payment.intent.order_id == order.pk
        and refund_payment.intent.order_id == order.pk
        and (pending.pk, pending.payment_id) in pending_rows
        and len({row[1] for row in pending_rows}) == len(pending_rows))


def capture(handover, refund, *, witness_receipt_id=None):
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .addon_payment import verify_receipt
    from .models import OrderAddonFundingReleaseRun, PointsNegotiationPosting
    from console.models import AdminAuditLog
    payment, order = handover.payment, handover.payment.intent.order
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
                    created_at__lt=refund.created_at)
                    .order_by('created_at', 'pk'))
    voluntary = handover.kind == 'VOLUNTARY'
    first_transfer = not voluntary and len(postings) == 1
    across_second = False
    if not voluntary and len(postings) == 2 and handover.created_at < postings[-1].created_at:
        from .models import OrderPointsTransferFollowupFundingRevision
        from .points_transfer_followup_pending import verify_snapshot
        followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=postings[-1]).first()
        snapshot = followup.pending_handover_snapshot if followup else None
        if (not isinstance(snapshot, list) or not any(isinstance(item, dict)
                and item.get('handover_id') == handover.pk and item.get('payment_id') == payment.pk
                for item in snapshot)):
            raise ValidationError('二次退款缺少原待接替方案快照')
        verify_snapshot(postings[-1], snapshot, receipt_id=witness_receipt_id)
        across_second = True
    transfer_revision = None
    if first_transfer:
        from .points_post_refund_transfer import _digest as transfer_digest
        from .points_transfer_addon_liability import effective_at, frozen_transfer_for_handover
        transfer_revision = frozen_transfer_for_handover(order, payment.pk, handover.source_segment_id,
                                                          handover.target_segment_id, handover.created_at)
        if (not transfer_revision or transfer_revision.snapshot.get('posting_id') != postings[0].pk
                or transfer_revision.evidence_hash != transfer_digest(order.pk, transfer_revision.transfer_id,
                    transfer_revision.prior_id, transfer_revision.snapshot)
                or handover.target_segment_id != transfer_revision.transfer.next_segment_id
                or effective_at(transfer_revision, payment, handover.created_at,
                                witness_receipt_id=witness_receipt_id) !=
                    [payment.pk, handover.source_segment_id, handover.source_basis_before]):
            raise ValidationError('首次退款后待接替原转单责任异常')
    if (len(postings) not in (1, 2)
            or handover.status != 'SUPERSEDED' or handover.accepted_at
            or not handover.created_at or not refund.created_at
            or (handover.created_at <= postings[-1].created_at and not across_second)
            or refund.created_at <= handover.created_at
            or refund.order_id != order.pk
            or OrderAddonFundingReleaseRun.objects.filter(handover=handover).exists()
            or payment.refund_records.filter(created_at__gte=handover.created_at,
                created_at__lte=refund.created_at).exclude(pk=refund.pk).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                refund__created_at__lt=refund.created_at).exists()):
        raise ValidationError('退款导致接替失效的事件顺序异常')
    verify_receipt(payment)
    verify_receipt(refund.payment)
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    active = [row for row in shares if row['is_current'] and row['basis'] > 0]
    if (len(active) != 1 or (previous.pk if previous else None) != handover.previous_id
            or active[0]['segment_id'] != handover.source_segment_id
            or active[0]['basis'] != handover.source_basis_before
            or handover.source_segment.attribution.order_id != order.pk
            or handover.target_segment.attribution.order_id != order.pk):
        raise ValidationError('失效方案的原责任不一致')
    deductions = list(refund.earning_deductions.filter(payment=payment).select_related('participant', 'segment').order_by('pk'))
    same_payment = refund.payment_id == payment.pk
    allocations = []
    if same_payment and previous:
        from .addon_refund_share_allocation import verify as verify_share_allocations
        verify_share_allocations(refund)
        allocations = list(refund.share_allocations.filter(payment=payment).order_by('pk'))
        if not allocations or sum(row.refund_basis for row in allocations) != refund.amount:
            raise ValidationError('同付款退款缺少真实逐份额退回凭证')
    if ((not same_payment and not deductions) or any(
            (not same_payment and row.segment_id != handover.source_segment_id) or row.amount <= 0
            or (same_payment and not previous and row.segment_id != payment.intent.segment_id)
            or row.participant.proposal_id != refund.proposal_id
            or row.segment.provider_account_id != row.participant.account_id
            or (not same_payment and row.participant.account_id != handover.source_segment.provider_account_id)
            for row in deductions)):
        raise ValidationError('退款没有真实改变待确认接替责任')
    if refund.payment_id != payment.pk:
        from .addon_refund_share_allocation import verify as verify_share_allocations
        verify_share_allocations(refund)
        if previous:
            active_deduction = sum(refund.share_allocations.filter(payment=payment,
                share__handover=previous, share__is_current=True,
                share__segment_id=handover.source_segment_id).values_list('earning_deduction', flat=True))
        else:
            active_deduction = sum(row.amount for row in deductions)
        if not 0 < active_deduction <= active[0]['income']:
            raise ValidationError('退款没有扣减待接替的实际当前份额')
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    parties = {(payment.intent.buyer_id, 'BOSS'), (handover.source_segment.provider_account_id, 'PROVIDER'),
               (handover.target_segment.provider_account_id, 'PROVIDER')}
    if (len(decisions) != 3 or {(row[0], row[1]) for row in decisions} != parties
            or any(row[2] is False or ((row[2] is None) != (row[3] is None))
                   or (row[3] and not handover.created_at <= row[3] <= refund.created_at) for row in decisions)
            or all(row[2] is True for row in decisions)):
        raise ValidationError('失效前的接替确认事实异常')
    try:
        target_snapshot = _commission(handover.target_segment, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('失效方案的抽成快照异常') from exc
    source = active[0]
    expected = [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], False)
                for row in shares if not row['is_current']]
    if handover.retained_basis:
        full = _income(source['basis'], source['commission_snapshot'])
        retained = (_income(handover.retained_basis, source['commission_snapshot']) if source['income'] == full
                    else source['income'] * handover.retained_basis // source['basis'])
        expected.append((handover.source_segment_id, handover.retained_basis, retained,
                         source['commission_snapshot'], False))
    expected.append((handover.target_segment_id, handover.transferred_basis,
                     _income(handover.transferred_basis, target_snapshot), target_snapshot, True))
    rows = list(handover.shares.order_by('position'))
    if (not handover.transferred_basis or handover.retained_basis + handover.transferred_basis != source['basis']
            or [row.position for row in rows] != list(range(len(rows)))
            or [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] != expected):
        raise ValidationError('失效方案的逐段份额异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    body = {'order_id': order.pk, 'payment_id': payment.pk,
            'source_provider': handover.source_segment.provider_account_id,
            'target_provider': handover.target_segment.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('失效方案的原请求审计异常')
    result = {'kind': ('VOLUNTARY_INVALIDATION_V1' if voluntary else
                       'TRANSFER_FIRST_INVALIDATION_V1' if first_transfer else 'TRANSFER_FOLLOWUP_INVALIDATION_V1'),
            'order_id': order.pk, 'payment_id': payment.pk,
            'handover_id': handover.pk, 'refund_id': refund.pk, 'previous_handover_id': handover.previous_id,
            'basis_before': source['basis'], 'income_before': source['income'],
            'deductions': [[row.pk, row.participant_id, row.segment_id, row.amount] for row in deductions],
            'shares': [[row.pk, row.segment_id, row.position, row.basis, row.income,
                        row.commission_snapshot, row.is_current] for row in rows],
            'decisions': [[account, role, accepted, at.isoformat() if at else None]
                          for account, role, accepted, at in decisions]}
    if first_transfer:
        result['transfer_revision_id'] = transfer_revision.pk
    if same_payment and previous:
        result['same_payment_refund_basis'] = [[row.pk, row.participant_id, row.share_id,
            row.refund_basis, row.earning_deduction] for row in allocations]
    return result


def verify(record, *, receipt_id=None):
    from .models import OrderAddonFundingReleaseRun
    from .points_transfer_followup_addon_version import _verify_prior_run
    if receipt_id is None:
        from .points_transfer_refund_access import permits, verification_scope
        order = record.handover.payment.intent.order
        from .points_voluntary_handover_event import entry_matches
        if record.handover.kind == 'VOLUNTARY' and entry_matches(order, record.handover.payment_id) \
                and not permits(order, phase='refund'):
            from .points_business_access import _bind
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                return verify(record)
        if permits(order, phase='transfer'):
            with verification_scope(order):
                return verify(record)
        if not permits(record.handover.payment.intent.order, phase='refund'):
            raise ValidationError('无到账的失效凭证须在本单持锁核对')
    try:
        actual = capture(record.handover, record.refund, witness_receipt_id=receipt_id)
        if (not isinstance(record.snapshot, dict) or record.snapshot != actual
                or record.evidence_hash != digest(actual) or record.created_at < record.refund.created_at):
            raise ValidationError('退款导致接替失效的凭证不一致')
        run = OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first()
        voluntary = record.handover.kind == 'VOLUNTARY'
        first_transfer = actual.get('kind') == 'TRANSFER_FIRST_INVALIDATION_V1'
        voluntary_source = (isinstance(run.points_addon_snapshot, dict) or
                            (isinstance(run.points_followup_snapshot, dict)
                             and run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1')) if run else False
        if (not run or run.created_at < record.created_at or (voluntary and not voluntary_source)
                or (first_transfer and not isinstance(run.points_addon_snapshot, dict))
                or (not voluntary and not first_transfer and (not isinstance(run.points_followup_snapshot, dict)
                                       or run.points_followup_snapshot.get('kind') != 'TRANSFER_FOLLOWUP_V1'))):
            raise ValidationError('失效凭证缺少真实退款释放事件')
        if first_transfer:
            from .models import PointsTransferAddonLiabilityRevision
            from .points_transfer_addon_liability import verify_locked as verify_liability
            liability = PointsTransferAddonLiabilityRevision.objects.filter(
                transfer_revision_id=actual['transfer_revision_id'], payment_id=record.handover.payment_id,
                refund_id=record.refund_id, funding_run=run).first()
            if not liability:
                raise ValidationError('失效凭证缺少本次退款的待接替责任版本')
            verify_liability(liability, witness_receipt_id=receipt_id)
        elif voluntary:
            from .addon_funding_release import verify_run
            verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        else:
            _verify_prior_run(run, receipt_id=receipt_id)
    except DjangoValidationError as exc:
        raise ValidationError('失效凭证的到账来源异常') from exc
    return actual
