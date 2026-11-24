"""Platform review and offline payout facts, committed within the store database."""
import hashlib
import json
from django.conf import settings
from django.db import DatabaseError, IntegrityError
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from stores.context import store_context
from stores.db import transaction
from stores.models import PlatformAuditEvent
from stores.platform_auth import IsPlatformAuthenticated, PlatformAuthentication, account_for_token
from stores.platform_permissions import platform_may, platform_stores_for
from stores.platform_views import admin_origin, fail
from wallet.models import SystemConfig, Transaction, WithdrawalPlatformEvent, WithdrawRequest, get_platform_wallet
from wallet.services import get_wallet

OPEN = (WithdrawRequest.Status.STORE_APPROVED, WithdrawRequest.Status.PLATFORM_APPROVED,
        WithdrawRequest.Status.PAYMENT_UNKNOWN)
HISTORY = (WithdrawRequest.Status.PAID, WithdrawRequest.Status.REJECTED, WithdrawRequest.Status.CANCELLED)
PAYOUT_REFERENCE_LOCK_KEY = 'withdraw_payout_reference_lock'


def _read_store(store):
    with store_context(store):
        values = WithdrawRequest.objects.filter(workflow_version=2, status__in=OPEN).values('status').annotate(count=Count('pk'))
        counts = {row['status']: row['count'] for row in values}
        legacy_pending = WithdrawRequest.objects.filter(workflow_version=1, status=WithdrawRequest.Status.PENDING).count()
        return counts, legacy_pending


def _legacy_ledger(row):
    """Read existing facts only: total frozen does not prove this payment is unpaid."""
    tx = row.transaction
    if tx is None:
        return {'transaction_present': False}
    wallet = tx.wallet
    pairs = [(row.account_id, wallet.account_id), (row.user_id, wallet.user_id)]
    comparable = [(left, right) for left, right in pairs if left is not None and right is not None]
    return {
        'transaction_present': True, 'transaction_no': tx.tx_no,
        'transaction_status': tx.status, 'transaction_status_display': tx.get_status_display(),
        'transaction_type': tx.tx_type, 'transaction_amount': tx.amount,
        'wallet_frozen_total': wallet.frozen_amount,
        'owner_match': bool(comparable) and all(left == right for left, right in comparable),
    }


def _authorized_store(request, slug, action):
    return platform_stores_for(request.platform_account, action).filter(slug=slug).first()


def _store_ready(store):
    from .dynamic_database import store_connection_ready
    return store_connection_ready(store)


class PlatformWithdrawalStoresView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        stores = platform_stores_for(request.platform_account, 'withdraw:view').order_by('id')
        if not stores.exists():
            return fail('无平台提现查看权限', 403)
        rows = []
        for store in stores:
            item = {'slug': store.slug, 'name': store.name, 'currency_name': store.currency_name,
                    'status': 'UNAVAILABLE', 'counts': None, 'legacy_pending': None}
            if _store_ready(store):
                try:
                    item['counts'], item['legacy_pending'] = _read_store(store)
                    item['status'] = 'OK'
                except DatabaseError:
                    pass
            rows.append(item)
        PlatformAuditEvent.objects.create(account=request.platform_account, action='withdraw.view_stores')
        response = Response({'code': 0, 'data': {'stores': rows, 'read_at': timezone.now().isoformat()}})
        response['Cache-Control'] = 'no-store'
        return response


