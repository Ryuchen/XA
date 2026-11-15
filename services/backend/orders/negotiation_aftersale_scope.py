"""Protect completed negotiated service scopes when a new case is requested."""
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def closed_provider_accounts(order):
    """Return verified agreed scopes; caller holds this order's transaction lock.

    An old store without the negotiation migrations continues to use its
    ordinary aftersale flow. Evidence that exists but cannot be reconciled is
    never treated as an unrestricted order.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('售后范围核对必须在订单事务内执行')
    from .models import OrderPlatformSubsidy
    if OrderPlatformSubsidy.objects.filter(order=order).exists():
        from .subsidy_execution import table_available, verify_execution
        if table_available():
            from .models import SubsidyAgreementExecution
            execution = SubsidyAgreementExecution.objects.select_for_update().filter(order=order).select_related(
                'order', 'proposal__case').first()
            if execution:
                try:
                    return set(verify_execution(execution))
                except ValidationError as exc:
                    raise ValidationError('安全箱协商凭证待核对，请联系客服处理') from exc
        else:
            from django.conf import settings
            if getattr(settings, 'VIP_SAFE_BOX_NEGOTIATION_ENABLED', False):
                raise ValidationError('安全箱协商凭证表尚未就绪')
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.exists():
        from .models import PointsNegotiationPosting
        connection = transaction.get_connection()
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
        if PointsNegotiationPosting._meta.db_table not in tables:
            raise ValidationError('积分协商凭证缺失，请联系客服核对')
        from .financial_lock import lock_existing_order_money
        lock_existing_order_money()
        postings = list(PointsNegotiationPosting.objects.select_for_update().filter(
            points_refund__redemption__order=order).order_by('points_refund__refund_id'))
        if (len(postings) == 2 and order.refund_records.count() == 2
                and isinstance(postings[0].participants, dict)):
            from .points_transfer_followup_termination import verify_closed_or_settled_locked as verify_followup
            second = verify_followup(postings[1])
            first_scope = set(postings[0].shares.values_list('account_id', flat=True))
            second_scope = set(second.shares.values_list('account_id', flat=True))
            if not first_scope or not second_scope or first_scope.intersection(second_scope):
                raise ValidationError('积分协商退款范围待核对')
            return first_scope | second_scope
        if (len(postings) == 2 and order.refund_records.count() == 2
                and isinstance(postings[0].participants, list)):
            from .points_followup_termination import verify_followup_termination_locked
            second = verify_followup_termination_locked(postings[1])
            first_scope = set(postings[0].shares.values_list('account_id', flat=True))
            second_scope = set(second.shares.values_list('account_id', flat=True))
            if not first_scope or not second_scope or first_scope.intersection(second_scope):
                raise ValidationError('积分协商退款范围待核对')
            return first_scope | second_scope
        if len(postings) != 1 or order.refund_records.count() != 1:
            raise ValidationError('积分协商退款范围待核对')
        from .points_negotiation_termination import verify_termination_locked
        posting = verify_termination_locked(postings[0])
        return set(posting.shares.values_list('account_id', flat=True))
    from .models import NegotiationExecution, NegotiationFollowupLink
    from .negotiation_followup_accounting import marked_followup_id
    from .negotiation_zero import marked_execution_id
    from .negotiation_settlement import execution_for_order
    from .settlement import SettlementError
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        columns = ({item.name for item in connection.introspection.get_table_description(
            cursor, NegotiationExecution._meta.db_table)}
            if NegotiationExecution._meta.db_table in tables else set())
    refund_rows = list(order.refund_records.values_list('pk', 'benefit_effects'))
    marked_refund = any(isinstance(effects, dict) and effects.get('negotiation_execution_version') == 1
                        for _, effects in refund_rows)
    try:
        followup_id, zero_id = marked_followup_id(order), marked_execution_id(order)
        if not columns:
            if marked_refund or followup_id or zero_id:
                raise ValidationError('协商凭证缺失')
            return set()
        condition = Q(refund_id__in=[row[0] for row in refund_rows])
        if 'zero_order_id' in columns:
            condition |= Q(zero_order_id=order.pk)
        has_receipt = NegotiationExecution.objects.filter(condition).exists()
        if not (marked_refund or followup_id or zero_id or has_receipt):
            return set()
        # The receipt verifier reads the newer zero-receipt columns even for a
        # positive refund. Old stores with a receipt must wait for migration;
        # do not issue SQL that references columns their table does not have.
        if not {'zero_order_id', 'zero_request_id', 'zero_allocation'}.issubset(columns):
            raise ValidationError('协商凭证结构缺失')
        from .financial_lock import lock_existing_order_money
        lock_existing_order_money()
        receipt = execution_for_order(order, lock=True)
        if not receipt:
            raise ValidationError('协商标记与凭证不一致')
        if followup_id:
            from .negotiation_followup_accounting import verify_followup_accounting, verify_followup_termination
            link = NegotiationFollowupLink.objects.select_for_update().get(pk=followup_id, order=order, execution=receipt)
            shares = list(verify_followup_accounting(link))
            verify_followup_termination(link)
            times = {link.previous_id:link.previous.accepted_at,
                     link.execution_id:(order.completed_at if link.projected_ledger.get('completion_evidence') is not None
                                        else link.execution.accepted_at)}
        elif receipt.zero_order_id:
            from .negotiation_zero import verify_zero_execution, verify_zero_termination
            shares = verify_zero_execution(receipt)
            verify_zero_termination(receipt)
            times = {receipt.pk:receipt.accepted_at}
        else:
            from .negotiation_execution import verify_execution
            from .negotiation_termination import verify_termination_evidence
            shares = verify_execution(receipt)
            verify_termination_evidence(receipt)
            times = {receipt.pk:receipt.accepted_at}
        transferred = bool(receipt.refund_id and isinstance(receipt.refund.benefit_effects, dict)
                           and receipt.refund.benefit_effects.get('ordinary_transfer_negotiation_version') == 1)
        if (not shares or not receipt.termination_processed_at
                or any(not share.account_id or not share.assignment.released_at
                       or (share.assignment.released_at > times.get(share.execution_id) if transferred
                           else share.assignment.released_at != times.get(share.execution_id)) for share in shares)):
            raise ValidationError('协商履约范围不一致')
        return {share.account_id for share in shares}
    except (ValidationError, SettlementError, ObjectDoesNotExist, ValueError) as exc:
        raise ValidationError('已确认的协商记录待核对，请联系客服处理') from exc


def assert_scope_not_closed(order, scope):
    closed = closed_provider_accounts(order)
    if closed and scope is None:
        raise ValidationError('已确认终止的服务不能重新售后，请选择其他陪玩')
    if closed.intersection(scope or ()):
        raise ValidationError('所选陪玩的协商终止方案已确认，不能重新售后')
    return closed
