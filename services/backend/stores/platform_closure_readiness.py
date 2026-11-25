"""Store-scoped, read-only E3 settlement preflight; never a finalization grant."""
from __future__ import annotations

import hashlib
import json
import re

from django.db import DatabaseError, InterfaceError
from django.utils import timezone

from wallet.closure_settlement_readiness import build_closure_funds_readiness

from .closure import ClosureError, read_platform_closure
from .context import store_context
from .platform_closure import PlatformClosureBase, _error, _response


OBLIGATION_CODES = (
    'ORDERS', 'RESERVATIONS', 'AFTERSALES', 'REPORTS', 'INCOME_HOLDS', 'WITHDRAWALS',
    'DEPOSIT_DEFICITS', 'RECOVERY_PERMITS', 'GIFT_APPROVALS', 'REWARD_CLAIMS',
    'PAYROLL', 'OTHER_RIGHTS',
)
TOTAL_FIELDS = (
    'wallet_units', 'principal_units', 'gift_units', 'compensation_units',
    'points_hundredths', 'frozen_units', 'refund_fen', 'paid_fen',
)
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,79}')
_RESOURCE = re.compile(r'[a-z][a-z0-9_-]{0,63}')
_REFERENCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
POLICY_BLOCKERS = [
    {'code': 'FEE_RULES_PENDING', 'resource': 'closure_policy', 'reference': 'PC04'},
    {'code': 'FINAL_WRITE_BARRIER_UNAVAILABLE', 'resource': 'closure_policy',
     'reference': 'shared-write-barrier'},
]


def closure_obligation_readiness(store):
    """Late import keeps the API module independently testable during rollout."""
    from .closure_obligations import closure_obligation_readiness as scan
    return scan(store)


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _read_blocker(store):
    return {'code': 'READ_INCOMPLETE', 'resource': 'store', 'reference': f'store:{store.pk}'}


def _public_blocker(row, store):
    if not isinstance(row, dict):
        return _read_blocker(store)
    code = row.get('code')
    resource = row.get('resource')
    reference = row.get('reference')
    if (isinstance(code, str) and _CODE.fullmatch(code)
            and isinstance(resource, str) and _RESOURCE.fullmatch(resource)
            and isinstance(reference, str) and _REFERENCE.fullmatch(reference)):
        return {'code': code, 'resource': resource, 'reference': reference}
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        return _read_blocker(store)
    object_type = row.get('object_type')
    object_id = row.get('object_id')
    if object_type == 'CLOSURE_RETURN_CASE' and object_id:
        return {'code': code, 'resource': 'closure_return_case', 'reference': f'case:{object_id}'}
    if object_type == 'BOSS_ACCOUNT' and row.get('account_id') is not None:
        return {'code': code, 'resource': 'boss_account',
                'reference': f"account:{row['account_id']}"}
    return {'code': code, 'resource': 'store', 'reference': f'store:{store.pk}'}


def _public_funds(raw, store):
    complete = raw.get('complete') is True
    counts = raw.get('counts') if isinstance(raw.get('counts'), dict) else {}
    count_values = [counts.get(key) for key in ('accounts', 'completed', 'zero_balance')]
    valid_counts = all(type(value) is int and value >= 0 for value in count_values)
    totals = raw.get('totals')
    blockers = [_public_blocker(row, store) for row in raw.get('blockers', [])]
    facts = raw.get('facts_sha256')
    valid_facts = isinstance(facts, str) and re.fullmatch(r'[0-9a-f]{64}', facts) is not None
    if not valid_counts or not valid_facts:
        complete = False
        blockers.append(_read_blocker(store))
    if complete and (not isinstance(totals, dict) or set(totals) != set(TOTAL_FIELDS)
                     or any(type(totals.get(field)) is not int for field in TOTAL_FIELDS)):
        complete = False
        blockers.append(_read_blocker(store))
    if not complete:
        totals = None
    if not blockers and raw.get('status') != 'CLEAR':
        blockers.append({'code': 'FUNDS_REVIEW_REQUIRED', 'resource': 'store',
                         'reference': f'store:{store.pk}'})
    status = 'UNKNOWN' if not complete else 'BLOCKED' if blockers else 'CLEAR'
    public_counts = [value if type(value) is int and value >= 0 else 0 for value in count_values]
    public_totals = {field: totals[field] for field in TOTAL_FIELDS} if totals is not None else None
    if not valid_facts:
        facts = _digest({
            'domain': 'funds', 'store_id': store.pk, 'complete': complete,
            'counts': public_counts, 'totals': public_totals, 'blockers': blockers,
        })
    return {
        'complete': complete,
        'status': status,
        'account_count': public_counts[0],
        'completed_count': public_counts[1],
        'zero_balance_count': public_counts[2],
        'totals': public_totals,
        'blockers': blockers,
        'facts_sha256': facts,
    }


