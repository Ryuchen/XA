"""Snapshot the new segment economics without changing the prior refund."""
from collections import defaultdict
from dataclasses import asdict, replace
import hashlib
import json

from rest_framework.exceptions import ValidationError
from django.db.models import Q
from stores.db import transaction

from .models import (NegotiationExecution, OrderOriginalShareTransfer, OrderTransferNegotiationEvidence,
                     OrdinaryPostRefundTransferRevision)


def _hash(order_id, transfer_id, previous_id, snapshot):
    payload = [order_id, transfer_id, previous_id, snapshot]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def record_pre_main_locked(order, transfer, *, source_kind='TRANSFER', source_id=None):
    """Freeze and release a real transfer before the first main agreement."""
    from .ordinary_transfer_capability import permits
    if (not transaction.get_connection().in_atomic_block or transfer.previous_segment.attribution.order_id != order.pk
            or not permits(order, allow_addon_refund=True, allow_addon_handover=True)
            or order.refund_records.exists() or OrderTransferNegotiationEvidence.objects.filter(order=order).exists()):
        raise ValidationError('主单退款前转单资金版本尚未开放')
    source_id = transfer.pk if source_id is None else source_id
    if source_kind not in ('TRANSFER', 'ADDON_PAYMENT', 'ADDON_REFUND', 'ADDON_HANDOVER'):
        raise ValidationError('转单资金事件来源无效')
    existing = OrdinaryPostRefundTransferRevision.objects.select_for_update().filter(order=order,
        source_kind=source_kind, source_id=source_id).first()
    if existing:
        return verify_locked(existing)
    from .points_funding_evidence import _verified_allocations_for_locked_order
    from .addon_funding_release import addon_release_totals
    allocations = _verified_allocations_for_locked_order(order)
    if any(item.receipt.usage_version is None for item in allocations):
        raise ValidationError('主单退款前转单的到账用途版本尚未接通')
    released = addon_release_totals(allocations)
    allocated = sum(item.amount - released[item.pk] for item in allocations)
    previous = OrdinaryPostRefundTransferRevision.objects.select_for_update().filter(order=order).order_by('-pk').first()
    if previous:
        verify_locked(previous)
    revision = OrdinaryPostRefundTransferRevision.objects.create(order=order, transfer=transfer, previous=previous,
        source_kind=source_kind, source_id=source_id, snapshot={}, snapshot_hash='')
    from .ordinary_transfer_pre_main_version import facts
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        computed = facts(order, transfer, revision.created_at, allocated)
    except DjangoValidationError as exc:
        raise ValidationError('主单退款前转单资金事实异常') from exc
    revision.snapshot = {'pre_main_transfer_v1': True, 'transfer_id': transfer.pk, 'source_kind': source_kind,
                         'source_id': source_id, **computed}
    revision.snapshot_hash = _hash(order.pk, transfer.pk, previous.pk if previous else None, revision.snapshot)
    revision.save(update_fields=['snapshot', 'snapshot_hash'])
    return revision


