"""Internal atomic posting for the bounded third transferred-points refund.

The outer workflow must close the case and terminate the confirmed segment in
the same order transaction. This primitive is not a public endpoint.
"""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .refunds import authorized_refund
    from .points_transfer_refund_access import permits
    if (not transaction.get_connection().in_atomic_block or not permits(phase='refund')
            or not operator or not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or type(case_id) is not int or case_id <= 0 or type(proposal_id) is not int or proposal_id <= 0
            or not isinstance(request_id, str) or not request_id or len(request_id) > 64
            or request_id != request_id.strip()):
        raise ValidationError('第三轮退款记账尚未开放或经办凭证无效')
    with transaction.atomic():
        return _post_locked(case_id, proposal_id, operator=operator,
                            legacy_operator=legacy_operator, request_id=request_id)


def _post_locked(case_id, proposal_id, *, operator, legacy_operator, request_id, _round=False):
    from django.utils import timezone
    from club_accounts.models import LegacyAccountMap
    from .financial_lock import lock_order_money
    from .models import (NegotiationProposal, OrderRefund, OrderProvider, PointsNegotiationPosting,
                         PointsNegotiationPostingShare, PointsFollowupTransferRevision,
                         PointsRefundRoundEvidence)
    from .negotiation_models import PointsNegotiationSegmentDeduction
    from .negotiation_proposals import _lock_case, _verified, _decisions
    from .points_transfer_refund_access import permits
    if _round:
        from .points_round_refund_preview import preview_locked
        from .points_round_funding import current_locked
    else:
        from .points_third_refund_preview import preview_locked
    from .points_negotiation_preview import asset_terms, verified_asset_terms
    from .points_negotiation_execution import participant_digest
    from .points_transfer_refund_evidence import digest as transfer_digest
    from .points_service_refund import refund_assets_locked
    from .points_third_refund_release import record_locked as record_release
    from .refunds import request_fingerprint

    order, case = _lock_case(case_id)
    if not permits(order, phase='refund'):
        raise ValidationError('第三轮退款与当前订单资金上下文不一致')
    lock_order_money()
    if legacy_operator is not None and not LegacyAccountMap.objects.select_for_update().filter(
            account=operator, legacy_user_id=legacy_operator.pk).exists():
        raise ValidationError('退款经办人的业务账号与历史身份不一致')
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    if not proposal:
        raise ValidationError('退款方案不存在')
    existing = PointsNegotiationPosting.objects.select_for_update().filter(proposal=proposal).first()
    if existing:
        # A later public workflow must separately verify the case-closure and
        # segment-settlement versions before exposing this replay to callers.
        version = PointsRefundRoundEvidence.objects.filter(posting=existing, order=order).first()
        if (not version or existing.operator_id != operator.pk
                or existing.points_refund.refund.client_request_id != request_id
                or existing.points_refund.refund.operator_id != getattr(legacy_operator, 'pk', None)):
            raise ValidationError('该方案已处理，请核对原退款记录')
        raise ValidationError('第三轮退款后的完整结案回读尚未接通')
    if OrderRefund.objects.filter(order=order, client_request_id=request_id).exists():
        raise ValidationError('操作凭证已用于其他退款')
    latest = NegotiationProposal.objects.select_for_update().filter(case=case).order_by('-version').first()
    if not latest or latest.pk != proposal.pk or proposal.status != 'ACCEPTED':
        raise ValidationError('第三轮退款方案尚未由双方确认或已更新')
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    before_state = current_locked(order) if _round else None
    calculation = (preview_locked(case.pk, refund_equivalent_units=proposal.refund_units) if _round else
                   preview_locked(case.pk, refund_equivalent_units=proposal.refund_units,
                                  _allow_unsettled_addons=True))
    if (calculation['provider_terms'] != proposal.provider_terms
            or asset_terms(calculation) != verified_asset_terms(proposal)
            or calculation['shortfall_units']):
        raise ValidationError('第三轮确认金额或可用资金已变化')
    if calculation['deposit_units']:
        from wallet.refund_deposit import enabled as deposit_enabled
        from wallet.refund_recovery_order import enabled as recovery_enabled
        if not deposit_enabled() or not recovery_enabled():
            raise ValidationError('第三轮退款押金扣款尚未启用')
    transfer = PointsFollowupTransferRevision.objects.select_for_update().select_related(
        'transfer__next_segment__attribution').get(pk=calculation['prior_version_id'], order=order)
    assignment = None
    if not (_round and calculation.get('provider_recoveries') is not None):
        assignment = OrderProvider.objects.select_for_update().get(order=order,
            provider_account_id=calculation['account_id'], released_at__isnull=True)
    if _round:
        from .points_transfer_refund_evidence import _facts
        facts = _facts(order)
    else:
        facts = transfer.snapshot['facts']
    participants = {'facts': facts, 'digest': transfer_digest(facts)}
    refund = OrderRefund.objects.create(order=order, amount=calculation['coin_units'], reason=proposal.explanation,
        operator=legacy_operator, operator_account=operator, client_request_id=request_id, created_at=timezone.now(),
        request_fingerprint=request_fingerprint(calculation['coin_units'], proposal.explanation,
            {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units}))
    proof = refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=proposal.refund_units)
    posting = PointsNegotiationPosting.objects.create(proposal=proposal, points_refund=proof, operator=operator,
        terms_hash=proposal.terms_hash, accepted_at=proposal.accepted_at,
        rounding_to_store_units=calculation['rounding_to_store_units'], participants=participants,
        participants_hash=participant_digest(proposal, participants))
    if _round and calculation.get('provider_recoveries') is not None:
        _post_round_shares(order, posting, refund, calculation, operator, legacy_operator)
        from .points_round_refund_release import record_locked as record_round_release
        record_round_release(order, posting, calculation, before_state)
        return posting
    term = proposal.provider_terms[0]
    wallet_tx = deficit = None
    if calculation['wallet_units']:
        from wallet.services import get_wallet, WalletAddressingError
        from wallet.models import Transaction
        try:
            wallet = get_wallet(account_id=calculation['account_id'], user_id=assignment.provider_id,
                                for_update=True, create=False)
        except WalletAddressingError as exc:
            raise ValidationError('第三轮陪玩钱包归属待核对') from exc
        if not wallet or wallet.balance != calculation['wallet_available_before'] or wallet.balance < calculation['wallet_units']:
            raise ValidationError('第三轮陪玩钱包余额已变化')
        before = wallet.balance
        wallet.balance -= calculation['wallet_units']
        wallet.save(update_fields=['balance'])
        wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND',
            amount=-calculation['wallet_units'], balance_before=before, balance_after=wallet.balance,
            operator=legacy_operator, operator_account=operator, remark=f'积分协商退款承担：{order.order_no}')
    if calculation['deposit_units']:
        from users.models import EscortProfile
        from wallet.refund_deposit import charge_refund_deposit
        profile = EscortProfile.objects.select_for_update().get(account_id=calculation['account_id'])
        if profile.deposit_paid != calculation['deposit_paid_before']:
            raise ValidationError('第三轮陪玩押金余额已变化')
        try:
            deficit = charge_refund_deposit(refund=refund, profile=profile, amount=calculation['deposit_units'],
                                            operator=legacy_operator, operator_account=operator)
        except ValueError as exc:
            raise ValidationError('第三轮陪玩押金资金或缺口凭证待核对') from exc
    share = PointsNegotiationPostingShare.objects.create(posting=posting, assignment=assignment,
        account_id=calculation['account_id'], original_income=term['original_income_units'],
        burden=term['burden_units'], pending_deduction=calculation['pending_units'],
        retained_income=term['retained_units'], wallet_deduction=calculation['wallet_units'],
        deposit_deduction=calculation['deposit_units'], wallet_transaction=wallet_tx, deposit_deficit=deficit,
        wallet_available_before=calculation['wallet_available_before'],
        deposit_paid_before=calculation['deposit_paid_before'])
    addon_plan = calculation.get('addon_pending_plan')
    if addon_plan is not None:
        # This plan is also included in the accepted asset digest above.
        # Do not infer a share from a payment or a provider account.
        from .models import OrderAddonPayment
        from .negotiation_models import PointsNegotiationAddonDeduction
        if _round:
            from .points_third_addon_responsibilities import current as current_responsibilities
        else:
            from .addon_handover import current_responsibilities
        from .addon_payment import verify_receipt, verify_order_debit_links
        if not isinstance(addon_plan, list):
            raise ValidationError('第三轮加购承担计划异常')
        verify_order_debit_links(order, table_exists=True, lock=True)
        seen = set()
        for position, item in enumerate(addon_plan):
            if (not isinstance(item, dict) or set(item) != {'payment_id', 'segment_id', 'addon_share_id', 'income_before', 'amount'}
                    or any(type(item[key]) is not int or item[key] <= 0 for key in ('payment_id', 'segment_id', 'amount'))
                    or type(item['income_before']) is not int or item['income_before'] < item['amount']
                    or (item['addon_share_id'] is not None and
                        (type(item['addon_share_id']) is not int or item['addon_share_id'] <= 0))):
                raise ValidationError('第三轮加购承担份额异常')
            key = (item['payment_id'], item['segment_id'], item['addon_share_id'])
            if key in seen:
                raise ValidationError('第三轮加购承担份额重复')
            seen.add(key)
            payment = OrderAddonPayment.objects.select_for_update().filter(pk=item['payment_id'], intent__order=order).first()
            if not payment:
                raise ValidationError('第三轮加购付款不存在')
            verify_receipt(payment)
            _, responsibilities = current_responsibilities(payment, lock=True, with_share_id=True)
            matching = [row for row in responsibilities if row['segment_id'] == item['segment_id']
                        and row['provider_id'] == share.account_id
                        and row.get('share_id') == item['addon_share_id']]
            if len(matching) != 1 or matching[0]['income'] != item['income_before']:
                raise ValidationError('第三轮加购实际剩余责任已变化')
            PointsNegotiationAddonDeduction.objects.create(share=share, payment=payment,
                segment_id=item['segment_id'], addon_share_id=item['addon_share_id'], position=position,
                income_before=item['income_before'], amount=item['amount'])
        main_plan = calculation.get('segment_pending_plan')
        if (not isinstance(main_plan, list)
                or any(not isinstance(item, dict) or type(item.get('amount')) is not int
                       or item['amount'] <= 0 for item in main_plan)
                or sum(item['amount'] for item in main_plan) + sum(item['amount'] for item in addon_plan)
                   != calculation['pending_units']):
            raise ValidationError('第三轮逐份额承担与本人待结扣款不一致')
    if addon_plan is None:
        if calculation['pending_units']:
            PointsNegotiationSegmentDeduction.objects.create(share=share, segment_id=calculation['segment_id'],
                                                             amount=calculation['pending_units'])
    else:
        for item in calculation['segment_pending_plan']:
            if item.get('account_id') != share.account_id or item.get('segment_id') != calculation['segment_id']:
                raise ValidationError('第三轮主单逐段承担范围异常')
            PointsNegotiationSegmentDeduction.objects.create(share=share, segment_id=item['segment_id'],
                                                             amount=item['amount'])
    refund.recovered_amount = calculation['wallet_units'] + calculation['deposit_units']
    refund.save(update_fields=['recovered_amount'])
    if _round:
        from .points_round_refund_release import record_locked as record_round_release
        record_round_release(order, posting, calculation, before_state)
    else:
        record_release(order, posting, transfer, calculation)
    return posting


