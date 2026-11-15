"""Auditable second-refund funding for transferred points orders.

This internal building block is restricted to disposable test databases. It
does not close an aftersale or permit a subsequent provider settlement.
"""
import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from wallet.models import Transaction
from .models import (OrderPointsFundingAllocation, OrderPointsFundingRelease,
                     OrderPointsTransferFollowupFundingRevision)
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError
from .points_funding_plan import PointsFundingPlanError
from .points_funding_revision import plan_funding_revision, plan_unpaid_funding_revision
from .points_negotiation_preview import funding_after_refund


def _guard():
    from .points_transfer_refund_access import permits
    if any(permits(phase=phase) for phase in ('refund', 'settlement')):
        if not transaction.get_connection().in_atomic_block:
            raise ValidationError('转单追加退款资金须在门店事务内核验')
        return
    require_isolated_checkout()
    from django.db import connections
    directory = connections['default'].settings_dict
    if (not transaction.get_connection().in_atomic_block
            or directory.get('ENGINE') != 'django.db.backends.sqlite3'
            or directory.get('NAME') not in (':memory:', 'file:memorydb_default?mode=memory&cache=shared')
            or not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING', False)):
        raise ValidationError('转单追加退款的资金版本尚未开放')


def _json(value):
    return json.loads(json.dumps(value, sort_keys=True, separators=(',', ':')))


def _digest(posting, first_id, manifest, allocations, releases, funding, revision, prior_required, pending_snapshot=None):
    payload = [posting.pk, posting.terms_hash, posting.points_refund_id, first_id, manifest,
               allocations, releases, funding, revision, prior_required, pending_snapshot or []]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _verify_paid_manifest(order, snapshot, *, cutoff, allow_later=False):
    if snapshot is None:
        return
    from django.utils.dateparse import parse_datetime
    from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=snapshot['manifest_id'], order=order)
    try:
        if (not isinstance(snapshot['plan'], dict) or snapshot['plan_hash'] != _hash(snapshot['plan'])
                or not isinstance(manifest.plan, dict) or manifest.plan_hash != _hash(manifest.plan)):
            raise ValueError('settlement snapshot hash mismatch')
        terms = {item['account_id']: item for item in snapshot['plan']['people']}
        if len(terms) != len(snapshot['plan']['people']):
            raise ValueError('duplicate people')
        people = []
        records = {row.pk: row for row in OrderSegmentPersonSettlement.objects.select_for_update().filter(
            manifest=manifest).select_related('income_transaction', 'deposit_debit', 'deposit_credit')}
        for paid in snapshot['people']:
            person = terms[paid['account_id']]
            times = [parse_datetime(item['submitted_at']) for item in paid['report_versions']]
            record = records[paid['id']]
            transactions = [tx for tx in (record.income_transaction, record.deposit_debit, record.deposit_credit) if tx]
            actual = {'id': record.pk, 'account_id': record.account_id, 'amount': record.amount,
                'segment_ids': record.segment_ids, 'report_versions': record.report_versions,
                'income_transaction_id': record.income_transaction_id, 'deposit_debit_id': record.deposit_debit_id,
                'deposit_credit_id': record.deposit_credit_id,
                'settled_at': record.settled_at.isoformat() if record.settled_at else None}
            if (actual != paid or not times or any(value is None for value in times)
                    or paid['amount'] != person['income'] or paid['segment_ids'] != person['segment_ids']
                    or not record.settled_at or record.settled_at > cutoff
                    or any(not tx.created_at or tx.created_at > record.settled_at for tx in transactions)):
                raise ValueError('paid share mismatch')
            people.append(SimpleNamespace(account_id=paid['account_id'], segment_ids=tuple(paid['segment_ids']),
                income=paid['amount'], freeze_until=max(times) + timedelta(hours=order.service_freeze_hours)))
        snapshot_ids = [paid['id'] for paid in snapshot['people']] if allow_later else None
        _validate_records(order, manifest, SimpleNamespace(persons=people), snapshot_ids=snapshot_ids,
                          paid_addon_snapshot=snapshot if snapshot.get('addon_people') is not None else None,
                          paid_addon_cutoff=cutoff)
        from .points_transfer_paid_addons import verify as verify_paid_addons
        verify_paid_addons(order, snapshot, cutoff=cutoff)
    except (KeyError, TypeError, ValueError, SettlementError) as exc:
        raise ValidationError('转单追加退款的已付收益或报单流水异常') from exc


