"""Capture the transfer facts needed to reconcile a future points refund.

This is an isolated-test building block, not an authorization or a refund.
The caller must persist the returned facts together with the agreed proposal
inside the posting transaction and verify both before using them as evidence.
"""
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction
from .models import (Order, OrderOriginalAttribution, OrderOriginalShareTransfer,
                     OrderParticipationSegment, OrderSegmentFinancialCoverage,
                     OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis)
from .points_checkout_test import require_isolated_checkout
from .points_transfer_refund_preview import preview_locked


def _guard():
    from .points_transfer_refund_access import permits, require_refund_or_isolated
    formal = permits(phase='refund') or permits(phase='settlement')
    if not formal:
        require_refund_or_isolated()
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING', False))
            or current_store() is None or not transaction.get_connection().in_atomic_block):
        raise ValidationError('积分转单退款履约凭证尚未开放')


def _time(value):
    return value.isoformat() if value is not None else None


def _facts(order):
    return _read_facts(order, lock=True)


def _facts_current_read(order, receipt_id):
    """Receipt-ledger use only: current reads without locking another order."""
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _require_current_unlocked_reads
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    return _read_facts(order, lock=False)


def _facts_without_receipt_locked(order):
    """Current facts for a bound order with no allocation at all."""
    from django.db.models import Q
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .financial_lock import lock_existing_order_money, lock_order_money
    from .models import OrderPointsFundingAllocation
    from .points_business_access import permits
    connection = transaction.get_connection()
    store = current_store()
    if (connection.vendor != 'mysql' or not connection.in_atomic_block or not store
            or store.database_alias != connection.alias
            or not any(permits(order, phase=phase, kind=kind)
                       for phase, kind in (('payment', 'addon'), ('refund', 'addon'),
                                           ('refund', 'transfer'), ('report', 'transfer'), ('settlement', 'transfer'),
                                           ('transfer', 'transfer')))):
        raise ValidationError('无到账的订单资金事实须在本单授权事务内核验')
    if not Order.objects.select_for_update().filter(pk=order.pk).exists():
        raise ValidationError('无到账的订单不存在')
    if permits(order, phase='report', kind='transfer'):
        lock_existing_order_money()
    else:
        lock_order_money()
    if OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).exists():
        raise ValidationError('订单已有到账分配，须使用到账用途凭证')
    _require_current_unlocked_reads()
    return _read_facts(order, lock=False)


def current_read_matches_history(order, receipt_id, frozen, boundary):
    """Match a frozen transfer episode without treating later segments as old facts."""
    return _matches_history(_facts_current_read(order, receipt_id), order, frozen, boundary)


