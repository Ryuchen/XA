"""Reserve the fully funded settlement for an ordinary refund with add-ons."""
from dataclasses import asdict
import hashlib
import json

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .ordinary_addon_production import eligible as _ordinary_addon_eligible
from .models import (NegotiationExecution, OrderAddonPayment, OrderOrdinaryRefundAddonSettlementEvidence,
                     OrderPointsSettlement, OrderPointsSettlementFunding)
from .points_checkout_test import require_order_funding_access, ordinary_refund_production_order
from .points_funding_plan import PointsFundingPlanError
from .settlement import SettlementError


def _production_order(order):
    from .negotiation_zero import ordinary_funding_enabled
    from .ordinary_funding_continuation import refunded_continuation_active
    return (refunded_continuation_active(order) or ordinary_refund_production_order(order) or _ordinary_addon_eligible(order)
            or ordinary_funding_enabled(order))


def _economics_locked(order, plan, uses):
    """Freeze each original participant's remaining main and add-on income."""
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    from .addon_main_deduction import deductions_at
    from .negotiation_execution import verify_execution
    from .points_service_participation import service_snapshots
    from club_accounts.models import LegacyAccountMap

    if (order.status != order.Status.COMPLETED or not order.completed_at or order.service_clock_version is None
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100):
        raise SettlementError('普通退款及加购的结算时限快照待核对')
    from .negotiation_settlement import execution_for_order
    execution = execution_for_order(order, lock=True)
    if not execution:
        raise SettlementError('协商终止凭证缺失')
    if execution.zero_order_id:
        from .negotiation_zero import verify_zero_execution
        shares = {row.account_id: row for row in verify_zero_execution(execution)}
    else:
        shares = {row.account_id: row for row in verify_execution(execution)}
    prior = deductions_at(order, before=execution.created_at)
    current = deductions_at(order)
    snapshots = service_snapshots(order)
    assignments = {row.provider_account_id: row for row in order.providers.select_for_update().order_by('pk')}
    if (len(snapshots) != order.required_provider_count or len(assignments) != len(snapshots)
            or {row.segment.provider_account_id for row in snapshots} != set(assignments)):
        raise SettlementError('普通退款的陪玩履约席位待核对')
    people = []
    for snap in snapshots:
        segment = snap.segment
        assignment = assignments[segment.provider_account_id]
        affected = shares.get(assignment.provider_account_id)
        earlier, total = prior.get(assignment.provider_account_id, 0), current.get(assignment.provider_account_id, 0)
        income = (affected.retained_income if affected else assignment.provider_income - earlier) - (total - earlier)
        if (not assignment.provider_id or not LegacyAccountMap.objects.filter(
                    account_id=assignment.provider_account_id, legacy_user_id=assignment.provider_id).exists()
                or not segment.started_at or segment.attribution.order_id != order.pk
                or snap.incoming_basis != assignment.settlement_base or snap.income_at_entry != assignment.provider_income
                or snap.commission_type != assignment.commission_type or snap.commission_rate != assignment.commission_rate
                or snap.commission_fixed != assignment.commission_fixed
                or snap.game_commission_snapshot != assignment.game_commission_snapshot
                or total < earlier or income < 0):
            raise SettlementError('普通退款的陪玩收益快照待核对')
        people.append({'assignment_id': assignment.pk, 'account_id': assignment.provider_account_id,
                       'user_id': assignment.provider_id, 'segment_id': segment.pk, 'main_income': income,
                       'affected': affected is not None})
    addons = []
    for payment in OrderAddonPayment.objects.select_for_update().filter(intent__order=order).select_related('intent').order_by('pk'):
        assert_no_pending_handover(payment)
        _, responsibilities = current_responsibilities(payment, lock=True)
        for row in responsibilities:
            if (row['provider_id'] not in assignments or row['segment_id'] not in {item['segment_id'] for item in people}
                    or not any(item['account_id'] == row['provider_id'] and item['segment_id'] == row['segment_id']
                               for item in people)):
                raise SettlementError('加购收益的履约归属待核对')
            addons.append({'payment_id': payment.pk, 'segment_id': row['segment_id'],
                           'account_id': row['provider_id'], 'basis': row['basis'], 'income': row['income']})
    if sum(row['main_income'] for row in people) + sum(row['income'] for row in addons) != plan.combined.provider_income_units:
        raise SettlementError('主单与加购的陪玩收益不守恒')
    return execution, {'execution_id': execution.pk, 'order_id': order.pk, 'people': people, 'addons': addons,
        'addon_history': list(plan.addon_history),
        'funding_uses': [list(row) for row in uses], 'combined': asdict(plan.combined),
        'completed_at': order.completed_at.isoformat() if order.completed_at else None,
        'clock_version': order.service_clock_version,
        'aftersale_hours': order.service_aftersale_hours, 'freeze_hours': order.service_freeze_hours,
        'deposit_percent': order.service_deposit_percent}


