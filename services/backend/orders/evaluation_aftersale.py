"""Create or associate a timely low-rating case under the locked order."""
from types import SimpleNamespace

from django.db.models import Q
from django.utils import timezone

from engagement.confirmation import aftersale_deadline
from stores.db import transaction
from users.models import EscortProfile
from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
from .aftersale_scope import applies_to
from .models import AfterSale, Order


def register_low_rating(evaluation, order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('低星售后须在订单事务内处理')
    if min(evaluation.score, evaluation.skill_score, evaluation.attitude_score, evaluation.communication_score) > 2:
        return
    deadline = aftersale_deadline(order)
    case = order.aftersales.select_for_update().first()
    target = evaluation.provider_account_id
    closed_accounts, closed_segments, target_segments = set(), set(), None
    if deadline and timezone.now() < deadline and case:
        # A returned provider has a new segment; a prior confirmed segment is
        # closed without excluding the later independently performed work.
        from .models import OrderTransferNegotiationEvidence, OrderParticipationSegment, OrderOriginalShareTransfer
        from .points_service_payment import needs_integrated_processing
        ordinary_evidence = OrderTransferNegotiationEvidence.objects.filter(
            order=order, execution__termination_processed_at__isnull=False).exists()
        points_transfer = (needs_integrated_processing(order) and order.refund_records.exists()
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
        if ordinary_evidence or points_transfer:
            from .aftersale_segment_scope import closed_segments_locked
            if points_transfer:
                from .points_transfer_refund_access import after_transfer
                with after_transfer(order.pk, phase='refund') as locked_order:
                    closed_segments = closed_segments_locked(locked_order)
            else:
                closed_segments = closed_segments_locked(order)
            segments = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, started_at__isnull=False).values_list('pk', 'provider_account_id'))
            accounts = {account for _, account in segments if account}
            closed_accounts = {account for account in accounts
                               if all(pk in closed_segments for pk, owner in segments if owner == account)}
            target_segments = sorted(pk for pk, owner in segments if owner == target and pk not in closed_segments)
            if target and not any(owner == target for _, owner in segments):
                from rest_framework.exceptions import ValidationError
                raise ValidationError('评价陪玩履约段待核对')
            if target and not target_segments:
                closed_accounts.add(target)
        else:
            from .negotiation_aftersale_scope import closed_provider_accounts
            closed_accounts = closed_provider_accounts(order)
    if (closed_accounts or closed_segments) and not target:
        from rest_framework.exceptions import ValidationError
        raise ValidationError('评价陪玩身份待核对，无法登记低星售后')
    reopened_for_other = bool(case and case.status != 'OPEN' and (closed_accounts or closed_segments)
                              and target not in closed_accounts)
    closed_target = bool(closed_accounts and target in closed_accounts)
    if not deadline:
        state = 'INELIGIBLE'
    elif timezone.now() >= deadline:
        state = 'EXPIRED'
    elif closed_target:
        # Record the rating, but never associate it with another player's
        # open case or reopen the confirmed termination.
        state = 'CLOSED'
    elif case and case.status != 'OPEN' and not reopened_for_other:
        state = 'CLOSED'
    elif (order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED)
          and not ((closed_accounts or closed_segments) and order.payment_status == Order.PaymentStatus.REFUNDED)):
        state = 'INELIGIBLE'
    else:
        if (closed_accounts and case and case.status == 'OPEN'
                and (not isinstance(case.affected_provider_account_ids, list)
                     or not case.affected_provider_account_ids
                     or closed_accounts.intersection(case.affected_provider_account_ids))):
            from rest_framework.exceptions import ValidationError
            raise ValidationError('当前售后范围包含已确认终止人员，请先核对')
        if (closed_segments and case and case.status == 'OPEN'
                and (case.affected_segment_ids is None
                     or closed_segments.intersection(case.affected_segment_ids))):
            from rest_framework.exceptions import ValidationError
            raise ValidationError('当前售后范围包含已确认终止履约段，请先核对')
        # Serialize with the existing deposit-refund eligibility check, just as
        # a customer-created case does.
        participants = list(order.providers.values_list('provider_id', 'provider_account_id'))
        participants.append((order.provider_id, order.provider_account_id))
        users = {user_id for user_id, _ in participants if user_id}
        accounts = {account_id for _, account_id in participants if account_id}
        list(EscortProfile.objects.select_for_update().filter(
            Q(user_id__in=users) | Q(account_id__in=accounts)).order_by('pk'))
        scope = [target] if target and order.service_clock_version is not None else None
        already_affected = bool(case and case.status == 'OPEN' and applies_to(case, target))
        if (closed_accounts or closed_segments) and not scope:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('低星售后的陪玩范围待核对')
        if not case:
            case = AfterSale(order=order, account_id=evaluation.customer_account_id,
                             reason='老板提交低星评价，请客服核实',
                             client_request_id=f'low-rating:{evaluation.pk}',
                             affected_provider_account_ids=scope,
                             affected_segment_ids=target_segments)
            case.save()
            append_history(case)
            state = 'OPENED'
        elif reopened_for_other:
            # The schema holds one row per order. Preserve the prior scope and
            # conclusion in history, then start a distinct scoped case on it.
            if not case.history:
                append_history(case)
            case.status, case.reason, case.resolution = 'OPEN', '老板提交低星评价，请客服核实', ''
            case.operator, case.resolved_at, case.created_at = None, None, timezone.now()
            case.account_id = evaluation.customer_account_id
            case.client_request_id = f'low-rating:{evaluation.pk}'
            case.affected_provider_account_ids = scope
            case.affected_segment_ids = target_segments
            append_history(case)
            state = 'OPENED'
        else:
            state = 'ATTACHED'
            if case.affected_provider_account_ids is not None:
                previous = case.affected_provider_account_ids
                if (not isinstance(previous, list) or not previous
                        or any(type(item) is not int or item <= 0 for item in previous)):
                    case.affected_provider_account_ids = None
                else:
                    case.affected_provider_account_ids = sorted(set(previous + scope)) if scope else None
        if not reopened_for_other and case.affected_segment_ids is not None:
            if target_segments is None:
                case.affected_segment_ids = None
            else:
                case.affected_segment_ids = sorted(set(case.affected_segment_ids + target_segments))
        case.history = [*case.history, {'event': 'LOW_RATING', 'evaluation_id': evaluation.pk,
            'provider_account_id': target, 'status': case.status, 'reason': '新增低星评价',
            'created_at': timezone.now().isoformat(), 'client_request_id': ''}]
        if reopened_for_other:
            case.save()
        else:
            case.save(update_fields=['affected_provider_account_ids', 'affected_segment_ids', 'history'])
        transaction.on_commit(lambda: notify_aftersale_reports(order))
        if not already_affected:
            notice = SimpleNamespace(affected_provider_account_ids=scope)
            schedule_aftersale_notices(order, notice, resolved=False, source_case=case)
    evaluation.low_rating_aftersale_state = state
    evaluation.low_rating_aftersale = case if case and state in ('OPENED', 'ATTACHED', 'CLOSED') and not closed_target else None
    evaluation.save(update_fields=['low_rating_aftersale_state', 'low_rating_aftersale'])
