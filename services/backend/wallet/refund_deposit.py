"""Refund deposit deficits and idempotent allocation of released income.

Callers must hold the order, money mutex and provider profile/wallet locks in
the existing global order. A refund deficit may be registered only from both
real deposit recovery ledger lines of the same refund; current legacy refund
flows are not automatically opted in until their source priority is updated.
"""
from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone
from stores.db import transaction

from .models import (RefundDepositCashRepayment, RefundDepositDeficit, RefundDepositIncomeApplication, RefundDepositRepayment,
                     Transaction, get_platform_wallet)

MAX_LEDGER_AMOUNT = 2147483647


def enabled():
    return bool(getattr(settings, 'REFUND_DEPOSIT_DEFICIT_ENABLED', False))


def protection_enabled():
    return (enabled() or bool(getattr(settings, 'REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED', False))
            or bool(getattr(settings, 'REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED', False)))


def _atomic():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('退款押金缺口操作必须在门店事务内执行')


def _valid(tx, *, kind, amount, wallet_id, order_id):
    return bool(tx and tx.wallet_id == wallet_id and tx.order_id == order_id and tx.tx_type == kind
        and tx.status == Transaction.Status.SUCCESS and tx.amount == amount
        and tx.balance_after == tx.balance_before + tx.amount)


def _cash_repaid(deficit, platform):
    """Reconcile real offline receipts without creating a second money flow."""
    rows = list(deficit.cash_repayments.select_for_update().select_related('topup__platform_transaction'))
    for row in rows:
        topup, tx = row.topup, row.topup.platform_transaction
        allocations = list(topup.refund_gap_repayments.select_for_update().select_related('deficit'))
        total = sum(item.amount for item in allocations)
        if (topup.profile_id != deficit.profile_id or topup.account_id != deficit.account_id
                or not deficit.account_business_cycle
                or topup.account_business_cycle != deficit.account_business_cycle
                or topup.kind != 'SUPPLEMENT' or not topup.reference_key and not topup.proof_key
                or topup.paid_after != topup.paid_before + topup.amount
                or topup.paid_after > topup.target_snapshot or not 0 < total <= topup.amount
                or any(item.deficit.profile_id != topup.profile_id or item.deficit.account_id != topup.account_id
                       or item.deficit.account_business_cycle != topup.account_business_cycle
                       for item in allocations)
                or not _valid(tx, kind='DEPOSIT_INCOME', amount=topup.amount, wallet_id=platform.pk, order_id=None)
                or tx.operator_account_id != topup.operator_id or tx.created_at > topup.created_at
                or tx.reversal_of_id or tx.reversals.exists()):
            raise ValueError('线下补差与退款押金缺口凭证不一致')
    return sum(row.amount for row in rows)


