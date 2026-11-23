"""Isolated preflight for a second points-negotiation refund.

The result is conditional, valid only under the caller's existing locks, and
is not evidence that a wallet or deposit debit has been executed or backed.
"""
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Wallet
from .financial_lock import lock_order_money
from .models import NegotiationProposal, OrderProvider
from .negotiation_proposals import _decisions, _lock_case, _verified, enabled
from .points_checkout_test import require_isolated_checkout
from .points_followup_preview import preview_followup
from .points_negotiation_preview import asset_terms, verified_asset_terms
from .refunds import authorized_refund


def prepare_followup_locked(case_id, proposal_id, *, operator):
    from .points_business_access import permits
    formal = any(permits(phase='refund', kind=kind) for kind in ('service', 'addon', 'transfer'))
    if not formal:
        require_isolated_checkout()
    if (not transaction.get_connection().in_atomic_block or not enabled()
            or (not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING', False))
            or not authorized_refund(operator) or not operator.is_active or not operator.can_login):
        raise ValidationError('后续积分协商退款核对尚未开放或无权限')
    if type(case_id) is not int or case_id <= 0 or type(proposal_id) is not int or proposal_id <= 0:
        raise ValidationError('售后或方案编号无效')
    order, case = _lock_case(case_id)
    if formal and not any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon', 'transfer')):
        raise ValidationError('后续退款与当前订单资金上下文不一致')
    lock_order_money()
    from .points_receipt_usage import prelock_followup_receipts
    prelock_followup_receipts(order)
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if not proposal or not latest or proposal.pk != latest.pk or proposal.status != 'ACCEPTED':
        raise ValidationError('后续协商方案尚未由双方确认或已更新')
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    from .models import OrderOriginalShareTransfer
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    if transferred:
        from .points_transfer_followup_preview import internal_agreement_enabled, preview_locked as transfer_followup_preview
        if ((not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_EXECUTION_TESTING', False))
                or not internal_agreement_enabled()):
            raise ValidationError('转单后的追加退款执行核对尚未开放')
        calculation = transfer_followup_preview(case.pk, refund_equivalent_units=proposal.refund_units)
    else:
        calculation = preview_followup(case.pk, refund_equivalent_units=proposal.refund_units)
        from .points_followup_termination import formal_followup_funding_ready
        if formal and not formal_followup_funding_ready(calculation):
            raise ValidationError('已有陪玩结算的后续退款须先完成资金核对')
    if (calculation['provider_terms'] != proposal.provider_terms
            or verified_asset_terms(proposal) != asset_terms(calculation)):
        raise ValidationError('后续协商金额或退款构成已变化')
    providers = []
    terms = sorted(proposal.provider_terms, key=lambda item: item['account_id'])
    for term in terms:
        assignment = OrderProvider.objects.select_for_update().get(order=order, provider_account_id=term['account_id'])
        profile = EscortProfile.objects.select_for_update().filter(account_id=term['account_id']).first()
        if (not profile or profile.user_id != assignment.provider_id or profile.deposit_paid < 0
                or not LegacyAccountMap.objects.filter(account_id=profile.account_id, legacy_user_id=profile.user_id).exists()):
            raise ValidationError('后续退款陪玩身份或押金待核对')
        wallets = list(Wallet.objects.select_for_update().filter(
            Q(account_id=profile.account_id) | Q(user_id=profile.user_id)).order_by('pk'))
        if len(wallets) > 1 or any(not row.is_active or row.balance < 0
                or row.account_id not in (None, profile.account_id)
                or row.user_id not in (None, profile.user_id) for row in wallets):
            raise ValidationError('后续退款陪玩钱包待核对')
        wallet = wallets[0] if wallets else None
        pending = min(term['burden_units'], term['original_income_units'])
        remaining = term['burden_units'] - pending
        wallet_units = min(remaining, wallet.balance if wallet else 0)
        remaining -= wallet_units
        deposit_units = min(remaining, profile.deposit_paid)
        if remaining > deposit_units:
            raise ValidationError('陪玩待结算收益、可用余额及押金不足，本次不能退款')
        providers.append({'term': term, 'assignment': assignment, 'profile': profile, 'wallet': wallet,
                          'pending_units': pending, 'wallet_units': wallet_units, 'deposit_units': deposit_units})
    if not providers:
        raise ValidationError('后续退款缺少陪玩承担方案')
    return {'order': order, 'case': case, 'proposal': proposal, 'calculation': calculation,
            'providers': providers, **providers[0]}


def post_followup_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Post assets under the caller's transaction; closure is not implemented.

    This test-only primitive must be composed with case closure, immutable
    funding revision and settlement before it can be used by an endpoint.
    The returned plan is valid only under the same transaction and locks.
    """
    from .points_business_access import permits
    if not any(permits(phase='refund', kind=kind) for kind in ('service', 'addon')):
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('后续积分退款须由完整的门店事务调用')
    with transaction.atomic():
        return _post_followup_locked(case_id, proposal_id, operator=operator,
                                     legacy_operator=legacy_operator, request_id=request_id)


def _post_followup_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .points_business_access import permits
    formal = any(permits(phase='refund', kind=kind) for kind in ('service', 'addon'))
    if ((not formal and (not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_POSTING_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_REVISION_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING', False)))
            or not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or type(case_id) is not int or case_id <= 0 or type(proposal_id) is not int or proposal_id <= 0
            or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip()):
        raise ValidationError('后续积分退款记账尚未开放或经办凭证无效')
    from .models import OrderRefund, PointsNegotiationPosting, PointsNegotiationPostingShare
    from .points_negotiation_execution import participant_digest, verify_posting_locked
    from .points_service_refund import refund_assets_locked
    from .refunds import request_fingerprint
    from wallet.models import Transaction
    order, case = _lock_case(case_id)
    if formal and not any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon')):
        raise ValidationError('后续退款与当前订单资金上下文不一致')
    lock_order_money()
    from .points_receipt_usage import prelock_followup_receipts
    prelock_followup_receipts(order)
    if legacy_operator is not None and not LegacyAccountMap.objects.select_for_update().filter(
            account=operator, legacy_user_id=legacy_operator.pk).exists():
        raise ValidationError('退款经办人的业务账号与历史身份不一致')
    from .models import OrderOriginalShareTransfer
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        raise ValidationError('转单后的追加退款记账和资金版本尚未开放')
    existing = PointsNegotiationPosting.objects.select_for_update().filter(
        proposal_id=proposal_id, proposal__case=case).first()
    if existing:
        if (existing.operator_id != operator.pk or existing.points_refund.refund.client_request_id != request_id
                or existing.points_refund.refund.operator_id != getattr(legacy_operator, 'pk', None)):
            raise ValidationError('该方案已处理，请核对原退款记录')
        verified = verify_posting_locked(existing)
        from .models import OrderPointsFollowupFundingRevision
        from .points_followup_revision import verify_followup_revision_locked
        revision = OrderPointsFollowupFundingRevision.objects.select_for_update().filter(posting=verified).first()
        if not revision:
            raise ValidationError('后续退款资金版本凭证缺失')
        verify_followup_revision_locked(revision)
        from .models import OrderPointsFollowupFundingReleaseEvidence
        from .points_followup_release import verify_followup_release_locked
        release = OrderPointsFollowupFundingReleaseEvidence.objects.filter(revision=revision).first()
        if not release:
            raise ValidationError('后续退款资金释放凭证缺失')
        verify_followup_release_locked(release)
        return verified, revision
    if OrderRefund.objects.filter(order=order, client_request_id=request_id).exists():
        raise ValidationError('操作凭证已用于其他退款')
    plan = prepare_followup_locked(case_id, proposal_id, operator=operator)
    proposal, calc = plan['proposal'], plan['calculation']
    if plan['deposit_units']:
        from wallet.refund_deposit import enabled as deposit_enabled
        from wallet.refund_recovery_order import tracking_enabled
        if (not deposit_enabled() or not tracking_enabled()
                or (not formal and not getattr(settings, 'SERVICE_POINTS_REFUND_DEPOSIT_TESTING', False))):
            raise ValidationError('积分退款押金扣款尚未启用')
    prior = PointsNegotiationPosting.objects.select_for_update().filter(
        points_refund_id=calc['prior_refund_id']).first()
    if not prior:
        raise ValidationError('原积分协商退款凭证缺失')
    prior = verify_posting_locked(prior)
    # The second agreement has its own point-in-time view: an add-on refund
    # may have reduced an unpaid main share after the first agreement.
    participants = prior.participants
    if isinstance(participants, list):
        from .addon_main_deduction import deductions_at
        current_deductions = deductions_at(order)
        participants = [{**item, 'prior_addon_deduction': current_deductions.get(item['account_id'], 0)}
                        for item in participants]
    refund = OrderRefund.objects.create(order=order, amount=calc['coin_units'], reason=proposal.explanation,
        operator=legacy_operator, operator_account=operator, client_request_id=request_id,
        request_fingerprint=request_fingerprint(calc['coin_units'], proposal.explanation,
            {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units}))
    proof = refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=proposal.refund_units)
    posting = PointsNegotiationPosting.objects.create(proposal=proposal, points_refund=proof, operator=operator,
        terms_hash=proposal.terms_hash, accepted_at=proposal.accepted_at,
        rounding_to_store_units=calc['rounding_to_store_units'], participants=participants,
        participants_hash=participant_digest(proposal, participants))
    wallet_tx = deficit = None
    wallet_before = plan['wallet'].balance if plan['wallet'] else 0
    deposit_before = plan['profile'].deposit_paid
    if plan['wallet_units']:
        from wallet.services import get_wallet, WalletAddressingError
        try:
            wallet = get_wallet(account_id=plan['profile'].account_id, user_id=plan['assignment'].provider_id,
                                for_update=True, create=False)
        except WalletAddressingError as exc:
            raise ValidationError('陪玩钱包归属待核对') from exc
        if not wallet or wallet.pk != plan['wallet'].pk or wallet.balance < plan['wallet_units']:
            raise ValidationError('陪玩可用余额已变化，请重新核对')
        before = wallet.balance
        wallet.balance -= plan['wallet_units']; wallet.save(update_fields=['balance'])
        wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND',
            amount=-plan['wallet_units'], balance_before=before, balance_after=wallet.balance,
            operator=legacy_operator, operator_account=operator, remark=f'积分协商退款承担：{order.order_no}')
    if plan['deposit_units']:
        from wallet.refund_deposit import charge_refund_deposit
        try:
            deficit = charge_refund_deposit(refund=refund, profile=plan['profile'], amount=plan['deposit_units'],
                                            operator=legacy_operator, operator_account=operator)
        except ValueError as exc:
            raise ValidationError('陪玩押金资金或凭证待核对') from exc
    term = proposal.provider_terms[0]
    PointsNegotiationPostingShare.objects.create(posting=posting, assignment=plan['assignment'],
        account_id=plan['profile'].account_id, original_income=term['original_income_units'], burden=term['burden_units'],
        pending_deduction=plan['pending_units'], wallet_available_before=wallet_before,
        deposit_paid_before=deposit_before, wallet_deduction=plan['wallet_units'],
        deposit_deduction=plan['deposit_units'], retained_income=term['retained_units'],
        wallet_transaction=wallet_tx, deposit_deficit=deficit)
    refund.recovered_amount = plan['wallet_units'] + plan['deposit_units']
    refund.save(update_fields=['recovered_amount'])
    posting = verify_posting_locked(posting)
    from .points_followup_revision import record_followup_revision_locked
    revision = record_followup_revision_locked(posting, calc)
    from .points_followup_release import record_followup_release_locked
    record_followup_release_locked(revision)
    return posting, revision
