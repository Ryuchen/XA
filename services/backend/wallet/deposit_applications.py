"""Provider deposit-return requests; cash moves only in the staff workflow."""
from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser, get_account_permissions
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import DepositRefundApplication


def _data(row):
    return {'id': row.pk, 'amount': row.amount, 'note': row.note, 'status': row.status,
            'cleared_at': row.cleared_at, 'eligible_at': row.eligible_at, 'created_at': row.created_at,
            'submitted_cleared_at': row.cleared_at, 'submitted_eligible_at': row.eligible_at,
            'processed_at': row.processed_at, 'resolution': row.resolution,
            'refund_transaction_id': row.refund_transaction_id, 'notification_state': row.notification_state}


def _current_eligibility(row):
    """A current read for display; the payout transaction always rechecks."""
    if row.status != 'PENDING':
        return None
    checked_at = timezone.now()
    account, profile = row.account, row.profile
    if (not account or not profile or profile.account_id != row.account_id
            or account.account_type != 'PROVIDER' or not account.license_membership_active
            or account.business_cycle != row.business_cycle):
        return {'checked_at': checked_at, 'can_refund': False, 'wait_status': 'REVIEW',
                'reasons': ['申请人原业务周期或身份已变化'], 'cleared_at': None, 'eligible_at': None}
    from console.deposit_refund import preview_deposit_refund
    try:
        current = preview_deposit_refund(profile)
    except ValueError:
        return {'checked_at': checked_at, 'can_refund': False, 'wait_status': 'REVIEW',
                'reasons': ['当前业务结清证据待核对'], 'cleared_at': None, 'eligible_at': None}
    enough = row.amount <= profile.deposit_paid
    if not enough:
        current = {**current, 'can_refund': False,
                   'reasons': [*current.get('reasons', []), '申请金额超过当前已缴押金']}
    return {'checked_at': checked_at, 'requested_amount_sufficient': enough, **current}


