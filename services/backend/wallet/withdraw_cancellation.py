"""Owner cancellation before the store's initial review."""
from rest_framework.response import Response
from rest_framework.views import APIView
from django.conf import settings
from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from .models import Transaction, WithdrawRequest
from .receipts import fund_success, recover_withdrawal_result
from .services import claim_fund_write, get_wallet


class WithdrawCancelView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'wallet_write'

    @recover_withdrawal_result('withdraw.cancel')
    def post(self, request, pk):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩本人可取消提现'})
        key = request.data.get('client_request_id')
        if not isinstance(key, str) or not key.strip() or len(key) > 64:
            return Response({'code': 400, 'msg': '缺少有效的请求编号'})
        with transaction.atomic():
            if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operation
                try:
                    lock_provider_operation(request.account)
                except ProviderLifecycleError:
                    return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请联系客服'}, status=503)
                # Authentication may have completed before a concurrent
                # retirement acquired the exclusive lifecycle lock. Never
                # release an old request into a later membership cycle.
                if not ClubAccount.objects.filter(pk=request.account.pk,
                        account_type=ClubAccount.AccountType.PROVIDER,
                        license_membership_active=True,
                        business_cycle=request.account.business_cycle).exists():
                    return Response({'code': 409, 'msg': '陪玩账号周期已变化，请重新登录核对'}, status=409)
            row = WithdrawRequest.objects.select_for_update().filter(pk=pk, account=request.account).first()
            if row is None:
                return Response({'code': 404, 'msg': '提现申请不存在'})
            from .withdraw_lifecycle import matches_cycle
            if not matches_cycle(row):
                return Response({'code': 409, 'msg': '提现申请账号周期待核对'}, status=409)
            if row.workflow_version != 2:
                return Response({'code': 409, 'msg': '历史提现须由工作人员核实付款状态后处理'})
            if row.status != WithdrawRequest.Status.PENDING:
                return Response({'code': 409, 'msg': '已进入审核或已处理，请联系工作人员核实'})
            wallet = get_wallet(account_id=row.account_id, user_id=row.user_id, for_update=True, create=False)
            if (wallet is None or wallet.frozen_amount < row.amount or wallet.balance + row.amount > 2147483647
                    or not row.transaction_id or not Transaction.objects.filter(pk=row.transaction_id, wallet=wallet,
                        tx_type=Transaction.TxType.WITHDRAW, status=Transaction.Status.PENDING, amount=-row.amount).exists()):
                return Response({'code': 409, 'msg': '提现资金状态异常，请联系客服核对'})
            allowed, reason = claim_fund_write(account=request.account, request_id=key,
                scope='withdraw.cancel', fingerprint=str(row.pk))
            if not allowed:
                return Response({'code': 409, 'msg': reason})
            from .withdraw_release import release_locked
            try:
                release_locked(row, wallet, actor_kind='OWNER', actor_account=request.account, reason='陪玩本人取消')
            except ValueError as exc:
                transaction.set_rollback(True)
                return Response({'code': 409, 'msg': str(exc)}, status=409)
            row.status = WithdrawRequest.Status.CANCELLED
            row.save(update_fields=['status', 'updated_at'])
            result = fund_success(request, 'withdraw.cancel', msg='提现已取消，余额已解冻',
                                  data={'id': row.pk, 'status': row.status, 'balance': wallet.balance})
            from .withdraw_notifications import withdrawal_message
            withdrawal_message(row, 'owner_cancelled')
            from .notifier import notify_wallet_update, notify_withdraw_update
            transaction.on_commit(lambda: notify_wallet_update(wallet))
            transaction.on_commit(lambda: notify_withdraw_update(row))
        return result
