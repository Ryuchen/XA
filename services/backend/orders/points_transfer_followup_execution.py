"""Internal atomic posting for a second transferred-points refund.

The caller must close the aftersale and reconcile later settlement in the
same wider workflow; no public endpoint is enabled by this primitive.
"""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from .financial_lock import lock_order_money
from .models import (OrderRefund, OrderPointsTransferFollowupFundingRevision,
                     PointsNegotiationPosting, PointsNegotiationPostingShare)
from .negotiation_proposals import _lock_case
from .points_checkout_test import require_isolated_checkout
from .refunds import authorized_refund, request_fingerprint


def post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .points_transfer_refund_access import permits
    formal = permits(phase='refund')
    if not formal:
        require_isolated_checkout()
    from .points_transfer_followup_preview import internal_agreement_enabled
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('转单追加退款必须在订单事务中执行')
    if ((not formal and (not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_POSTING_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING', False)))
            or not internal_agreement_enabled()
            or not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or type(case_id) is not int or case_id <= 0 or type(proposal_id) is not int or proposal_id <= 0
            or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip()):
        raise ValidationError('转单追加退款记账尚未开放或经办凭证无效')
    if legacy_operator is not None and not LegacyAccountMap.objects.select_for_update().filter(
            account=operator, legacy_user_id=legacy_operator.pk).exists():
        raise ValidationError('退款经办人的业务账号与历史身份不一致')
    # Roll back asset returns, debits, evidence and releases even when the
    # caller catches an exception inside a longer-running transaction.
    with transaction.atomic():
        return _post_locked(case_id, proposal_id, operator=operator,
                            legacy_operator=legacy_operator, request_id=request_id)


def _post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from wallet.models import Transaction
    from .points_followup_execution import prepare_followup_locked
    from .points_negotiation_execution import participant_digest, verify_posting_locked
    from .points_negotiation_termination import verify_termination_locked
    from .points_service_refund import refund_assets_locked
    from .points_transfer_followup_revision import record_locked, verify_locked
    from .points_transfer_refund_evidence import _prior_settlement
    order, case = _lock_case(case_id)
    lock_order_money()
    existing = PointsNegotiationPosting.objects.select_for_update().filter(proposal_id=proposal_id,
                                                                             proposal__case=case).first()
    if existing:
        if (existing.operator_id != operator.pk or existing.points_refund.refund.client_request_id != request_id
                or existing.points_refund.refund.operator_id != getattr(legacy_operator, 'pk', None)):
            raise ValidationError('该方案已处理，请核对原退款记录')
        row = OrderPointsTransferFollowupFundingRevision.objects.select_for_update().filter(posting=existing).first()
        if not row:
            raise ValidationError('转单追加退款的资金凭证缺失')
        from .models import OrderSegmentSettlementManifest
        manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
        terms = manifest.plan.get('points_funding', {}) if manifest and isinstance(manifest.plan, dict) else {}
        if terms.get('followup_posting_id') == existing.pk:
            from .points_transfer_followup_termination import verify_closed_or_settled_locked
            verify_closed_or_settled_locked(existing)
        else:
            verify_locked(row)
        return existing
    if OrderRefund.objects.filter(order=order, client_request_id=request_id).exists():
        raise ValidationError('操作凭证已用于其他退款')
    plan = prepare_followup_locked(case_id, proposal_id, operator=operator)
    from .points_transfer_refund_access import permits
    calculation, proposal = plan['calculation'], plan['proposal']
    if any(person['deposit_units'] for person in plan['providers']):
        from wallet.refund_deposit import enabled as deposit_enabled
        from wallet.refund_recovery_order import enabled as recovery_enabled
        if (not deposit_enabled() or (permits(order, phase='refund') and not recovery_enabled())
                or (not permits(order, phase='refund') and not getattr(settings, 'SERVICE_POINTS_REFUND_DEPOSIT_TESTING', False))):
            raise ValidationError('积分退款押金扣款尚未启用')
    first = PointsNegotiationPosting.objects.select_for_update().filter(
        points_refund_id=calculation['prior_refund_id']).first()
    if not first or not isinstance(first.participants, (dict, list)):
        raise ValidationError('首次转单退款凭证缺失')
    first = verify_termination_locked(first)
    if permits(order, phase='refund'):
        from .points_transfer_followup_scope import supported_addon, addon_chain_before
        from .models import OrderAddonPayment
        if OrderAddonPayment.objects.filter(intent__order=order).exists() and not supported_addon(order, first):
            addon_chain_before(order, first, timezone.now())
    from .models import PointsPostRefundTransferRevision
    version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
    participants = first.participants
    if isinstance(participants, list) and not version:
        raise ValidationError('首次退款后的实际转单资金版本缺失')
    if version:
        from .points_post_refund_transfer import verify_locked as verify_transfer_version
        from .points_transfer_refund_evidence import digest as transfer_digest
        verify_transfer_version(version)
        transfer_facts = version.snapshot['facts']
        participants = {'facts': transfer_facts, 'digest': transfer_digest(transfer_facts)}
    prior_manifest = _prior_settlement(order)
    refund = OrderRefund.objects.create(order=order, amount=calculation['coin_units'], reason=proposal.explanation,
        operator=legacy_operator, operator_account=operator, client_request_id=request_id, created_at=timezone.now(),
        request_fingerprint=request_fingerprint(calculation['coin_units'], proposal.explanation,
            {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units}))
    proof = refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=proposal.refund_units)
    posting = PointsNegotiationPosting.objects.create(proposal=proposal, points_refund=proof, operator=operator,
        terms_hash=proposal.terms_hash, accepted_at=proposal.accepted_at,
        rounding_to_store_units=calculation['rounding_to_store_units'], participants=participants,
        participants_hash=participant_digest(proposal, participants))
    for person in plan['providers']:
        wallet_tx = deficit = None
        wallet_before = person['wallet'].balance if person['wallet'] else 0
        deposit_before = person['profile'].deposit_paid
        if person['wallet_units']:
            from wallet.services import get_wallet, WalletAddressingError
            try:
                wallet = get_wallet(account_id=person['profile'].account_id, user_id=person['assignment'].provider_id,
                                    for_update=True, create=False)
            except WalletAddressingError as exc:
                raise ValidationError('陪玩钱包归属待核对') from exc
            if not wallet or wallet.pk != person['wallet'].pk or wallet.balance < person['wallet_units']:
                raise ValidationError('陪玩可用余额已变化，请重新核对')
            before = wallet.balance
            wallet.balance -= person['wallet_units']; wallet.save(update_fields=['balance'])
            wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND',
                amount=-person['wallet_units'], balance_before=before, balance_after=wallet.balance,
                operator=legacy_operator, operator_account=operator, remark=f'积分协商退款承担：{order.order_no}')
        if person['deposit_units']:
            from wallet.refund_deposit import charge_refund_deposit
            try:
                deficit = charge_refund_deposit(refund=refund, profile=person['profile'], amount=person['deposit_units'],
                                                operator=legacy_operator, operator_account=operator)
            except ValueError as exc:
                raise ValidationError('陪玩押金资金或凭证待核对') from exc
        term = person['term']
        share = PointsNegotiationPostingShare.objects.create(posting=posting, assignment=person['assignment'],
            account_id=person['profile'].account_id, original_income=term['original_income_units'], burden=term['burden_units'],
            pending_deduction=person['pending_units'], wallet_available_before=wallet_before,
            deposit_paid_before=deposit_before, wallet_deduction=person['wallet_units'], deposit_deduction=person['deposit_units'],
            retained_income=term['retained_units'], wallet_transaction=wallet_tx, deposit_deficit=deficit)
        segment_plan = proposal.points_terms.segment_pending_plan
        if segment_plan is not None:
            from .negotiation_models import PointsNegotiationSegmentDeduction
            from .models import OrderParticipationSegment
            parts = [item for item in segment_plan if item['account_id'] == person['profile'].account_id]
            if parts:
                ids = [item['segment_id'] for item in parts]
                valid = set(OrderParticipationSegment.objects.filter(pk__in=ids,
                    attribution__order=order, provider_account_id=person['profile'].account_id)
                    .values_list('pk', flat=True))
                if (len(ids) != len(set(ids)) or set(ids) != valid
                        or any(type(item['amount']) is not int or item['amount'] <= 0 for item in parts)
                        or sum(item['amount'] for item in parts) != person['pending_units']):
                    raise ValidationError('本人协商退款分段扣减计划异常')
                PointsNegotiationSegmentDeduction.objects.bulk_create([
                    PointsNegotiationSegmentDeduction(share=share, segment_id=item['segment_id'], amount=item['amount'])
                    for item in parts])
    if proposal.points_terms.segment_pending_plan is not None:
        from .negotiation_models import PointsNegotiationSegmentDeduction
        actual = [{'account_id': account, 'segment_id': segment, 'amount': amount}
            for account, segment, amount in PointsNegotiationSegmentDeduction.objects.filter(
                share__posting=posting).values_list('share__account_id', 'segment_id', 'amount')]
        key = lambda item: (item['account_id'], item['segment_id'])
        if sorted(actual, key=key) != sorted(proposal.points_terms.segment_pending_plan, key=key):
            raise ValidationError('协商退款逐段扣减未完整写入')
    refund.recovered_amount = sum(person['wallet_units'] + person['deposit_units'] for person in plan['providers'])
    refund.save(update_fields=['recovered_amount'])
    posting = verify_posting_locked(posting)
    record_locked(posting, first, calculation, prior_manifest)
    return posting
