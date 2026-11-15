"""Release add-on earnings with the corresponding transferred service segments."""
from django.conf import settings

from .models import OrderSegmentAddonSettlement, OrderSegmentReport
from .settlement import SettlementError
from wallet.models import Transaction


def _parts(manifest):
    funding = manifest.plan.get('points_funding') or manifest.plan.get('ordinary_transfer_funding') or {}
    return funding.get('addons', [])


def enabled():
    from .addon_settlement import enabled as addon_enabled
    return bool(getattr(settings, 'ADDON_SEGMENT_SETTLEMENT_INTERNAL_TESTING', False) and addon_enabled())


def validate_records(order, manifest, people, known):
    from .segment_settlement import _valid_tx

    parts = _parts(manifest)
    expected = {(p['payment_id'], p['segment_id']): p for p in parts}
    if len(expected) != len(parts):
        raise SettlementError('加购履约段重复')
    rows = list(manifest.addon_settlements.select_for_update().select_related(
        'segment', 'report', 'payment__intent', 'income_transaction__wallet'))
    for row in rows:
        part = expected.get((row.payment_id, row.segment_id))
        person = people.get(row.segment.provider_account_id)
        if (not part or not person or row.payment.intent.order_id != order.pk
                or row.report.segment_id != row.segment_id or row.segment_id not in person.segment_ids
                or part['provider_account_id'] != row.segment.provider_account_id
                or row.amount != part['provider_income_units'] or row.settled_at != person.settled_at
                or row.payment.settlements.filter(segment_id=row.segment_id).exists()):
            raise SettlementError('加购逐段结算记录待核对')
        if row.amount:
            if not _valid_tx(row.income_transaction, order=order, account_id=person.account_id,
                             amount=row.amount, kind=Transaction.TxType.INCOME):
                raise SettlementError('加购逐段收益流水待核对')
            known.add(row.income_transaction_id)
        elif row.income_transaction_id:
            raise SettlementError('零收益加购存在流水')
    paid = {(row.payment_id, row.segment_id) for row in rows}
    required = {key for key, part in expected.items() if part['provider_account_id'] in people}
    if paid != required:
        raise SettlementError('已结算陪玩的加购记录不完整')


def settle_person(order, manifest, person, profile, *, now, trace_id):
    from .services import _credit_wallet
    from wallet.income import hold_provider_income

    parts = _parts(manifest)
    for part in parts:
        if part['provider_account_id'] != person.account_id:
            continue
        if part['segment_id'] not in person.segment_ids:
            raise SettlementError('加购不属于陪玩的履约段')
        report = OrderSegmentReport.objects.get(segment_id=part['segment_id'])
        amount = part['provider_income_units']
        income = _credit_wallet(order=order, amount=amount, tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no} 加购 {part["payment_id"]}', event='order.addon_settle.segment',
            trace_id=trace_id, user_id=profile.user_id, account_id=profile.account_id, log_after_commit=True)
        if income:
            hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                                 apply_without_hold=True)
        OrderSegmentAddonSettlement.objects.create(payment_id=part['payment_id'], segment_id=part['segment_id'],
            manifest=manifest, report=report, amount=amount, income_transaction=income, settled_at=now)
