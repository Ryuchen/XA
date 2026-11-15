"""Release verified negotiated service earnings after each overlapping clock."""
from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from club_accounts.models import LegacyAccountMap
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction, get_platform_wallet
from wallet.services import get_wallet
from .ordinary_addon_production import eligible as _ordinary_addon_eligible
from .financial_lock import lock_existing_order_money, lock_order_money
from .models import NegotiationExecution, OrderOriginalShareTransfer
from .negotiation_execution import verify_execution
from .settlement import SettlementError, assert_order_conserved


def _has_execution_marker(order):
    return any(isinstance(row.benefit_effects, dict) and row.benefit_effects.get('negotiation_execution_version') == 1
               for row in order.refund_records.all())


def _zero_report_id(order):
    """Avoid per-report lookups for normal orders; money paths always re-read."""
    from .negotiation_zero import MARKER, marked_execution_id
    if not isinstance(order.funding_terms, dict) or MARKER not in order.funding_terms:
        return None
    return marked_execution_id(order)


def execution_for_order(order, *, lock=False):
    """Read only the new receipt when an order explicitly carries its marker."""
    from .negotiation_zero import marked_execution_id
    from .negotiation_followup_accounting import marked_followup_id
    try:
        followup_id = marked_followup_id(order)
        zero_id = marked_execution_id(order)
    except ValidationError as exc:
        raise SettlementError('协商标记待核对') from exc
    if followup_id is not None:
        from .models import NegotiationFollowupLink
        query = NegotiationFollowupLink.objects.filter(pk=followup_id, order_id=order.pk).select_related('execution')
        link = (query.select_for_update() if lock else query).first()
        if not link:
            raise SettlementError('追加协商凭证缺失')
        return link.execution
    refunds = [row for row in order.refund_records.all()
               if isinstance(row.benefit_effects, dict) and row.benefit_effects.get('negotiation_execution_version') == 1]
    if zero_id is not None:
        if refunds:
            raise SettlementError('订单同时存在多种协商凭证，须先核对')
        if NegotiationExecution.objects.filter(zero_order_id=order.pk).exclude(pk=zero_id).exists():
            raise SettlementError('订单存在未关联的追加协商凭证')
        query = NegotiationExecution.objects.filter(pk=zero_id, zero_order_id=order.pk)
        execution = (query.select_for_update() if lock else query).first()
        if not execution:
            raise SettlementError('零退款协商执行凭证缺失')
        return execution
    if not refunds:
        return None
    if len(refunds) != 1:
        raise SettlementError('协商退款凭证数量异常')
    # An added zero-refund receipt must not be mistaken for an unaffected
    # provider if the chain marker is missing.  Older stores at migration
    # 0120 have no zero-order column, so inspect only when a refund exists.
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        columns = {item.name for item in connection.introspection.get_table_description(
            cursor, NegotiationExecution._meta.db_table)}
    if ('zero_order_id' in columns and
            NegotiationExecution.objects.filter(zero_order_id=order.pk).exists()):
        raise SettlementError('订单存在未关联的追加协商凭证')
    query = NegotiationExecution.objects.filter(refund_id=refunds[0].pk)
    execution = (query.select_for_update() if lock else query).first()
    if not execution:
        raise SettlementError('协商退款执行凭证缺失')
    return execution