def _page(query, params, *, default_status):
    status = params.get('status', default_status)
    if status not in ('ALL', 'PENDING', 'REFUNDED', 'REJECTED', 'CANCELLED'):
        raise ValueError('申请状态筛选无效')
    if status != 'ALL':
        query = query.filter(status=status)
    before = params.get('before_id')
    if before is not None:
        if not isinstance(before, str) or not before.isascii() or not before.isdigit() or int(before) <= 0:
            raise ValueError('申请游标无效')
        query = query.filter(pk__lt=int(before))
    raw_limit = params.get('limit', '50')
    if not isinstance(raw_limit, str) or not raw_limit.isascii() or not raw_limit.isdigit() or not 1 <= int(raw_limit) <= 100:
        raise ValueError('每页数量须为 1 至 100')
    limit = int(raw_limit)
    rows = list(query.order_by('-pk')[:limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].pk if more else None


def _prepare(account):
    from orders.services import ensure_provider_dispatch_locks
    from club_accounts.provider_lifecycle import ensure_provider_lifecycle
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        ensure_provider_lifecycle(account)
    ensure_provider_dispatch_locks([account.pk])


def _lock_provider(account):
    from orders.services import lock_provider_dispatch
    from club_accounts.provider_lifecycle import lock_provider_operation
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        lock_provider_operation(account)
    lock_provider_dispatch([account.pk])
    profile = EscortProfile.objects.select_for_update().filter(account_id=account.pk).first()
    current = ClubAccount.objects.select_for_update().filter(pk=account.pk, account_type='PROVIDER', is_active=True,
        can_login=True, license_membership_active=True, business_cycle=account.business_cycle).first()
    if not profile or not current:
        raise ValueError('陪玩账号或原业务周期已变化')
    return profile


class ProviderDepositApplicationView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type != 'PROVIDER':
            return Response({'code': 403, 'msg': '仅陪玩可查看退押金申请'})
        try:
            rows, cursor = _page(DepositRefundApplication.objects.select_related('account', 'profile').filter(
                account=request.account, business_cycle=request.account.business_cycle), request.query_params, default_status='ALL')
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)})
        return Response({'code': 0, 'data': {'items': [{**_data(row), 'current_eligibility': _current_eligibility(row)}
            for row in rows], 'next_before_id': cursor}})

    def post(self, request):
        if request.account.account_type != 'PROVIDER':
            return Response({'code': 403, 'msg': '仅陪玩可申请退押金'})
        body = request.data
        if not isinstance(body, dict) or set(body) - {'request_id', 'amount', 'note'}:
            return Response({'code': 400, 'msg': '申请内容无效'})
        key, amount, note = body.get('request_id'), body.get('amount'), body.get('note', '')
        if (not isinstance(key, str) or not key.strip() or key != key.strip() or len(key) > 64
                or type(amount) is not int or amount <= 0 or amount > 2147483647
                or not isinstance(note, str) or len(note.strip()) > 255):
            return Response({'code': 400, 'msg': '请填写有效的申请编号和押金金额'})
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from wallet.services import get_wallet, WalletAddressingError
        from console.deposit_refund import deposit_refund_eligibility
        try:
            _prepare(request.account)
            with transaction.atomic():
                profile = _lock_provider(request.account)
                existing = DepositRefundApplication.objects.select_for_update().filter(account=request.account, request_id=key).first()
                if existing:
                    if (existing.business_cycle != request.account.business_cycle or existing.amount != amount
                            or existing.note != note.strip()):
                        return Response({'code': 409, 'msg': '申请编号已用于其他请求'})
                    return Response({'code': 0, 'data': _data(existing)})
                if DepositRefundApplication.objects.select_for_update().filter(profile=profile, status='PENDING').exists():
                    return Response({'code': 409, 'msg': '已有待处理退押金申请'})
                wallet = get_wallet(account=profile.account, user_id=profile.user_id, for_update=True, create=False)
                result = deposit_refund_eligibility(profile, wallet)
                allowed = result['can_refund'] or (result['wait_status'] == 'WAITING'
                    and result['reasons'] == ['业务结清后须等待 7 天'])
                if not allowed or amount > profile.deposit_paid:
                    return Response({'code': 409, 'msg': '；'.join(result['reasons']) or '申请金额超过已缴押金', 'data': result})
                row = DepositRefundApplication.objects.create(account=request.account, profile=profile,
                    business_cycle=request.account.business_cycle, request_id=key, amount=amount, note=note.strip(),
                    cleared_at=result['cleared_at'], eligible_at=result['eligible_at'])
                from site_messages.deposit_application_notice import record_all
                record_all(row)
                return Response({'code': 0, 'msg': '申请已提交，尚未退还押金', 'data': _data(row)})
        except (ProviderLifecycleError, WalletAddressingError, ValueError) as exc:
            return Response({'code': 409, 'msg': str(exc)})


class ProviderDepositApplicationCancelView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, pk):
        if request.account.account_type != 'PROVIDER':
            return Response({'code': 403, 'msg': '仅陪玩可取消申请'})
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        try:
            _prepare(request.account)
            with transaction.atomic():
                profile = _lock_provider(request.account)
                row = DepositRefundApplication.objects.select_for_update().filter(pk=pk, profile=profile,
                    account=request.account, business_cycle=request.account.business_cycle).first()
                if not row:
                    return Response({'code': 404, 'msg': '申请不存在'})
                if row.status == 'CANCELLED':
                    return Response({'code': 0, 'data': _data(row)})
                if row.status != 'PENDING':
                    return Response({'code': 409, 'msg': '申请已处理，不能取消', 'data': _data(row)})
                row.status, row.processed_at = 'CANCELLED', timezone.now()
                row.save(update_fields=['status', 'processed_at'])
                from site_messages.deposit_application_notice import refresh_delivered
                refresh_delivered(row)
                return Response({'code': 0, 'data': _data(row)})
        except (ProviderLifecycleError, ValueError) as exc:
            return Response({'code': 409, 'msg': str(exc)})


class StaffDepositApplicationListView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if 'escort:deposit_refund' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权处理退押金申请'})
        try:
            rows, cursor = _page(DepositRefundApplication.objects.select_related('account', 'profile'),
                                 request.query_params, default_status='PENDING')
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)})
        return Response({'code': 0, 'data': {'items': [{**_data(row), 'current_eligibility': _current_eligibility(row), 'account_id': row.account_id,
            'account_name': row.account.nickname or row.account.username} for row in rows], 'next_before_id': cursor}})


