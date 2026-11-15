"""Store review must not mark a withdrawal as paid."""
from django.utils import timezone
from django.conf import settings
from rest_framework.response import Response
from stores.db import transaction
from wallet.models import WithdrawRequest


def store_approve(view, request, pk):
    if request.data.get('payout_reference'):
        return Response({'code': 400, 'msg': '门店仅能初审，请勿登记打款；请刷新页面'})
    provider = None
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        from club_accounts.models import ClubAccount
        from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
        provider_id = WithdrawRequest.objects.filter(pk=pk).values_list('account_id', flat=True).first()
        provider = ClubAccount.objects.filter(pk=provider_id, account_type='PROVIDER').first()
        if provider is None:
            return Response({'code': 409, 'msg': '提现所属陪玩待核对'}, status=409)
        try:
            ensure_provider_lifecycle(provider)
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验'}, status=503)
    with transaction.atomic():
        if provider is not None:
            from club_accounts.provider_lifecycle import lock_provider_operation
            try:
                lock_provider_operation(provider)
            except ProviderLifecycleError:
                return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验'}, status=503)
        row = WithdrawRequest.objects.select_for_update().filter(pk=pk).first()
        if row is None:
            return Response({'code': 404, 'msg': '提现申请不存在'}, status=404)
        if row.workflow_version != 2:
            return Response({'code': 409, 'msg': '历史提现须先人工核实付款状态，不能直接审核'}, status=409)
        from .views import _withdraw_claim_is_current
        if (row.status == WithdrawRequest.Status.STORE_APPROVED and row.auditor_account_id == request.account.pk
                and row.claimed_by_account_id == request.account.pk
                and _withdraw_claim_is_current(row, request.data.get('expected_claimed_at'))):
            return Response({'code': 0, 'data': view.get_serializer(row).data, 'msg': '门店初审已通过'})
        if row.status != WithdrawRequest.Status.PENDING:
            return Response({'code': 409, 'msg': '提现状态已变化，请刷新'}, status=409)
        from wallet.withdraw_lifecycle import matches_cycle
        if ((provider is not None and (row.account_id != provider.pk
                or row.account_business_cycle != provider.business_cycle)) or not matches_cycle(row)):
            return Response({'code': 409, 'msg': '提现申请账号周期待核对'}, status=409)
        if row.claimed_by_account_id != request.account.pk or not _withdraw_claim_is_current(row, request.data.get('expected_claimed_at')):
            return Response({'code': 409, 'msg': '领取状态已变化，请刷新后重新核对'}, status=409)
        # Freeze is shared by other pending withdrawals and income holds, so
        # verify this application's own pending debit while accepting a larger
        # aggregate frozen balance. Initial review never moves wallet funds.
        from wallet.models import Wallet, Transaction, compute_withdraw_tax
        wallet = Wallet.objects.select_for_update().filter(account_id=row.account_id).first()
        debit = (Transaction.objects.filter(pk=row.transaction_id, wallet=wallet).first()
                 if wallet and row.transaction_id else None)
        if (not wallet or wallet.account_id != row.account_id
                or (row.user_id is not None and wallet.user_id != row.user_id)
                or row.amount <= 0 or wallet.frozen_amount < row.amount
                or not 0 <= row.tax_rate <= 100 or row.tax_amount != compute_withdraw_tax(row.amount, row.tax_rate)
                or row.actual_amount != row.amount - row.tax_amount or row.actual_amount <= 0
                or not debit or debit.tx_type != Transaction.TxType.WITHDRAW
                or debit.status != Transaction.Status.PENDING or debit.amount != -row.amount
                or debit.balance_after != debit.balance_before + debit.amount
                or debit.reversal_of_id or debit.reversals.exists()
                or WithdrawRequest.objects.filter(transaction_id=row.transaction_id).exclude(pk=row.pk).exists()):
            return Response({'code': 409, 'msg': '提现冻结或申请流水待核对，暂不能初审'}, status=409)
        row.status = WithdrawRequest.Status.STORE_APPROVED
        row.auditor, row.auditor_account, row.audited_at = request.legacy_user, request.account, timezone.now()
        row.save(update_fields=['status', 'auditor', 'auditor_account', 'audited_at', 'updated_at'])
        from wallet.withdraw_notifications import withdrawal_message
        withdrawal_message(row, 'store_approved')
        from wallet.notifier import notify_withdraw_update
        transaction.on_commit(lambda: notify_withdraw_update(row))
    return Response({'code': 0, 'data': view.get_serializer(row).data, 'msg': '门店初审通过，等待平台财务审核'})