def record_locked(order, transfer, *, source_kind='TRANSFER', source_id=None):
    if not transaction.get_connection().in_atomic_block or transfer.previous_segment.attribution.order_id != order.pk:
        raise ValidationError('转单资金版本须在原订单事务内保存')
    from .ordinary_transfer_capability import permits
    from .negotiation_zero import marked_execution_id
    zero_id = marked_execution_id(order)
    zero_pretransfer = bool(zero_id and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
        snapshot__zero_pretransfer_execution_id=zero_id).exists())
    first_transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    positive_pretransfer = None
    if not zero_pretransfer and first_transfer and not OrderTransferNegotiationEvidence.objects.filter(order=order).exists():
        from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            positive_pretransfer = verify_positive_pretransfer(order, before=first_transfer.created_at)
        except DjangoValidationError as exc:
            raise ValidationError('转单前正退款凭证待核对') from exc
    if (not permits(order, allow_addon_refund=True, allow_addon_handover=True)
            or (not zero_pretransfer and not positive_pretransfer and not OrderTransferNegotiationEvidence.objects.filter(
                order=order, execution__termination_processed_at__isnull=False).exists())):
        raise ValidationError('该转单退款资金组合尚未开放')
    source_id = transfer.pk if source_id is None else source_id
    if source_kind not in ('TRANSFER', 'ZERO_AGREEMENT', 'ADDON_PAYMENT', 'ADDON_REFUND', 'ADDON_HANDOVER') or type(source_id) is not int or source_id <= 0:
        raise ValidationError('资金版本来源无效')
    from .models import OrderAddonHandover, OrderAddonPayment, OrderAddonRefund
    if ((source_kind == 'TRANSFER' and source_id != transfer.pk)
            or (source_kind == 'ADDON_REFUND' and not OrderAddonRefund.objects.filter(pk=source_id, order=order).exists())
            or (source_kind == 'ADDON_PAYMENT' and not OrderAddonPayment.objects.filter(pk=source_id, intent__order=order).exists())
            or (source_kind == 'ZERO_AGREEMENT' and not NegotiationExecution.objects.filter(
                pk=source_id, zero_order=order, termination_processed_at__isnull=False).exists())
            or (source_kind == 'ADDON_HANDOVER' and not OrderAddonHandover.objects.filter(
                pk=source_id, payment__intent__order=order, status='ACCEPTED').exists())):
        raise ValidationError('资金版本与原业务事件不一致')
    if source_kind == 'ADDON_REFUND':
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .addon_refund_share_allocation import verify as verify_share_allocations
        source_refund = OrderAddonRefund.objects.select_related('payment', 'proposal').get(pk=source_id, order=order)
        try:
            verify_share_allocations(source_refund)
        except DjangoValidationError as exc:
            raise ValidationError('转单加购退款逐份额来源异常') from exc
    existing = OrdinaryPostRefundTransferRevision.objects.select_for_update().filter(
        order=order, source_kind=source_kind, source_id=source_id).first()
    if existing:
        return verify_locked(existing)
    first = OrderTransferNegotiationEvidence.objects.select_for_update().filter(order=order).first()
    if not first and not zero_pretransfer and not positive_pretransfer:
        raise ValidationError('原协商资金凭证缺失')
    from .negotiation_execution import verify_execution
    from .negotiation_termination import verify_termination_evidence
    if first:
        from .negotiation_transfer_evidence import verify_locked as verify_first
        verify_first(first.execution)
    elif zero_pretransfer:
        from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
        first_transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
        if not first_transfer or not verify_pretransfer_zero(order, before=first_transfer.created_at):
            raise ValidationError('转单前零退款协商凭证缺失')
    receipts = list(NegotiationExecution.objects.select_for_update().filter(
        Q(refund__order=order) | Q(zero_order=order), termination_processed_at__isnull=False).order_by('pk'))
    if {row.refund_id for row in receipts if row.refund_id} != set(order.refund_records.values_list('pk', flat=True)):
        raise ValidationError('转单前退款记录未全部结案')
    for receipt in receipts:
        if receipt.refund_id:
            if not positive_pretransfer or receipt.pk != positive_pretransfer.pk:
                verify_execution(receipt)
                verify_termination_evidence(receipt)
        else:
            from .negotiation_zero import verify_zero_execution as verify_zero, verify_zero_termination as verify_zero_end
            if not (zero_pretransfer and receipt.pk == zero_id):
                verify_zero(receipt)
                from .models import OrdinaryTransferFollowupRound
                if OrdinaryTransferFollowupRound.objects.filter(execution=receipt).exists():
                    from .ordinary_transfer_followup_zero import verify_termination as verify_followup_end
                    verify_followup_end(receipt)
                else:
                    verify_zero_end(receipt)
    from .segment_settlement_plan import build_segment_settlement_plan
    from .settlement import SettlementError
    try:
        plan = build_segment_settlement_plan(order, allow_recorded=True, _preview_unstarted=True,
                                             _allow_recorded_refunds=True)
    except SettlementError as exc:
        raise ValidationError('转单后的履约段和抽成快照待核对') from exc
    pending, external = defaultdict(int), 0
    for receipt in receipts:
        for share in receipt.shares.select_for_update().all():
            pending[share.account_id] += share.pending_deduction
            external += share.wallet_deduction + share.deposit_deduction
    income = defaultdict(int)
    for line in plan.segments:
        income[line.account_id] += line.income
    for account, amount in pending.items():
        if account not in income or amount > income[account]:
            raise ValidationError('原协商承担超过本人保留收益')
        income[account] -= amount
    sources = list(order.funding_allocations.select_for_update().select_related('lot').order_by('pk'))
    from engagement.funding_wallet import validate_order_funding_sources
    validate_order_funding_sources(order, sources)
    paid = {kind: sum(row.amount for row in sources if row.kind == kind) for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    returned = {kind: sum(row.refunded_amount for row in sources if row.kind == kind) for kind in paid}
    if sum(paid.values()) != order.amount or sum(returned.values()) != sum(row.refund.amount for row in receipts if row.refund_id):
        raise ValidationError('原付款与累计退款来源待核对')
    from .points_funding_evidence import _verified_allocations_for_locked_order
    from .addon_funding_release import addon_release_totals
    allocations = _verified_allocations_for_locked_order(order)
    released = addon_release_totals(allocations)
    allocated = sum(row.amount - released[row.pk] for row in allocations)
    from .points_negotiation_preview import funding_after_refund
    main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=sum(income.values()), pending_deduction_units=0, provider_external_units=external,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=allocated)
    from .addon_funding_history import combined_at
    from django.utils import timezone
    history = combined_at(order, main, timezone.now(), segment_shares=[
        (line.segment_id, line.account_id, line.basis, line.fee, line.income) for line in plan.segments])
    if history.get('addon_history'):
        from .refunded_addon_funding import combine_refunded_history
        result = combine_refunded_history(main, history, main_retained_by_account=income)
    else:
        result = main
    from .ordinary_transfer_addon_rounding import facts as addon_rounding_facts
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        addon_rounding = addon_rounding_facts(order)
    except DjangoValidationError as exc:
        raise ValidationError('转单加购退款取整来源异常') from exc
    rounding = sum(receipt.rounding_to_store_units for receipt in receipts) + sum(row[1] for row in addon_rounding)
    extra = max(rounding - result.store_income_units, 0)
    if extra:
        required = result.required_units + extra
        result = replace(result, store_income_units=result.store_income_units + extra, required_units=required,
                         release_units=max(allocated - required, 0), remaining_units=max(required - allocated, 0))
    previous = OrdinaryPostRefundTransferRevision.objects.select_for_update().filter(order=order).order_by('-pk').first()
    if previous:
        verify_locked(previous)
    from .models import OrderSegmentSettlementManifest, OrderSegmentPersonSettlement
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if manifest and manifest.finalized_at:
        raise ValidationError('订单已最终结算，不能变更资金版本')
    settled = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(
        manifest__order=order).order_by('pk').values('id', 'account_id', 'amount', 'segment_ids'))
    snapshot = {'transfer_id': transfer.pk, 'source_kind': source_kind, 'source_id': source_id,
                'lines': [asdict(line) for line in plan.segments],
                'pending': [[key, value] for key, value in sorted(pending.items())],
                'receipts': [receipt.pk for receipt in receipts], 'sources': paid, 'returned': returned,
                'funding': asdict(result), 'addon_history': history.get('addon_history', []),
                'addon_rounding': addon_rounding,
                'prior_manifest_id': manifest.pk if manifest else None,
                'prior_manifest_plan': manifest.plan if manifest else None,
                'prior_manifest_plan_hash': manifest.plan_hash if manifest else '', 'settled_people': settled}
    if zero_pretransfer:
        snapshot['zero_pretransfer_execution_id'] = zero_id
    if positive_pretransfer:
        snapshot['positive_pretransfer_execution_id'] = positive_pretransfer.pk
    previous_id = previous.pk if previous else None
    return OrdinaryPostRefundTransferRevision.objects.create(order=order, transfer=transfer, previous=previous,
        source_kind=source_kind, source_id=source_id,
        snapshot=snapshot, snapshot_hash=_hash(order.pk, transfer.pk, previous_id, snapshot))


