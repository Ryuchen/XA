"""Isolated building blocks for a points-paid negotiated refund.

The returned objects are valid only while the caller holds the same tenant
transaction and order-money lock.  In particular, a planned deposit deduction
is not evidence that the deposit has been recovered or that it is backed.
"""
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError
import hashlib
import json

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Wallet

from .financial_lock import lock_order_money
from .models import NegotiationProposal
from .negotiation_proposals import _decisions, _lock_case, _scope, _verified, enabled
from .points_checkout_test import require_isolated_checkout
from .points_negotiation_preview import asset_terms, preview_locked, verified_asset_terms
from .refunds import authorized_refund


def participant_snapshot(order):
    """Capture immutable entry facts before refund changes eligibility."""
    from .points_service_participation import service_snapshots
    rows = service_snapshots(order)
    assignments = {row.provider_account_id: row for row in order.providers.filter(released_at__isnull=True)}
    from .addon_main_deduction import deductions_at
    prior_deductions = deductions_at(order)
    if not rows or len(rows) != len(assignments) or len({row.segment.provider_account_id for row in rows}) != len(rows):
        raise ValidationError('积分订单陪玩履约快照不完整')
    result = []
    for row in rows:
        segment = row.segment
        assignment = assignments.get(segment.provider_account_id)
        if not assignment or not segment.started_at or segment.released_at or assignment.released_at:
            raise ValidationError('积分订单陪玩履约快照已变化')
        result.append({'snapshot_id': row.pk, 'segment_id': segment.pk, 'assignment_id': assignment.pk,
            'account_id': segment.provider_account_id, 'provider_id': assignment.provider_id, 'slot': segment.slot,
            'original_share': segment.original_share, 'started_at': segment.started_at.isoformat(),
            'incoming_basis': row.incoming_basis, 'commission_type': row.commission_type,
            'commission_rate': row.commission_rate, 'commission_fixed': row.commission_fixed,
            'fixed_rule_total': row.fixed_rule_total, 'commission_source': row.commission_source,
            'income_at_entry': row.income_at_entry, 'prior_addon_deduction': prior_deductions.get(segment.provider_account_id, 0),
            'game_commission_snapshot': row.game_commission_snapshot})
    return result