def apply_cash_topup_locked(*, topup, profile, platform):
    """Allocate this real offline receipt to the oldest verified deficits.

    The caller holds the profile and platform wallet and writes the receipt in
    the same transaction. No provider wallet or second cash ledger is created.
    """
    _atomic()
    deficits = list(RefundDepositDeficit.objects.select_for_update().filter(
        profile=profile, remaining__gt=0).order_by('created_at', 'pk'))
    if not deficits:
        return 0
    from .deposit_cycle import deposit_cycle
    cycle = deposit_cycle(profile)
    if topup.account_business_cycle != cycle or any(row.account_business_cycle != cycle for row in deficits):
        raise ValueError('退款押金补差账号周期待核对')
    if any(row.profile_id != profile.pk or row.account_id != profile.account_id for row in deficits):
        raise ValueError('退款押金缺口账号归属待核对')
    if not enabled():
        raise ValueError('退款押金缺口尚未接通线下补差，请先核对')
    if (topup.profile_id != profile.pk or topup.account_id != profile.account_id
            or topup.kind != 'SUPPLEMENT' or topup.amount <= 0
            or topup.paid_after != profile.deposit_paid or topup.paid_after != topup.paid_before + topup.amount
            or topup.paid_after > topup.target_snapshot or not (topup.reference_key or topup.proof_key)
            or not _valid(topup.platform_transaction, kind='DEPOSIT_INCOME', amount=topup.amount,
                          wallet_id=platform.pk, order_id=None)
            or topup.platform_transaction.operator_account_id != topup.operator_id
            or topup.platform_transaction.reversal_of_id or topup.platform_transaction.reversals.exists()
            or topup.refund_gap_repayments.exists()):
        raise ValueError('线下押金补差凭证异常')
    for deficit in deficits:
        if deficit.source_kind == 'DIRECT':
            _verify_direct_deficit(deficit, platform)
        else:
            verified = register_refund_deficit(refund=deficit.refund, profile=profile,
                provider_transaction=deficit.source_provider_transaction, store_transaction=deficit.source_store_transaction)
            if verified.pk != deficit.pk:
                raise ValueError('退款押金缺口原始凭证异常')
            paid = sum(deficit.repayments.select_for_update().values_list('amount', flat=True)) + _cash_repaid(deficit, platform)
            if paid + deficit.remaining != deficit.amount or deficit.closed_at:
                raise ValueError('退款押金缺口历史记录不一致')
    available = topup.amount
    for deficit in deficits:
        if not available:
            break
        amount = min(available, deficit.remaining)
        RefundDepositCashRepayment.objects.create(topup=topup, deficit=deficit, amount=amount)
        deficit.remaining -= amount
        if not deficit.remaining:
            deficit.closed_at = timezone.now()
        deficit.save(update_fields=['remaining', 'closed_at'])
        available -= amount
    if not RefundDepositDeficit.objects.filter(profile=profile, account_id=profile.account_id, remaining__gt=0).exists():
        from .models import RefundDepositRecoveryPermit
        permits = list(RefundDepositRecoveryPermit.objects.select_for_update().filter(
            profile=profile, closed_at__isnull=True))
        if len(permits) > 1 or any(row.profile_id != profile.pk or row.account_id != profile.account_id for row in permits):
            raise ValueError('陪玩存在多个未关闭的补差机会')
        if permits:
            permit = permits[0]
            permit.closed_at, permit.close_reason, permit.cash_topup = timezone.now(), 'CASH_GAP_PAID', topup
            permit.save(update_fields=['closed_at', 'close_reason', 'cash_topup'])
    return topup.amount - available


def register_refund_deficit(*, refund, profile, provider_transaction, store_transaction):
    """Attach a deficit to paired, already-posted deposit recovery facts."""
    _atomic()
    if not enabled():
        raise ValueError('退款押金缺口功能未启用')
    if not profile.account_id or not refund.pk or not provider_transaction or not store_transaction:
        raise ValueError('退款、陪玩或押金凭证不完整')
    amount = provider_transaction.amount
    if type(amount) is not int or amount <= 0:
        raise ValueError('退款扣押金金额无效')
    wallet = provider_transaction.wallet
    platform = get_platform_wallet(for_update=True)
    if (wallet.account_id != profile.account_id or wallet.user_id != profile.user_id
            or provider_transaction.deposit_owner_id != profile.account_id
            or store_transaction.deposit_owner_id != profile.account_id
            or not provider_transaction.deposit_business_cycle
            or store_transaction.deposit_business_cycle != provider_transaction.deposit_business_cycle
            or store_transaction.deposit_pair_id != provider_transaction.pk
            or not _valid(provider_transaction, kind='DEPOSIT', amount=amount, wallet_id=wallet.pk, order_id=refund.order_id)
            or not _valid(store_transaction, kind='DEPOSIT_INCOME', amount=-amount, wallet_id=platform.pk, order_id=refund.order_id)):
        raise ValueError('退款押金两侧流水不匹配')
    provider_recovery = refund.recovery_lines.filter(source='DEPOSIT', amount=amount,
        reversal_transaction=provider_transaction, wallet=wallet).first()
    store_recovery = refund.recovery_lines.filter(source='DEPOSIT', amount=-amount,
        reversal_transaction=store_transaction, wallet=platform).first()
    if (not provider_recovery or not store_recovery
            or provider_recovery.original_transaction_id != provider_transaction.reversal_of_id
            or store_recovery.original_transaction_id != store_transaction.reversal_of_id):
        raise ValueError('退款缺少可核验的押金追回记录')
    existing = RefundDepositDeficit.objects.filter(source_provider_transaction=provider_transaction).first()
    if existing:
        if (existing.refund_id != refund.pk or existing.profile_id != profile.pk or existing.account_id != profile.account_id
                or existing.amount != amount or existing.source_store_transaction_id != store_transaction.pk
                or existing.account_business_cycle != provider_transaction.deposit_business_cycle):
            raise ValueError('押金缺口登记与原退款不一致')
        return existing
    if RefundDepositDeficit.objects.filter(source_store_transaction=store_transaction).exists():
        raise ValueError('退款押金流水已关联其他缺口')
    from .deposit_cycle import deposit_cycle
    cycle = deposit_cycle(profile)
    if profile.deposit_business_cycle != cycle or provider_transaction.deposit_business_cycle != cycle:
        raise ValueError('退款押金来源周期待核对')
    return RefundDepositDeficit.objects.create(refund=refund, profile=profile, account_id=profile.account_id,
        account_business_cycle=cycle,
        amount=amount, remaining=amount, source_provider_transaction=provider_transaction,
        source_store_transaction=store_transaction)


