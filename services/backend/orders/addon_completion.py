"""Confirm completed add-on work after a negotiated service termination."""
import hashlib
import json
from collections.abc import Mapping

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser, get_account_permissions
from stores.db import transaction
from .addon_handover import _payment_locked, assert_no_pending_handover, current_responsibilities
from .models import OrderAddonCompletion
from .points_checkout_test import require_isolated_checkout


def _points_completion_scope(method):
    """Keep receipt and account locks until the completion operation ends."""
    from functools import wraps

    @wraps(method)
    def wrapped(self, request, order_id, *args, **kwargs):
        from .models import Order, OrderOriginalShareTransfer
        from .points_service_payment import needs_integrated_processing
        order = Order.objects.filter(pk=order_id).first()
        if (order and needs_integrated_processing(order)
                and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                and transaction.get_connection().vendor == 'mysql'
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
            from .points_transfer_refund_access import after_transfer
            with after_transfer(order.pk, phase='refund', allow_addon_completion=True):
                return method(self, request, order_id, *args, **kwargs)
        return method(self, request, order_id, *args, **kwargs)
    return wrapped


def _enabled(order=None):
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    if order is not None:
        from .points_transfer_refund_access import permits
        if any(permits(order, phase=phase) for phase in ('refund', 'settlement')):
            return
        from .ordinary_transfer_continuation import applies as transfer_continuation_applies
        ordinary_transfer_closed = ordinary_transfer_permits(order) and transfer_continuation_applies(order)
        if ordinary_transfer_closed or (ordinary_addon_eligible(order) and order.refund_records.exists()):
            return
    if not getattr(settings, 'ADDON_COMPLETION_INTERNAL_TESTING', False):
        raise ValidationError('加购完成确认尚未开放')
    require_isolated_checkout()


def _completion_payment_locked(order_id, payment_id):
    # Acquire the ordinary refund mutex and both participant locks before the
    # payment's joined rows when this is a reverse-refund order.
    from .models import Order
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('订单不存在')
    from .ordinary_transfer_continuation import applies as transfer_continuation_applies
    transferred_agreement = transfer_continuation_applies(order)
    reverse_refund = (order.required_provider_count == 2
                      and order.addon_intents.filter(payment__refund_records__isnull=False).exists())
    if transferred_agreement or reverse_refund:
        from .financial_lock import lock_order_money
        lock_order_money()
        if reverse_refund:
            from .negotiation_execution import _lock_reverse_addon_people
            _lock_reverse_addon_people(order)
    return _payment_locked(order_id, payment_id)


def _target_already_settled(order, payment, segment_id):
    from .models import OrderSegmentPersonSettlement
    if (payment.settlements.filter(segment_id=segment_id).exists()
            or payment.segment_settlements.filter(segment_id=segment_id).exists()):
        return True
    rows = OrderSegmentPersonSettlement.objects.select_for_update().filter(manifest__order=order)
    for row in rows:
        if not isinstance(row.segment_ids, list):
            raise ValidationError('已结算履约段记录待核对')
        if segment_id in row.segment_ids:
            return True
    return False


def _snapshot(order, payment):
    from .addon_payment import verify_receipt
    from .negotiation_settlement import execution_for_order
    from .negotiation_zero import verify_zero_execution, verify_zero_termination
    from .settlement import SettlementError
    from .models import OrdinaryTransferFollowupRound, NegotiationExecution
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.count() >= 3:
        from .points_third_addon_responsibilities import current as read_responsibilities
    else:
        read_responsibilities = current_responsibilities
    handover, responsibilities = read_responsibilities(payment, lock=True, with_share_id=True)
    current = [row for row in responsibilities if row['is_current']]
    from .points_transfer_refund_access import permits
    if any(permits(order, phase=phase) for phase in ('refund', 'settlement')):
        from .models import PointsNegotiationPosting
        from .points_negotiation_termination import verify_termination_locked
        from .addon_payment import verify_receipt
        postings = list(PointsNegotiationPosting.objects.select_for_update().filter(
            points_refund__redemption__order=order, termination_processed_at__isnull=False)
            .select_related('proposal').order_by('pk'))
        if len(current) != 1:
            raise ValidationError('积分协商终止或加购履约凭证不存在')
        row = current[0]
        matches = [item for item in postings if row['provider_id'] in item.proposal.affected_accounts
                   and (not item.proposal.affected_segment_ids
                        or row['segment_id'] in item.proposal.affected_segment_ids)]
        if len(matches) != 1:
            raise ValidationError('加购负责人对应的终止履约段不唯一')
        posting = matches[0]
        if posting == postings[0]:
            verify_termination_locked(posting, allow_verified_later_final=len(postings) > 1)
        elif len(postings) >= 3 and posting == postings[2]:
            from .points_third_refund_termination import _verify_completed_locked
            refund = posting.points_refund.refund
            _verify_completed_locked(order, posting.proposal.case, posting, operator=posting.operator,
                legacy_operator=refund.operator, request_id=refund.client_request_id, allow_later=len(postings) > 3)
        elif posting in postings[3:]:
            from .points_round_refund_termination import verify_completed_locked
            refund = posting.points_refund.refund
            verify_completed_locked(order, posting.proposal.case, posting, operator=posting.operator,
                legacy_operator=refund.operator, request_id=refund.client_request_id)
        else:
            from .points_transfer_followup_termination import _verify_followup_closure_facts_locked
            _verify_followup_closure_facts_locked(posting)
        selected = set(posting.proposal.affected_segment_ids or [])
        if (row['provider_id'] == payment.intent.buyer_id
                or row['provider_id'] not in posting.proposal.affected_accounts
                or (selected and row['segment_id'] not in selected)):
            raise ValidationError('加购负责人不属于已终止的履约段')
        verify_receipt(payment)
        assert_no_pending_handover(payment)
        if payment.refund_proposals.filter(status__in=('PENDING', 'ACCEPTED'), refund__isnull=True).exists():
            raise ValidationError('加购退款方案尚未处理，请先完成售后')
        result = {'payment_id': payment.pk, 'points_posting_id': posting.pk,
            'buyer_id': payment.intent.buyer_id, 'segment_id': row['segment_id'],
            'provider_id': row['provider_id'], 'basis': row['basis'], 'income': row['income'],
            'commission': row['commission_snapshot'], 'handover_id': handover.pk if handover else None,
            'refunds': [list(item) for item in payment.refund_records.order_by('pk').values_list('pk', 'amount')]}
        if len(postings) >= 3 and posting in postings[2:]:
            deducted = sum(posting.shares.get(account_id=row['provider_id']).addon_deductions.filter(
                payment=payment, segment_id=row['segment_id'], addon_share_id=row.get('share_id'))
                .values_list('amount', flat=True))
            result['points_addon_deduction'] = deducted
        return posting, result
    followup_present = OrdinaryTransferFollowupRound.objects.filter(order=order, execution__isnull=False).exists()
    transfer_verified = False
    try:
        from .ordinary_transfer_continuation import applies as transfer_continuation_applies
        if len(current) == 1 and transfer_continuation_applies(order):
            from .ordinary_transfer_continuation import verify_locked
            verify_locked(order)
            transfer_verified = True
            receipts = list(NegotiationExecution.objects.select_for_update().filter(
                Q(refund__order=order) | Q(zero_order=order),
                termination_processed_at__isnull=False).select_related('proposal').order_by('pk'))
            from .ordinary_transfer_end_scope import endings
            execution = endings(order, receipts).get(current[0]['segment_id'])
        elif followup_present and len(current) == 1:
            execution = (NegotiationExecution.objects.select_for_update().filter(
                Q(refund__order=order) | Q(zero_order=order), shares__account_id=current[0]['provider_id'],
                termination_processed_at__isnull=False).order_by('-accepted_at', '-pk').first())
        else:
            execution = execution_for_order(order, lock=True)
    except (SettlementError, DjangoValidationError) as exc:
        raise ValidationError('协商凭证待核对') from exc
    if not execution:
        raise ValidationError('协商终止凭证不存在')
    if transfer_verified:
        # The chain verifier checked this exact receipt (including a first
        # agreement before transfer) and the segment-to-ending mapping.
        # Re-reading it as a standalone current-order execution would apply
        # later transfer/refund state to the original agreement.
        if not (execution.zero_order_id == order.pk or
                (execution.refund_id and execution.refund.order_id == order.pk)):
            raise ValidationError('加购所属协商不属于本订单')
        affected = set(execution.shares.values_list('account_id', flat=True))
    elif execution.zero_order_id == order.pk:
        affected = {share.account_id for share in verify_zero_execution(execution)}
        verify_zero_termination(execution)
    elif execution.refund_id and execution.refund.order_id == order.pk:
        from .ordinary_addon_production import eligible as ordinary_addon_eligible
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        if not (getattr(settings, 'ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING', False)
                or ordinary_addon_eligible(order) or ordinary_transfer_permits(order)):
            raise ValidationError('该协商终止的加购完成确认尚未接通')
        from .negotiation_execution import verify_execution
        from .negotiation_termination import verify_termination_evidence
        if order.refund_records.count() != 1 and not followup_present:
            raise ValidationError('追加售后的加购完成确认尚未接通')
        affected = {share.account_id for share in verify_execution(execution)}
        verify_termination_evidence(execution)
    else:
        raise ValidationError('该协商终止的加购完成确认尚未接通')
    verify_receipt(payment)
    assert_no_pending_handover(payment)
    if payment.refund_proposals.filter(status__in=('PENDING', 'ACCEPTED'), refund__isnull=True).exists():
        raise ValidationError('加购退款方案尚未处理，请先完成售后')
    if len(current) != 1 or current[0]['provider_id'] not in affected:
        raise ValidationError('当前加购负责人不是已终止的陪玩或没有待确认份额')
    row = current[0]
    if row['provider_id'] == payment.intent.buyer_id:
        raise ValidationError('老板与陪玩身份不能相同')
    return execution, {'payment_id': payment.pk, 'execution_id': execution.pk,
        'buyer_id': payment.intent.buyer_id, 'segment_id': row['segment_id'],
        'provider_id': row['provider_id'], 'basis': row['basis'], 'income': row['income'],
        'commission': row['commission_snapshot'], 'handover_id': handover.pk if handover else None,
        'refunds': [list(row) for row in payment.refund_records.order_by('pk').values_list('pk', 'amount')]}


def _digest(snapshot, completed_work):
    return hashlib.sha256(json.dumps([snapshot, completed_work], sort_keys=True,
        separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def verify_accepted(order, payment, execution, responsibility):
    """Recheck both decisions and the exact financial responsibility."""
    _enabled(order)
    current_execution, snapshot = _snapshot(order, payment)
    from .models import PointsNegotiationPosting
    origin = {'points_posting': execution} if isinstance(execution, PointsNegotiationPosting) else {'execution': execution}
    request = (OrderAddonCompletion.objects.select_for_update().filter(payment=payment, **origin,
        segment_id=responsibility['segment_id'], provider_id=responsibility['provider_id'], status='ACCEPTED')
        .order_by('-pk').first())
    if (current_execution.pk != execution.pk or not request or request.snapshot != snapshot
            or request.snapshot_hash != _digest(snapshot, request.completed_work)
            or request.buyer_id != payment.intent.buyer_id or request.buyer_accepted is not True
            or request.provider_accepted is not True or not request.buyer_decided_at or not request.provider_decided_at
            or not request.accepted_at or request.accepted_at != max(request.buyer_decided_at, request.provider_decided_at)
            or request.created_at < execution.termination_processed_at or request.accepted_at > timezone.now()
            or min(request.buyer_decided_at, request.provider_decided_at) < request.created_at):
        raise ValidationError('加购完成情况尚未经老板和陪玩确认')
    from console.models import AdminAuditLog
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/completion/'
    audits = list(AdminAuditLog.objects.filter(resource='addon-completion', object_id=str(request.pk),
                                               path=path, method='POST', status_code=200))
    if (len(audits) != 1 or audits[0].operator_account_id != request.created_by_id
            or audits[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                                           'completed_work': request.completed_work}):
        raise ValidationError('加购完成确认的客服审计记录待核对')
    return request


def _data(item):
    return {'id': item.pk, 'payment_id': item.payment_id, 'provider_account_id': item.provider_id,
            'completed_work': item.completed_work, 'completed_basis_raw': item.snapshot.get('basis'), 'status': item.status,
            'buyer_accepted': item.buyer_accepted, 'provider_accepted': item.provider_accepted,
            'created_at': item.created_at, 'accepted_at': item.accepted_at}


class StaffAddonCompletionView(APIView):
    permission_classes = [IsConsoleUser]

    @transaction.atomic
    @_points_completion_scope
    def get(self, request, order_id, payment_id):
        if not {'order:view', 'order:dispatch'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看加购完成确认'}, status=403)
        from .models import Order
        _enabled(Order.objects.filter(pk=order_id).first())
        rows = OrderAddonCompletion.objects.filter(payment_id=payment_id, payment__intent__order_id=order_id).order_by('-pk')
        return Response({'code': 0, 'data': [_data(row) for row in rows]})

    @transaction.atomic
    @_points_completion_scope
    def post(self, request, order_id, payment_id):
        if 'order:dispatch' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权发起加购完成确认'}, status=403)
        body = request.data
        if not isinstance(body, Mapping) or set(body) != {'request_id', 'completed_work'}:
            raise ValidationError('请填写请求编号和已完成内容')
        key, work = body['request_id'], body['completed_work']
        if (not isinstance(key, str) or not 1 <= len(key.strip()) <= 64 or key != key.strip()
                or not isinstance(work, str) or not 2 <= len(work.strip()) <= 500 or work != work.strip()):
            raise ValidationError('请填写有效的请求编号和已完成内容')
        order, payment = _completion_payment_locked(order_id, payment_id)
        _enabled(order)
        existing = OrderAddonCompletion.objects.filter(payment=payment, request_id=key).first()
        if existing:
            if existing.created_by_id != request.account.pk or existing.completed_work != work:
                raise ValidationError('请求编号已用于其他确认')
            return Response({'code': 0, 'data': _data(existing)})
        from .points_service_payment import needs_integrated_processing
        if (order.settled_at or (needs_integrated_processing(order)
                and (payment.settlements.exists() or payment.segment_settlements.exists()))):
            raise ValidationError('已结算的加购不能发起完成确认')
        execution, snapshot = _snapshot(order, payment)
        if not needs_integrated_processing(order) and _target_already_settled(order, payment, snapshot['segment_id']):
            raise ValidationError('已结算的加购履约段不能发起完成确认')
        previous_requests = list(OrderAddonCompletion.objects.select_for_update().filter(payment=payment).filter(
            Q(status='PENDING') | Q(status='ACCEPTED', segment_id=snapshot['segment_id'])))
        from site_messages.addon_completion_notice import refresh_delivered
        for previous in previous_requests:
            previous.status = 'SUPERSEDED'
            previous.save(update_fields=['status'])
            refresh_delivered(previous)
        from .models import PointsNegotiationPosting
        origin = {'points_posting': execution} if isinstance(execution, PointsNegotiationPosting) else {'execution': execution}
        item = OrderAddonCompletion.objects.create(payment=payment, **origin,
            segment_id=snapshot['segment_id'], provider_id=snapshot['provider_id'], buyer_id=snapshot['buyer_id'],
            created_by=request.account, request_id=key, completed_work=work,
            snapshot=snapshot, snapshot_hash=_digest(snapshot, work))
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
            path=request.path[:255], resource='addon-completion', object_id=str(item.pk),
            request_body={'order_id': order.pk, 'payment_id': payment.pk, 'completed_work': work}, status_code=200)
        request._request._business_audit_recorded = True
        from site_messages.addon_completion_notice import record_all as record_completion_notices
        record_completion_notices(item)
        from engagement.confirmation import notify_committed_order
        notify_committed_order(order.pk)
        return Response({'code': 0, 'data': _data(item)})


class UserAddonCompletionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    @_points_completion_scope
    def get(self, request, order_id, payment_id, completion_id=None):
        from .models import Order
        _enabled(Order.objects.filter(pk=order_id).first())
        rows = OrderAddonCompletion.objects.filter(payment_id=payment_id, payment__intent__order_id=order_id).filter(
            Q(buyer=request.account, payment__intent__order__business_cycle=request.account.business_cycle)
            | Q(provider=request.account, segment__provider_business_cycle=request.account.business_cycle)).order_by('-pk')
        if completion_id is not None:
            rows = rows.filter(pk=completion_id)
        return Response({'code': 0, 'data': [_data(row) for row in rows]})

    @transaction.atomic
    @_points_completion_scope
    def post(self, request, order_id, payment_id, completion_id=None):
        if not isinstance(request.data, Mapping) or set(request.data) != {'accepted'} or type(request.data['accepted']) is not bool:
            raise ValidationError('请选择同意或拒绝')
        order, payment = _completion_payment_locked(order_id, payment_id)
        _enabled(order)
        item = OrderAddonCompletion.objects.select_for_update().filter(pk=completion_id, payment=payment).filter(
            Q(buyer=request.account) | Q(provider=request.account)).first()
        if not item:
            return Response({'code': 404, 'msg': '确认记录不存在'}, status=404)
        buyer = item.buyer_id == request.account.pk
        from club_accounts.models import ClubAccount
        account = ClubAccount.objects.select_for_update().get(pk=request.account.pk)
        source_cycle = order.business_cycle if buyer else item.segment.provider_business_cycle
        if (not account.license_membership_active or account.business_cycle != request.account.business_cycle
                or source_cycle is None or account.business_cycle != source_cycle):
            raise ValidationError('账号所属业务周期已变化，不能确认此加购')
        field = 'buyer_accepted' if buyer else 'provider_accepted'
        at_field = 'buyer_decided_at' if buyer else 'provider_decided_at'
        accepted = request.data['accepted']
        if getattr(item, field) is not None:
            if getattr(item, field) != accepted:
                raise ValidationError('确认已记录，请联系客服重新发起')
            return Response({'code': 0, 'data': _data(item)})
        if item.status != 'PENDING' or order.settled_at or not account.is_active or not account.can_login:
            raise ValidationError('确认已结束或账号不可用')
        execution, snapshot = _snapshot(order, payment)
        from .points_service_payment import needs_integrated_processing
        if not needs_integrated_processing(order) and _target_already_settled(order, payment, snapshot['segment_id']):
            raise ValidationError('该加购履约段已结算，请联系客服核对')
        from .models import PointsNegotiationPosting
        origin_id = item.points_posting_id if isinstance(execution, PointsNegotiationPosting) else item.execution_id
        if execution.pk != origin_id or snapshot != item.snapshot or item.snapshot_hash != _digest(snapshot, item.completed_work):
            raise ValidationError('加购责任或金额已变化，请联系客服重新发起')
        now = timezone.now()
        setattr(item, field, accepted)
        setattr(item, at_field, now)
        if not accepted:
            item.status = 'REJECTED'
        elif item.buyer_accepted and item.provider_accepted:
            item.status, item.accepted_at = 'ACCEPTED', now
        item.save(update_fields=[field, at_field, 'status', 'accepted_at'])
        from site_messages.addon_completion_notice import refresh_delivered
        refresh_delivered(item)
        from engagement.confirmation import notify_committed_order
        notify_committed_order(order.pk)
        return Response({'code': 0, 'data': _data(item)})
