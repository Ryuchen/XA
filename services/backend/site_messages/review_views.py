"""Store-local review of notice intents without rewriting their original recipient."""
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import get_account_permissions, is_console_account
from stores.db import transaction


def _allowed(account, *permissions):
    return is_console_account(account) and set(permissions).issubset(get_account_permissions(account))


def _sources(kind):
    from .models import (AddonPaymentNotice, GiftReceiptNotice, NegotiationProposalNotice, RechargeReceiptNotice, RechargeGiftDecisionNotice,
                         StaffStartNotice, ReportApprovalNotice)
    from . import addon_payment_notice, gift_receipt_notice, negotiation_proposal_notice, recharge_receipt_notice, recharge_gift_decision_notice, staff_start_notice, report_approval_notice
    return {
        'negotiation': (NegotiationProposalNotice, negotiation_proposal_notice,
                        'proposal__case__order_id', 'proposal_id'),
        'gift': (GiftReceiptNotice, gift_receipt_notice, 'order_id', 'order_id'),
        'addon-payment': (AddonPaymentNotice, addon_payment_notice, 'payment__intent__order_id', 'payment_id'),
        'recharge': (RechargeReceiptNotice, recharge_receipt_notice, None, 'recharge_id'),
        'recharge-gift-decision': (RechargeGiftDecisionNotice, recharge_gift_decision_notice, None, 'approval_id'),
        'staff-start': (StaffStartNotice, staff_start_notice, 'status_log__order_id', 'status_log_id'),
        'report-approval': (ReportApprovalNotice, report_approval_notice, 'report__order_id', 'report_id'),
    }.get(kind)


def _display(row, order_id, source_id):
    return {'id': str(row.pk), 'status': row.status, 'note': row.note,
            'account_id': str(row.account_id) if row.account_id else None,
            'business_cycle': row.business_cycle,
            'legacy_user_id': str(row.legacy_user_id) if getattr(row, 'legacy_user_id', None) else None,
            'order_id': str(order_id) if order_id else None, 'source_id': str(source_id) if source_id else None,
            'created_at': row.created_at, 'completed_at': row.completed_at}


class NoticeReviewListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        kind = request.query_params.get('kind', '')
        domain_permission = ('withdraw:view' if kind == 'withdrawal' else
            'recharge:view' if kind in ('recharge', 'recharge-gift-decision') else 'order:view')
        if not _allowed(request.account, 'message:view', domain_permission):
            return Response({'code': 403, 'msg': '无权查看订单通知核对'}, status=403)
        if kind == 'withdrawal':
            from .withdrawal_review import list_reviews
            return list_reviews(request)
        spec = _sources(kind)
        if not spec:
            return Response({'code': 400, 'msg': '请选择受支持的通知类型'}, status=400)
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            size = max(1, min(100, int(request.query_params.get('page_size', 20))))
        except (ValueError, TypeError):
            return Response({'code': 400, 'msg': '分页参数无效'}, status=400)
        model, _, order_path, source_path = spec
        qs = model.objects.filter(status='REVIEW').order_by('-pk')
        total = qs.count()
        fields = ['pk', source_path] + ([order_path] if order_path else [])
        rows = list(qs.values(*fields)[(page - 1) * size:page * size])
        details = model.objects.select_related('report') if kind == 'report-approval' else model.objects.all()
        objects = details.in_bulk([row['pk'] for row in rows])
        items = []
        for item in rows:
            row = objects.get(item['pk'])
            if not row:
                continue
            data = _display(row, item[order_path] if order_path else None, item[source_path])
            if kind == 'recharge':
                data.update({'recharge_transaction_id': str(row.recharge_tx_id),
                             'gift_transaction_id': str(row.gift_tx_id) if row.gift_tx_id else None,
                             'gift_units': row.gift_units, 'gift_pending_at_recharge': row.gift_pending})
            if kind == 'recharge-gift-decision':
                data.update({'recharge_id': str(row.approval.recharge_id), 'decision': row.decision,
                             'decided_at': row.decided_at, 'gift_units': row.amount,
                             'gift_transaction_id': str(row.gift_tx_id) if row.gift_tx_id else None})
            if kind == 'report-approval':
                data.update({'review_kind': row.kind, 'audited_at': row.audited_at,
                             'preview': row.preview, 'detail': row.detail,
                             'legacy_user_id': str(row.report.provider_id) if row.report.provider_id else None,
                             'current_report_account_id': str(row.report.provider_account_id) if row.report.provider_account_id else None,
                             'current_report_status': row.report.status,
                             'current_report_audited_at': row.report.audited_at})
            items.append(data)
        return Response({'code': 0, 'data': {'kind': kind, 'total': total, 'page': page, 'page_size': size,
            'items': items}})


class NoticeReviewActionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, kind, notice_id):
        if kind != 'withdrawal':
            return Response({'code': 404, 'msg': '通知类型不存在'}, status=404)
        if not _allowed(request.account, 'message:view', 'withdraw:view'):
            return Response({'code': 403, 'msg': '无权查看提现通知核对'}, status=403)
        from .withdrawal_review import detail
        return detail(notice_id)

    def post(self, request, kind, notice_id):
        domain_permission = ('withdraw:view' if kind == 'withdrawal' else
            'recharge:view' if kind in ('recharge', 'recharge-gift-decision') else 'order:view')
        if not _allowed(request.account, 'message:push', domain_permission):
            return Response({'code': 403, 'msg': '无权处理订单通知核对'}, status=403)
        if kind == 'withdrawal':
            from .withdrawal_review import act
            return act(request, notice_id)
        spec = _sources(kind)
        if not spec:
            return Response({'code': 404, 'msg': '通知类型不存在'}, status=404)
        if set(request.data.keys()) - {'action', 'reason', 'expected_note'}:
            return Response({'code': 400, 'msg': '不可修改原收件账号、周期或来源'}, status=400)
        action = request.data.get('action')
        reason = request.data.get('reason')
        expected_note = request.data.get('expected_note')
        if action not in ('recheck', 'dismiss') or not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 255:
            return Response({'code': 400, 'msg': '请选择处理方式并填写2至255字原因'}, status=400)
        if not isinstance(expected_note, str):
            return Response({'code': 400, 'msg': '请先刷新并核对当前待处理原因'}, status=400)
        model, handler, _, _ = spec
        if action == 'recheck' and not handler.enabled():
            return Response({'code': 409, 'msg': '该类通知恢复尚未启用'}, status=409)
        with transaction.atomic():
            row = model.objects.select_for_update().filter(pk=notice_id).first()
            if not row:
                return Response({'code': 404, 'msg': '待核对通知不存在'}, status=404)
            if row.status != 'REVIEW' or row.note != expected_note:
                return Response({'code': 409, 'msg': '待核对状态已变化，请刷新后处理'}, status=409)
            if action == 'recheck' and (not row.account_id or type(row.business_cycle) is not int or row.business_cycle <= 0):
                return Response({'code': 409, 'msg': '原收件账号或周期未记录，无法自动重核；请核实后结束提醒'}, status=409)
            if action == 'recheck':
                row.status, row.note, row.completed_at = 'PENDING', '', None
            else:
                row.status, row.note, row.completed_at = 'SKIPPED', '经后台核对结束提醒', timezone.now()
            row.save(update_fields=['status', 'note', 'completed_at'])
            from console.models import AdminAuditLog
            AdminAuditLog.objects.create(operator=getattr(request, 'legacy_user', None), operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
                path=request.path[:255], resource='notice-review', object_id=f'{kind}:{row.pk}',
                request_body={'action': action, 'reason': reason.strip(), 'previous_note': expected_note}, status_code=200)
            if action == 'recheck':
                transaction.on_commit(lambda pk=row.pk: handler.deliver(pk), robust=True)
        return Response({'code': 0, 'data': {'action': action},
                         'msg': '已提交按原始来源重核' if action == 'recheck' else '已结束该提醒'})
