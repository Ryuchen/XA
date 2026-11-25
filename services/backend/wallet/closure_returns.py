"""Boss-account closure inventory and atomic principal-return state changes.

Wallet/source writes stay in the target store DB. New write paths use the same
wallet -> account lock order as boss retirement. Immutable ClosureReturnLot rows
own the original frozen shares; PointFundingLot.remaining remains available for
later original refunds and existing order payments.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
from datetime import datetime, timezone as datetime_timezone

from django.db import DatabaseError, IntegrityError, InterfaceError
from django.utils import timezone

from club_accounts.models import ClubAccount
from engagement.models import MemberProfile, PointFundingLot, PointLedger
from stores.closure import ClosureError, read_platform_closure
from stores.context import store_context
from stores.db import transaction
from stores.models import Store
from stores.platform_permissions import platform_may

from .closure_return_sources import verified_source_balances
from .models import (
    ClosureReturnCase, ClosureReturnEvent, ClosureReturnLot, ClosureReturnOutbox,
    Transaction, Wallet,
)

MAX_UNITS = 2147483647
REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}')
SHA256 = re.compile(r'[0-9a-f]{64}')
EXTERNAL_REFERENCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}')
BANK_ACCOUNT = re.compile(r'(?<![A-Za-z0-9])(?:\d[ -]?){12,19}(?![A-Za-z0-9])')
ROW_FIELDS = (
    'case_id', 'account_id', 'business_cycle', 'account_label', 'status',
    'wallet_units', 'principal_units', 'gift_units', 'compensation_units',
    'points_hundredths', 'frozen_units', 'refund_fen', 'paid_fen', 'version',
    'blocking_reasons', 'updated_at',
)


class ClosureReturnError(ValueError):
    def __init__(self, message, status=400, code='CLOSURE_RETURN_INVALID'):
        super().__init__(message)
        self.status, self.code = status, code


def _request_id(value):
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value.strip()):
        raise ClosureReturnError('client_request_id 无效')
    return value.strip()


def _integer(value, label, *, positive=False):
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= MAX_UNITS:
        raise ClosureReturnError(f'{label}无效')
    return value


def _fingerprint(store_id, actor_id, operation, values):
    raw = json.dumps({
        'store_id': store_id, 'actor_id': str(actor_id), 'operation': operation, 'values': values,
    }, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def mask_external_reference(value):
    if not isinstance(value, str):
        raise ClosureReturnError('线下外部编号无效')
    value = unicodedata.normalize('NFKC', value).strip()
    if not EXTERNAL_REFERENCE.fullmatch(value) or BANK_ACCOUNT.search(value):
        raise ClosureReturnError('外部编号须为安全引用，不得包含完整银行账号')
    if len(value) <= 4:
        return '*' * len(value)
    return value[:2] + '*' * min(12, len(value) - 4) + value[-2:]


def _proof(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value.strip().lower()):
        raise ClosureReturnError('proof_sha256 必须为有效的 SHA256')
    return value.strip().lower()


def _points(account):
    profile = MemberProfile.objects.filter(account=account).only('points_balance').first()
    if profile is None:
        return None
    latest = PointLedger.objects.filter(account=account).order_by('-id').only('balance_after').first()
    if latest is None:
        return 0 if profile.points_balance == 0 else None
    return profile.points_balance if latest.balance_after == profile.points_balance else None


def _latest_case(account, *, lock=False):
    rows = ClosureReturnCase.objects.filter(account=account, business_cycle=account.business_cycle)
    if lock:
        rows = rows.select_for_update()
    return rows.order_by('-created_at', '-id').first()


def _updated_at(account, wallet, case=None):
    values = [account.updated_at, wallet.updated_at if wallet else None, case.updated_at if case else None]
    return max(value for value in values if value is not None)


def _inventory(account, wallet, lots, points, *, zero_blocks=True):
    reasons = []
    complete = True
    values = {'PREPAID': None, 'GIFT': None, 'COMPENSATION': None}
    if wallet is None:
        reasons.append('WALLET_UNAVAILABLE')
        complete = False
    else:
        values, source_complete, source_reasons = verified_source_balances(account, lots)
        reasons.extend(source_reasons)
        complete = complete and source_complete
        if sum(lot.remaining for lot in lots) != wallet.balance:
            values = {'PREPAID': None, 'GIFT': None, 'COMPENSATION': None}
            reasons.append('WALLET_LOT_MISMATCH')
            complete = False
    if points is None:
        reasons.append('POINTS_UNAVAILABLE')
        complete = False
    if values['GIFT'] or values['COMPENSATION']:
        reasons.append('NON_PRINCIPAL_BALANCE')
    if zero_blocks and wallet is not None and wallet.balance == 0 and not lots and wallet.frozen_amount == 0:
        reasons.append('ZERO_BALANCE')
    return values, list(dict.fromkeys(reasons)), complete


def _live_row(account, wallet, lots, points, case=None):
    positive = [lot for lot in lots if lot.remaining > 0]
    values, reasons, complete = _inventory(account, wallet, positive, points)
    if wallet is not None and wallet.frozen_amount:
        reasons.append('EXISTING_FROZEN_BALANCE')
    reasons = list(dict.fromkeys(reasons))
    ready = not reasons
    principal = values['PREPAID']
    return {
        'case_id': str(case.pk) if case else None, 'account_id': account.pk,
        'business_cycle': account.business_cycle, 'account_label': account.nickname or account.username,
        'status': 'READY_TO_PREPARE' if ready else 'REVIEW_REQUIRED',
        'wallet_units': wallet.balance if wallet else None, 'principal_units': principal,
        'gift_units': values['GIFT'], 'compensation_units': values['COMPENSATION'],
        'points_hundredths': points, 'frozen_units': wallet.frozen_amount if wallet else None,
        'refund_fen': principal if ready and principal and principal > 0 else None,
        'paid_fen': 0,
        'version': case.version if case else 0, 'blocking_reasons': reasons,
        'updated_at': _updated_at(account, wallet, case).isoformat(),
    }, complete


def _case_row(case, account, wallet, points):
    blocking = [] if points is not None else ['POINTS_UNAVAILABLE']
    return {
        'case_id': str(case.pk), 'account_id': account.pk, 'business_cycle': case.business_cycle,
        'account_label': account.nickname or account.username, 'status': case.status,
        'wallet_units': wallet.balance if wallet else None, 'principal_units': case.principal_units,
        'gift_units': 0, 'compensation_units': 0, 'points_hundredths': points,
        'frozen_units': wallet.frozen_amount if wallet else None,
        'refund_fen': case.principal_units if case.status in ('FROZEN', 'PAID') else None,
        'paid_fen': case.paid_fen if case.status == 'PAID' else 0,
        'version': case.version, 'blocking_reasons': blocking,
        'updated_at': case.updated_at.isoformat(),
    }


def _frozen_issues(case, account, wallet, lots):
    issues = ['MIGRATION_REVIEW_REQUIRED'] if case.migration_review_required else []
    snapshots = list(case.lots.order_by('source_lot_id'))
    by_id = {lot.pk: lot for lot in lots}
    if (wallet is None or wallet.frozen_amount < case.principal_units or not snapshots
            or sum(row.principal_units for row in snapshots) != case.principal_units):
        issues.append('FROZEN_WALLET_MISMATCH')
    freeze = case.freeze_transaction
    if (freeze.wallet_id != (wallet.pk if wallet else None) or freeze.tx_type != 'CLOSURE_FREEZE'
            or freeze.status != 'SUCCESS' or freeze.amount != -case.principal_units
            or freeze.balance_after != freeze.balance_before - case.principal_units):
        issues.append('FROZEN_WALLET_MISMATCH')
    for snapshot in snapshots:
        lot = by_id.get(snapshot.source_lot_id)
        if (lot is None or lot.account_id != account.pk or lot.source_key != snapshot.source_key
                or lot.kind != snapshot.source_kind or snapshot.gift_units != 0
                or snapshot.principal_units != snapshot.remaining_units):
            issues.append('FROZEN_SOURCE_MISMATCH')
            break
    return list(dict.fromkeys(issues))


def _frozen_row(case, account, wallet, lots, points):
    positive = [lot for lot in lots if lot.remaining > 0]
    values, current_reasons, current_complete = _inventory(
        account, wallet, positive, points, zero_blocks=False,
    )
    issues = _frozen_issues(case, account, wallet, lots)
    new_available = bool(wallet is not None and (wallet.balance or positive))
    reasons = issues + current_reasons
    if new_available:
        reasons.append('NEW_AVAILABLE_BALANCE')
    reasons = list(dict.fromkeys(reasons))
    serious = issues or current_reasons
    status = 'REVIEW_REQUIRED' if serious else 'FROZEN'
    principal = values['PREPAID']
    return {
        'case_id': str(case.pk), 'account_id': account.pk, 'business_cycle': case.business_cycle,
        'account_label': account.nickname or account.username, 'status': status,
        'wallet_units': wallet.balance if wallet else None,
        'principal_units': case.principal_units + principal if principal is not None else None,
        'gift_units': values['GIFT'], 'compensation_units': values['COMPENSATION'],
        'points_hundredths': points, 'frozen_units': wallet.frozen_amount if wallet else None,
        'refund_fen': case.principal_units if status == 'FROZEN' else None,
        'paid_fen': 0, 'version': case.version, 'blocking_reasons': reasons,
        'updated_at': _updated_at(account, wallet, case).isoformat(),
    }, bool(not issues and current_complete and points is not None)


def _account_projection(account, *, case=None, lock=False, wallet=None):
    if wallet is None:
        rows = Wallet.objects.filter(account=account)
        if lock:
            rows = rows.select_for_update()
        wallet = rows.first()
    lots_query = PointFundingLot.objects.filter(account=account).order_by('id')
    if lock:
        lots_query = lots_query.select_for_update()
    lots = list(lots_query)
    points = _points(account)
    case = case or _latest_case(account, lock=lock)
    if case is not None and case.status == ClosureReturnCase.Status.FROZEN:
        return _frozen_row(case, account, wallet, lots, points)
    current_activity = bool(wallet is not None and (
        wallet.balance or wallet.frozen_amount or any(lot.remaining > 0 for lot in lots)
    ))
    if case is not None and case.status == ClosureReturnCase.Status.PAID and not current_activity:
        return _case_row(case, account, wallet, points), bool(wallet and points is not None)
    return _live_row(account, wallet, lots, points, case)


def closure_return_row(account, *, case=None, lock=False):
    return _account_projection(account, case=case, lock=lock)[0]


def closure_return_list(*, page=1, page_size=20):
    accounts = ClubAccount.objects.filter(account_type=ClubAccount.AccountType.BOSS).order_by('id')
    total = accounts.count()
    all_rows = []
    complete = True
    counts = {'READY_TO_PREPARE': 0, 'REVIEW_REQUIRED': 0, 'FROZEN': 0, 'PAID': 0, 'RELEASED': 0}
    # Store-wide summary deliberately includes accounts outside the visible page.
    for account in accounts:
        try:
            row, row_complete = _account_projection(account)
        except (DatabaseError, InterfaceError):
            raise
        except Exception:
            row = {
                'case_id': None, 'account_id': account.pk, 'business_cycle': account.business_cycle,
                'account_label': account.nickname or account.username, 'status': 'REVIEW_REQUIRED',
                'wallet_units': None, 'principal_units': None, 'gift_units': None,
                'compensation_units': None, 'points_hundredths': None, 'frozen_units': None,
                'refund_fen': None, 'paid_fen': None, 'version': 0,
                'blocking_reasons': ['READ_INCOMPLETE'], 'updated_at': account.updated_at.isoformat(),
            }
            row_complete = False
        complete = complete and row_complete
        counts[row['status']] += 1
        all_rows.append(row)
    totals = None
    if complete:
        totals = {field: sum((row[field] or 0) for row in all_rows) for field in (
            'wallet_units', 'principal_units', 'gift_units', 'compensation_units',
            'points_hundredths', 'frozen_units', 'refund_fen', 'paid_fen',
        )}
    rows = all_rows[(page - 1) * page_size:page * page_size]
    return {
        'list': rows, 'total': total, 'page': page, 'page_size': page_size,
        'has_more': page * page_size < total,
        'summary': {'complete': complete, 'counts': counts, 'totals': totals},
    }


def _historical_row(case):
    kind = {
        'FROZEN': 'PREPARED', 'PAID': 'PAYMENT_RECORDED', 'RELEASED': 'RELEASED',
    }.get(case.status)
    event = case.events.filter(kind=kind).first() if kind else None
    if event is None or not isinstance(event.response, dict):
        raise ClosureReturnError('原周期返还回执待核对', 409, 'CLOSURE_RETURN_HISTORY_INVALID')
    return {key: event.response.get(key) for key in ROW_FIELDS}


def closure_return_detail(case_id):
    case = ClosureReturnCase.objects.select_related('account').filter(pk=case_id).first()
    if case is None:
        raise ClosureReturnError('返还单不存在', 404, 'CLOSURE_RETURN_NOT_FOUND')
    account = ClubAccount.objects.filter(pk=case.account_id, account_type='BOSS').first()
    if account is None:
        raise ClosureReturnError('返还账号不存在或类型已变化', 409, 'CLOSURE_RETURN_ACCOUNT_REVIEW')
    if (account.business_cycle != case.business_cycle
            or case.status in (ClosureReturnCase.Status.PAID, ClosureReturnCase.Status.RELEASED)):
        row = _historical_row(case)
    else:
        row = closure_return_row(account, case=case)
    events = [{
        'event_id': str(event.pk), 'kind': event.kind,
        'client_request_id': event.client_request_id, 'platform_actor_id': str(event.platform_actor_id),
        'from_status': event.from_status, 'to_status': event.to_status,
        'detail': event.detail, 'created_at': event.created_at.isoformat(),
    } for event in case.events.order_by('created_at', 'id')]
    return {**row, 'events': events, 'channel_status': 'NOT_REQUESTED'}


def _revalidate_control(actor, store):
    current = Store.objects.filter(pk=store.pk, slug=store.slug, database_alias=store.database_alias).first()
    if current is None:
        raise ClosureReturnError('门店身份已变化', 409, 'STORE_IDENTITY_CONFLICT')
    if not platform_may(actor, 'closure:manage', store_slug=current.slug):
        raise ClosureReturnError('缺少该门店的闭店管理权限', 403, 'PLATFORM_FORBIDDEN')
    try:
        status = read_platform_closure(current)['status']
    except ClosureError as exc:
        raise ClosureReturnError(str(exc), exc.status, exc.code) from exc
    if status != 'SETTLEMENT_PENDING':
        raise ClosureReturnError('门店尚未进入待结清状态', 409, 'CLOSURE_NOT_SETTLEMENT_PENDING')


def _lock_wallet_account(account_id, cycle, *, wallet_id=None):
    rows = Wallet.objects.select_for_update().filter(account_id=account_id)
    if wallet_id is not None:
        rows = rows.filter(pk=wallet_id)
    wallet = rows.first()
    if wallet is None:
        raise ClosureReturnError('老板钱包不存在或归属不一致', 409, 'CLOSURE_RETURN_WALLET_REVIEW')
    account = ClubAccount.objects.select_for_update().filter(
        pk=account_id, account_type=ClubAccount.AccountType.BOSS,
    ).first()
    if account is None:
        raise ClosureReturnError('老板账号不存在', 404, 'CLOSURE_RETURN_ACCOUNT_NOT_FOUND')
    if type(cycle) is not int or cycle <= 0 or account.business_cycle != cycle:
        raise ClosureReturnError('老板账号业务周期已变化，请重新清点', 409, 'CLOSURE_RETURN_CYCLE_CONFLICT')
    return wallet, account


def _prior(request_id, fingerprint, kind, *, actor, case_id=None, account_id=None):
    prior = ClosureReturnEvent.objects.select_for_update().select_related('case').filter(
        client_request_id=request_id,
    ).first()
    if prior is None:
        return None
    valid = bool(
        prior.kind == kind and prior.platform_actor_id == actor.pk
        and (case_id is None or str(prior.case_id) == str(case_id))
        and (account_id is None or prior.case.account_id == account_id)
        and hmac.compare_digest(prior.request_fingerprint, fingerprint)
        and isinstance(prior.response, dict)
    )
    if not valid:
        raise ClosureReturnError('请求编号已用于其他返还内容', 409, 'REQUEST_ID_CONFLICT')
    return {**prior.response, 'replayed': True}, prior


def _event(case, kind, request_id, fingerprint, actor, from_status, to_status, detail, response):
    event = ClosureReturnEvent.objects.create(
        case=case, kind=kind, client_request_id=request_id, request_fingerprint=fingerprint,
        platform_actor_id=actor.pk, from_status=from_status, to_status=to_status,
        detail=detail, response=response,
    )
    ClosureReturnOutbox.objects.create(event=event)
    return event


def prepare_return(actor, store, data):
    if not isinstance(data, dict) or set(data) != {
        'account_id', 'business_cycle', 'expected_wallet_units', 'expected_principal_units', 'client_request_id',
    }:
        raise ClosureReturnError('请严格填写账号、周期、预期钱包、预期本金和请求编号')
    account_id = _integer(data['account_id'], 'account_id', positive=True)
    cycle = _integer(data['business_cycle'], 'business_cycle', positive=True)
    expected_wallet = _integer(data['expected_wallet_units'], 'expected_wallet_units')
    expected_principal = _integer(data['expected_principal_units'], 'expected_principal_units')
    request_id = _request_id(data['client_request_id'])
    values = {
        'account_id': account_id, 'business_cycle': cycle,
        'expected_wallet_units': expected_wallet, 'expected_principal_units': expected_principal,
    }
    fingerprint = _fingerprint(store.pk, actor.pk, 'PREPARED', values)
    try:
        with store_context(store), transaction.atomic():
            _revalidate_control(actor, store)
            # A durable original receipt precedes every current-cycle check.
            replay = _prior(request_id, fingerprint, 'PREPARED', actor=actor, account_id=account_id)
            if replay:
                return replay[0], False, replay[1].pk
            wallet, account = _lock_wallet_account(account_id, cycle)
            active = ClosureReturnCase.objects.select_for_update().filter(
                account=account, business_cycle=cycle, active_key='ACTIVE',
            ).first()
            if active is not None:
                raise ClosureReturnError('该账号周期已有冻结中的返还单', 409, 'CLOSURE_RETURN_CASE_CONFLICT')
            row, complete = _account_projection(account, lock=True, wallet=wallet)
            if not complete or row['status'] != 'READY_TO_PREPARE':
                raise ClosureReturnError('账户现金来源不完整、含非本金或暂无可退余额', 409,
                                         'CLOSURE_RETURN_REVIEW_REQUIRED')
            if row['wallet_units'] != expected_wallet or row['principal_units'] != expected_principal:
                raise ClosureReturnError('钱包或本金已变化，请重新清点', 409, 'CLOSURE_RETURN_EXPECTATION_CONFLICT')
            if expected_principal <= 0:
                raise ClosureReturnError('零本金无需建立返还冻结单', 409, 'CLOSURE_RETURN_EMPTY')
            lots = list(PointFundingLot.objects.select_for_update().filter(
                account=account, remaining__gt=0,
            ).order_by('id'))
            if wallet.frozen_amount + expected_principal > MAX_UNITS:
                raise ClosureReturnError('冻结金额超出账本上限', 409, 'CLOSURE_RETURN_LIMIT_EXCEEDED')
            balance_before, frozen_before = wallet.balance, wallet.frozen_amount
            freeze = Transaction.objects.create(
                wallet=wallet, tx_type=Transaction.TxType.CLOSURE_FREEZE, amount=-expected_principal,
                balance_before=balance_before, balance_after=balance_before - expected_principal,
                status=Transaction.Status.SUCCESS, remark='闭店老板纯本金返还冻结',
            )
            case = ClosureReturnCase.objects.create(
                account=account, business_cycle=cycle, wallet=wallet, status='FROZEN', active_key='ACTIVE',
                principal_units=expected_principal, version=1, freeze_transaction=freeze, prepared_by=actor.pk,
            )
            snapshots = []
            for lot in lots:
                snapshots.append(ClosureReturnLot(
                    case=case, source_lot=lot, source_key=lot.source_key, source_kind=lot.kind,
                    remaining_units=lot.remaining, principal_units=lot.remaining, gift_units=0,
                ))
            ClosureReturnLot.objects.bulk_create(snapshots)
            for lot in lots:
                changed = PointFundingLot.objects.filter(pk=lot.pk, remaining=lot.remaining).update(remaining=0)
                if changed != 1:
                    raise ClosureReturnError('本金批次发生并发变化', 409, 'CLOSURE_RETURN_LOT_MISMATCH')
            changed = Wallet.objects.filter(pk=wallet.pk, balance=balance_before, frozen_amount=frozen_before).update(
                balance=balance_before - expected_principal,
                frozen_amount=frozen_before + expected_principal, updated_at=timezone.now(),
            )
            if changed != 1:
                raise ClosureReturnError('钱包发生并发变化，请按原请求核对', 409, 'CLOSURE_RETURN_WALLET_CONFLICT')
            wallet.refresh_from_db()
            row = closure_return_row(account, case=case)
            response = {
                **row, 'replayed': False, 'wallet_balance': wallet.balance,
                'wallet_frozen_units': wallet.frozen_amount,
            }
            event = _event(case, 'PREPARED', request_id, fingerprint, actor, 'READY_TO_PREPARE', 'FROZEN', {
                'account_id': account.pk, 'business_cycle': cycle, 'principal_units': expected_principal,
                'refund_fen': expected_principal, 'wallet_before': balance_before,
                'wallet_after': wallet.balance, 'frozen_before': frozen_before,
                'frozen_after': wallet.frozen_amount, 'lot_ids': [lot.source_lot_id for lot in snapshots],
                'freeze_transaction_id': freeze.pk,
            }, response)
            return response, True, event.pk
    except IntegrityError as exc:
        raise ClosureReturnError('返还单或请求编号发生竞争，请用原请求编号核对', 409,
                                 'CLOSURE_RETURN_CONCURRENCY_CONFLICT') from exc


def _locked_case(case_id):
    initial = ClosureReturnCase.objects.filter(pk=case_id).only(
        'account_id', 'business_cycle', 'wallet_id',
    ).first()
    if initial is None:
        raise ClosureReturnError('返还单不存在', 404, 'CLOSURE_RETURN_NOT_FOUND')
    wallet, account = _lock_wallet_account(
        initial.account_id, initial.business_cycle, wallet_id=initial.wallet_id,
    )
    case = ClosureReturnCase.objects.select_for_update().select_related('freeze_transaction').filter(
        pk=case_id, account=account, business_cycle=initial.business_cycle, wallet=wallet,
    ).first()
    if case is None:
        raise ClosureReturnError('返还单账号或周期不一致', 409, 'CLOSURE_RETURN_HISTORY_INVALID')
    if case.migration_review_required:
        raise ClosureReturnError('旧返还单迁移证据待人工核对，禁止实付或解除', 409, 'MIGRATION_REVIEW_REQUIRED')
    return case, account, wallet


def _validate_frozen_sources(case, account, wallet):
    if wallet.frozen_amount < case.principal_units:
        raise ClosureReturnError('原钱包冻结金额待核对', 409, 'CLOSURE_RETURN_WALLET_REVIEW')
    freeze = Transaction.objects.select_for_update().filter(pk=case.freeze_transaction_id, wallet=wallet).first()
    if (freeze is None or freeze.tx_type != 'CLOSURE_FREEZE' or freeze.status != 'SUCCESS'
            or freeze.amount != -case.principal_units
            or freeze.balance_after != freeze.balance_before - case.principal_units):
        raise ClosureReturnError('原冻结流水待核对', 409, 'CLOSURE_RETURN_FREEZE_REVIEW')
    snapshots = list(case.lots.order_by('source_lot_id'))
    ids = [row.source_lot_id for row in snapshots]
    lots = list(PointFundingLot.objects.select_for_update().filter(account=account, pk__in=ids).order_by('id'))
    if len(lots) != len(snapshots) or not snapshots or sum(row.principal_units for row in snapshots) != case.principal_units:
        raise ClosureReturnError('原本金冻结份额不完整', 409, 'CLOSURE_RETURN_LOT_MISMATCH')
    by_id = {lot.pk: lot for lot in lots}
    for snapshot in snapshots:
        lot = by_id.get(snapshot.source_lot_id)
        if (lot is None or lot.source_key != snapshot.source_key or lot.kind != snapshot.source_kind
                or snapshot.gift_units != 0 or snapshot.principal_units != snapshot.remaining_units):
            raise ClosureReturnError('原本金批次身份已变化，冻结保持不变', 409, 'CLOSURE_RETURN_LOT_MISMATCH')
    return snapshots, by_id


def record_payment(actor, store, case_id, data):
    if not isinstance(data, dict) or set(data) != {
        'amount_fen', 'external_reference', 'proof_sha256', 'occurred_at', 'client_request_id',
    }:
        raise ClosureReturnError('请严格填写实付金额、外部编号、凭证摘要、发生时间和请求编号')
    amount = _integer(data['amount_fen'], 'amount_fen', positive=True)
    masked = mask_external_reference(data['external_reference'])
    normalized_reference = unicodedata.normalize('NFKC', data['external_reference']).strip()
    reference_digest = hashlib.sha256(normalized_reference.casefold().encode('utf-8')).hexdigest()
    proof = _proof(data['proof_sha256'])
    occurred_at = data['occurred_at']
    if not isinstance(occurred_at, datetime) or timezone.is_naive(occurred_at):
        raise ClosureReturnError('occurred_at 必须包含时区')
    occurred_at = occurred_at.astimezone(datetime_timezone.utc)
    request_id = _request_id(data['client_request_id'])
    values = {
        'case_id': str(case_id), 'amount_fen': amount, 'external_reference': normalized_reference,
        'proof_sha256': proof, 'occurred_at': occurred_at.isoformat(),
    }
    fingerprint = _fingerprint(store.pk, actor.pk, 'PAYMENT_RECORDED', values)
    try:
        with store_context(store), transaction.atomic():
            _revalidate_control(actor, store)
            replay = _prior(request_id, fingerprint, 'PAYMENT_RECORDED', actor=actor, case_id=case_id)
            if replay:
                return replay[0], False, replay[1].pk
            case, account, wallet = _locked_case(case_id)
            if case.status != ClosureReturnCase.Status.FROZEN:
                raise ClosureReturnError('仅冻结中的返还单可登记实付', 409, 'CLOSURE_RETURN_STATE_CONFLICT')
            if amount != case.principal_units:
                raise ClosureReturnError('只允许登记与原冻结本金相等的全额实付', 409, 'CLOSURE_RETURN_AMOUNT_CONFLICT')
            _snapshots, _lots = _validate_frozen_sources(case, account, wallet)
            frozen_before = wallet.frozen_amount
            changed = Wallet.objects.filter(pk=wallet.pk, balance=wallet.balance, frozen_amount=frozen_before).update(
                frozen_amount=frozen_before - case.principal_units, updated_at=timezone.now(),
            )
            if changed != 1:
                raise ClosureReturnError('钱包发生并发变化', 409, 'CLOSURE_RETURN_WALLET_CONFLICT')
            wallet.refresh_from_db()
            payment = Transaction.objects.create(
                wallet=wallet, tx_type=Transaction.TxType.CLOSURE_PAYMENT, amount=0,
                balance_before=wallet.balance, balance_after=wallet.balance,
                status=Transaction.Status.SUCCESS, remark=f'闭店本金线下实付登记，扣减原冻结：{case.pk}',
                external_tx_id=reference_digest[:64],
            )
            now = timezone.now()
            changed = ClosureReturnCase.objects.filter(pk=case.pk, status='FROZEN', version=case.version).update(
                status='PAID', active_key=None, payment_transaction=payment, paid_fen=amount,
                proof_sha256=proof, proof_ref=f'closure-proof:{case.pk}', external_reference_masked=masked,
                external_reference_sha256=reference_digest, offline_occurred_at=occurred_at,
                paid_by=actor.pk, paid_at=now, version=case.version + 1, updated_at=now,
            )
            if changed != 1:
                raise ClosureReturnError('实付与解除发生竞争，请核对原事件', 409, 'CLOSURE_RETURN_STATE_CONFLICT')
            case.refresh_from_db()
            row = _case_row(case, account, wallet, _points(account))
            response = {
                **row, 'channel_status': 'NOT_REQUESTED', 'replayed': False,
                'wallet_balance': wallet.balance, 'wallet_frozen_units': wallet.frozen_amount,
            }
            event = _event(case, 'PAYMENT_RECORDED', request_id, fingerprint, actor, 'FROZEN', 'PAID', {
                'amount_fen': amount, 'external_reference_masked': masked,
                'external_reference_sha256': reference_digest, 'proof_sha256': proof,
                'proof_ref': case.proof_ref, 'occurred_at': occurred_at.isoformat(),
                'frozen_before': frozen_before, 'frozen_after': wallet.frozen_amount,
                'payment_transaction_id': payment.pk, 'channel_status': 'NOT_REQUESTED',
            }, response)
            return response, True, event.pk
    except IntegrityError as exc:
        raise ClosureReturnError('实付登记发生竞争，请用原请求编号核对', 409,
                                 'CLOSURE_RETURN_CONCURRENCY_CONFLICT') from exc


def release_return(actor, store, case_id, data):
    if not isinstance(data, dict) or set(data) != {'reason', 'client_request_id'}:
        raise ClosureReturnError('请严格填写解除原因和请求编号')
    reason = data['reason']
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise ClosureReturnError('请填写 500 字以内的解除原因')
    reason = reason.strip()
    request_id = _request_id(data['client_request_id'])
    fingerprint = _fingerprint(store.pk, actor.pk, 'RELEASED', {'case_id': str(case_id), 'reason': reason})
    try:
        with store_context(store), transaction.atomic():
            _revalidate_control(actor, store)
            replay = _prior(request_id, fingerprint, 'RELEASED', actor=actor, case_id=case_id)
            if replay:
                return replay[0], False, replay[1].pk
            case, account, wallet = _locked_case(case_id)
            if case.status != ClosureReturnCase.Status.FROZEN:
                raise ClosureReturnError('已实付或非冻结返还单不可解除', 409, 'CLOSURE_RETURN_STATE_CONFLICT')
            snapshots, lots = _validate_frozen_sources(case, account, wallet)
            if wallet.balance + case.principal_units > MAX_UNITS:
                raise ClosureReturnError('解除后钱包金额超出账本上限', 409, 'CLOSURE_RETURN_LIMIT_EXCEEDED')
            balance_before, frozen_before = wallet.balance, wallet.frozen_amount
            for snapshot in snapshots:
                lot = lots[snapshot.source_lot_id]
                if lot.remaining + snapshot.principal_units > MAX_UNITS:
                    raise ClosureReturnError('解除后批次金额超出账本上限', 409, 'CLOSURE_RETURN_LIMIT_EXCEEDED')
                changed = PointFundingLot.objects.filter(pk=lot.pk, remaining=lot.remaining).update(
                    remaining=lot.remaining + snapshot.principal_units,
                )
                if changed != 1:
                    raise ClosureReturnError('原批次发生并发变化', 409, 'CLOSURE_RETURN_LOT_MISMATCH')
            changed = Wallet.objects.filter(pk=wallet.pk, balance=balance_before, frozen_amount=frozen_before).update(
                balance=balance_before + case.principal_units,
                frozen_amount=frozen_before - case.principal_units, updated_at=timezone.now(),
            )
            if changed != 1:
                raise ClosureReturnError('钱包发生并发变化', 409, 'CLOSURE_RETURN_WALLET_CONFLICT')
            wallet.refresh_from_db()
            release = Transaction.objects.create(
                wallet=wallet, tx_type=Transaction.TxType.CLOSURE_RELEASE, amount=case.principal_units,
                balance_before=balance_before, balance_after=wallet.balance,
                status=Transaction.Status.SUCCESS, reversal_of=case.freeze_transaction,
                remark=f'闭店本金未实付解除：{case.pk}',
            )
            now = timezone.now()
            changed = ClosureReturnCase.objects.filter(pk=case.pk, status='FROZEN', version=case.version).update(
                status='RELEASED', active_key=None, release_transaction=release, released_by=actor.pk,
                released_at=now, release_reason=reason, version=case.version + 1, updated_at=now,
            )
            if changed != 1:
                raise ClosureReturnError('实付与解除发生竞争，请核对原事件', 409, 'CLOSURE_RETURN_STATE_CONFLICT')
            case.refresh_from_db()
            row = _case_row(case, account, wallet, _points(account))
            response = {
                **row, 'replayed': False, 'wallet_balance': wallet.balance,
                'wallet_frozen_units': wallet.frozen_amount,
            }
            event = _event(case, 'RELEASED', request_id, fingerprint, actor, 'FROZEN', 'RELEASED', {
                'reason': reason, 'principal_units': case.principal_units,
                'wallet_before': balance_before, 'wallet_after': wallet.balance,
                'frozen_before': frozen_before, 'frozen_after': wallet.frozen_amount,
                'release_transaction_id': release.pk, 'original_freeze_transaction_id': case.freeze_transaction_id,
            }, response)
            return response, True, event.pk
    except IntegrityError as exc:
        raise ClosureReturnError('解除发生竞争，请用原请求编号核对', 409,
                                 'CLOSURE_RETURN_CONCURRENCY_CONFLICT') from exc