def _verify_direct_deficit(deficit, platform):
    tx = Transaction.objects.select_for_update().get(pk=deficit.source_store_transaction_id)
    source = deficit.addon_refund if deficit.addon_refund_id else deficit.refund
    key = (f'addon:{deficit.addon_refund_id}:{deficit.profile_id}' if deficit.addon_refund_id
           else f'{deficit.refund_id}:{deficit.profile_id}')
    if (deficit.source_kind != 'DIRECT' or deficit.source_provider_transaction_id is not None
            or deficit.direct_key != key
            or deficit.account_id != deficit.profile.account_id
            or not deficit.account_business_cycle or tx.deposit_owner_id != deficit.account_id
            or tx.deposit_business_cycle != deficit.account_business_cycle
            or not _valid(tx, kind='DEPOSIT_INCOME', amount=-deficit.amount,
                          wallet_id=platform.pk, order_id=source.order_id)
            or tx.reversal_of_id or tx.reversals.select_for_update().exists()):
        raise ValueError('退款押金直接扣款凭证异常')
    repayments = list(deficit.repayments.select_for_update().select_related('application__source_transaction',
        'provider_transaction__wallet', 'store_transaction'))
    if (sum(row.amount for row in repayments) + _cash_repaid(deficit, platform) + deficit.remaining != deficit.amount
            or bool(deficit.closed_at) != (deficit.remaining == 0)):
        raise ValueError('退款押金缺口余额与还款流水不一致')
    for row in repayments:
        source, provider, store = row.application.source_transaction, row.provider_transaction, row.store_transaction
        if (row.application.account_id != deficit.account_id or row.application.profile_id != deficit.profile_id
                or provider.deposit_owner_id != deficit.account_id or store.deposit_owner_id != deficit.account_id
                or provider.deposit_business_cycle != deficit.account_business_cycle
                or store.deposit_business_cycle != deficit.account_business_cycle
                or store.deposit_pair_id != provider.pk
                or provider.wallet.account_id != deficit.account_id or provider.wallet.user_id != deficit.profile.user_id
                or source.wallet_id != provider.wallet_id or source.status != Transaction.Status.SUCCESS
                or source.tx_type not in ('INCOME', 'GIFT', 'EARNINGS_RELEASE') or source.amount < row.amount
                or source.balance_after != source.balance_before + source.amount or source.reversal_of_id
                or source.reversals.select_for_update().exists()
                or row.application.available_amount != source.amount or row.application.applied_amount > source.amount
                or not _valid(provider, kind='DEPOSIT', amount=-row.amount, wallet_id=source.wallet_id, order_id=source.order_id)
                or not _valid(store, kind='DEPOSIT_INCOME', amount=row.amount, wallet_id=platform.pk, order_id=source.order_id)
                or provider.reversal_of_id or store.reversal_of_id or provider.reversals.select_for_update().exists()
                or store.reversals.select_for_update().exists()):
            raise ValueError('退款押金还款配对凭证异常')