def _matches_history(current, order, frozen, boundary):
    from django.utils.dateparse import parse_datetime
    if not isinstance(frozen, dict) or not boundary:
        return False
    dynamic = {'segments', 'transfers', 'attribution_status', 'coverage_status', 'coverage_review_reason'}
    if any(current.get(key) != value for key, value in frozen.items() if key not in dynamic):
        return False
    old_transfers, new_transfers = frozen.get('transfers'), current.get('transfers')
    old_segments, new_segments = frozen.get('segments'), current.get('segments')
    if not all(isinstance(value, list) for value in (old_transfers, new_transfers, old_segments, new_segments)):
        return False
    transfers = {item.get('id'): item for item in new_transfers if isinstance(item, dict)}
    segments = {item.get('id'): item for item in new_segments if isinstance(item, dict)}
    if any(not isinstance(item, dict) or transfers.get(item.get('id')) != item for item in old_transfers):
        return False
    try:
        old_transfer_ids = {item['id'] for item in old_transfers}
        old_segment_ids = {item['id'] for item in old_segments}
        if (any(item['id'] not in old_transfer_ids and parse_datetime(item['created_at']) <= boundary
                for item in new_transfers)
                or any(item['id'] not in old_segment_ids and parse_datetime(item['accepted_at']) <= boundary
                       for item in new_segments)):
            return False
        for old in old_segments:
            actual = segments.get(old['id'])
            if not actual:
                return False
            changed = {'started_at', 'start_source', 'transfer_released_at', 'pre_service_released',
                       'original_share', 'retained_basis'}
            if any(actual.get(key) != value for key, value in old.items() if key not in changed):
                return False
            later_out = next((item for item in new_transfers if item['id'] not in old_transfer_ids
                              and item['previous_segment_id'] == old['id']), None)
            if later_out:
                if (parse_datetime(later_out['created_at']) <= boundary
                        or old['original_share'] != later_out['original_share_before']
                        or actual['original_share'] != later_out['retained_share']
                        or old['retained_basis'] != later_out['basis_before']
                        or actual['retained_basis'] != later_out['retained_basis']):
                    return False
            elif (old['original_share'] != actual['original_share']
                  or old['retained_basis'] != actual['retained_basis']):
                return False
            if old.get('started_at'):
                if (old['started_at'], old.get('start_source')) != (actual.get('started_at'), actual.get('start_source')):
                    return False
            elif actual.get('started_at') and parse_datetime(actual['started_at']) <= boundary:
                return False
            if old.get('transfer_released_at'):
                if old['transfer_released_at'] != actual.get('transfer_released_at'):
                    return False
            elif actual.get('transfer_released_at') and parse_datetime(actual['transfer_released_at']) <= boundary:
                return False
            if old.get('pre_service_released') and not actual.get('pre_service_released'):
                return False
            if not old.get('pre_service_released') and actual.get('pre_service_released'):
                released = OrderParticipationSegment.objects.filter(pk=old['id']).values_list('released_at', flat=True).first()
                if not released or released <= boundary:
                    return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _read_facts(order, *, lock):
    def query(manager):
        return manager.select_for_update() if lock else manager.all()
    attribution = query(OrderOriginalAttribution.objects).filter(order=order).first()
    coverage = (query(OrderSegmentFinancialCoverage.objects).filter(attribution=attribution).first()
                if attribution else None)
    if not attribution or not coverage:
        raise ValidationError('转单履约资金凭证缺失')
    segments = list(query(OrderParticipationSegment.objects).filter(attribution=attribution).order_by('pk'))
    snapshots = {row.segment_id: row for row in query(OrderSegmentFinancialSnapshot.objects)
                 .filter(segment__attribution=attribution)}
    transfers = list(query(OrderOriginalShareTransfer.objects)
                     .filter(previous_segment__attribution=attribution).order_by('pk'))
    bases = {row.transfer_id: row for row in query(OrderSegmentTransferBasis.objects)
             .filter(transfer__in=transfers)}
    assignments = list(query(order.providers).order_by('pk'))
    by_account = {row.provider_account_id: row for row in assignments}
    if (not segments or not transfers or len(snapshots) != len(segments) or len(bases) != len(transfers)
            or len(by_account) != len(assignments) or None in by_account):
        raise ValidationError('转单履约或人员凭证不完整')
    outgoing = {row.previous_segment_id: row for row in transfers}
    if len(outgoing) != len(transfers):
        raise ValidationError('转单履约凭证重复')
    rows = []
    for segment in segments:
        snap, assignment = snapshots.get(segment.pk), by_account.get(segment.provider_account_id)
        if not snap or not assignment or not assignment.provider_id:
            raise ValidationError('转单陪玩或抽成凭证缺失')
        rows.append({'id': segment.pk, 'account_id': segment.provider_account_id, 'assignment_id': assignment.pk,
            'provider_id': assignment.provider_id, 'slot': segment.slot, 'original_share': segment.original_share,
            'accepted_at': _time(segment.accepted_at), 'started_at': _time(segment.started_at),
            'start_source': segment.start_source,
            'pre_service_released': bool(segment.released_at and not segment.started_at and segment.pk not in outgoing),
            # Existing transfer-out times are immutable; a current segment's
            # release can legitimately be set by a later agreed termination.
            'transfer_released_at': _time(segment.released_at) if segment.pk in outgoing else None,
            'snapshot_id': snap.pk, 'incoming_basis': snap.incoming_basis, 'retained_basis': snap.retained_basis,
            'commission_type': snap.commission_type, 'commission_rate': snap.commission_rate,
            'commission_fixed': snap.commission_fixed, 'fixed_rule_total': snap.fixed_rule_total,
            'commission_source': snap.commission_source, 'income_at_entry': snap.income_at_entry,
            'game_commission_snapshot': snap.game_commission_snapshot})
    events = []
    for item in transfers:
        basis = bases[item.pk]
        events.append({'id': item.pk, 'previous_segment_id': item.previous_segment_id,
            'next_segment_id': item.next_segment_id, 'original_share_before': item.original_share_before,
            'retained_share': item.retained_share, 'transferred_share': item.transferred_share,
            'operator_id': item.operator_id, 'agreement': item.agreement, 'created_at': _time(item.created_at),
            'basis_id': basis.pk, 'basis_before': basis.basis_before, 'retained_basis': basis.retained_basis,
            'transferred_basis': basis.transferred_basis, 'remainder_numerator': basis.remainder_numerator,
            'fixed_fee_store_shortfall': basis.fixed_fee_store_shortfall})
    return {'store_slug': current_store().slug, 'order_id': order.pk, 'customer_account_id': order.customer_account_id,
        'customer_id': order.customer_id, 'business_cycle': order.business_cycle,
        'order_original_amount': order.original_amount, 'paid_amount': order.amount,
        'boss_discount': order.boss_discount, 'promo_discount': order.promo_discount,
        'coupon_discount': order.coupon_discount, 'inviter_id': order.inviter_id,
        'inviter_account_id': order.inviter_account_id,
        'inviter_commission_rate': order.inviter_commission_rate_snapshot,
        'attribution_id': attribution.pk, 'original_amount': attribution.original_amount,
        'rounding_surplus': attribution.rounding_surplus, 'attribution_status': attribution.status,
        'coverage_id': coverage.pk, 'deal_amount': coverage.deal_amount, 'coverage_source': coverage.source,
        'coverage_status': coverage.status, 'coverage_review_reason': coverage.review_reason,
        'segments': rows, 'transfers': events}