def _digest(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _parts(economics):
    from .addon_funding import AddonFundingPart
    grouped = {}
    for row in economics['addons']:
        key = (row['payment_id'], row['segment_id'])
        prior = grouped.get(key)
        if prior and prior[0] != row['account_id']:
            raise SettlementError('加购同一履约段的陪玩归属不一致')
        grouped[key] = (row['account_id'], (prior[1] if prior else 0) + row['income'])
    return [AddonFundingPart(payment_id, account_id, segment_id, income)
            for (payment_id, segment_id), (account_id, income) in grouped.items()]


def _verify_paid_people(order, settlement, execution, economics, *, segment_ids=None):
    """Reconcile already released individual income before another release."""
    from datetime import timedelta
    from django.utils import timezone
    from wallet.models import ProviderReport, Transaction
    from .addon_settlement import verify_addon_records
    from .negotiation_settlement import _check_credit
    from .services import missing_report_evidence

    expected = {row['segment_id']: row for row in economics['people']}
    person_query = settlement.persons.select_for_update().select_related(
        'segment', 'report', 'income_transaction__wallet').order_by('pk')
    if segment_ids is not None:
        person_query = person_query.filter(segment_id__in=segment_ids)
    people = list(person_query)
    assignments = {row.pk: row for row in order.providers.select_for_update()}
    for person in people:
        fact = expected.get(person.segment_id)
        assignment = assignments.get(fact['assignment_id']) if fact else None
        report = person.report
        share = execution.shares.filter(account_id=fact['account_id']).first() if fact else None
        if not fact or not assignment:
            raise SettlementError('普通退款已结算陪玩身份凭证待核对')
        from .negotiation_settlement import negotiated_aftersale_start
        start = negotiated_aftersale_start(order, execution, assignment, share)
        if (not fact or not assignment or person.amount != fact['main_income']
                or assignment.provider_account_id != fact['account_id'] or assignment.provider_id != fact['user_id']
                or assignment.settled_at != person.settled_at or not start or person.settled_at < order.completed_at
                or person.settled_at > timezone.now()
                or person.settled_at < start + timedelta(hours=order.service_aftersale_hours)
                or report.order_id != order.pk or report.provider_account_id != fact['account_id']
                or report.provider_id != fact['user_id'] or report.status != ProviderReport.Status.APPROVED
                or not report.submitted_at or not report.audited_at or report.audited_at < report.submitted_at
                or report.audited_at > person.settled_at
                or person.settled_at < report.submitted_at + timedelta(hours=order.service_freeze_hours)
                or report.payout_amount != assignment.provider_income or missing_report_evidence(report)):
            raise SettlementError('普通退款已结算陪玩的报单或收益凭证异常')
        if share:
            if (share.settled_at != person.settled_at or share.income_transaction_id != person.income_transaction_id
                    or share.settled_report_id != report.pk or share.report_submitted_at != report.submitted_at):
                raise SettlementError('普通退款已结算陪玩的协商凭证异常')
        else:
            saved = execution.unaffected_report_evidence.get(str(assignment.pk))
            if (not isinstance(saved, dict) or saved.get('report_id') != report.pk
                    or saved.get('settled_at') != person.settled_at.isoformat()
                    or saved.get('submitted_at') != report.submitted_at.isoformat()
                    or saved.get('income_transaction_id') != person.income_transaction_id):
                raise SettlementError('普通退款其他陪玩的结算凭证异常')
        credits = list(order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
            wallet__account_id=fact['account_id'], wallet__user_id=fact['user_id'],
            remark=f'订单收入：{order.order_no}'))
        if len(credits) != (1 if person.amount else 0) or (credits and credits[0].pk != person.income_transaction_id):
            raise SettlementError('普通退款已结算陪玩的主单流水异常')
        _check_credit(person.income_transaction, order=order, amount=person.amount,
                      account_id=fact['account_id'], user_id=fact['user_id'])
        from wallet.models import RefundDepositRecoveryPermit
        permit = RefundDepositRecoveryPermit.objects.filter(zero_ordinary_addon_person=person).first()
        if permit and (person.amount or person.income_transaction_id or permit.account_id != fact['account_id']
                or permit.profile.account_id != fact['account_id'] or permit.order_id != order.pk
                or not permit.closed_at or permit.close_reason != 'ZERO_ADDON_SETTLED'
                or permit.income_application_id or permit.zero_income_settlement_id
                or permit.zero_negotiated_share_id or permit.zero_points_refund_person_id
                or settlement.addon_settlements.filter(segment_id=person.segment_id).exclude(amount=0).exists()):
            raise SettlementError('普通退款及加购的零收益补差凭证异常')
    parts = _parts(economics)
    paid = verify_addon_records(order, settlement, parts, people, segment_ids=segment_ids)
    expected_addons = {(part.payment_id, part.segment_id) for part in parts
                       if part.segment_id in {person.segment_id for person in people}}
    if paid != expected_addons:
        raise SettlementError('普通退款已结算陪玩的加购收益凭证不完整')
    by_segment = {row['segment_id']: row for row in economics['people']}
    addon_query = settlement.addon_settlements.select_related('income_transaction__wallet')
    if segment_ids is not None:
        addon_query = addon_query.filter(segment_id__in=segment_ids)
    for row in addon_query:
        tx = row.income_transaction
        if tx and (tx.wallet.user_id != by_segment[row.segment_id]['user_id']
                   or tx.reversal_of_id or tx.reversals.exists()):
            raise SettlementError('普通退款已结算陪玩的加购收益流水异常')
    return people