def verify_locked(revision):
    from django.core.exceptions import ValidationError as DjangoValidationError
    if (revision.transfer.previous_segment.attribution.order_id != revision.order_id
            or revision.snapshot.get('transfer_id') != revision.transfer_id
            or revision.snapshot.get('source_kind') != revision.source_kind
            or revision.snapshot.get('source_id') != revision.source_id
            or revision.snapshot_hash != _hash(revision.order_id, revision.transfer_id,
                                                revision.previous_id, revision.snapshot)):
        raise ValidationError('转单后的资金版本凭证异常')
    if revision.previous_id and (revision.previous.order_id != revision.order_id
                                 or revision.previous_id >= revision.pk):
        raise ValidationError('转单资金版本先后关系异常')
    if revision.snapshot.get('pre_main_transfer_v1') is True:
        from .ordinary_transfer_pre_main_version import facts
        try:
            expected = facts(revision.order, revision.transfer, revision.created_at,
                             revision.snapshot['funding']['allocated_units'])
        except (DjangoValidationError, KeyError, TypeError) as exc:
            raise ValidationError('主单退款前转单资金事实异常') from exc
        if any(revision.snapshot.get(key) != value for key, value in expected.items()):
            raise ValidationError('主单退款前转单资金版本不一致')
        return revision
    from .models import OrderAddonHandover, OrdinaryTransferFollowupRound
    from .ordinary_transfer_pre_main_addon import verify_handover_at
    for handover in OrderAddonHandover.objects.filter(payment__intent__order_id=revision.order_id,
            status='ACCEPTED', accepted_at__lt=revision.created_at).select_related(
                'payment__intent', 'source_segment__attribution', 'target_segment__attribution').order_by('pk'):
        try:
            verify_handover_at(handover.payment, handover, revision.created_at)
        except DjangoValidationError as exc:
            raise ValidationError('转单资金版本加购接替来源异常') from exc
    from .ordinary_transfer_zero_receipt import verify as verify_zero_local
    positive_id = revision.snapshot.get('positive_pretransfer_execution_id')
    if positive_id:
        from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
        first_transfer = OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order_id=revision.order_id).order_by('created_at', 'pk').first()
        try:
            first_positive = verify_positive_pretransfer(revision.order, before=first_transfer.created_at) if first_transfer else None
        except DjangoValidationError as exc:
            raise ValidationError('转单前正退款凭证异常') from exc
        if not first_positive or first_positive.pk != positive_id or positive_id not in revision.snapshot.get('receipts', []):
            raise ValidationError('转单前正退款与资金版本不一致')
    zero = OrderTransferNegotiationEvidence.objects.filter(order_id=revision.order_id,
        execution__zero_order_id=revision.order_id).values_list('execution_id', flat=True).first()
    if zero and zero not in revision.snapshot.get('receipts', []):
        raise ValidationError('转单资金版本缺少零退款协商')
    for execution in NegotiationExecution.objects.filter(pk__in=revision.snapshot.get('receipts', []),
                                                           zero_order_id=revision.order_id):
        try:
            if execution.pk == revision.snapshot.get('zero_pretransfer_execution_id'):
                from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
                first_transfer = OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order_id=revision.order_id).order_by('created_at', 'pk').first()
                if not first_transfer or not verify_pretransfer_zero(revision.order, before=first_transfer.created_at):
                    raise DjangoValidationError('转单前零退款凭证缺失')
            elif OrdinaryTransferFollowupRound.objects.filter(execution=execution).exists():
                from .ordinary_transfer_followup_zero import verify_termination
                verify_termination(execution, lock=False)
            else:
                verify_zero_local(execution)
        except DjangoValidationError as exc:
            raise ValidationError('转单零退款协商凭证异常') from exc
    if revision.source_kind == 'ADDON_REFUND':
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .models import OrderAddonRefund
        from .addon_refund_share_allocation import verify as verify_share_allocations
        refund = OrderAddonRefund.objects.filter(pk=revision.source_id, order_id=revision.order_id).first()
        if not refund or refund.created_at >= revision.created_at:
            raise ValidationError('转单加购退款资金来源异常')
        try:
            verify_share_allocations(refund)
        except DjangoValidationError as exc:
            raise ValidationError('转单加购退款逐份额来源异常') from exc
    return revision