def digest(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode()).hexdigest()


def _prior_settlement(order):
    """Snapshot already credited people, after preview has reconciled them.

    This is evidence only; it does not permit a refund or change a settlement.
    A later settlement-version transition must explicitly preserve this data.
    """
    from .models import OrderSegmentSettlementManifest, OrderSegmentPersonSettlement
    from .segment_settlement import _hash
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not manifest:
        return None
    if manifest.finalized_at or manifest.plan_hash != _hash(manifest.plan):
        raise ValidationError('已结算人员的原结算快照异常')
    rows = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(manifest=manifest).order_by('pk'))
    if not rows:
        raise ValidationError('原结算快照缺少已结算人员')
    result = {'manifest_id': manifest.pk, 'plan': deepcopy(manifest.plan), 'plan_hash': manifest.plan_hash,
        'people': [{'id': row.pk, 'account_id': row.account_id, 'amount': row.amount,
            'segment_ids': deepcopy(row.segment_ids), 'report_versions': deepcopy(row.report_versions),
            'income_transaction_id': row.income_transaction_id, 'deposit_debit_id': row.deposit_debit_id,
            'deposit_credit_id': row.deposit_credit_id, 'settled_at': _time(row.settled_at)} for row in rows]}
    if manifest.plan.get('points_funding', {}).get('addons'):
        from .points_transfer_paid_addons import snapshot_rows
        result['addon_people'] = snapshot_rows(manifest)
    return result