def participant_digest(proposal, rows):
    return hashlib.sha256(json.dumps([proposal.pk, proposal.terms_hash, rows], sort_keys=True,
                                    ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _verify_transfer_terms_from_facts(posting, facts, history):
    """Check agreed burdens against previously verified immutable facts."""
    from .refund_math import RefundCalculationError, provider_refund_burden
    segments = {row['id']: row for row in facts['segments']}
    scope = posting.proposal.affected_accounts
    selected = set(posting.proposal.affected_segment_ids or [])
    weights = {pk: sum(row['original_share'] for row in facts['segments']
                       if row['account_id'] == pk and row['started_at']
                       and (not selected or row['id'] in selected)) for pk in scope}
    if any(not weight for weight in weights.values()):
        raise ValidationError('积分转单退款履约范围异常')
    try:
        burden = provider_refund_burden(refund_equivalent_units=posting.proposal.refund_units,
                                        original_price_weights=weights)
    except RefundCalculationError as exc:
        raise ValidationError('积分转单退款承担凭证异常') from exc
    from .addon_main_segment_deduction import verified_deductions
    prior_addon = verified_deductions(posting.proposal.case.order, before=posting.created_at)
    if any(prior_addon.get(line[0], 0) > line[4] for line in history.segment_shares):
        raise ValidationError('加购退款已占履约段收益异常')
    incomes = {pk: sum(line[4] - prior_addon.get(line[0], 0) for line in history.segment_shares
                       if line[1] == pk and segments[line[0]]['started_at']
                       and (not selected or line[0] in selected)) for pk in scope}
    expected = [{'account_id': pk, 'original_income_units': incomes[pk], 'burden_units': burden.amounts[pk],
        'retained_units': max(incomes[pk] - burden.amounts[pk], 0),
        'additional_units': max(burden.amounts[pk] - incomes[pk], 0)} for pk in scope]
    if expected != posting.proposal.provider_terms or posting.rounding_to_store_units != burden.rounding_to_store_units:
        raise ValidationError('积分转单退款承担与履约凭证不一致')


def verify_participants(posting):
    from .models import OrderSegmentFinancialSnapshot
    rows = posting.participants
    if isinstance(rows, dict):
        from .points_transfer_refund_access import permits, require_refund_or_isolated
        require_refund_or_isolated()
        if ((not permits(posting.proposal.case.order, phase='refund')
             and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING', False))
                or posting.participants_hash != participant_digest(posting.proposal, rows)):
            raise ValidationError('积分转单退款履约凭证异常')
        from .points_transfer_refund_evidence import reconstruct_funding_locked, verify_locked
        facts = verify_locked(posting.proposal.case.order_id, rows, boundary=posting.created_at)
        latest = facts['transfers'][-1]['id'] if facts['transfers'] else 0
        history = reconstruct_funding_locked(posting.proposal.case.order_id, rows,
            refund_id=posting.points_refund.refund_id, through_transfer_id=latest, boundary=posting.created_at)
        _verify_transfer_terms_from_facts(posting, facts, history)
        return rows
    if (not isinstance(rows, list) or not rows or posting.participants_hash != participant_digest(posting.proposal, rows)
            or any(not isinstance(row, dict) for row in rows)):
        raise ValidationError('积分协商履约快照缺失或异常')
    from .models import OrderOriginalShareTransfer
    order = posting.proposal.case.order
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        from .points_transfer_refund_access import permits
        if not permits(order, phase='refund'):
            raise ValidationError('转单后的原退款快照须在本单资金事务内核对')
        from .points_pretransfer_refund_source import verify_participants_locked
        return verify_participants_locked(posting)
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(posting.proposal.case.order)
    from .addon_main_deduction import deductions_at
    prior_deductions = deductions_at(posting.proposal.case.order, before=posting.created_at)
    assignments = {row.pk: row for row in posting.proposal.case.order.providers.filter(
        pk__in=[row.get('assignment_id') for row in rows])}
    if len(snapshots) != len(rows) or len(assignments) != len(rows):
        raise ValidationError('积分协商陪玩履约范围已变化')
    for snap, evidence in zip(snapshots, rows):
        segment = snap.segment
        assignment = assignments.get(evidence.get('assignment_id'))
        actual = {'snapshot_id': snap.pk, 'segment_id': segment.pk, 'assignment_id': assignment.pk if assignment else None,
            'account_id': segment.provider_account_id, 'provider_id': assignment.provider_id if assignment else None,
            'slot': segment.slot, 'original_share': segment.original_share,
            'started_at': segment.started_at.isoformat() if segment.started_at else None,
            'incoming_basis': snap.incoming_basis, 'commission_type': snap.commission_type,
            'commission_rate': snap.commission_rate, 'commission_fixed': snap.commission_fixed,
            'fixed_rule_total': snap.fixed_rule_total, 'commission_source': snap.commission_source,
            'income_at_entry': snap.income_at_entry, 'prior_addon_deduction': prior_deductions.get(segment.provider_account_id, 0),
            'game_commission_snapshot': snap.game_commission_snapshot}
        if (not assignment or assignment.provider_account_id != segment.provider_account_id or actual != evidence):
            raise ValidationError('积分协商陪玩收益或身份快照异常')
        share = posting.shares.filter(account_id=segment.provider_account_id).first()
        if share and share.original_income != snap.income_at_entry - actual['prior_addon_deduction']:
            raise ValidationError('积分协商的个人剩余收益不一致')
    return rows


def prepare_locked(case_id, proposal_id, *, operator):
    """Revalidate consent and plan pending income, wallet, then deposit.

    This internal test-only preflight neither authorizes nor implements a
    refund, and must not be exposed as evidence of a posted recovery.
    """
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('积分协商退款核对必须在门店资金事务内执行')
    if (not enabled() or not authorized_refund(operator)
            or not operator.is_active or not operator.can_login):
        raise ValidationError('积分协商退款未启用或经办人无退款权限')
    if type(proposal_id) is not int or proposal_id <= 0:
        raise ValidationError('协商方案无效')

    order, case = _lock_case(case_id)
    lock_order_money()
    from .points_receipt_usage import prelock_transfer_refund_receipts
    prelock_transfer_refund_receipts(order)
    scope = _scope(order, case)
    proposal = NegotiationProposal.objects.select_for_update().filter(case=case, pk=proposal_id).first()
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if not proposal or not latest or proposal.pk != latest.pk or proposal.affected_accounts != scope:
        raise ValidationError('协商方案已变化，请重新核对')
    from .points_transfer_refund_access import permits
    if not permits(order, phase='refund'):
        try:
            require_isolated_checkout()
        except ValidationError:
            from .points_negotiation_production import require_order_locked
            require_order_locked(order.pk, phase='before', refund_equivalent_units=proposal.refund_units)
        else:
            if not getattr(settings, 'SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING', False):
                raise ValidationError('积分协商退款未启用或经办人无退款权限')
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    if proposal.status != 'ACCEPTED':
        raise ValidationError('协商方案尚未获得全部当事人同意')
    from .models import OrderOriginalShareTransfer
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        from .points_transfer_refund_preview import preview_locked as transfer_preview
        calculated = transfer_preview(case.pk, refund_equivalent_units=proposal.refund_units)
    else:
        calculated = preview_locked(case.pk, refund_equivalent_units=proposal.refund_units)
    if (calculated['provider_terms'] != proposal.provider_terms
            or asset_terms(calculated) != verified_asset_terms(proposal)):
        raise ValidationError('订单收益或退款构成已变化，请重新确认方案')

    assignments = {item.provider_account_id: item for item in order.providers.select_for_update().order_by('pk')}
    profiles = {item.account_id: item for item in EscortProfile.objects.select_for_update()
                .filter(account_id__in=scope).order_by('pk')}
    user_ids = [assignments[account].provider_id for account in scope]
    wallets = list(Wallet.objects.select_for_update().filter(
        Q(account_id__in=scope) | Q(user_id__in=user_ids)).order_by('pk'))
    plans = []
    for term in proposal.provider_terms:
        account_id = term['account_id']
        assignment, profile = assignments[account_id], profiles.get(account_id)
        matches = [wallet for wallet in wallets
                   if wallet.account_id == account_id or wallet.user_id == assignment.provider_id]
        if (not profile or profile.user_id != assignment.provider_id or profile.deposit_paid < 0
                or not LegacyAccountMap.objects.select_for_update().filter(
                    account_id=account_id, legacy_user_id=assignment.provider_id).exists()
                or len(matches) > 1 or any(not wallet.is_active or wallet.balance < 0
                    or wallet.account_id not in (None, account_id)
                    or wallet.user_id not in (None, assignment.provider_id) for wallet in matches)):
            raise ValidationError('陪玩钱包或押金归属不完整，请先核对')
        # Do not backfill a legacy wallet in a read-only preflight. A posting
        # operation must resolve its complete identity again before debiting.
        wallet = matches[0] if matches else None
        pending = min(term['burden_units'], term['original_income_units'])
        remaining = term['burden_units'] - pending
        available = min(remaining, wallet.balance if wallet else 0)
        remaining -= available
        deposit = min(remaining, profile.deposit_paid)
        plans.append({'account_id': account_id, 'assignment': assignment, 'profile': profile, 'wallet': wallet,
                      'pending_units': pending, 'wallet_units': available, 'deposit_units': deposit,
                      'shortfall_units': remaining - deposit})
    if any(item['shortfall_units'] for item in plans):
        raise ValidationError('陪玩待结算收益、可用余额及押金不足，本次不能退款')
    return {'order': order, 'case': case, 'proposal': proposal, 'calculation': calculated, 'providers': plans}


def _verify_posting_core_locked(posting):
    """Check consent, returned assets and debits; callers must verify participation."""
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('积分退款凭证必须在门店事务内核对')
    from .models import PointsNegotiationPostingShare
    from .points_service_refund import verify_refund_history_locked
    from .refunds import request_fingerprint
    posting = type(posting).objects.select_for_update().select_related(
        'proposal__case__order', 'points_refund__refund', 'operator').get(pk=posting.pk)
    proposal, proof = posting.proposal, posting.points_refund
    case, refund = proposal.case, proof.refund
    from copy import copy
    episode = copy(case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(episode, proposal)
    _decisions(episode, proposal, lock=True)
    assets = verified_asset_terms(proposal)
    verified = verify_refund_history_locked(refund.pk)
    if (verified.pk != proof.pk or proposal.status != 'ACCEPTED' or posting.terms_hash != proposal.terms_hash
            or posting.accepted_at != proposal.accepted_at or posting.operator_id != refund.operator_account_id
            or refund.order_id != case.order_id or refund.reason != proposal.explanation
            or not refund.client_request_id or refund.amount != assets['coin_units']
            or proof.equivalent_units != proposal.refund_units
            or (proof.redemption_id, proof.coin_units, proof.points_hundredths, proof.prepaid_units,
                proof.gift_units, proof.compensation_units, proof.is_final) != (
                    assets['redemption_id'], assets['coin_units'], assets['points_hundredths'],
                    assets['prepaid_units'], assets['gift_units'], assets['compensation_units'], assets['is_final'])
            or refund.request_fingerprint != request_fingerprint(refund.amount, refund.reason,
                {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units})):
        raise ValidationError('积分协商退款凭证不一致，请联系管理员核对')
    rows = list(PointsNegotiationPostingShare.objects.select_for_update().filter(posting=posting)
                .select_related('assignment', 'wallet_transaction__wallet', 'deposit_deficit').order_by('account_id'))
    if len(rows) != len(proposal.provider_terms):
        raise ValidationError('陪玩退款承担凭证不完整')
    for row, term in zip(rows, proposal.provider_terms):
        if (row.account_id != term['account_id'] or row.assignment.order_id != case.order_id
                or row.assignment.provider_account_id != row.account_id
                or (row.original_income, row.burden, row.retained_income) !=
                    (term['original_income_units'], term['burden_units'], term['retained_units'])
                or row.pending_deduction != min(row.burden, row.original_income)
                or row.wallet_deduction != min(row.burden - row.pending_deduction, row.wallet_available_before)
                or row.deposit_deduction > row.deposit_paid_before
                or row.burden != row.pending_deduction + row.wallet_deduction + row.deposit_deduction):
            raise ValidationError('陪玩退款承担与已确认方案不一致')
        tx = row.wallet_transaction
        if row.wallet_deduction and (not tx or tx.wallet.account_id != row.account_id
                or tx.wallet.user_id != row.assignment.provider_id or tx.order_id != case.order_id
                or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS' or tx.amount != -row.wallet_deduction
                or tx.balance_before != row.wallet_available_before
                or tx.balance_after != tx.balance_before + tx.amount or tx.operator_account_id != posting.operator_id
                or tx.operator_id != refund.operator_id or tx.reversal_of_id or tx.reversals.exists()):
            raise ValidationError('陪玩钱包扣款流水异常')
        deficit = row.deposit_deficit
        if row.deposit_deduction:
            from wallet.models import get_platform_wallet
            from wallet.refund_deposit import _verify_direct_deficit
            if (not deficit or deficit.refund_id != refund.pk or deficit.account_id != row.account_id
                    or deficit.amount != row.deposit_deduction
                    or deficit.profile.user_id != row.assignment.provider_id
                    or deficit.source_store_transaction.operator_account_id != posting.operator_id
                    or deficit.source_store_transaction.operator_id != refund.operator_id):
                raise ValidationError('陪玩退款押金凭证异常')
            try:
                _verify_direct_deficit(deficit, get_platform_wallet(for_update=True))
            except ValueError as exc:
                raise ValidationError('陪玩退款押金凭证异常') from exc
    segment_plan = assets.get('segment_pending_plan')
    from .negotiation_models import PointsNegotiationSegmentDeduction
    segments = [{'account_id': account, 'segment_id': segment, 'amount': amount}
        for account, segment, amount in PointsNegotiationSegmentDeduction.objects.filter(
            share__posting=posting).values_list('share__account_id', 'segment_id', 'amount')]
    key = lambda item: (item['account_id'], item['segment_id'])
    if (segment_plan is not None and not isinstance(segment_plan, list)) or (
            sorted(segments, key=key) != sorted(segment_plan or [], key=key)):
        raise ValidationError('积分协商逐段承担与确认方案不一致')
    if (posting.rounding_to_store_units != sum(row.burden for row in rows) - proposal.refund_units
            or refund.recovered_amount != sum(row.wallet_deduction + row.deposit_deduction for row in rows)):
        raise ValidationError('积分协商退款承担合计异常')
    return posting


def verify_posting_locked(posting):
    """Check the actual asset returns and every affected provider's debit."""
    from .points_receipt_usage import prelock_posting_receipts
    prelock_posting_receipts(posting)
    posting = _verify_posting_core_locked(posting)
    verify_participants(posting)
    return posting


def _verify_transfer_posting_facts_locked(posting):
    """Verify a transfer posting's facts without authorizing settlement state.

    This is an internal component for a future version-chain verifier.  It
    checks the actual money and immutable transfer records, but deliberately
    does not establish that any later settlement manifest is legitimate.
    """
    posting = _verify_posting_core_locked(posting)
    rows = posting.participants
    from .points_transfer_refund_access import permits
    formal = permits(posting.proposal.case.order, phase='refund') or permits(posting.proposal.case.order, phase='settlement')
    if not formal:
        require_isolated_checkout()
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING', False))
            or not isinstance(rows, dict) or posting.participants_hash != participant_digest(posting.proposal, rows)):
        raise ValidationError('积分转单退款履约凭证异常')
    from .points_transfer_refund_evidence import _verify_facts_locked, _reconstruct_from_verified_facts
    # This verifier replays a persisted posting. Later real service events
    # must not be folded into its original snapshot or rejected merely for
    # occurring after it; the ordinary live posting verifier stays strict.
    _, facts, _ = _verify_facts_locked(posting.proposal.case.order_id, rows, boundary=posting.created_at)
    if not facts.get('transfers'):
        raise ValidationError('积分转单退款履约凭证异常')
    history = _reconstruct_from_verified_facts(posting.proposal.case.order_id, facts,
        refund_id=posting.points_refund.refund_id, through_transfer_id=facts['transfers'][-1]['id'])
    _verify_transfer_terms_from_facts(posting, facts, history)
    return posting, facts, history


def post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Post one accepted refund inside the caller's existing test transaction.

    This primitive is intentionally not an endpoint: its caller must also
    atomically terminate the affected participation, adjust funding and
    complete the aftersale. Until those paths are connected it is test-only.
    """
    from .points_transfer_refund_access import require_refund_or_isolated
    from .points_business_access import permits as service_permits
    if not (service_permits(phase='refund', kind='service') or service_permits(phase='refund', kind='addon')):
        require_refund_or_isolated()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('积分协商退款须由完整的门店事务调用')
    # A caller can catch the exception; do not leave asset returns or debits
    # in that caller's transaction when a later posting step fails.
    with transaction.atomic():
        return _post_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator,
                            request_id=request_id)


def _post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .points_transfer_refund_access import permits
    from .points_business_access import permits as service_permits
    formal_service = (service_permits(phase='refund', kind='service') or service_permits(phase='refund', kind='addon'))
    if ((not permits(phase='refund') and not formal_service and (not getattr(settings, 'SERVICE_POINTS_NEGOTIATION_POSTING_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING', False)))
            or not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or type(case_id) is not int or case_id <= 0 or type(proposal_id) is not int or proposal_id <= 0
            or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip()):
        raise ValidationError('积分退款记账尚未启用或经办凭证无效')
    if (not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False)
            and legacy_operator is not None and not LegacyAccountMap.objects.select_for_update().filter(
                account=operator, legacy_user_id=legacy_operator.pk).exists()):
        raise ValidationError('退款经办人的业务账号与历史身份不一致')
    from .models import OrderRefund, PointsNegotiationPosting, PointsNegotiationPostingShare
    from .refunds import request_fingerprint
    from .points_service_refund import refund_assets_locked
    from wallet.models import Transaction
    order, case = _lock_case(case_id)
    if permits(phase='refund') and not permits(order, phase='refund'):
        raise ValidationError('转单退款上下文不属于当前订单')
    if formal_service and not (service_permits(order, phase='refund', kind='service') or service_permits(order, phase='refund', kind='addon')):
        raise ValidationError('积分退款上下文不属于当前订单')
    lock_order_money()
    from .points_receipt_usage import prelock_transfer_refund_receipts
    prelock_transfer_refund_receipts(order)
    if (getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False)
            and legacy_operator is not None and not LegacyAccountMap.objects.select_for_update().filter(
                account=operator, legacy_user_id=legacy_operator.pk).exists()):
        raise ValidationError('退款经办人的业务账号与历史身份不一致')
    existing = PointsNegotiationPosting.objects.select_for_update().filter(proposal_id=proposal_id, proposal__case=case).first()
    if existing:
        if (existing.operator_id != operator.pk or existing.points_refund.refund.client_request_id != request_id
                or existing.points_refund.refund.operator_id != getattr(legacy_operator, 'pk', None)):
            raise ValidationError('该方案已处理，请核对原退款记录')
        result = verify_posting_locked(existing)
        from .models import OrderPointsFundingAllocation, OrderPointsFundingReleaseEvidence
        if OrderPointsFundingAllocation.objects.filter(Q(redemption__order=order) | Q(service_order=order)).exists():
            if permits(order, phase='refund'):
                from .points_transfer_refund_release import verify_release_locked
            else:
                from .points_funding_release import verify_release_locked
            evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=existing).first()
            if not evidence:
                raise ValidationError('退款后的补资释放凭证缺失')
            verify_release_locked(evidence)
        return result
    if OrderRefund.objects.filter(order=order, client_request_id=request_id).exists():
        raise ValidationError('操作凭证已用于其他退款')
    from .addon_settlement import has_paid_addons
    if (has_paid_addons(order) and not service_permits(order, phase='refund', kind='addon')
            and not permits(order, phase='refund')
            and not getattr(settings, 'SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING', False)):
        raise ValidationError('主单退款与加购的补资释放及结算尚未接通，暂不能执行退款')
    plan = prepare_locked(case_id, proposal_id, operator=operator)
    if permits(order, phase='refund') and not 1 <= len(plan['providers']) <= 2:
        raise ValidationError('转单退款受影响陪玩范围异常')
    if formal_service and not 1 <= len(plan['providers']) <= 2:
        raise ValidationError('积分退款陪玩承担范围待核对')
    proposal, calc = plan['proposal'], plan['calculation']
    if service_permits(order, phase='refund', kind='addon'):
        combined = calc.get('conditional_combined_funding')
        if combined is None or combined.remaining_units:
            raise ValidationError('该主单与加购退款叠加的到账补资不足，暂不能执行')
    from .models import OrderPointsFundingAllocation
    has_funding = OrderPointsFundingAllocation.objects.filter(Q(redemption__order=order) | Q(service_order=order)).exists()
    if has_funding and not (permits(order, phase='refund') or formal_service or getattr(settings, 'SERVICE_POINTS_FUNDING_RELEASE_TESTING', False)):
        raise ValidationError('退款后的补资释放尚未启用')
    if any(row['deposit_units'] for row in plan['providers']):
        from wallet.refund_deposit import enabled as deposit_enabled
        from wallet.refund_recovery_order import enabled as recovery_enabled
        formal_transfer = permits(order, phase='refund')
        if (not deposit_enabled() or ((formal_service or formal_transfer) and not recovery_enabled())
                or (not formal_service and not formal_transfer
                    and not getattr(settings, 'SERVICE_POINTS_REFUND_DEPOSIT_TESTING', False))):
            raise ValidationError('积分退款押金扣款或后续补差尚未启用')
    from .models import OrderOriginalShareTransfer
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    if transferred:
        from .points_transfer_refund_preview import agreement_enabled as transfer_agreement_enabled
        from .models import OrderSegmentSettlementManifest
        prior_manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
        if ((not permits(order, phase='refund') and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING', False))
                or not transfer_agreement_enabled()
                or (prior_manifest and not permits(order, phase='refund')
                    and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING', False))
                or (has_funding and not permits(order, phase='refund')
                    and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING', False))):
            raise ValidationError('积分转单退款记账或补资释放尚未开放')
        from .points_transfer_refund_evidence import capture_locked
        participants = capture_locked(case_id, refund_equivalent_units=proposal.refund_units)
    else:
        participants = participant_snapshot(order)
    refund = OrderRefund.objects.create(order=order, amount=calc['coin_units'], reason=proposal.explanation,
        operator=legacy_operator, operator_account=operator, client_request_id=request_id,
        request_fingerprint=request_fingerprint(calc['coin_units'], proposal.explanation,
            {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units}))
    proof = refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=proposal.refund_units)
    posting = PointsNegotiationPosting.objects.create(proposal=proposal, points_refund=proof, operator=operator,
        terms_hash=proposal.terms_hash, accepted_at=proposal.accepted_at,
        rounding_to_store_units=calc['rounding_to_store_units'], participants=participants,
        participants_hash=participant_digest(proposal, participants))
    terms = {row['account_id']: row for row in proposal.provider_terms}
    for item in plan['providers']:
        wallet_tx = deficit = None
        wallet_before = item['wallet'].balance if item['wallet'] else 0
        deposit_before = item['profile'].deposit_paid
        if item['wallet_units']:
            from wallet.services import get_wallet, WalletAddressingError
            try:
                wallet = get_wallet(account_id=item['account_id'], user_id=item['assignment'].provider_id,
                                    for_update=True, create=False)
            except WalletAddressingError as exc:
                raise ValidationError('陪玩钱包归属待核对') from exc
            if not wallet or wallet.pk != item['wallet'].pk or wallet.balance < item['wallet_units']:
                raise ValidationError('陪玩可用余额已变化，请重新核对')
            before = wallet.balance
            wallet.balance -= item['wallet_units']; wallet.save(update_fields=['balance'])
            wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND',
                amount=-item['wallet_units'], balance_before=before, balance_after=wallet.balance,
                operator=legacy_operator, operator_account=operator, remark=f'积分协商退款承担：{order.order_no}')
        if item['deposit_units']:
            from wallet.refund_deposit import charge_refund_deposit
            try:
                deficit = charge_refund_deposit(refund=refund, profile=item['profile'], amount=item['deposit_units'],
                    operator=legacy_operator, operator_account=operator)
            except ValueError as exc:
                raise ValidationError('陪玩押金资金或凭证待核对') from exc
        term = terms[item['account_id']]
        PointsNegotiationPostingShare.objects.create(posting=posting, assignment=item['assignment'],
            account_id=item['account_id'], original_income=term['original_income_units'], burden=term['burden_units'],
            pending_deduction=item['pending_units'], wallet_available_before=wallet_before,
            deposit_paid_before=deposit_before, wallet_deduction=item['wallet_units'],
            deposit_deduction=item['deposit_units'], retained_income=term['retained_units'],
            wallet_transaction=wallet_tx, deposit_deficit=deficit)
    refund.recovered_amount = sum(row['wallet_units'] + row['deposit_units'] for row in plan['providers'])
    refund.save(update_fields=['recovered_amount'])
    posting = verify_posting_locked(posting)
    if transferred and (has_funding or permits(order, phase='refund')
                        or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING', False)):
        from .points_transfer_refund_release import record_release_locked
        record_release_locked(posting, calc)
    elif not transferred and (has_funding or formal_service or getattr(settings, 'SERVICE_POINTS_FUNDING_RELEASE_TESTING', False)):
        from .points_funding_release import record_release_locked
        record_release_locked(posting, calc)
    return posting
