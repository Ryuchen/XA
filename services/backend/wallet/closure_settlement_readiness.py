"""Read-only, store-local E3 funds preflight; never a finalization grant."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from django.db import DatabaseError, InterfaceError
from django.utils import timezone

from club_accounts.models import ClubAccount
from stores.context import store_context
from stores.db import transaction
from stores.models import PlatformAuditEvent

from .closure_return_audit import _matches as audit_matches, read_event
from .closure_returns import closure_return_list
from .models import ClosureReturnCase, ClosureReturnOutbox


SHA256 = re.compile(r'[0-9a-f]{64}')
BUSINESS_BLOCKERS = {
    'EMPTY_ACCOUNT_LIST', 'READY_TO_PREPARE', 'FROZEN', 'NON_PRINCIPAL_BALANCE',
    'NEW_AVAILABLE_BALANCE', 'PAID_WITH_NEW_BALANCE', 'RELEASED_WITH_BALANCE',
}
AMOUNT_FIELDS = (
    'wallet_units', 'principal_units', 'gift_units', 'compensation_units',
    'points_hundredths', 'frozen_units', 'paid_fen',
)


def _blocker(code, *, case=None, account_id=None, business_cycle=None):
    if case is not None:
        return {
            'code': code, 'object_type': 'CLOSURE_RETURN_CASE', 'object_id': str(case.pk),
            'account_id': case.account_id, 'business_cycle': case.business_cycle,
        }
    row = {'code': code, 'object_type': 'BOSS_ACCOUNT' if account_id is not None else 'STORE'}
    if account_id is not None:
        row['object_id'] = str(account_id)
        row['account_id'] = account_id
    if business_cycle is not None:
        row['business_cycle'] = business_cycle
    return row


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _read_failure(store):
    blockers = [{'code': 'READ_INCOMPLETE', 'object_type': 'STORE', 'object_id': str(store.pk)}]
    facts = {
        'version': 'E3_FUNDS_V1',
        'store': {'id': store.pk, 'slug': store.slug, 'database_alias': store.database_alias},
        'blockers': blockers,
    }
    return {
        'status': 'UNKNOWN', 'complete': False, 'counts': None, 'totals': None,
        'blockers': blockers, 'facts_sha256': _digest(facts),
    }


def _common_case_errors(case):
    errors = []
    freeze = case.freeze_transaction
    if (freeze is None or freeze.wallet_id != case.wallet_id or freeze.tx_type != 'CLOSURE_FREEZE'
            or freeze.status != 'SUCCESS' or freeze.amount != -case.principal_units
            or freeze.balance_after != freeze.balance_before - case.principal_units):
        errors.append('FREEZE_TRANSACTION_INVALID')
    snapshots = list(case.lots.select_related('source_lot').order_by('id'))
    if (not snapshots or sum(row.principal_units for row in snapshots) != case.principal_units
            or any(row.gift_units != 0 or row.principal_units != row.remaining_units for row in snapshots)):
        errors.append('FROZEN_SNAPSHOT_INVALID')
    for row in snapshots:
        lot = row.source_lot
        if (lot.account_id != case.account_id or lot.source_key != row.source_key
                or lot.kind != row.source_kind):
            errors.append('FROZEN_SNAPSHOT_INVALID')
            break
    return errors


def _prepare_errors(case, events):
    prepare = events.get('PREPARED')
    if (prepare is None or prepare.from_status != 'READY_TO_PREPARE' or prepare.to_status != 'FROZEN'
            or prepare.platform_actor_id != case.prepared_by or not isinstance(prepare.detail, dict)):
        return ['PREPARE_EVENT_INVALID']
    detail = prepare.detail
    if (detail.get('account_id') != case.account_id
            or detail.get('business_cycle') != case.business_cycle
            or detail.get('principal_units') != case.principal_units
            or detail.get('refund_fen') != case.principal_units
            or detail.get('freeze_transaction_id') != case.freeze_transaction_id):
        return ['PREPARE_EVENT_INVALID']
    return []


def _audit_errors(store, case):
    """Read tenant event/outbox facts and matching control audits without repair."""
    errors = []
    event_facts = []
    events = list(case.events.order_by('created_at', 'id'))
    if not events:
        return ['CASE_EVENTS_MISSING'], event_facts
    for event in events:
        event_facts.append({'event_id': str(event.pk), 'kind': event.kind})
        try:
            facts = read_event(store, event.pk)
        except (ValueError, AttributeError, TypeError):
            errors.append('RETURN_EVENT_INVALID')
            continue
        outbox = ClosureReturnOutbox.objects.filter(event_id=event.pk).first()
        if outbox is None:
            errors.append('RETURN_OUTBOX_MISSING')
            continue
        event_facts[-1]['outbox_status'] = outbox.status
        if outbox.status == 'PENDING':
            errors.append('RETURN_OUTBOX_PENDING')
            continue
        if outbox.status == 'REVIEW':
            errors.append('RETURN_AUDIT_REVIEW')
            continue
        if outbox.status != 'RECORDED':
            errors.append('RETURN_OUTBOX_INVALID')
            continue
        event_facts[-1]['audit_id'] = outbox.audit_id
        if outbox.audit_id is None:
            errors.append('RETURN_AUDIT_MISSING')
            continue
        audits = list(PlatformAuditEvent.objects.filter(
            action=facts['action'], target_store_slug=store.slug,
            target_type='closure_return_event', target_id=facts['event_id'],
        ).order_by('id')[:2])
        if not audits:
            errors.append('RETURN_AUDIT_MISSING')
        elif len(audits) != 1 or audits[0].pk != outbox.audit_id or not audit_matches(audits[0], store, facts):
            errors.append('RETURN_AUDIT_MISMATCH')
    return list(dict.fromkeys(errors)), event_facts


def _paid_errors(case, events):
    errors = _common_case_errors(case) + _prepare_errors(case, events)
    if len(events) != 2 or set(events) != {'PREPARED', 'PAYMENT_RECORDED'}:
        errors.append('PAYMENT_EVENT_INVALID')
    event = events.get('PAYMENT_RECORDED')
    payment = case.payment_transaction
    if (payment is None or payment.wallet_id != case.wallet_id or payment.tx_type != 'CLOSURE_PAYMENT'
            or payment.status != 'SUCCESS' or payment.amount != 0
            or payment.balance_after != payment.balance_before
            or payment.reversal_of_id is not None or payment.reversals.exists()):
        errors.append('PAYMENT_TRANSACTION_INVALID')
    if (case.paid_fen != case.principal_units or not case.paid_at or timezone.is_naive(case.paid_at)
            or not case.offline_occurred_at or timezone.is_naive(case.offline_occurred_at) or not case.paid_by):
        errors.append('PAYMENT_AMOUNT_INVALID')
    if (not SHA256.fullmatch(case.proof_sha256 or '') or not case.proof_ref
            or case.proof_ref != f'closure-proof:{case.pk}'
            or not SHA256.fullmatch(case.external_reference_sha256 or '')
            or not case.external_reference_masked):
        errors.append('PAYMENT_PROOF_INVALID')
    if payment is not None and payment.external_tx_id != case.external_reference_sha256:
        errors.append('PAYMENT_TRANSACTION_INVALID')
    if event is None:
        return list(dict.fromkeys(errors))
    detail = event.detail
    response = event.response
    if (event.from_status != 'FROZEN' or event.to_status != 'PAID'
            or event.platform_actor_id != case.paid_by or not isinstance(detail, dict)
            or detail.get('amount_fen') != case.paid_fen
            or detail.get('proof_sha256') != case.proof_sha256
            or detail.get('proof_ref') != case.proof_ref
            or detail.get('external_reference_sha256') != case.external_reference_sha256
            or detail.get('external_reference_masked') != case.external_reference_masked
            or detail.get('occurred_at') != case.offline_occurred_at.isoformat()
            or detail.get('payment_transaction_id') != case.payment_transaction_id
            or detail.get('channel_status') != 'NOT_REQUESTED'):
        errors.append('PAYMENT_EVENT_INVALID')
    if (not isinstance(response, dict) or response.get('case_id') != str(case.pk)
            or response.get('business_cycle') != case.business_cycle
            or response.get('status') != 'PAID' or response.get('paid_fen') != case.paid_fen
            or response.get('refund_fen') != case.principal_units
            or response.get('principal_units') != case.principal_units
            or response.get('channel_status') != 'NOT_REQUESTED'):
        errors.append('PAYMENT_EVENT_INVALID')
    return list(dict.fromkeys(errors))


def _release_errors(case, events):
    errors = _common_case_errors(case) + _prepare_errors(case, events)
    if len(events) != 2 or set(events) != {'PREPARED', 'RELEASED'}:
        errors.append('RELEASE_EVENT_INVALID')
    release = case.release_transaction
    if (release is None or release.wallet_id != case.wallet_id or release.tx_type != 'CLOSURE_RELEASE'
            or release.status != 'SUCCESS' or release.amount != case.principal_units
            or release.balance_after != release.balance_before + case.principal_units
            or release.reversal_of_id != case.freeze_transaction_id):
        errors.append('RELEASE_TRANSACTION_INVALID')
    if (not case.released_at or timezone.is_naive(case.released_at) or not case.released_by
            or not case.release_reason):
        errors.append('RELEASE_EVENT_INVALID')
    event = events.get('RELEASED')
    if event is None:
        return list(dict.fromkeys(errors))
    detail = event.detail
    response = event.response
    if (event.from_status != 'FROZEN' or event.to_status != 'RELEASED'
            or event.platform_actor_id != case.released_by or not isinstance(detail, dict)
            or detail.get('reason') != case.release_reason
            or detail.get('principal_units') != case.principal_units
            or detail.get('release_transaction_id') != case.release_transaction_id
            or detail.get('original_freeze_transaction_id') != case.freeze_transaction_id):
        errors.append('RELEASE_EVENT_INVALID')
    if (not isinstance(response, dict) or response.get('case_id') != str(case.pk)
            or response.get('business_cycle') != case.business_cycle
            or response.get('status') != 'RELEASED' or response.get('paid_fen') != 0
            or response.get('refund_fen') is not None
            or release is not None and response.get('wallet_balance') != release.balance_after):
        errors.append('RELEASE_EVENT_INVALID')
    return list(dict.fromkeys(errors))


def _row_facts(row):
    return {key: row.get(key) for key in (
        'case_id', 'account_id', 'business_cycle', 'status', 'wallet_units', 'principal_units',
        'gift_units', 'compensation_units', 'points_hundredths', 'frozen_units', 'refund_fen',
        'paid_fen', 'version', 'blocking_reasons',
    )}


def _build_funds(store):
    account_total = ClubAccount.objects.filter(account_type=ClubAccount.AccountType.BOSS).count()
    # The existing function always computes all rows before slicing. Dynamic
    # sizing removes external pagination and any hidden later page; a changed
    # account set becomes an explicit incomplete fact instead of a partial scan.
    listing = closure_return_list(page=1, page_size=max(1, account_total))
    rows = listing['list']
    blockers = []
    facts_accounts = []
    blocked_accounts = set()
    complete = bool(listing['summary']['complete'])
    if (listing['total'] != account_total or listing['has_more']
            or len(rows) != account_total or listing['page'] != 1):
        blockers.append(_blocker('ACCOUNT_SET_CHANGED'))
        complete = False
    if not rows:
        blockers.append(_blocker('EMPTY_ACCOUNT_LIST'))
    status_counts = {key: listing['summary']['counts'].get(key, 0)
                     for key in ('READY_TO_PREPARE', 'REVIEW_REQUIRED', 'FROZEN', 'PAID', 'RELEASED')}
    for row in rows:
        facts_accounts.append(_row_facts(row))
        account_id = row.get('account_id')
        cycle = row.get('business_cycle')
        status = row.get('status')
        reasons = list(row.get('blocking_reasons') or [])
        if status == 'READY_TO_PREPARE':
            blockers.append(_blocker('READY_TO_PREPARE', account_id=account_id, business_cycle=cycle))
            blocked_accounts.add(account_id)
        elif status == 'FROZEN':
            blockers.append(_blocker('FROZEN', account_id=account_id, business_cycle=cycle))
            blocked_accounts.add(account_id)
        elif status == 'REVIEW_REQUIRED':
            for reason in reasons:
                if reason != 'ZERO_BALANCE':
                    blockers.append(_blocker(reason, account_id=account_id, business_cycle=cycle))
                    blocked_accounts.add(account_id)
            if not reasons or (set(reasons) != {'ZERO_BALANCE'}):
                if not reasons:
                    blockers.append(_blocker('FUNDS_REVIEW_REQUIRED', account_id=account_id, business_cycle=cycle))
                    blocked_accounts.add(account_id)
        elif status not in ('PAID', 'RELEASED'):
            blockers.append(_blocker('FUNDS_STATE_UNKNOWN', account_id=account_id, business_cycle=cycle))
            blocked_accounts.add(account_id)
        if any(row.get(field) is None for field in AMOUNT_FIELDS):
            blockers.append(_blocker('AMOUNT_UNKNOWN', account_id=account_id, business_cycle=cycle))
            blocked_accounts.add(account_id)
            complete = False
        if (row.get('gift_units') or 0) > 0 or (row.get('compensation_units') or 0) > 0:
            blockers.append(_blocker('NON_PRINCIPAL_BALANCE', account_id=account_id, business_cycle=cycle))
            blocked_accounts.add(account_id)
        if 'NEW_AVAILABLE_BALANCE' in reasons:
            blockers.append(_blocker('NEW_AVAILABLE_BALANCE', account_id=account_id, business_cycle=cycle))
            blocked_accounts.add(account_id)
    if not listing['summary']['complete']:
        complete = False

    cases = list(ClosureReturnCase.objects.select_related(
        'wallet', 'freeze_transaction', 'payment_transaction', 'release_transaction',
    ).order_by('account_id', 'business_cycle', 'created_at', 'id'))
    valid_paid, valid_released = set(), set()
    case_facts = []
    external_groups = defaultdict(list)
    rows_by_account = {row['account_id']: row for row in rows}
    for case in cases:
        events = {event.kind: event for event in case.events.all()}
        if len(events) != case.events.count():
            case_errors = ['CASE_EVENT_INVALID']
        else:
            case_errors = []
        case_errors.extend(_common_case_errors(case))
        if case.migration_review_required:
            case_errors.append('MIGRATION_REVIEW_REQUIRED')
        if case.status == 'PAID':
            case_errors.extend(_paid_errors(case, events))
            if case.external_reference_sha256:
                external_groups[case.external_reference_sha256].append(case)
        elif case.status == 'RELEASED':
            case_errors.extend(_release_errors(case, events))
        elif case.status == 'FROZEN':
            case_errors.extend(_prepare_errors(case, events))
            if len(events) != 1:
                case_errors.append('FROZEN_EVENT_INVALID')
            case_errors.append('FROZEN')
        else:
            case_errors.append('FUNDS_STATE_UNKNOWN')
        audit_errors, event_facts = _audit_errors(store, case)
        case_errors.extend(audit_errors)
        case_errors = list(dict.fromkeys(case_errors))
        for code in case_errors:
            blockers.append(_blocker(code, case=case))
            blocked_accounts.add(case.account_id)
        if not case_errors:
            if case.status == 'PAID':
                valid_paid.add(str(case.pk))
            elif case.status == 'RELEASED':
                valid_released.add(str(case.pk))
        current = rows_by_account.get(case.account_id)
        if current and current['business_cycle'] == case.business_cycle:
            if case.status == 'PAID' and (current.get('wallet_units') or 0) > 0:
                blockers.append(_blocker('PAID_WITH_NEW_BALANCE', case=case))
                blocked_accounts.add(case.account_id)
            if case.status == 'RELEASED' and (current.get('wallet_units') or 0) > 0:
                blockers.append(_blocker('RELEASED_WITH_BALANCE', case=case))
                blocked_accounts.add(case.account_id)
        case_facts.append({
            'case_id': str(case.pk), 'account_id': case.account_id,
            'business_cycle': case.business_cycle, 'status': case.status,
            'principal_units': case.principal_units, 'paid_fen': case.paid_fen,
            'migration_review_required': case.migration_review_required,
            'migration_review_reason': case.migration_review_reason,
            'external_reference_sha256': case.external_reference_sha256,
            'events': event_facts,
        })
    for group in external_groups.values():
        if len(group) > 1:
            for case in group:
                blockers.append(_blocker('DUPLICATE_EXTERNAL_REFERENCE', case=case))
                blocked_accounts.add(case.account_id)
                valid_paid.discard(str(case.pk))

    completed = zero_balance = 0
    for row in rows:
        account_id = row['account_id']
        if account_id in blocked_accounts:
            continue
        case_id = row.get('case_id')
        if row['status'] == 'PAID' and case_id in valid_paid:
            if row.get('wallet_units') == 0 and row.get('frozen_units') == 0:
                completed += 1
            continue
        legal_zero = bool(
            row.get('wallet_units') == 0 and row.get('frozen_units') == 0
            and row.get('principal_units') == 0 and row.get('gift_units') == 0
            and row.get('compensation_units') == 0 and row.get('points_hundredths') is not None
            and set(row.get('blocking_reasons') or []) == {'ZERO_BALANCE'}
            and (case_id is None or case_id in valid_released or case_id in valid_paid)
        )
        if legal_zero:
            completed += 1
            zero_balance += 1

    if any(code not in BUSINESS_BLOCKERS for row in blockers for code in [row['code']]):
        complete = False
    blockers.sort(key=lambda row: (
        row['code'], row['object_type'], row.get('object_id', ''),
        row.get('account_id') or 0, row.get('business_cycle') or 0,
    ))
    counts = {
        **status_counts, 'accounts': account_total, 'completed': completed,
        'zero_balance': zero_balance, 'paid': len(valid_paid), 'released': len(valid_released),
    }
    totals = None
    if complete:
        totals = {field: sum((row.get(field) or 0) for row in rows) for field in (
            'wallet_units', 'principal_units', 'gift_units', 'compensation_units',
            'points_hundredths', 'frozen_units', 'refund_fen',
        )}
        totals['paid_fen'] = sum(case.paid_fen for case in cases if str(case.pk) in valid_paid)
    facts = {
        'version': 'E3_FUNDS_V1',
        'store': {'id': store.pk, 'slug': store.slug, 'database_alias': store.database_alias},
        'complete': complete, 'counts': counts, 'totals': totals,
        'accounts': facts_accounts, 'cases': case_facts, 'blockers': blockers,
    }
    return {
        'status': 'UNKNOWN' if not complete else 'BLOCKED' if blockers else 'CLEAR',
        'complete': complete, 'counts': counts, 'totals': totals,
        'blockers': blockers, 'facts_sha256': _digest(facts),
    }


def build_closure_funds_readiness(store):
    """Return only serializable funds facts; all database operations are reads."""
    try:
        with store_context(store), transaction.atomic():
            return {'funds': _build_funds(store)}
    except (DatabaseError, InterfaceError):
        # Transport/storage failures are unavailable reads, not unknown zeroes.
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        return {'funds': _read_failure(store)}