def _verify_prior_settlement(order, prior):
    """Recheck the old manifest and ledger; do not treat a hash as proof."""
    from django.utils.dateparse import parse_datetime
    from .models import (OrderSegmentPersonSettlement, OrderSegmentSettlementManifest,
                         OrderSegmentSettlementRevision)
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not isinstance(prior, dict) or not manifest or prior.get('manifest_id') != manifest.pk:
        raise ValidationError('退款前已结算人员或原结算快照发生变化')
    if (not isinstance(prior.get('plan'), dict) or prior.get('plan_hash') != _hash(prior['plan'])
            or not isinstance(prior.get('people'), list) or not prior['people']
            or not isinstance(manifest.plan, dict)
            or manifest.plan_hash != _hash(manifest.plan)):
        raise ValidationError('退款前结算快照摘要异常')
    rows = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(manifest=manifest).order_by('pk'))
    actual = [{'id': row.pk, 'account_id': row.account_id, 'amount': row.amount,
        'segment_ids': row.segment_ids, 'report_versions': row.report_versions,
        'income_transaction_id': row.income_transaction_id, 'deposit_debit_id': row.deposit_debit_id,
        'deposit_credit_id': row.deposit_credit_id, 'settled_at': _time(row.settled_at)} for row in rows]
    paid_ids = [item.get('id') for item in prior['people'] if isinstance(item, dict)]
    if (len(paid_ids) != len(prior['people']) or any(type(pk) is not int or pk <= 0 for pk in paid_ids)
            or len(set(paid_ids)) != len(paid_ids) or paid_ids != sorted(paid_ids)
            or [item for item in actual if item['id'] in paid_ids] != prior['people']):
        raise ValidationError('退款前已结算人员或原结算流水发生变化')
    if 'addon_people' in prior:
        from .points_transfer_paid_addons import verify as verify_paid_addons
        from django.utils import timezone
        verify_paid_addons(order, prior, cutoff=timezone.now())
    revised = manifest.plan != prior['plan'] or manifest.plan_hash != prior['plan_hash']
    if not revised:
        if actual != prior['people'] or manifest.finalized_at:
            raise ValidationError('退款前结算清单出现额外记录')
        first = OrderSegmentSettlementRevision.objects.filter(manifest=manifest).select_related('posting').order_by('pk').first()
        if not first and manifest.voluntary_payment_revisions.exists():
            from .models import OrderPointsFundingAllocation
            from .points_voluntary_paid_evidence import verify_revisions_current_read
            from django.core.exceptions import ValidationError as DjangoValidationError
            witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order).order_by('pk').first()
            earliest = manifest.voluntary_payment_revisions.order_by('created_at', 'pk').first()
            if not witness or not earliest:
                raise ValidationError('加购付款结算缺少到账见证')
            try:
                verify_revisions_current_read(manifest, start_plan=earliest.before_plan,
                    start_hash=earliest.before_plan_hash, receipt_id=witness.receipt_id)
            except DjangoValidationError as exc:
                raise ValidationError('加购付款结算历史异常') from exc
        if first:
            original = first.posting.participants.get('facts', {}).get('prior_settlement') if isinstance(
                first.posting.participants, dict) else None
            if not original:
                from .models import OrderPointsTransferFollowupFundingRevision
                followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=first.posting).first()
                original = followup.prior_manifest if followup else None
            if not original or original == prior:
                raise ValidationError('退款前结算版本缺少独立原始凭证')
            _verify_prior_settlement(order, original)
    else:
        from .points_transfer_refund_access import permits
        if not (permits(order, phase='refund') or permits(order, phase='settlement')
                or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING', False)):
            raise ValidationError('退款后的结算版本尚未开放')
        revisions = list(OrderSegmentSettlementRevision.objects.select_for_update().filter(manifest=manifest)
                         .select_related('posting__proposal', 'posting__points_refund__refund').order_by('pk')[:3])
        prior_hash = prior['plan_hash']
        from .points_transfer_paid_addons import unchanged
        for index, revision in enumerate(revisions):
            posting = revision.posting
            if revision.before_plan_hash != prior_hash and manifest.addon_revisions.exists():
                from .models import OrderPointsFundingAllocation
                from .points_voluntary_paid_evidence import verify_revisions_current_read
                from django.core.exceptions import ValidationError as DjangoValidationError
                witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order).order_by('pk').first()
                if not witness:
                    raise ValidationError('交错结算历史缺少到账见证')
                start_plan = revisions[index - 1].after_plan if index else prior['plan']
                after_at = revisions[index - 1].created_at if index else None
                try:
                    _, prior_hash = verify_revisions_current_read(manifest, start_plan=start_plan,
                        start_hash=prior_hash, receipt_id=witness.receipt_id,
                        after=after_at, before=revision.created_at)
                except DjangoValidationError as exc:
                    raise ValidationError('交错加购结算历史异常') from exc
            recorded_prior = posting.participants.get('facts', {}).get('prior_settlement') if isinstance(
                posting.participants, dict) else None
            if index == 0 and not recorded_prior:
                from .models import OrderPointsTransferFollowupFundingRevision
                followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=posting).first()
                recorded_prior = followup.prior_manifest if followup else None
            if (not posting or posting.proposal.case.order_id != order.pk
                    or not isinstance(posting.participants, dict)
                    or (index == 0 and recorded_prior != prior)
                    or revision.before_plan_hash != prior_hash or revision.after_plan_hash != _hash(revision.after_plan)
                    or revision.created_at < posting.points_refund.refund.created_at
                    or set(posting.proposal.affected_accounts).intersection(item['account_id'] for item in prior['people'])):
                raise ValidationError('退款后的结算版本或已付人员依据异常')
            unchanged(prior, revision.after_plan.get('points_funding', {}).get('addons', []))
            prior_hash = revision.after_plan_hash
        from .models import PointsTransferZeroSettlementRevision
        zero_revision = PointsTransferZeroSettlementRevision.objects.filter(manifest=manifest).select_related(
            'termination__proposal__case', 'termination__prior_posting', 'termination__assignment',
            'termination__segment', 'termination__operator', 'manifest').first()
        if zero_revision:
            from .points_transfer_zero_settlement import verify_revision
            prior_hash = verify_revision(zero_revision, before_hash=prior_hash)
            unchanged(prior, zero_revision.after_plan.get('points_funding', {}).get('addons', []))
        addon_revised = bool(zero_revision and manifest.addon_revisions.exists())
        if addon_revised:
            from .models import OrderPointsFundingAllocation
            from .points_transfer_zero_addon_settlement import verify_revisions_current_read
            from django.core.exceptions import ValidationError as DjangoValidationError
            witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order).order_by('pk').first()
            if not witness:
                raise ValidationError('加购结算历史缺少真实到账见证')
            try:
                prior_hash = verify_revisions_current_read(manifest, zero_revision, receipt_id=witness.receipt_id)
            except DjangoValidationError as exc:
                raise ValidationError('零退款后的加购结算历史异常') from exc
            unchanged(prior, manifest.plan.get('points_funding', {}).get('addons', []))
        elif revisions and not zero_revision and (manifest.addon_revisions.exists()
                                                    or manifest.voluntary_payment_revisions.exists()):
            from .models import OrderPointsFundingAllocation
            from .points_voluntary_paid_evidence import verify_revisions_current_read
            from django.core.exceptions import ValidationError as DjangoValidationError
            witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order).order_by('pk').first()
            if not witness:
                raise ValidationError('已付加购结算历史缺少到账见证')
            try:
                _, prior_hash = verify_revisions_current_read(manifest,
                    start_plan=revisions[-1].after_plan, start_hash=prior_hash, receipt_id=witness.receipt_id,
                    after=revisions[-1].created_at)
            except DjangoValidationError as exc:
                raise ValidationError('已付加购结算历史异常') from exc
            unchanged(prior, manifest.plan.get('points_funding', {}).get('addons', []))
            addon_revised = True
        elif not revisions and not zero_revision and manifest.voluntary_payment_revisions.exists():
            from .models import OrderPointsFundingAllocation
            from .points_voluntary_paid_evidence import verify_revisions_current_read
            from django.core.exceptions import ValidationError as DjangoValidationError
            witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order).order_by('pk').first()
            if not witness:
                raise ValidationError('加购付款历史缺少到账见证')
            try:
                _, prior_hash = verify_revisions_current_read(manifest, start_plan=prior['plan'],
                    start_hash=prior_hash, receipt_id=witness.receipt_id)
            except DjangoValidationError as exc:
                raise ValidationError('加购付款结算历史异常') from exc
            unchanged(prior, manifest.plan.get('points_funding', {}).get('addons', []))
            addon_revised = True
        if ((not revisions and not addon_revised) or len(revisions) > 2 or prior_hash != manifest.plan_hash
                or (not addon_revised and (zero_revision.after_plan if zero_revision else revisions[-1].after_plan) != manifest.plan)
                or [item['id'] for item in actual if item['id'] <= max(paid_ids)] != paid_ids):
            raise ValidationError('退款后的结算版本或已付人员依据异常')
    # Reconstruct only the paid people. Their approved report versions and
    # actual wallet/deposit ledgers are independently checked below.
    try:
        plan_people = {item['account_id']: item for item in manifest.plan['people']}
        if len(plan_people) != len(manifest.plan['people']):
            raise ValueError('duplicate people')
        people = []
        for row in actual:
            item = plan_people[row['account_id']]
            times = [parse_datetime(version['submitted_at']) for version in row['report_versions']]
            if (not times or any(value is None for value in times)
                    or item['income'] != row['amount'] or item['segment_ids'] != row['segment_ids']):
                raise ValueError('settlement mismatch')
            people.append(SimpleNamespace(account_id=row['account_id'], segment_ids=tuple(row['segment_ids']),
                income=row['amount'], freeze_until=max(times) + timedelta(hours=order.service_freeze_hours)))
        plan = SimpleNamespace(persons=people, inviter_income=manifest.plan['inviter_income'],
                               store_income=manifest.plan['store_income'])
        _validate_records(order, manifest, plan, finalized=bool(manifest.finalized_at))
    except (KeyError, TypeError, ValueError, SettlementError) as exc:
        raise ValidationError('退款前已结算人员的报单或资金流水异常') from exc


