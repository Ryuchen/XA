"""Isolated settlement of one negotiated, points-paid service refund."""
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction
from .models import (Order, OrderPointsFundingAllocation, OrderPointsFundingRelease,
                     OrderPointsRefundSettlement, OrderPointsRefundSettlementFunding,
                     OrderPointsRefundSettlementPerson, OrderProvider, OrderSegmentFinancialSnapshot,
                     PointsNegotiationPosting)
from .points_checkout_test import require_isolated_checkout
from .points_funding_evidence import verify_order_funding
from .points_funding_plan import PointsFundingPlanError
from .points_refunded_funding import plan_refunded_order
from .settlement import SettlementError


def enabled():
    from .points_business_access import permits
    if permits(phase='settlement', kind='service') or permits(phase='settlement', kind='addon'):
        return True
    if not (getattr(settings, 'SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING', False)
            and getattr(settings, 'SERVICE_POINTS_REFUNDED_FUNDING_TESTING', False)):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    return True


def _deadline(order, posting, account_id):
    affected = account_id in posting.proposal.affected_accounts
    start = posting.accepted_at if posting.termination_in_service and affected else order.completed_at
    if not start or order.service_aftersale_hours is None:
        raise SettlementError('积分退款后的售后时限待核对')
    return start + timedelta(hours=order.service_aftersale_hours)


def _person_amount(posting, snapshot):
    share = posting.shares.filter(account_id=snapshot.segment.provider_account_id).first()
    prior = next((row.get('prior_addon_deduction', 0) for row in posting.participants
                  if row.get('account_id') == snapshot.segment.provider_account_id), 0)
    from .addon_main_deduction import deductions_at
    current = deductions_at(posting.proposal.case.order).get(snapshot.segment.provider_account_id, 0)
    amount = (share.retained_income if share else snapshot.income_at_entry - prior) - (current - prior)
    if current < prior or amount < 0:
        raise SettlementError('加购退款后的主单个人收益待核对')
    return amount


def _report_amount(posting, snapshot, report):
    # An already approved report remains an audit of the original service;
    # the separately agreed refund changes the actual settlement instead.
    if report.audited_at and report.audited_at < posting.created_at:
        return snapshot.income_at_entry
    prior = next((row.get('prior_addon_deduction', 0) for row in posting.participants
                  if row.get('account_id') == snapshot.segment.provider_account_id), 0)
    from .addon_main_deduction import deductions_at
    at_review = deductions_at(posting.proposal.case.order, before=report.audited_at).get(
        snapshot.segment.provider_account_id, 0) if report.audited_at else prior
    share = posting.shares.filter(account_id=snapshot.segment.provider_account_id).first()
    return (share.retained_income if share else snapshot.income_at_entry - prior) - (at_review - prior)


def _check_person(order, posting, person, snapshot, assignment):
    from .services import missing_report_evidence
    amount, report = _person_amount(posting, snapshot), person.report
    if (person.segment_id != snapshot.segment_id or person.amount != amount or assignment.settled_at != person.settled_at
            or not order.completed_at or person.settled_at < order.completed_at or person.settled_at > timezone.now()
            or report.order_id != order.pk or report.provider_id != assignment.provider_id
            or report.provider_account_id != assignment.provider_account_id or report.status != ProviderReport.Status.APPROVED
            or report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate
            or report.payout_amount != _report_amount(posting, snapshot, report) or not report.submitted_at or not report.audited_at
            or report.audited_at < report.submitted_at or report.audited_at > person.settled_at
            or _deadline(order, posting, assignment.provider_account_id) > person.settled_at
            or report.submitted_at + timedelta(hours=order.service_freeze_hours) > person.settled_at
            or missing_report_evidence(report)):
        raise SettlementError('积分退款后的陪玩报单或结算时限待核对')
    credits = set(order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
        remark=f'订单收入：{order.order_no}', wallet__user_id=assignment.provider_id).values_list('pk', flat=True))
    tx = person.income_transaction
    if not amount:
        if tx or credits:
            raise SettlementError('零收益不应产生陪玩收入流水')
    elif (not tx or tx.order_id != order.pk or tx.wallet.user_id != assignment.provider_id
            or tx.wallet.account_id != assignment.provider_account_id or tx.tx_type != Transaction.TxType.INCOME
            or tx.status != Transaction.Status.SUCCESS or tx.amount != amount or tx.reversal_of_id
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversals.exists() or credits != {tx.pk}):
        raise SettlementError('积分退款后的陪玩收入流水待核对')
    from wallet.models import RefundDepositRecoveryPermit
    if RefundDepositRecoveryPermit.objects.filter(account_id=assignment.provider_account_id,
            order=order, closed_at__isnull=True).exists():
        from wallet.models import IncomeHold
        pending = bool(tx and IncomeHold.objects.filter(income_transaction=tx, wallet_id=tx.wallet_id,
            released_at__isnull=True, cancelled_at__isnull=True, amount=tx.amount,
            deposit_amount=0, recovered_amount=0, priority_opening_evidence__isnull=False).exists())
        if not pending:
            raise SettlementError('退款后的补差接单机会尚未正确关闭')
    zero_permit = RefundDepositRecoveryPermit.objects.filter(zero_points_refund_person=person).first()
    if zero_permit and (amount or zero_permit.account_id != assignment.provider_account_id
            or zero_permit.profile.user_id != assignment.provider_id or zero_permit.order_id != order.pk
            or zero_permit.close_reason != 'ZERO_POINTS_REF_SETTLED' or not zero_permit.closed_at
            or zero_permit.income_application_id or zero_permit.zero_income_settlement_id
            or zero_permit.zero_negotiated_share_id):
        raise SettlementError('退款后的零收益补差凭证异常')