def _public_obligations(raw, store):
    if isinstance(raw, dict) and isinstance(raw.get('obligations'), dict):
        raw = raw['obligations']
    if not isinstance(raw, dict):
        raw = {}
    complete = raw.get('complete') is True
    metrics = raw.get('metrics')
    valid_metrics = isinstance(metrics, list) and len(metrics) == len(OBLIGATION_CODES)
    if valid_metrics:
        rows_by_code = {row.get('code'): row for row in metrics if isinstance(row, dict)}
        valid_metrics = set(rows_by_code) == set(OBLIGATION_CODES) and all(
            set(row) == {'code', 'count'}
            and (row.get('count') is None or type(row.get('count')) is int and row['count'] >= 0)
            for row in rows_by_code.values()
        )
    if valid_metrics:
        metrics = [
            {'code': code, 'count': rows_by_code[code]['count']}
            for code in OBLIGATION_CODES
        ]
    blockers = [_public_blocker(row, store) for row in raw.get('blockers', [])]
    facts = raw.get('facts_sha256')
    valid_facts = isinstance(facts, str) and re.fullmatch(r'[0-9a-f]{64}', facts) is not None
    has_unknown_counts = valid_metrics and any(row['count'] is None for row in metrics)
    if not valid_metrics or not valid_facts or has_unknown_counts:
        complete = False
        blockers.append(_read_blocker(store))
    if not valid_metrics:
        metrics = [{'code': code, 'count': None} for code in OBLIGATION_CODES]
    if not blockers and raw.get('status') != 'CLEAR':
        blockers.append({'code': 'BUSINESS_OBLIGATIONS', 'resource': 'store',
                         'reference': f'store:{store.pk}'})
    status = 'UNKNOWN' if not complete else 'BLOCKED' if blockers else 'CLEAR'
    if not valid_facts:
        facts = _digest({
            'domain': 'obligations', 'store_id': store.pk, 'complete': complete,
            'metrics': metrics, 'blockers': blockers,
        })
    return {
        'complete': complete,
        'status': status,
        'metrics': metrics,
        'blockers': blockers,
        'facts_sha256': facts,
    }


def _closure_facts(state, store):
    notice = state.get('notice') or {}
    event = state.get('expiry_event') or {}
    return {
        'store': {'id': store.pk, 'slug': store.slug, 'status': store.status},
        'notice': {
            'id': notice.get('id'), 'status': state.get('status'),
            'notice_policy_version': notice.get('notice_policy_version'),
            'notice_days': notice.get('notice_days'),
            'started_at': notice.get('started_at'),
            'earliest_settlement_at': notice.get('earliest_settlement_at'),
        },
        'expiry_event': {
            'id': event.get('id'), 'kind': event.get('kind'), 'occurred_at': event.get('occurred_at'),
        },
    }


def settlement_readiness(store):
    state = read_platform_closure(store)
    if state['status'] != 'SETTLEMENT_PENDING':
        raise ClosureError('门店尚未进入待结清状态', 409, 'CLOSURE_NOT_SETTLEMENT_PENDING')
    funds_raw = build_closure_funds_readiness(store)
    funds = _public_funds(funds_raw.get('funds', funds_raw), store)
    with store_context(store):
        obligations = _public_obligations(closure_obligation_readiness(store), store)
    complete = funds['complete'] and obligations['complete']
    reasons = []
    if funds['blockers']:
        reasons.append('FUNDS_REVIEW_REQUIRED')
    if obligations['blockers']:
        reasons.append('BUSINESS_OBLIGATIONS')
    reasons.extend(['FEE_RULES_PENDING', 'FINAL_WRITE_BARRIER_UNAVAILABLE'])
    if not complete:
        reasons.append('READ_INCOMPLETE')
    snapshot = _digest({
        'version': 'E3_READINESS_V1', 'closure': _closure_facts(state, store),
        'funds_sha256': funds['facts_sha256'], 'obligations_sha256': obligations['facts_sha256'],
        'policy': POLICY_BLOCKERS,
    })
    return {
        'status': 'BLOCKED' if complete else 'UNKNOWN',
        'complete': complete,
        'can_finalize': False,
        'observed_at': timezone.now().isoformat(),
        'snapshot_sha256': snapshot,
        'funds': funds,
        'obligations': obligations,
        'policy': {
            'complete': True, 'fee_rules': 'PENDING', 'final_write_barrier': 'UNAVAILABLE',
            'blockers': POLICY_BLOCKERS,
        },
        'blocking_reasons': reasons,
    }


class PlatformStoreClosureReadinessView(PlatformClosureBase):
    def get(self, request, pk):
        try:
            store = self.authorized_store(request, pk, 'closure:view')
            return _response({'code': 0, 'data': settlement_readiness(store)})
        except ClosureError as error:
            return _error(error)
        except (DatabaseError, InterfaceError):
            return _response({
                'code': 503, 'errorCode': 'CLOSURE_READINESS_UNAVAILABLE',
                'msg': '结清预检暂时无法读取，请稍后重试；当前状态未确认',
            }, 503)
