"""First-refund funding releases against immutable allocation evidence.

Order-side writers acquire receipt locks after the order and money locks.
Receipt-side readers verify source facts without acquiring order locks.
"""
from dataclasses import asdict
import hashlib
import json
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_database, current_store
from stores.db import connection, transaction
from .models import (OrderPointsFundingAction, OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsFundingReleaseEvidence,
                     OrderPointsSettlement, OrderSegmentFinancialSnapshot, PointsNegotiationPosting)
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_negotiation_execution import verify_posting_locked
from .points_negotiation_preview import funding_after_refund


def _paged(query):
    """Read every row in deterministic keyset pages under the caller's locks."""
    last = 0
    while True:
        page = list(query.filter(pk__gt=last).order_by('pk')[:256])
        if not page:
            return
        yield from page
        last = page[-1].pk


def _chunks(ids):
    ids = sorted(ids)
    for offset in range(0, len(ids), 256):
        yield ids[offset:offset + 256]


def release_tables_exist():
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    required = {OrderPointsFundingRelease._meta.db_table, OrderPointsFundingReleaseEvidence._meta.db_table}
    if required.issubset(tables):
        return True
    if (required.intersection(tables) or 'django_migrations' not in tables
            or MigrationRecorder.Migration.objects.using(current_database()).filter(
                app='orders', name='0131_orderpointsfundingreleaseevidence_and_more').exists()):
        raise DatabaseError('积分补资释放凭证表缺失，须先核对数据库')
    return False


def _digest(posting, allocations, provider_income, settlement_id, expected, addon_snapshot=None, prior_addon=None,
            *, sort_keys=False):
    payload = [posting.pk, posting.terms_hash, posting.points_refund_id, allocations,
               provider_income, settlement_id, expected]
    if addon_snapshot is not None:
        payload.append(addon_snapshot)
    if prior_addon:
        payload.append(prior_addon)
    return hashlib.sha256(json.dumps(payload, separators=(',', ':'), ensure_ascii=True,
                                     sort_keys=sort_keys).encode()).hexdigest()


def first_release_hash_matches(evidence, posting):
    """Check both the canonical hash and the encoding used for older first refunds."""
    facts = (posting, evidence.allocations, evidence.original_provider_income,
             evidence.original_settlement_id, evidence.expected_release,
             evidence.addon_funding_snapshot, evidence.prior_addon_release_snapshot)
    return (evidence.evidence_hash == _digest(*facts, sort_keys=True)
            or evidence.evidence_hash == _digest(*facts))


def _prior_addon_releases(allocations, boundary):
    from .models import OrderAddonFundingRelease
    from .addon_funding_release import verify_run
    rows = list(OrderAddonFundingRelease.objects.filter(allocation__in=allocations,
        run__created_at__lte=boundary).select_related('run__refund', 'run__handover__payment__intent').order_by('pk'))
    if any(row.run.created_at == boundary for row in rows):
        raise ValidationError('加购补资释放与主单退款时序无法区分')
    for run in {row.run_id: row.run for row in rows}.values():
        # The caller reconciles the receipt ledger after rebuilding the
        # historical release; do not recursively enter that same ledger.
        verify_run(run, _skip_usage=True)
    return [[row.pk, row.allocation_id, row.run_id, row.amount] for row in rows]


def _net_allocations(items, prior_addon):
    released = {}
    for _, allocation_id, _, amount in prior_addon:
        released[allocation_id] = released.get(allocation_id, 0) + amount
    result = [[pk, amount - released.get(pk, 0), receipt] for pk, amount, receipt in items]
    if any(row[1] < 0 for row in result) or set(released) - {item[0] for item in items}:
        raise ValidationError('加购补资释放超过原分配')
    return result, released