def report_allowed(order, report):
    """Only a verified completed negotiation can reopen refunded-order reports.

    The caller owns the order lock for changes; serializers may call this in
    their own read transaction.  Verification itself also locks money evidence.
    """
    if not report.provider_account_id or not report.provider_id:
        return False
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.exists():
        from .points_funding_plan import PointsFundingPlanError
        from .points_report_test import verified_report_snapshot
        try:
            verified_report_snapshot(order, report.provider_account_id, report.provider_id)
            return True
        except PointsFundingPlanError as exc:
            raise SettlementError('积分协商退款报单凭证待核对') from exc
    try:
        zero_id = _zero_report_id(order) if order.payment_status == order.PaymentStatus.PAID else None
    except ValidationError as exc:
        raise SettlementError('零退款协商标记待核对') from exc
    if not zero_id and (order.payment_status not in (order.PaymentStatus.PARTIALLY_REFUNDED, order.PaymentStatus.REFUNDED)
                        or not _has_execution_marker(order)):
        return False
    with transaction.atomic():
        from .models import Order
        # Keep the same order -> money -> receipt lock order as execution and
        # settlement, including read-only report endpoints on MySQL.
        order = Order.objects.select_for_update().get(pk=order.pk)
        lock_existing_order_money()
        from .models import OrderTransferNegotiationEvidence
        if OrderTransferNegotiationEvidence.objects.filter(order=order).exists():
            try:
                from .ordinary_transfer_continuation import verify_locked
                verify_locked(order)
            except (ValidationError, ValueError) as exc:
                raise SettlementError('转单协商报单凭证待核对') from exc
            return order.providers.filter(provider_id=report.provider_id,
                                          provider_account_id=report.provider_account_id).exists()
        execution = execution_for_order(order, lock=True)
        if not execution or not execution.termination_processed_at:
            return False
        try:
            from .negotiation_followup_accounting import marked_followup_id
            followup_id = marked_followup_id(order)
            if followup_id is not None:
                from .models import NegotiationFollowupLink
                from .negotiation_followup_accounting import verify_followup_accounting, verify_followup_termination
                link = NegotiationFollowupLink.objects.select_for_update().get(pk=followup_id, order=order, execution=execution)
                from .ordinary_addon_followup_settlement import is_followup
                if is_followup(link):
                    from .ordinary_followup_capability import stage_enabled
                    if not stage_enabled('ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING'):
                        raise ValidationError('带加购的追加售后结算尚未开放')
                    from .ordinary_addon_followup_posting import verify_posting_locked
                    verify_posting_locked(link)
                else:
                    verify_followup_accounting(link)
                verify_followup_termination(link)
            elif zero_id:
                from .negotiation_zero import verify_zero_execution, verify_zero_termination
                if execution.zero_order_id != order.pk:
                    raise ValidationError('零退款协商订单不一致')
                verify_zero_execution(execution); verify_zero_termination(execution)
            else:
                expected = order.PaymentStatus.REFUNDED if execution.refund.amount == order.amount else order.PaymentStatus.PARTIALLY_REFUNDED
                if execution.refund.order_id != order.pk or order.payment_status != expected:
                    raise ValidationError('协商退款状态待核对')
                verify_execution(execution)
                from .negotiation_termination import verify_termination_evidence
                verify_termination_evidence(execution)
        except (ValidationError, ValueError) as exc:
            raise SettlementError('协商退款凭证待核对') from exc
        return order.providers.filter(provider_id=report.provider_id,
                                      provider_account_id=report.provider_account_id).exists()


def report_income(report, *, display_only=False):
    """Return the negotiated entitlement, without presenting the original as net."""
    from django.conf import settings
    if (display_only and report.order_id and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and not transaction.get_connection().in_atomic_block):
        from .points_business_access import report_scope
        try:
            with transaction.atomic(), report_scope(report.pk):
                return _report_income(report, display_only=display_only)
        except ValidationError:
            return True, None
    return _report_income(report, display_only=display_only)


