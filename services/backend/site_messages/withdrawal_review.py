"""Staff review for unresolved original withdrawal notices."""
from django.conf import settings
from rest_framework.response import Response
from stores.db import transaction
from .models import WithdrawalNoticeDelivery
from wallet.withdraw_notifications import OWNER_REVIEW_REASON


def _data(row):
    withdrawal = row.withdrawal
    state = ('MESSAGE_CREATED' if row.message_id else 'REVIEW' if row.skipped_reason == OWNER_REVIEW_REASON else 'CLOSED')
    return {'id': str(row.pk), 'withdrawal_id': str(row.withdrawal_id), 'event_key': row.event_key,
            'account_id': str(withdrawal.account_id) if withdrawal.account_id else None,
            'business_cycle': withdrawal.account_business_cycle, 'status': state,
            'note': row.skipped_reason, 'message_id': str(row.message_id) if row.message_id else None,
            'realtime_delivery': 'UNKNOWN', 'created_at': row.created_at}


def _owner_event(row):
    return next((event for event in ('submitted', 'owner_cancelled')
                 if row.event_key == f'owner:{row.withdrawal_id}:{event}'), None)


def list_reviews(request):
    try:
        page = max(1, int(request.query_params.get('page', 1)))
        size = max(1, min(100, int(request.query_params.get('page_size', 20))))
    except (TypeError, ValueError):
        return Response({'code': 400, 'msg': '分页参数无效'}, status=400)
    query = WithdrawalNoticeDelivery.objects.filter(skipped_reason=OWNER_REVIEW_REASON, message__isnull=True,
        platform_event__isnull=True, event_key__startswith='owner:').select_related('withdrawal').order_by('-pk')
    total = query.count()
    rows = query[(page - 1) * size:page * size]
    return Response({'code': 0, 'data': {'kind': 'withdrawal', 'total': total, 'page': page,
        'page_size': size, 'items': [_data(row) for row in rows if _owner_event(row)]}})


def detail(notice_id):
    row = WithdrawalNoticeDelivery.objects.select_related('withdrawal').filter(pk=notice_id, platform_event__isnull=True).first()
    if not row or not _owner_event(row):
        return Response({'code': 404, 'msg': '提现通知不存在'}, status=404)
    return Response({'code': 0, 'data': _data(row)})


def act(request, notice_id):
    if set(request.data.keys()) - {'action', 'reason', 'expected_note'}:
        return Response({'code': 400, 'msg': '不可修改原收件账号、周期或来源'}, status=400)
    action, reason, expected = (request.data.get(name) for name in ('action', 'reason', 'expected_note'))
    if (action not in ('recheck', 'dismiss') or not isinstance(reason, str)
            or not 2 <= len(reason.strip()) <= 255 or not isinstance(expected, str)):
        return Response({'code': 400, 'msg': '请选择处理方式并填写原因及当前待核对说明'}, status=400)
    if action == 'recheck' and not getattr(settings, 'WITHDRAWAL_NOTICE_RECOVERY_ENABLED', False):
        return Response({'code': 409, 'msg': '提现通知恢复尚未启用'}, status=409)
    with transaction.atomic():
        row = WithdrawalNoticeDelivery.objects.select_for_update().filter(
            pk=notice_id, platform_event__isnull=True).first()
        event = _owner_event(row) if row else None
        if not row or not event:
            return Response({'code': 404, 'msg': '提现通知不存在'}, status=404)
        if row.message_id or row.skipped_reason != OWNER_REVIEW_REASON or expected != row.skipped_reason:
            return Response({'code': 409, 'msg': '通知状态已变化，请刷新后处理'}, status=409)
        if action == 'recheck' and (not row.withdrawal.account_id or not row.withdrawal.account_business_cycle):
            return Response({'code': 409, 'msg': '原收件账号或周期未记录，无法重核'}, status=409)
        if action == 'dismiss':
            row.skipped_reason = '经后台核对结束提醒'
            row.save(update_fields=['skipped_reason'])
        from console.models import AdminAuditLog
        AdminAuditLog.objects.create(operator=getattr(request, 'legacy_user', None), operator_account=request.account,
            operator_name=(request.account.nickname or request.account.username)[:150], method='POST', path=request.path[:255],
            resource='notice-review', object_id=f'withdrawal:{row.pk}',
            request_body={'action': action, 'reason': reason.strip(), 'previous_note': expected}, status_code=200)
        request._request._business_audit_recorded = True
        if action == 'recheck':
            from wallet.withdraw_notice_recovery import _project
            transaction.on_commit(lambda pk=row.withdrawal_id, event=event: _project(pk, owner_event=event,
                retry_owner_review=True), robust=True)
    return Response({'code': 0, 'data': {'action': action}, 'msg': '已提交原来源重核' if action == 'recheck' else '已结束该提醒'})