def _addon_facts(order, posting):
    """Recover separately settled add-on obligations from the verified snapshot."""
    from .models import OrderAddonPayment, OrderPointsFundingReleaseEvidence
    evidence = OrderPointsFundingReleaseEvidence.objects.get(posting=posting)
    from .points_refunded_funding import later_addon_runs
    runs = later_addon_runs(order, posting)
    latest = runs[-1] if runs else None
    history = (latest.refunded_main_snapshot['addon_history'] if latest else evidence.addon_funding_snapshot) or []
    if not history:
        return (0, 0, 0, 0, 0, 0), ()
    from .addon_funding import AddonFundingPart
    from .addon_handover import current_responsibilities
    payments = {row.pk: row for row in OrderAddonPayment.objects.select_for_update().filter(
        intent__order=order, pk__in=[item['payment_id'] for item in history])}
    parts = []
    for item in history:
        payment = payments.get(item['payment_id'])
        if not payment:
            raise SettlementError('退款后的加购付款凭证缺失')
        _, responsibilities = current_responsibilities(payment, lock=True)
        if payment.handovers.filter(status='ACCEPTED').exists() and payment.refund_records.exists():
            from .points_addon_handover_refund_facts import settlement_parts
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                verified = settlement_parts(payment.refund_records.order_by('created_at', 'pk').first())
            except DjangoValidationError as exc:
                raise SettlementError('接替加购退款后的逐段收益凭证待核对') from exc
            expected = {}
            for row in verified:
                key = (row['segment_id'], row['provider_account_id'])
                expected[key] = expected.get(key, 0) + row['provider_income_units']
            actual = {}
            for row in responsibilities:
                key = (row['segment_id'], row['provider_id'])
                actual[key] = actual.get(key, 0) + row['income']
            if actual != expected:
                raise SettlementError('接替加购退款后的个人收益不一致')
        grouped = {}
        for row in responsibilities:
            key = (row['segment_id'], row['provider_id'])
            grouped[key] = grouped.get(key, 0) + row['income']
        if sum(grouped.values()) != item['provider_units']:
            raise SettlementError('退款后的加购收益份额待核对')
        parts.extend(AddonFundingPart(payment.pk, account, segment, amount)
                     for (segment, account), amount in grouped.items())
    fields = ('paid_units', 'principal_units', 'gift_units', 'compensation_units', 'recovery_units', 'provider_units')
    return tuple(sum(item[key] for item in history) for key in fields), tuple(parts)


def _facts(plan, addon=(0, 0, 0, 0, 0, 0)):
    paid, principal, gift, compensation, recovery, provider = addon
    return (plan.net_coin_units - paid, plan.net_principal_units - principal,
            plan.net_gift_units - gift, plan.net_compensation_units - compensation,
            plan.planned_provider_external_units - recovery, plan.required_units,
            plan.provider_income_units - provider, plan.inviter_income_units, plan.store_income_units, *addon)