def verify_recorded_locked(order, *, allow_unfunded_followup=False):
    """Read a reservation and posted people without modifying settlement state."""
    from .ordinary_refunded_addon_funding import plan_locked
    from .points_funding_evidence import verified_order_allocations_locked
    from .addon_funding_release import addon_release_totals
    from .financial_lock import lock_order_money
    from .negotiation_execution import _lock_reverse_addon_people
    lock_order_money()
    _lock_reverse_addon_people(order)
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
    if not settlement:
        raise SettlementError('普通退款及加购结算预留缺失')
    try:
        plan = plan_locked(order.pk, _allow_settled=True, _allow_finalized=bool(order.settled_at))
        allocations = verified_order_allocations_locked(order)
        released = addon_release_totals(allocations)
        uses = [(row.pk, row.amount - released[row.pk]) for row in allocations if row.amount > released[row.pk]]
        execution, economics = _economics_locked(order, plan, uses)
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError('普通退款及加购的资金凭证待核对') from exc
    combined = plan.combined
    expected = (combined.net_coin_units, combined.net_principal_units, combined.planned_provider_external_units,
        combined.net_gift_units, combined.net_compensation_units, plan.required,
        combined.provider_income_units, combined.inviter_income_units, combined.store_income_units)
    recorded = (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
        settlement.gift_units, settlement.compensation_units, settlement.funding_units,
        settlement.provider_units, settlement.inviter_units, settlement.store_units)
    evidence = OrderOrdinaryRefundAddonSettlementEvidence.objects.select_for_update().filter(settlement=settlement).first()
    from .ordinary_followup_capability import stage_enabled
    if ((plan.remaining and not (allow_unfunded_followup and stage_enabled(
                'ORDINARY_ADDON_FOLLOWUP_UNFUNDED_INTERNAL_TESTING')))
            or plan.releasable or sum(amount for _, amount in uses) != plan.allocated
            or settlement.redemption_id or recorded != expected
            or list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount')) != uses
            or not evidence or evidence.execution_id != execution.pk or evidence.facts != economics
            or evidence.facts_hash != _digest(evidence.facts)):
        raise SettlementError('普通退款及加购结算凭证不一致')
    people = _verify_paid_people(order, settlement, execution, economics)
    if plan.remaining and people:
        raise SettlementError('整单补资不足时存在已结算陪玩')
    if order.settled_at:
        _verify_destinations(order, settlement, execution, economics, people)
    elif (settlement.finalized_at or settlement.inviter_transaction_id or settlement.store_transaction_id
          or evidence.rounding_transaction_id
          or order.addon_refunds.filter(rounding_transaction__isnull=False).exists()
          or execution.finalized_at or execution.inviter_transaction_id or execution.shop_transaction_id
          or execution.rounding_transaction_id):
        raise SettlementError('普通退款及加购的最终结算状态异常')
    return settlement, people