def _report_income(report, *, display_only=False):
    if not report.order_id:
        return False, None
    order = report.order
    from .points_service_payment import display_has_points, needs_integrated_processing
    points_order = display_has_points(order) if display_only else needs_integrated_processing(order)
    if points_order and order.refund_records.exists():
        from .points_funding_plan import PointsFundingPlanError
        from .points_report_test import verified_report_snapshot
        from .models import PointsNegotiationPosting
        try:
            with transaction.atomic():
                snapshot = verified_report_snapshot(order, report.provider_account_id, report.provider_id)
                postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order))
                if len(postings) not in (1, 2):
                    return True, None
                shares = [share for posting in postings for share in posting.shares.filter(
                    account_id=report.provider_account_id)]
                if len(shares) > 1:
                    return True, None
                if len(postings) == 1 and isinstance(postings[0].participants, list):
                    prior = next((row.get('prior_addon_deduction', 0) for row in postings[0].participants
                                  if row.get('account_id') == report.provider_account_id), 0)
                    from .addon_main_deduction import deductions_at
                    current = deductions_at(order).get(report.provider_account_id, 0)
                    value = (shares[0].retained_income if shares else snapshot.income_at_entry - prior) - (current - prior)
                    return True, value if 0 <= value and current >= prior else None
                if shares:
                    return True, shares[0].retained_income
                return True, snapshot.income_at_entry
        except (PointsFundingPlanError, PointsNegotiationPosting.DoesNotExist,
                PointsNegotiationPosting.MultipleObjectsReturned):
            return True, None
    try:
        zero_id = _zero_report_id(order) if order.payment_status == 'PAID' else None
    except ValidationError:
        return True, None
    if not zero_id and (order.payment_status not in ('PARTIALLY_REFUNDED', 'REFUNDED') or not _has_execution_marker(order)):
        return False, None
    with transaction.atomic():
        try:
            from .models import Order
            order = Order.objects.select_for_update().get(pk=order.pk)
            lock_existing_order_money()
            execution = execution_for_order(order, lock=True)
            if not execution:
                return False, None
            if not report_allowed(order, report):
                return True, None
            from .negotiation_followup_accounting import marked_followup_id
            followup_id = marked_followup_id(order)
            if followup_id is not None:
                from .models import NegotiationFollowupLink, NegotiationExecutionShare
                link = NegotiationFollowupLink.objects.get(pk=followup_id, order=order, execution=execution)
                from .ordinary_addon_followup_settlement import is_followup
                if is_followup(link):
                    facts = link.order.points_settlement.ordinary_refund_addon_evidence.facts['people']
                    prior = next((row for row in facts if row['account_id'] == report.provider_account_id and row['affected']), None)
                    if prior:
                        return True, prior['main_income']
                shares = list(NegotiationExecutionShare.objects.filter(
                    execution_id__in=[link.previous_id, link.execution_id], account_id=report.provider_account_id))
                return (True, shares[0].retained_income) if len(shares) == 1 else (True, None)
            affected = execution.shares.filter(account_id=report.provider_account_id).first()
            assignment = order.providers.filter(provider_id=report.provider_id,
                                                  provider_account_id=report.provider_account_id).first()
            if not assignment:
                return True, None
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                return True, affected.retained_income if affected else assignment.provider_income
            from .addon_main_deduction import deductions_at
            prior = deductions_at(order, before=execution.created_at).get(report.provider_account_id, 0)
            current = deductions_at(order).get(report.provider_account_id, 0)
            base = affected.retained_income if affected else assignment.provider_income - prior
            amount = base - (current - prior)
            return True, amount if 0 <= amount and current >= prior else None
        except (SettlementError, ValidationError):
            return True, None


def _check_credit(tx, *, order, amount, account_id=None, user_id=None, kind='INCOME', platform=False):
    if not amount:
        if tx is not None:
            raise SettlementError('零收益却存在结算流水')
        return
    wallet = get_platform_wallet(for_update=True) if platform else get_wallet(
        user_id=user_id, account_id=account_id, for_update=True)
    if (not tx or tx.order_id != order.pk or tx.wallet_id != wallet.pk or tx.tx_type != kind
            or tx.status != 'SUCCESS' or tx.amount != amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.balance_after != tx.balance_before + tx.amount):
        raise SettlementError('协商收益结算流水待核对')
    if kind == 'INCOME' and not platform and tx.remark == f'订单收入：{order.order_no}':
        from wallet.refund_deposit import protection_enabled, _verify_application
        if protection_enabled():
            from wallet.models import RefundDepositDeficit, RefundDepositIncomeApplication
            application = RefundDepositIncomeApplication.objects.filter(source_transaction=tx).select_related('profile').first()
            if application:
                if application.profile.account_id != account_id or application.profile.user_id != user_id:
                    raise SettlementError('收益补押金账户不一致')
                try:
                    _verify_application(application, source=tx, profile=application.profile)
                except ValueError as exc:
                    raise SettlementError('收益补押金流水异常') from exc
            elif RefundDepositDeficit.objects.filter(account_id=account_id, created_at__lte=tx.created_at).exists():
                raise SettlementError('收益缺少退款押金补差记录')


def _credit(order, *, amount, kind, remark, event, trace_id, account_id=None, user_id=None, platform=False):
    if not amount:
        return None
    wallet = get_platform_wallet(for_update=True) if platform else get_wallet(
        user_id=user_id, account_id=account_id, for_update=True)
    if not wallet.is_active or wallet.balance + amount > 2147483647:
        raise SettlementError('收益钱包不可用或余额超过系统范围')
    from .services import _credit_wallet
    return _credit_wallet(order=order, amount=amount, tx_type=kind, remark=remark, event=event,
                          trace_id=trace_id, user_id=user_id, account_id=account_id, platform=platform)


def negotiated_aftersale_start(order, execution, assignment, share):
    """A service-time termination starts at agreement; later refunds do not reset it."""
    if share and (execution.completed_by_negotiation or assignment.released_at == execution.accepted_at):
        return execution.accepted_at
    return order.completed_at