def capture_locked(case_id, *, refund_equivalent_units):
    """Verify the current preview before capturing facts; never post money."""
    _guard()
    calculation = preview_locked(case_id, refund_equivalent_units=refund_equivalent_units)
    order = Order.objects.select_for_update().get(pk=calculation['order_id'])
    facts = _facts(order)
    prior = _prior_settlement(order)
    if prior:
        facts['prior_settlement'] = prior
    return {'facts': facts, 'digest': digest(facts)}


def _verify_facts_locked(order_id, evidence, *, boundary=None):
    """Check immutable transfer facts only; this does not authorize a payout."""
    _guard()
    if (type(order_id) is not int or order_id <= 0 or not isinstance(evidence, dict)
            or set(evidence) != {'facts', 'digest'} or not isinstance(evidence['facts'], dict)
            or not isinstance(evidence['digest'], str) or evidence['facts'].get('order_id') != order_id
            or evidence['facts'].get('store_slug') != current_store().slug):
        raise ValidationError('转单履约凭证格式或摘要异常')
    try:
        matches = evidence['digest'] == digest(evidence['facts'])
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise ValidationError('转单履约凭证格式或摘要异常')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    from .points_transfer_refund_access import permits
    if ((permits(phase='refund') or permits(phase='settlement'))
            and not (permits(order, phase='refund') or permits(order, phase='settlement'))):
        raise ValidationError('转单凭证上下文不属于当前订单')
    facts = dict(evidence['facts'])
    prior = facts.pop('prior_settlement', None)
    current = _facts(order) if order else None
    if not order or (not _matches_history(current, order, facts, boundary) if boundary else current != facts):
        raise ValidationError('转单履约凭证与现有记录不一致')
    return order, evidence['facts'], prior