def current_locked(order, *, ordinary_pending_handover_id=None):
    """Return the latest version only while its monetary facts remain current."""
    revision = (OrdinaryPostRefundTransferRevision.objects.select_for_update().filter(order=order)
                .select_related('transfer__previous_segment__attribution', 'previous').order_by('-pk').first())
    if not revision:
        return None
    verify_locked(revision)
    if (revision.snapshot.get('pre_main_transfer_v1')
            and revision.snapshot.get('zero_pretransfer_execution_id')):
        from django.utils import timezone
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .ordinary_transfer_pre_main_version import facts as pretransfer_facts
        from .points_funding_evidence import _verified_allocations_for_locked_order
        from .addon_funding_release import addon_release_totals
        allocations = _verified_allocations_for_locked_order(
            order, ordinary_pending_handover_id=ordinary_pending_handover_id)
        released = addon_release_totals(allocations)
        allocated = sum(item.amount - released[item.pk] for item in allocations)
        try:
            current = pretransfer_facts(order, revision.transfer, timezone.now(), allocated)
        except (DjangoValidationError, ValueError) as exc:
            raise ValidationError('零退款后转单资金事实待核对') from exc
        expected = revision.snapshot
        monetary = ('net_principal_units', 'planned_provider_external_units', 'provider_income_units',
                    'inviter_income_units', 'store_income_units', 'required_units')
        if (current['lines'] != expected.get('lines') or current['addon_history'] != expected.get('addon_history')
                or current.get('zero_pretransfer_execution_id') != expected.get('zero_pretransfer_execution_id')
                or any(current['funding'].get(key) != expected.get('funding', {}).get(key) for key in monetary)):
            raise ValidationError('零退款后转单资金版本已变化')
        return revision
    from django.core.exceptions import ValidationError as DjangoValidationError
    from .addon_refund_share_allocation import verify as verify_share_allocations
    for refund in order.addon_refunds.select_related('payment', 'proposal').order_by('created_at', 'pk'):
        try:
            verify_share_allocations(refund)
        except DjangoValidationError as exc:
            raise ValidationError('转单加购退款逐份额来源异常') from exc
    from .segment_settlement_plan import build_segment_settlement_plan
    from .settlement import SettlementError
    try:
        plan = build_segment_settlement_plan(order, allow_recorded=True, _preview_unstarted=True,
                                             _allow_recorded_refunds=True)
    except SettlementError as exc:
        raise ValidationError('后续转单履约段待核对') from exc
    snapshot = revision.snapshot
    from .ordinary_transfer_addon_rounding import facts as addon_rounding_facts
    try:
        if snapshot.get('addon_rounding') != addon_rounding_facts(order, before=revision.created_at):
            raise ValidationError('转单加购退款取整版本已变化')
    except DjangoValidationError as exc:
        raise ValidationError('转单加购退款取整来源异常') from exc
    if [asdict(line) for line in plan.segments] != snapshot['lines']:
        raise ValidationError('后续转单履约段与资金版本不一致')
    receipts = list(NegotiationExecution.objects.select_for_update().filter(
        Q(refund__order=order) | Q(zero_order=order), termination_processed_at__isnull=False).order_by('pk'))
    if [receipt.pk for receipt in receipts] != snapshot['receipts']:
        raise ValidationError('转单后的退款已变化，请先生成新资金版本')
    sources = list(order.funding_allocations.select_for_update().order_by('pk'))
    for key, field in (('sources', 'amount'), ('returned', 'refunded_amount')):
        actual = {kind: sum(getattr(item, field) for item in sources if item.kind == kind)
                  for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        if actual != snapshot[key]:
            raise ValidationError('转单后的原付款来源已变化')
    from .models import OrderAddonPayment, OrderAddonRefund, OrderAddonHandover
    later_refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__gte=revision.created_at)
                         .order_by('created_at', 'pk'))
    if (OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at__gte=revision.created_at).exists()
            or OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
                                                  accepted_at__gte=revision.created_at).exists()):
        raise ValidationError('转单后的加购资金已变化，请生成新的资金版本')
    if later_refunds:
        from .models import OrderAddonFundingReleaseRun
        from .addon_funding_release import verify_run
        from .points_funding_evidence import _verified_allocations_for_locked_order
        from .ordinary_followup_addon_receipt import verify as verify_refund_receipt
        allocations = _verified_allocations_for_locked_order(
            order, ordinary_pending_handover_id=ordinary_pending_handover_id)
        allocation_by_id = {item.pk: item for item in allocations}
        latest = None
        for refund in later_refunds:
            event = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
            if (not event or not isinstance(event.ordinary_refund_snapshot, dict)
                    or event.ordinary_refund_snapshot.get('kind') != 'ordinary_followup_addon_refund_v1'
                    or event.created_at <= refund.created_at):
                raise ValidationError('转单后加购退款资金版本缺失')
            verify_run(event)
            if event.allocation_snapshot:
                witness = allocation_by_id.get(event.allocation_snapshot[0][0])
                if not witness:
                    raise ValidationError('转单后加购退款到账分配缺失')
                verify_refund_receipt(event, witness.receipt_id)
            else:
                verify_refund_receipt(event, None)
            latest = event
        from copy import copy
        revision = copy(revision)
        revision.snapshot = dict(revision.snapshot)
        revision.snapshot['funding'] = latest.ordinary_refund_snapshot['funding']
        revision.snapshot['addon_history'] = latest.ordinary_refund_snapshot['addon_history']
        from .ordinary_transfer_addon_rounding import facts as addon_rounding_facts
        revision.snapshot['addon_rounding'] = addon_rounding_facts(order)
    return revision


