"""Verified already-paid people before a later points refund."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def capture_locked(order, case):
    """Capture existing credits while holding the order, money and receipt locks."""
    from .models import OrderSegmentSettlementManifest, OrderSegmentPersonSettlement
    from .points_transfer_refund_access import permits
    from .points_transfer_refund_evidence import _prior_settlement
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    from engagement.confirmation import aftersale_deadline

    if not transaction.get_connection().in_atomic_block or not permits(order, phase='refund'):
        raise ValidationError('已付人员须在本单资金事务中核对')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not manifest:
        if OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists():
            raise ValidationError('已付人员结算清单缺失')
        return None
    deadline = aftersale_deadline(order)
    if (case.order_id != order.pk or case.status != 'OPEN' or not case.created_at
            or order.status != order.Status.COMPLETED or not order.completed_at or order.settled_at
            or manifest.finalized_at or not deadline or case.created_at >= deadline
            or case.created_at > timezone.now()):
        raise ValidationError('已付人员退款的整单状态或售后时间异常')
    snapshot = _prior_settlement(order)
    if not snapshot or set(case.affected_provider_account_ids or []).intersection(
            row['account_id'] for row in snapshot['people']):
        raise ValidationError('本次退款不能改变已结算人员收益')
    # A previous refund can leave the original paid manifest in place while
    # the shop still owes funding. Its immutable round chain, not that old
    # plan, is the current funding baseline for the next refund.
    from .models import PointsRefundRoundEvidence
    latest = PointsRefundRoundEvidence.objects.select_for_update().filter(order=order, previous__isnull=False).select_related(
        'posting__proposal__points_terms').order_by('-posting__created_at', '-posting_id').first()
    context = latest.posting.proposal.points_terms.round_context if latest else None
    if isinstance(context, dict) and context.get('paid_snapshot') == snapshot:
        from .points_round_funding import current_locked
        from .points_round_receipt_usage import verify_locked
        from .points_voluntary_paid_evidence import verify_historical_paid
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            state = current_locked(order)
            if state['round'].pk != latest.pk:
                raise ValidationError('多轮退款资金前版已变化')
            verify_locked(latest)
            verify_historical_paid(order, snapshot, timezone.now(), manifest.pk)
        except DjangoValidationError as exc:
            raise ValidationError('已付流水或多轮实际退款待核对') from exc
        return snapshot
    try:
        if order.refund_records.count() == 3:
            from .points_third_refund_settlement_plan import prepare_locked
        else:
            from .points_round_settlement_plan import prepare_locked
        plan, terms = prepare_locked(order.pk)
        if manifest.plan != terms or manifest.plan_hash != _hash(terms):
            raise ValidationError('已付人员原资金清单与真实订单不一致')
        _validate_records(order, manifest, plan)
    except SettlementError as exc:
        raise ValidationError('已付人员原流水、报单或整单补资待核对') from exc
    return snapshot


def transition_locked(order, manifest, plan, terms):
    """Revise the unpaid plan after a real later refund, preserving paid rows."""
    from .models import OrderSegmentSettlementRevision, PointsRefundRoundEvidence
    from .segment_settlement import _hash, _validate_records
    from .points_voluntary_paid_evidence import verify_historical_paid
    from .points_transfer_paid_addons import unchanged
    from .settlement import SettlementError
    if not transaction.get_connection().in_atomic_block or manifest.order_id != order.pk or manifest.finalized_at:
        raise SettlementError('多轮退款已付清单的事务范围异常')
    versions = list(PointsRefundRoundEvidence.objects.filter(order=order, previous__isnull=False)
        .select_related('posting__proposal__points_terms', 'posting__points_refund__refund').order_by('-posting__created_at', '-pk'))
    candidate = next((version for version in versions if isinstance(version.posting.proposal.points_terms.round_context, dict)
        and version.posting.proposal.points_terms.round_context.get('paid_snapshot', {}).get('plan_hash') == manifest.plan_hash), None)
    if not candidate:
        raise SettlementError('多轮退款缺少与已付清单对应的真实快照')
    posting = candidate.posting
    paid = posting.proposal.points_terms.round_context['paid_snapshot']
    if (paid.get('manifest_id') != manifest.pk or paid.get('plan') != manifest.plan
            or manifest.plan_hash != _hash(manifest.plan) or not posting.termination_processed_at
            or set(posting.proposal.affected_accounts).intersection(row['account_id'] for row in paid['people'])
            or OrderSegmentSettlementRevision.objects.filter(posting=posting).exists()):
        raise SettlementError('多轮退款的原已付清单或受影响范围异常')
    try:
        verify_historical_paid(order, paid, posting.created_at, manifest.pk)
        unchanged(paid, terms.get('points_funding', {}).get('addons', []))
    except ValidationError as exc:
        raise SettlementError('多轮退款的已付流水待核对') from exc
    people = {person.account_id: person for person in plan.persons}
    if any(row['account_id'] not in people or people[row['account_id']].income != row['amount']
            or list(people[row['account_id']].segment_ids) != row['segment_ids'] for row in paid['people']):
        raise SettlementError('多轮退款改变了已付人员的收益或履约段')
    # The normal record verifier additionally checks the original income,
    # deposit and add-on ledgers; no paid row is written or credited again.
    _validate_records(order, manifest, plan)
    with transaction.atomic():
        OrderSegmentSettlementRevision.objects.create(manifest=manifest, posting=posting,
            before_plan_hash=manifest.plan_hash, after_plan=terms, after_plan_hash=_hash(terms))
        manifest.plan, manifest.plan_hash = terms, _hash(terms)
        manifest.save(update_fields=['plan', 'plan_hash'])
        return _validate_records(order, manifest, plan)


def verify_revision_locked(manifest, revision, *, before_plan):
    """Recheck one persisted transition without touching paid ledger rows."""
    from .models import PointsRefundRoundEvidence
    from .segment_settlement import _hash
    from .points_round_receipt_usage import verify_locked
    from .points_transfer_paid_addons import unchanged
    from django.core.exceptions import ValidationError as DjangoValidationError
    from .settlement import SettlementError
    version = PointsRefundRoundEvidence.objects.filter(posting=revision.posting, previous__isnull=False,
        order_id=manifest.order_id).select_related('posting__proposal__points_terms').first()
    context = version.posting.proposal.points_terms.round_context if version else None
    paid = context.get('paid_snapshot') if isinstance(context, dict) else None
    if (not isinstance(paid, dict) or paid.get('manifest_id') != manifest.pk
            or paid.get('plan') != before_plan or paid.get('plan_hash') != _hash(before_plan)
            or revision.before_plan_hash != _hash(before_plan)
            or revision.after_plan_hash != _hash(revision.after_plan)
            or revision.created_at < version.posting.created_at
            or set(version.posting.proposal.affected_accounts).intersection(
                row['account_id'] for row in paid['people'])):
        raise SettlementError('多轮退款已付清单版本异常')
    try:
        verify_locked(version)
        unchanged(paid, revision.after_plan.get('points_funding', {}).get('addons', []))
    except (ValidationError, DjangoValidationError) as exc:
        raise SettlementError('多轮退款已付流水或释放待核对') from exc
    before_people = {row['account_id']: row for row in before_plan.get('people', [])}
    after_people = {row['account_id']: row for row in revision.after_plan.get('people', [])}
    if any(before_people.get(row['account_id']) != after_people.get(row['account_id']) for row in paid['people']):
        raise SettlementError('多轮退款清单改变了已付人员')
    return revision.after_plan