def _funding_rows(order):
    from django.db.models import Q
    rows = list(OrderPointsFundingAllocation.objects.select_for_update().filter(
        Q(redemption__order=order) | Q(service_order=order)).order_by('pk'))
    from .addon_funding_release import addon_release_totals
    addon_released = addon_release_totals(rows)
    result = []
    for row in rows:
        released = (sum(OrderPointsFundingRelease.objects.filter(allocation=row).values_list('amount', flat=True))
                    + addon_released[row.pk])
        if released > row.amount:
            raise SettlementError('退款后的补资释放金额异常')
        if row.amount - released:
            result.append((row.pk, row.amount - released))
    return result


def _prior_paid_person(order, posting, snapshots, assignments, addon_parts=()):
    """Use the original paid person without creating a second settlement."""
    import hashlib
    import json
    from .models import PointsFirstRefundPaidPersonEvidence, OrderPointsSettlement
    from .points_settlement_test import _verify_person
    evidence = PointsFirstRefundPaidPersonEvidence.objects.select_for_update().filter(
        posting=posting).select_related('person__settlement', 'person__segment',
                                        'person__report', 'person__income_transaction__wallet').first()
    if not evidence:
        return None
    person = evidence.person
    source = OrderPointsSettlement.objects.select_for_update().filter(order=order, pk=person.settlement_id).first()
    snapshot = next((row for row in snapshots if row.segment_id == person.segment_id), None)
    assignment = assignments.get(person.segment.provider_account_id)
    release = getattr(posting, 'funding_release_evidence', None)
    payload = [posting.pk, person.pk, person.amount, person.income_transaction_id, evidence.allocation_snapshot]
    from .points_funding_release import _net_allocations
    if (not source or source.redemption_id != posting.points_refund.redemption_id
            or source.finalized_at or source.inviter_transaction_id or source.store_transaction_id
            or source.persons.count() != 1 or not snapshot or not assignment
            or not release or release.original_settlement_id != source.pk
            or evidence.allocation_snapshot != _net_allocations(
                release.allocations, release.prior_addon_release_snapshot)[0]
            or evidence.evidence_hash != hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
            or person.settled_at >= posting.created_at
            or posting.shares.filter(account_id=person.segment.provider_account_id).exists()
            or person.amount != _person_amount(posting, snapshot)):
        raise SettlementError('首退前已付人员或原补资凭证异常')
    from .addon_main_deduction import deductions_at
    deduction = deductions_at(order, before=person.settled_at).get(person.segment.provider_account_id, 0)
    _verify_person(order, person, snapshot, assignment, deduction=deduction)
    from .points_first_paid_source import verify_original_addons
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        _, paid_addons = verify_original_addons(order, source, person, posting.created_at)
    except DjangoValidationError as exc:
        raise SettlementError('首退前已付加购收益凭证异常') from exc
    expected = {(part.payment_id, part.segment_id): part.provider_income_units for part in addon_parts
                if part.provider_account_id == person.segment.provider_account_id}
    if ({(row.payment_id, row.segment_id): row.amount for row in paid_addons} != expected
            or len(expected) != len(paid_addons)):
        raise SettlementError('首退前已付加购与退款后份额不一致')
    return person


def _check_final(order, settlement, plan, snapshots, assignments, addon_parts=()):
    persons = {row.segment_id: row for row in settlement.persons.select_for_update()
               .select_related('report', 'income_transaction__wallet')}
    prior = _prior_paid_person(order, settlement.posting, snapshots, assignments, addon_parts)
    if prior and prior.segment_id in persons:
        raise SettlementError('原已付人员被重复结算')
    if set(persons) | ({prior.segment_id} if prior else set()) != {row.segment_id for row in snapshots}:
        raise SettlementError('退款后的逐人结算凭证不完整')
    for snapshot in snapshots:
        if not prior or prior.segment_id != snapshot.segment_id:
            _check_person(order, settlement.posting, persons[snapshot.segment_id], snapshot,
                          assignments[snapshot.segment.provider_account_id])
    if addon_parts:
        from .addon_settlement import verify_addon_records
        paid = verify_addon_records(order, settlement, addon_parts, persons.values())
        prior_addons = ({(row.payment_id, row.segment_id) for row in
                         prior.settlement.addon_settlements.filter(segment_id=prior.segment_id)} if prior else set())
        if paid & prior_addons or paid | prior_addons != {(part.payment_id, part.segment_id) for part in addon_parts}:
            raise SettlementError('退款后的加购收益尚未全部结算')
    if (settlement.finalized_at != order.settled_at or not settlement.finalized_at
            or any(row.settled_at > settlement.finalized_at for row in persons.values())
            or (prior and prior.settled_at > settlement.finalized_at)):
        raise SettlementError('退款后的整单结算时间不一致')
    addon_contribution = max(0, settlement.addon_principal_units + settlement.addon_recovered_units
                             - settlement.addon_provider_units)
    if (order.settlement_store_funding != settlement.funding_units
            or order.settlement_addon_contribution != addon_contribution):
        raise SettlementError('退款后的整单补资汇总不一致')
    _check_destinations(order, settlement, plan)