def verify_locked(order_id, evidence, *, boundary=None):
    """Verify immutable transfer facts and the earlier settlement state.

    Consent, refund assets, fund releases and the segment clocks remain the
    responsibility of the corresponding full posting/settlement verifiers.
    """
    order, facts, prior = _verify_facts_locked(order_id, evidence, boundary=boundary)
    if prior is not None:
        from .models import PointsTransferZeroSettlementRevision
        zero_revision = PointsTransferZeroSettlementRevision.objects.filter(manifest__order=order).first()
        if boundary is not None and zero_revision and zero_revision.manifest.addon_revisions.exists():
            # Replay only what existed at this refund boundary.  The outer
            # event verifies its own newer release and settlement versions.
            from .models import PointsNegotiationPosting
            from .points_transfer_paid_addons import verify_first_refund_receipt
            posting = PointsNegotiationPosting.objects.filter(
                points_refund__redemption__order=order, created_at__lte=boundary).order_by('created_at', 'pk').first()
            if not posting:
                raise ValidationError('首次退款已付人员历史凭证缺失')
            verify_first_refund_receipt(order, posting, prior, before=boundary)
        else:
            _verify_prior_settlement(order, prior)
    else:
        from .models import OrderSegmentSettlementRevision
        revisions = OrderSegmentSettlementRevision.objects.filter(manifest__order=order)
        if boundary:
            revisions = revisions.filter(created_at__lte=boundary)
        if revisions.exists():
            raise ValidationError('已结算人员的退款前凭证缺失')
    return facts


def reconstruct_funding_locked(order_id, evidence, *, refund_id, through_transfer_id, boundary=None):
    """Rebuild a pre-refund transfer boundary after an asset return.

    The caller must still establish that the evidence was captured by an
    accepted proposal and persist it atomically with the actual posting. This
    calculation is not a receipt release or evidence of provider recovery.
    """
    _guard()
    if (type(refund_id) is not int or refund_id <= 0
            or type(through_transfer_id) is not int or through_transfer_id < 0):
        raise ValidationError('退款或转单资金版本无效')
    facts = verify_locked(order_id, evidence, boundary=boundary)
    return _reconstruct_from_verified_facts(order_id, facts, refund_id=refund_id,
                                             through_transfer_id=through_transfer_id)


