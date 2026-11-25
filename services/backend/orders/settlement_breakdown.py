"""Read-only provider settlement facts for one store order.

This module deliberately does not call settlement, refund, or plan-execution
paths.  It reads the current immutable manifest and verifies the ledger rows
that actually credited the provider.  Missing or contradictory evidence stays
unknown instead of being turned into a zero or a reconstructed allocation.
"""
import hashlib
import json
from types import SimpleNamespace

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.context import current_store
from wallet.income import valid_freeze_transaction
from wallet.models import IncomeHold, Transaction

from .addon_funding_models import OrderAddonSettlement, OrderSegmentAddonSettlement
from .addon_payment_models import OrderAddonPayment
from .models import (
    Order,
    OrderProvider,
    OrderSegmentFinancialSnapshot,
    OrderSegmentPersonSettlement,
    OrderSegmentSettlementManifest,
)
from .settlement import SettlementError


def _integer(value):
    return value if type(value) is int and 0 <= value < 2 ** 63 else None


def _positive_id(value):
    return type(value) is int and 0 < value < 2 ** 63


def _unknown(code, message, *, segment_id=None, payment_id=None):
    return {'code': code, 'message': message, 'segment_id': segment_id, 'payment_id': payment_id}


def _dedupe_unknowns(rows):
    result = []
    seen = set()
    for row in rows:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _iso(value):
    return value.isoformat() if value else None


def _transaction_ref(tx, scope):
    if not tx:
        return None
    return {
        'id': tx.pk,
        'transaction_no': tx.tx_no,
        'tx_type': tx.tx_type,
        'amount_raw': tx.amount,
        'status': tx.status,
        'created_at': _iso(tx.created_at),
        'scope': scope,
    }


def _valid_income_tx(tx, *, order, account_id, amount):
    return bool(
        tx
        and tx.order_id == order.pk
        and tx.wallet.account_id == account_id
        and tx.tx_type == Transaction.TxType.INCOME
        and tx.status == Transaction.Status.SUCCESS
        and tx.amount == amount
        and tx.reversal_of_id is None
        and not tx.reversals.exists()
        and tx.balance_after == tx.balance_before + tx.amount
    )


def _hold_facts(tx, *, order, account_id):
    """Return (currently frozen, verified refs, unknown rows) for one credit."""
    hold = IncomeHold.objects.select_related(
        'freeze_transaction', 'release_transaction',
    ).filter(income_transaction=tx).first() if tx is not None else None
    if hold is None:
        hold_flows = Transaction.objects.filter(
            order=order, wallet__account_id=account_id,
            tx_type__in=(Transaction.TxType.EARNINGS_FREEZE, Transaction.TxType.EARNINGS_RELEASE),
            status=Transaction.Status.SUCCESS,
        ).filter(
            Q(tx_type=Transaction.TxType.EARNINGS_FREEZE, created_income_hold__isnull=True)
            | Q(tx_type=Transaction.TxType.EARNINGS_RELEASE, released_income_hold__isnull=True))
        if tx is not None:
            hold_flows = hold_flows.filter(wallet_id=tx.wallet_id)
        if hold_flows.exists():
            return None, [], [_unknown('INCOME_HOLD_MISSING',
                '收益冻结或解冻流水缺少对应冻结记录')]
        return 0, [], []
    refs = []
    if hold.wallet_id != tx.wallet_id or not valid_freeze_transaction(hold):
        return None, [], [_unknown('INCOME_HOLD_INVALID', '收益冻结原流水或账户归属待核对')]
    refs.append(_transaction_ref(hold.freeze_transaction, 'FREEZE'))
    if hold.cancelled_at and (hold.released_at or hold.release_transaction_id):
        return None, refs, [_unknown('INCOME_HOLD_STATE_INVALID', '收益冻结同时存在取消和释放记录')]
    if hold.cancelled_at:
        return 0, refs, []
    if hold.released_at:
        release = hold.release_transaction
        valid = bool(
            release
            and release.wallet_id == hold.wallet_id
            and release.order_id == order.pk
            and release.tx_type == Transaction.TxType.EARNINGS_RELEASE
            and release.status == Transaction.Status.SUCCESS
            and release.amount == hold.amount - hold.recovered_amount
            and release.balance_after == release.balance_before + release.amount
            and release.reversal_of_id is None
            and not release.reversals.exists()
        )
        if not valid:
            return None, refs, [_unknown('INCOME_RELEASE_INVALID', '收益解冻流水待核对')]
        refs.append(_transaction_ref(release, 'RELEASE'))
        return 0, refs, []
    if hold.release_transaction_id:
        return None, refs, [_unknown('INCOME_HOLD_STATE_INVALID', '收益冻结状态与解冻流水不一致')]
    remaining = _integer(hold.amount - hold.recovered_amount)
    if remaining is None or remaining > hold.amount:
        return None, refs, [_unknown('INCOME_HOLD_AMOUNT_INVALID', '收益冻结剩余金额待核对')]
    return remaining, refs, []


