"""Recover the customer's exact points-order after-sale refund notice."""
from django.conf import settings
from django.db.models import F
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'POINTS_AFTERSALE_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def _decimal(units, scale):
    whole, fraction = divmod(units, scale)
    return str(whole) if not fraction else f'{whole}.{fraction:0{len(str(scale)) - 1}d}'.rstrip('0')


def prepare(order, case):
    from orders.models import PointsNegotiationPosting
    if (case.status not in ('REFUNDED', 'PARTIALLY_REFUNDED') or not case.resolved_at
            or not isinstance(case.history, list) or not case.history):
        return None
    entry = case.history[-1]
    if (not isinstance(entry, dict) or entry.get('status') != case.status
            or entry.get('resolved_at') != case.resolved_at.isoformat()):
        return None
    matches = list(PointsNegotiationPosting.objects.filter(proposal__case=case,
        proposal__case_opened_at=case.created_at, proposal__case_request_id=case.client_request_id,
        proposal__status='ACCEPTED', termination_processed_at__isnull=False,
        points_refund__refund__order=order, points_refund__redemption__order=order)
        .select_related('points_refund', 'proposal').order_by('pk')[:2])
    return matches[0] if len(matches) == 1 else None


def record(posting, *, order, cycle, legacy_user_id):
    from stores.context import current_currency_name
    from .aftersale_closure_notice import _fingerprint
    from .models import PointsAftersaleRefundNotice
    case = posting.proposal.case
    proof = posting.points_refund
    parts = []
    if proof.coin_units:
        parts.append(f'{_decimal(proof.coin_units, 10)} {current_currency_name()}至余额')
    if proof.points_hundredths:
        parts.append(f'{_decimal(proof.points_hundredths, 100)} 积分')
    preview = '售后退款已处理，退回' + '、'.join(parts) + '。' if parts else '售后退款已处理，请查看订单。'
    expected = {'account_id': order.customer_account_id, 'business_cycle': cycle, 'legacy_user_id': legacy_user_id,
                'coin_units': proof.coin_units, 'points_hundredths': proof.points_hundredths,
                'case_history_index': len(case.history) - 1, 'case_history_fingerprint': _fingerprint(case.history[-1]),
                'preview': preview[:255]}
    intent, created = PointsAftersaleRefundNotice.objects.get_or_create(posting=posting, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('积分售后退款通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from engagement.models import PointLedger
    from orders.models import AfterSale, Order, PointsNegotiationPosting
    from wallet.models import Transaction
    from .aftersale_closure_notice import _fingerprint
    from .models import Message, PointsAftersaleRefundNotice
    from .notifier import notify_messages_after_commit

    initial = PointsAftersaleRefundNotice.objects.filter(pk=intent_id).values('account_id', 'posting_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        source = PointsNegotiationPosting.objects.filter(pk=initial['posting_id']).values(
            'proposal__case_id', 'proposal__case__order_id').first()
        order = Order.objects.select_for_update().filter(pk=source['proposal__case__order_id']).first() if source else None
        case = AfterSale.objects.select_for_update().filter(pk=source['proposal__case_id'], order=order).first() if order else None
        posting = PointsNegotiationPosting.objects.select_for_update().filter(pk=initial['posting_id'],
            proposal__case=case).first() if case else None
        intent = PointsAftersaleRefundNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], posting_id=initial['posting_id']).first()
        if not intent or intent.completed_at:
            return
        entry = (case.history[intent.case_history_index] if case and isinstance(case.history, list)
                 and intent.case_history_index < len(case.history) else None)
        proof = posting.points_refund if posting else None
        proposal = posting.proposal if posting else None
        refund = proof.refund if proof else None
        redemption = proof.redemption if proof else None
        cycle = intent.business_cycle
        wallet_valid = bool(proof and redemption and order and (
            (proof.coin_units == 0 and proof.coin_transaction_id is None) or
            (proof.coin_units > 0 and Transaction.objects.filter(pk=proof.coin_transaction_id, order=order,
                wallet_id=redemption.payment.wallet_id, wallet__account_id=order.customer_account_id,
                tx_type='REFUND', status='SUCCESS', amount=proof.coin_units, reversal_of__isnull=True,
                reversals__isnull=True, balance_after=F('balance_before') + F('amount')).exists())))
        points_valid = bool(proof and order and ((proof.points_hundredths == 0 and proof.points_ledger_id is None) or
            (proof.points_hundredths > 0 and PointLedger.objects.filter(pk=proof.points_ledger_id, order=order,
                account_id=order.customer_account_id, kind='ORDER_POINTS_RETURN', amount=proof.points_hundredths,
                event_key=f'order:{order.pk}:points-return:{proof.refund_id}').exists())))
        payment_valid = bool(redemption and order and redemption.order_id == order.pk
            and redemption.payment.order_id == order.pk and redemption.payment.wallet.account_id == order.customer_account_id
            and redemption.payment.wallet.user_id == order.customer_id and redemption.payment.tx_type == 'PAY'
            and redemption.payment.status == 'SUCCESS' and redemption.payment.amount == -redemption.paid_units
            and redemption.payment.balance_after == redemption.payment.balance_before + redemption.payment.amount
            and not redemption.payment.reversal_of_id and not redemption.payment.reversals.exists()
            and redemption.ledger.order_id == order.pk and redemption.ledger.account_id == order.customer_account_id
            and redemption.ledger.kind == 'ORDER_POINTS_REDEEM' and redemption.ledger.amount == -redemption.points_hundredths
            and redemption.ledger.event_key == f'order:{order.pk}:points-redemption')
        event_valid = bool(case and proposal and posting and posting.termination_processed_at and proposal.status == 'ACCEPTED'
            and proposal.case_opened_at == case.created_at and proposal.case_request_id == case.client_request_id
            and isinstance(entry, dict) and intent.case_history_index == len(case.history) - 1
            and _fingerprint(entry) == intent.case_history_fingerprint and entry.get('status') == case.status
            and case.resolved_at and entry.get('resolved_at') == case.resolved_at.isoformat()
            and case.status in ('REFUNDED', 'PARTIALLY_REFUNDED'))
        valid = bool(event_valid and proof and refund and proof.coin_units == intent.coin_units
            and proof.points_hundredths == intent.points_hundredths
            and refund.order_id == order.pk and refund.amount == proof.coin_units
            and proof.equivalent_units == proposal.refund_units and proof.is_final == (case.status == 'REFUNDED')
            and proof.coin_units + proof.points_hundredths > 0 and wallet_valid and points_valid and payment_valid
            and account and account.account_type == 'BOSS' and account.license_membership_active
            and intent.account_id == order.customer_account_id and intent.legacy_user_id == order.customer_id
            and type(cycle) is int and cycle > 0 and account.business_cycle == cycle and order.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '售后退款或原账号周期无法核验'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title='售后退款已处理', preview=intent.preview,
                related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