def _check_destinations(order, settlement, plan):
    """Verify final store/referral credits for either refund settlement."""
    from .points_rounding_settlement import verify as verify_rounding
    rounding, destinations = verify_rounding(order, plan.store_income_units)
    for tx, amount, kind in ((settlement.inviter_transaction, plan.inviter_income_units, 'inviter'),
                             (settlement.store_transaction, plan.store_income_units - rounding, 'store')):
        if not amount:
            if tx:
                raise SettlementError('零金额不应产生结算流水')
            continue
        if (not tx or tx.order_id != order.pk or tx.amount != amount or tx.status != Transaction.Status.SUCCESS
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
            raise SettlementError('退款后的门店或推荐收益流水待核对')
        if kind == 'inviter':
            if (tx.tx_type != Transaction.TxType.INCOME or tx.remark != f'推荐分佣：{order.order_no}'
                    or (order.inviter_id and tx.wallet.user_id != order.inviter_id)
                    or (order.inviter_account_id and tx.wallet.account_id != order.inviter_account_id)):
                raise SettlementError('退款后的推荐收益归属待核对')
            if tx.wallet.account_id:
                from club_accounts.models import ClubAccount
                if ClubAccount.objects.filter(pk=tx.wallet.account_id, account_type=ClubAccount.AccountType.BOSS).exists():
                    from engagement.models import PointFundingLot
                    if not PointFundingLot.objects.filter(account_id=tx.wallet.account_id,
                        source_key=f'points-refund-settlement-referral:{settlement.pk}',
                        kind=PointFundingLot.Kind.PREPAID, remaining__lte=tx.amount).exists():
                        raise SettlementError('退款后的推荐收益本金来源待核对')
        else:
            from wallet.models import get_platform_wallet
            if (tx.tx_type != Transaction.TxType.SHOP_INCOME or tx.wallet_id != get_platform_wallet(for_update=True).pk
                    or tx.remark != f'平台收入：{order.order_no}'):
                raise SettlementError('退款后的门店收益归属待核对')
        destinations.append(tx.pk)
    actual = set(order.transactions.filter(status=Transaction.Status.SUCCESS).filter(
        Q(tx_type=Transaction.TxType.SHOP_INCOME) |
        Q(tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}')).values_list('pk', flat=True))
    if actual != set(destinations):
        raise SettlementError('退款后的门店或推荐收益流水数量异常')


def verified_person_display(report):
    """Read settlement status without moving money or advancing clocks."""
    if not enabled() or not report.order_id:
        return 'REVIEW', None
    from django.db import DatabaseError
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=report.order_id)
            from .financial_lock import lock_order_money
            lock_order_money()
            plan = plan_refunded_order(order.pk, allow_finalized=True)
            funding = verify_order_funding(order.pk, allow_finalized=True)
            posting = PointsNegotiationPosting.objects.get(points_refund__redemption__order=order)
            addon, addon_parts = _addon_facts(order, posting)
            if not any(item.get('account_id') == report.provider_account_id and item.get('provider_id') == report.provider_id
                       for item in posting.participants):
                return 'REVIEW', None
            settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=posting).first()
            if not settlement:
                unexpected = order.transactions.filter(tx_type=Transaction.TxType.INCOME,
                    status=Transaction.Status.SUCCESS, remark=f'订单收入：{order.order_no}').exists()
                return ('REVIEW' if order.settled_at or unexpected else 'WAITING'), None
            facts = (settlement.paid_units, settlement.principal_units, settlement.gift_units,
                     settlement.compensation_units, settlement.recovered_units, settlement.funding_units,
                     settlement.provider_units, settlement.inviter_units, settlement.store_units,
                     settlement.addon_paid_units, settlement.addon_principal_units, settlement.addon_gift_units,
                     settlement.addon_compensation_units, settlement.addon_recovered_units, settlement.addon_provider_units)
            uses = list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
            if not funding.fully_funded or facts != _facts(plan, addon) or uses != _funding_rows(order):
                return 'REVIEW', None
            from .points_service_participation import service_snapshots
            snapshots = service_snapshots(order)
            assignments = {row.provider_account_id: row for row in OrderProvider.objects.filter(
                order=order, pk__in=[item['assignment_id'] for item in posting.participants])}
            people = list(settlement.persons.select_related('report', 'income_transaction__wallet'))
            by_segment = {row.segment_id: row for row in snapshots}
            if (len(snapshots) != len(posting.participants) or len(assignments) != len(snapshots)
                    or any(row.segment_id not in by_segment for row in people)
                    or report.provider_account_id not in assignments
                    or assignments[report.provider_account_id].provider_id != report.provider_id):
                return 'REVIEW', None
            for person in people:
                snapshot = by_segment[person.segment_id]
                assignment = assignments.get(snapshot.segment.provider_account_id)
                if not assignment:
                    return 'REVIEW', None
                _check_person(order, posting, person, snapshot, assignment)
            prior = _prior_paid_person(order, posting, snapshots, assignments, addon_parts)
            if prior and any(person.segment_id == prior.segment_id for person in people):
                return 'REVIEW', None
            if addon_parts or settlement.addon_settlements.exists():
                from .addon_settlement import verify_addon_records
                verify_addon_records(order, settlement, addon_parts, people)
            settled_segments = {person.segment_id for person in people}
            if prior:
                settled_segments.add(prior.segment_id)
            for snapshot in snapshots:
                if snapshot.segment_id in settled_segments:
                    continue
                assignment = assignments.get(snapshot.segment.provider_account_id)
                if (not assignment or assignment.settled_at or order.transactions.filter(
                        tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                        remark=f'订单收入：{order.order_no}', wallet__user_id=assignment.provider_id).exists()):
                    return 'REVIEW', None
            if order.settled_at:
                _check_final(order, settlement, plan, snapshots, assignments, addon_parts)
            elif settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id:
                return 'REVIEW', None
            mine = (prior if prior and prior.report_id == report.pk else
                    next((person for person in people if person.report_id == report.pk), None))
            return ('SETTLED', mine.settled_at) if mine else ('REVIEW' if order.settled_at else 'WAITING', None)
    except (PointsFundingPlanError, SettlementError, ValidationError, DatabaseError, Order.DoesNotExist,
            PointsNegotiationPosting.DoesNotExist, PointsNegotiationPosting.MultipleObjectsReturned):
        return 'REVIEW', None


def settle_refunded_points_order(order, *, operator=None, operator_account=None, trace_id=None):
    """Reserve a fully funded order, then release each independently matured share."""
    if not enabled() or not transaction.get_connection().in_atomic_block:
        raise SettlementError('积分退款后的收益结算尚未开放')
    from .points_business_access import configured, permits
    if configured() and not (permits(order, phase='settlement', kind='service') or permits(order, phase='settlement', kind='addon')):
        raise SettlementError('积分退款结算上下文与订单不一致')
    with transaction.atomic():
        return _settle_refunded_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)