def refresh_for_event(order, *, source_kind, source_id):
    """Refresh the same funding ledger after a paid add-on changes."""
    from .ordinary_transfer_capability import permits
    if not permits(order, allow_addon_refund=True, allow_addon_handover=True):
        return False
    event = (OrderOriginalShareTransfer.objects.select_for_update().filter(
        previous_segment__attribution__order=order).select_related('previous_segment__attribution')
        .order_by('-pk').first())
    if not event:
        return False
    has_transfer_agreement = OrderTransferNegotiationEvidence.objects.filter(order=order,
        execution__termination_processed_at__isnull=False).exists()
    positive_pretransfer = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
        snapshot__positive_pretransfer_execution_id__isnull=False).exists()
    from .models import OrdinaryTransferFollowupRound
    if (not has_transfer_agreement and (positive_pretransfer or OrdinaryTransferFollowupRound.objects.filter(order=order,
            execution__termination_processed_at__isnull=False).exists())):
        revision = record_locked(order, event, source_kind=source_kind, source_id=source_id)
        from .ordinary_post_refund_release import record_locked as release
        release(revision)
        return True
    if not has_transfer_agreement:
        from .negotiation_zero import marked_execution_id
        if not marked_execution_id(order):
            return False
        from .models import OrderAddonHandover, OrderAddonPayment, OrderAddonRefund
        from .addon_funding_release import (record_handover_funding_release, record_payment_funding_release,
                                            record_addon_funding_release)
        if source_kind == 'ADDON_HANDOVER':
            actual = OrderAddonHandover.objects.get(pk=source_id, payment__intent__order=order, status='ACCEPTED')
            record_handover_funding_release(order, actual)
        elif source_kind == 'ADDON_PAYMENT':
            actual = OrderAddonPayment.objects.get(pk=source_id, intent__order=order)
            record_payment_funding_release(order, actual)
        elif source_kind == 'ADDON_REFUND':
            actual = OrderAddonRefund.objects.get(pk=source_id, order=order)
            record_addon_funding_release(order, actual)
        else:
            raise ValidationError('零退款后转单资金事件不支持')
        revision = record_pre_main_locked(order, event, source_kind=source_kind, source_id=source_id)
    else:
        revision = record_locked(order, event, source_kind=source_kind, source_id=source_id)
    from .ordinary_post_refund_release import record_locked as release
    release(revision)
    return True


def transition_settlement_locked(order, manifest, plan, terms):
    """Revise only the unpaid part of the prior segment manifest."""
    revision = current_locked(order)
    if (not revision or revision.snapshot['prior_manifest_id'] != manifest.pk
            or revision.snapshot['prior_manifest_plan_hash'] != manifest.plan_hash
            or revision.snapshot['prior_manifest_plan'] != manifest.plan):
        raise ValidationError('原逐段结算清单已变化')
    from .models import OrderSegmentPersonSettlement
    actual = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(manifest=manifest)
                  .order_by('pk').values('id', 'account_id', 'amount', 'segment_ids'))
    if actual != revision.snapshot['settled_people']:
        raise ValidationError('已结算履约段已变化')
    people = {person.account_id: person for person in plan.persons}
    for paid in actual:
        person = people.get(paid['account_id'])
        if not person or person.income != paid['amount'] or list(person.segment_ids) != paid['segment_ids']:
            raise ValidationError('后续转单不得更改已结算收益')
    from .segment_settlement import _hash as plan_hash, _validate_records
    manifest.plan, manifest.plan_hash = terms, plan_hash(terms)
    manifest.save(update_fields=['plan', 'plan_hash'])
    return _validate_records(order, manifest, plan)