def _reconstruct_from_verified_facts(order_id, facts, *, refund_id, through_transfer_id):
    """Calculate from facts already checked by the caller; never authorize payment."""
    _guard()
    if (type(order_id) is not int or order_id <= 0 or type(refund_id) is not int or refund_id <= 0
            or type(through_transfer_id) is not int or through_transfer_id < 0
            or not isinstance(facts, dict) or facts.get('order_id') != order_id):
        raise ValidationError('退款或转单资金版本无效')
    from .points_service_refund import verify_refund_history_locked
    from .points_funding_plan import plan_service_funding
    from .points_transfer_history import HistoricTransferFunding
    from .settlement import ProviderShare
    proof = verify_refund_history_locked(refund_id)
    if proof.redemption.order_id != order_id:
        raise ValidationError('退款不属于该转单订单')
    events, rows = facts['transfers'], facts['segments']
    ids = {event['id'] for event in events}
    if not events or (through_transfer_id and through_transfer_id not in ids):
        raise ValidationError('转单资金版本不存在')
    if through_transfer_id:
        boundary = next(event for event in events if event['id'] == through_transfer_id)
        if any(event['id'] > through_transfer_id and event['created_at'] == boundary['created_at'] for event in events):
            raise ValidationError('请选择整次转单完成后的资金版本')
    incoming = {event['next_segment_id']: event for event in events}
    outgoing = {event['previous_segment_id']: event for event in events}
    if len(incoming) != len(events) or len(outgoing) != len(events):
        raise ValidationError('转单履约关联重复')
    shares, lines = [], []
    for row in rows:
        arrived, left = incoming.get(row['id']), outgoing.get(row['id'])
        if arrived and arrived['id'] > through_transfer_id:
            continue
        ended = left is not None and left['id'] <= through_transfer_id
        if ended:
            basis = left['retained_basis']
        elif row['pre_service_released']:
            basis = 0
        else:
            basis = row['incoming_basis']
        if row['commission_type'] == 'FIXED':
            fee = ((row['fixed_rule_total'] * basis // facts['deal_amount']) if ended else
                   0 if row['pre_service_released'] else row['commission_fixed'])
        else:
            fee = basis * row['commission_rate'] // 100
        if fee > basis:
            raise ValidationError('转单历史抽成超过成交份额')
        income = basis - fee
        shares.append(ProviderShare(settlement_base=basis, commission_type=row['commission_type'],
            commission_rate=row['commission_rate'], commission_fixed=fee if row['commission_type'] == 'FIXED' else 0,
            provider_income=income))
        lines.append((row['id'], row['account_id'], basis, fee, income))
    order = Order.objects.select_for_update().get(pk=order_id)
    from django.db.models import Sum
    sources = {item['kind']: item['total'] for item in order.funding_allocations.values('kind')
               .annotate(total=Sum('amount'))}
    receipt = proof.redemption
    rate = facts['inviter_commission_rate'] if facts['inviter_id'] or facts['inviter_account_id'] else 0
    funding = plan_service_funding(deal_units=receipt.deal_units, point_discount_units=receipt.discount_units,
        shares=shares, inviter_rate=rate, principal_units=sources.get('PREPAID', 0),
        gift_units=sources.get('GIFT', 0), compensation_units=sources.get('COMPENSATION', 0))
    return HistoricTransferFunding(through_transfer_id, tuple(lines), funding)


def verify_transfer_revisions_after_refund_locked(order_id, evidence, *, refund_id):
    """Reconcile transfer releases created before this verified refund.

    This does not verify later refund releases or available receipt balances;
    those require the complete posting and cross-use evidence independently.
    """
    _guard()
    from .models import PointsNegotiationPosting
    posting = PointsNegotiationPosting.objects.filter(points_refund__refund_id=refund_id).first()
    if not posting:
        raise ValidationError('转单退款记账凭证缺失')
    if isinstance(evidence, list):
        # The first refund was recorded before any transfer existed. Its
        # participant snapshot is the original source; subsequent transfers
        # have their own post-refund versions and releases.
        from .models import OrderOriginalShareTransfer, OrderPointsTransferFundingRevision
        from .points_pretransfer_refund_source import verify_participants_locked
        if (posting.proposal.case.order_id != order_id or evidence != posting.participants
                or OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order_id=order_id, created_at__lte=posting.created_at).exists()
                or OrderPointsTransferFundingRevision.objects.filter(
                    order_id=order_id, created_at__lte=posting.created_at).exists()):
            raise ValidationError('首次退款前转单资金来源异常')
        verify_participants_locked(posting)
        return {}
    facts = verify_locked(order_id, evidence, boundary=posting.created_at)
    return _verify_revisions_from_verified_facts(order_id, facts, refund_id=refund_id)


