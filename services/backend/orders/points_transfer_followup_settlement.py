"""Verify funding and manage settlement versions for two transferred refunds."""
from dataclasses import asdict

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import Order, OrderPointsFundingRelease, OrderPointsTransferFollowupFundingRevision
from .points_checkout_test import require_isolated_checkout
from .points_funding_plan import PointsFundingPlanError
from .settlement import SettlementError


def prepare_locked(order_id, *, now=None, allow_recorded=False, allow_finalized=False):
    from .points_transfer_refund_access import permits
    formal = permits(phase='refund') or permits(phase='settlement')
    try:
        if not formal:
            require_isolated_checkout()
    except ValidationError as exc:
        raise SettlementError('转单追加退款结算尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block or type(order_id) is not int or order_id <= 0
            or (not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING', False))
            or (not formal and allow_recorded and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False))
            or (allow_finalized and not allow_recorded)):
        raise SettlementError('转单追加退款结算尚未开放')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if formal and (not order or not any(permits(order, phase=phase) for phase in ('refund', 'settlement'))):
        raise SettlementError('转单追加退款结算范围尚未开放')
    if not order:
        raise SettlementError('订单不存在')
    from .points_transfer_followup_settlement_plan import build_locked
    from .points_funding_evidence import verify_order_funding, verified_order_allocations_locked
    from .points_transfer_refund_evidence import verify_transfer_revisions_after_refund_locked
    from .segment_settlement import _terms
    try:
        plan, funding = build_locked(order_id, now=now, allow_recorded=allow_recorded, allow_finalized=allow_finalized)
        verified = verify_order_funding(order_id, allow_finalized=allow_finalized,
                                        allow_followup_recorded=allow_recorded)
        revision = OrderPointsTransferFollowupFundingRevision.objects.select_for_update().select_related(
            'first_posting__points_refund', 'posting__proposal').get(posting__points_refund__redemption__order=order)
        first, second = revision.first_posting, revision.posting
        allocations = verified_order_allocations_locked(order)
        if allow_recorded and isinstance(first.participants, dict):
            from .points_transfer_refund_evidence import _verify_facts_locked, _verify_revisions_from_verified_facts
            _, facts, _ = _verify_facts_locked(order_id, first.participants, boundary=first.created_at)
            transfer_releases = _verify_revisions_from_verified_facts(order_id, facts,
                refund_id=first.points_refund.refund_id)
        else:
            transfer_releases = verify_transfer_revisions_after_refund_locked(order_id, first.participants,
                refund_id=first.points_refund.refund_id)
    except (ValidationError, PointsFundingPlanError, OrderPointsTransferFollowupFundingRevision.DoesNotExist,
            OrderPointsTransferFollowupFundingRevision.MultipleObjectsReturned) as exc:
        raise SettlementError('追加退款的补资或结算凭证待核对') from exc
    if not verified.fully_funded:
        raise SettlementError('门店补资尚未足额，暂不能结算')
    from .points_transfer_followup_scope import addon_snapshot, addon_chain_before
    from .points_transfer_refund_access import has_paid_addon_before
    had_addons = has_paid_addon_before(order, second.created_at)
    addon_history = addon_snapshot(order, first) if had_addons else None
    if not addon_history and had_addons:
        addon_history = addon_chain_before(order, first, second.created_at,
                                           pending_snapshot=revision.pending_handover_snapshot)
    from .points_transfer_followup_addon_version import latest_run_locked
    from django.utils.dateparse import parse_datetime
    latest = latest_run_locked(order, revision)
    from .models import PointsFollowupTransferRevision
    post_transfer = PointsFollowupTransferRevision.objects.filter(order=order).first()
    if post_transfer and latest and latest.created_at < post_transfer.created_at:
        latest = None
    addon_boundary = second.created_at
    if post_transfer and not latest:
        addon_history = post_transfer.snapshot.get('addon_history')
        addon_boundary = post_transfer.transfer.created_at
    if latest:
        addon_history = latest.points_followup_snapshot['addon_history']
        addon_boundary = parse_datetime(latest.points_followup_snapshot.get('captured_at', ''))
        if not addon_boundary:
            raise SettlementError('转单后续加购资金事件时间待核对')
    addon_parts, addon_totals = [], {}
    if addon_history:
        from .points_transfer_followup_scope import supported_addon
        from .models import OrderAddonPayment
        from .addon_payment import verify_receipt, verify_order_debit_links
        from .addon_handover import assert_no_pending_handover, current_responsibilities
        from .addon_completion import verify_accepted
        verify_order_debit_links(order, table_exists=True, lock=True)
        eligible = {(segment_id, person.account_id) for person in plan.persons for segment_id in person.segment_ids}
        keys = ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
                'provider_units', 'inviter_units', 'recovery_units')
        addon_totals = {key: 0 for key in keys}
        addon_refunds = []
        for expected in addon_history:
            payment = OrderAddonPayment.objects.select_for_update().select_related('intent', 'buyer_transaction__wallet').filter(
                pk=expected['payment_id'], intent__order=order).first()
            if not payment:
                raise SettlementError('追加退款加购付款凭证缺失')
            verify_receipt(payment)
            assert_no_pending_handover(payment)
            handover, responsibilities = current_responsibilities(payment, lock=True)
            refunds = list(payment.refund_records.order_by('created_at', 'pk'))
            if (any(item.created_at >= addon_boundary for item in refunds)
                    or [item.pk for item in refunds] != expected['refund_ids']
                    or sum(part['basis'] for part in responsibilities) != expected['paid_units']
                    or sum(part['income'] for part in responsibilities) != expected['provider_units']
                    or (handover.pk if handover else None) != expected['handover_id']
                    or (not responsibilities and (expected['paid_units'] or expected['provider_units']
                        or sum(item.amount for item in refunds) != payment.intent.amount))):
                raise SettlementError('追加退款加购责任与付款快照不一致')
            addon_refunds.extend(refunds)
            for part in responsibilities:
                if (part['segment_id'], part['provider_id']) not in eligible:
                    raise SettlementError('追加退款加购不属于可结算履约段')
                endings = [posting for posting in (first, second) if part['provider_id'] in posting.proposal.affected_accounts
                           and (not posting.proposal.affected_segment_ids
                                or part['segment_id'] in posting.proposal.affected_segment_ids)]
                if len(endings) > 1:
                    raise SettlementError('追加退款加购终止段不唯一')
                if endings and part['is_current']:
                    verify_accepted(order, payment, endings[0], part)
                addon_parts.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                    'provider_account_id': part['provider_id'], 'provider_income_units': part['income']})
            for key in keys:
                addon_totals[key] += expected[key]
        # A provider can receive the same add-on again after retaining an
        # earlier share. Keep those earnings in one payment/segment posting.
        merged = {}
        for part in addon_parts:
            key = (part['payment_id'], part['segment_id'], part['provider_account_id'])
            if key not in merged:
                merged[key] = dict(part)
            else:
                merged[key]['provider_income_units'] += part['provider_income_units']
        addon_parts = list(merged.values())
        from .points_transfer_paid_addons import unchanged
        try:
            unchanged(revision.prior_manifest, addon_parts)
        except ValidationError as exc:
            raise SettlementError('追加退款改变了已结算人的加购收益') from exc
    items = []
    from .models import PointsPostRefundTransferRelease, PointsFollowupTransferRelease
    from .addon_funding_release import addon_release_totals
    addon_released = addon_release_totals(allocations)
    for allocation in allocations:
        returned = sum(OrderPointsFundingRelease.objects.filter(allocation=allocation).values_list('amount', flat=True))
        post_transfer = sum(PointsPostRefundTransferRelease.objects.filter(allocation=allocation,
            revision__created_at__lt=second.created_at).values_list('amount', flat=True))
        followup_transfer = sum(PointsFollowupTransferRelease.objects.filter(
            allocation=allocation).values_list('amount', flat=True))
        net = (allocation.amount - transfer_releases.get(allocation.pk, 0) - returned - post_transfer
               - followup_transfer - addon_released.get(allocation.pk, 0))
        if net < 0:
            raise SettlementError('追加退款的补资释放超过原分配')
        items.append({'id': allocation.pk, 'receipt_id': allocation.receipt_id, 'amount': net})
    calculation = asdict(funding)
    for key in ('allocated_units', 'release_units', 'remaining_units'):
        calculation.pop(key)
    calculation['additional_store_funding_units'] = funding.required_units
    terms = {'calculation': calculation, 'allocations': items, 'refund_posting_id': first.pk,
             'followup_posting_id': second.pk}
    if addon_history:
        from .addon_main_segment_deduction import verified_deductions
        deductions = verified_deductions(order, before=addon_boundary)
        terms.update(addons=addon_parts, addon_history=addon_history, addon_totals=addon_totals,
                     addon_refund_ids=[item.pk for item in addon_refunds],
                     main_segment_deductions=[[key, value] for key, value in sorted(deductions.items())])
    if (verified.required != funding.required_units or verified.allocated != sum(row['amount'] for row in items)
            or funding.net_principal_units + funding.planned_provider_external_units + verified.allocated
                != sum(person.income for person in plan.persons)
                   + sum(part['provider_income_units'] for part in addon_parts)
                   + plan.inviter_income + plan.store_income):
        raise SettlementError('追加退款后的补资与收益不守恒')
    people = {person.account_id: person for person in plan.persons}
    if len(people) != len(plan.persons):
        raise SettlementError('追加退款后的陪玩收益重复')
    prior = revision.prior_manifest
    for paid in prior['people'] if prior else []:
        person = people.get(paid['account_id'])
        if (not person or paid['account_id'] in second.proposal.affected_accounts
                or person.income != paid['amount'] or list(person.segment_ids) != paid['segment_ids']):
            raise SettlementError('追加退款后的方案改变了已结算收益')
    return plan, _terms(order, plan, points_funding=terms), prior