def _verify_settled_assignment(order, execution, assignment, share, report):
    """Verify one already-paid share without releasing another participant."""
    from .services import missing_report_evidence
    account_id, user_id = assignment.provider_account_id, assignment.provider_id
    amount = share.retained_income if share else assignment.provider_income
    if not assignment.settled_at or not report or not report.submitted_at:
        raise SettlementError('陪玩结算凭证不完整')
    if (report.status != ProviderReport.Status.APPROVED or report.provider_id != user_id
            or report.provider_account_id != account_id or report.payout_amount != assignment.provider_income
            or missing_report_evidence(report) or assignment.settled_at < order.completed_at
            or assignment.settled_at < report.submitted_at + timedelta(hours=order.service_freeze_hours)):
        raise SettlementError('已结算陪玩的报单或时限凭证异常')
    if share:
        if (share.settled_at != assignment.settled_at or report.pk != share.settled_report_id
                or report.submitted_at != share.report_submitted_at
                or assignment.settled_at < negotiated_aftersale_start(
                    order, execution, assignment, share) + timedelta(hours=order.service_aftersale_hours)):
            raise SettlementError('已结算陪玩的报单或时限凭证异常')
        if amount and (not share.income_transaction or share.income_transaction.wallet.account_id != account_id
                       or share.income_transaction.wallet.user_id != user_id):
            raise SettlementError('协商收益结算流水待核对')
        credits = list(order.transactions.filter(tx_type='INCOME', status='SUCCESS', reversal_of__isnull=True,
            wallet__account_id=account_id, wallet__user_id=user_id, remark=f'订单收入：{order.order_no}'))
        if (len(credits) != (1 if amount else 0)
                or (credits and credits[0].pk != share.income_transaction_id)):
            raise SettlementError('协商收益结算流水待核对')
        _check_credit(share.income_transaction, order=order, amount=amount, account_id=account_id, user_id=user_id)
        from wallet.refund_recovery_order import tracking_enabled
        if tracking_enabled():
            from wallet.models import RefundDepositRecoveryPermit
            permit = RefundDepositRecoveryPermit.objects.filter(zero_negotiated_share=share).first()
            if permit and (amount != 0 or permit.account_id != account_id or permit.order_id != order.pk
                    or permit.profile.account_id != account_id or permit.close_reason != 'ZERO_NEGOTIATED_SETTLED'
                    or not permit.closed_at or permit.income_application_id or permit.zero_income_settlement_id):
                raise SettlementError('协商零收益补差凭证异常')
    else:
        evidence = execution.unaffected_report_evidence.get(str(assignment.pk))
        if (not isinstance(evidence, dict) or evidence.get('report_id') != report.pk
                or evidence.get('submitted_at') != report.submitted_at.isoformat()
                or evidence.get('settled_at') != assignment.settled_at.isoformat()
                or assignment.settled_at < order.completed_at + timedelta(hours=order.service_aftersale_hours)):
            raise SettlementError('未受影响陪玩的报单或时限凭证异常')
        allocations = execution.refund.negotiated_allocation if execution.refund_id else execution.zero_allocation
        prior = next((item for item in allocations
                      if item.get('share_id') == assignment.pk), None)
        if prior and prior.get('settled') and (
                not report.audited_at or not report.auditor_id or not report.auditor_account_id
                or not report.submitted_at <= report.audited_at <= assignment.settled_at
                or evidence.get('audited_at') != report.audited_at.isoformat()
                or evidence.get('auditor_id') != report.auditor_id
                or evidence.get('auditor_account_id') != report.auditor_account_id
                or not LegacyAccountMap.objects.filter(account_id=report.auditor_account_id,
                                                       legacy_user_id=report.auditor_id).exists()):
            raise SettlementError('未受影响陪玩的审核凭证异常')
        credits = list(order.transactions.filter(tx_type='INCOME', status='SUCCESS', reversal_of__isnull=True,
            wallet__account_id=account_id, wallet__user_id=user_id, remark=f'订单收入：{order.order_no}'))
        if (len(credits) != (1 if amount else 0)
                or evidence.get('income_transaction_id') != (credits[0].pk if credits else None)):
            raise SettlementError('未受影响陪玩的结算流水待核对')
        _check_credit(credits[0] if credits else None, order=order, amount=amount, account_id=account_id, user_id=user_id)