def _verify_revisions_from_verified_facts(order_id, facts, *, refund_id):
    """Reconcile transfer releases using facts the caller has already verified."""
    _guard()
    if (type(order_id) is not int or order_id <= 0 or type(refund_id) is not int or refund_id <= 0
            or not isinstance(facts, dict) or facts.get('order_id') != order_id):
        raise ValidationError('退款或转单资金版本无效')
    from django.db.models import Q
    from .models import OrderAddonFundingRelease, OrderPointsFundingAllocation, OrderPointsTransferFundingRevision
    from .addon_funding_history import combined_at
    from .addon_funding_release import verify_run
    from .points_funding_allocation import PointsFundingAllocationError, plan_rebalance
    from .points_funding_plan import PointsFundingPlanError
    from .points_service_refund import verify_refund_history_locked
    from .points_transfer_revision import PendingRevision, _digest, _verify_allocation, revision_tables_exist
    from django.utils.dateparse import parse_datetime
    proof = verify_refund_history_locked(refund_id)
    order = Order.objects.select_for_update().get(pk=order_id)
    if (proof.redemption.order_id != order.pk or not revision_tables_exist()
            or any(not parse_datetime(event['created_at']) or
                   parse_datetime(event['created_at']) > proof.refund.created_at for event in facts['transfers'])):
        raise ValidationError('退款前转单资金凭证不完整')
    owner = Q(redemption__order=order) | Q(service_order=order)
    allocations, last_id = [], 0
    while True:
        page = list(OrderPointsFundingAllocation.objects.select_for_update().filter(owner, pk__gt=last_id)
                    .select_related('receipt', 'redemption').order_by('pk')[:256])
        if not page:
            break
        allocations.extend(page)
        last_id = page[-1].pk
    try:
        completed_at = {row.pk: _verify_allocation(order, row) for row in allocations}
    except PointsFundingPlanError as exc:
        raise ValidationError('转单补资原分配凭证异常') from exc
    revisions = list(OrderPointsTransferFundingRevision.objects.select_for_update().filter(order=order)
                     .order_by('last_transfer_id'))
    by_event = {row.last_transfer_id: row for row in revisions}
    if len(by_event) != len(revisions):
        raise ValidationError('转单资金版本重复')
    groups = []
    for event in facts['transfers']:
        if groups and event['created_at'] == groups[-1][-1]['created_at']:
            groups[-1].append(event)
        else:
            groups.append([event])
    used, released, previous = set(), {}, 0
    for group in groups:
        end = group[-1]
        end_time = parse_datetime(end['created_at'])
        if end_time is None:
            raise ValidationError('转单资金时间异常')
        present = [row for row in allocations if row.created_at <= end_time]
        revision = by_event.get(end['id'])
        if present:
            if any(completed_at[row.pk] > end_time for row in present):
                raise ValidationError('转单前平台补资尚未完成')
            before = _reconstruct_from_verified_facts(order_id, facts, refund_id=refund_id,
                                                      through_transfer_id=previous)
            after = _reconstruct_from_verified_facts(order_id, facts, refund_id=refund_id,
                                                     through_transfer_id=end['id'])
            addon_releases = list(OrderAddonFundingRelease.objects.filter(
                allocation__in=present, run__created_at__lte=end_time).select_related('run'))
            for run in {item.run_id: item.run for item in addon_releases}.values():
                if run.created_at == end_time:
                    raise ValidationError('加购补资释放与转单时序无法区分')
                verify_run(run)
            addon_before = {}
            for part in addon_releases:
                addon_before[part.allocation_id] = addon_before.get(part.allocation_id, 0) + part.amount
            rows = [[row.pk, row.receipt_id, row.amount, released.get(row.pk, 0) + addon_before.get(row.pk, 0)]
                    for row in present]
            try:
                before_funding = combined_at(order, before.funding, end_time, segment_shares=before.segment_shares)
                after_funding = combined_at(order, after.funding, end_time, segment_shares=after.segment_shares)
                if sum(row[2] - row[3] for row in rows) > before_funding['additional_store_funding_units']:
                    raise ValidationError('转单前补资超过当时缺口')
                planned = plan_rebalance(allocations=[(row[0], row[2], row[3]) for row in rows],
                                         required=after_funding['additional_store_funding_units'])
            except (PointsFundingAllocationError, PointsFundingPlanError) as exc:
                raise ValidationError('转单补资历史计算异常') from exc
            expected = PendingRevision(end['id'], previous, rows, before_funding, after_funding, planned.release_parts)
            if (not revision or revision.created_at < end_time or revision.created_at > proof.refund.created_at
                    or revision.previous_transfer_id != previous or revision.allocations != rows
                    or revision.before_funding != expected.before_funding or revision.after_funding != expected.after_funding
                    or revision.evidence_hash != _digest(order.pk, expected)):
                raise ValidationError('退款前转单资金版本不一致')
            actual = list(revision.releases.order_by('-allocation_id'))
            if ([(row.allocation_id, row.amount) for row in actual]
                    != [(part.allocation_id, part.amount) for part in planned.release_parts]
                    or any(not revision.created_at <= row.created_at <= proof.refund.created_at for row in actual)):
                raise ValidationError('退款前转单补资释放流水不一致')
            for part in planned.release_parts:
                released[part.allocation_id] = released.get(part.allocation_id, 0) + part.amount
            used.add(revision.pk)
        elif revision:
            # An add-on refund before this transfer can create a real funding
            # version even when no receipt was allocated and nothing released.
            from .models import OrderAddonRefund
            if not OrderAddonRefund.objects.filter(order=order, created_at__lt=end_time).exists():
                raise ValidationError('无已分配补资的转单不应有资金版本')
            before = _reconstruct_from_verified_facts(order_id, facts, refund_id=refund_id,
                                                      through_transfer_id=previous)
            after = _reconstruct_from_verified_facts(order_id, facts, refund_id=refund_id,
                                                     through_transfer_id=end['id'])
            try:
                before_funding = combined_at(order, before.funding, end_time, segment_shares=before.segment_shares)
                after_funding = combined_at(order, after.funding, end_time, segment_shares=after.segment_shares)
            except PointsFundingPlanError as exc:
                raise ValidationError('无到账转单前的加购资金历史异常') from exc
            expected = PendingRevision(end['id'], previous, [], before_funding, after_funding, ())
            if (revision.created_at < end_time or revision.created_at > proof.refund.created_at
                    or revision.previous_transfer_id != previous or revision.allocations != []
                    or revision.before_funding != before_funding or revision.after_funding != after_funding
                    or revision.evidence_hash != _digest(order.pk, expected) or revision.releases.exists()):
                raise ValidationError('无到账转单资金版本不一致')
            used.add(revision.pk)
        previous = end['id']
    if used != {row.pk for row in revisions}:
        raise ValidationError('订单存在无法归属的转单资金版本')
    return released