def _verify_deposit_sources(profile, wallet, rows, platform):
    """Match each provider-side deposit to an identifiable store-side fact."""
    for tx in rows:
        if tx.amount < 0 and not tx.reversal_of_id:
            repayment = RefundDepositRepayment.objects.select_for_update().filter(provider_transaction=tx).select_related(
                'store_transaction', 'deficit').first()
            if repayment:
                counterpart = repayment.store_transaction
                valid = (repayment.deficit.profile_id == profile.pk and repayment.deficit.account_id == profile.account_id
                    and repayment.amount == -tx.amount
                    and _valid(counterpart, kind='DEPOSIT_INCOME', amount=-tx.amount,
                               wallet_id=platform.pk, order_id=tx.order_id)
                    and not counterpart.reversal_of_id)
            else:
                counterpart = Transaction.objects.select_for_update().filter(deposit_pair=tx).first()
                valid = bool(counterpart and _valid(counterpart, kind='DEPOSIT_INCOME', amount=-tx.amount,
                    wallet_id=platform.pk, order_id=tx.order_id) and not counterpart.reversal_of_id)
        elif tx.amount > 0 and tx.reversal_of_id:
            deficit = RefundDepositDeficit.objects.select_for_update().filter(source_kind='PAIRED', profile=profile,
                source_provider_transaction=tx).select_related('source_store_transaction', 'refund').first()
            counterpart = deficit.source_store_transaction if deficit else None
            provider_line = deficit.refund.recovery_lines.filter(source='DEPOSIT', reversal_transaction=tx,
                original_transaction_id=tx.reversal_of_id, wallet=wallet, amount=tx.amount).exists() if deficit else False
            store_line = deficit.refund.recovery_lines.filter(source='DEPOSIT', reversal_transaction=counterpart,
                original_transaction_id=getattr(counterpart, 'reversal_of_id', None), wallet=platform, amount=-tx.amount).exists() if deficit else False
            original_provider = Transaction.objects.select_for_update().filter(pk=tx.reversal_of_id).first()
            original_store = Transaction.objects.select_for_update().filter(pk=getattr(counterpart, 'reversal_of_id', None)).first()
            valid = bool(deficit and deficit.account_id == profile.account_id and deficit.amount == tx.amount
                and provider_line and store_line and counterpart.reversal_of_id
                and _valid(counterpart, kind='DEPOSIT_INCOME', amount=-tx.amount,
                           wallet_id=platform.pk, order_id=tx.order_id)
                and original_provider and original_store and original_provider.amount == -original_store.amount
                and _valid(original_provider, kind='DEPOSIT', amount=original_provider.amount,
                           wallet_id=wallet.pk, order_id=tx.order_id)
                and _valid(original_store, kind='DEPOSIT_INCOME', amount=original_store.amount,
                           wallet_id=platform.pk, order_id=tx.order_id)
                and original_provider.amount < 0 and tx.amount <= -original_provider.amount)
        elif tx.amount > 0 and not tx.order_id and not tx.reversal_of_id:
            counterpart = Transaction.objects.select_for_update().filter(deposit_pair=tx).first()
            valid = bool(counterpart and _valid(counterpart, kind='DEPOSIT_INCOME', amount=-tx.amount,
                wallet_id=platform.pk, order_id=None) and not counterpart.reversal_of_id
                and tx.remark.startswith('退还押金：') and counterpart.remark.startswith('退还押金：'))
        else:
            valid = False
        if (not valid or tx.wallet_id != wallet.pk or tx.balance_after != tx.balance_before + tx.amount
                or ((tx.amount < 0 or not tx.order_id) and (tx.deposit_owner_id != profile.account_id
                    or tx.deposit_business_cycle != profile.deposit_business_cycle
                    or not counterpart or counterpart.deposit_owner_id != profile.account_id
                    or counterpart.deposit_business_cycle != profile.deposit_business_cycle
                    or counterpart.deposit_pair_id != tx.pk))):
            raise ValueError('陪玩押金流水缺少可核对的门店配对凭证')