def report_settlement_snapshot(report):
    """Read a negotiated report's own verified status without doing settlement."""
    if not report.order_id:
        return None
    try:
        zero_id = _zero_report_id(report.order) if report.order.payment_status == 'PAID' else None
    except ValidationError:
        return ('REVIEW', None)
    if not zero_id and (report.order.payment_status not in ('PARTIALLY_REFUNDED', 'REFUNDED')
                        or not _has_execution_marker(report.order)):
        return None
    from .models import Order
    from wallet.services import WalletAddressingError
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=report.order_id)
        try:
            lock_existing_order_money()
            execution = execution_for_order(order, lock=True)
            if not execution:
                return None
            if not report_allowed(order, report):
                return ('REVIEW', None)
            from django.conf import settings
            from .negotiation_followup_accounting import marked_followup_id
            followup_id = marked_followup_id(order)
            if followup_id is not None:
                from .models import NegotiationFollowupLink
                from .ordinary_addon_followup_settlement import is_followup, _load_locked, _verify_person
                link = NegotiationFollowupLink.objects.get(pk=followup_id, order=order, execution=execution)
                if is_followup(link):
                    link, settlement, economics, fact = _load_locked(order, link, require_funded=False)
                    if report.provider_account_id == fact['account_id']:
                        person = _verify_person(order, link, settlement, economics, fact)
                    else:
                        person = settlement.persons.filter(report_id=report.pk).first()
                    return ('SETTLED', person.settled_at) if person else ('WAITING', None)
            from .models import OrderOrdinaryRefundAddonSettlementEvidence
            from .points_checkout_test import ordinary_refund_production_order
            if (((ordinary_refund_production_order(order) or _ordinary_addon_eligible(order)) or getattr(settings, 'ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING', False))
                    and OrderOrdinaryRefundAddonSettlementEvidence.objects.filter(settlement__order=order).exists()):
                from .ordinary_refunded_addon_settlement import verify_recorded_locked
                settlement, paid = verify_recorded_locked(order)
                person = next((row for row in paid if row.report_id == report.pk), None)
                return ('SETTLED', person.settled_at) if person else ('WAITING', None)
            if not isinstance(execution.unaffected_report_evidence, dict):
                raise SettlementError('未受影响陪玩的报单凭证异常')
            assignment = order.providers.select_for_update().filter(provider_id=report.provider_id,
                provider_account_id=report.provider_account_id).first()
            share = execution.shares.filter(account_id=report.provider_account_id).first()
            from .negotiation_followup_accounting import marked_followup_id
            followup_id = marked_followup_id(order)
            if followup_id is not None:
                from .models import NegotiationFollowupLink, NegotiationExecutionShare
                link = NegotiationFollowupLink.objects.select_related('previous', 'execution').get(
                    pk=followup_id, order=order, execution=execution)
                shares = list(NegotiationExecutionShare.objects.filter(
                    execution_id__in=[link.previous_id, link.execution_id], account_id=report.provider_account_id))
                if not assignment or len(shares) != 1 or shares[0].assignment_id != assignment.pk:
                    raise SettlementError('追加协商陪玩份额待核对')
                share = shares[0]
                if not assignment.settled_at:
                    if share.settled_at:
                        raise SettlementError('追加协商陪玩结算状态不一致')
                    return ('WAITING', None)
                current_report = ProviderReport.objects.select_for_update().get(pk=report.pk, order=order)
                owner = link.previous if share.execution_id == link.previous_id else link.execution
                _verify_settled_assignment(order, owner, assignment, share, current_report)
                return ('SETTLED', assignment.settled_at)
            if (not assignment or order.service_clock_version is None or order.service_aftersale_hours is None
                    or order.service_freeze_hours is None or not order.in_service_at
                    or execution.accepted_at < order.in_service_at
                    or execution.termination_processed_at < execution.accepted_at
                    or (not execution.zero_order_id and not order.refunded_at)
                    or (execution.completed_by_negotiation and order.completed_at != execution.accepted_at)):
                raise SettlementError('协商结算时间或参与人待核对')
            if not assignment.settled_at:
                if (share and share.settled_at) or (not share and
                        str(assignment.pk) in execution.unaffected_report_evidence):
                    raise SettlementError('协商陪玩结算状态不一致')
                return ('WAITING', None)
            if order.status != order.Status.COMPLETED or not order.completed_at:
                raise SettlementError('协商订单尚未完成')
            current_report = ProviderReport.objects.select_for_update().get(pk=report.pk, order=order)
            _verify_settled_assignment(order, execution, assignment, share, current_report)
            return ('SETTLED', assignment.settled_at)
        except (SettlementError, ValidationError, ValueError, WalletAddressingError):
            return ('REVIEW', None)