def verify_manifest_locked(order, manifest, terms, prior):
    """Verify the actual version chain against both independent refund facts."""
    from .models import OrderSegmentSettlementRevision
    from .segment_settlement import _hash
    from .points_transfer_followup_revision import _verify_followup_revision_facts_locked
    from .points_transfer_refund_access import permits
    formal = any(permits(order, phase=phase) for phase in ('refund', 'settlement'))
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False))
            or not transaction.get_connection().in_atomic_block or manifest.order_id != order.pk
            or manifest.plan != terms or manifest.plan_hash != _hash(terms)):
        raise SettlementError('追加退款后的结算清单不一致')
    evidence = OrderPointsTransferFollowupFundingRevision.objects.select_for_update().select_related(
        'posting__points_refund__refund', 'first_posting__points_refund__refund').get(
            posting__points_refund__redemption__order=order)
    try:
        _verify_followup_revision_facts_locked(evidence)
    except ValidationError as exc:
        raise SettlementError('追加退款后的资金凭证待核对') from exc
    if evidence.prior_manifest != prior:
        raise SettlementError('追加退款前的结算快照已变化')
    revisions = list(OrderSegmentSettlementRevision.objects.select_for_update().filter(manifest=manifest)
                     .select_related('posting__points_refund__refund').order_by('pk'))
    if not prior:
        if revisions or manifest.created_at < evidence.posting.points_refund.refund.created_at:
            raise SettlementError('追加退款后的新结算清单时序异常')
        # A person can be paid after this manifest was created while a timely
        # case concerning someone else remains open.  Any later paid-refund
        # version must still prove the original credits and its real refund.
        from .segment_addon_settlement_revision import verify_revisions
        verify_revisions(manifest)
        return
    if manifest.pk != prior['manifest_id']:
        raise SettlementError('追加退款前的结算清单已变化')
    later = [item for item in revisions if item.posting_id == evidence.posting_id]
    addon_versions = list(manifest.addon_revisions.select_for_update().order_by('version'))
    payment_versions = list(manifest.voluntary_payment_revisions.select_for_update().order_by('created_at', 'pk'))
    handover_versions = list(manifest.paid_handover_revisions.select_for_update().order_by('created_at', 'pk'))
    completion_versions = list(manifest.paid_completion_revisions.select_for_update().order_by('created_at', 'pk'))
    if addon_versions or payment_versions or handover_versions or completion_versions:
        from .segment_addon_settlement_revision import verify_revisions
        verify_revisions(manifest)
    after_changes = sorted((item for item in addon_versions + payment_versions + handover_versions + completion_versions
                            if later and item.created_at > later[0].created_at), key=lambda item: item.created_at)
    after_followup = after_changes[0].before_plan if after_changes else terms
    if (len(later) != 1 or later[0].before_plan_hash != prior['plan_hash']
            or later[0].after_plan != after_followup or later[0].after_plan_hash != _hash(after_followup)
            or later[0].created_at < evidence.posting.points_refund.refund.created_at
            or later[0].created_at < manifest.created_at):
        raise SettlementError('追加退款后的结算版本不一致')
    first_participants = evidence.first_posting.participants
    first_prior = (first_participants['facts'].get('prior_settlement')
                   if isinstance(first_participants, dict) else None)
    earlier = [item for item in revisions if item.pk != later[0].pk]
    before_addons = sorted((item for item in addon_versions + payment_versions + handover_versions + completion_versions
                            if item.created_at < later[0].created_at), key=lambda item: item.created_at)
    if first_prior and (first_prior['plan'] != prior['plan'] or first_prior['plan_hash'] != prior['plan_hash']):
        if (len(earlier) != 1 or earlier[0].posting_id != evidence.first_posting_id
                or earlier[0].before_plan_hash != first_prior['plan_hash']
                or earlier[0].after_plan != (before_addons[0].before_plan if before_addons else prior['plan'])
                or earlier[0].after_plan_hash != _hash(earlier[0].after_plan)
                or (before_addons and before_addons[-1].after_plan != prior['plan'])
                or earlier[0].created_at < evidence.first_posting.points_refund.refund.created_at
                or earlier[0].pk >= later[0].pk):
            raise SettlementError('首次退款的结算版本不一致')
    elif earlier:
        raise SettlementError('订单存在多余结算版本')


