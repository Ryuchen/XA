"""Identify paid referral credits without confusing them with service income."""
from wallet.notifier import owner_account_id


REFERRAL_REMARK_PREFIXES = ('推荐分佣：', '礼物推荐分佣：')


class ReferralEvidenceError(ValueError):
    """An original credit cannot be safely attributed to the recorded inviter."""


def record_referral_principal(tx):
    """Give a boss's newly posted referral credit its spendable principal source."""
    if not tx or not tx.wallet.account_id:
        return
    from club_accounts.models import ClubAccount
    if not ClubAccount.objects.filter(pk=tx.wallet.account_id, account_type=ClubAccount.AccountType.BOSS).exists():
        return
    from engagement.models import PointFundingLot
    PointFundingLot.objects.create(account_id=tx.wallet.account_id, source_key=f'referral-income:{tx.pk}',
                                   kind=PointFundingLot.Kind.PREPAID, remaining=tx.amount)


def paid_referral_credits(order, original_credits, *, expected_amount=None):
    """Return explicitly labelled, successful credits belonging to the inviter.

    The caller supplies the original positive order credits. An account ID on
    the order takes precedence over a legacy user ID, including when a legacy
    wallet's account mapping has since changed. Provider and inviter can share
    one wallet, so wallet ownership alone is never enough to label a credit.
    """
    labelled = [tx for tx in original_credits if tx.remark.startswith(REFERRAL_REMARK_PREFIXES)]
    for tx in labelled:
        if (tx.order_id != order.pk or tx.status != 'SUCCESS' or tx.tx_type not in ('INCOME', 'GIFT')
                or tx.amount <= 0 or tx.reversal_of_id):
            raise ReferralEvidenceError('推荐佣金原始流水类型或订单不符')
        if order.inviter_account_id:
            matches = owner_account_id(tx.wallet) == order.inviter_account_id
        else:
            matches = bool(order.inviter_id and tx.wallet.user_id == order.inviter_id)
        if not matches:
            raise ReferralEvidenceError('推荐佣金收款人与订单记录不一致')
    if expected_amount is not None and sum(tx.amount for tx in labelled) != expected_amount:
        raise ReferralEvidenceError('推荐佣金原始流水不完整，请人工核对')
    return labelled
