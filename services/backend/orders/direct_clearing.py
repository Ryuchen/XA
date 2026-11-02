"""Simulate a fully completed direct order in separate balanced test books.

This isolated domain does not settle an order, credit a wallet, fund a
withdrawal, award points or confirm VIP consumption. A verified payment is a
channel receivable, not proof that money has reached the merchant's bank.
Promotions, refunds, unresolved aftersales and transfer cases remain intentionally
unsupported until their complete source-of-funds rules exist.
Provider eligibility uses completion plus 72 hours and approved reports.
The independent VIP deadline is recorded but does not delay this simulation;
compare it with the batch creation time to see whether it was mature then.
For this test domain only, channel and local clocks may differ by at most
five minutes; live deployment needs an independently validated clock policy.
"""
import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from club_accounts.models import ClubAccount, LegacyAccountMap
from engagement.models import ConsumptionConfirmation, PointLedger
from wallet.models import ProviderReport

from .direct_clearing_models import DirectClearingBatch, DirectClearingLine
from .direct_payment import _require_test_domain
from .direct_payment_models import DirectPaymentIntent
from .direct_payment_quote import quote_matches_intent
from .direct_payment_quote_models import DirectPaymentQuote
from .direct_payment_session_models import DirectPaymentSession
from .models import Order, OrderProvider
from .settlement import compute_provider_income


class DirectClearingError(ValueError):
    pass


TEST_CHANNEL_CLOCK_SKEW = timedelta(minutes=5)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _line(key, book, side, account_code, amount, *, user_id=None, account_id=None):
    if type(amount) is not int or amount < 0:
        raise DirectClearingError('模拟分录金额无效')
    if not amount:
        return None
    return {'line_key': key, 'book': book, 'side': side, 'account_code': account_code,
            'recipient_user_id': user_id, 'recipient_account_id': account_id, 'amount': amount}


def _balanced(lines):
    for book in ('FEN', 'UNIT'):
        debits = sum(item['amount'] for item in lines if item['book'] == book and item['side'] == 'DEBIT')
        credits = sum(item['amount'] for item in lines if item['book'] == book and item['side'] == 'CREDIT')
        if debits <= 0 or debits != credits:
            raise DirectClearingError('模拟账簿借贷不平')