def _compute(posting, allocations, provider_income, *, include_addons=True, read_receipt_id=None):
    """Recompute using actual refund and debits, never a planned recovery."""
    order, proof = posting.proposal.case.order, posting.points_refund
    sources = list(order.funding_allocations.all())
    paid = {kind: sum(row.amount for row in sources if row.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    from .models import OrderOriginalShareTransfer
    if (isinstance(posting.participants, list) and OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).exists()):
        from .points_transfer_refund_access import permits
        from .points_pretransfer_refund_source import verify_participants_locked
        if read_receipt_id is None and not permits(order, phase='refund'):
            raise ValidationError('转单后的原退款资金须在本单事务内核对')
        rows = verify_participants_locked(posting, receipt_id=read_receipt_id)
        snapshots_by_id = {item.pk: item for item in OrderSegmentFinancialSnapshot.objects.filter(
            pk__in=[row['snapshot_id'] for row in rows]).select_related('segment')}
        if len(snapshots_by_id) != len(rows):
            raise ValidationError('原退款的收益快照缺失')
        snapshots = [snapshots_by_id[row['snapshot_id']] for row in rows]
    else:
        from .points_service_participation import service_snapshots
        snapshots = service_snapshots(order)
    shares = list(posting.shares.all())
    if (sum(paid.values()) != proof.redemption.paid_units
            or sum(item.income_at_entry for item in snapshots) != provider_income
            or len({item.segment.provider_account_id for item in snapshots}) != len(snapshots)):
        raise ValidationError('退款后的原付款或陪玩收益快照异常')
    external = sum(item.wallet_deduction + item.deposit_deduction for item in shares)
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    result = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=proof.prepaid_units,
        returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
        provider_income_units=provider_income, pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=external, inviter_rate=rate, allocated_units=sum(item[1] for item in allocations),
        rounding_reserved_units=posting.rounding_to_store_units)
    if not include_addons:
        return result, None
    from .models import OrderAddonPayment
    if OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=posting.created_at).exists():
        raise ValidationError('加购付款与主单退款时序无法区分')
    if not OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at__lt=posting.created_at).exists():
        return result, None
    from .addon_funding_history import combined_at
    from .refunded_addon_funding import combine_refunded_history
    from .points_funding_plan import PointsFundingPlanError
    try:
        pending = {row.account_id: row.pending_deduction for row in shares}
        retained = {row.segment.provider_account_id: row.income_at_entry - pending.get(row.segment.provider_account_id, 0)
                    for row in snapshots}
        segment_shares = [(row.segment_id, row.segment.provider_account_id, row.incoming_basis, 0,
                           retained[row.segment.provider_account_id]) for row in snapshots]
        history = combined_at(order, result, posting.created_at, segment_shares=segment_shares)
        return combine_refunded_history(result, history, main_retained_by_account=retained), history['addon_history']
    except PointsFundingPlanError as exc:
        raise ValidationError('主单退款与加购历史补资待核对') from exc


