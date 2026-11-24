"""Receipt-local current-read proof for one narrow transferred points refund."""
from copy import copy

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError

from .models import OrderPointsFundingAllocation, OrderPointsFundingRelease


def facts(row):
    from users.receipt_usage_sources import _digest
    from .points_transfer_refund_evidence import current_read_matches_history, digest as transfer_digest
    from .points_negotiation_execution import participant_digest
    from .points_negotiation_preview import asset_digest, funding_after_refund
    from .points_funding_release import first_release_hash_matches
    from .points_funding_allocation import plan_release
    from .points_funding_plan import plan_service_funding
    from .negotiation_proposals import _digest as proposal_digest, _snapshot_terms
    from .refund_math import payment_refund, wallet_source_refund, provider_refund_burden
    from .refunds import request_fingerprint
    from .settlement import ProviderShare
    posting, allocation = row.posting, row.allocation
    historical_witness = row.pk == 0 and row.amount == 0
    proposal, proof = posting.proposal, posting.points_refund
    order, refund = proposal.case.order, proof.refund
    evidence = getattr(posting, 'funding_release_evidence', None)
    terms = getattr(proposal, 'points_terms', None)
    participants = posting.participants
    later_order_witness = bool(historical_witness and evidence and evidence.allocations == []
        and allocation.service_order_id == order.pk and allocation.created_at > posting.created_at)
    if (not evidence or not terms or terms.prior_refund_id or not isinstance(participants, dict)
            or set(participants) != {'facts', 'digest'} or not isinstance(participants['facts'], dict)
            or participants['digest'] != transfer_digest(participants['facts'])
            or posting.participants_hash != participant_digest(proposal, participants)
            or evidence.original_settlement_id is not None
            or (not later_order_witness and (allocation.redemption_id != proof.redemption_id
                                             or allocation.service_order_id is not None))
            or proof.redemption.order_id != order.pk
            or refund.order_id != order.pk or refund.amount != proof.coin_units
            or refund.operator_account_id != posting.operator_id or proof.equivalent_units != proposal.refund_units
            or order.refund_records.filter(pk__lte=refund.pk).count() != 1):
        raise ValidationError('转单退款原付款或补资凭证异常')
    frozen = participants['facts']
    prior_settlement = frozen.get('prior_settlement')
    transfer_history = {key: value for key, value in frozen.items() if key != 'prior_settlement'}
    paid_source = None
    if prior_settlement is not None:
        from .points_transfer_paid_addons import verify_first_refund_receipt
        try:
            paid_source = verify_first_refund_receipt(order, posting, prior_settlement,
                before=getattr(row, 'history_before', None),
                receipt_id=getattr(row, 'read_receipt_id', allocation.receipt_id))
        except RestValidationError as exc:
            raise ValidationError('首次退款前已付履约及加购凭证异常') from exc
    if (not isinstance(frozen.get('transfers'), list)
            or not frozen['transfers']
            or not current_read_matches_history(order, getattr(row, 'read_receipt_id', allocation.receipt_id),
                                                transfer_history, posting.created_at)):
        raise ValidationError('转单退款履约历史不一致')
    events = frozen['transfers']
    segments = {item['id']: item for item in frozen.get('segments', []) if isinstance(item, dict) and 'id' in item}
    outgoing = {event.get('previous_segment_id'): event for event in events if isinstance(event, dict)}
    incoming = {event.get('next_segment_id'): event for event in events if isinstance(event, dict)}
    slots = {}
    if (not segments or len(segments) != len(frozen.get('segments', []))
            or len(outgoing) != len(events) or len(incoming) != len(events)):
        raise ValidationError('转单履约份额不守恒')
    for event in events:
        previous, following = segments.get(event.get('previous_segment_id')), segments.get(event.get('next_segment_id'))
        if (not previous or not following or previous['id'] == following['id']
                or previous['slot'] != following['slot']
                or event.get('basis_before') != event.get('retained_basis', -1) + event.get('transferred_basis', -1)
                or event.get('original_share_before') != event.get('retained_share', -1) + event.get('transferred_share', -1)):
            raise ValidationError('转单履约份额不守恒')
        slots[previous['slot']] = slots.get(previous['slot'], 0) + 1
    if any(count > 2 for count in slots.values()):
        raise ValidationError('同一席位转单次数超过上限')
    from .models import OrderAddonFundingRelease, OrderPointsTransferFundingRelease, OrderPointsTransferFundingRevision
    from .models import OrderAddonPayment
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=posting.created_at).select_related('intent', 'buyer_transaction__wallet'))
    if (OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=posting.created_at).exists()
            or any(item.refund_records.filter(created_at=posting.created_at).exists() for item in addons)):
        raise ValidationError('转单退款前的加购退款时序或次数异常')
    if bool(addons) != (evidence.addon_funding_snapshot is not None):
        raise ValidationError('转单退款加购快照缺失')
    if prior_settlement is not None and addons:
        from .points_transfer_paid_addons import unchanged, parts_before
        try:
            unchanged(prior_settlement,
                      parts_before(order, evidence.addon_funding_snapshot, posting.created_at))
        except RestValidationError as exc:
            raise ValidationError('首次退款改变了已付加购履约份额') from exc
    addon_payload = None
    if addons:
        from .points_addon_receipt_usage import payment_facts, facts as addon_refund_facts
        witness = getattr(row, 'read_receipt_id', allocation.receipt_id)
        if len(addons) > 1:
            from .points_addon_receipt_usage import _prior_addon_events
            from .models import OrderAddonHandover
            from .addon_handover import verify_first_handover, verify_handover
            from .points_returned_addon_refund import before_first_main_refund, verify_funding
            returned = before_first_main_refund(order)
            if returned:
                verify_funding(order, returned, receipt_id=witness)
            verifier = verify_handover if returned else verify_first_handover
            handover_facts = []
            for handover in OrderAddonHandover.objects.filter(payment__in=addons, status='ACCEPTED',
                    accepted_at__lt=posting.created_at).select_related('payment__intent').order_by('accepted_at', 'pk'):
                parts = verifier(handover.payment, handover, as_of=posting.created_at, receipt_id=witness)
                decisions = list(handover.decisions.order_by('account_id').values_list(
                    'account_id', 'role', 'accepted', 'decided_at'))
                handover_facts.append([handover.pk, handover.payment_id, handover.accepted_at.isoformat(),
                    [[account, role, accepted, at.isoformat()] for account, role, accepted, at in decisions],
                    [[part.pk, part.segment_id, part.basis, part.income, part.commission_snapshot]
                     for part in parts]])
            addon_payload = [_prior_addon_events(order, posting.created_at, read_receipt_id=witness), handover_facts]
        else:
            addon_payload = payment_facts(addons[0], read_receipt_id=witness)
            prior_refunds = list(addons[0].refund_records.filter(created_at__lt=posting.created_at).order_by('pk'))
            if prior_refunds:
                addon_payload = [addon_payload, [addon_refund_facts(item,
                    read_receipt_id=witness) for item in prior_refunds]]
            prior_handover = addons[0].handovers.filter(status='ACCEPTED',
                accepted_at__lt=posting.created_at).order_by('accepted_at', 'pk').first()
            if prior_handover and any(item.created_at < prior_handover.accepted_at for item in prior_refunds):
                from .addon_handover import verify_first_handover
                from .addon_funding_release import verify_run
                from .models import OrderAddonFundingReleaseRun
                parts = verify_first_handover(addons[0], prior_handover, as_of=posting.created_at,
                                               receipt_id=witness)
                handover_run = OrderAddonFundingReleaseRun.objects.filter(handover=prior_handover).first()
                if not handover_run:
                    raise ValidationError('主单退款前加购接替释放凭证缺失')
                verify_run(handover_run, _skip_usage=True, witness_receipt_id=witness)
                addon_payload = [addon_payload, {'handover_id': prior_handover.pk,
                    'run_id': handover_run.pk, 'run_hash': handover_run.points_handover_hash,
                    'shares': [[part.pk, part.segment_id, part.basis, part.income] for part in parts]}]
    prior_addon = list(OrderAddonFundingRelease.objects.filter(allocation__redemption_id=proof.redemption_id,
        run__created_at__lt=posting.created_at).select_related('run', 'allocation__redemption').order_by('pk'))
    if [[part.pk, part.allocation_id, part.run_id, part.amount] for part in prior_addon] != evidence.prior_addon_release_snapshot:
        raise ValidationError('转单退款前加购释放凭证异常')
    from users.receipt_usage_sources import _addon_release_facts
    for part in prior_addon:
        if part.run.prestart_handoff_id:
            if part.run.prestart_handoff.order_id != order.pk:
                raise ValidationError('改派释放不属于该订单')
        elif not addons or not (part.run.payment_id in {item.pk for item in addons}
                or (part.run.handover_id and part.run.handover.payment_id in {item.pk for item in addons}
                    and part.run.handover.accepted_at < posting.created_at)
                or (part.run.refund_id and part.run.refund.payment_id in {item.pk for item in addons}
                    and part.run.refund.created_at < posting.created_at)):
            raise ValidationError('转单退款前加购释放来源异常')
        _addon_release_facts(part, witness_receipt_id=getattr(row, 'read_receipt_id', allocation.receipt_id))
    shares = list(posting.shares.select_related('assignment').order_by('account_id'))
    if not 1 <= len(shares) <= 2 or len({item.account_id for item in shares}) != len(shares):
        raise ValidationError('转单退款参与人范围异常')
    share = shares[0]
    decisions = list(proposal.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    episode = copy(proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    if (set((a, b, c) for a, b, c, _ in decisions) != (
            {(order.customer_account_id, 'BOSS', True)} | {(item.account_id, 'PROVIDER', True) for item in shares})
            or any(not d for _, _, _, d in decisions) or proposal.accepted_at != max(d for _, _, _, d in decisions)
            or proposal.status != 'ACCEPTED' or proposal.affected_accounts != [item.account_id for item in shares]
            or _snapshot_terms(proposal.provider_terms, proposal.affected_accounts) != proposal.provider_terms
            or proposal.terms_hash != proposal_digest(episode, proposal.version, proposal.affected_accounts,
                proposal.refund_units, proposal.provider_terms, proposal.explanation, proposal.affected_segment_ids)
            or posting.terms_hash != proposal.terms_hash or posting.accepted_at != proposal.accepted_at
            or refund.reason != proposal.explanation or not refund.client_request_id
            or refund.request_fingerprint != request_fingerprint(refund.amount, refund.reason,
                {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units})
            or refund.recovered_amount != sum(item.wallet_deduction + item.deposit_deduction for item in shares)):
        raise ValidationError('转单退款双方确认或收益承担异常')
    for participant in shares:
        if (participant.assignment.order_id != order.pk or participant.assignment.provider_account_id != participant.account_id
                or participant.pending_deduction != min(participant.burden, participant.original_income)
                or participant.wallet_deduction != min(participant.burden - participant.pending_deduction, participant.wallet_available_before)
                or participant.deposit_deduction != participant.burden - participant.pending_deduction - participant.wallet_deduction
                or participant.deposit_deduction > participant.deposit_paid_before):
            raise ValidationError('转单退款个人收益承担异常')
    def provider_evidence(share):
        wallet_tx = share.wallet_transaction
        if share.wallet_deduction:
            if (not wallet_tx or wallet_tx.order_id != order.pk or wallet_tx.wallet.account_id != share.account_id
                    or wallet_tx.wallet.user_id != share.assignment.provider_id or wallet_tx.tx_type != 'REFUND'
                    or wallet_tx.status != 'SUCCESS' or wallet_tx.amount != -share.wallet_deduction
                    or wallet_tx.balance_before != share.wallet_available_before
                    or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount
                    or wallet_tx.operator_account_id != posting.operator_id or wallet_tx.operator_id != refund.operator_id
                    or wallet_tx.reversal_of_id or wallet_tx.reversals.exists()):
                raise ValidationError('转单退款钱包承担凭证异常')
        elif wallet_tx:
            raise ValidationError('转单退款存在多余钱包承担流水')
        deficit = share.deposit_deficit
        if share.deposit_deduction:
            from django.conf import settings
            store_tx = deficit.source_store_transaction if deficit else None
            if (not deficit or deficit.refund_id != refund.pk or deficit.addon_refund_id
                    or deficit.source_kind != 'DIRECT' or deficit.source_provider_transaction_id
                    or deficit.profile.account_id != share.account_id or deficit.profile.user_id != share.assignment.provider_id
                    or deficit.account_id != share.account_id or deficit.amount != share.deposit_deduction
                    or deficit.direct_key != f'{refund.pk}:{deficit.profile_id}' or not store_tx
                    or store_tx.order_id != order.pk or store_tx.tx_type != 'DEPOSIT_INCOME'
                    or store_tx.status != 'SUCCESS' or store_tx.amount != -deficit.amount
                    or store_tx.balance_after != store_tx.balance_before + store_tx.amount
                    or not store_tx.wallet.user_id or store_tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                    or store_tx.operator_account_id != posting.operator_id or store_tx.operator_id != refund.operator_id
                    or store_tx.reversal_of_id or store_tx.reversals.exists()):
                raise ValidationError('转单退款押金承担凭证异常')
        elif deficit:
            raise ValidationError('转单退款存在多余押金缺口')
        return wallet_tx, deficit
    provider_evidence_rows = [provider_evidence(item) for item in shares]
    wallet_tx, deficit = provider_evidence_rows[0]
    assets = {'redemption_id': terms.redemption_id, 'coin_units': terms.coin_units,
        'points_hundredths': terms.points_hundredths, 'prepaid_units': terms.prepaid_units,
        'gift_units': terms.gift_units, 'compensation_units': terms.compensation_units, 'is_final': terms.is_final}
    if (terms.evidence_hash != asset_digest(proposal, assets)
            or (terms.redemption_id, terms.coin_units, terms.points_hundredths, terms.prepaid_units,
                terms.gift_units, terms.compensation_units, terms.is_final) !=
               (proof.redemption_id, proof.coin_units, proof.points_hundredths, proof.prepaid_units,
                proof.gift_units, proof.compensation_units, proof.is_final)):
        raise ValidationError('转单退款资产构成异常')
    original, spent = proof.redemption.payment, proof.redemption.ledger
    if (original.order_id != order.pk or original.wallet.account_id != order.customer_account_id
            or original.tx_type != 'PAY' or original.status != 'SUCCESS' or original.amount != -proof.redemption.paid_units
            or original.balance_after != original.balance_before + original.amount or original.reversal_of_id
            or original.reversals.exists() or spent.order_id != order.pk or spent.account_id != order.customer_account_id
            or spent.kind != 'ORDER_POINTS_REDEEM' or spent.amount != -proof.redemption.points_hundredths
            or spent.event_key != f'order:{order.pk}:points-redemption'):
        raise ValidationError('转单退款原付款或积分流水异常')
    coin, points = proof.coin_transaction, proof.points_ledger
    if proof.coin_units and (not coin or coin.order_id != order.pk or coin.wallet_id != original.wallet_id
            or coin.tx_type != 'REFUND' or coin.status != 'SUCCESS' or coin.amount != proof.coin_units
            or coin.balance_after != coin.balance_before + coin.amount or coin.reversal_of_id or coin.reversals.exists()
            or coin.operator_account_id != posting.operator_id or coin.operator_id != refund.operator_id):
        raise ValidationError('转单退款店币流水异常')
    if proof.points_hundredths and (not points or points.order_id != order.pk
            or points.account_id != order.customer_account_id or points.kind != 'ORDER_POINTS_RETURN'
            or points.amount != proof.points_hundredths or points.operator_id != posting.operator_id
            or points.event_key != f'order:{order.pk}:points-return:{refund.pk}'):
        raise ValidationError('转单退款积分流水异常')
    if (not proof.coin_units and coin) or (not proof.points_hundredths and points):
        raise ValidationError('转单退款存在多余资产流水')
    sources = list(order.funding_allocations.order_by('pk'))
    from engagement.funding_wallet import validate_order_funding_sources
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('转单退款原资金来源异常') from exc
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    returned = {kind: sum(item.refunded_amount for item in sources if item.kind == kind) for kind in paid}
    all_proofs = list(proof.redemption.refunds.order_by('refund_id'))
    if (set(order.refund_records.values_list('pk', flat=True)) != {item.refund_id for item in all_proofs}
            or sum(paid.values()) != proof.redemption.paid_units or returned != {
                'PREPAID': sum(item.prepaid_units for item in all_proofs),
                'GIFT': sum(item.gift_units for item in all_proofs),
                'COMPENSATION': sum(item.compensation_units for item in all_proofs)}):
        raise ValidationError('转单退款与原付款来源不一致')
    from engagement.funding_plan import RefundablePart, FundingPlanError, plan_refund
    try:
        parts = [RefundablePart(item.pk, item.kind, item.amount) for item in sources]
        historical_sources = plan_refund(parts, proof.coin_units)
        current_sources = plan_refund(parts, sum(item.coin_units for item in all_proofs))
    except FundingPlanError as exc:
        raise ValidationError('转单退款逐批付款来源异常') from exc
    if any(item.refunded_amount != current_sources[item.pk] for item in sources):
        raise ValidationError('转单退款累计资金批次异常')
    try:
        expected = payment_refund(paid_coin_units=proof.redemption.paid_units,
            paid_point_hundredths=proof.redemption.points_hundredths, point_value_units=proof.redemption.discount_units,
            refund_equivalent_units=proof.equivalent_units, previously_refunded_equivalent_units=0,
            previously_refunded_coin_units=0, previously_refunded_point_hundredths=0)
        returned_plan = wallet_source_refund(coin_units=expected.coin_units, paid_prepaid_units=paid['PREPAID'],
            paid_gift_units=paid['GIFT'], paid_compensation_units=paid['COMPENSATION'], refunded_prepaid_units=0,
            refunded_gift_units=0, refunded_compensation_units=0)
        if (proof.coin_units, proof.points_hundredths, proof.is_final, proof.prepaid_units, proof.gift_units,
                proof.compensation_units) != (expected.coin_units, expected.point_hundredths, expected.is_final,
                returned_plan.prepaid_units, returned_plan.gift_units, returned_plan.compensation_units):
            raise ValidationError('转单退款原来源计算不一致')
        segment_shares, lines = [], []
        for item in frozen['segments']:
            outgoing_event = outgoing.get(item['id'])
            ended = outgoing_event is not None
            basis = outgoing_event['retained_basis'] if ended else 0 if item['pre_service_released'] else item['incoming_basis']
            fee = ((item['fixed_rule_total'] * basis // frozen['deal_amount']) if ended else
                   0 if item['pre_service_released'] else item['commission_fixed']) if item['commission_type'] == 'FIXED' \
                  else basis * item['commission_rate'] // 100
            if fee > basis:
                raise ValidationError('转单收益抽成异常')
            segment_shares.append(ProviderShare(basis, item['commission_type'], item['commission_rate'],
                                                fee if item['commission_type'] == 'FIXED' else 0, basis - fee))
            lines.append((item['id'], item['account_id'], basis, fee, basis - fee))
        if prior_settlement is not None:
            from types import SimpleNamespace
            from .points_transfer_followup_revision import _verify_manifest_terms
            _verify_manifest_terms(prior_settlement, posting, SimpleNamespace(segment_shares=tuple(lines)),
                                   frozen, cutoff=posting.created_at)
        rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
        original_plan = plan_service_funding(deal_units=proof.redemption.deal_units,
            point_discount_units=proof.redemption.discount_units, shares=segment_shares, inviter_rate=rate,
            principal_units=paid['PREPAID'], gift_units=paid['GIFT'], compensation_units=paid['COMPENSATION'])
        by_segment = {item['id']: item for item in frozen['segments']}
        selected = set(proposal.affected_segment_ids or [])
        from .addon_main_segment_deduction import verified_deductions
        prior_earnings = verified_deductions(order, before=posting.created_at)
        if any(prior_earnings.get(line[0], 0) > line[4] for line in lines):
            raise ValidationError('加购退款已占履约段收益异常')
        incomes = {person.account_id: sum(line[4] - prior_earnings.get(line[0], 0) for line in lines
                   if line[1] == person.account_id and by_segment[line[0]]['started_at']
                   and (not selected or line[0] in selected)) for person in shares}
        weights = {person.account_id: sum(item['original_share'] for item in frozen['segments']
                   if item['account_id'] == person.account_id and item['started_at']
                   and (not selected or item['id'] in selected)) for person in shares}
        burden = provider_refund_burden(refund_equivalent_units=proposal.refund_units,
                                        original_price_weights=weights)
        expected_terms = [{'account_id': person.account_id, 'original_income_units': incomes[person.account_id],
            'burden_units': burden.amounts[person.account_id],
            'retained_units': max(incomes[person.account_id] - burden.amounts[person.account_id], 0),
            'additional_units': max(burden.amounts[person.account_id] - incomes[person.account_id], 0)} for person in shares]
        if (proposal.provider_terms != expected_terms or posting.rounding_to_store_units != burden.rounding_to_store_units
                or any((person.original_income, person.burden, person.retained_income) !=
                       (term['original_income_units'], term['burden_units'], term['retained_units'])
                       for person, term in zip(shares, expected_terms))):
            raise ValidationError('转单退款承担与履约份额不一致')
        items = evidence.allocations
        if (not isinstance(items, list) or (not items and not historical_witness) or any(not isinstance(i, list) or len(i) != 4
                or any(type(v) is not int for v in i) or not 0 <= i[3] <= i[1] for i in items)):
            raise ValidationError('转单退款分配快照异常')
        original_rows = list(OrderPointsFundingAllocation.objects.filter(redemption_id=proof.redemption_id,
            created_at__lte=refund.created_at).order_by('pk'))
        from .points_transfer_receipt_usage import facts as transfer_release_facts
        prior_releases = list(OrderPointsTransferFundingRelease.objects.filter(
            allocation__in=original_rows, revision__created_at__lt=posting.created_at)
            .select_related('allocation__redemption', 'allocation__receipt', 'revision__order').order_by('pk'))
        released = {}
        for prior_release in prior_releases:
            from types import SimpleNamespace
            transfer_release_facts(SimpleNamespace(revision=prior_release.revision, allocation=prior_release.allocation,
                pk=prior_release.pk, amount=prior_release.amount,
                read_receipt_id=getattr(row, 'read_receipt_id', allocation.receipt_id)))
            released[prior_release.allocation_id] = released.get(prior_release.allocation_id, 0) + prior_release.amount
        for prior_release in prior_addon:
            released[prior_release.allocation_id] = released.get(prior_release.allocation_id, 0) + prior_release.amount
        if ([[x.pk, x.amount, x.receipt_id, released.get(x.pk, 0)] for x in original_rows] != items
                or (not any(x.pk == allocation.pk for x in original_rows)
                    and not (historical_witness and not items and allocation.created_at > refund.created_at))):
            raise ValidationError('转单退款分配记录已变化')
        from django.utils.dateparse import parse_datetime
        transfer_time = max(parse_datetime(item['created_at']) for item in events)
        if not transfer_time:
            raise ValidationError('积分转单时间缺失')
        net = sum(item[1] - item[3] for item in items)
        after = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
            compensation_units=paid['COMPENSATION'], returned_prepaid_units=proof.prepaid_units,
            returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
            provider_income_units=original_plan.provider_income_units,
            pending_deduction_units=sum(person.pending_deduction for person in shares),
            provider_external_units=sum(person.wallet_deduction + person.deposit_deduction for person in shares),
            inviter_rate=rate, allocated_units=net, rounding_reserved_units=posting.rounding_to_store_units)
        if addons:
            from collections import defaultdict
            from .addon_funding_history import combined_at
            from .refunded_addon_funding import combine_refunded_history
            retained = defaultdict(int)
            for _, account_id, _, _, income_value in lines:
                retained[account_id] += income_value
            adjusted = {line[0]: line[4] for line in lines}
            for person in shares:
                retained[person.account_id] -= person.pending_deduction
                chosen = [line[0] for line in lines if line[1] == person.account_id and by_segment[line[0]]['started_at']
                          and (not selected or line[0] in selected)]
                if person.pending_deduction and len(chosen) != 1:
                    raise ValidationError('主单退款扣减须明确唯一履约段')
                if person.pending_deduction:
                    adjusted[chosen[0]] -= person.pending_deduction
            history = combined_at(order, after, posting.created_at, segment_shares=[
                (line[0], line[1], line[2], line[3], adjusted[line[0]]) for line in lines])
            if history['addon_history'] != evidence.addon_funding_snapshot:
                raise ValidationError('转单退款加购历史与快照不一致')
            after = combine_refunded_history(after, history, main_retained_by_account=retained)
        plan = plan_release(allocations=[(i[0], i[1], i[3]) for i in items], amount=after.release_units)
    except (ValueError, TypeError, KeyError, IndexError, RestValidationError) as exc:
        raise ValidationError('转单退款资金或履约计算异常') from exc
    actual = list(OrderPointsFundingRelease.objects.filter(posting=posting).order_by('-allocation_id')
                  .values_list('allocation_id', 'amount'))
    if (evidence.original_provider_income != original_plan.provider_income_units
            or evidence.expected_release != after.release_units
            or not first_release_hash_matches(evidence, posting)
            or actual != [(part.allocation_id, part.amount) for part in plan]):
        raise ValidationError('转单退款 RELEASE 与实际资金不一致')
    return ('RELEASE', f'order:{allocation.pk}', f'points-transfer-refund-release:{row.pk}', row.amount,
        _digest(['points-transfer-refund', row.pk, row.amount, allocation.pk, allocation.receipt_id,
            posting.pk, posting.terms_hash, posting.participants_hash, participants['digest'], terms.evidence_hash,
            evidence.pk, evidence.evidence_hash, proposal.case_opened_at.isoformat(), proposal.case_request_id,
            proposal.accepted_at.isoformat(), proposal.provider_terms, proposal.explanation,
            [[a, b, c, d.isoformat()] for a, b, c, d in decisions],
            [share.pk, share.assignment_id, share.account_id, share.original_income, share.burden,
             share.pending_deduction, share.wallet_available_before, share.deposit_paid_before, share.retained_income],
            proof.pk, proof.refund_id, proof.equivalent_units, proof.coin_units, proof.points_hundredths,
            proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final,
            [[x.pk, x.lot_id, x.kind, x.amount, historical_sources[x.pk], x.lot.kind, x.lot.source_key] for x in sources],
            [coin.pk, coin.wallet_id, coin.amount, coin.balance_before, coin.balance_after,
             coin.operator_account_id, coin.operator_id] if coin else None,
            [points.pk, points.account_id, points.order_id, points.kind, points.amount,
             points.event_key, points.operator_id] if points else None]
             + ([addon_payload, evidence.prior_addon_release_snapshot] if addons else [])
             + ([paid_source] if paid_source is not None else [])
             + ([[wallet_tx.pk, wallet_tx.wallet_id, wallet_tx.amount, wallet_tx.balance_before, wallet_tx.balance_after]
                 if wallet_tx else None,
                 [deficit.pk, deficit.profile_id, deficit.amount, deficit.direct_key,
                  deficit.source_store_transaction_id] if deficit else None] if wallet_tx or deficit else [])
             + ([[[person.pk, person.assignment_id, person.account_id, person.original_income, person.burden,
                    person.pending_deduction, person.wallet_available_before, person.deposit_paid_before,
                    person.wallet_deduction, person.deposit_deduction, person.retained_income,
                    [wallet.pk, wallet.wallet_id, wallet.amount, wallet.balance_before, wallet.balance_after]
                    if wallet else None,
                    [gap.pk, gap.profile_id, gap.amount, gap.direct_key, gap.source_store_transaction_id]
                    if gap else None] for person, (wallet, gap) in zip(shares, provider_evidence_rows)]]
                if len(shares) > 1 else [])))