def _status(net, pending, frozen, posted, unknown_rows):
    if unknown_rows or None in (net, pending, frozen, posted):
        return 'REVIEW'
    if pending and pending > 0:
        return 'PENDING' if posted == 0 else 'PARTIAL'
    if frozen and frozen > 0:
        return 'FROZEN'
    return 'POSTED'


def _line(*, line_id, kind, segment, net, pending, frozen, posted, transactions,
          amount_basis, unknown_rows, payment=None, assignment_id=None):
    segment_id = segment.pk if segment else None
    rows = _dedupe_unknowns(unknown_rows)
    addon_name = payment.intent.addon_name if payment else None
    return {
        'line_id': line_id,
        'kind': kind,
        'segment_id': segment_id,
        'provider_assignment_id': assignment_id,
        'slot': segment.slot if segment else None,
        'started_at': _iso(segment.started_at) if segment else None,
        'ended_at': _iso(segment.released_at) if segment else None,
        'payment_id': payment.pk if payment else None,
        'addon_name': addon_name,
        'net_income_raw': net,
        'main_net_income_raw': net if kind == 'MAIN' else None,
        'addon_net_income_raw': net if kind == 'ADDON' else None,
        'pending_income_raw': pending,
        'frozen_income_raw': frozen,
        'posted_income_raw': posted,
        'settlement_status': _status(net, pending, frozen, posted, rows),
        'amount_basis': amount_basis,
        'freeze_until': None,
        'transactions': [row for row in transactions if row],
        'unknown_items': rows,
    }


def _identity_matches(row, account, legacy_user):
    if row.provider_account_id is not None:
        if row.provider_account_id != account.pk:
            return False
        if row.provider_id is None:
            return True
        if legacy_user is not None:
            return row.provider_id == legacy_user.pk
        from club_accounts.models import LegacyAccountMap
        mapped = LegacyAccountMap.objects.filter(account_id=account.pk).values_list(
            'legacy_user_id', flat=True).first()
        return row.provider_id == mapped
    return bool(legacy_user and row.provider_id == legacy_user.pk)


def _report_versions_match(row):
    try:
        from .segment_settlement import _report_versions
        return row.report_versions == _report_versions(SimpleNamespace(segment_ids=row.segment_ids))
    except (SettlementError, KeyError, TypeError, ValueError):
        return False