def charge_refund_deposit(*, refund, profile, amount, operator, operator_account):
    """Post a refund-funded deposit deficit without crediting the provider wallet.

    The caller must hold the order/money locks, have locked the provider
    profile and execute the customer refund in this same outer transaction.
    It must also verify the accepted proposal and this provider's exact
    burden; the refund amount alone does not establish either. This internal
    primitive is not an authorization or payout endpoint.
    """
    _atomic()
    # A caller may catch a failed debit while remaining in its outer workflow;
    # the savepoint still rolls back every change made by this primitive.
    with transaction.atomic():
        return _charge_refund_deposit(refund=refund, profile=profile, amount=amount,
                                      operator=operator, operator_account=operator_account)


def _charge_refund_deposit(*, refund, profile, amount, operator, operator_account):
    if not enabled():
        raise ValueError('退款押金缺口功能未启用')
    from orders.refunds import authorized_refund
    from orders.models import Order, OrderRefund, OrderAddonRefund
    from users.models import EscortProfile
    from .models import CashDepositTopup, Wallet
    if (not authorized_refund(operator_account) or not operator_account.is_active or not operator_account.can_login
            or not refund.pk
            or not profile.pk or not profile.account_id or type(amount) is not int or amount <= 0):
        raise ValueError('退款押金操作人或金额无效')
    # Keep the same order -> refund -> profile lock order as the refund
    # coordinator, including when a points refund has no coin transaction.
    order = Order.objects.select_for_update().get(pk=refund.order_id)
    from orders.financial_lock import lock_order_money
    lock_order_money()
    addon = isinstance(refund, OrderAddonRefund)
    if addon:
        from orders.ordinary_addon_production import eligible as ordinary_addon_eligible
        production_addon = ordinary_addon_eligible(order, allow_open=True)
        from orders.ordinary_refunded_team_handover import is_posting_refund
        production_addon = production_addon or is_posting_refund(refund)
        from orders.points_business_access import permits
        points_addon = permits(order, phase='refund', kind='addon')
        if not (getattr(settings, 'ADDON_REFUND_EXECUTION_INTERNAL_TESTING', False) or production_addon or points_addon):
            raise ValueError('加购退款押金扣款尚未开放')
        refund = OrderAddonRefund.objects.select_for_update().select_related('proposal').get(pk=refund.pk, order=order)
        participant = refund.proposal.participants.filter(account_id=profile.account_id).first()
        if (refund.proposal.status != 'ACCEPTED' or refund.proposal.payment_id != refund.payment_id
                or not participant or participant.deposit_deduction != amount):
            raise ValueError('加购退款协议或押金承担不一致')
    else:
        refund = OrderRefund.objects.select_for_update().get(pk=refund.pk, order=order)
    # An add-on participant's independently rounded burden can exceed the
    # buyer's refund. Its accepted participant record is the actual limit.
    allowed_amount = participant.burden if addon else refund.amount
    from orders.points_service_payment import needs_integrated_processing
    points_refund = not addon and needs_integrated_processing(order)
    if points_refund:
        from rest_framework.exceptions import ValidationError as RestValidationError
        from orders.points_checkout_test import require_isolated_checkout
        from orders.points_service_refund import verify_refund_history_locked
        from orders.points_business_access import permits
        formal = (permits(order, phase='refund', kind='service') or permits(order, phase='refund', kind='addon')
                  or permits(order, phase='refund', kind='transfer'))
        if not formal and not getattr(settings, 'SERVICE_POINTS_REFUND_DEPOSIT_TESTING', False):
            raise ValueError('积分退款的押金扣款尚未开放')
        try:
            if not formal:
                require_isolated_checkout()
            proof = verify_refund_history_locked(refund.pk)
            if formal:
                from orders.models import PointsNegotiationPosting
                from orders.negotiation_proposals import _verified, _decisions
                posting = PointsNegotiationPosting.objects.select_for_update().select_related(
                    'proposal__case').filter(points_refund=proof).first()
                if not posting or posting.proposal.status != 'ACCEPTED':
                    raise ValueError('积分退款协商凭证缺失')
                _verified(posting.proposal.case, posting.proposal)
                _decisions(posting.proposal.case, posting.proposal, lock=True)
                terms = [row for row in posting.proposal.provider_terms if row.get('account_id') == profile.account_id]
                if len(terms) != 1 or amount > terms[0].get('additional_units', 0):
                    raise ValueError('积分退款协商押金承担不一致')
        except RestValidationError as exc:
            raise ValueError('积分退款凭证不完整，请先核对') from exc
        allowed_amount = proof.equivalent_units
    refund_account_id = refund.operator_id if addon else refund.operator_account_id
    refund_legacy_id = refund.legacy_operator_id if addon else refund.operator_id
    if (refund_account_id != operator_account.pk or refund_legacy_id != getattr(operator, 'pk', None)
            or amount > allowed_amount):
        raise ValueError('退款押金金额或经办人不一致')
    profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
    if points_refund and not order.providers.filter(provider_id=profile.user_id,
                                                    provider_account_id=profile.account_id).exists():
        raise ValueError('积分退款陪玩不属于该订单')
    key = f'addon:{refund.pk}:{profile.pk}' if addon else f'{refund.pk}:{profile.pk}'
    existing = RefundDepositDeficit.objects.select_for_update().filter(direct_key=key).first()
    if existing:
        platform = get_platform_wallet(for_update=True)
        _verify_direct_deficit(existing, platform)
        if ((existing.addon_refund_id if addon else existing.refund_id) != refund.pk or existing.profile_id != profile.pk
                or existing.account_id != profile.account_id or existing.amount != amount):
            raise ValueError('退款押金操作凭证与原记录不一致')
        return existing
    from .deposit_cycle import deposit_cycle
    cycle = deposit_cycle(profile)
    if profile.deposit_business_cycle != cycle:
        raise ValueError('退款押金来源周期待核对')
    # A newly registered provider may have a user-addressed empty wallet
    # before its club account is linked. Resolve both identities even when no
    # available balance is deducted, so deposit-only refunds work as well.
    from .services import get_wallet, WalletAddressingError
    try:
        wallet = get_wallet(account_id=profile.account_id, user_id=profile.user_id,
                            for_update=True, create=False)
    except WalletAddressingError as exc:
        raise ValueError('陪玩押金钱包归属不一致') from exc
    wallets = [wallet] if wallet else []
    platform = get_platform_wallet(for_update=True)
    topups = list(CashDepositTopup.objects.select_for_update().filter(profile=profile).order_by('pk'))
    for topup in topups:
        source = Transaction.objects.select_for_update().get(pk=topup.platform_transaction_id)
        if (topup.account_id != profile.account_id or topup.account_business_cycle != cycle
                or topup.paid_after != topup.paid_before + topup.amount
                or not _valid(source, kind='DEPOSIT_INCOME', amount=topup.amount, wallet_id=platform.pk, order_id=None)
                or source.reversal_of_id or source.reversals.select_for_update().exists()):
            raise ValueError('线下现金押金凭证异常')
    deposit_txs = list(Transaction.objects.select_for_update().filter(
        wallet_id__in=[wallet.pk for wallet in wallets], tx_type='DEPOSIT', status='SUCCESS').order_by('pk'))
    if deposit_txs:
        _verify_deposit_sources(profile, wallets[0], deposit_txs, platform)
    previous = list(RefundDepositDeficit.objects.select_for_update().filter(
        profile=profile, source_kind='DIRECT').order_by('pk'))
    for deficit in previous:
        _verify_direct_deficit(deficit, platform)
    evidenced = sum(row.amount for row in topups) - sum(tx.amount for tx in deposit_txs) - sum(row.amount for row in previous)
    if evidenced != profile.deposit_paid:
        raise ValueError('陪玩已缴押金与历史凭证不一致，请先核对')
    pool = sum(Transaction.objects.select_for_update().filter(
        wallet=platform, tx_type='DEPOSIT_INCOME', status='SUCCESS').values_list('amount', flat=True))
    if (amount > profile.deposit_paid or amount > pool or platform.balance < pool):
        raise ValueError('陪玩押金或门店押金资金不足')
    before = platform.balance
    platform.balance -= amount
    platform.save(update_fields=['balance'])
    profile.deposit_paid -= amount
    profile.save(update_fields=['deposit_paid'])
    store_tx = Transaction.objects.create(wallet=platform, order_id=refund.order_id, amount=-amount,
        deposit_owner_id=profile.account_id, deposit_business_cycle=cycle,
        tx_type='DEPOSIT_INCOME', balance_before=before, balance_after=platform.balance,
        operator=operator, operator_account=operator_account, remark=f'退款 {refund.pk} 直接扣陪玩押金')
    return RefundDepositDeficit.objects.create(**({'addon_refund': refund} if addon else {'refund': refund}),
        profile=profile, account_id=profile.account_id, account_business_cycle=cycle,
        amount=amount, remaining=amount, source_store_transaction=store_tx, source_kind='DIRECT', direct_key=key)