def _verify_destinations(order, settlement, execution, economics, people):
    """Final settlement is valid only when every real destination is reconciled."""
    from django.db.models import Q
    from wallet.models import Transaction
    from .negotiation_settlement import _check_credit
    evidence = OrderOrdinaryRefundAddonSettlementEvidence.objects.select_for_update().get(settlement=settlement)
    addon_refunds = list(order.addon_refunds.select_related('rounding_transaction').order_by('pk'))
    rounding = execution.rounding_to_store_units + sum(row.rounding_surplus for row in addon_refunds)
    if rounding > settlement.store_units:
        raise SettlementError('退款取整差额资金待核对')
    addon_contribution = max(0, sum(row['principal_units'] + row['recovery_units'] - row['provider_units']
                                    for row in economics['addon_history']))
    if (not order.settled_at or settlement.finalized_at != order.settled_at
            or execution.finalized_at != order.settled_at or len(people) != len(economics['people'])
            or settlement.inviter_transaction_id != execution.inviter_transaction_id
            or settlement.store_transaction_id != execution.shop_transaction_id
            or evidence.rounding_transaction_id != execution.rounding_transaction_id
            or order.settlement_store_funding != settlement.funding_units
            or order.settlement_addon_contribution != addon_contribution):
        raise SettlementError('普通退款及加购的最终结算凭证不完整')
    _check_credit(settlement.inviter_transaction, order=order, amount=settlement.inviter_units,
                  account_id=order.inviter_account_id, user_id=order.inviter_id)
    _check_credit(settlement.store_transaction, order=order, amount=settlement.store_units - rounding,
                  kind='SHOP_INCOME', platform=True)
    _check_credit(evidence.rounding_transaction, order=order, amount=execution.rounding_to_store_units,
                  kind='SHOP_INCOME', platform=True)
    for refund in addon_refunds:
        _check_credit(refund.rounding_transaction, order=order, amount=refund.rounding_surplus,
                      kind='SHOP_INCOME', platform=True)
    if settlement.inviter_transaction_id and order.inviter_account_id:
        from club_accounts.models import ClubAccount
        if ClubAccount.objects.filter(pk=order.inviter_account_id, account_type=ClubAccount.AccountType.BOSS).exists():
            from engagement.models import PointFundingLot
            if not PointFundingLot.objects.filter(account_id=order.inviter_account_id,
                    source_key=f'negotiation-referral:{execution.pk}', kind=PointFundingLot.Kind.PREPAID,
                    remaining__lte=settlement.inviter_units).exists():
                raise SettlementError('普通退款推荐佣金本金来源待核对')
    actual = set(order.transactions.filter(status=Transaction.Status.SUCCESS).filter(
        Q(tx_type=Transaction.TxType.SHOP_INCOME) |
        Q(tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}')).values_list('pk', flat=True))
    expected = {pk for pk in (settlement.inviter_transaction_id, settlement.store_transaction_id,
                              evidence.rounding_transaction_id) if pk}
    expected.update(row.rounding_transaction_id for row in addon_refunds if row.rounding_transaction_id)
    if actual != expected:
        raise SettlementError('普通退款门店或推荐收入流水异常')


def reserve_locked(order, *, allow_unfunded_followup=False):
    """Record a balanced whole-order reservation without paying any person.

    All allocations belong to the same store and are revalidated under locks;
    subsequent income posting must verify these facts again before release.
    """
    from .ordinary_followup_capability import stage_enabled
    require_order_funding_access(order)
    if (not transaction.get_connection().in_atomic_block
            or not (_production_order(order) or getattr(settings, 'ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING', False))):
        raise SettlementError('普通退款及加购的整单结算尚未开放')
    from .ordinary_refunded_addon_funding import plan_locked
    from .points_funding_evidence import verified_order_allocations_locked
    from .addon_funding_release import addon_release_totals
    from .financial_lock import lock_order_money
    from .negotiation_execution import _lock_reverse_addon_people
    lock_order_money()
    _lock_reverse_addon_people(order)
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
    if settlement:
        from .models import OrdinaryRefundedAddonPaidRevision
        pending = list(OrdinaryRefundedAddonPaidRevision.objects.filter(settlement=settlement)
                       .select_related('run', 'refund').order_by('pk'))
        current_evidence = getattr(settlement, 'ordinary_refund_addon_evidence', None)
        if current_evidence:
            matching = [row for row in pending if isinstance(row.snapshot, dict)
                        and row.snapshot.get('before_hash') == current_evidence.facts_hash
                        and row.snapshot.get('before_facts') == current_evidence.facts]
            if len(matching) > 1:
                raise SettlementError('存在多个未修订的已付退款版本')
            if matching:
                from .ordinary_refunded_addon_paid_revision import advance_locked
                try:
                    if not advance_locked(order, matching[0]):
                        return None
                    settlement.refresh_from_db()
                except ValidationError as exc:
                    raise SettlementError('已付退款后的结算清单待核对') from exc
    has_paid_people = bool(settlement and settlement.persons.exists())
    try:
        plan = plan_locked(order.pk, _allow_settled=has_paid_people)
        if plan.releasable or (plan.remaining and not allow_unfunded_followup):
            return None
        allocations = verified_order_allocations_locked(order)
        released = addon_release_totals(allocations)
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError('普通退款及加购的补资凭证待核对') from exc
    uses = [(row.pk, row.amount - released[row.pk]) for row in allocations if row.amount > released[row.pk]]
    if (sum(amount for _, amount in uses) != plan.allocated or plan.allocated > plan.required
            or (plan.remaining and (has_paid_people or not allow_unfunded_followup
                or not stage_enabled('ORDINARY_ADDON_FOLLOWUP_UNFUNDED_INTERNAL_TESTING')))):
        raise SettlementError('普通退款及加购的补资分配不一致')
    combined = plan.combined
    facts = (combined.net_coin_units, combined.net_principal_units, combined.planned_provider_external_units,
             combined.net_gift_units, combined.net_compensation_units, plan.required,
             combined.provider_income_units, combined.inviter_income_units, combined.store_income_units)
    if (combined.net_coin_units < 10 and not (
            combined.net_coin_units == 0
            and (_production_order(order)
                 or getattr(settings, 'ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING', False))
            and not plan.addon_history)):
        raise SettlementError('零实付的普通退款及加购结算尚未接通')
    try:
        execution, economics = _economics_locked(order, plan, uses)
    except ValidationError as exc:
        raise SettlementError('普通退款及加购的陪玩份额待核对') from exc
    if settlement:
        stored = (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
                  settlement.gift_units, settlement.compensation_units, settlement.funding_units,
                  settlement.provider_units, settlement.inviter_units, settlement.store_units)
        existing_uses = list(settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
        if (settlement.redemption_id or settlement.finalized_at or settlement.inviter_transaction_id
                or settlement.store_transaction_id or (not has_paid_people and settlement.addon_settlements.exists())):
            raise SettlementError('普通退款及加购的结算预留已变化，请先核对')
        if has_paid_people and (stored != facts or existing_uses != uses):
            raise SettlementError('部分陪玩已结算，后续资金变化尚待核对')
        if stored == facts and existing_uses != uses:
            raise SettlementError('普通退款及加购的补资使用记录待核对')
        # Nothing has been paid yet: a verified change to add-on refunds or
        # funding can safely replace the pending reservation.
        if stored != facts:
            (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
             settlement.gift_units, settlement.compensation_units, settlement.funding_units,
             settlement.provider_units, settlement.inviter_units, settlement.store_units) = facts
            settlement.save(update_fields=['paid_units', 'principal_units', 'recovery_units', 'gift_units',
                'compensation_units', 'funding_units', 'provider_units', 'inviter_units', 'store_units'])
        if existing_uses != uses:
            settlement.funding_uses.all().delete()
            OrderPointsSettlementFunding.objects.bulk_create([
                OrderPointsSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount)
                for pk, amount in uses])
    else:
        settlement = OrderPointsSettlement.objects.create(order=order, redemption=None,
            paid_units=facts[0], principal_units=facts[1], recovery_units=facts[2], gift_units=facts[3],
            compensation_units=facts[4], funding_units=facts[5], provider_units=facts[6],
            inviter_units=facts[7], store_units=facts[8])
        OrderPointsSettlementFunding.objects.bulk_create([
            OrderPointsSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount) for pk, amount in uses])
    evidence = OrderOrdinaryRefundAddonSettlementEvidence.objects.select_for_update().filter(settlement=settlement).first()
    digest = _digest(economics)
    if evidence:
        if evidence.execution_id != execution.pk or evidence.facts_hash != _digest(evidence.facts):
            raise SettlementError('普通退款及加购的收益凭证待核对')
        if evidence.facts != economics:
            if has_paid_people:
                raise SettlementError('部分陪玩已结算，后续收益变化尚待核对')
            evidence.facts, evidence.facts_hash = economics, digest
            evidence.save(update_fields=['facts', 'facts_hash', 'updated_at'])
    else:
        OrderOrdinaryRefundAddonSettlementEvidence.objects.create(settlement=settlement, execution=execution,
                                                                    facts=economics, facts_hash=digest)
    if has_paid_people:
        _verify_paid_people(order, settlement, execution, economics)
    return settlement


def settle_due_locked(order, *, trace_id=None):
    """Release each matured person's main and add-on income exactly once."""
    from datetime import timedelta
    from django.db.models import F
    from django.utils import timezone
    from club_accounts.models import LegacyAccountMap
    from common.logging_utils import new_trace_id
    from users.models import EscortProfile
    from wallet.income import hold_provider_income
    from wallet.models import ProviderReport, RefundDepositDeficit, RefundDepositRecoveryPermit, Transaction
    from .aftersale_scope import applies_to
    from .addon_settlement import settle_person_addons
    from .models import OrderPointsSettlementPerson, OrderSegmentFinancialSnapshot
    from .services import _credit_wallet, missing_report_evidence, refresh_escort_status

    if not (_production_order(order) or getattr(settings, 'ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING', False)):
        raise SettlementError('普通退款及加购的逐人收益结算尚未开放')
    if order.settled_at:
        # A completed whole-order settlement is an idempotent replay, but
        # only after its actual per-person and final destination postings have
        # been reconciled.  Do not enter the mutable reservation path again.
        verify_recorded_locked(order)
        return []
    settlement = reserve_locked(order)
    if not settlement:
        return []
    evidence = settlement.ordinary_refund_addon_evidence
    execution, economics = evidence.execution, evidence.facts
    people = _verify_paid_people(order, settlement, execution, economics)
    paid_segments = {person.segment_id for person in people}
    snapshots = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.select_for_update()
                 .filter(segment_id__in=[item['segment_id'] for item in economics['people']]).select_related('segment')}
    assignments = {row.pk: row for row in order.providers.select_for_update().order_by('pk')}
    reports = {row.provider_account_id: row for row in ProviderReport.objects.select_for_update().filter(order=order)}
    cases = list(order.aftersales.filter(status='OPEN'))
    now, trace_id = timezone.now(), trace_id or new_trace_id()
    parts, eligible = _parts(economics), []
    for fact in economics['people']:
        if fact['segment_id'] in paid_segments:
            continue
        assignment = assignments.get(fact['assignment_id'])
        snapshot = snapshots.get(fact['segment_id'])
        report = reports.get(fact['account_id'])
        share = execution.shares.filter(account_id=fact['account_id']).first()
        if (not assignment or not snapshot or assignment.settled_at
                or not LegacyAccountMap.objects.filter(account_id=fact['account_id'], legacy_user_id=fact['user_id']).exists()
                or order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                    wallet__account_id=fact['account_id'], remark=f'订单收入：{order.order_no}').exists()):
            raise SettlementError('普通退款待结算陪玩的身份或流水待核对')
        from .negotiation_settlement import negotiated_aftersale_start
        start = negotiated_aftersale_start(order, execution, assignment, share)
        if (not start or now < order.completed_at or now < start + timedelta(hours=order.service_aftersale_hours)
                or any(applies_to(case, fact['account_id']) for case in cases)
                or not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at
                or report.submitted_at + timedelta(hours=order.service_freeze_hours) > now):
            continue
        if (report.provider_id != fact['user_id'] or not report.audited_at or report.audited_at < report.submitted_at
                or report.audited_at > now or report.amount != assignment.settlement_base
                or report.commission_rate != assignment.commission_rate
                or report.payout_amount != assignment.provider_income or missing_report_evidence(report)):
            raise SettlementError('普通退款待结算陪玩的报单凭证待核对')
        eligible.append((fact, assignment, snapshot, report, share))
    profiles = {row.user_id: row for row in EscortProfile.objects.select_for_update().filter(
        user_id__in=sorted(item[0]['user_id'] for item in eligible)).order_by('account_id', 'pk')}
    settled = []
    for fact, assignment, snapshot, report, share in eligible:
        profile = profiles.get(fact['user_id'])
        if not profile or profile.account_id != fact['account_id']:
            raise SettlementError('普通退款陪玩资料待核对')
        from wallet.refund_deposit import enabled as deficit_enabled
        from wallet.refund_recovery_order import tracking_enabled
        gap = RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
        permit = RefundDepositRecoveryPermit.objects.filter(profile=profile, closed_at__isnull=True).exists()
        if ((gap or permit) and not deficit_enabled()) or (permit and not tracking_enabled()):
            raise SettlementError('普通退款押金缺口或补差机会尚未接通')
        income = _credit_wallet(order=order, amount=fact['main_income'], tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}', event='order.ordinary_refund_addon_settle.provider', trace_id=trace_id,
            user_id=fact['user_id'], account_id=fact['account_id'], log_after_commit=True)
        hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                             apply_without_hold=True)
        person = OrderPointsSettlementPerson.objects.create(settlement=settlement, segment=snapshot.segment,
            report=report, income_transaction=income, amount=fact['main_income'], settled_at=now)
        settle_person_addons(order, settlement, parts, snapshot=snapshot, assignment=assignment,
                             report=report, profile=profile, now=now, trace_id=trace_id)
        assignment.settled_at = now
        assignment.save(update_fields=['settled_at'])
        if permit and not fact['main_income'] and not any(
                part.provider_account_id == fact['account_id'] and part.provider_income_units for part in parts):
            from wallet.refund_recovery_order import close_for_zero_ordinary_addon
            close_for_zero_ordinary_addon(profile, person)
        if share:
            share.settled_at, share.income_transaction = now, income
            share.settled_report, share.report_submitted_at = report, report.submitted_at
            share.save(update_fields=['settled_at', 'income_transaction', 'settled_report', 'report_submitted_at'])
        else:
            execution.unaffected_report_evidence = {**execution.unaffected_report_evidence,
                str(assignment.pk): {'report_id': report.pk, 'submitted_at': report.submitted_at.isoformat(),
                    'settled_at': now.isoformat(), 'income_transaction_id': income.pk if income else None}}
            execution.save(update_fields=['unaffected_report_evidence'])
        settled.append(fact['user_id'])
    if settled:
        EscortProfile.objects.filter(user_id__in=settled).update(completed_order_count=F('completed_order_count') + 1)
        refresh_escort_status(provider_ids=settled, account_ids=[item[0]['account_id'] for item in eligible],
                              exclude_order_ids=[order.pk])
        from .notifier import notify_report_update
        for _, _, _, report, _ in eligible:
            transaction.on_commit(lambda report=report: notify_report_update(report))
    if ((_production_order(order) or getattr(settings, 'ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING', False))
            and not cases and settlement.persons.count() == len(economics['people'])):
        from django.db.models import Q
        people = _verify_paid_people(order, settlement, execution, economics)
        if (order.transactions.filter(status=Transaction.Status.SUCCESS, reversal_of__isnull=True).filter(
                Q(tx_type=Transaction.TxType.SHOP_INCOME) |
                Q(tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}')).exists()
                or settlement.finalized_at or execution.finalized_at or order.settled_at):
            raise SettlementError('普通退款及加购已有最终结算流水')
        if settlement.inviter_units and not (order.inviter_id or order.inviter_account_id):
            raise SettlementError('普通退款推荐人身份待核对')
        settlement.inviter_transaction = _credit_wallet(order=order, amount=settlement.inviter_units,
            tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}',
            event='order.ordinary_refund_addon_settle.inviter', trace_id=trace_id,
            user_id=order.inviter_id, account_id=order.inviter_account_id, log_after_commit=True)
        if settlement.inviter_transaction_id and order.inviter_account_id:
            from club_accounts.models import ClubAccount
            if ClubAccount.objects.filter(pk=order.inviter_account_id, account_type=ClubAccount.AccountType.BOSS).exists():
                from engagement.models import PointFundingLot
                PointFundingLot.objects.create(account_id=order.inviter_account_id,
                    source_key=f'negotiation-referral:{execution.pk}', kind=PointFundingLot.Kind.PREPAID,
                    remaining=settlement.inviter_units)
        evidence = OrderOrdinaryRefundAddonSettlementEvidence.objects.select_for_update().get(settlement=settlement)
        addon_refunds = list(order.addon_refunds.select_for_update().order_by('pk'))
        rounding = execution.rounding_to_store_units + sum(row.rounding_surplus for row in addon_refunds)
        if rounding > settlement.store_units or evidence.rounding_transaction_id or any(row.rounding_transaction_id for row in addon_refunds):
            raise SettlementError('退款取整差额资金待核对')
        settlement.store_transaction = _credit_wallet(order=order, amount=settlement.store_units - rounding,
            tx_type=Transaction.TxType.SHOP_INCOME, remark=f'平台收入：{order.order_no}',
            event='order.ordinary_refund_addon_settle.store', trace_id=trace_id, platform=True, log_after_commit=True)
        evidence.rounding_transaction = _credit_wallet(order=order, amount=execution.rounding_to_store_units,
            tx_type=Transaction.TxType.SHOP_INCOME, remark=f'退款取整差额：{order.order_no}',
            event='order.ordinary_refund_addon_settle.rounding', trace_id=trace_id, platform=True, log_after_commit=True)
        evidence.save(update_fields=['rounding_transaction', 'updated_at'])
        for refund in addon_refunds:
            refund.rounding_transaction = _credit_wallet(order=order, amount=refund.rounding_surplus,
                tx_type=Transaction.TxType.SHOP_INCOME, remark=f'加购退款取整差额：{order.order_no}（退款 {refund.pk}）',
                event='order.ordinary_refund_addon_settle.addon_rounding', trace_id=trace_id,
                platform=True, log_after_commit=True)
            refund.save(update_fields=['rounding_transaction'])
        settlement.finalized_at = now
        settlement.save(update_fields=['inviter_transaction', 'store_transaction', 'finalized_at'])
        execution.inviter_transaction, execution.shop_transaction = settlement.inviter_transaction, settlement.store_transaction
        execution.rounding_transaction = evidence.rounding_transaction
        execution.finalized_at = now
        execution.save(update_fields=['inviter_transaction', 'shop_transaction', 'rounding_transaction', 'finalized_at'])
        order.settlement_store_funding = settlement.funding_units
        order.settlement_addon_contribution = max(0, sum(
            row['principal_units'] + row['recovery_units'] - row['provider_units'] for row in economics['addon_history']))
        order.settled_at = now
        order.save(update_fields=['settlement_store_funding', 'settlement_addon_contribution', 'settled_at'])
        _verify_destinations(order, settlement, execution, economics, people)
    return settled