class StaffDepositApplicationProcessView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request, pk):
        if 'escort:deposit_refund' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权处理退押金申请'})
        body = request.data
        if not isinstance(body, dict) or set(body) != {'request_id', 'reason'}:
            return Response({'code': 400, 'msg': '请填写处理编号和原因'})
        key, reason = body['request_id'], body['reason']
        if not isinstance(key, str) or not key.strip() or key != key.strip() or len(key) > 64 or not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
            return Response({'code': 400, 'msg': '处理编号或原因无效'})
        row = DepositRefundApplication.objects.filter(pk=pk).first()
        if not row:
            return Response({'code': 404, 'msg': '申请不存在'})
        if row.status == 'REFUNDED' and row.refund_transaction_id:
            return Response({'code': 0, 'msg': '申请已办理', 'data': _data(row)})
        if row.status != 'PENDING':
            return Response({'code': 409, 'msg': '申请已结束', 'data': _data(row)})
        from console.deposit_refund import refund_deposit
        code, msg, details = refund_deposit(request, row.profile_id, row.amount, reason.strip(), key, application_id=row.pk)
        row.refresh_from_db()
        if code and row.status == 'REFUNDED' and row.refund_transaction_id:
            return Response({'code': 0, 'msg': '申请已办理', 'data': _data(row)})
        return Response({'code': code, 'msg': msg, 'data': _data(row) if not code else details})


class StaffDepositApplicationRejectView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request, pk):
        if 'escort:deposit_refund' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权处理退押金申请'})
        reason = request.data.get('reason') if isinstance(request.data, dict) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
            return Response({'code': 400, 'msg': '请填写拒绝原因'})
        row = DepositRefundApplication.objects.select_related('account').filter(pk=pk).first()
        if not row:
            return Response({'code': 404, 'msg': '申请不存在'})
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        try:
            _prepare(row.account)
            with transaction.atomic():
                profile = _lock_provider(row.account)
                row = DepositRefundApplication.objects.select_for_update().get(pk=pk, profile=profile)
                if row.business_cycle != row.account.business_cycle:
                    return Response({'code': 409, 'msg': '申请所属业务周期已变化'})
                if row.status == 'REJECTED' and row.operator_id == request.account.pk and row.resolution == reason.strip():
                    return Response({'code': 0, 'data': _data(row)})
                if row.status != 'PENDING':
                    return Response({'code': 409, 'msg': '申请已结束', 'data': _data(row)})
                row.status, row.operator, row.resolution, row.processed_at = 'REJECTED', request.account, reason.strip(), timezone.now()
                row.save(update_fields=['status', 'operator', 'resolution', 'processed_at'])
                from site_messages.deposit_application_notice import refresh_delivered, record_rejection
                refresh_delivered(row)
                record_rejection(row)
                return Response({'code': 0, 'data': _data(row)})
        except (ProviderLifecycleError, ValueError) as exc:
            return Response({'code': 409, 'msg': str(exc)})


class StaffDepositApplicationRemindView(APIView):
    permission_classes = [IsConsoleUser]

    def post(self, request, pk):
        if 'escort:deposit_refund' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权处理退押金申请'})
        initial = DepositRefundApplication.objects.select_related('account').filter(pk=pk).first()
        if not initial:
            return Response({'code': 404, 'msg': '申请不存在'})
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        try:
            _prepare(initial.account)
            with transaction.atomic():
                profile = _lock_provider(initial.account)
                row = DepositRefundApplication.objects.select_for_update().filter(pk=pk, profile=profile).first()
                if not row or row.status != 'PENDING' or row.business_cycle != initial.account.business_cycle:
                    return Response({'code': 409, 'msg': '申请已结束或原业务周期已变化'})
                from site_messages.deposit_application_notice import record_all
                result = record_all(row, retry=True)
                from console.models import AdminAuditLog
                AdminAuditLog.objects.create(operator=getattr(request, 'legacy_user', None), operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
                    path=request.path[:255], resource='deposit-application-remind', object_id=str(row.pk),
                    request_body={'application_id': row.pk}, status_code=200)
                request._request._business_audit_recorded = True
                msg = ('已创建或重试站内通知意图' if result['queued'] else
                       '已有站内消息记录，无需重复通知' if result['already_recorded'] else
                       '当前没有可通知的有权客服，请从待办队列处理')
                return Response({'code': 0, 'msg': msg, 'data': result})
        except (ProviderLifecycleError, ValueError) as exc:
            return Response({'code': 409, 'msg': str(exc)})