def verify_release_locked(evidence, *, _skip_usage=False):
    """Verify release history without assuming a provider's planned debit paid."""
    from .points_transfer_refund_access import permits
    from .points_business_access import permits as service_permits
    formal_transfer = permits(phase='refund')
    formal_service = (service_permits(phase='payment', kind='addon') or any(service_permits(phase=phase, kind=kind) for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement')))
    if not (formal_transfer or formal_service):
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block or current_store() is None:
        raise ValidationError('补资释放凭证必须在门店事务内核对')
    evidence = OrderPointsFundingReleaseEvidence.objects.select_related(
        'posting__proposal__case__order', 'posting__points_refund__redemption').get(pk=evidence.pk)
    if isinstance(evidence.posting.participants, dict):
        from .points_transfer_refund_release import verify_release_locked as verify_transfer_release
        return verify_transfer_release(evidence)
    evidence_order = evidence.posting.proposal.case.order
    bound = (service_permits(evidence_order, phase='payment', kind='addon') or any(
        service_permits(evidence_order, phase=phase, kind=kind)
        for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement')))
    historical_transfer = formal_transfer and permits(evidence_order, phase='refund')
    if (formal_transfer and not historical_transfer) or (formal_service and not bound and not historical_transfer):
        raise ValidationError('非转单退款不属于当前资金阶段')
    posting = verify_posting_locked(evidence.posting)
    terms = getattr(posting.proposal, 'points_terms', None)
    if terms and terms.prior_refund_id:
        raise ValidationError('后续退款须使用独立资金释放凭证')
    order = posting.proposal.case.order
    items = evidence.allocations
    if (not isinstance(items, list) or any(not isinstance(item, list) or len(item) != 3
            or any(type(value) is not int or value <= 0 for value in item) for item in items)
            or len({item[0] for item in items}) != len(items)
            or items != sorted(items, key=lambda item: item[0])
            or not first_release_hash_matches(evidence, posting)):
        raise ValidationError('补资释放快照异常')
    owner = Q(redemption__order=order) | Q(service_order=order)
    max_id = items[-1][0] if items else 0
    prior = list(OrderPointsFundingAllocation.objects.filter(owner, pk__lte=max_id).order_by('pk')
                 .values_list('pk', 'amount', 'receipt_id'))
    if [list(item) for item in prior] != items:
        raise ValidationError('补资释放原分配记录异常')
    allocations = list(OrderPointsFundingAllocation.objects.filter(pk__in=[item[0] for item in items]))
    prior_addon = _prior_addon_releases(allocations, posting.created_at)
    if prior_addon != evidence.prior_addon_release_snapshot:
        raise ValidationError('主单退款前的加购补资释放凭证已变化')
    net_items, addon_released = _net_allocations(items, prior_addon)
    # Recheck platform authorization without calling the order-level totals
    # helper, which would recurse back into this verifier.
    from stores.models import PlatformPointsFundingOperation
    from stores.platform_pass_repayment import RepaymentError, _claimed_receipt, _sha
    from stores.platform_points_funding import _verified_receipt
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_guard import capture_allocation_usage_locked, isolated_allocation_state_locked
    from .points_addon_receipt_usage import reuse_history_locked

    receipt_ids = {item[2] for item in items}
    ledger_enabled = getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
    # Several allocations can share a receipt. Verify its tenant ledger once
    # under the existing locks and still check each platform operation.
    locked_receipts = {}
    if ledger_enabled:
        locked_receipts = {receipt.pk: receipt for receipt in PassRefundRepaymentReceipt.objects.select_for_update()
                           .filter(pk__in=receipt_ids).order_by('pk')}
        if set(locked_receipts) != receipt_ids:
            raise ValidationError('补资释放原到账凭证缺失')
    verified_usage = {}
    for allocation in OrderPointsFundingAllocation.objects.filter(pk__in=[item[0] for item in items]).select_related('receipt'):
        key = allocation.request_key
        try:
            if not key.startswith('points:') or len(key) != 39:
                raise ValueError('invalid allocation key')
            operation_id = UUID(hex=key[7:])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError('补资释放原分配编号异常') from exc
        operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').filter(
            pk=operation_id, claim__store__slug=current_store().slug).first()
        action = OrderPointsFundingAction.objects.filter(request_key=key).first()
        expected_action = {'receipt_id': allocation.receipt_id, 'allocation_id': allocation.pk,
                           'order_id': order.pk, 'amount': allocation.amount}
        if (not operation or not operation.completed_at or operation.rejected_at or operation.voided_at
                or not action or operation.tenant_action_id != action.pk or action.allocation_id != allocation.pk
                or operation.claim.tenant_receipt_id != allocation.receipt_id
                or action.actor != str(operation.actor_id) or allocation.allocated_by != str(operation.actor_id)
                or action.result != expected_action or operation.payload_digest != _sha(json.dumps(
                    [str(operation.claim_id), order.pk, allocation.amount], separators=(',', ':')))):
            raise ValidationError('补资释放原平台分配凭证异常')
        receipt = locked_receipts.get(allocation.receipt_id, allocation.receipt)
        try:
            _claimed_receipt(operation.claim, current_store().slug, receipt)
            if ledger_enabled and receipt.pk not in verified_usage:
                with reuse_history_locked(order, {receipt.pk}):
                    verified_usage[receipt.pk] = capture_allocation_usage_locked(receipt)
            _verified_receipt(operation.claim, receipt, verified_usage=verified_usage.get(receipt.pk))
        except (RepaymentError, DjangoValidationError) as exc:
            raise ValidationError('补资释放原到账核实凭证异常') from exc
    settlement = OrderPointsSettlement.objects.filter(order=order).first()
    from .models import PointsFirstRefundPaidPersonEvidence
    paid_evidence = PointsFirstRefundPaidPersonEvidence.objects.filter(posting=posting).select_related(
        'person__settlement', 'person__segment').first()
    if paid_evidence:
        person = paid_evidence.person
        payload = [posting.pk, person.pk, person.amount, person.income_transaction_id,
                   paid_evidence.allocation_snapshot]
        if (not settlement or person.settlement_id != settlement.pk
                or person.segment.attribution.order_id != order.pk
                or paid_evidence.allocation_snapshot != net_items
                or paid_evidence.evidence_hash != hashlib.sha256(
                    json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
                or posting.shares.filter(account_id=person.segment.provider_account_id).exists()):
            raise ValidationError('首退前已付人员资金证据异常')
    if (evidence.original_settlement_id is not None
            and (not settlement or settlement.pk != evidence.original_settlement_id)):
        raise ValidationError('补资预留结算记录已变化，须先核对')
    if settlement and evidence.original_settlement_id is None and settlement.created_at < evidence.created_at:
        raise ValidationError('退款前的补资预留结算凭证缺失')
    if settlement and evidence.original_settlement_id is not None:
        uses = list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
        prior_people = settlement.persons.filter(settled_at__lt=evidence.created_at)
        if (uses != [(item[0], item[1]) for item in net_items if item[1]]
                or (settlement.finalized_at and settlement.finalized_at < evidence.created_at)):
            raise ValidationError('退款前的补资预留或收益结算异常')
        if prior_people.exists():
            from .points_first_paid_source import verify_current
            try:
                verify_current(posting, evidence)
            except DjangoValidationError as exc:
                raise ValidationError('首退前已付人员资金来源待核对') from exc
        elif paid_evidence:
            raise ValidationError('首退已付人员与原结算不一致')
    result, addon_snapshot = _compute(posting, net_items, evidence.original_provider_income)
    if evidence.addon_funding_snapshot != addon_snapshot:
        raise ValidationError('主单退款时的加购资金快照已变化')
    try:
        expected = plan_release(allocations=[(item[0], item[1], addon_released.get(item[0], 0)) for item in items],
                                amount=result.release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('补资释放分配异常') from exc
    rows = list(posting.funding_releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
    if (result.release_units != evidence.expected_release
            or rows != [(item.allocation_id, item.amount) for item in expected]):
        raise ValidationError('补资释放金额与退款结果不一致')
    if not _skip_usage and (formal_service or getattr(settings, 'SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING', False)):
        for receipt in PassRefundRepaymentReceipt.objects.select_for_update().filter(
                pk__in=receipt_ids - verified_usage.keys()).order_by('pk'):
            with reuse_history_locked(order, {receipt.pk}):
                isolated_allocation_state_locked(receipt)
    return result


def record_release_locked(posting, calculation):
    """Write release facts after the provider debits, in the refund savepoint."""
    from .points_business_access import permits
    formal_service = any(permits(posting.proposal.case.order, phase='refund', kind=kind) for kind in ('service', 'addon'))
    if not formal_service:
        require_isolated_checkout()
    if (not transaction.get_connection().in_atomic_block
            or (not formal_service and not getattr(settings, 'SERVICE_POINTS_FUNDING_RELEASE_TESTING', False))
            or not release_tables_exist()):
        raise ValidationError('退款后补资释放尚未启用')
    posting = verify_posting_locked(posting)
    if OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).exists():
        return verify_release_locked(posting.funding_release_evidence)
    order = posting.proposal.case.order
    owner = Q(redemption__order=order) | Q(service_order=order)
    allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(owner).order_by('pk'))
    # The preview already holds the same order/receipt locks and checks actual
    # platform verification. Never release previously released allocation.
    if OrderPointsFundingRelease.objects.filter(allocation__in=allocations).exists():
        raise ValidationError('订单已有补资释放记录，须先核对')
    items = [[item.pk, item.amount, item.receipt_id] for item in allocations]
    prior_addon = _prior_addon_releases(allocations, posting.created_at)
    net_items, addon_released = _net_allocations(items, prior_addon)
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(order)
    income = sum(item.income_at_entry for item in snapshots)
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
    settlement_id = settlement.pk if settlement else None
    if settlement:
        paid_people = list(settlement.persons.select_for_update().select_related(
            'segment__attribution', 'report', 'income_transaction__wallet').order_by('pk'))
        if paid_people:
            # This records the actual pre-refund payout in the refund savepoint;
            # it never creates or changes a provider transaction. The existing
            # first-refund gates remain until release and settlement can both
            # consume this evidence and preserve the original payout.
            from .models import PointsFirstRefundPaidPersonEvidence, OrderProvider
            from .points_settlement_test import _verify_person
            from .settlement import SettlementError
            if (len(paid_people) != 1 or order.required_provider_count != 2 or settlement.finalized_at
                    or settlement.order_id != order.pk or settlement.redemption_id != posting.points_refund.redemption_id
                    or list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
                       != [(item[0], item[1]) for item in net_items if item[1]]):
                raise ValidationError('首退前已付人员或原补资范围待核对')
            person = paid_people[0]
            snapshot = next((row for row in snapshots if row.segment_id == person.segment_id), None)
            assignment = OrderProvider.objects.filter(order=order,
                provider_account_id=person.segment.provider_account_id).first()
            if (not snapshot or not assignment or person.settled_at >= posting.created_at
                    or posting.shares.filter(account_id=person.segment.provider_account_id).exists()
                    or person.segment.provider_account_id in (posting.proposal.case.affected_provider_account_ids or [])):
                raise ValidationError('首退前已付人员与本次受影响人员不一致')
            try:
                from .addon_main_deduction import deductions_at
                deduction = deductions_at(order, before=person.settled_at).get(person.segment.provider_account_id, 0)
                _verify_person(order, person, snapshot, assignment, deduction=deduction)
            except SettlementError as exc:
                raise ValidationError('首退前已付人员的原结算待核对') from exc
            from .points_first_paid_source import verify_original_addons
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                verify_original_addons(order, settlement, person, posting.created_at)
            except DjangoValidationError as exc:
                raise ValidationError('首退前已付加购或收益流水待核对') from exc
            payload = [posting.pk, person.pk, person.amount, person.income_transaction_id, net_items]
            evidence_hash = hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
            PointsFirstRefundPaidPersonEvidence.objects.create(posting=posting, person=person,
                allocation_snapshot=net_items, evidence_hash=evidence_hash)
    actual, addon_snapshot = _compute(posting, net_items, income)
    expected = (calculation.get('conditional_combined_funding') if addon_snapshot is not None
                else calculation['conditional_funding_if_recovered'])
    if expected is None or asdict(actual) != asdict(expected):
        raise ValidationError('实际退款承担与补资测算不一致')
    try:
        parts = plan_release(allocations=[(item[0], item[1], addon_released.get(item[0], 0)) for item in items],
                             amount=actual.release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('补资释放金额异常') from exc
    evidence = OrderPointsFundingReleaseEvidence.objects.create(posting=posting, allocations=items,
        original_provider_income=income, original_settlement_id=settlement_id, expected_release=actual.release_units,
        addon_funding_snapshot=addon_snapshot,
        prior_addon_release_snapshot=prior_addon,
        evidence_hash=_digest(posting, items, income, settlement_id, actual.release_units, addon_snapshot, prior_addon,
                              sort_keys=True))
    OrderPointsFundingRelease.objects.bulk_create([OrderPointsFundingRelease(posting=posting,
        allocation_id=item.allocation_id, amount=item.amount) for item in parts])
    # Receipt verification checks the full usage chain, including the release
    # rows above. Append their events before verifying the completed write.
    if formal_service or getattr(settings, 'SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING', False):
        from .points_receipt_usage import eligible
        if not eligible(order):
            raise ValidationError('该积分退款到账用途尚未接入')
        from users.receipt_usage_sources import record_first_negotiation_release_locked, verify_allocations_locked
        rows = list(OrderPointsFundingRelease.objects.filter(posting=posting).select_related(
            'allocation__receipt', 'posting__proposal__points_terms', 'posting__points_refund__redemption__payment',
            'posting__points_refund__refund', 'posting__points_refund__coin_transaction',
            'posting__points_refund__points_ledger').order_by('allocation__receipt_id', 'pk'))
        touched = set()
        for row in rows:
            if row.allocation.receipt.usage_version is not None:
                record_first_negotiation_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    return verify_release_locked(evidence, _skip_usage=True)


def receipt_points_used_locked(receipt, *, pending_followup_posting_id=None):
    """Verified net funding; allocation writers must hold the receipt lock."""
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('到账分配须在门店事务内核对')
    gross = sum(receipt.points_funding_allocations.values_list('amount', flat=True))
    verified_transfer_followups = set()

    def verify_transfer_followup(item):
        if item.pk not in verified_transfer_followups:
            from .points_transfer_followup_revision import _verify_followup_revision_facts_locked
            _verify_followup_revision_facts_locked(item)
            verified_transfer_followups.add(item.pk)

    def verify_first_release(item):
        # The historical verifier is opt-in and only valid for a complete
        # transfer followup; it does not authorize or verify settlement.
        if (getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING', False)
                and isinstance(item.posting.participants, dict)):
            from .models import OrderPointsTransferFollowupFundingRevision
            matches = list(OrderPointsTransferFollowupFundingRevision.objects.filter(
                first_posting_id=item.posting_id).select_related('posting')[:2])
            if len(matches) > 1:
                raise ValidationError('转单后续退款资金版本重复')
            if matches:
                verify_transfer_followup(matches[0])
                return
        verify_release_locked(item)
    if not release_tables_exist():
        return gross
    rows = list(_paged(OrderPointsFundingRelease.objects.filter(allocation__receipt=receipt)))
    posting_ids = {row.pk for row in _paged(PointsNegotiationPosting.objects.filter(
        points_refund__redemption__funding_allocations__receipt=receipt).distinct())}
    if pending_followup_posting_id is not None:
        require_isolated_checkout()
        if (type(pending_followup_posting_id) is not int or pending_followup_posting_id not in posting_ids
                or not PointsNegotiationPosting.objects.filter(pk=pending_followup_posting_id,
                    proposal__points_terms__prior_refund__isnull=False).exists()
                or OrderPointsFundingReleaseEvidence.objects.filter(posting_id=pending_followup_posting_id).exists()):
            raise ValidationError('待核对的后续退款补资记录异常')
        from .models import OrderPointsFollowupFundingReleaseEvidence
        completed = OrderPointsFollowupFundingReleaseEvidence.objects.select_related('revision').filter(
            revision__posting_id=pending_followup_posting_id).first()
        if not completed and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_REVISION_TESTING', False):
            raise ValidationError('待写入的后续退款补资版本尚未开放')
        pending_rows = [row for row in rows if row.posting_id == pending_followup_posting_id]
        if completed:
            expected = completed.revision.releases
            if (not isinstance(expected, list) or any(not isinstance(item, list) or len(item) != 3
                    or any(type(value) is not int or value <= 0 for value in item) for item in expected)):
                raise ValidationError('后续退款补资释放快照异常')
            expected_receipt = sorted((item[0], item[2]) for item in expected if item[1] == receipt.pk)
            if sorted((row.allocation_id, row.amount) for row in pending_rows) != expected_receipt:
                raise ValidationError('后续退款补资释放与到账不一致')
        elif pending_rows:
            raise ValidationError('后续退款补资释放凭证缺失')
        posting_ids.remove(pending_followup_posting_id)
    if rows or posting_ids:
        require_isolated_checkout()
        evidence = [item for ids in _chunks(posting_ids) for item in _paged(
            OrderPointsFundingReleaseEvidence.objects.filter(posting_id__in=ids))]
        first_ids = {item.posting_id for item in evidence}
        missing = posting_ids - first_ids
        from .models import OrderPointsFollowupFundingReleaseEvidence
        followups = [item for ids in _chunks(missing) for item in _paged(
            OrderPointsFollowupFundingReleaseEvidence.objects.filter(revision__posting_id__in=ids)
            .select_related('revision'))]
        from .models import OrderPointsTransferFollowupFundingRevision
        other_missing = missing - {item.revision.posting_id for item in followups}
        transfer_followups = []
        if other_missing:
            with connection.cursor() as cursor:
                tables = set(connection.introspection.table_names(cursor))
            if OrderPointsTransferFollowupFundingRevision._meta.db_table in tables:
                transfer_followups = [item for ids in _chunks(other_missing) for item in _paged(
                    OrderPointsTransferFollowupFundingRevision.objects.filter(
                        posting_id__in=ids).select_related('posting'))]
        valid_ids = (first_ids | {item.revision.posting_id for item in followups}
                     | {item.posting_id for item in transfer_followups})
        # During an outer revision verification another order sharing this
        # receipt can encounter its not-yet-written release marker. This is
        # valid only inside that verification and before any release rows are
        # written; the surrounding savepoint will verify and write the marker.
        from .points_followup_context import is_active
        active_pending = {posting_id for posting_id in posting_ids - valid_ids if is_active(posting_id)}
        if active_pending:
            from .models import OrderPointsFollowupFundingRevision
            if (not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_REVISION_TESTING', False)
                    or any(row.posting_id in active_pending for row in rows)
                    or {row.posting_id for ids in _chunks(active_pending) for row in _paged(
                        OrderPointsFollowupFundingRevision.objects.filter(posting_id__in=ids))} != active_pending):
                raise ValidationError('待写入的后续退款补资凭证异常')
        allowed_rows = posting_ids | ({pending_followup_posting_id} if pending_followup_posting_id else set())
        if (valid_ids | active_pending != posting_ids or any(row.posting_id not in allowed_rows for row in rows)):
            raise ValidationError('到账补资释放凭证缺失')
        for item in evidence:
            verify_first_release(item)
        if followups:
            from .points_followup_release import verify_followup_release_locked
            for item in followups:
                verify_followup_release_locked(item)
        if transfer_followups:
            if getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING', False):
                for item in transfer_followups:
                    verify_transfer_followup(item)
            else:
                from .points_transfer_followup_revision import verify_locked as verify_transfer_followup_full
                for item in transfer_followups:
                    verify_transfer_followup_full(item)
    released = sum(row.amount for row in rows)
    from .points_transfer_revision import revision_tables_exist
    if revision_tables_exist():
        from .models import OrderPointsTransferFundingRelease
        transfer_rows = list(_paged(OrderPointsTransferFundingRelease.objects.filter(
            allocation__receipt=receipt).select_related('revision__order')))
        if transfer_rows:
            from .points_transfer_revision import verify_revisions_locked
            from .points_funding_plan import PointsFundingPlanError
            try:
                for order in {row.revision.order_id: row.revision.order for row in transfer_rows}.values():
                    transfer_posting = PointsNegotiationPosting.objects.filter(
                        points_refund__redemption__order=order).first()
                    if transfer_posting and isinstance(transfer_posting.participants, dict):
                        transfer_evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=transfer_posting).first()
                        if not transfer_evidence:
                            raise ValidationError('转单退款补资释放凭证缺失')
                        verify_first_release(transfer_evidence)
                    else:
                        verify_revisions_locked(order)
            except PointsFundingPlanError as exc:
                raise ValidationError('转单补资释放凭证待核对') from exc
            released += sum(row.amount for row in transfer_rows)
    from .reservation_cancel_funding_test import receipt_released_locked
    released += receipt_released_locked(receipt)
    from .models import PointsPrestartCancelFundingRelease
    from users.receipt_usage_sources import prestart_cancel_release_facts
    cancelled = list(_paged(PointsPrestartCancelFundingRelease.objects.filter(allocation__receipt=receipt)
                     .select_related('allocation__redemption', 'evidence__points_refund__redemption')))
    for item in cancelled:
        prestart_cancel_release_facts(item)
    released += sum(item.amount for item in cancelled)
    from .addon_funding_release import receipt_addon_released
    released += receipt_addon_released(receipt)
    if released > gross:
        raise ValidationError('到账补资释放超过原分配金额')
    return gross - released