def _verify_manifest_terms(snapshot, first, history, facts, *, cutoff=None):
    if snapshot is None:
        return
    from collections import defaultdict
    plan = snapshot['plan']
    previous = facts.get('prior_settlement')
    before_first = bool(previous and previous.get('manifest_id') == snapshot['manifest_id']
                        and previous.get('plan') == plan and previous.get('plan_hash') == snapshot['plan_hash'])
    if not before_first and plan.get('points_funding', {}).get('refund_posting_id') != first.pk:
        raise ValidationError('追加退款前结算方案版本不一致')
    by_id = {item['id']: item for item in facts['segments']}
    outgoing = {item['previous_segment_id'] for item in facts['transfers']}
    grouped = defaultdict(list)
    for line in history.segment_shares:
        part = by_id.get(line[0])
        if not part or (line[2] and not part['started_at']):
            raise ValidationError('追加退款前履约份额异常')
        if part['started_at'] and (part['id'] not in outgoing or part['original_share'] > 0):
            grouped[line[1]].append(line[0])
    if 'addon_people' in snapshot:
        addon_parts = plan.get('points_funding', {}).get('addons', [])
        if not isinstance(addon_parts, list) or not cutoff:
            raise ValidationError('已付加购结算依据缺失')
        for part in addon_parts:
            segment = by_id.get(part.get('segment_id'))
            account_id = part.get('provider_account_id')
            if not segment or segment['account_id'] != account_id or not segment['started_at']:
                raise ValidationError('已付加购履约段异常')
            if segment['id'] not in grouped[account_id]:
                grouped[account_id].append(segment['id'])
        from .addon_main_segment_deduction import verified_deductions
        actual_deductions = verified_deductions(first.proposal.case.order, before=cutoff)
        saved_rows = plan.get('points_funding', {}).get('main_segment_deductions', [])
        if not isinstance(saved_rows, list):
            raise ValidationError('已付主单收益扣减依据异常')
        saved = dict(saved_rows)
        paid_ids = {segment_id for item in snapshot['people'] for segment_id in item['segment_ids']}
        if any(saved.get(segment_id, 0) != actual_deductions.get(segment_id, 0) for segment_id in paid_ids):
            raise ValidationError('追加退款改变了已付履约段收益')
        old_people = {item['account_id']: item for item in plan.get('people', [])}
        old_segments = {item['segment_id']: item for item in plan.get('segments', [])}
        lines = {line[0]: dict(zip(('segment_id', 'account_id', 'basis', 'fee', 'income'), line))
                 for line in history.segment_shares}
        shares = {item.account_id: item for item in first.shares.all()}
        for paid in snapshot['people']:
            account = paid['account_id']
            segment_ids = grouped.get(account, [])
            income = sum(lines[segment_id]['income'] - actual_deductions.get(segment_id, 0)
                         for segment_id in segment_ids)
            if not before_first and account in shares:
                income -= shares[account].pending_deduction
            if (old_people.get(account) != {'account_id': account, 'segment_ids': segment_ids, 'income': income}
                    or any(old_segments.get(segment_id) != lines.get(segment_id) for segment_id in segment_ids)):
                raise ValidationError('追加退款改变了已结算人的履约份额')
        return
    shares = {item.account_id: item for item in first.shares.all()}
    people = []
    for account_id, segment_ids in sorted(grouped.items()):
        income = (shares[account_id].retained_income if not before_first and account_id in shares else
                  sum(line[4] for line in history.segment_shares if line[1] == account_id))
        people.append({'account_id': account_id, 'segment_ids': segment_ids, 'income': income})
    segments = [dict(zip(('segment_id', 'account_id', 'basis', 'fee', 'income'), line))
                for line in history.segment_shares]
    if (plan.get('deal_amount') != facts['deal_amount'] or plan.get('segments') != segments
            or plan.get('people') != people):
        raise ValidationError('追加退款前结算方案与履约或首次退款不一致')