def _post_round_shares(order, posting, refund, calculation, operator, legacy_operator):
    """Post each confirmed provider's actual pending, wallet and deposit use."""
    from .models import OrderProvider, OrderParticipationSegment, OrderAddonPayment, PointsNegotiationPostingShare
    from .negotiation_models import PointsNegotiationSegmentDeduction, PointsNegotiationAddonDeduction
    from .points_third_addon_responsibilities import current as current_responsibilities
    from .addon_payment import verify_receipt, verify_order_debit_links
    from users.models import EscortProfile
    from wallet.models import Transaction
    from wallet.services import get_wallet, WalletAddressingError

    recoveries, terms = calculation['provider_recoveries'], posting.proposal.provider_terms
    main, addons = calculation.get('segment_pending_plan'), calculation.get('addon_pending_plan', [])
    if (not isinstance(recoveries, list) or not recoveries or not isinstance(terms, list)
            or not isinstance(main, list) or not isinstance(addons, list)
            or [r.get('account_id') for r in recoveries] != sorted(set(r.get('account_id') for r in recoveries))
            or {r['account_id'] for r in recoveries} != {t.get('account_id') for t in terms}
            or {r['account_id'] for r in recoveries} != set(posting.proposal.affected_accounts)):
        raise ValidationError('多轮退款逐人承担计划异常')
    for field in ('pending_units', 'wallet_units', 'deposit_units', 'shortfall_units'):
        if any(type(r.get(field)) is not int or r[field] < 0 for r in recoveries) or sum(r[field] for r in recoveries) != calculation[field]:
            raise ValidationError('多轮退款逐人资金合计异常')
    segment_ids = {item.get('segment_id') for item in main + addons if isinstance(item, dict)}
    segments = {s.pk: s for s in OrderParticipationSegment.objects.select_for_update().filter(
        pk__in=segment_ids, attribution__order=order)}
    if set(segments) != segment_ids:
        raise ValidationError('多轮退款承担段不属于本单')
    if addons:
        verify_order_debit_links(order, table_exists=True, lock=True)
    for recovery in recoveries:
        account = recovery['account_id']
        matching_terms = [t for t in terms if t.get('account_id') == account]
        assignment_query = OrderProvider.objects.select_for_update().filter(
            order=order, provider_account_id=account, settled_at__isnull=True)
        if order.status != order.Status.COMPLETED:
            assignment_query = assignment_query.filter(released_at__isnull=True)
        assignments = list(assignment_query)
        if len(matching_terms) != 1 or len(assignments) != 1 or recovery['shortfall_units']:
            raise ValidationError('多轮退款本人方案或履约席位异常')
        term, assignment = matching_terms[0], assignments[0]
        own_main = [item for item in main if item.get('account_id') == account]
        own_addons = [item for item in addons if segments[item['segment_id']].provider_account_id == account]
        if (any(segments[item['segment_id']].provider_account_id != account or type(item.get('amount')) is not int
                or item['amount'] <= 0 for item in own_main)
                or sum(item['amount'] for item in own_main + own_addons) != recovery['pending_units']
                or recovery['pending_units'] + recovery['wallet_units'] + recovery['deposit_units'] != term['burden_units']):
            raise ValidationError('多轮退款本人逐段承担不一致')
        wallet_tx = deficit = None
        if recovery['wallet_units']:
            try:
                wallet = get_wallet(account_id=account, user_id=assignment.provider_id, for_update=True, create=False)
            except WalletAddressingError as exc:
                raise ValidationError('陪玩钱包归属待核对') from exc
            if not wallet or wallet.balance != recovery['wallet_available_before'] or wallet.balance < recovery['wallet_units']:
                raise ValidationError('陪玩钱包余额已变化')
            before = wallet.balance
            wallet.balance -= recovery['wallet_units']
            wallet.save(update_fields=['balance'])
            wallet_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND',
                amount=-recovery['wallet_units'], balance_before=before, balance_after=wallet.balance,
                operator=legacy_operator, operator_account=operator, remark=f'积分协商退款承担：{order.order_no}')
        if recovery['deposit_units']:
            from wallet.refund_deposit import charge_refund_deposit
            profile = EscortProfile.objects.select_for_update().get(account_id=account)
            if profile.deposit_paid != recovery['deposit_paid_before']:
                raise ValidationError('陪玩押金余额已变化')
            try:
                deficit = charge_refund_deposit(refund=refund, profile=profile, amount=recovery['deposit_units'],
                                                operator=legacy_operator, operator_account=operator)
            except ValueError as exc:
                raise ValidationError('陪玩押金扣款待核对') from exc
        share = PointsNegotiationPostingShare.objects.create(posting=posting, assignment=assignment,
            account_id=account, original_income=term['original_income_units'], burden=term['burden_units'],
            pending_deduction=recovery['pending_units'], retained_income=term['retained_units'],
            wallet_deduction=recovery['wallet_units'], deposit_deduction=recovery['deposit_units'],
            wallet_transaction=wallet_tx, deposit_deficit=deficit,
            wallet_available_before=recovery['wallet_available_before'], deposit_paid_before=recovery['deposit_paid_before'])
        for item in own_main:
            PointsNegotiationSegmentDeduction.objects.create(share=share, segment_id=item['segment_id'], amount=item['amount'])
        for position, item in enumerate(own_addons):
            payment = OrderAddonPayment.objects.select_for_update().filter(pk=item['payment_id'], intent__order=order).first()
            if not payment:
                raise ValidationError('加购付款不存在')
            verify_receipt(payment)
            _, rows = current_responsibilities(payment, lock=True, with_share_id=True)
            matching = [row for row in rows if row['provider_id'] == account and row['segment_id'] == item['segment_id']
                        and row.get('share_id') == item['addon_share_id']]
            if (len(matching) != 1 or matching[0]['income'] != item['income_before']
                    or not 0 < item['amount'] <= item['income_before']):
                raise ValidationError('加购实际剩余份额已变化')
            PointsNegotiationAddonDeduction.objects.create(share=share, payment=payment,
                segment_id=item['segment_id'], addon_share_id=item['addon_share_id'], position=position,
                income_before=item['income_before'], amount=item['amount'])
    refund.recovered_amount = calculation['wallet_units'] + calculation['deposit_units']
    refund.save(update_fields=['recovered_amount'])