class PlatformWithdrawalListView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        store = _authorized_store(request, slug, 'withdraw:view')
        if store is None:
            return fail('门店不存在或无权查看', 404)
        if not _store_ready(store):
            return fail('门店数据暂不可用', 503)
        try:
            page = int(request.query_params.get('page', '1'))
            if not 1 <= page <= 100000:
                raise ValueError()
        except ValueError:
            return fail('页码无效', 400)
        scope = request.query_params.get('scope', 'open')
        if scope not in ('open', 'history', 'legacy'):
            return fail('查询范围无效', 400)
        try:
            with store_context(store):
                if scope == 'legacy':
                    qs = WithdrawRequest.objects.filter(workflow_version=1, status=WithdrawRequest.Status.PENDING).select_related('transaction__wallet')
                else:
                    qs = WithdrawRequest.objects.filter(workflow_version=2, status__in=OPEN if scope == 'open' else HISTORY)
                qs = qs.select_related('account', 'auditor_account').order_by('-created_at', '-id')
                total = qs.count()
                rows = list(qs[(page - 1) * 30:page * 30])
                data = [{'id': row.pk, 'amount': row.amount, 'actual_amount': row.actual_amount,
                         'tax_amount': row.tax_amount, 'status': row.status, 'status_display': row.get_status_display(),
                         'provider_account_id': row.account_id,
                         'provider_name': (row.account.nickname or row.account.username) if row.account else None,
                         'store_reviewer_account_id': row.auditor_account_id,
                         'store_reviewer_name': (row.auditor_account.nickname or row.auditor_account.username)
                             if row.auditor_account else None,
                         'store_reviewed_at': row.audited_at.isoformat() if row.audited_at else None,
                         'platform_reviewer_id': str(row.platform_reviewer_id) if row.platform_reviewer_id else None,
                         'platform_payer_id': str(row.platform_payer_id) if row.platform_payer_id else None,
                         'payee_method': row.get_payee_method_display(), 'payee_name': row.payee_name,
                         'payee_account': row.payee_account, 'created_at': row.created_at.isoformat(),
                         'platform_reviewed_at': row.platform_reviewed_at.isoformat() if row.platform_reviewed_at else None,
                         'payout_reference': row.payout_reference, 'audit_remark': row.audit_remark,
                         'paid_at': row.paid_at.isoformat() if row.paid_at else None,
                         'payout_recorded_at': row.payout_recorded_at.isoformat() if row.payout_recorded_at else None,
                         'workflow_version': row.workflow_version,
                         'legacy_ledger': _legacy_ledger(row) if scope == 'legacy' else None}
                        for row in rows]
        except DatabaseError:
            return fail('门店数据暂不可用', 503)
        PlatformAuditEvent.objects.create(account=request.platform_account, action='withdraw.view_queue', target_store_slug=slug,
                                          detail={'page': page, 'scope': scope})
        response = Response({'code': 0, 'data': {'name': store.name, 'currency_name': store.currency_name,
                            'page': page, 'page_size': 30, 'total': total, 'results': data}})
        response['Cache-Control'] = 'no-store'
        return response