def verify_locked(row):
    _guard()
    from .points_negotiation_execution import verify_posting_locked
    from .points_negotiation_termination import verify_termination_locked
    row = OrderPointsTransferFollowupFundingRevision.objects.select_for_update().select_related(
        'posting__proposal__case__order', 'posting__points_refund__refund', 'first_posting__points_refund__refund').get(pk=row.pk)
    second = verify_posting_locked(row.posting)
    first = verify_termination_locked(row.first_posting, allow_verified_later_final=True)
    return _verify_funding_from_verified_postings(row, first, second)


def _verify_funding_from_verified_postings(row, first, second, *, facts_only=False):
    """Recompute funding after the caller has verified both refund postings."""
    _guard()
    from .points_transfer_refund_access import permits
    if facts_only and not (permits(phase='refund') or permits(phase='settlement')
            or getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING', False)):
        raise ValidationError('转单追加退款的历史资金核对尚未开放')
    from .points_transfer_refund_evidence import (_prior_settlement, reconstruct_funding_locked,
        verify_transfer_revisions_after_refund_locked, _verify_facts_locked, _reconstruct_from_verified_facts,
        _verify_revisions_from_verified_facts)
    from .points_transfer_revision import _verify_allocation
    order, proof = second.proposal.case.order, second.points_refund
    addon_history = None
    from .points_transfer_refund_access import has_paid_addon_before
    had_addons = has_paid_addon_before(order, second.created_at)
    if had_addons:
        from .points_transfer_followup_scope import addon_snapshot, addon_chain_before
        addon_history = addon_snapshot(order, first)
        if addon_history is None:
            addon_history = addon_chain_before(order, first, second.created_at,
                                               pending_snapshot=row.pending_handover_snapshot)
    if permits(phase='refund') or permits(phase='settlement'):
        if (not any(permits(order, phase=phase) for phase in ('refund', 'settlement'))
                or (had_addons and not addon_history)
                or (row.prior_manifest is not None
                    and (not isinstance(row.prior_manifest, dict)
                         or not isinstance(row.prior_manifest.get('people'), list)
                         or not row.prior_manifest['people']))):
            raise ValidationError('转单追加退款正式资金范围尚未开放')
    first_proof = first.points_refund
    from .models import PointsPostRefundTransferRevision
    versions = list(PointsPostRefundTransferRevision.objects.filter(order=order,
        created_at__lt=second.created_at).order_by('pk'))
    version = versions[-1] if versions else None
    if version:
        from .points_post_refund_transfer import verify_locked as verify_transfer_version
        from .points_transfer_refund_evidence import digest as transfer_digest
        for item in versions:
            verify_transfer_version(item)
        expected_participants = {'facts': version.snapshot['facts'], 'digest': transfer_digest(version.snapshot['facts'])}
    else:
        expected_participants = first.participants
    first_pretransfer = isinstance(first.participants, list) and version is not None
    if first_pretransfer:
        from .points_pretransfer_refund_source import verify_participants_locked
        verify_participants_locked(first)
    if ((not isinstance(first.participants, dict) and not first_pretransfer) or second.participants != expected_participants
            or second.proposal.points_terms.prior_refund_id != first_proof.pk
            or first_proof.redemption_id != proof.redemption_id or order.pk != first.proposal.case.order_id
            or ((order.refund_records.filter(pk__lte=proof.refund_id).count() != 2) if facts_only
                else (order.refund_records.count() != 2)) or proof.refund_id <= first_proof.refund_id
            or row.created_at < proof.refund.created_at
            or (not facts_only and _prior_settlement(order) != row.prior_manifest)
            or row.evidence_hash != _digest(second, first.pk, row.prior_manifest, row.allocations,
                row.releases, row.funding, row.revision, row.prior_required_units, row.pending_handover_snapshot)):
        raise ValidationError('转单追加退款资金版本或原结算凭证不一致')
    from .points_transfer_followup_pending import verify_snapshot
    verify_snapshot(second, row.pending_handover_snapshot)
    _verify_paid_manifest(order, row.prior_manifest, cutoff=proof.refund.created_at, allow_later=facts_only)
    prior_people = row.prior_manifest['people'] if row.prior_manifest else []
    if facts_only:
        from .models import OrderSegmentPersonSettlement
        historical_ids = set(OrderSegmentPersonSettlement.objects.filter(manifest__order=order,
            settled_at__lte=proof.refund.created_at).values_list('pk', flat=True))
        if historical_ids != {item['id'] for item in prior_people}:
            raise ValidationError('追加退款之前的已付人员凭证缺失或不完整')
    from .points_transfer_followup_scope import verify as verify_segment_scope
    verify_segment_scope(first, second, version)
    if (set(second.proposal.affected_accounts).intersection(item['account_id'] for item in prior_people)
            or (not facts_only and not row.prior_manifest and order.transactions.filter(
                tx_type__in=(Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME),
                status=Transaction.Status.SUCCESS).exists())):
        raise ValidationError('追加退款范围与已结算人员不一致')
    if (not isinstance(row.allocations, list) or any(not isinstance(item, list) or len(item) != 4
            or any(type(value) is not int for value in item) or item[0] <= 0 or item[1] <= 0
            or item[2] <= 0 or not 0 <= item[3] <= item[2] for item in row.allocations)
            or row.allocations != sorted(row.allocations, key=lambda item: item[0])
            or len({item[0] for item in row.allocations}) != len(row.allocations)):
        raise ValidationError('转单追加退款原补资快照异常')
    ids = [item[0] for item in row.allocations]
    owner = Q(redemption__order=order) | Q(service_order=order)
    all_allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(owner)
                           .select_related('receipt', 'redemption').order_by('pk'))
    allocations = [item for item in all_allocations if item.pk in ids]
    extra = [item for item in all_allocations if item.pk not in ids]
    if ([item.pk for item in allocations] != ids or any(
            item.created_at < row.created_at or (ids and item.pk <= max(ids))
            or OrderPointsFundingRelease.objects.filter(allocation=item).exists() for item in extra)):
        raise ValidationError('转单追加退款原分配或后续补资时序异常')
    try:
        for allocation in allocations:
            if _verify_allocation(order, allocation) > proof.refund.created_at:
                raise ValidationError('追加退款前补资尚未完成')
        facts = second.participants['facts']
        if facts_only:
            _, facts, _ = _verify_facts_locked(order.pk, second.participants, boundary=second.created_at)
            history = _reconstruct_from_verified_facts(order.pk, facts, refund_id=first_proof.refund_id,
                                                        through_transfer_id=facts['transfers'][-1]['id'])
            if first_pretransfer:
                transfer_releases = verify_transfer_revisions_after_refund_locked(
                    order.pk, first.participants, refund_id=first_proof.refund_id)
            else:
                first_facts = first.participants['facts']
                transfer_releases = _verify_revisions_from_verified_facts(order.pk, facts,
                    refund_id=first_proof.refund_id) if not version else _verify_revisions_from_verified_facts(
                        order.pk, first_facts, refund_id=first_proof.refund_id)
        else:
            history = reconstruct_funding_locked(order.pk, second.participants, refund_id=first_proof.refund_id,
                through_transfer_id=facts['transfers'][-1]['id'], boundary=second.created_at)
            transfer_releases = verify_transfer_revisions_after_refund_locked(
                order.pk, first.participants, refund_id=first_proof.refund_id)
    except (PointsFundingPlanError, KeyError, IndexError) as exc:
        raise ValidationError('转单追加退款的历史补资依据异常') from exc
    _verify_manifest_terms(row.prior_manifest, first, history, facts, cutoff=second.created_at)
    actual_allocations = []
    for item in allocations:
        first_releases = list(OrderPointsFundingRelease.objects.filter(allocation=item, posting=first))
        if OrderPointsFundingRelease.objects.filter(allocation=item).exclude(posting__in=(first, second)).exists():
            raise ValidationError('存在无法归属的补资释放')
        from .models import PointsPostRefundTransferRelease
        post_releases = PointsPostRefundTransferRelease.objects.filter(allocation=item,
            revision__created_at__lt=second.created_at)
        from .models import OrderAddonFundingRelease
        from .addon_funding_release import verify_run as verify_addon_release
        addon_releases = list(OrderAddonFundingRelease.objects.filter(allocation=item,
            run__created_at__lt=second.created_at).select_related('run').order_by('pk'))
        for part in addon_releases:
            snapshot = addon_history
            prestart = bool(part.run.prestart_handoff_id and part.run.prestart_handoff.order_id == order.pk
                            and part.run.created_at < first.created_at)
            if not prestart and (not snapshot or not any(part.run.payment_id == entry['payment_id']
                    or (part.run.refund_id in entry['refund_ids'] and part.run.refund.payment_id == entry['payment_id'])
                    or (part.run.handover_id and part.run.handover.payment_id == entry['payment_id'])
                    for entry in snapshot)
                    or (part.run.created_at >= first.created_at and not part.run.refunded_main_snapshot)):
                raise ValidationError('转单追加退款加购释放范围异常')
            verify_addon_release(part.run, _skip_usage=True)
        used = (transfer_releases.get(item.pk, 0) + sum(part.amount for part in first_releases)
                + sum(post_releases.values_list('amount', flat=True))
                + sum(part.amount for part in addon_releases))
        actual_allocations.append([item.pk, item.receipt_id, item.amount, used])
    if actual_allocations != row.allocations:
        raise ValidationError('转单追加退款的原补资记录已变化')
    initial = history.funding
    first_shares, second_shares = list(first.shares.all()), list(second.shares.all())
    second_accounts = [item.account_id for item in second_shares]
    if (not first_shares or not second_shares or len(second_accounts) != len(set(second_accounts))
            or set(second_accounts) != set(second.proposal.affected_accounts)):
        raise ValidationError('转单追加退款的陪玩承担不完整')
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    common = dict(principal_units=initial.principal_units, gift_units=initial.gift_units,
        compensation_units=initial.compensation_units, provider_income_units=initial.provider_income_units,
        inviter_rate=rate, allocated_units=sum(item[2] - item[3] for item in row.allocations))
    prior = funding_after_refund(**common, returned_prepaid_units=first_proof.prepaid_units,
        returned_gift_units=first_proof.gift_units, returned_compensation_units=first_proof.compensation_units,
        pending_deduction_units=sum(item.pending_deduction for item in first_shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in first_shares),
        rounding_reserved_units=first.rounding_to_store_units)
    actual = funding_after_refund(**common, returned_prepaid_units=first_proof.prepaid_units + proof.prepaid_units,
        returned_gift_units=first_proof.gift_units + proof.gift_units,
        returned_compensation_units=first_proof.compensation_units + proof.compensation_units,
        pending_deduction_units=sum(item.pending_deduction for item in first_shares + second_shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in first_shares + second_shares),
        rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
    if had_addons:
        from .points_transfer_followup_scope import combine_addon
        if not addon_history:
            raise ValidationError('转单追加退款加购资金范围尚未开放')
        prior_deductions = [(item.account_id, item.pending_deduction,
            set(first.proposal.affected_segment_ids or [])) for item in first_shares]
        later_deductions = [(item.account_id, item.pending_deduction,
            set(second.proposal.affected_segment_ids or []),
            list(item.segment_deductions.values_list('segment_id', 'amount')) or None) for item in second_shares]
        prior, prior_addons = combine_addon(order, prior, second.created_at,
            history.segment_shares, facts['segments'], prior_deductions)
        actual, actual_addons = combine_addon(order, actual, second.created_at,
            history.segment_shares, facts['segments'], prior_deductions + later_deductions)
        if prior_addons != addon_history or actual_addons != prior_addons:
            raise ValidationError('转单追加退款加购历史快照不一致')
        if prior_people:
            from .points_transfer_paid_addons import unchanged, parts_before
            unchanged(row.prior_manifest, parts_before(order, addon_history, second.created_at))
    from .points_transfer_paid_addons import verify as verify_paid_addons
    paid_addon_units = verify_paid_addons(order, row.prior_manifest, cutoff=second.created_at)
    reservation = [(pk, amount - used) for pk, _, amount, used in row.allocations if amount > used]
    try:
        if prior_people:
            revision = plan_funding_revision(allocations=row.allocations, original_reservation=reservation,
                previous_required=prior.required_units,
                paid_provider_units=sum(item['amount'] for item in prior_people) + paid_addon_units,
                projected=actual)
        else:
            revision = plan_unpaid_funding_revision(allocations=row.allocations, original_reservation=None,
                                                    previous_required=prior.required_units, projected=actual)
    except PointsFundingAllocationError as exc:
        raise ValidationError('转单追加退款资金不守恒') from exc
    releases = [list(item) for item in revision.releases]
    recorded = list(OrderPointsFundingRelease.objects.filter(posting=second).order_by('-allocation_id')
                    .values_list('allocation_id', 'allocation__receipt_id', 'amount'))
    if (row.prior_required_units != prior.required_units or row.funding != _json(asdict(actual))
            or row.revision != _json(asdict(revision)) or row.releases != releases
            or recorded != [tuple(item) for item in releases]):
        raise ValidationError('转单追加退款资金版本或释放流水不一致')
    return actual, revision


def _verify_followup_revision_facts_locked(row):
    """Verify historical refund funding without authorizing later credits."""
    _guard()
    from .points_transfer_refund_access import permits
    if not (permits(phase='refund') or permits(phase='settlement')
            or getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING', False)):
        raise ValidationError('转单追加退款的历史资金核对尚未开放')
    from .points_negotiation_termination import _verify_transfer_termination_facts_locked
    from .points_transfer_followup_termination import _verify_followup_closure_facts_locked
    row = OrderPointsTransferFollowupFundingRevision.objects.select_for_update().select_related(
        'posting__proposal__case__order', 'posting__points_refund__refund', 'first_posting__points_refund__refund').get(pk=row.pk)
    if isinstance(row.first_posting.participants, list):
        from .points_negotiation_execution import _verify_posting_core_locked
        from .points_pretransfer_refund_source import verify_participants_locked
        from .points_negotiation_termination import _verify_termination_from_verified_posting
        first = _verify_posting_core_locked(row.first_posting)
        verify_participants_locked(first)
        first = _verify_termination_from_verified_posting(first, allow_verified_later_final=True, facts_only=True)
    else:
        first = _verify_transfer_termination_facts_locked(row.first_posting, allow_verified_later_final=True)
    second = _verify_followup_closure_facts_locked(row.posting)
    return _verify_funding_from_verified_postings(row, first, second, facts_only=True)


def record_locked(posting, first, calculation, prior_manifest):
    _guard()
    if OrderPointsTransferFollowupFundingRevision.objects.filter(posting=posting).exists():
        raise ValidationError('转单追加退款资金版本已存在')
    if OrderPointsFundingRelease.objects.filter(posting=posting).exists():
        raise ValidationError('转单追加退款已有未核对的补资释放')
    revision = _json(calculation['conditional_funding_revision'])
    funding = _json(calculation['conditional_funding'])
    allocations, releases = revision['allocations'], revision['releases']
    prior_required = calculation['current_required_units']
    with transaction.atomic():
        from .points_transfer_followup_pending import capture_locked
        pending_snapshot = capture_locked(posting)
        row = OrderPointsTransferFollowupFundingRevision.objects.create(posting=posting, first_posting=first,
            prior_manifest=prior_manifest, allocations=allocations, releases=releases, funding=funding, revision=revision,
            prior_required_units=prior_required, pending_handover_snapshot=pending_snapshot,
            evidence_hash=_digest(posting, first.pk, prior_manifest, allocations, releases, funding, revision,
                                  prior_required, pending_snapshot))
        OrderPointsFundingRelease.objects.bulk_create([OrderPointsFundingRelease(posting=posting,
            allocation_id=allocation_id, amount=amount) for allocation_id, _, amount in releases])
        from .points_transfer_refund_access import permits
        if permits(posting.proposal.case.order, phase='refund'):
            from types import SimpleNamespace
            from .points_transfer_followup_receipt_usage import facts
            from users.receipt_usage_sources import record_followup_release_locked, verify_allocations_locked
            first_allocation = OrderPointsFundingAllocation.objects.filter(
                pk__in=[item[0] for item in allocations], receipt__usage_version__isnull=False).first()
            if first_allocation:
                facts(SimpleNamespace(posting=posting, allocation=first_allocation, pk=0, amount=0))
                touched = set()
                for release in posting.funding_releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
                    if release.allocation.receipt.usage_version is not None:
                        record_followup_release_locked(release)
                        touched.add(release.allocation.receipt_id)
                for receipt_id in sorted(touched):
                    verify_allocations_locked(receipt_id)
        verify_locked(row)
        return row