def _manifest_main(order, account, segments, unknowns):
    manifest = OrderSegmentSettlementManifest.objects.filter(order=order).first()
    plan = manifest.plan if manifest and isinstance(manifest.plan, dict) else {}
    hash_valid = bool(manifest and isinstance(manifest.plan, dict)
                      and manifest.plan_hash == hashlib.sha256(json.dumps(
                          manifest.plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    if not hash_valid:
        unknowns.append(_unknown('MANIFEST_HASH_MISMATCH', '结算清单或清单摘要待核对'))

    expected_ids = [segment.pk for segment in segments]
    people = plan.get('people') if isinstance(plan.get('people'), list) else []
    matches = [row for row in people if isinstance(row, dict)
               and type(row.get('account_id')) is int and row.get('account_id') == account.pk]
    person_plan = matches[0] if len(matches) == 1 else None
    if len(matches) != 1:
        unknowns.append(_unknown('PERSON_PLAN_INVALID', '本人结算清单记录缺失或重复'))
    person_income = _integer(person_plan.get('income')) if person_plan else None
    person_segment_ids = person_plan.get('segment_ids') if person_plan else None
    person_ids_valid = bool(isinstance(person_segment_ids, list)
        and all(_positive_id(value) for value in person_segment_ids)
        and len(person_segment_ids) == len(set(person_segment_ids))
        and set(person_segment_ids) == set(expected_ids))

    plan_segments = plan.get('segments') if isinstance(plan.get('segments'), list) else []
    selected = [row for row in plan_segments if isinstance(row, dict)
                and type(row.get('account_id')) is int and row.get('account_id') == account.pk]
    selected_ids = [row.get('segment_id') for row in selected]
    selected_valid = bool(selected_ids and all(_positive_id(value) for value in selected_ids)
        and len(selected_ids) == len(set(selected_ids))
        and set(selected_ids) == set(expected_ids))
    amounts = {}
    if selected_valid:
        for row in selected:
            amount = _integer(row.get('income'))
            if amount is None:
                selected_valid = False
                break
            amounts[row['segment_id']] = amount
    segment_net_valid = bool(hash_valid and person_plan and person_income is not None
        and person_ids_valid and selected_valid and sum(amounts.values()) == person_income)
    if not segment_net_valid:
        unknowns.append(_unknown('MAIN_SEGMENT_NET_UNKNOWN',
            '主单逐段净收益无法与本人已保存净收益互相核对'))

    settlement = OrderSegmentPersonSettlement.objects.select_related('income_transaction__wallet').filter(
        manifest=manifest, account=account).first() if manifest else None
    posted = None
    record_valid = False
    if settlement:
        amount = _integer(settlement.amount)
        tx = settlement.income_transaction
        tx_valid = (amount == 0 and tx is None) or (
            amount is not None and amount > 0 and _valid_income_tx(
                tx, order=order, account_id=account.pk, amount=amount))
        record_valid = bool(hash_valid and person_plan and amount == person_income
            and settlement.segment_ids == person_segment_ids and _report_versions_match(settlement)
            and tx_valid)
        if tx_valid:
            posted = amount
        else:
            unknowns.append(_unknown('PERSON_INCOME_TRANSACTION_INVALID',
                '本人主单收益入账流水待核对'))
        if not record_valid:
            unknowns.append(_unknown('PERSON_SETTLEMENT_MISMATCH',
                '本人结算凭证与当前清单或报单版本不一致'))
    else:
        main_like = Transaction.objects.filter(order=order, wallet__account=account,
            tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
            reversal_of__isnull=True).exclude(remark__contains=' 加购 ')
        if main_like.exists():
            posted = None
            unknowns.append(_unknown('PERSON_SETTLEMENT_MISSING',
                '存在本人主单收益流水但缺少对应结算凭证'))
        else:
            posted = 0

    main_net = person_income if segment_net_valid and (settlement is None or record_valid) else None
    pending = None
    if main_net is not None and posted is not None and 0 <= posted <= main_net:
        pending = main_net - posted
    main_refs = []
    main_frozen = 0 if posted == 0 else None
    freeze_until = None
    if settlement and posted is not None:
        tx = settlement.income_transaction
        if tx:
            main_refs.append(_transaction_ref(tx, 'PERSON_MAIN'))
        main_frozen, hold_refs, hold_unknowns = _hold_facts(
            tx, order=order, account_id=account.pk)
        main_refs.extend(hold_refs)
        unknowns.extend(hold_unknowns)
        hold = IncomeHold.objects.filter(income_transaction=tx).first() if tx else None
        if hold and main_frozen is not None and not hold.released_at and not hold.cancelled_at:
            freeze_until = _iso(hold.release_at)

    lines = []
    for segment in segments:
        line_unknowns = []
        net = amounts.get(segment.pk) if segment_net_valid else None
        if settlement and posted is not None and posted > 0 and len(segments) > 1:
            line_posted = None
            line_frozen = None
            line_unknowns.append(_unknown('SEGMENT_POSTED_UNKNOWN',
                '本人多段共用一笔主单入账流水，逐段到账金额待核对', segment_id=segment.pk))
            line_unknowns.append(_unknown('SEGMENT_FROZEN_UNKNOWN',
                '本人多段共用一笔主单入账流水，逐段冻结金额待核对', segment_id=segment.pk))
        else:
            line_posted = posted
            line_frozen = main_frozen
        line_pending = net - line_posted if net is not None and line_posted is not None \
            and 0 <= line_posted <= net else None
        line_refs = main_refs if len(segments) == 1 else []
        line = _line(line_id=f'main:{segment.pk}', kind='MAIN', segment=segment,
            net=net, pending=line_pending, frozen=line_frozen, posted=line_posted,
            transactions=line_refs, amount_basis='SEGMENT_SETTLEMENT_MANIFEST' if net is not None else 'UNKNOWN',
            unknown_rows=line_unknowns)
        line['freeze_until'] = freeze_until if len(segments) == 1 else None
        lines.append(line)
        unknowns.extend(line_unknowns)

    if not lines:
        line = _line(line_id='main:unknown', kind='MAIN', segment=None,
            net=None, pending=None, frozen=None, posted=posted, transactions=main_refs,
            amount_basis='UNKNOWN', unknown_rows=[_unknown('MAIN_SEGMENT_MISSING',
                '本人主单履约段记录缺失')])
        line['freeze_until'] = freeze_until
        lines.append(line)
        unknowns.extend(line['unknown_items'])
    return {
        'manifest': manifest,
        'plan': plan,
        'hash_valid': hash_valid,
        'person_plan': person_plan,
        'settlement': settlement,
        'main_net': main_net,
        'pending': pending,
        'frozen': main_frozen,
        'posted': posted,
        'freeze_until': freeze_until,
        'transactions': main_refs,
        'lines': lines,
    }


def _manifest_addons(order, account, segments, manifest, plan, person_settled, plan_hash_valid, unknowns):
    funding_values = [plan.get('points_funding'), plan.get('ordinary_transfer_funding')]
    funding_valid = all(value is None or isinstance(value, dict) for value in funding_values)
    for value in funding_values:
        if value is not None and not isinstance(value, dict):
            unknowns.append(_unknown('ADDON_FUNDING_INVALID', '本人加购资金清单结构待核对'))
    populated_funding = [value for value in funding_values if isinstance(value, dict) and value]
    if len(populated_funding) > 1:
        funding_valid = False
        unknowns.append(_unknown('ADDON_FUNDING_INVALID', '本人加购资金清单存在多种资金来源'))
    funding = populated_funding[0] if populated_funding else {}
    raw_parts = funding.get('addons', [])
    if not isinstance(raw_parts, list):
        funding_valid = False
        unknowns.append(_unknown('ADDON_FUNDING_INVALID', '本人加购资金清单结构待核对'))
        raw_parts = []
    parts = raw_parts
    selected = [part for part in parts if isinstance(part, dict)
                and type(part.get('provider_account_id')) is int
                and part.get('provider_account_id') == account.pk]
    keys = [(part.get('payment_id'), part.get('segment_id')) for part in selected
            if _positive_id(part.get('payment_id')) and _positive_id(part.get('segment_id'))]
    duplicate_parts = len(keys) != len(set(keys))
    if duplicate_parts:
        unknowns.append(_unknown('ADDON_PART_DUPLICATE', '本人加购结算清单存在重复付款或履约段'))
    segment_by_id = {segment.pk: segment for segment in segments}
    lines = []
    net_values = []
    pending_values = []
    frozen_values = []
    posted_values = []
    all_refs = []
    represented_payments = set()
    for index, part in enumerate(selected):
        raw_payment_id, raw_segment_id = part.get('payment_id'), part.get('segment_id')
        payment_id = raw_payment_id if _positive_id(raw_payment_id) else None
        segment_id = raw_segment_id if _positive_id(raw_segment_id) else None
        payment = OrderAddonPayment.objects.select_related('intent').filter(pk=payment_id).first() \
            if payment_id is not None else None
        segment = segment_by_id.get(segment_id) if segment_id is not None else None
        # A corrupt manifest must not turn another provider's payment or credit
        # into a readable line merely because its ID was referenced here.
        visible_payment = payment if payment and payment.intent.order_id == order.pk \
            and payment.intent.provider_id == account.pk else None
        amount = _integer(part.get('provider_income_units'))
        part_valid = bool(visible_payment and segment and amount is not None)
        if visible_payment:
            represented_payments.add(visible_payment.pk)
        if not part_valid:
            unknowns.append(_unknown('ADDON_PART_INVALID', '加购付款、履约段或收益金额待核对',
                segment_id=segment.pk if segment else None,
                payment_id=visible_payment.pk if visible_payment else None))
        net = amount if part_valid and plan_hash_valid and funding_valid else None

        segment_rows = OrderSegmentAddonSettlement.objects.select_related(
            'income_transaction__wallet', 'report__segment').filter(
                manifest=manifest, payment_id=payment_id, segment_id=segment_id) \
            if manifest and part_valid else []
        legacy_rows = OrderAddonSettlement.objects.select_related(
            'income_transaction__wallet', 'report').filter(
                payment_id=payment_id, segment_id=segment_id) if part_valid else []
        segment_row = segment_rows.first() if len(segment_rows) == 1 else None
        legacy_row = legacy_rows.first() if len(legacy_rows) == 1 else None
        row = segment_row or legacy_row
        row_unknowns = []
        posted = None
        tx = None
        if len(segment_rows) > 1 or len(legacy_rows) > 1 or (segment_row and legacy_row):
            row_unknowns.append(_unknown('ADDON_SETTLEMENT_DUPLICATE',
                '加购存在重复或冲突的结算凭证', segment_id=segment_id,
                payment_id=visible_payment.pk if visible_payment else None))
        elif row:
            tx = row.income_transaction
            report_valid = bool(segment_row and row.report.segment_id == segment_id)
            if legacy_row:
                report_valid = bool(row.report.order_id == order.pk)
            credit_valid = bool((amount == 0 and tx is None) or (amount and amount > 0
                and _valid_income_tx(tx, order=order, account_id=account.pk, amount=amount)))
            row_valid = bool(part_valid and row.amount == amount and report_valid and credit_valid)
            if not row_valid:
                row_unknowns.append(_unknown('ADDON_SETTLEMENT_INVALID',
                    '加购结算凭证或实际收益流水待核对', segment_id=segment_id,
                    payment_id=visible_payment.pk if visible_payment else None))
            if part_valid and credit_valid:
                posted = amount
        elif person_settled:
            row_unknowns.append(_unknown('ADDON_SETTLEMENT_MISSING',
                '本人已结算但缺少逐笔加购结算凭证',
                segment_id=segment.pk if segment else None,
                payment_id=visible_payment.pk if visible_payment else None))
        elif part_valid:
            income_exists = Transaction.objects.filter(
                order=order,
                wallet__account=account,
                tx_type=Transaction.TxType.INCOME,
                status=Transaction.Status.SUCCESS,
            ).exists()
            if income_exists:
                row_unknowns.append(_unknown('ADDON_SETTLEMENT_MISSING',
                    '本人收益已入账但缺少逐笔加购结算凭证',
                    segment_id=segment.pk if segment else None,
                    payment_id=visible_payment.pk if visible_payment else None))
            else:
                posted = 0

        pending = net - posted if net is not None and posted is not None and 0 <= posted <= net else None
        if part_valid and tx is not None and posted is not None:
            frozen, refs, hold_unknowns = _hold_facts(tx, order=order, account_id=account.pk)
        elif tx is None and posted is not None:
            frozen, refs, hold_unknowns = 0, [], []
        else:
            frozen, refs, hold_unknowns = None, [], []
        row_unknowns.extend(hold_unknowns)
        transactions = ([_transaction_ref(tx, 'ADDON')]
                        if part_valid and tx is not None and posted is not None else []) + refs
        payment_key = visible_payment.pk if visible_payment else 'unknown'
        segment_key = segment.pk if segment else 'unknown'
        suffix = '' if payment_key != 'unknown' and segment_key != 'unknown' else f':{index}'
        line = _line(line_id=f'addon:{payment_key}:{segment_key}{suffix}', kind='ADDON', segment=segment,
            payment=visible_payment, net=net, pending=pending, frozen=frozen, posted=posted,
            transactions=transactions, amount_basis='SEGMENT_SETTLEMENT_MANIFEST' if net is not None else 'UNKNOWN',
            unknown_rows=row_unknowns)
        hold = IncomeHold.objects.filter(income_transaction=tx).first() \
            if part_valid and tx is not None and posted is not None else None
        line['freeze_until'] = _iso(hold.release_at) \
            if hold and frozen is not None and not hold.released_at and not hold.cancelled_at else None
        lines.append(line)
        unknowns.extend(row_unknowns)
        net_values.append(net)
        pending_values.append(pending)
        frozen_values.append(frozen)
        posted_values.append(posted)
        all_refs.extend(transactions)

    paid = OrderAddonPayment.objects.filter(intent__order=order, intent__provider=account,
        paid_at__isnull=False).select_related('intent')
    for payment in paid:
        if payment.pk in represented_payments:
            continue
        segment = segment_by_id.get(payment.intent.segment_id)
        row_unknowns = [_unknown('ADDON_PAYMENT_NOT_IN_MANIFEST',
            '已付加购未进入本人当前结算清单，收益待核对',
            segment_id=segment.pk if segment else None, payment_id=payment.pk)]
        lines.append(_line(line_id=f'addon:{payment.pk}:unknown', kind='ADDON', segment=segment,
            payment=payment, net=None, pending=None, frozen=None, posted=None, transactions=[],
            amount_basis='UNKNOWN', unknown_rows=row_unknowns))
        unknowns.extend(row_unknowns)
        net_values.append(None)
        pending_values.append(None)
        frozen_values.append(None)
        posted_values.append(None)
    net_known = bool(plan_hash_valid and funding_valid and not duplicate_parts
        and all(value is not None for value in net_values))
    posted_known = bool(not duplicate_parts and all(value is not None for value in posted_values))
    return {
        'lines': lines,
        'net': sum(net_values) if net_known else None,
        'pending': sum(pending_values) if net_known and all(value is not None for value in pending_values) else None,
        'frozen': sum(frozen_values) if posted_known and all(value is not None for value in frozen_values) else None,
        'posted': sum(posted_values) if posted_known else None,
        'transactions': all_refs,
    }


def _ordinary_main(order, account, segments, legacy_user, unknowns):
    assignments = [row for row in order.providers.select_related('provider', 'provider_account').all()
                   if _identity_matches(row, account, legacy_user)]
    if len(assignments) != 1:
        unknowns.append(_unknown('ORDINARY_ASSIGNMENT_INVALID', '本人普通订单履约记录缺失或重复'))
    assignment = assignments[0] if len(assignments) == 1 else None
    paid_addons = OrderAddonPayment.objects.filter(intent__order=order, intent__provider=account,
        paid_at__isnull=False).exists()
    unsupported = bool(order.refund_records.exists() or paid_addons or order.is_gift_snapshot
        or hasattr(order, 'platform_subsidy') or len(segments) > 1)
    if unsupported:
        unknowns.append(_unknown('ORDINARY_BREAKDOWN_UNSUPPORTED',
            '普通订单含退款、加购、补贴或多段履约，逐段净收益待核对'))
    amount = _integer(assignment.provider_income) if assignment else None
    tx = None
    posted = None
    if assignment and assignment.settled_at and not unsupported:
        incomes = list(Transaction.objects.select_related('wallet').filter(order=order,
            wallet__account=account, tx_type=Transaction.TxType.INCOME,
            status=Transaction.Status.SUCCESS, reversal_of__isnull=True))
        if amount == 0 and not incomes:
            posted = 0
        elif amount is not None and len(incomes) == 1 and _valid_income_tx(
                incomes[0], order=order, account_id=account.pk, amount=amount):
            tx = incomes[0]
            posted = amount
        else:
            unknowns.append(_unknown('ORDINARY_INCOME_TRANSACTION_INVALID',
                '普通订单本人收益流水缺失、重复或金额不一致'))
    elif not unsupported:
        unknowns.append(_unknown('ORDINARY_SETTLEMENT_PENDING',
            '普通订单尚无已核验的收益入账流水'))

    snapshot = None
    if len(segments) == 1 and assignment and not unsupported:
        snapshot = OrderSegmentFinancialSnapshot.objects.filter(segment=segments[0]).first()
        transferred = hasattr(segments[0], 'original_transfer_out') \
            or hasattr(segments[0], 'original_transfer_in')
        if transferred or not snapshot or snapshot.income_at_entry != amount:
            unsupported = True
            unknowns.append(_unknown('ORDINARY_SEGMENT_SNAPSHOT_INVALID',
                '普通订单逐段快照与本人已结算收益不一致'))
    net = amount if assignment and assignment.settled_at and posted is not None and not unsupported else None
    pending = net - posted if net is not None and posted is not None else None
    frozen, hold_refs, hold_unknowns = _hold_facts(tx, order=order, account_id=account.pk)
    unknowns.extend(hold_unknowns)
    refs = ([_transaction_ref(tx, 'MAIN')] if tx else []) + hold_refs
    line_segments = segments or [None]
    lines = []
    for segment in line_segments:
        line = _line(line_id=f'main:{segment.pk}' if segment else f'main:assignment:{assignment.pk if assignment else "unknown"}',
            kind='MAIN', segment=segment, net=net if len(line_segments) == 1 else None,
            pending=pending if len(line_segments) == 1 else None,
            frozen=frozen if len(line_segments) == 1 else None,
            posted=posted if len(line_segments) == 1 else None,
            assignment_id=assignment.pk if assignment else None,
            transactions=refs if len(line_segments) == 1 else [],
            amount_basis='VERIFIED_ORDINARY_SETTLEMENT' if net is not None else 'UNKNOWN',
            unknown_rows=[] if len(line_segments) == 1 else [_unknown('SEGMENT_POSTED_UNKNOWN',
                '本人多段共用一笔主单入账流水，逐段到账金额待核对',
                segment_id=segment.pk if segment else None)])
        hold = IncomeHold.objects.filter(income_transaction=tx).first() if tx else None
        line['freeze_until'] = _iso(hold.release_at) \
            if hold and frozen is not None and not hold.released_at and not hold.cancelled_at else None
        lines.append(line)
        unknowns.extend(line['unknown_items'])
    return {'main_net': net, 'pending': pending, 'frozen': frozen, 'posted': posted,
            'transactions': refs, 'lines': lines}


def _reconcile_income_references(order, account, main, addons, unknowns):
    """Keep duplicate or ownerless service credits from looking fully posted."""
    main_ids = {row['id'] for row in main['transactions']
                if row and row['tx_type'] == Transaction.TxType.INCOME}
    addon_ids = [row['id'] for row in addons['transactions']
                 if row and row['tx_type'] == Transaction.TxType.INCOME]
    addon_counts = {tx_id: addon_ids.count(tx_id) for tx_id in set(addon_ids)}
    shared_ids = {tx_id for tx_id, count in addon_counts.items() if count > 1} | (main_ids & set(addon_ids))
    main_shared = bool(main_ids & shared_ids)
    addon_shared = bool(shared_ids & set(addon_ids))
    if shared_ids:
        for line in main['lines']:
            if main_shared:
                line['posted_income_raw'] = None
                line['pending_income_raw'] = None
                line['frozen_income_raw'] = None
                line['freeze_until'] = None
                line['settlement_status'] = 'REVIEW'
                line['unknown_items'] = _dedupe_unknowns(line['unknown_items'] + [_unknown(
                    'INCOME_TRANSACTION_SHARED', '同一笔收益流水被多处结算凭证引用，到账归属待核对',
                    segment_id=line['segment_id'])])
        for line in addons['lines']:
            refs = {row['id'] for row in line['transactions'] if row}
            if refs & shared_ids:
                line['posted_income_raw'] = None
                line['pending_income_raw'] = None
                line['frozen_income_raw'] = None
                line['freeze_until'] = None
                line['settlement_status'] = 'REVIEW'
                line['unknown_items'] = _dedupe_unknowns(line['unknown_items'] + [_unknown(
                    'INCOME_TRANSACTION_SHARED', '同一笔收益流水被多处结算凭证引用，到账归属待核对',
                    segment_id=line['segment_id'], payment_id=line['payment_id'])])
        if main_shared:
            main.update(posted=None, pending=None, frozen=None, freeze_until=None)
        if addon_shared:
            addons.update(posted=None, pending=None, frozen=None)
        unknowns.append(_unknown('INCOME_TRANSACTION_SHARED',
            '同一笔收益流水被多处结算凭证引用，到账归属待核对'))

    expected_ids = main_ids | set(addon_ids)
    service_incomes = Transaction.objects.select_related('wallet').filter(
        order=order, wallet__account=account, tx_type=Transaction.TxType.INCOME,
        status=Transaction.Status.SUCCESS, reversal_of__isnull=True).order_by('pk')
    extra_refs = [_transaction_ref(tx, 'UNRECONCILED_INCOME') for tx in service_incomes
                  if tx.pk not in expected_ids]
    if extra_refs:
        unknowns.append(_unknown('INCOME_TRANSACTION_UNRECONCILED',
            '存在本人订单收益流水但缺少对应结算凭证，到账合计待核对'))
    return extra_refs, bool(extra_refs)


def build_settlement_breakdown(order, account, *, legacy_user=None):
    """Build the provider-scoped response without changing any financial fact."""
    if not isinstance(account, ClubAccount) or account.account_type != ClubAccount.AccountType.PROVIDER:
        raise PermissionError('仅陪玩账号可查看逐段收益')
    segments = list(order.original_attribution.segments.filter(provider_account=account).order_by('pk')) \
        if hasattr(order, 'original_attribution') else []
    unknowns = []
    manifest = OrderSegmentSettlementManifest.objects.filter(order=order).first()
    if manifest:
        main = _manifest_main(order, account, segments, unknowns)
        addons = _manifest_addons(order, account, segments, manifest, main['plan'],
            person_settled=main['settlement'] is not None,
            plan_hash_valid=main['hash_valid'], unknowns=unknowns)
    else:
        main = _ordinary_main(order, account, segments, legacy_user, unknowns)
        paid = OrderAddonPayment.objects.filter(intent__order=order, intent__provider=account,
            paid_at__isnull=False).select_related('intent')
        addon_lines = []
        for payment in paid:
            segment = next((row for row in segments if row.pk == payment.intent.segment_id), None)
            row_unknowns = [_unknown('ADDON_SETTLEMENT_UNSUPPORTED',
                '缺少逐段结算清单，逐笔加购净收益待核对',
                segment_id=segment.pk if segment else None, payment_id=payment.pk)]
            addon_lines.append(_line(line_id=f'addon:{payment.pk}:unknown', kind='ADDON',
                segment=segment, payment=payment, net=None, pending=None, frozen=None, posted=None,
                transactions=[], amount_basis='UNKNOWN', unknown_rows=row_unknowns))
            unknowns.extend(row_unknowns)
        addons = {'lines': addon_lines, 'net': None if addon_lines else 0,
            'pending': None if addon_lines else 0, 'frozen': None if addon_lines else 0,
            'posted': None if addon_lines else 0, 'transactions': []}

    extra_refs = []
    posted_total_unknown = False
    if manifest:
        extra_refs, posted_total_unknown = _reconcile_income_references(
            order, account, main, addons, unknowns)

    def total(left, right):
        return left + right if left is not None and right is not None else None

    totals = {
        'main_net_income_raw': main['main_net'],
        'addon_net_income_raw': addons['net'],
        'net_income_raw': total(main['main_net'], addons['net']),
        'total_income_raw': total(main['main_net'], addons['net']),
        'pending_income_raw': total(main['pending'], addons['pending']),
        'frozen_income_raw': total(main['frozen'], addons['frozen']),
        'posted_income_raw': total(main['posted'], addons['posted']),
    }
    if posted_total_unknown:
        totals['posted_income_raw'] = None
        totals['pending_income_raw'] = None
        totals['frozen_income_raw'] = None
    unknowns = _dedupe_unknowns(unknowns)
    totals['unknown'] = bool(unknowns)
    transactions = main['transactions'] + addons['transactions'] + extra_refs
    person_transactions = []
    seen_tx = set()
    for ref in transactions:
        if ref and ref['id'] not in seen_tx:
            seen_tx.add(ref['id'])
            person_transactions.append(ref)
    income_tx = None
    if manifest and main.get('settlement') and main.get('posted') is not None \
            and main['settlement'].income_transaction_id:
        income_tx = _transaction_ref(main['settlement'].income_transaction, 'PERSON_MAIN')
    else:
        income_tx = next((ref for ref in main.get('transactions', [])
            if ref and ref['tx_type'] == Transaction.TxType.INCOME), None)
    status = _status(totals['net_income_raw'], totals['pending_income_raw'],
        totals['frozen_income_raw'], totals['posted_income_raw'], unknowns)
    store = current_store()
    return {
        'order_id': order.pk,
        'order_no': order.order_no,
        'store_slug': store.slug if store else None,
        'current_provider': {
            'account_id': account.pk,
            'account_no': account.account_no,
            'display_name': account.nickname or account.username,
        },
        'scope': f'PROVIDER#{account.pk}',
        'generated_at': timezone.now().isoformat(),
        'status': status,
        'main_segments': main['lines'],
        'addon_earnings': addons['lines'],
        'person_settlement': {
            'account_id': account.pk,
            'segment_ids': [segment.pk for segment in segments],
            'main_net_income_raw': main['main_net'],
            'addon_net_income_raw': addons['net'],
            'net_income_raw': totals['net_income_raw'],
            'pending_income_raw': totals['pending_income_raw'],
            'frozen_income_raw': totals['frozen_income_raw'],
            'posted_income_raw': totals['posted_income_raw'],
            'settlement_status': status,
            'freeze_until': main.get('freeze_until'),
            'income_transaction': income_tx,
            'transactions': person_transactions,
            'unknown_items': unknowns,
        },
        'totals': totals,
        'transaction_refs': person_transactions,
        'unknown_items': unknowns,
    }


def _response(payload, *, status=200):
    response = Response(payload, status=status)
    response['Cache-Control'] = 'no-store'
    return response


class SettlementBreakdownView(APIView):
    """Current provider only; IDs from another store or assignment are hidden."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id):
        account = request.account
        if account.account_type != ClubAccount.AccountType.PROVIDER:
            return _response({'code': 403, 'msg': '仅陪玩账号可查看逐段收益'}, status=403)
        if not _positive_id(order_id):
            return _response({'code': 404, 'msg': '订单不存在或无权查看'}, status=404)
        ownership = Q(provider_account=account) | Q(providers__provider_account=account) \
            | Q(original_attribution__segments__provider_account=account)
        order = Order.objects.filter(pk=order_id).filter(ownership).distinct().first()
        legacy_user = getattr(request, 'legacy_user', None)
        if order is None and legacy_user:
            order = Order.objects.filter(pk=order_id).filter(
                Q(provider_account__isnull=True, provider=legacy_user)
                | Q(providers__provider_account__isnull=True, providers__provider=legacy_user)
            ).distinct().first()
        if order is None:
            return _response({'code': 404, 'msg': '订单不存在或无权查看'}, status=404)
        try:
            data = build_settlement_breakdown(order, account, legacy_user=legacy_user)
        except PermissionError as exc:
            return _response({'code': 403, 'msg': str(exc)}, status=403)
        return _response({'code': 0, 'data': data})