def settle_due_negotiated(order, execution, *, now=None, operator=None, operator_account=None, trace_id=None):
    """The caller holds the order; each affected person's clock starts at consent."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('协商收益结算必须在门店事务内执行')
    if order.status != order.Status.COMPLETED or not order.completed_at:
        return []
    from .negotiation_followup_accounting import marked_followup_id
    try:
        followup_id = marked_followup_id(order)
    except ValidationError as exc:
        raise SettlementError('追加协商标记待核对') from exc
    if followup_id is not None:
        from .models import NegotiationFollowupLink
        from .negotiation_followup_settlement import settle_followup_internal
        link = NegotiationFollowupLink.objects.filter(pk=followup_id, order=order, execution=execution).first()
        if not link:
            raise SettlementError('追加协商凭证缺失')
        return settle_followup_internal(order, link, now=now, trace_id=trace_id)
    from .points_checkout_test import ordinary_refund_production_order
    if ordinary_refund_production_order(order) or _ordinary_addon_eligible(order):
        # All ordinary funded refunds share the same reservation and posting
        # path, including gift/compensation shortfalls and rounding proceeds.
        from .ordinary_refunded_addon_settlement import settle_due_locked
        return settle_due_locked(order, trace_id=trace_id)
    lock_order_money()
    execution = NegotiationExecution.objects.select_for_update().get(pk=execution.pk)
    is_zero = execution.zero_order_id is not None
    try:
        if is_zero:
            from .negotiation_zero import verify_zero_execution, verify_zero_termination
            affected = verify_zero_execution(execution)
            verify_zero_termination(execution)
        else:
            affected = verify_execution(execution)
            from .negotiation_termination import verify_termination_evidence
            verify_termination_evidence(execution)
    except (ValidationError, ValueError) as exc:
        raise SettlementError('协商终止凭证待核对' if is_zero else '协商退款凭证待核对') from exc
    receipt_order_id = execution.zero_order_id if is_zero else execution.refund.order_id
    if (not execution.termination_processed_at or receipt_order_id != order.pk
            or order.settlement_mode != order.SettlementMode.REPORT_APPROVAL or order.service_clock_version is None
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100):
        raise SettlementError('协商终止或订单时限快照待核对')
    refund_amount = 0 if is_zero else execution.refund.amount
    recovered_amount = 0 if is_zero else execution.refund.recovered_amount
    referral_rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if order.inviter_commission != order.amount * referral_rate // 100:
        raise SettlementError('主单推荐佣金快照待核对')
    net_inviter_commission = (order.amount - refund_amount) * referral_rate // 100
    net_store_income = order.shop_income + order.inviter_commission - net_inviter_commission
    expected_status = (order.PaymentStatus.PAID if is_zero else order.PaymentStatus.REFUNDED
                       if refund_amount == order.amount else order.PaymentStatus.PARTIALLY_REFUNDED)
    if (order.payment_status != expected_status or (not is_zero and not order.refunded_at)
            or (is_zero and order.refunded_at is not None) or not order.in_service_at
            or execution.accepted_at < order.in_service_at
            or execution.termination_processed_at < execution.accepted_at
            or (execution.completed_by_negotiation and order.completed_at != execution.accepted_at)):
        raise SettlementError('协商退款状态或时间凭证待核对')
    if order.refund_records.count() != (0 if is_zero else 1) or order.funding_terms is None:
        raise SettlementError('协商退款后的历史付款或追加退款待核对')
    # The fallback cannot infer cash from promotional balances. Such orders
    # must use the funded settlement path, which reserves the whole shortfall
    # (including the designated rounding surplus) before paying anyone.
    funding = list(order.funding_allocations.select_for_update().order_by('pk'))
    if (not funding or sum(row.amount for row in funding) != order.amount
            or any(row.kind in ('GIFT', 'COMPENSATION') and row.amount > row.refunded_amount for row in funding)):
        raise SettlementError('订单赠送或赔付来源须先通过门店补资结算')
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        raise SettlementError('协商退款后的转单收益待核对')
    rows = list(order.providers.select_for_update().order_by('pk'))
    allocations = execution.zero_allocation if is_zero else execution.refund.negotiated_allocation
    if (len(rows) != len(allocations) or [row.pk for row in rows] != [item.get('share_id') for item in allocations]
            or any(not row.provider_id or not row.provider_account_id for row in rows)
            or sum(row.settlement_base for row in rows) != order.amount
            or sum(row.provider_income for row in rows) != order.provider_income):
        raise SettlementError('协商订单陪玩份额待核对')
    assert_order_conserved(order.amount, [row.provider_income for row in rows], order.inviter_commission, order.shop_income)
    by_account = {row.account_id: row for row in affected}
    if not isinstance(execution.unaffected_report_evidence, dict):
        raise SettlementError('未受影响陪玩的报单凭证异常')
    unaffected_keys = {str(row.pk) for row in rows if row.provider_account_id not in by_account}
    if set(execution.unaffected_report_evidence) - unaffected_keys:
        raise SettlementError('未受影响陪玩的报单凭证异常')
    if sum(row.retained_income for row in affected) + sum(row.provider_income for row in rows
            if row.provider_account_id not in by_account) + order.inviter_commission + order.shop_income + execution.rounding_to_store_units != (
            order.amount - refund_amount + recovered_amount):
        raise SettlementError('协商退款后的收益不守恒')
    from .aftersale_scope import applies_to
    open_cases = list(order.aftersales.filter(status='OPEN'))
    reports = {row.provider_account_id: row for row in order.provider_reports.select_for_update().filter(
        provider_account_id__in=[row.provider_account_id for row in rows])}
    now = now or timezone.now()
    if now < order.completed_at:
        return []
    from .services import missing_report_evidence, refresh_escort_status
    from wallet.income import hold_provider_income
    settled_users, settled_accounts = [], []
    for assignment in rows:
        account_id, user_id = assignment.provider_account_id, assignment.provider_id
        if LegacyAccountMap.objects.filter(account_id=account_id, legacy_user_id=user_id).count() != 1:
            raise SettlementError('陪玩结算身份待核对')
        share = by_account.get(account_id)
        amount = share.retained_income if share else assignment.provider_income
        if assignment.settled_at:
            _verify_settled_assignment(order, execution, assignment, share, reports.get(account_id))
            continue
        if share and share.settled_at:
            raise SettlementError('协商陪玩结算状态不一致')
        if not share and str(assignment.pk) in execution.unaffected_report_evidence:
            raise SettlementError('未受影响陪玩的结算状态不一致')
        start = negotiated_aftersale_start(order, execution, assignment, share)
        if start + timedelta(hours=order.service_aftersale_hours) > now or any(applies_to(case, account_id) for case in open_cases):
            continue
        report = reports.get(account_id)
        if not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at:
            continue
        if report.submitted_at + timedelta(hours=order.service_freeze_hours) > now:
            continue
        if (report.provider_id != user_id or report.payout_amount != assignment.provider_income
                or missing_report_evidence(report)):
            raise SettlementError('陪玩报单或原收益凭证待核对')
        if order.transactions.filter(tx_type='INCOME', status='SUCCESS', wallet__account_id=account_id,
                                     remark=f'订单收入：{order.order_no}').exists():
            raise SettlementError('陪玩已有未关联的收益流水')
        profile = EscortProfile.objects.select_for_update().filter(account_id=account_id, user_id=user_id).first()
        if not profile:
            raise SettlementError('陪玩资料与收益归属不一致')
        income = _credit(order, amount=amount, kind='INCOME', remark=f'订单收入：{order.order_no}',
            event='order.settle.negotiated', trace_id=trace_id, account_id=account_id, user_id=user_id)
        hold_provider_income(income, profile=profile, hold_hours=0,
                             deposit_percent=order.service_deposit_percent, apply_without_hold=True)
        assignment.settled_at = now; assignment.save(update_fields=['settled_at'])
        if share:
            share.settled_at = now; share.income_transaction = income
            share.settled_report = report; share.report_submitted_at = report.submitted_at
            share.save(update_fields=['settled_at', 'income_transaction', 'settled_report', 'report_submitted_at'])
            if not income:
                from wallet.refund_recovery_order import close_for_zero_negotiated
                close_for_zero_negotiated(profile, share)
        else:
            execution.unaffected_report_evidence = {**execution.unaffected_report_evidence,
                str(assignment.pk): {'report_id': report.pk, 'submitted_at': report.submitted_at.isoformat(),
                                     'settled_at': now.isoformat(), 'income_transaction_id': income.pk if income else None}}
            execution.save(update_fields=['unaffected_report_evidence'])
            if not amount:
                from wallet.refund_recovery_order import close_for_zero_unaffected
                close_for_zero_unaffected(profile, assignment, execution, report)
        settled_users.append(user_id); settled_accounts.append(account_id)
    if settled_users:
        EscortProfile.objects.filter(user_id__in=settled_users).update(completed_order_count=F('completed_order_count') + 1)
        refresh_escort_status(provider_ids=settled_users, account_ids=settled_accounts, exclude_order_ids=[order.pk])
    if not open_cases and all(row.settled_at for row in rows):
        if execution.finalized_at or order.settled_at:
            if execution.finalized_at != order.settled_at:
                raise SettlementError('协商订单最终结算状态不一致')
            _check_credit(execution.shop_transaction, order=order, amount=net_store_income,
                          kind='SHOP_INCOME', platform=True)
            _check_credit(execution.rounding_transaction, order=order, amount=execution.rounding_to_store_units,
                          kind='SHOP_INCOME', platform=True)
            _check_credit(execution.inviter_transaction, order=order, amount=net_inviter_commission,
                          account_id=order.inviter_account_id, user_id=order.inviter_id)
        else:
            if order.transactions.filter(Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}'),
                                         status='SUCCESS', reversal_of__isnull=True).exists():
                raise SettlementError('门店或推荐收益已有未关联流水')
            if net_inviter_commission and not (order.inviter_id or order.inviter_account_id):
                raise SettlementError('推荐收益身份待核对')
            execution.inviter_transaction = _credit(order, amount=net_inviter_commission, kind='INCOME',
                remark=f'推荐分佣：{order.order_no}', event='order.settle.inviter', trace_id=trace_id,
                account_id=order.inviter_account_id, user_id=order.inviter_id)
            if execution.inviter_transaction and execution.inviter_transaction.wallet.account_id:
                from club_accounts.models import ClubAccount
                if ClubAccount.objects.filter(pk=execution.inviter_transaction.wallet.account_id,
                        account_type=ClubAccount.AccountType.BOSS).exists():
                    from engagement.models import PointFundingLot
                    PointFundingLot.objects.create(account_id=execution.inviter_transaction.wallet.account_id,
                        source_key=f'negotiation-referral:{execution.pk}', kind=PointFundingLot.Kind.PREPAID,
                        remaining=net_inviter_commission)
            execution.shop_transaction = _credit(order, amount=net_store_income,
                kind='SHOP_INCOME', remark=f'平台收入：{order.order_no}', event='order.settle.platform',
                trace_id=trace_id, platform=True)
            execution.rounding_transaction = _credit(order, amount=execution.rounding_to_store_units,
                kind='SHOP_INCOME', remark=f'退款取整差额：{order.order_no}', event='order.settle.rounding',
                trace_id=trace_id, platform=True)
            execution.finalized_at = now
            execution.save(update_fields=['inviter_transaction', 'shop_transaction', 'rounding_transaction', 'finalized_at'])
            order.settled_at = now; order.save(update_fields=['settled_at'])
    return settled_users


def verify_finalized(order, execution):
    """Recheck all paid evidence before granting downstream consumption benefits."""
    execution.refresh_from_db(fields=['finalized_at'])
    if (not order.settled_at or execution.finalized_at != order.settled_at
            or not order.providers.exists() or order.providers.filter(settled_at__isnull=True).exists()):
        raise SettlementError('协商订单最终结算凭证不完整')
    from django.conf import settings
    from .models import NegotiationFollowupLink
    from .ordinary_addon_followup_settlement import is_followup
    link = NegotiationFollowupLink.objects.filter(order=order, execution=execution).first()
    if link and is_followup(link):
        from .ordinary_followup_capability import stage_enabled
        if not stage_enabled('ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING'):
            raise SettlementError('带加购的追加售后结算尚未开放')
        from .ordinary_addon_followup_settlement import verify_finalized_locked
        verify_finalized_locked(order, link)
        return
    from .models import OrderOrdinaryRefundAddonSettlementEvidence
    from .points_checkout_test import ordinary_refund_production_order
    if (((ordinary_refund_production_order(order) or _ordinary_addon_eligible(order)) or getattr(settings, 'ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING', False))
            and OrderOrdinaryRefundAddonSettlementEvidence.objects.filter(settlement__order_id=order.pk).exists()):
        from .ordinary_refunded_addon_settlement import verify_recorded_locked
        settlement, _ = verify_recorded_locked(order)
        if settlement.finalized_at != order.settled_at:
            raise SettlementError('协商订单最终结算凭证不完整')
        return
    if settle_due_negotiated(order, execution):
        raise SettlementError('协商订单最终结算凭证不完整')