def _read_source(order, intent, quote, session, *, now):
    if (order.status != Order.Status.COMPLETED or order.payment_status != Order.PaymentStatus.PAID
            or order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL
            or order.completed_at is None or timezone.is_naive(order.completed_at)
            or not order.pool_entered_at or not order.grabbed_at or not order.in_service_at
            or timezone.is_naive(order.pool_entered_at) or timezone.is_naive(order.grabbed_at)
            or timezone.is_naive(order.in_service_at)
            or not order.created_at <= order.pool_entered_at <= order.grabbed_at <= order.in_service_at <= order.completed_at
            or order.completed_at + timedelta(hours=72) > now or order.settled_at
            or order.consumption_confirmed_at or order.source_order_id or order.is_gift_snapshot):
        raise DirectClearingError('仅支持已完成、过售后期且未结算的普通审核单')
    if (intent.status != DirectPaymentIntent.Status.PAID or not intent.transaction_id
            or intent.transaction_id != intent.transaction_id.strip() or not intent.paid_at
            or timezone.is_naive(intent.paid_at)
            or intent.paid_at < order.created_at - TEST_CHANNEL_CLOCK_SKEW
            or intent.paid_at > order.pool_entered_at + TEST_CHANNEL_CLOCK_SKEW
            or intent.paid_at > now + TEST_CHANNEL_CLOCK_SKEW
            or intent.evidence_risk_at or intent.evidence_issue
            or intent.account_id != order.customer_account_id or intent.currency != 'CNY'
            or not intent.openid or not intent.merchant_order_no or not intent.appid or not intent.mchid
            or intent.payer_currency != 'CNY' or intent.payer_total_fen != intent.amount_fen
            or intent.promotion_detail is not None or not quote_matches_intent(intent, order)):
        raise DirectClearingError('付款证据或报价尚未满足模拟清算条件')
    if session and (session.cancel_requested_at or session.fake_closed_seen_at or session.fake_payment_risk_at):
        raise DirectClearingError('支付会话存在取消或渠道证据风险')
    if session:
        from .direct_payment_session import _snapshot_matches
        if not _snapshot_matches(session, intent, order):
            raise DirectClearingError('支付会话快照不一致')
    if (intent.channel_refunds.exists() or order.refund_records.exists()
            or order.transactions.exists() or PointLedger.objects.filter(order=order).exists()
            or ConsumptionConfirmation.objects.filter(order=order).exists()
            or intent.payment_inboxes.filter(has_conflict=True).exists()
            or intent.payment_inboxes.filter(status='REVIEW').exists()):
        raise DirectClearingError('订单存在退款、售后、钱包或权益记录，需人工核对')
    aftersales = list(order.aftersales.select_for_update().order_by('pk'))
    aftersale_snapshot = []
    for ticket in aftersales:
        history = ticket.history
        if (ticket.status != 'RESOLVED' or ticket.account_id != order.customer_account_id
                or not ticket.operator_id or not ticket.resolved_at or not ticket.created_at
                or timezone.is_naive(ticket.resolved_at) or timezone.is_naive(ticket.created_at)
                or not order.completed_at <= ticket.created_at <= ticket.resolved_at <= now
                or len((ticket.resolution or '').strip()) < 2
                or not isinstance(history, list) or not history
                or any(not isinstance(item, dict) or item.get('status') not in ('OPEN', 'RESOLVED') for item in history)
                or history[-1].get('status') != 'RESOLVED'
                or history[-1].get('operator_id') != ticket.operator_id
                or history[-1].get('resolved_at') != ticket.resolved_at.isoformat()
                or history[-1].get('resolution') != ticket.resolution
                or not ClubAccount.objects.filter(pk=ticket.operator_id, account_type=ClubAccount.AccountType.STAFF).exists()):
            raise DirectClearingError('售后决议缺少可核对的无退款证据')
        aftersale_snapshot.append({'id': ticket.pk, 'status': ticket.status, 'account_id': ticket.account_id,
                                   'operator_id': ticket.operator_id, 'created_at': ticket.created_at.isoformat(),
                                   'resolved_at': ticket.resolved_at.isoformat()})
    if (order.experience_kind or order.boss_discount or order.promo_discount or order.coupon_discount
            or order.original_amount != order.amount):
        raise DirectClearingError('优惠或体验订单的模拟清算尚未支持')
    if (quote.service_units != order.amount or quote.total_fen != intent.amount_fen
            or quote.total_fen != quote.service_fen + quote.fee_fen):
        raise DirectClearingError('服务成交额与付款报价不一致')
    account = ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id).first()
    if (not account or account.account_type != ClubAccount.AccountType.BOSS
            or account.business_cycle != order.business_cycle):
        raise DirectClearingError('老板业务周期或身份不一致')
    if not LegacyAccountMap.objects.filter(account_id=account.pk, legacy_user_id=order.customer_id).exists():
        raise DirectClearingError('老板身份映射不一致')
    all_rows = list(OrderProvider.objects.select_for_update().filter(order=order).order_by('pk'))
    # Rejection before service keeps a zero-valued released row for audit.
    # Any released row retaining value could be a transfer and is unsupported.
    released = [row for row in all_rows if row.released_at]
    if any(row.settlement_base or row.provider_income or row.commission_fixed or row.settled_at
           or timezone.is_naive(row.released_at) or not order.created_at <= row.released_at <= order.in_service_at
           for row in released):
        raise DirectClearingError('包含已转交或已结算的陪玩份额')
    rows = [row for row in all_rows if not row.released_at]
    expected = order.required_provider_count
    if (expected not in (1, 2) or len(rows) != expected
            or order.escort_mode != (Order.EscortMode.SINGLE if expected == 1 else Order.EscortMode.DOUBLE)
            or any(not row.provider_id or not row.provider_account_id or row.settled_at for row in rows)
            or len({row.provider_id for row in rows}) != expected
            or len({row.provider_account_id for row in rows}) != expected
            or not any(row.provider_id == order.provider_id and row.provider_account_id == order.provider_account_id for row in rows)):
        raise DirectClearingError('陪玩成员快照不完整或包含转单')
    mappings = {row.legacy_user_id: row.account_id for row in LegacyAccountMap.objects.filter(
        legacy_user_id__in=[row.provider_id for row in rows])}
    provider_types = dict(ClubAccount.objects.filter(pk__in=[row.provider_account_id for row in rows])
                          .values_list('pk', 'account_type'))
    if any(mappings.get(row.provider_id) != row.provider_account_id
           or provider_types.get(row.provider_account_id) != ClubAccount.AccountType.PROVIDER for row in rows):
        raise DirectClearingError('陪玩账号映射不一致')
    if (sum(row.settlement_base for row in rows) != order.amount
            or sum(row.provider_income for row in rows) != order.provider_income
            or order.provider_income + order.inviter_commission + order.shop_income != order.amount
            or any(row.commission_type not in ('PERCENT', 'FIXED')
                   or not 0 <= row.commission_rate <= 100
                   or row.commission_fixed < 0 or row.commission_fixed > row.settlement_base
                   or (row.commission_type == 'PERCENT' and row.commission_fixed != 0)
                   or row.provider_income > row.settlement_base
                   or row.provider_income != compute_provider_income(row.settlement_base,
                       row.commission_type, row.commission_rate, row.commission_fixed) for row in rows)):
        raise DirectClearingError('陪玩份额与订单金额不守恒')
    if order.inviter_commission:
        if not order.inviter_id or not order.inviter_account_id or not LegacyAccountMap.objects.filter(
                legacy_user_id=order.inviter_id, account_id=order.inviter_account_id).exists():
            raise DirectClearingError('推荐人账号映射不一致')
    elif not order.inviter_id and order.inviter_account_id:
        raise DirectClearingError('推荐人快照不完整')
    platform_cut = order.amount - order.provider_income
    if order.inviter_commission != (platform_cut * order.inviter_commission_rate_snapshot // 100 if order.inviter_id else 0):
        raise DirectClearingError('推荐人分成与冻结比例不一致')
    reports = {report.provider_id: report for report in ProviderReport.objects.select_for_update()
               .filter(order=order).order_by('pk')}
    if len(reports) != len(rows):
        raise DirectClearingError('每名陪玩都需要一份已审核报单')
    members = []
    for row in rows:
        report = reports.get(row.provider_id)
        result_images = (list(report.result_images.order_by('pk').values_list('pk', 'image')) if report else [])
        if (not report or report.provider_account_id != row.provider_account_id
                or report.status != ProviderReport.Status.APPROVED or not report.submitted_at or not report.audited_at
                or timezone.is_naive(report.submitted_at) or timezone.is_naive(report.audited_at)
                or not order.completed_at <= report.submitted_at <= report.audited_at <= now
                or not report.auditor_id or not report.auditor_account_id
                or not report.entry_image or not report.completion_image or not result_images
                or any(not name for _, name in result_images)
                or report.amount != row.settlement_base or report.payout_amount != row.provider_income
                or report.commission_rate != row.commission_rate):
            raise DirectClearingError('陪玩报单、审核或凭证与成员份额不一致')
        if (not LegacyAccountMap.objects.filter(legacy_user_id=report.auditor_id,
                                                account_id=report.auditor_account_id).exists()
                or not ClubAccount.objects.filter(pk=report.auditor_account_id,
                                                   account_type=ClubAccount.AccountType.STAFF).exists()):
            raise DirectClearingError('报单审核人员身份不一致')
        members.append({'assignment_id': row.pk, 'user_id': row.provider_id, 'account_id': row.provider_account_id,
                        'base': row.settlement_base, 'income': row.provider_income, 'commission_type': row.commission_type,
                        'commission_rate': row.commission_rate, 'commission_fixed': row.commission_fixed,
                        'report_id': report.pk, 'auditor_id': report.auditor_id,
                        'auditor_account_id': report.auditor_account_id,
                        'submitted_at': report.submitted_at.isoformat(), 'audited_at': report.audited_at.isoformat(),
                        'entry_image': report.entry_image.name, 'completion_image': report.completion_image.name,
                        'result_images': [{'id': pk, 'name': name} for pk, name in result_images]})
    last_submitted = max(report.submitted_at for report in reports.values())
    if (order.report_ready_at != last_submitted
            or order.consumption_ready_at != last_submitted + timedelta(hours=72)):
        raise DirectClearingError('消费确认时钟与报单不一致')
    released_snapshot = [{'assignment_id': row.pk, 'user_id': row.provider_id,
                          'account_id': row.provider_account_id, 'base': row.settlement_base,
                          'income': row.provider_income, 'commission_fixed': row.commission_fixed,
                          'released_at': row.released_at.isoformat()} for row in released]
    return members, released_snapshot, aftersale_snapshot


def _build_lines(order, quote, members):
    lines = [
        _line('fen:channel-receivable', 'FEN', 'DEBIT', 'CHANNEL_RECEIVABLE', quote.total_fen),
        _line('fen:service-pending', 'FEN', 'CREDIT', 'SERVICE_PENDING', quote.service_fen),
        _line('fen:fee-held', 'FEN', 'CREDIT', 'FEE_HELD', quote.fee_fen),
        _line('unit:service-clearing', 'UNIT', 'DEBIT', 'SERVICE_CLEARING', order.amount),
    ]
    for member in members:
        lines.append(_line(f"unit:provider:{member['assignment_id']}", 'UNIT', 'CREDIT', 'PROVIDER_DUE', member['income'],
                           user_id=member['user_id'], account_id=member['account_id']))
    lines += [
        _line('unit:inviter', 'UNIT', 'CREDIT', 'INVITER_DUE', order.inviter_commission,
              user_id=order.inviter_id, account_id=order.inviter_account_id),
        _line('unit:platform', 'UNIT', 'CREDIT', 'PLATFORM_SERVICE', order.shop_income),
    ]
    lines = sorted((item for item in lines if item is not None), key=lambda item: item['line_key'])
    _balanced(lines)
    return lines


@transaction.atomic
def post_test_clearing(order_id):
    """Create or validate one simulated batch; no wallet or channel writes."""
    _require_test_domain()
    if type(order_id) is not int or order_id <= 0:
        raise DirectClearingError('订单编号无效')
    order = Order.objects.select_for_update().get(pk=order_id)
    intent = DirectPaymentIntent.objects.select_for_update().filter(order=order).first()
    if not intent:
        raise DirectClearingError('订单没有微信支付意图')
    session = DirectPaymentSession.objects.select_for_update().filter(intent=intent).first()
    quote = DirectPaymentQuote.objects.select_for_update().filter(intent=intent).first()
    if not quote:
        raise DirectClearingError('支付报价快照不存在')
    # Ensure the validator uses the row held under this transaction's lock.
    intent._state.fields_cache['fee_quote'] = quote
    members, released, aftersales = _read_source(order, intent, quote, session, now=timezone.now())
    snapshot = {'version': 'SIM_V1', 'order_id': order.pk, 'intent_id': intent.pk,
                'account_id': intent.account_id, 'business_cycle': order.business_cycle,
                'merchant_id': intent.mchid, 'app_id': intent.appid, 'merchant_order_no': intent.merchant_order_no,
                'openid_hash': hashlib.sha256(intent.openid.encode('utf-8')).hexdigest(),
                'transaction_id': intent.transaction_id,
                'paid_at': intent.paid_at.isoformat(), 'pool_entered_at': order.pool_entered_at.isoformat(),
                'grabbed_at': order.grabbed_at.isoformat(),
                'in_service_at': order.in_service_at.isoformat(), 'completed_at': order.completed_at.isoformat(),
                'report_ready_at': order.report_ready_at.isoformat(),
                'vip_consumption_ready_at': order.consumption_ready_at.isoformat(),
                'currency': intent.currency, 'payer_total_fen': intent.payer_total_fen,
                'quote_hash': quote.quote_hash, 'service_units': quote.service_units,
                'service_fen': quote.service_fen, 'fee_fen': quote.fee_fen, 'total_fen': quote.total_fen,
                'conversion_version': quote.conversion_version, 'units_per_yuan': quote.units_per_yuan,
                'fen_per_yuan': quote.fen_per_yuan, 'members': members, 'released_members': released,
                'resolved_aftersales': aftersales,
                'inviter_id': order.inviter_id, 'inviter_account_id': order.inviter_account_id,
                'inviter_commission': order.inviter_commission, 'shop_income': order.shop_income}
    lines = _build_lines(order, quote, members)
    snapshot_hash, lines_hash = _digest(snapshot), _digest(lines)
    batch = DirectClearingBatch.objects.select_for_update().filter(order=order).first()
    if batch:
        persisted = list(batch.lines.select_for_update().order_by('line_key').values(
            'line_key', 'book', 'side', 'account_code', 'recipient_user_id', 'recipient_account_id', 'amount'))
        if (batch.intent_id != intent.pk or batch.version != 'SIM_V1' or batch.transaction_id != intent.transaction_id
                or batch.quote_hash != quote.quote_hash or batch.business_cycle != order.business_cycle
                or batch.service_units != quote.service_units or batch.service_fen != quote.service_fen
                or batch.fee_fen != quote.fee_fen or batch.total_fen != quote.total_fen
                or batch.conversion_version != quote.conversion_version or batch.units_per_yuan != quote.units_per_yuan
                or batch.fen_per_yuan != quote.fen_per_yuan
                or batch.snapshot_hash != snapshot_hash or batch.snapshot != snapshot
                or batch.lines_hash != lines_hash or _digest(persisted) != lines_hash):
            raise DirectClearingError('已有模拟账本与当前快照不一致，请人工核对')
        return batch
    batch = DirectClearingBatch.objects.create(order=order, intent=intent, transaction_id=intent.transaction_id,
        quote_hash=quote.quote_hash, business_cycle=order.business_cycle, service_units=quote.service_units,
        service_fen=quote.service_fen, fee_fen=quote.fee_fen, total_fen=quote.total_fen,
        conversion_version=quote.conversion_version, units_per_yuan=quote.units_per_yuan,
        fen_per_yuan=quote.fen_per_yuan, snapshot=snapshot, snapshot_hash=snapshot_hash, lines_hash=lines_hash)
    DirectClearingLine.objects.bulk_create([DirectClearingLine(batch=batch, **item) for item in lines])
    return batch
