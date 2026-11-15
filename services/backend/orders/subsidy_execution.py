"""Execute an accepted free safety-box termination without moving funds."""
from copy import copy
import hashlib
import json

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import Order, OrderPlatformSubsidyShare, SubsidyAgreementExecution


def _digest(shares):
    return hashlib.sha256(json.dumps(shares, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def table_available():
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        return SubsidyAgreementExecution._meta.db_table in connection.introspection.table_names(cursor)


def _room_snapshot(order):
    """Capture the one room, active confirmation and open objection under lock."""
    from .models import OrderRoom, OrderRoomConfirmation, OrderRoomObjection
    room = OrderRoom.objects.select_for_update().filter(order=order).first()
    if order.room_required_snapshot is not True:
        if room:
            raise ValidationError('房间及订单关联待核对')
        return None
    if not room:
        raise ValidationError('房间记录待核对')
    objections = list(OrderRoomObjection.objects.select_for_update().filter(room=room, status='OPEN'))
    waits = list(OrderRoomConfirmation.objects.select_for_update().filter(
        request__room=room, status__in=['RUNNING', 'PAUSED']))
    if len(objections) > 1 or len(waits) > 1:
        raise ValidationError('房间异议或确认记录待核对')
    objection, wait = (objections[0] if objections else None), (waits[0] if waits else None)
    if objection and (objection.customer_id != order.customer_account_id
                      or (objection.confirmation_id and (not wait or objection.confirmation_id != wait.pk))):
        raise ValidationError('房间异议与确认记录待核对')
    return {'room_id':room.pk, 'objection_id':objection.pk if objection else None,
            'confirmation_id':wait.pk if wait else None,
            'objection_confirmation_id':objection.confirmation_id if objection else None}


def _verify_room(execution, evidence, *, provisional):
    from console.models import AdminAuditLog
    from .models import OrderRoom, OrderRoomConfirmation, OrderRoomObjection
    order, proposal = execution.order, execution.proposal
    if (not isinstance(evidence, dict) or set(evidence) != {'room_id', 'objection_id', 'confirmation_id',
                                                           'objection_confirmation_id'}
            or type(evidence['room_id']) is not int
            or any(value is not None and (type(value) is not int or value <= 0)
                   for value in (evidence['objection_id'], evidence['confirmation_id'],
                                 evidence['objection_confirmation_id']))):
        raise ValidationError('房间协商凭证待核对')
    room = OrderRoom.objects.filter(pk=evidence['room_id'], order=order).first()
    if not room or OrderRoomConfirmation.objects.filter(request__room=room, status__in=['RUNNING', 'PAUSED']).exists():
        raise ValidationError('房间确认记录待核对')
    if evidence['confirmation_id'] is not None:
        wait = OrderRoomConfirmation.objects.filter(pk=evidence['confirmation_id'], request__room=room).first()
        if (not wait or wait.status != 'INVALIDATED' or wait.invalidation_reason != 'NEGOTIATED_TERMINATION'
                or wait.invalidated_by_id != execution.operator_id):
            raise ValidationError('房间确认失效凭证待核对')
    if evidence['objection_id'] is None:
        if evidence['objection_confirmation_id'] is not None or OrderRoomObjection.objects.filter(room=room, status='OPEN').exists():
            raise ValidationError('房间异议待核对')
        return
    objection = OrderRoomObjection.objects.filter(pk=evidence['objection_id'], room=room,
                                                   customer_id=order.customer_account_id).first()
    if (not objection or objection.confirmation_id != evidence['objection_confirmation_id']
            or (objection.confirmation_id is not None and objection.confirmation_id != evidence['confirmation_id'])):
        raise ValidationError('房间异议凭证待核对')
    if provisional:
        if objection.status != 'OPEN' or objection.resolution_proposal_id is not None:
            raise ValidationError('房间异议状态待核对')
    else:
        path = f'/api/admin/aftersales/{proposal.case_id}/subsidy-negotiation-execution/'
        room_audits = list(AdminAuditLog.objects.filter(resource='room-objection', object_id=str(objection.pk),
                    path=path, operator_account_id=execution.operator_id, status_code=200))
        expected_audit = {'order_id':order.pk, 'case_id':proposal.case_id, 'proposal_id':proposal.pk,
                          'resolution':objection.resolution}
        if (objection.status != 'RESOLVED' or objection.resolution_proposal_id != proposal.pk
                or objection.resolved_by_id != execution.operator_id
                or (objection.confirmation_id and objection.confirmation_ready is not False)
                or len(room_audits) != 1 or room_audits[0].request_body != expected_audit
                or OrderRoomObjection.objects.filter(room=room, status='OPEN').exists()):
            raise ValidationError('房间异议结案凭证待核对')


def verify_execution(execution, *, locked_shares=None, provisional=False):
    """Check consent, audit, history and every saved subsidy before payout."""
    from console.models import AdminAuditLog
    from .negotiation_proposals import _decisions, _verified
    proposal, order = execution.proposal, execution.order
    from .subsidy_agreement import verify_coupon
    coupon = verify_coupon(order)
    case = proposal.case
    episode = copy(case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(episode, proposal)
    _decisions(episode, proposal)
    if provisional and not transaction.get_connection().in_atomic_block:
        raise RuntimeError('房间协商凭证须在订单事务内核对')
    status_valid = ((order.status == Order.Status.IN_SERVICE and order.completed_at is None
                     and not execution.completed_before_agreement and execution.completion_at == execution.accepted_at)
                    if provisional else (order.status == Order.Status.COMPLETED
                                         and order.completed_at == execution.completion_at))
    if (proposal.status != 'ACCEPTED' or proposal.refund_units != 0
            or execution.terms_hash != proposal.terms_hash or execution.accepted_at != proposal.accepted_at
            or coupon.used_at > execution.accepted_at
            or case.order_id != order.pk or execution.processed_at < execution.accepted_at
            or not status_valid or order.payment_status != Order.PaymentStatus.PAID or order.amount != 0
            or (execution.completed_before_agreement and execution.completion_at > execution.accepted_at)
            or (not execution.completed_before_agreement and execution.completion_at != execution.accepted_at)
            or not isinstance(execution.shares, list) or execution.shares_hash != _digest(execution.shares)):
        raise ValidationError('安全箱协商执行凭证待核对')
    terms = {item['account_id']: item for item in proposal.provider_terms}
    if not isinstance(case.history, list):
        raise ValidationError('安全箱协商售后历史待核对')
    histories = [item for item in case.history if isinstance(item, dict) and item.get('status') == 'RESOLVED'
        and item.get('created_at') == proposal.case_opened_at.isoformat()
        and item.get('client_request_id') == proposal.case_request_id]
    try:
        resolved = parse_datetime(histories[0].get('resolved_at') or '') if len(histories) == 1 else None
    except (ValueError, TypeError):
        resolved = None
    if (len(histories) != 1 or not resolved or timezone.is_naive(resolved)
            or not execution.accepted_at <= resolved <= execution.processed_at
            or histories[0].get('operator_id') != execution.operator_id
            or histories[0].get('resolution') != proposal.explanation
            or histories[0].get('affected_provider_account_ids') != proposal.affected_accounts):
        raise ValidationError('安全箱协商售后历史待核对')
    path = f'/api/admin/aftersales/{case.pk}/subsidy-negotiation-execution/'
    audit = list(AdminAuditLog.objects.filter(path=path, resource='aftersales', object_id=str(case.pk),
                                              operator_account_id=execution.operator_id, method='POST', status_code=200))
    expected = {'proposal_id':proposal.pk, 'execution_id':execution.pk, 'client_request_id':execution.request_id}
    audit = [item for item in audit if isinstance(item.request_body, dict)
             and item.request_body.get('execution_id') == execution.pk]
    from club_accounts.models import LegacyAccountMap
    if len(audit) == 1 and order.room_required_snapshot is True:
        evidence = audit[0].request_body.get('room_evidence')
        expected['room_evidence'] = evidence
        _verify_room(execution, evidence, provisional=provisional)
    elif order.room_required_snapshot is not True:
        from .models import OrderRoom
        if OrderRoom.objects.filter(order=order).exists():
            raise ValidationError('房间及订单关联待核对')
    if (len(audit) != 1 or audit[0].request_body != expected
            or (audit[0].operator_id and not LegacyAccountMap.objects.filter(
                account_id=execution.operator_id, legacy_user_id=audit[0].operator_id).exists())):
        raise ValidationError('安全箱协商审计凭证待核对')
    from .models import OrderOriginalShareTransfer
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    transfer_terms = None
    if transferred:
        from .subsidy_transfer_agreement_evidence import verify_committed
        transfer_terms = verify_committed(execution)
    shares = list(locked_shares) if locked_shares is not None else list(
        OrderPlatformSubsidyShare.objects.filter(subsidy__order=order).select_related('assignment').order_by('pk'))
    entries = execution.shares
    keys = {'share_id', 'assignment_id', 'account_id', 'original', 'retained', 'original_locked', 'locked_after', 'affected'}
    if (len(entries) != len(shares) or any(not isinstance(item, dict) or set(item) != keys
            or any(type(item[key]) is not int or item[key] < 0 for key in
                   ('share_id', 'assignment_id', 'account_id', 'original', 'retained', 'original_locked', 'locked_after'))
            or type(item['affected']) is not bool or item['retained'] > item['original']
            or item['original_locked'] > item['original']
            or item['locked_after'] != min(item['original_locked'], item['retained']) for item in entries)):
        raise ValidationError('安全箱协商补贴份额待核对')
    if [item['share_id'] for item in entries] != [row.pk for row in shares]:
        raise ValidationError('安全箱协商补贴份额已变化')
    affected = set()
    for item, row in zip(entries, shares):
        account_id = row.assignment.provider_account_id
        term = terms.get(account_id)
        if (item['assignment_id'] != row.assignment_id or item['account_id'] != account_id
                or row.amount != item['retained'] or row.retained_amount != item['locked_after']
                or item['affected'] != bool(term)):
            raise ValidationError('安全箱协商补贴份额已变化')
        if term:
            affected.add(account_id)
            prior = transfer_terms.prior_income if transfer_terms else 0
            if (item['original'] != prior + term['original_income_units']
                    or item['retained'] != prior + term['retained_units']
                    or (transfer_terms and item['original_locked'] != prior)):
                raise ValidationError('安全箱协商报酬与双方确认不一致')
        elif item['original'] != item['retained']:
            raise ValidationError('未受影响陪玩的补贴不得变化')
    if affected != set(proposal.affected_accounts):
        raise ValidationError('安全箱协商参与范围不一致')
    return affected


@transaction.atomic
def execute(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .financial_lock import lock_order_money
    from .negotiation_proposals import _decisions, _lock_case, _terms, _verified
    from .refunds import authorized_refund
    from .subsidy_agreement import calculate, enabled
    if not enabled() or not authorized_refund(operator):
        raise ValidationError('当前账号不能执行安全箱协商')
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id.strip() != request_id:
        raise ValidationError('请提供有效操作凭证')
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    order, case = _lock_case(case_id)
    lock_order_money()
    existing = SubsidyAgreementExecution.objects.select_for_update().filter(order=order).first()
    if existing:
        if existing.proposal_id != proposal_id or existing.operator_id != operator.pk or existing.request_id != request_id:
            raise ValidationError('安全箱订单已有其他协商执行记录')
        verify_execution(existing)
        return existing
    from .models import NegotiationProposal
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    if not proposal or case.status != 'OPEN':
        raise ValidationError('协商方案或售后状态已变化')
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if (proposal.status != 'ACCEPTED' or not proposal.accepted_at or proposal.accepted_at > timezone.now()
            or not latest or latest.pk != proposal.pk or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)):
        raise ValidationError('须由双方确认最新方案后处理订单')
    already_completed = order.status == Order.Status.COMPLETED
    if already_completed and (not order.completed_at or order.completed_at > proposal.accepted_at):
        raise ValidationError('订单完成时间待核对')
    from .models import OrderOriginalShareTransfer
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    transfer_plan = None
    if transferred:
        from .subsidy_transfer_capability import stage_enabled
        if not stage_enabled('VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING'):
            raise ValidationError('安全箱转单协商执行尚未开放')
        from .subsidy_transfer_agreement_plan import calculate as transfer_calculate
        transfer_plan = transfer_calculate(proposal.pk)
    else:
        requested = _terms([{'account_id':item['account_id'], 'retained_units':item['retained_units']}
                            for item in proposal.provider_terms], proposal.affected_accounts)
        calculation = calculate(order, case, proposal.affected_accounts, proposal.refund_units, requested)
        if calculation['provider_terms'] != proposal.provider_terms:
            raise ValidationError('安全箱补贴已变化，请重新确认')
    rows = list(OrderPlatformSubsidyShare.objects.select_for_update().filter(subsidy__order=order)
                .select_related('assignment').order_by('pk'))
    active = [row for row in rows if not row.assignment.released_at]
    if len(active) != 1 or active[0].assignment.provider_account_id not in proposal.affected_accounts:
        raise ValidationError('必须包含当前履约陪玩，才能终止本单')
    terms = {item['account_id']: item for item in proposal.provider_terms}
    saved = []
    transferred_amounts = ({item['account_id']:item['retained_income_units'] for item in transfer_plan['people']}
                           if transfer_plan else {})
    for row in rows:
        account_id = row.assignment.provider_account_id
        term = terms.get(account_id)
        retained = transferred_amounts[account_id] if transfer_plan else term['retained_units'] if term else row.amount
        saved.append({'share_id':row.pk, 'assignment_id':row.assignment_id, 'account_id':account_id,
                      'original':row.amount, 'retained':retained, 'original_locked':row.retained_amount,
                      'locked_after':min(row.retained_amount, retained), 'affected':bool(term)})
        if row.amount != retained or row.retained_amount > retained:
            row.amount = retained; row.retained_amount = min(row.retained_amount, retained)
            row.save(update_fields=['amount', 'retained_amount'])
    from .models import OrderParticipationSegment
    if not already_completed and not transfer_plan:
        for segment in OrderParticipationSegment.objects.select_for_update().filter(attribution__order=order,
                provider_account_id__in=proposal.affected_accounts, released_at__isnull=True):
            if not segment.started_at or segment.started_at > proposal.accepted_at:
                raise ValidationError('陪玩履约时间待核对')
            segment.released_at = proposal.accepted_at; segment.save(update_fields=['released_at'])
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    case.status = 'RESOLVED'; case.operator = operator; case.resolution = proposal.explanation
    case.resolved_at = timezone.now(); append_history(case)
    from wallet.income_priority_evidence import attach_closure
    attach_closure(case)
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    execution = SubsidyAgreementExecution.objects.create(order=order, proposal=proposal, operator=operator,
        request_id=request_id, terms_hash=proposal.terms_hash, shares=saved, shares_hash=_digest(saved),
        accepted_at=proposal.accepted_at, completion_at=order.completed_at if already_completed else proposal.accepted_at,
        completed_before_agreement=already_completed, processed_at=timezone.now())
    room_evidence = _room_snapshot(order)
    audit_body = {'proposal_id':proposal.pk, 'execution_id':execution.pk, 'client_request_id':request_id}
    if room_evidence is not None:
        audit_body['room_evidence'] = room_evidence
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/subsidy-negotiation-execution/', resource='aftersales',
        object_id=str(case.pk), request_body=audit_body, status_code=200)
    from .services import ensure_fulfillment_reports, refresh_escort_status
    from .state_machine import transition
    def finish(current):
        current.completed_at = proposal.accepted_at
        ensure_fulfillment_reports(current)
        refresh_escort_status(provider_ids=[row.assignment.provider_id for row in rows], exclude_order_ids=[current.pk])
    if already_completed:
        from .state_machine import log_only
        log_only(order, action='NEGOTIATED_STOP', operator=legacy_operator, operator_account=operator,
                 reason='双方确认安全箱售后报酬')
    else:
        from .room_completion_review import invalidate_for_transfer_locked
        invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
        order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_operator, operator_account=operator,
            action='COMPLETE', reason='双方确认安全箱协商终止', side_effect=finish, update_fields=['completed_at'],
            room_subsidy_agreement_id=execution.pk)
    execution.order = order
    verify_execution(execution)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    return execution