def transition_locked(order, manifest, plan, terms):
    """Switch a partly paid manifest without changing any historical credit."""
    from .points_transfer_refund_access import permits
    formal = permits(order, phase='settlement') or permits(order, phase='refund')
    if not formal:
        require_isolated_checkout()
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False))
            or not transaction.get_connection().in_atomic_block or manifest.finalized_at or order.settled_at):
        raise SettlementError('追加退款后的结算版本尚未开放')
    from .models import OrderSegmentSettlementRevision
    from .segment_settlement import _hash, _validate_records
    verified_plan, verified_terms, prior = prepare_locked(order.pk, allow_recorded=True)
    if (not prior or manifest.order_id != order.pk or manifest.pk != prior['manifest_id']
            or manifest.plan != prior['plan'] or manifest.plan_hash != prior['plan_hash']
            or terms != verified_terms or [(p.account_id, p.segment_ids, p.income) for p in plan.persons]
                != [(p.account_id, p.segment_ids, p.income) for p in verified_plan.persons]):
        raise SettlementError('追加退款前后结算方案已变化')
    posting_id = terms['points_funding']['followup_posting_id']
    if OrderSegmentSettlementRevision.objects.filter(posting_id=posting_id).exists():
        raise SettlementError('追加退款结算版本已存在')
    with transaction.atomic():
        OrderSegmentSettlementRevision.objects.create(manifest=manifest, posting_id=posting_id,
            before_plan_hash=manifest.plan_hash, after_plan=terms, after_plan_hash=_hash(terms))
        manifest.plan, manifest.plan_hash = terms, _hash(terms)
        manifest.save(update_fields=['plan', 'plan_hash'])
        verify_manifest_locked(order, manifest, terms, prior)
        return _validate_records(order, manifest, verified_plan), verified_plan