def _verify_application(application, *, source, profile):
    if (application.profile_id != profile.pk or application.account_id != profile.account_id
            or application.available_amount != source.amount):
        raise ValueError('收益补押金记录与原流水不一致')
    rows = list(application.repayments.select_related('provider_transaction', 'store_transaction', 'deficit'))
    if sum(row.amount for row in rows) != application.applied_amount:
        raise ValueError('收益补押金汇总与明细不一致')
    platform = get_platform_wallet(for_update=True) if rows else None
    for row in rows:
        if row.deficit.source_kind == 'DIRECT':
            _verify_direct_deficit(row.deficit, platform)
        if (row.deficit.account_id != profile.account_id
                or not row.deficit.account_business_cycle
                or row.provider_transaction.deposit_owner_id != profile.account_id
                or row.store_transaction.deposit_owner_id != profile.account_id
                or row.provider_transaction.deposit_business_cycle != row.deficit.account_business_cycle
                or row.store_transaction.deposit_business_cycle != row.deficit.account_business_cycle
                or row.store_transaction.deposit_pair_id != row.provider_transaction_id
                or not _valid(row.provider_transaction, kind='DEPOSIT', amount=-row.amount,
                              wallet_id=source.wallet_id, order_id=source.order_id)
                or not _valid(row.store_transaction, kind='DEPOSIT_INCOME', amount=row.amount,
                              wallet_id=platform.pk, order_id=source.order_id)
                or row.provider_transaction.reversals.exists() or row.store_transaction.reversals.exists()):
            raise ValueError('收益补押金配对流水异常')
        paid = sum(row.deficit.repayments.select_for_update().values_list('amount', flat=True)) + _cash_repaid(row.deficit, platform)
        if (paid + row.deficit.remaining != row.deficit.amount
                or bool(row.deficit.closed_at) != (row.deficit.remaining == 0)):
            raise ValueError('退款押金缺口余额与还款流水不一致')
    return application.applied_amount


