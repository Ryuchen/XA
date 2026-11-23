"""Current-read proof for a narrow, unpaid second points refund release.

Call only while the relevant receipt is locked and READ COMMITTED has been
checked by the receipt ledger. No other order or receipt is locked here.
"""
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError

from .models import OrderPointsFundingAllocation, OrderPointsFundingRelease, PointsNegotiationPosting


def facts(row):
    from users.receipt_usage_sources import _digest, first_negotiation_release_facts
    from .points_followup_revision import FIELDS, _facts, revision_hash_matches
    from .points_funding_revision import plan_unpaid_funding_revision, plan_funding_revision
    from .points_negotiation_execution import participant_digest
    from .points_negotiation_preview import asset_digest, funding_after_refund
    from .negotiation_proposals import _digest as proposal_digest, _snapshot_terms
    from .refunds import request_fingerprint
    from copy import copy
    from .refund_math import payment_refund, wallet_source_refund, provider_refund_burden
    revision, second = row.posting.followup_funding_revision, row.posting
    proposal, proof, allocation = second.proposal, second.points_refund, row.allocation
    terms = proposal.points_terms
    from .models import OrderAddonFundingReleaseRun
    team_runs = list(OrderAddonFundingReleaseRun.objects.filter(
        handover__payment__intent__order=proposal.case.order,
        points_handover_snapshot__kind='SAME_TEAM_V1', created_at__lt=second.created_at)
        .select_related('handover').order_by('handover__accepted_at', 'pk'))
    team = bool(team_runs)
    invalid_owner = (allocation.funding_order_id != proposal.case.order_id if team
                     else allocation.redemption_id != proof.redemption_id)
    if (not terms.prior_refund_id or revision.posting_id != second.pk
            or (revision.paid_person_id and not revision.prior_settlement_id)
            or not hasattr(revision, 'release_evidence') or invalid_owner):
        raise ValidationError('后续退款到账用途尚未接入该结算或加购组合')
    first = PointsNegotiationPosting.objects.filter(points_refund_id=terms.prior_refund_id).first()
    if not first or first.points_refund.redemption_id != proof.redemption_id:
        raise ValidationError('后续退款原协商凭证缺失')
    prior, order = first.points_refund, proposal.case.order
    from .models import OrderAddonPayment
    has_addons = OrderAddonPayment.objects.filter(
        intent__order=order, buyer_transaction__created_at__lt=second.created_at).exists()
    if has_addons != (revision.addon_snapshot is not None):
        raise ValidationError('后续退款的加购资金版本缺失或多余')
    evidence = getattr(first, 'funding_release_evidence', None)
    if (not evidence or not isinstance(evidence.allocations, list)
            or any(not isinstance(item, list) or len(item) != 3 for item in evidence.allocations)):
        raise ValidationError('原退款补资快照缺失')
    # The first release can be zero. Reuse its local proof against a frozen
    # original allocation without inventing a release event.
    initial = (OrderPointsFundingAllocation.objects.filter(pk=evidence.allocations[0][0]).first()
               if evidence.allocations else allocation)
    if not initial:
        raise ValidationError('原退款补资分配不存在')
    first_terms = first.proposal.points_terms
    first_assets = {'redemption_id': first_terms.redemption_id, 'coin_units': first_terms.coin_units,
        'points_hundredths': first_terms.points_hundredths, 'prepaid_units': first_terms.prepaid_units,
        'gift_units': first_terms.gift_units, 'compensation_units': first_terms.compensation_units,
        'is_final': first_terms.is_final}
    if (first_terms.prior_refund_id or first_terms.evidence_hash != asset_digest(first.proposal, first_assets)
            or (first_terms.redemption_id, first_terms.coin_units, first_terms.points_hundredths,
                first_terms.prepaid_units, first_terms.gift_units, first_terms.compensation_units, first_terms.is_final)
            != (prior.redemption_id, prior.coin_units, prior.points_hundredths, prior.prepaid_units,
                prior.gift_units, prior.compensation_units, prior.is_final)):
        raise ValidationError('首次退款构成凭证不一致')
    first_fact = first_negotiation_release_facts(SimpleNamespace(
        posting=first, allocation=initial, pk=0, amount=0, read_receipt_id=allocation.receipt_id))
    # New purchases are distinct post-main events even when they release zero.
    from .models import OrderAddonPayment
    from users.receipt_usage_sources import after_main_addon_release_facts
    postmain_payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__gt=first.created_at,
        buyer_transaction__created_at__lt=second.created_at).select_related('buyer_transaction').order_by(
            'buyer_transaction__created_at', 'pk'))
    payment_event_facts = []
    if len({item.buyer_transaction.created_at for item in postmain_payments}) != len(postmain_payments):
        raise ValidationError('主单退款后的加购付款时间无法区分')
    for addon_payment in postmain_payments:
        payment_run = OrderAddonFundingReleaseRun.objects.filter(payment=addon_payment).first()
        if (not payment_run or not payment_run.points_payment_snapshot or not payment_run.refunded_main_snapshot
                or payment_run.created_at < addon_payment.buyer_transaction.created_at
                or payment_run.created_at >= second.created_at
                or (not any(part[0] == initial.pk for part in payment_run.allocation_snapshot)
                    and initial.created_at <= payment_run.created_at)):
            raise ValidationError('后续主单退款缺少加购付款真实资金版本')
        after_main_addon_release_facts(SimpleNamespace(run=payment_run, allocation=initial, pk=0, amount=0),
                                       witness_receipt_id=allocation.receipt_id)
        payment_event_facts.append([payment_run.pk, payment_run.points_payment_hash])
    from .models import OrderAddonRefund
    team_event_facts = []
    if team:
        from .addon_funding_release import verify_run
        from .points_team_addon_handover_facts import has_ordered_funding_chain
        if (not has_ordered_funding_chain(team_runs)
                or any(not item.handover.accepted_at or item.created_at >= second.created_at
                       or (not revision.paid_person_id and (
                           item.handover.accepted_at == first.created_at
                           or (item.handover.accepted_at < first.created_at and item.created_at >= first.created_at)
                           or (item.handover.accepted_at > first.created_at and
                               not isinstance(item.refunded_main_snapshot, dict))))
                       or (revision.paid_person_id and (not revision.paid_person.settled_at
                           or item.handover.accepted_at >= revision.paid_person.settled_at
                           or item.created_at >= revision.paid_person.settled_at)) for item in team_runs)):
            raise ValidationError('后续退款的同单接替顺序待核对')
        if (OrderAddonRefund.objects.filter(order=order, created_at=second.created_at).exists()
                or OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
                    handover__accepted_at=second.created_at).exists()):
            raise ValidationError('后续退款与加购事件时间无法区分')
        refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=second.created_at)
                       .order_by('created_at', 'pk'))
        if revision.paid_person_id and postmain_payments:
            from .points_team_other_addon_refund import continuing_purchase
            if any(item.intent.provider_id == revision.paid_person.segment.provider_account_id
                    or not continuing_purchase(order, item) for item in postmain_payments):
                raise ValidationError('已付后续加购不属于未结算陪玩的实际履约')
        events = [(run.handover.accepted_at, 'handover', run) for run in team_runs]
        events += [(item.created_at, 'refund', item) for item in refunds]
        events += [(item.buyer_transaction.created_at, 'payment', item) for item in postmain_payments]
        if any(not item[0] for item in events) or len({item[0] for item in events}) != len(events):
            raise ValidationError('后续退款的加购事件时序无法区分')
        previous_at, accepted_payments, refund_run_facts = None, set(), []
        for event_at, kind, item in sorted(events, key=lambda event: event[0]):
            if (previous_at and event_at <= previous_at) or event_at == first.created_at:
                raise ValidationError('后续退款的加购资金版本时序异常')
            if kind == 'handover':
                run = item
                accepted_payments.add(item.handover.payment_id)
                if run.refunded_main_snapshot is not None:
                    if (not any(part[0] == initial.pk for part in run.allocation_snapshot)
                            and initial.created_at <= run.created_at):
                        raise ValidationError('主单退款后的接替缺少真实到账见证')
                    after_main_addon_release_facts(SimpleNamespace(run=run, allocation=initial, pk=0, amount=0),
                                                   witness_receipt_id=allocation.receipt_id)
                else:
                    verify_run(run, _skip_usage=True, witness_receipt_id=allocation.receipt_id)
            elif kind == 'payment':
                run = OrderAddonFundingReleaseRun.objects.filter(payment=item).first()
                if (not run or not run.points_payment_snapshot or not run.refunded_main_snapshot
                        or (not any(part[0] == initial.pk for part in run.allocation_snapshot)
                            and initial.created_at <= run.created_at)):
                    raise ValidationError('主单退款后的付款资金版本缺失')
                from users.receipt_usage_sources import after_main_addon_release_facts
                after_main_addon_release_facts(SimpleNamespace(run=run, allocation=initial, pk=0, amount=0),
                                               witness_receipt_id=allocation.receipt_id)
            else:
                runs = list(OrderAddonFundingReleaseRun.objects.filter(refund=item))
                from .points_team_other_addon_refund import applies as team_other_applies
                if ((item.payment_id not in accepted_payments and not team_other_applies(item)) or len(runs) != 1
                        or not runs[0].points_addon_snapshot or runs[0].created_at < event_at
                        or runs[0].created_at >= second.created_at):
                    raise ValidationError('后续退款的加购资金版本缺失')
                run = runs[0]
                if run.refunded_main_snapshot is not None:
                    from users.receipt_usage_sources import after_main_addon_release_facts
                    if (not any(part[0] == initial.pk for part in run.allocation_snapshot)
                            and initial.created_at <= run.created_at):
                        raise ValidationError('后续退款缺少加购版本的真实到账见证')
                    after_main_addon_release_facts(SimpleNamespace(run=run, allocation=initial, pk=0, amount=0),
                                                   witness_receipt_id=allocation.receipt_id)
                else:
                    verify_run(run, _skip_usage=True, witness_receipt_id=allocation.receipt_id)
                refund_run_facts.append([run.pk, run.points_addon_hash])
            if run.created_at < event_at:
                raise ValidationError('后续退款的加购资金版本早于实际事件')
            previous_at = run.created_at
        team_event_facts.extend([[run.pk, run.points_handover_hash] for run in team_runs])
        team_event_facts.extend(refund_run_facts)
    else:
        refunds = OrderAddonRefund.objects.filter(order=order)
        if refunds.filter(created_at__in=(first.created_at, second.created_at)).exists():
            raise ValidationError('后续退款与加购事件时间无法区分')
        earlier_refunds = refunds.filter(created_at__gt=first.created_at, created_at__lt=second.created_at)
        for refund in earlier_refunds.select_related('funding_release_run').order_by('created_at', 'pk'):
            run = getattr(refund, 'funding_release_run', None)
            if (not run or not isinstance(run.refunded_main_snapshot, dict)
                    or run.refunded_main_snapshot.get('posting_id') != first.pk
                    or not refund.created_at <= run.created_at < second.created_at):
                raise ValidationError('后续退款的加购资金版本缺失')
            # Nonzero releases are verified with their allocation rows below.
            if not run.released_amount or not run.releases.exists():
                after_main_addon_release_facts(SimpleNamespace(run=run, allocation=initial, pk=0, amount=0),
                                               witness_receipt_id=allocation.receipt_id)
    if (proposal.status != 'ACCEPTED' or second.accepted_at != proposal.accepted_at
            or second.terms_hash != proposal.terms_hash or not isinstance(second.participants, list)
            or second.participants_hash != participant_digest(proposal, second.participants)
            or proof.refund.order_id != order.pk or proof.refund.amount != proof.coin_units
            or proof.equivalent_units != proposal.refund_units or proof.refund_id <= prior.refund_id
            or proof.redemption.refunds.filter(refund_id__lte=proof.refund_id).count() != 2
            or order.refund_records.filter(pk__lte=proof.refund_id).count() != 2):
        raise ValidationError('后续退款及已确认方案不一致')
    first_shares, second_shares = list(first.shares.order_by('pk')), list(second.shares.order_by('pk'))
    if (len(first_shares) != 1 or len(second_shares) != 1
            or first_shares[0].account_id == second_shares[0].account_id):
        raise ValidationError('后续退款收益承担尚未接入该组合')
    paid_person = revision.paid_person if revision.paid_person_id else None
    paid_addon_facts = []
    if paid_person and team:
        from .points_team_other_addon_refund import applies as other_team_refund_applies
        team_ids = {run.handover.payment_id for run in team_runs}
        for refund in refunds:
            if refund.created_at <= first.created_at:
                continue
            accounts = list(refund.proposal.participants.values_list('account_id', flat=True))
            if (len(accounts) != 1 or (refund.created_at >= paid_person.settled_at
                                       and accounts[0] == first_shares[0].account_id)
                    or (refund.payment_id not in team_ids and
                        (refund.payment.intent.provider_id != accounts[0] or not other_team_refund_applies(refund)))):
                raise ValidationError('已结算陪玩后的加购退款责任范围异常')
    if (not paid_person and revision.prior_settlement_id
            and (revision.prior_settlement.posting_id != first.pk or revision.prior_settlement.finalized_at
                 or revision.prior_settlement.persons.exists())):
        raise ValidationError('未支付陪玩的原资金预留凭证异常')
    if paid_person:
        settlement, income = revision.prior_settlement, paid_person.income_transaction
        report = paid_person.report
        from .addon_main_deduction import deductions_at
        first_participants = first.participants if isinstance(first.participants, list) else []
        paid_snapshots = [item for item in first_participants if isinstance(item, dict)
                          and item.get('account_id') == first_shares[0].account_id]
        if len(paid_snapshots) != 1:
            raise ValidationError('先结算陪玩的首次退款履约凭证缺失')
        prior_deduction = paid_snapshots[0].get('prior_addon_deduction', 0)
        actual_deduction = deductions_at(order, before=second.created_at).get(first_shares[0].account_id, 0)
        if type(prior_deduction) is not int or actual_deduction < prior_deduction:
            raise ValidationError('先结算陪玩的历史收益扣减异常')
        expected_income = first_shares[0].retained_income - (actual_deduction - prior_deduction)
        if (paid_person.settlement_id != settlement.pk
                or settlement.posting_id != first.pk or settlement.persons.count() != 1
                or paid_person.segment.provider_account_id != first_shares[0].account_id
                or expected_income < 0 or paid_person.amount != expected_income
                or not paid_person.settled_at or paid_person.settled_at >= second.created_at
                or not report or report.order_id != order.pk
                or report.provider_account_id != first_shares[0].account_id or report.status != 'APPROVED'
                or not report.submitted_at or not report.audited_at
                or report.audited_at < report.submitted_at or report.audited_at > paid_person.settled_at
                or (paid_person.amount and (not income or income.order_id != order.pk
                    or income.wallet.account_id != first_shares[0].account_id or income.tx_type != 'INCOME'
                    or income.status != 'SUCCESS' or income.amount != paid_person.amount
                    or income.balance_after != income.balance_before + income.amount
                    or income.reversal_of_id or income.reversals.exists()))
                or (not paid_person.amount and income)):
            raise ValidationError('先结算陪玩实际收益凭证异常')
        if revision.addon_snapshot is not None:
            history = revision.addon_snapshot.get('addon_history') if isinstance(revision.addon_snapshot, dict) else None
            if (not isinstance(history, list) or not history or any(not isinstance(item, dict) for item in history)
                    or len({item.get('payment_id') for item in history}) != len(history)):
                raise ValidationError('先结算陪玩的加购历史范围异常')
            from .models import OrderAddonPayment
            addons = {item.pk: item for item in OrderAddonPayment.objects.filter(
                pk__in=[item.get('payment_id') for item in history], intent__order=order).select_related('intent')}
            if len(addons) != len(history):
                raise ValidationError('先结算陪玩的加购付款凭证异常')
            rows = list(settlement.addon_settlements.filter(segment_id=paid_person.segment_id)
                        .select_related('income_transaction__wallet', 'report').order_by('pk'))
            expected = {}
            for item in history:
                payment = addons[item['payment_id']]
                runs = [run for run in team_runs if run.handover.payment_id == payment.pk]
                if runs:
                    handover = runs[-1].handover
                    shares = list(handover.shares.select_related('segment').order_by('position'))
                    own_refunds = [refund for refund in refunds if refund.payment_id == payment.pk]
                    if own_refunds:
                        from .points_addon_handover_refund_facts import settlement_parts
                        parts = settlement_parts(own_refunds[0], before=second.created_at,
                                                 receipt_id=allocation.receipt_id)
                        values = [(part['segment_id'], part['provider_account_id'], part['provider_income_units'])
                                  for part in parts]
                    else:
                        values = []
                        grouped = {}
                        for share in shares:
                            key = (share.segment_id, share.segment.provider_account_id)
                            grouped[key] = grouped.get(key, 0) + share.income
                        for (segment_id, account_id), amount in sorted(grouped.items()):
                            deductions = sum(payment.refund_earning_deductions.filter(
                                segment_id=segment_id, participant__account_id=account_id,
                                refund__created_at__gt=handover.accepted_at,
                                refund__created_at__lt=second.created_at).values_list('amount', flat=True))
                            values.append((segment_id, account_id, amount - deductions))
                    if (item.get('handover_id') != handover.pk
                            or not shares
                            or len({part[0] for part in values}) != len(values)
                            or any(part[2] < 0 for part in values)
                            or sum(part[2] for part in values) != item.get('provider_units')):
                        raise ValidationError('先结算陪玩的接替加购履约段凭证异常')
                    for share in shares:
                        if share.segment.attribution.order_id != order.pk:
                            raise ValidationError('接替加购履约段不属于本单')
                    for segment_id, account_id, income_units in values:
                        if segment_id == paid_person.segment_id:
                            if account_id != first_shares[0].account_id:
                                raise ValidationError('已结算加购履约段陪玩不一致')
                            expected[payment.pk] = income_units
                elif payment.handovers.exists():
                    raise ValidationError('先结算陪玩的加购接替版本缺失')
                elif payment.intent.segment_id == paid_person.segment_id:
                    expected[payment.pk] = item.get('provider_units')
            if (len(rows) != len(expected) or {item.payment_id for item in rows} != set(expected)
                    or any(item.amount != expected[item.payment_id] or item.report_id != paid_person.report_id
                           or item.settled_at != paid_person.settled_at
                           or item.settled_at >= second.created_at for item in rows)):
                raise ValidationError('先结算陪玩的加购收益归属异常')
            for item in rows:
                tx = item.income_transaction
                if ((item.amount and (not tx or tx.order_id != order.pk or tx.wallet.account_id != first_shares[0].account_id
                        or tx.tx_type != 'INCOME' or tx.status != 'SUCCESS' or tx.amount != item.amount
                        or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()))
                        or (not item.amount and tx)):
                    raise ValidationError('先结算陪玩的加购收入流水异常')
                paid_addon_facts.append([item.pk, item.payment_id, item.segment_id, item.report_id, item.amount,
                    item.settled_at.isoformat(), [tx.pk, tx.wallet_id, tx.order_id, tx.amount,
                    tx.balance_before, tx.balance_after] if tx else None])
    from .points_service_participation import service_snapshots
    try:
        snapshots = {item.pk: item for item in service_snapshots(order)}
    except RestValidationError as exc:
        raise ValidationError('双陪收益快照不完整') from exc
    if len(snapshots) != 2 or len({row.segment.provider_account_id for row in snapshots.values()}) != 2:
        raise ValidationError('双陪收益快照不完整')
    for posting, shares in ((first, first_shares), (second, second_shares)):
        p, share, refund = posting.proposal, shares[0], posting.points_refund.refund
        from .addon_main_deduction import deductions_at
        prior_addon_deductions = deductions_at(order, before=posting.created_at)
        decisions = list(p.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
        episode = copy(p.case)
        episode.created_at, episode.client_request_id = p.case_opened_at, p.case_request_id
        if (set((a, b, c) for a, b, c, _ in decisions) != {
                (order.customer_account_id, 'BOSS', True), (share.account_id, 'PROVIDER', True)}
                or any(not d for _, _, _, d in decisions) or p.accepted_at != max(d for _, _, _, d in decisions)
                or p.affected_accounts != [share.account_id] or p.status != 'ACCEPTED'
                or _snapshot_terms(p.provider_terms, p.affected_accounts) != p.provider_terms
                or p.terms_hash != proposal_digest(episode, p.version, p.affected_accounts,
                                                   p.refund_units, p.provider_terms, p.explanation, p.affected_segment_ids)
                or posting.terms_hash != p.terms_hash or posting.accepted_at != p.accepted_at
                or refund.operator_account_id != posting.operator_id or refund.reason != p.explanation
                or not refund.client_request_id or refund.request_fingerprint != request_fingerprint(
                    refund.amount, refund.reason, {'proposal_id': p.pk, 'terms_hash': p.terms_hash,
                                                   'equivalent_units': p.refund_units})
                or len(p.provider_terms) != 1 or share.assignment.order_id != order.pk
                or share.assignment.provider_account_id != share.account_id
                or (share.original_income, share.burden, share.retained_income) !=
                   (p.provider_terms[0]['original_income_units'], p.provider_terms[0]['burden_units'],
                    p.provider_terms[0]['retained_units'])
                or share.pending_deduction != min(share.burden, share.original_income)
                or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                or refund.recovered_amount != share.wallet_deduction + share.deposit_deduction
                or posting.rounding_to_store_units != share.burden - p.refund_units):
            raise ValidationError('退款双方确认凭证不一致')
        wallet_tx = share.wallet_transaction
        deficit = share.deposit_deficit
        deposit_tx = deficit.source_store_transaction if deficit else None
        if ((share.wallet_deduction and (not wallet_tx or wallet_tx.order_id != order.pk
                or wallet_tx.wallet.account_id != share.account_id or wallet_tx.tx_type != 'REFUND'
                or wallet_tx.status != 'SUCCESS' or wallet_tx.amount != -share.wallet_deduction
                or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount
                or wallet_tx.reversal_of_id or wallet_tx.reversals.exists()))
                or (not share.wallet_deduction and wallet_tx)
                or (share.deposit_deduction and (not deficit or deficit.refund_id != refund.pk
                    or deficit.account_id != share.account_id or deficit.amount != share.deposit_deduction
                    or not deposit_tx or deposit_tx.order_id != order.pk or deposit_tx.tx_type != 'DEPOSIT_INCOME'
                    or deposit_tx.status != 'SUCCESS' or deposit_tx.amount != -share.deposit_deduction
                    or deposit_tx.balance_after != deposit_tx.balance_before + deposit_tx.amount
                    or deposit_tx.reversal_of_id or deposit_tx.reversals.exists()))
                or (not share.deposit_deduction and deficit)):
            raise ValidationError('退款陪玩钱包或押金承担流水异常')
        participant = [item for item in posting.participants if item.get('account_id') == share.account_id]
        if len(participant) != 1:
            raise ValidationError('退款陪玩履约份额不一致')
        item = participant[0]
        snap = snapshots.get(item.get('snapshot_id'))
        segment = snap.segment if snap else None
        if (not snap or not segment or item.get('segment_id') != segment.pk
                or item.get('assignment_id') != share.assignment_id
                or item.get('provider_id') != share.assignment.provider_id
                or item.get('account_id') != segment.provider_account_id
                or item.get('slot') != segment.slot or item.get('original_share') != segment.original_share
                or item.get('started_at') != (segment.started_at.isoformat() if segment.started_at else None)
                or item.get('incoming_basis') != snap.incoming_basis
                or item.get('commission_type') != snap.commission_type
                or item.get('commission_rate') != snap.commission_rate
                or item.get('commission_fixed') != snap.commission_fixed
                or item.get('fixed_rule_total') != snap.fixed_rule_total
                or item.get('commission_source') != snap.commission_source
                or item.get('income_at_entry') != snap.income_at_entry
                or item.get('prior_addon_deduction') != prior_addon_deductions.get(share.account_id, 0)
                or item.get('game_commission_snapshot') != snap.game_commission_snapshot
                or share.original_income != snap.income_at_entry - prior_addon_deductions.get(share.account_id, 0)):
            raise ValidationError('退款陪玩收益快照不一致')
        try:
            burden = provider_refund_burden(refund_equivalent_units=p.refund_units,
                original_price_weights={share.account_id: participant[0]['original_share']})
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError('退款陪玩承担计算异常') from exc
        if burden.amounts[share.account_id] != share.burden or burden.rounding_to_store_units != posting.rounding_to_store_units:
            raise ValidationError('退款陪玩承担与履约基数不一致')
    values = {'redemption_id': terms.redemption_id, 'coin_units': terms.coin_units,
        'points_hundredths': terms.points_hundredths, 'prepaid_units': terms.prepaid_units,
        'gift_units': terms.gift_units, 'compensation_units': terms.compensation_units,
        'is_final': terms.is_final, 'prior_refund_id': terms.prior_refund_id}
    if (terms.redemption_id != proof.redemption_id or terms.evidence_hash != asset_digest(proposal, values)
            or (terms.coin_units, terms.points_hundredths, terms.prepaid_units, terms.gift_units,
                terms.compensation_units, terms.is_final) != (proof.coin_units, proof.points_hundredths,
                proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final)):
        raise ValidationError('后续退款资产构成凭证不一致')
    coin, points, original = proof.coin_transaction, proof.points_ledger, proof.redemption.payment
    if proof.coin_units and (not coin or coin.order_id != order.pk or coin.wallet_id != original.wallet_id
            or coin.tx_type != 'REFUND' or coin.status != 'SUCCESS' or coin.amount != proof.coin_units
            or coin.balance_after != coin.balance_before + coin.amount or coin.reversal_of_id or coin.reversals.exists()
            or coin.operator_account_id != second.operator_id or coin.operator_id != proof.refund.operator_id):
        raise ValidationError('后续退款店币流水不一致')
    if proof.points_hundredths and (not points or points.order_id != order.pk
            or points.account_id != order.customer_account_id or points.kind != 'ORDER_POINTS_RETURN'
            or points.amount != proof.points_hundredths or points.operator_id != second.operator_id
            or points.event_key != f'order:{order.pk}:points-return:{proof.refund_id}'):
        raise ValidationError('后续退款积分流水不一致')
    if (not proof.coin_units and coin) or (not proof.points_hundredths and points):
        raise ValidationError('后续退款存在多余资产流水')
    from wallet.models import Transaction
    from engagement.models import PointLedger
    all_proofs = list(proof.redemption.refunds.select_related('refund').order_by('refund_id'))
    if set(order.refund_records.values_list('pk', flat=True)) != {item.refund_id for item in all_proofs}:
        raise ValidationError('订单退款未全部绑定积分原支付')
    coin_ids = {p.coin_transaction_id for p in all_proofs if p.coin_transaction_id}
    point_ids = {p.points_ledger_id for p in all_proofs if p.points_ledger_id}
    if (Transaction.objects.filter(order_id=order.pk, tx_type='PAY', status='SUCCESS').exclude(pk=original.pk).exists()
            or PointLedger.objects.filter(order_id=order.pk, kind='ORDER_POINTS_REDEEM').exclude(
                pk=proof.redemption.ledger_id).exists()
            or set(Transaction.objects.filter(order_id=order.pk, wallet_id=original.wallet_id,
                    tx_type='REFUND', status='SUCCESS').values_list('pk', flat=True)) != coin_ids
            or set(PointLedger.objects.filter(order_id=order.pk, kind='ORDER_POINTS_RETURN')
                   .values_list('pk', flat=True)) != point_ids):
        raise ValidationError('后续退款存在未绑定的资产流水')
    sources = list(order.funding_allocations.order_by('pk'))
    from engagement.funding_wallet import validate_order_funding_sources
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('后续退款原资金批次异常') from exc
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    returned = {kind: sum(item.refunded_amount for item in sources if item.kind == kind) for kind in paid}
    if (sum(paid.values()) != proof.redemption.paid_units
            or returned != {'PREPAID': sum(item.prepaid_units for item in all_proofs),
                            'GIFT': sum(item.gift_units for item in all_proofs),
                            'COMPENSATION': sum(item.compensation_units for item in all_proofs)}):
        raise ValidationError('后续退款与原付款来源不一致')
    from engagement.funding_plan import RefundablePart, FundingPlanError, plan_refund
    try:
        original_parts = [RefundablePart(item.pk, item.kind, item.amount) for item in sources]
        historical_sources = plan_refund(original_parts, prior.coin_units + proof.coin_units)
        current_sources = plan_refund(original_parts, sum(item.coin_units for item in all_proofs))
    except FundingPlanError as exc:
        raise ValidationError('后续退款的逐批原付款来源异常') from exc
    if any(item.refunded_amount != current_sources[item.pk] for item in sources):
        raise ValidationError('订单累计退款批次与原支付顺序不一致')
    try:
        first_expected = payment_refund(paid_coin_units=proof.redemption.paid_units,
            paid_point_hundredths=proof.redemption.points_hundredths, point_value_units=proof.redemption.discount_units,
            refund_equivalent_units=prior.equivalent_units, previously_refunded_equivalent_units=0,
            previously_refunded_coin_units=0, previously_refunded_point_hundredths=0)
        first_source = wallet_source_refund(coin_units=first_expected.coin_units, paid_prepaid_units=paid['PREPAID'],
            paid_gift_units=paid['GIFT'], paid_compensation_units=paid['COMPENSATION'],
            refunded_prepaid_units=0, refunded_gift_units=0, refunded_compensation_units=0)
        if (prior.coin_units, prior.points_hundredths, prior.is_final, prior.prepaid_units, prior.gift_units,
                prior.compensation_units) != (first_expected.coin_units, first_expected.point_hundredths,
                first_expected.is_final, first_source.prepaid_units, first_source.gift_units,
                first_source.compensation_units):
            raise ValidationError('首次退款原来源计算不一致')
        expected = payment_refund(paid_coin_units=proof.redemption.paid_units,
            paid_point_hundredths=proof.redemption.points_hundredths, point_value_units=proof.redemption.discount_units,
            refund_equivalent_units=proof.equivalent_units, previously_refunded_equivalent_units=prior.equivalent_units,
            previously_refunded_coin_units=prior.coin_units, previously_refunded_point_hundredths=prior.points_hundredths)
        source = wallet_source_refund(coin_units=expected.coin_units, paid_prepaid_units=paid['PREPAID'],
            paid_gift_units=paid['GIFT'], paid_compensation_units=paid['COMPENSATION'],
            refunded_prepaid_units=prior.prepaid_units, refunded_gift_units=prior.gift_units,
            refunded_compensation_units=prior.compensation_units)
        if (proof.coin_units, proof.points_hundredths, proof.is_final, proof.prepaid_units, proof.gift_units,
                proof.compensation_units) != (expected.coin_units, expected.point_hundredths, expected.is_final,
                source.prepaid_units, source.gift_units, source.compensation_units):
            raise ValidationError('后续退款原来源计算不一致')
        if (not isinstance(revision.allocations, list) or any(not isinstance(item, list) or len(item) != 4
                or any(type(value) is not int or value <= 0 for value in item[:3])
                or type(item[3]) is not int or item[3] < 0 for item in revision.allocations)):
            raise ValidationError('后续退款补资分配快照异常')
        from django.db.models import Q
        owner = (Q(redemption__order=order) | Q(service_order=order)) if team else Q(redemption__order=order)
        if OrderPointsFundingAllocation.objects.filter(owner, created_at=second.created_at).exists():
            raise ValidationError('补资与后续退款时间无法区分')
        actual = list(OrderPointsFundingAllocation.objects.filter(owner, created_at__lt=second.created_at)
                      .order_by('pk').values_list('pk', 'receipt_id', 'amount', 'created_at'))
        old = {item[0]: [item[0], item[2], item[1]] for item in evidence.allocations}
        if ([list(item[:3]) for item in actual] != [item[:3] for item in revision.allocations]
                or len(old) != len(evidence.allocations)
                or any((list(item[:3]) != old[item[0]]) if item[0] in old else
                       item[3] <= first.created_at for item in actual)
                or set(old) - {item[0] for item in actual}):
            raise ValidationError('两次退款之间的补资记录与真实分配不一致')
        from .models import OrderAddonFundingRelease, OrderPointsTransferFundingRelease
        if OrderPointsTransferFundingRelease.objects.filter(
                    allocation_id__in=[i[0] for i in revision.allocations],
                    revision__created_at__lte=revision.created_at).exists():
            raise ValidationError('其他补资释放用途尚未接入')
        for item in revision.allocations:
            allocation_id, receipt_id, amount, previous = item
            existing = OrderPointsFundingAllocation.objects.get(pk=allocation_id)
            addon_releases = list(OrderAddonFundingRelease.objects.filter(allocation_id=allocation_id,
                run__created_at__lt=second.created_at).select_related('run').order_by('pk'))
            from users.receipt_usage_sources import _addon_release_facts
            for release in addon_releases:
                _addon_release_facts(release)
            if (existing.receipt_id != receipt_id or existing.amount != amount
                    or (existing.funding_order_id != order.pk if team else existing.redemption_id != proof.redemption_id)
                    or previous != sum(OrderPointsFundingRelease.objects.filter(
                        allocation_id=allocation_id, posting=first).values_list('amount', flat=True))
                        + sum(release.amount for release in addon_releases)):
                raise ValidationError('后续退款分配或首次释放已变化')
        historical_empty = (not revision.allocations and row.pk == 0 and row.amount == 0
                            and allocation.created_at > second.created_at)
        if (not any(item[0] == allocation.pk and item[1] == allocation.receipt_id for item in revision.allocations)
                and not historical_empty):
            raise ValidationError('后续退款释放未绑定原分配')
        net = sum(i[2] - i[3] for i in revision.allocations)
        income = evidence.original_provider_income
        rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
        common = dict(principal_units=paid['PREPAID'], gift_units=paid['GIFT'], compensation_units=paid['COMPENSATION'],
                      provider_income_units=income, inviter_rate=rate, allocated_units=net)
        before = funding_after_refund(**common, returned_prepaid_units=prior.prepaid_units,
            returned_gift_units=prior.gift_units, returned_compensation_units=prior.compensation_units,
            pending_deduction_units=first_shares[0].pending_deduction,
            provider_external_units=first_shares[0].wallet_deduction + first_shares[0].deposit_deduction,
            rounding_reserved_units=first.rounding_to_store_units)
        projected = funding_after_refund(**common, returned_prepaid_units=prior.prepaid_units + proof.prepaid_units,
            returned_gift_units=prior.gift_units + proof.gift_units,
            returned_compensation_units=prior.compensation_units + proof.compensation_units,
            pending_deduction_units=sum(s.pending_deduction for s in first_shares + second_shares),
            provider_external_units=sum(s.wallet_deduction + s.deposit_deduction for s in first_shares + second_shares),
            rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
        projected_addons = None
        if revision.addon_snapshot is not None:
            from .points_followup_revision import _with_addons
            before, before_addons = _with_addons(order, before, list(snapshots.values()), first_shares, second.created_at)
            projected, projected_addons = _with_addons(order, projected, list(snapshots.values()),
                                                       first_shares + second_shares, second.created_at)
            if before_addons != revision.addon_snapshot or projected_addons != revision.addon_snapshot:
                raise ValidationError('后续退款加购历史资金快照异常')
        if paid_person:
            addon_history = revision.addon_snapshot.get('addon_history', []) if revision.addon_snapshot else []
            fields = ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
                      'recovery_units', 'provider_units')
            addon_totals = [sum(item[field] for item in addon_history) for field in fields]
            paid, principal, gift, compensation, recovery, provider = addon_totals
            settlement = revision.prior_settlement
            actual_settlement = (settlement.paid_units, settlement.principal_units, settlement.gift_units,
                settlement.compensation_units, settlement.recovered_units, settlement.funding_units,
                settlement.provider_units, settlement.inviter_units, settlement.store_units,
                settlement.addon_paid_units, settlement.addon_principal_units, settlement.addon_gift_units,
                settlement.addon_compensation_units, settlement.addon_recovered_units, settlement.addon_provider_units)
            expected_settlement = (before.net_coin_units - paid, before.net_principal_units - principal,
                before.net_gift_units - gift, before.net_compensation_units - compensation,
                before.planned_provider_external_units - recovery, before.required_units,
                before.provider_income_units - provider, before.inviter_income_units, before.store_income_units,
                *addon_totals)
            if actual_settlement != expected_settlement or settlement.finalized_at:
                raise ValidationError('先结算陪玩的整单资金快照异常')
            reservation = list(revision.prior_settlement.funding_uses.order_by('allocation_id')
                               .values_list('allocation_id', 'amount'))
            plan = plan_funding_revision(allocations=revision.allocations, original_reservation=reservation,
                previous_required=before.required_units,
                paid_provider_units=paid_person.amount + sum(item[4] for item in paid_addon_facts), projected=projected)
        else:
            reservation = (list(revision.prior_settlement.funding_uses.order_by('allocation_id')
                                .values_list('allocation_id', 'amount')) if revision.prior_settlement_id else None)
            plan = plan_unpaid_funding_revision(allocations=revision.allocations, original_reservation=reservation,
                                               previous_required=before.required_units, projected=projected)
    except (ValueError, TypeError, KeyError, IndexError, OrderPointsFundingAllocation.DoesNotExist, RestValidationError) as exc:
        raise ValidationError('后续退款资金来源或计算异常') from exc
    numeric = list(_facts(projected, plan, before.required_units))
    actual_rows = list(OrderPointsFundingRelease.objects.filter(posting=second).order_by('-allocation_id')
                       .values_list('allocation_id', 'allocation__receipt_id', 'amount'))
    if ([getattr(revision, field) for field in FIELDS] != numeric
            or revision.releases != [list(item) for item in plan.releases]
            or actual_rows != list(plan.releases)
            or not revision_hash_matches(revision, second, numeric, projected_addons)):
        raise ValidationError('后续退款释放与资金版本不一致')
    payload = ['points-followup-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                first_fact, second.pk, second.terms_hash, second.participants_hash, terms.evidence_hash,
                proof.pk, proof.refund_id, proof.equivalent_units, proof.coin_units, proof.points_hundredths,
                proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final,
                proposal.case_id, proposal.case_opened_at.isoformat(), proposal.case_request_id,
                proposal.accepted_at.isoformat(), proposal.provider_terms, proposal.explanation,
                [[item.pk, item.lot_id, item.kind, item.amount, historical_sources[item.pk],
                  item.lot.kind, item.lot.source_key] for item in sources],
                [[a, b, c, d.isoformat()] for a, b, c, d in proposal.decisions.order_by('account_id')
                    .values_list('account_id', 'role', 'accepted', 'decided_at')],
                [[s.pk, s.assignment_id, s.account_id, s.original_income, s.burden, s.pending_deduction,
                  s.wallet_available_before, s.deposit_paid_before, s.wallet_deduction, s.deposit_deduction,
                  s.retained_income, s.wallet_transaction_id, s.deposit_deficit_id] for s in second_shares],
                [coin.pk, coin.wallet_id, coin.amount, coin.balance_before, coin.balance_after,
                 coin.operator_account_id, coin.operator_id] if coin else None,
                [points.pk, points.account_id, points.order_id, points.kind, points.amount,
                 points.event_key, points.operator_id] if points else None,
                revision.pk, revision.evidence_hash, revision.release_evidence.pk]
    if paid_person:
        income = paid_person.income_transaction
        payload.append([paid_person.pk, paid_person.settlement_id, paid_person.segment_id, paid_person.report_id,
                        paid_person.amount, paid_person.settled_at.isoformat(),
                        [income.pk, income.wallet_id, income.order_id, income.tx_type, income.status,
                         income.amount, income.balance_before, income.balance_after] if income else None,
                        paid_addon_facts])
    if team_event_facts:
        payload.append(['SAME_TEAM_V1', team_event_facts])
    if payment_event_facts:
        payload.append(['POSTMAIN_ADDON_PAYMENTS_V1', payment_event_facts])
    return ('RELEASE', f'order:{allocation.pk}', f'points-followup-release:{row.pk}', row.amount, _digest(payload))
