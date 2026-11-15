"""Transition a partly paid transfer settlement after an actual refund.

The accepted refund owns the pre-refund snapshot. This transition is performed
only inside the same locked transaction as the next settlement attempt and only
after the complete revised funding has been verified by the caller.
"""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderSegmentSettlementRevision, PointsNegotiationPosting
from .points_checkout_test import require_isolated_checkout
from .settlement import SettlementError


def transition_locked(order, manifest, plan, terms):
    from .points_transfer_refund_access import permits
    formal = any(permits(order, phase=phase) for phase in ('refund', 'settlement'))
    if not formal:
        try:
            require_isolated_checkout()
        except ValidationError as exc:
            raise SettlementError('转单退款后的结算版本尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block
            or (not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING', False))
            or manifest.order_id != order.pk or manifest.finalized_at or order.settled_at):
        raise SettlementError('转单退款后的结算版本尚未开放')
    from .points_transfer_refund_evidence import _verify_prior_settlement
    from .segment_settlement import _hash, _points_transfer_terms, _terms, _validate_records
    try:
        posting = PointsNegotiationPosting.objects.select_for_update().get(
            points_refund__redemption__order=order)
        prior = posting.participants['facts']['prior_settlement']
        _verify_prior_settlement(order, prior)
    except (PointsNegotiationPosting.DoesNotExist, KeyError, TypeError, ValidationError) as exc:
        raise SettlementError('退款前结算凭证待核对') from exc
    verified_plan, verified_funding = _points_transfer_terms(order)
    if (verified_funding is None or _terms(order, verified_plan, points_funding=verified_funding) != terms
            or _terms(order, plan, points_funding=verified_funding) != terms):
        raise SettlementError('退款后整单补资或结算方案已变化')
    plan = verified_plan
    if (manifest.plan != prior['plan'] or manifest.plan_hash != prior['plan_hash']
            or terms.get('points_funding', {}).get('refund_posting_id') != posting.pk
            or OrderSegmentSettlementRevision.objects.filter(manifest=manifest).exists()):
        raise SettlementError('退款前后结算版本不一致')
    people = {person.account_id: person for person in plan.persons}
    if len(people) != len(plan.persons):
        raise SettlementError('退款后的逐人收益重复')
    for paid in prior['people']:
        person = people.get(paid['account_id'])
        if (not person or paid['account_id'] in posting.proposal.affected_accounts
                or person.income != paid['amount'] or list(person.segment_ids) != paid['segment_ids']):
            raise SettlementError('退款后的方案改变了已结算人员收益')
    from .points_transfer_paid_addons import unchanged
    try:
        unchanged(prior, terms.get('points_funding', {}).get('addons', []))
    except ValidationError as exc:
        raise SettlementError('退款后的方案改变了已结算加购收益') from exc
    revised_hash = _hash(terms)
    # A caller may catch a validation error while keeping its outer atomic
    # block alive. Never leave a half-written version in that transaction.
    with transaction.atomic():
        OrderSegmentSettlementRevision.objects.create(manifest=manifest, posting=posting,
            before_plan_hash=manifest.plan_hash, after_plan=terms, after_plan_hash=revised_hash)
        manifest.plan, manifest.plan_hash = terms, revised_hash
        manifest.save(update_fields=['plan', 'plan_hash'])
        try:
            _verify_prior_settlement(order, prior)
            return _validate_records(order, manifest, plan)
        except ValidationError as exc:
            raise SettlementError('退款后的结算版本凭证异常') from exc