def _settle_refunded_points_order(order, *, operator=None, operator_account=None, trace_id=None):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.COMPLETED or not order.completed_at:
        return []
    from engagement.models import ConsumptionConfirmation
    confirmed = bool(order.consumption_confirmed_at or ConsumptionConfirmation.objects.filter(
        order=order, reversed_at__isnull=True).exists())
    if confirmed and not order.settled_at:
        raise SettlementError('退款后的会员实销已存在，须先核对')
    from .financial_lock import lock_order_money
    lock_order_money()
    from .models import OrderPointsRedemption
    from wallet.models import Wallet
    redemption = OrderPointsRedemption.objects.select_related('payment').filter(order=order).first()
    if not redemption:
        raise SettlementError('积分退款的原付款凭证缺失')
    wallet = Wallet.objects.select_for_update().filter(pk=redemption.payment.wallet_id).first()
    if not wallet or not wallet.is_active or wallet.account_id != order.customer_account_id or wallet.user_id != order.customer_id:
        raise SettlementError('积分退款的老板钱包归属待核对')
    try:
        plan = plan_refunded_order(order.pk, allow_finalized=True)
        funding = verify_order_funding(order.pk, allow_finalized=True)
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError('退款后的订单与补资凭证待核对') from exc
    if not funding.fully_funded:
        return []
    posting = PointsNegotiationPosting.objects.get(points_refund__redemption__order=order)
    addon, addon_parts = _addon_facts(order, posting)
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(order)
    assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update().filter(
        order=order, pk__in=[item['assignment_id'] for item in posting.participants])}
    if (len(snapshots) != len(posting.participants) or len(assignments) != len(snapshots)
            or sum(_person_amount(posting, row) for row in snapshots) != plan.provider_income_units - addon[5]):
        raise SettlementError('退款后的陪玩收益份额待核对')
    facts, uses = _facts(plan, addon), _funding_rows(order)
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=posting).first()
    if settlement:
        recorded = (settlement.paid_units, settlement.principal_units, settlement.gift_units,
                    settlement.compensation_units, settlement.recovered_units, settlement.funding_units,
                    settlement.provider_units, settlement.inviter_units, settlement.store_units,
                    settlement.addon_paid_units, settlement.addon_principal_units, settlement.addon_gift_units,
                    settlement.addon_compensation_units, settlement.addon_recovered_units, settlement.addon_provider_units)
        saved_uses = list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
        if recorded != facts or saved_uses != uses:
            from .models import OrderAddonFundingReleaseRun
            if (settlement.finalized_at or not OrderAddonFundingReleaseRun.objects.filter(
                    Q(refund__order=order) | Q(payment__intent__order=order),
                    refunded_main_snapshot__isnull=False).exists()):
                raise SettlementError('退款后的结算或补资快照待核对')
            by_segment = {row.segment_id: row for row in snapshots}
            existing = list(settlement.persons.select_for_update().select_related('report', 'income_transaction__wallet'))
            for person in existing:
                snap = by_segment.get(person.segment_id)
                assignment = assignments.get(snap.segment.provider_account_id) if snap else None
                if not snap or not assignment:
                    raise SettlementError('已结算陪玩的履约记录待核对')
                _check_person(order, posting, person, snap, assignment)
            if addon_parts:
                from .addon_settlement import verify_addon_records
                paid = verify_addon_records(order, settlement, addon_parts, existing)
                expected = {(part.payment_id, part.segment_id) for part in addon_parts
                            if part.segment_id in {person.segment_id for person in existing}}
                if paid != expected:
                    raise SettlementError('已结算陪玩的加购凭证待核对')
            fields = ('paid_units', 'principal_units', 'gift_units', 'compensation_units', 'recovered_units',
                      'funding_units', 'provider_units', 'inviter_units', 'store_units', 'addon_paid_units',
                      'addon_principal_units', 'addon_gift_units', 'addon_compensation_units',
                      'addon_recovered_units', 'addon_provider_units')
            for name, value in zip(fields, facts):
                setattr(settlement, name, value)
            settlement.save(update_fields=list(fields))
            settlement.funding_uses.all().delete()
            OrderPointsRefundSettlementFunding.objects.bulk_create([
                OrderPointsRefundSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount)
                for pk, amount in uses])
    else:
        if order.settled_at:
            raise SettlementError('退款后的结算凭证缺失')
        settlement = OrderPointsRefundSettlement.objects.create(posting=posting, paid_units=facts[0],
            principal_units=facts[1], gift_units=facts[2], compensation_units=facts[3], recovered_units=facts[4],
            funding_units=facts[5], provider_units=facts[6], inviter_units=facts[7], store_units=facts[8],
            addon_paid_units=facts[9], addon_principal_units=facts[10], addon_gift_units=facts[11],
            addon_compensation_units=facts[12], addon_recovered_units=facts[13], addon_provider_units=facts[14])
        OrderPointsRefundSettlementFunding.objects.bulk_create([
            OrderPointsRefundSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount) for pk, amount in uses])
    if order.settled_at:
        _check_final(order, settlement, plan, snapshots, assignments, addon_parts)
        from engagement.models import RefundedPointsConsumptionEvidence
        benefit = RefundedPointsConsumptionEvidence.objects.filter(settlement=settlement).first()
        if confirmed and not benefit:
            raise SettlementError('退款后的会员实销凭证缺失')
        if benefit:
            from engagement.refunded_points_confirmation import verify_evidence_locked
            verify_evidence_locked(benefit)
        return []
    if settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id:
        raise SettlementError('退款后的未完成结算状态异常')
    persons = {row.segment_id: row for row in settlement.persons.select_for_update()
               .select_related('report', 'income_transaction__wallet')}
    prior = _prior_paid_person(order, posting, snapshots, assignments, addon_parts)
    if prior and prior.segment_id in persons:
        raise SettlementError('原已付人员被重复结算')
    if set(persons) - {row.segment_id for row in snapshots}:
        raise SettlementError('退款后的结算人员异常')
    if addon_parts:
        from .addon_settlement import verify_addon_records
        paid_addons = verify_addon_records(order, settlement, addon_parts, persons.values())
        expected = {(part.payment_id, part.segment_id) for part in addon_parts if part.segment_id in persons}
        if paid_addons != expected:
            raise SettlementError('退款后的已结算陪玩加购凭证不完整')
    reports = {row.provider_account_id: row for row in ProviderReport.objects.select_for_update().filter(order=order)}
    from .aftersale_scope import applies_to
    from .services import _credit_wallet, missing_report_evidence, refresh_escort_status
    from wallet.income import hold_provider_income
    from common.logging_utils import new_trace_id
    now, trace_id = timezone.now(), trace_id or new_trace_id()
    cases = list(order.aftersales.filter(status='OPEN'))
    eligible = []
    for snapshot in snapshots:
        assignment = assignments.get(snapshot.segment.provider_account_id)
        if (not assignment or not assignment.provider_id or not LegacyAccountMap.objects.filter(
                account_id=assignment.provider_account_id, legacy_user_id=assignment.provider_id).exists()):
            raise SettlementError('退款后的陪玩账号归属待核对')
        person, report = persons.get(snapshot.segment_id), reports.get(assignment.provider_account_id)
        if prior and prior.segment_id == snapshot.segment_id:
            continue
        if person:
            _check_person(order, posting, person, snapshot, assignment)
            continue
        if assignment.settled_at or order.transactions.filter(tx_type=Transaction.TxType.INCOME,
                status=Transaction.Status.SUCCESS, remark=f'订单收入：{order.order_no}',
                wallet__user_id=assignment.provider_id).exists():
            raise SettlementError('陪玩已有结算记录但缺少退款后凭证')
        if (any(applies_to(case, assignment.provider_account_id) for case in cases)
                or now < _deadline(order, posting, assignment.provider_account_id)
                or not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at
                or report.submitted_at + timedelta(hours=order.service_freeze_hours) > now):
            continue
        amount = _person_amount(posting, snapshot)
        if (report.provider_id != assignment.provider_id or not report.audited_at
                or report.audited_at < report.submitted_at or report.audited_at > now
                or report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate
                or report.payout_amount != _report_amount(posting, snapshot, report) or missing_report_evidence(report)):
            raise SettlementError('退款后的报单金额或凭证待核对')
        eligible.append((snapshot, assignment, report, amount))
    profiles = {row.user_id: row for row in EscortProfile.objects.select_for_update().filter(
        user_id__in=sorted(item[1].provider_id for item in eligible)).order_by('user_id')}
    settled = []
    for snapshot, assignment, report, amount in eligible:
        profile = profiles.get(assignment.provider_id)
        if not profile or profile.account_id != assignment.provider_account_id:
            raise SettlementError('退款后的陪玩档案待核对')
        from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit
        from wallet.refund_deposit import enabled as deficit_enabled
        from wallet.refund_recovery_order import tracking_enabled
        has_gap = RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
        has_permit = RefundDepositRecoveryPermit.objects.filter(profile=profile, closed_at__isnull=True).exists()
        if ((has_gap or has_permit) and not deficit_enabled()) or (has_permit and not tracking_enabled()):
            raise SettlementError('陪玩退款押金缺口或补差机会尚未接通')
        income = _credit_wallet(order=order, amount=amount, tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}', event='order.points_refund_settle.provider', trace_id=trace_id,
            user_id=assignment.provider_id, account_id=assignment.provider_account_id, log_after_commit=True)
        hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                             apply_without_hold=True)
        person = OrderPointsRefundSettlementPerson.objects.create(settlement=settlement, segment=snapshot.segment,
            report=report, income_transaction=income, amount=amount, settled_at=now)
        if addon_parts:
            from .addon_settlement import settle_person_addons
            settle_person_addons(order, settlement, addon_parts, snapshot=snapshot, assignment=assignment,
                                 report=report, profile=profile, now=now, trace_id=trace_id)
        assignment.settled_at = now; assignment.save(update_fields=['settled_at'])
        if not amount:
            from wallet.refund_recovery_order import close_for_zero_points_refund
            close_for_zero_points_refund(profile, person)
        settled.append(assignment.provider_id)
    if settled:
        EscortProfile.objects.filter(user_id__in=settled).update(completed_order_count=F('completed_order_count') + 1)
        refresh_escort_status(provider_ids=settled, account_ids=[item[1].provider_account_id for item in eligible],
                              exclude_order_ids=[order.pk])
        from .notifier import notify_report_update
        for _, _, report, _ in eligible:
            transaction.on_commit(lambda report=report: notify_report_update(report))
    if not cases and settlement.persons.count() + (1 if prior else 0) == len(snapshots):
        if order.transactions.filter(status=Transaction.Status.SUCCESS, reversal_of__isnull=True,
                tx_type=Transaction.TxType.SHOP_INCOME).exists() or order.transactions.filter(
                    status=Transaction.Status.SUCCESS, reversal_of__isnull=True, tx_type=Transaction.TxType.INCOME,
                    remark=f'推荐分佣：{order.order_no}').exists():
            raise SettlementError('退款后的门店或推荐收益已有流水')
        if order.inviter_id or order.inviter_account_id:
            settlement.inviter_transaction = _credit_wallet(order=order, amount=plan.inviter_income_units,
                tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}',
                event='order.points_refund_settle.inviter', trace_id=trace_id,
                user_id=order.inviter_id, account_id=order.inviter_account_id, log_after_commit=True)
            tx = settlement.inviter_transaction
            if tx and tx.wallet.account_id:
                from club_accounts.models import ClubAccount
                if ClubAccount.objects.filter(pk=tx.wallet.account_id, account_type=ClubAccount.AccountType.BOSS).exists():
                    from engagement.models import PointFundingLot
                    PointFundingLot.objects.create(account_id=tx.wallet.account_id,
                        source_key=f'points-refund-settlement-referral:{settlement.pk}',
                        kind=PointFundingLot.Kind.PREPAID, remaining=tx.amount)
        from .points_rounding_settlement import post as post_rounding
        rounding = post_rounding(order, plan.store_income_units, trace_id)
        settlement.store_transaction = _credit_wallet(order=order, amount=plan.store_income_units - rounding,
            tx_type=Transaction.TxType.SHOP_INCOME, remark=f'平台收入：{order.order_no}',
            event='order.points_refund_settle.store', trace_id=trace_id, platform=True, log_after_commit=True)
        settlement.finalized_at = now
        settlement.save(update_fields=['inviter_transaction', 'store_transaction', 'finalized_at'])
        order.settlement_store_funding = settlement.funding_units
        order.settlement_addon_contribution = max(0, settlement.addon_principal_units + settlement.addon_recovered_units
                                                  - settlement.addon_provider_units)
        order.settled_at = now
        order.save(update_fields=['settled_at', 'settlement_store_funding', 'settlement_addon_contribution'])
        _check_final(order, settlement, plan, snapshots, assignments, addon_parts)
        if settlement.inviter_transaction_id:
            from wallet.notifier import notify_wallet_update
            transaction.on_commit(lambda wallet=settlement.inviter_transaction.wallet: notify_wallet_update(wallet))
    if settled or settlement.finalized_at:
        from engagement.confirmation import notify_committed_order
        notify_committed_order(order.pk)
    return settled