def apply_released_income(*, source, profile, wallet, hold=None, now=None):
    """Apply this freshly released service/gift income to oldest deficits first."""
    _atomic()
    if not enabled():
        return 0
    if (not profile or not profile.account_id or wallet.pk != source.wallet_id
            or wallet.account_id != profile.account_id or wallet.user_id != profile.user_id
            or source.status != Transaction.Status.SUCCESS or source.amount <= 0
            or source.tx_type not in ('INCOME', 'GIFT', 'EARNINGS_RELEASE')
            or source.balance_after != source.balance_before + source.amount or source.reversal_of_id
            or source.reversals.exists()):
        raise ValueError('可释放收益与陪玩钱包不匹配')
    if source.tx_type == 'INCOME' and not source.remark.startswith('订单收入：'):
        raise ValueError('非服务订单收入不能自动补退款押金')
    if source.tx_type == 'INCOME' and (not source.order_id or source.order.is_gift_snapshot
            or source.order.status != source.order.Status.COMPLETED):
        raise ValueError('服务收益尚未满足整单完成条件')
    if source.tx_type == 'GIFT':
        gift = source.order if source.order_id else None
        if (not gift or not gift.is_gift_snapshot or gift.gift_aftersale_hours is None or gift.gift_freeze_hours is None
                or max(gift.gift_aftersale_hours, gift.gift_freeze_hours) != 0
                or gift.aftersales.filter(status='OPEN').exists()):
            raise ValueError('礼物收益尚未满足释放条件')
    if source.tx_type == 'EARNINGS_RELEASE':
        now = now or timezone.now()
        if (not source.order_id or not hold or hold.wallet_id != wallet.pk or hold.income_transaction.order_id != source.order_id
                or hold.released_at or hold.cancelled_at or hold.release_at > now
                or source.amount != hold.amount - hold.recovered_amount):
            raise ValueError('解冻收益或释放时间不一致')
    prior = RefundDepositIncomeApplication.objects.select_for_update().filter(source_transaction=source).first()
    if prior:
        return _verify_application(prior, source=source, profile=profile)
    if wallet.balance < source.amount:
        raise ValueError('可释放收益未完整进入钱包')
    deficits = list(RefundDepositDeficit.objects.select_for_update().filter(
        profile=profile, account_id=profile.account_id, remaining__gt=0).order_by('created_at', 'pk'))
    if deficits:
        from .deposit_cycle import deposit_cycle
        cycle = deposit_cycle(profile, increase=True)
        if any(row.account_business_cycle != cycle for row in deficits):
            raise ValueError('退款押金补差账号周期待核对')
    application = RefundDepositIncomeApplication.objects.create(source_transaction=source, profile=profile,
        account_id=profile.account_id, available_amount=source.amount)
    remaining = source.amount
    platform = get_platform_wallet(for_update=True) if deficits else None
    for deficit in deficits:
        if not remaining:
            break
        if deficit.source_kind == 'DIRECT':
            _verify_direct_deficit(deficit, platform)
        paid = sum(deficit.repayments.select_for_update().values_list('amount', flat=True)) + _cash_repaid(deficit, platform)
        if paid + deficit.remaining != deficit.amount or deficit.closed_at:
            raise ValueError('退款押金缺口历史记录不一致')
        amount = min(remaining, deficit.remaining)
        if platform.balance + amount > MAX_LEDGER_AMOUNT or profile.deposit_paid + amount > MAX_LEDGER_AMOUNT:
            raise ValueError('押金余额超出支持范围')
        before = wallet.balance; wallet.balance -= amount; wallet.save(update_fields=['balance'])
        provider_tx = Transaction.objects.create(wallet=wallet, order_id=source.order_id, amount=-amount,
            deposit_owner_id=profile.account_id, deposit_business_cycle=cycle,
            tx_type=Transaction.TxType.DEPOSIT, balance_before=before, balance_after=wallet.balance,
            remark='可释放收益优先补退款押金缺口')
        store_before = platform.balance; platform.balance += amount; platform.save(update_fields=['balance'])
        store_tx = Transaction.objects.create(wallet=platform, order_id=source.order_id, amount=amount,
            deposit_pair=provider_tx,
            deposit_owner_id=profile.account_id, deposit_business_cycle=cycle,
            tx_type=Transaction.TxType.DEPOSIT_INCOME, balance_before=store_before, balance_after=platform.balance,
            remark='可释放收益补退款押金缺口')
        profile.deposit_paid += amount; profile.save(update_fields=['deposit_paid', 'deposit_business_cycle'])
        deficit.remaining -= amount
        if not deficit.remaining:
            deficit.closed_at = timezone.now()
        deficit.save(update_fields=['remaining', 'closed_at'])
        RefundDepositRepayment.objects.create(application=application, deficit=deficit, amount=amount,
            provider_transaction=provider_tx, store_transaction=store_tx)
        remaining -= amount
    application.applied_amount = source.amount - remaining
    application.save(update_fields=['applied_amount'])
    from .refund_recovery_order import close_for_income
    close_for_income(profile, application)
    return application.applied_amount