def _fingerprint(action, payload):
    values = {key: value for key, value in payload.items() if key != 'client_request_id'}
    return hashlib.sha256(json.dumps({'action': action, 'data': values}, sort_keys=True,
                                     separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class PlatformWithdrawalActionView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug, pk, action):
        if not admin_origin(request) or action != 'audit-recover':
            return fail('操作无效', 404)
        try:
            store = _authorized_store(request, slug, 'withdraw:view')
            if store is None:
                return fail('门店不存在或无权查看', 404)
            if not _store_ready(store):
                return fail('门店数据暂不可用', 503)
            with store_context(store):
                if not WithdrawRequest.objects.filter(pk=pk, workflow_version=2).exists():
                    return fail('提现申请不存在或需要人工核实', 404)
                events = list(WithdrawalPlatformEvent.objects.filter(withdrawal_id=pk).order_by('id'))
            from .models import PlatformWithdrawalAuditReceipt
            from .platform_withdrawal_audit import read_event, receipt_matches
            receipts = {row.tenant_event_id: row for row in PlatformWithdrawalAuditReceipt.objects.select_related('audit').filter(
                store=store, tenant_event_id__in=[event.pk for event in events])}
            rows = []
            for event in events:
                receipt = receipts.get(event.pk)
                try:
                    facts = read_event(store, pk, event.pk)
                    audit_status = ('RECORDED' if receipt_matches(receipt, store, facts) else 'REVIEW') if receipt else 'MISSING'
                except ValueError:
                    audit_status = 'REVIEW'
                rows.append({'event_id': event.pk, 'request_id': event.request_id, 'action': event.action,
                    'actor_id': str(event.platform_actor_id), 'actor_name': event.actor_name,
                    'from_status': event.from_status, 'to_status': event.to_status, 'audit_status': audit_status})
            response = Response({'code': 0, 'data': {'events': rows}})
            response['Cache-Control'] = 'no-store'
            return response
        except DatabaseError:
            return fail('平台或门店审计服务暂不可用，请稍后重试', 503, 'AUDIT_UNAVAILABLE')

    def post(self, request, slug, pk, action):
        if not admin_origin(request):
            return fail('平台账号请从管理后台入口访问', 403)
        if action == 'audit-recover':
            return self.recover_audit(request, slug, pk)
        required = 'withdraw:record_payment' if action in ('paid', 'mark-unknown') else 'withdraw:final_review'
        if action not in ('approve', 'mark-unknown', 'paid', 'reject', 'cancel'):
            return fail('操作无效', 404)
        try:
            store = _authorized_store(request, slug, required)
        except DatabaseError:
            return fail('平台授权服务暂不可用，请稍后重试', 503, 'PLATFORM_UNAVAILABLE')
        if store is None:
            return fail('门店不存在或无权操作', 404)
        if not _store_ready(store):
            return fail('门店数据暂不可用', 503)
        if not isinstance(request.data, dict):
            return fail('请求格式无效', 400)
        key = request.data.get('client_request_id')
        if not isinstance(key, str) or not key.strip() or len(key) > 64:
            return fail('请提供有效的操作编号', 400)
        key = key.strip()
        fingerprint = _fingerprint(action, request.data)
        with store_context(store), transaction.atomic():
            provider = None
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.models import ClubAccount
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operation
                provider_id = WithdrawRequest.objects.filter(pk=pk).values_list('account_id', flat=True).first()
                provider = ClubAccount.objects.filter(pk=provider_id, account_type='PROVIDER').first()
                if provider is None:
                    return fail('提现所属陪玩待核对', 409)
                try:
                    lock_provider_operation(provider)
                except ProviderLifecycleError:
                    return fail('陪玩账号状态暂不可核验，请联系平台', 503)
            row = WithdrawRequest.objects.select_for_update().filter(pk=pk).first()
            if row is None or row.workflow_version != 2:
                return fail('提现申请不存在或需要人工核实历史状态', 404)
            if provider is not None and row.account_id != provider.pk:
                return fail('提现所属陪玩已变化，请重新核对', 409)
            try:
                actor = account_for_token(request.auth)
                authorized = platform_may(actor, required, store_slug=slug)
            except TokenError:
                return fail('平台登录已失效', 401)
            except DatabaseError:
                return fail('平台授权服务暂不可用，请稍后重试', 503, 'PLATFORM_UNAVAILABLE')
            if not authorized:
                return fail('门店授权已变化', 403)
            prior = WithdrawalPlatformEvent.objects.filter(platform_actor_id=actor.pk, request_id=key).first()
            if prior:
                prior_action_matches = (prior.action == action or (action == 'paid' and prior.action == 'mark-unknown'
                    and isinstance(prior.detail, dict) and prior.detail.get('source_action') == 'paid'))
                if prior.withdrawal_id != row.pk or not prior_action_matches or prior.request_fingerprint != fingerprint:
                    return fail('操作编号已用于其他请求', 409, 'REQUEST_ID_CONFLICT')
                result = {'id': row.pk, 'status': prior.to_status, 'replayed': True, 'audit_status': 'PENDING'}
                self.schedule_audit(store, row.pk, prior.pk, result)
                return Response({'code': 0, 'msg': '该操作已登记', 'data': result})
            if provider is not None and not ClubAccount.objects.filter(pk=provider.pk,
                    account_type=ClubAccount.AccountType.PROVIDER,
                    business_cycle=provider.business_cycle).exists():
                return fail('提现所属陪玩账号周期已变化，请核对', 409)
            from wallet.withdraw_lifecycle import matches_cycle
            if not matches_cycle(row):
                return fail('提现申请账号周期待核对', 409)
            previous = row.status
            wallet = None
            detail = {}
            if action == 'approve':
                if previous != WithdrawRequest.Status.STORE_APPROVED:
                    return fail('提现状态已变化，请刷新', 409)
                row.status = WithdrawRequest.Status.PLATFORM_APPROVED
                row.platform_reviewer_id, row.platform_reviewed_at = actor.pk, timezone.now()
            elif action == 'mark-unknown':
                reason = request.data.get('reason')
                if previous != WithdrawRequest.Status.PLATFORM_APPROVED:
                    return fail('提现状态已变化，请刷新', 409)
                if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
                    return fail('请填写待核实原因', 400)
                row.status, detail = WithdrawRequest.Status.PAYMENT_UNKNOWN, {'reason': reason.strip()}
            elif action in ('reject', 'cancel'):
                reason = request.data.get('reason')
                if previous not in OPEN or request.data.get('confirmed_unpaid') is not True:
                    return fail('请先核实该申请尚未打款', 409)
                if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
                    return fail('请填写处理原因', 400)
                wallet = get_wallet(account_id=row.account_id, user_id=row.user_id, for_update=True, create=False)
                if not _valid_frozen(row, wallet) or wallet.balance + row.amount > 2147483647:
                    return fail('冻结资金状态异常，请核对', 409)
                from wallet.withdraw_release import release_locked
                try:
                    release = release_locked(row, wallet, actor_kind='PLATFORM', platform_actor_id=actor.pk,
                                             reason=reason.strip())
                except ValueError as exc:
                    transaction.set_rollback(True)
                    return fail(str(exc), 409)
                row.status = WithdrawRequest.Status.REJECTED if action == 'reject' else WithdrawRequest.Status.CANCELLED
                row.audit_remark, detail = reason.strip(), {'reason': reason.strip(), 'confirmed_unpaid': True,
                                                           'release_id': release.pk}
            else:
                if previous not in (WithdrawRequest.Status.PLATFORM_APPROVED, WithdrawRequest.Status.PAYMENT_UNKNOWN):
                    return fail('请先完成平台终审', 409)
                reference, raw_time = request.data.get('payout_reference'), request.data.get('paid_at')
                if not isinstance(reference, str) or not reference.strip() or len(reference.strip()) > 100 or not isinstance(raw_time, str):
                    return fail('请填写实际打款凭据及时间', 400)
                try:
                    paid_at = parse_datetime(raw_time)
                except ValueError:
                    paid_at = None
                if not paid_at or timezone.is_naive(paid_at) or paid_at > timezone.now() or paid_at < row.created_at:
                    return fail('实际打款时间无效', 400)
                if not row.platform_reviewed_at or paid_at < row.platform_reviewed_at:
                    return fail('实际打款时间不能早于平台终审通过时间，请核对打款凭据', 400)
                reference = reference.strip()
                paid_amount_fen = request.data.get('paid_amount_fen')
                if type(paid_amount_fen) is not int or not 0 < paid_amount_fen <= 2147483647:
                    return fail('请填写实际打款人民币金额，单位为分', 400, 'PAYOUT_AMOUNT_REQUIRED')
                if paid_amount_fen != row.actual_amount:
                    if previous == WithdrawRequest.Status.PAYMENT_UNKNOWN:
                        return fail('实际打款金额与申请快照不一致，请继续核对；冻结保持不变', 409, 'PAYOUT_AMOUNT_MISMATCH')
                    # This records the operator's report, not proof of payment.
                    # Keep all funds frozen until the original payout is reconciled.
                    row.status = WithdrawRequest.Status.PAYMENT_UNKNOWN
                    row.save(update_fields=['status', 'updated_at'])
                    mismatch_detail = {'source_action': 'paid', 'reason': '实际打款金额与申请快照不一致',
                        'reported_paid_amount_fen': paid_amount_fen, 'expected_amount_fen': row.actual_amount,
                        'reported_payout_reference': reference, 'reported_paid_at': paid_at.isoformat()}
                    try:
                        with transaction.atomic():
                            event = WithdrawalPlatformEvent.objects.create(withdrawal=row, platform_actor_id=actor.pk,
                                actor_name=actor.username, action='mark-unknown', request_id=key,
                                request_fingerprint=fingerprint, from_status=previous, to_status=row.status,
                                detail=mismatch_detail)
                    except IntegrityError:
                        transaction.set_rollback(True)
                        return fail('操作编号已用于其他请求，请核对原记录', 409, 'REQUEST_ID_CONFLICT')
                    from wallet.withdraw_notifications import withdrawal_message
                    withdrawal_message(row, 'payment_unknown', platform_event=event)
                    from wallet.notifier import notify_withdraw_update
                    transaction.on_commit(lambda: notify_withdraw_update(row))
                    result = {'id': row.pk, 'status': row.status, 'replayed': False, 'audit_status': 'PENDING'}
                    self.schedule_audit(store, row.pk, event.pk, result)
                    return Response({'code': 0, 'msg': '打款金额与申请不一致，已转待核对，冻结保持不变', 'data': result})
                # Distinct withdrawals sharing a batch reference serialize the
                # duplicate warning, including zero-fee payouts.
                lock, _ = SystemConfig.objects.get_or_create(key=PAYOUT_REFERENCE_LOCK_KEY,
                    defaults={'value': '', 'remark': '提现凭据核验并发锁'})
                SystemConfig.objects.select_for_update().get(pk=lock.pk)
                duplicate = WithdrawRequest.objects.filter(payout_reference=reference, status__in=['PAID', 'APPROVED']).exclude(pk=row.pk).exists()
                if duplicate and request.data.get('acknowledge_duplicate_reference') is not True:
                    return fail('该凭据编号曾用于其他提现，请核对是否属于同一批次', 409, 'DUPLICATE_PAYOUT_REFERENCE')
                wallet = get_wallet(account_id=row.account_id, user_id=row.user_id, for_update=True, create=False)
                if not _valid_frozen(row, wallet):
                    return fail('冻结资金状态异常，请核对', 409)
                shop = get_platform_wallet(for_update=True) if row.tax_amount else None
                if shop and shop.balance + row.tax_amount > 2147483647:
                    return fail('门店费用账户金额超限', 409)
                wallet.frozen_amount -= row.amount
                wallet.save(update_fields=['frozen_amount'])
                Transaction.objects.filter(pk=row.transaction_id).update(status=Transaction.Status.SUCCESS, remark='平台财务登记线下提现打款')
                if shop:
                    before = shop.balance
                    shop.balance += row.tax_amount
                    shop.save(update_fields=['balance'])
                    Transaction.objects.create(wallet=shop, tx_type=Transaction.TxType.WITHDRAW_TAX, amount=row.tax_amount,
                        balance_before=before, balance_after=shop.balance, remark=f'提现费用：申请 #{row.pk}')
                row.status, row.payout_reference, row.paid_at = WithdrawRequest.Status.PAID, reference, paid_at
                row.platform_payer_id, row.payout_recorded_at = actor.pk, timezone.now()
                detail = {'payout_reference': reference, 'paid_at': paid_at.isoformat(),
                          'paid_amount_fen': paid_amount_fen,
                          'duplicate_reference_acknowledged': bool(duplicate)}
            row.save(update_fields=['status', 'platform_reviewer_id', 'platform_reviewed_at', 'platform_payer_id',
                                    'payout_recorded_at', 'payout_reference', 'paid_at', 'audit_remark', 'updated_at'])
            try:
                with transaction.atomic():
                    event = WithdrawalPlatformEvent.objects.create(withdrawal=row, platform_actor_id=actor.pk, actor_name=actor.username,
                        action=action, request_id=key, request_fingerprint=fingerprint, from_status=previous, to_status=row.status, detail=detail)
            except IntegrityError:
                # Another withdrawal may have claimed the same key. Roll back
                # the *whole* funds transaction, not merely this savepoint.
                transaction.set_rollback(True)
                return fail('操作编号已用于其他请求，请核对原记录', 409, 'REQUEST_ID_CONFLICT')
            from wallet.withdraw_notifications import withdrawal_message
            notification_event = {'approve': 'platform_approved', 'mark-unknown': 'payment_unknown',
                                  'paid': 'paid', 'reject': 'rejected', 'cancel': 'cancelled'}[action]
            withdrawal_message(row, notification_event,
                reason=detail.get('reason', '') if action in ('reject', 'cancel') else '', platform_event=event)
            from wallet.notifier import notify_wallet_update, notify_withdraw_update
            transaction.on_commit(lambda: notify_withdraw_update(row))
            if wallet:
                transaction.on_commit(lambda: notify_wallet_update(wallet))
            result = {'id': row.pk, 'status': row.status, 'replayed': False, 'audit_status': 'PENDING'}
            self.schedule_audit(store, row.pk, event.pk, result)
            return Response({'code': 0, 'msg': '提现状态已更新', 'data': result})

    @staticmethod
    def schedule_audit(store, withdrawal_id, event_id, result):
        from .platform_withdrawal_audit import publish_committed_event
        def publish():
            publish_committed_event(store, withdrawal_id, event_id)
            from django.db import transaction as directory_transaction
            directory_transaction.on_commit(lambda: result.update(audit_status='RECORDED'),
                                            using='default', robust=True)
        transaction.on_commit(publish, robust=True)

    def recover_audit(self, request, slug, pk):
        if not isinstance(request.data, dict) or set(request.data) != {'event_id', 'client_request_id'}:
            return fail('请提供门店事件和原操作编号', 400)
        event_id, key = request.data['event_id'], request.data['client_request_id']
        if type(event_id) is not int or event_id <= 0 or not isinstance(key, str) or not key.strip() or len(key) > 64:
            return fail('门店事件或原操作编号无效', 400)
        try:
            store = _authorized_store(request, slug, 'withdraw:view')
            if store is None:
                return fail('门店不存在或无权查看', 404)
            if not _store_ready(store):
                return fail('门店数据暂不可用', 503)
            from .platform_withdrawal_audit import read_event, record_committed_event
            facts = read_event(store, pk, event_id)
            if facts['request_id'] != key.strip():
                return fail('原操作编号与门店事件不一致', 409)
            receipt, created = record_committed_event(store, facts, token=request.auth)
            result = {'event_id': event_id, 'audit_recorded': False, 'audit_status': 'PENDING',
                      'recovered': False, 'audit_id': None}
            from django.db import transaction as directory_transaction
            directory_transaction.on_commit(lambda: result.update(audit_recorded=True, audit_status='RECORDED',
                recovered=created, audit_id=receipt.audit_id), using='default', robust=True)
            return Response({'code': 0, 'data': result})
        except TokenError:
            return fail('平台登录已失效', 401)
        except PermissionError:
            return fail('无权补记该提现事件', 403)
        except ValueError as exc:
            return fail(str(exc), 409, 'AUDIT_REVIEW_REQUIRED')
        except DatabaseError:
            return fail('审计结果暂不可核验，请稍后以原事件重试', 503, 'AUDIT_UNAVAILABLE')


def _valid_frozen(row, wallet):
    return bool(wallet and wallet.frozen_amount >= row.amount and row.transaction_id and Transaction.objects.filter(
        pk=row.transaction_id, wallet=wallet, tx_type=Transaction.TxType.WITHDRAW,
        status=Transaction.Status.PENDING, amount=-row.amount).exists())
