"""Receipt-local proof of a second, unpaid transferred-points refund.

Call only under the receipt lock with READ COMMITTED current reads. This
does not authorize refund posting or acquire another order's locks.
"""
from dataclasses import asdict
from types import SimpleNamespace

from django.core.exceptions import ValidationError


def _paid_people(order, second, snapshot, *, receipt_id, before=None):
    """Read the already-paid, unaffected people without locking another row."""
    from django.conf import settings
    from django.utils.dateparse import parse_datetime
    from datetime import timedelta
    from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest, OrderSegmentSettlementRevision
    from .segment_settlement import _hash, _report_versions
    from .settlement import SettlementError
    if snapshot is None:
        if OrderSegmentPersonSettlement.objects.filter(manifest__order=order, settled_at__lte=second.created_at).exists():
            raise ValidationError('追加退款前已付人员凭证缺失')
        return []
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('plan'), dict)
            or snapshot.get('plan_hash') != _hash(snapshot['plan']) or not isinstance(snapshot.get('people'), list)
            or not snapshot['people']):
        raise ValidationError('追加退款前结算快照异常')
    manifest = OrderSegmentSettlementManifest.objects.filter(pk=snapshot.get('manifest_id'), order=order).first()
    if not manifest or manifest.plan_hash != _hash(manifest.plan):
        raise ValidationError('追加退款前结算清单异常')
    if manifest.plan_hash != snapshot['plan_hash']:
        query = OrderSegmentSettlementRevision.objects.filter(manifest=manifest, posting=second)
        if before is not None:
            if query.filter(created_at=before).exists():
                raise ValidationError('退款与结算版本时间无法区分')
            query = query.filter(created_at__lt=before)
        revisions = list(query)
        if not revisions and before is not None:
            pass  # The refund source predates the settlement version.
        elif (len(revisions) != 1 or revisions[0].before_plan_hash != snapshot['plan_hash']
                or revisions[0].after_plan_hash != _hash(revisions[0].after_plan)):
            raise ValidationError('追加退款结算清单版本异常')
        elif (manifest.addon_revisions.filter(created_at__gt=revisions[0].created_at).exists()
              or manifest.voluntary_payment_revisions.filter(created_at__gt=revisions[0].created_at).exists()):
            from .points_voluntary_paid_evidence import verify_revisions_current_read
            try:
                verify_revisions_current_read(manifest, start_plan=revisions[0].after_plan,
                    start_hash=revisions[0].after_plan_hash, receipt_id=receipt_id,
                    after=revisions[0].created_at, before=before)
            except ValidationError as exc:
                raise ValidationError('追加退款后的加购结算版本异常') from exc
        elif before is None and (revisions[0].after_plan != manifest.plan
                                 or revisions[0].after_plan_hash != manifest.plan_hash):
            raise ValidationError('追加退款结算清单版本异常')
    people = {item.get('account_id'): item for item in snapshot['plan'].get('people', [])}
    ids = [item.get('id') for item in snapshot['people']]
    rows = list(OrderSegmentPersonSettlement.objects.filter(manifest=manifest, settled_at__lte=second.created_at)
                .select_related('income_transaction__wallet', 'deposit_debit__wallet', 'deposit_credit__wallet')
                .order_by('pk'))
    if [item.pk for item in rows] != ids or len(set(ids)) != len(ids):
        raise ValidationError('追加退款前已付人员记录异常')
    for row, expected in zip(rows, snapshot['people']):
        actual = {'id': row.pk, 'account_id': row.account_id, 'amount': row.amount, 'segment_ids': row.segment_ids,
            'report_versions': row.report_versions, 'income_transaction_id': row.income_transaction_id,
            'deposit_debit_id': row.deposit_debit_id, 'deposit_credit_id': row.deposit_credit_id,
            'settled_at': row.settled_at.isoformat() if row.settled_at else None}
        person = people.get(row.account_id)
        first = second.proposal.points_terms.prior_refund.negotiation_posting
        selected = set(first.proposal.affected_segment_ids or [])
        start = order.completed_at
        if (first.termination_in_service and row.account_id in first.proposal.affected_accounts
                and (not selected or set(row.segment_ids).issubset(selected))):
            start = first.accepted_at
        if (actual != expected or not person or row.account_id in second.proposal.affected_accounts
                or row.amount != person.get('income') or row.segment_ids != person.get('segment_ids')
                or not row.settled_at or not start or order.service_aftersale_hours is None
                or row.settled_at < start + timedelta(hours=order.service_aftersale_hours)
                or row.settled_at > second.created_at or not row.report_versions):
            raise ValidationError('追加退款前本人已付收益异常')
        try:
            if _report_versions(SimpleNamespace(segment_ids=tuple(row.segment_ids))) != row.report_versions:
                raise ValidationError('追加退款前已付报单凭证异常')
        except SettlementError as exc:
            raise ValidationError('追加退款前已付报单凭证异常') from exc
        for item in row.report_versions:
            submitted = parse_datetime(item.get('submitted_at') or '')
            reviewed = parse_datetime(item.get('reviewed_at') or '')
            if (not submitted or not reviewed or reviewed < submitted or reviewed > row.settled_at
                    or submitted + timedelta(hours=order.service_freeze_hours) > row.settled_at):
                raise ValidationError('追加退款前已付报单时钟异常')
        income = row.income_transaction
        if row.amount:
            from .models import OrderProvider
            if (not income or income.order_id != order.pk or income.wallet.account_id != row.account_id
                    or not OrderProvider.objects.filter(order=order, provider_account_id=row.account_id,
                                                        provider_id=income.wallet.user_id).exists()
                    or income.tx_type != 'INCOME' or income.status != 'SUCCESS' or income.amount != row.amount
                    or income.balance_after != income.balance_before + income.amount
                    or not income.created_at or income.created_at > row.settled_at
                    or income.reversal_of_id or income.reversals.exists()):
                raise ValidationError('追加退款前实际收益流水异常')
        elif income:
            raise ValidationError('零收益已付人员存在流水')
        debit, credit = row.deposit_debit, row.deposit_credit
        if bool(debit) != bool(credit):
            raise ValidationError('追加退款前押金流水缺少配对')
        if debit and (debit.order_id != order.pk or debit.wallet.account_id != row.account_id
                or debit.tx_type != 'DEPOSIT' or debit.status != 'SUCCESS' or not -row.amount <= debit.amount < 0
                or debit.balance_after != debit.balance_before + debit.amount or debit.reversal_of_id or debit.reversals.exists()
                or credit.order_id != order.pk or credit.tx_type != 'DEPOSIT_INCOME' or credit.status != 'SUCCESS'
                or credit.amount != -debit.amount or credit.balance_after != credit.balance_before + credit.amount
                or not credit.wallet.user_id or credit.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or credit.reversal_of_id or credit.reversals.exists()):
            raise ValidationError('追加退款前押金配对流水异常')
    from .points_transfer_paid_addons import verify as verify_paid_addons
    from rest_framework.exceptions import ValidationError as RestValidationError
    try:
        verify_paid_addons(order, snapshot, cutoff=second.created_at)
    except RestValidationError as exc:
        raise ValidationError('追加退款前已付加购收益异常') from exc
    return rows


def facts(row):
    from users.receipt_usage_sources import _require_current_unlocked_reads, _digest
    from .models import OrderPointsFundingAllocation, OrderPointsFundingRelease
    from .points_transfer_refund_receipt_usage import facts as first_facts
    from .points_transfer_followup_revision import _digest as revision_digest, _json
    from .points_negotiation_execution import participant_digest
    from .points_negotiation_preview import asset_digest, funding_after_refund
    from .points_funding_revision import plan_unpaid_funding_revision, plan_funding_revision
    from .points_transfer_receipt_usage import historical_plan, facts as transfer_facts
    from .refund_math import payment_refund, wallet_source_refund, provider_refund_burden
    from .negotiation_proposals import _digest as proposal_digest, _snapshot_terms
    from .refunds import request_fingerprint
    from copy import copy
    from .points_transfer_refund_evidence import current_read_matches_history
    _require_current_unlocked_reads()
    second, allocation = row.posting, row.allocation
    read_receipt_id = getattr(row, 'read_receipt_id', allocation.receipt_id)
    revision = getattr(second, 'transfer_followup_funding_revision', None)
    if not revision:
        from .models import OrderPointsTransferFollowupFundingRevision
        revision = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=second).first()
    if not revision:
        raise ValidationError('转单追加退款资金版本缺失')
    first, proof, proposal = revision.first_posting, second.points_refund, second.proposal
    prior, order = first.points_refund, proposal.case.order
    evidence = getattr(first, 'funding_release_evidence', None)
    if (not evidence
            or not isinstance(evidence.allocations, list) or not isinstance(first.participants, (dict, list))
            or not isinstance(second.participants, dict) or proposal.points_terms.prior_refund_id != prior.pk
            or proof.redemption_id != prior.redemption_id
            or allocation.funding_order_id != order.pk
            or proof.refund.order_id != order.pk or proof.refund.amount != proof.coin_units
            or proof.equivalent_units != proposal.refund_units or proof.refund.operator_account_id != second.operator_id
            or proof.refund_id <= prior.refund_id or order.refund_records.filter(pk__lte=proof.refund_id).count() != 2
            or second.terms_hash != proposal.terms_hash or second.participants_hash != participant_digest(proposal, second.participants)
            or revision.evidence_hash != revision_digest(second, first.pk, revision.prior_manifest,
                revision.allocations, revision.releases, revision.funding, revision.revision,
                revision.prior_required_units, revision.pending_handover_snapshot)):
        raise ValidationError('转单追加退款来源或资金版本异常')
    from rest_framework.exceptions import ValidationError as RestValidationError
    from .points_transfer_followup_pending import verify_snapshot
    try:
        verify_snapshot(second, revision.pending_handover_snapshot, receipt_id=read_receipt_id)
    except RestValidationError as exc:
        raise ValidationError('退款时待接替责任快照异常') from exc
    paid_people = _paid_people(order, second, revision.prior_manifest, receipt_id=read_receipt_id,
                               before=getattr(row, 'history_before', None))
    original_allocation = (OrderPointsFundingAllocation.objects.filter(pk=evidence.allocations[0][0]).first()
                           if evidence.allocations else allocation)
    if not original_allocation:
        raise ValidationError('首次转单退款到账分配缺失')
    first_row = SimpleNamespace(posting=first, allocation=original_allocation, pk=0, amount=0,
                                read_receipt_id=read_receipt_id, history_before=second.created_at)
    if isinstance(first.participants, list):
        from users.receipt_usage_sources import first_negotiation_release_facts
        previous_source = first_negotiation_release_facts(first_row)
    else:
        previous_source = first_facts(first_row)
    from .models import PointsPostRefundTransferRevision, PointsPostRefundTransferRelease
    from .points_transfer_refund_evidence import digest as transfer_digest
    versions = list(PointsPostRefundTransferRevision.objects.filter(order=order,
        created_at__lt=second.created_at).order_by('pk'))
    version = versions[-1] if versions else None
    version_source = None
    earlier_version_sources = []
    if version:
        from .points_post_refund_transfer_receipt_usage import facts as post_transfer_facts
        allocated_ids = [item[0] for item in version.snapshot.get('allocations', [])]
        version_allocation = (OrderPointsFundingAllocation.objects.filter(pk__in=allocated_ids).first()
                              if allocated_ids else allocation)
        if not version_allocation:
            raise ValidationError('退款后新转单的原到账来源缺失')
        version_source = post_transfer_facts(SimpleNamespace(revision=version, allocation=version_allocation, pk=0,
            amount=0, read_receipt_id=read_receipt_id))
        for previous in versions[:-1]:
            previous_ids = [item[0] for item in previous.snapshot.get('allocations', [])]
            previous_allocation = (OrderPointsFundingAllocation.objects.filter(pk__in=previous_ids).order_by('pk').first()
                                   if previous_ids else allocation)
            if not previous_allocation:
                raise ValidationError('退款后历史转单到账来源缺失')
            earlier_version_sources.append(post_transfer_facts(SimpleNamespace(revision=previous,
                allocation=previous_allocation, pk=0, amount=0, read_receipt_id=read_receipt_id)))
        frozen = version.snapshot['facts']
    else:
        if not isinstance(first.participants, dict):
            raise ValidationError('首次退款后的实际转单资金版本缺失')
        frozen = first.participants['facts']
    if second.participants != {'facts': frozen, 'digest': transfer_digest(frozen)}:
        raise ValidationError('追加退款未绑定当时的转单履约版本')
    from .points_transfer_followup_scope import verify as verify_segment_scope
    from rest_framework.exceptions import ValidationError as RestValidationError
    try:
        verify_segment_scope(first, second, version)
    except RestValidationError as exc:
        raise ValidationError('追加退款履约段范围异常') from exc
    if not current_read_matches_history(order, read_receipt_id, frozen, second.created_at):
        raise ValidationError('追加退款历史履约已变化')
    shares = list(second.shares.select_related('assignment').order_by('account_id'))
    if not shares or [row.account_id for row in shares] != sorted(proposal.affected_accounts):
        raise ValidationError('追加退款陪玩承担范围异常')
    decisions = list(proposal.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    episode = copy(proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    expected_decisions = {(order.customer_account_id, 'BOSS', True)} | {
        (share.account_id, 'PROVIDER', True) for share in shares}
    if (len(decisions) != len(expected_decisions)
            or set((a, b, c) for a, b, c, _ in decisions) != expected_decisions or any(not d for _, _, _, d in decisions)
            or proposal.status != 'ACCEPTED' or proposal.accepted_at != max(d for _, _, _, d in decisions)
            or second.accepted_at != proposal.accepted_at
            or _snapshot_terms(proposal.provider_terms, proposal.affected_accounts) != proposal.provider_terms
            or proposal.terms_hash != proposal_digest(episode, proposal.version, proposal.affected_accounts,
                proposal.refund_units, proposal.provider_terms, proposal.explanation, proposal.affected_segment_ids)
            or proof.refund.reason != proposal.explanation or not proof.refund.client_request_id
            or proof.refund.request_fingerprint != request_fingerprint(proof.coin_units, proposal.explanation,
                {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units})
            or proof.refund.recovered_amount != sum(row.wallet_deduction + row.deposit_deduction for row in shares)):
        raise ValidationError('转单追加退款确认或个人承担异常')
    for share in shares:
        if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                or share.pending_deduction != min(share.burden, share.original_income)
                or share.wallet_deduction != min(share.burden - share.pending_deduction, share.wallet_available_before)
                or share.deposit_deduction != share.burden - share.pending_deduction - share.wallet_deduction
                or share.deposit_deduction > share.deposit_paid_before):
            raise ValidationError('追加退款个人承担异常')
        wallet_tx, deficit = share.wallet_transaction, share.deposit_deficit
        if share.wallet_deduction:
            if (not wallet_tx or wallet_tx.order_id != order.pk or wallet_tx.wallet.account_id != share.account_id
                    or wallet_tx.wallet.user_id != share.assignment.provider_id or wallet_tx.tx_type != 'REFUND'
                    or wallet_tx.status != 'SUCCESS' or wallet_tx.amount != -share.wallet_deduction
                    or wallet_tx.balance_before != share.wallet_available_before
                    or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount
                    or wallet_tx.operator_account_id != second.operator_id or wallet_tx.operator_id != proof.refund.operator_id
                    or wallet_tx.reversal_of_id or wallet_tx.reversals.exists()):
                raise ValidationError('追加退款钱包承担凭证异常')
        elif wallet_tx:
            raise ValidationError('追加退款存在多余钱包扣款')
        if share.deposit_deduction:
            from django.conf import settings
            store_tx = deficit.source_store_transaction if deficit else None
            if (not deficit or deficit.refund_id != proof.refund_id or deficit.addon_refund_id
                    or deficit.source_kind != 'DIRECT' or deficit.source_provider_transaction_id
                    or deficit.profile.account_id != share.account_id or deficit.profile.user_id != share.assignment.provider_id
                    or deficit.account_id != share.account_id or deficit.amount != share.deposit_deduction
                    or deficit.direct_key != f'{proof.refund_id}:{deficit.profile_id}' or not store_tx
                    or store_tx.order_id != order.pk or store_tx.tx_type != 'DEPOSIT_INCOME'
                    or store_tx.status != 'SUCCESS' or store_tx.amount != -deficit.amount
                    or store_tx.balance_after != store_tx.balance_before + store_tx.amount
                    or not store_tx.wallet.user_id or store_tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                    or store_tx.operator_account_id != second.operator_id or store_tx.operator_id != proof.refund.operator_id
                    or store_tx.reversal_of_id or store_tx.reversals.exists()):
                raise ValidationError('追加退款押金承担凭证异常')
        elif deficit:
            raise ValidationError('追加退款存在多余押金缺口')
    terms = proposal.points_terms
    assets = {'redemption_id': terms.redemption_id, 'coin_units': terms.coin_units,
        'points_hundredths': terms.points_hundredths, 'prepaid_units': terms.prepaid_units,
        'gift_units': terms.gift_units, 'compensation_units': terms.compensation_units, 'is_final': terms.is_final,
        'prior_refund_id': terms.prior_refund_id}
    if terms.segment_pending_plan is not None:
        assets['segment_pending_plan'] = terms.segment_pending_plan
    actual_segments = [{'account_id': share.account_id, 'segment_id': segment_id, 'amount': amount}
        for share in shares for segment_id, amount in share.segment_deductions.order_by('segment_id')
            .values_list('segment_id', 'amount')]
    expected_segments = terms.segment_pending_plan or []
    if (not isinstance(expected_segments, list)
            or sorted(actual_segments, key=lambda item: (item['account_id'], item['segment_id']))
                != sorted(expected_segments, key=lambda item: (item['account_id'], item['segment_id']))):
        raise ValidationError('积分退款逐段扣减与确认方案不一致')
    if (terms.evidence_hash != asset_digest(proposal, assets)
            or (terms.coin_units, terms.points_hundredths, terms.prepaid_units, terms.gift_units, terms.compensation_units, terms.is_final)
                != (proof.coin_units, proof.points_hundredths, proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final)):
        raise ValidationError('追加退款资产构成异常')
    payment, spent = proof.redemption.payment, proof.redemption.ledger
    coin, points = proof.coin_transaction, proof.points_ledger
    if (proof.coin_units and (not coin or coin.wallet_id != payment.wallet_id or coin.order_id != order.pk
            or coin.tx_type != 'REFUND' or coin.status != 'SUCCESS' or coin.amount != proof.coin_units
            or coin.balance_after != coin.balance_before + coin.amount or coin.reversal_of_id or coin.reversals.exists()
            or coin.operator_account_id != second.operator_id or coin.operator_id != proof.refund.operator_id)
            or proof.points_hundredths and (not points or points.account_id != order.customer_account_id
            or points.order_id != order.pk or points.kind != 'ORDER_POINTS_RETURN' or points.amount != proof.points_hundredths
            or points.event_key != f'order:{order.pk}:points-return:{proof.refund_id}' or points.operator_id != second.operator_id)
            or (not proof.coin_units and coin) or (not proof.points_hundredths and points)):
        raise ValidationError('追加退款资产流水异常')
    sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    paid = {kind: sum(item.amount for item in sources if item.kind == kind) for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    expected = payment_refund(paid_coin_units=proof.redemption.paid_units,
        paid_point_hundredths=proof.redemption.points_hundredths, point_value_units=proof.redemption.discount_units,
        refund_equivalent_units=proof.equivalent_units, previously_refunded_equivalent_units=prior.equivalent_units,
        previously_refunded_coin_units=prior.coin_units, previously_refunded_point_hundredths=prior.points_hundredths)
    returned = wallet_source_refund(coin_units=expected.coin_units, paid_prepaid_units=paid['PREPAID'],
        paid_gift_units=paid['GIFT'], paid_compensation_units=paid['COMPENSATION'],
        refunded_prepaid_units=prior.prepaid_units, refunded_gift_units=prior.gift_units,
        refunded_compensation_units=prior.compensation_units)
    if ((proof.coin_units, proof.points_hundredths, proof.is_final, proof.prepaid_units, proof.gift_units,
            proof.compensation_units) != (expected.coin_units, expected.point_hundredths, expected.is_final,
                returned.prepaid_units, returned.gift_units, returned.compensation_units)):
        raise ValidationError('追加退款与原付款构成不一致')
    history, lines = historical_plan(frozen, proof.redemption, paid, frozen['transfers'][-1]['id'])
    if revision.prior_manifest is not None:
        from .points_transfer_followup_revision import _verify_manifest_terms
        try:
            _verify_manifest_terms(revision.prior_manifest, first, SimpleNamespace(segment_shares=tuple(lines)),
                                   frozen, cutoff=second.created_at)
        except Exception as exc:
            from rest_framework.exceptions import ValidationError as RestValidationError
            if isinstance(exc, RestValidationError):
                raise ValidationError('追加退款前的分段收益方案异常') from exc
            raise
    first_shares = list(first.shares.all())
    selected = set(proposal.affected_segment_ids or [])
    by_id = {item['id']: item for item in frozen['segments']}
    weights = {share.account_id: sum(item['original_share'] for item in frozen['segments']
        if item['account_id'] == share.account_id and item['started_at']
        and (not selected or item['id'] in selected)) for share in shares}
    amount = provider_refund_burden(refund_equivalent_units=proof.equivalent_units,
                                    original_price_weights=weights)
    from .addon_main_segment_deduction import verified_deductions
    prior_addon_deductions = verified_deductions(order, before=second.created_at)
    if any(prior_addon_deductions.get(line[0], 0) > line[4] for line in lines):
        raise ValidationError('追加退款前加购已占履约段收益异常')
    for share in shares:
        income = sum(line[4] - prior_addon_deductions.get(line[0], 0)
                     for line in lines if line[1] == share.account_id and by_id[line[0]]['started_at']
                     and (not selected or line[0] in selected))
        if (share.original_income != income or share.burden != amount.amounts[share.account_id]
                or share.retained_income != income - share.pending_deduction):
            raise ValidationError('追加退款本人分段收益或承担异常')
    if second.rounding_to_store_units != amount.rounding_to_store_units:
        raise ValidationError('追加退款取整差额异常')
    from django.db.models import Q
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption=proof.redemption) | Q(service_order=order),
        created_at__lte=proof.refund.created_at).order_by('pk'))
    from .points_transfer_followup_scope import addon_snapshot, addon_chain_receipt_before
    from .points_transfer_refund_access import has_paid_addon_before
    had_addons = has_paid_addon_before(order, second.created_at)
    addon_history = addon_snapshot(order, first) if had_addons else None
    addon_chain_sources = []
    if had_addons and addon_history is None:
        addon_history, addon_chain_sources = addon_chain_receipt_before(order, first, second.created_at,
            read_receipt_id, pending_snapshot=revision.pending_handover_snapshot)
    actual = []
    addon_sources = []
    for item in allocations:
        transfer_rows = list(item.transfer_releases.filter(revision__created_at__lt=second.created_at)
                             .select_related('revision__order', 'allocation__redemption'))
        for part in transfer_rows:
            transfer_facts(SimpleNamespace(revision=part.revision, allocation=part.allocation,
                pk=part.pk, amount=part.amount, read_receipt_id=read_receipt_id))
        used = sum(part.amount for part in transfer_rows) + sum(OrderPointsFundingRelease.objects.filter(
            allocation=item, posting=first).values_list('amount', flat=True))
        post_rows = list(PointsPostRefundTransferRelease.objects.filter(allocation=item,
            revision__created_at__lt=second.created_at).select_related('revision__order', 'revision__transfer',
                                                                     'allocation__redemption').order_by('pk'))
        from .points_post_refund_transfer_receipt_usage import facts as post_transfer_facts
        for part in post_rows:
            post_transfer_facts(SimpleNamespace(revision=part.revision, allocation=part.allocation,
                pk=part.pk, amount=part.amount, read_receipt_id=read_receipt_id))
            used += part.amount
        from .models import OrderAddonFundingRelease
        from .addon_funding_release import verify_run as verify_addon_release
        parts = list(OrderAddonFundingRelease.objects.filter(allocation=item,
            run__created_at__lt=second.created_at).select_related('run').order_by('pk'))
        for part in parts:
            snapshot = addon_history
            prestart = bool(part.run.prestart_handoff_id and part.run.prestart_handoff.order_id == order.pk
                            and part.run.created_at < first.created_at)
            if not prestart and (not snapshot or not any(part.run.payment_id == entry['payment_id']
                    or (part.run.refund_id in entry['refund_ids'] and part.run.refund.payment_id == entry['payment_id'])
                    or (part.run.handover_id and part.run.handover.payment_id == entry['payment_id'])
                    for entry in snapshot)
                    or (part.run.created_at >= first.created_at and not part.run.refunded_main_snapshot)):
                raise ValidationError('追加退款前加购释放范围异常')
            try:
                if prestart or part.run.refunded_main_snapshot:
                    from users.receipt_usage_sources import _addon_release_facts
                    _addon_release_facts(part, witness_receipt_id=read_receipt_id)
                else:
                    verify_addon_release(part.run, _skip_usage=True)
            except Exception as exc:
                from rest_framework.exceptions import ValidationError as RestValidationError
                if isinstance(exc, (RestValidationError, ValidationError)):
                    raise ValidationError('追加退款前加购到账释放凭证异常') from exc
                raise
            used += part.amount
            addon_sources.append([part.pk, item.pk, part.amount, part.run_id,
                part.run.points_payment_hash, part.run.points_addon_hash, part.run.points_handover_hash])
        actual.append([item.pk, item.receipt_id, item.amount, used])
    historical_empty = (row.pk == 0 and row.amount == 0 and not actual
                        and allocation.created_at > revision.created_at)
    if actual != revision.allocations or (not any(item[0] == allocation.pk for item in actual)
                                          and not historical_empty):
        raise ValidationError('追加退款历史到账分配异常')
    common = dict(principal_units=history.principal_units, gift_units=history.gift_units,
        compensation_units=history.compensation_units, provider_income_units=history.provider_income_units,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=sum(item[2] - item[3] for item in actual))
    previous = funding_after_refund(**common, returned_prepaid_units=prior.prepaid_units,
        returned_gift_units=prior.gift_units, returned_compensation_units=prior.compensation_units,
        pending_deduction_units=sum(item.pending_deduction for item in first_shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in first_shares),
        rounding_reserved_units=first.rounding_to_store_units)
    after = funding_after_refund(**common, returned_prepaid_units=prior.prepaid_units + proof.prepaid_units,
        returned_gift_units=prior.gift_units + proof.gift_units,
        returned_compensation_units=prior.compensation_units + proof.compensation_units,
        pending_deduction_units=sum(item.pending_deduction for item in first_shares + shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in first_shares + shares),
        rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
    if had_addons:
        from .points_transfer_followup_scope import combine_addon
        if not addon_history:
            raise ValidationError('追加退款加购来源范围尚未接通')
        prior_deductions = [(item.account_id, item.pending_deduction,
            set(first.proposal.affected_segment_ids or [])) for item in first_shares]
        current_deductions = prior_deductions + [(share.account_id, share.pending_deduction, selected,
            list(share.segment_deductions.values_list('segment_id', 'amount')) or None) for share in shares]
        try:
            previous, previous_addons = combine_addon(order, previous, second.created_at, lines,
                frozen['segments'], prior_deductions)
            after, after_addons = combine_addon(order, after, second.created_at, lines,
                frozen['segments'], current_deductions)
        except Exception as exc:
            from rest_framework.exceptions import ValidationError as RestValidationError
            if isinstance(exc, RestValidationError):
                raise ValidationError('追加退款加购来源待核对') from exc
            raise
        if previous_addons != addon_history or after_addons != previous_addons:
            raise ValidationError('追加退款加购历史资金不一致')
        if paid_people:
            from .points_transfer_paid_addons import unchanged, parts_before
            from rest_framework.exceptions import ValidationError as RestValidationError
            try:
                unchanged(revision.prior_manifest, parts_before(order, addon_history, second.created_at))
            except RestValidationError as exc:
                raise ValidationError('追加退款改变了已付加购收益') from exc
    if revision.prior_manifest is not None:
        previous_calculation = asdict(previous)
        for key in ('allocated_units', 'release_units', 'remaining_units'):
            previous_calculation.pop(key)
        previous_calculation['additional_store_funding_units'] = previous.required_units
        if revision.prior_manifest['plan'].get('points_funding', {}).get('calculation') != previous_calculation:
            raise ValidationError('追加退款前已付资金计划异常')
    from .points_transfer_paid_addons import verify as verify_paid_addons
    from rest_framework.exceptions import ValidationError as RestValidationError
    try:
        paid_addon_units = verify_paid_addons(order, revision.prior_manifest, cutoff=second.created_at)
    except RestValidationError as exc:
        raise ValidationError('追加退款前已付加购收益异常') from exc
    reservation = [(pk, amount - used) for pk, _, amount, used in actual if amount > used]
    planned = (plan_funding_revision(allocations=actual, original_reservation=reservation,
        previous_required=previous.required_units,
        paid_provider_units=sum(item.amount for item in paid_people) + paid_addon_units,
        projected=after) if paid_people else plan_unpaid_funding_revision(allocations=actual, original_reservation=None,
        previous_required=previous.required_units, projected=after))
    recorded = list(OrderPointsFundingRelease.objects.filter(posting=second).order_by('-allocation_id')
                    .values_list('allocation_id', 'allocation__receipt_id', 'amount'))
    if (revision.prior_required_units != previous.required_units or revision.funding != _json(asdict(after))
            or revision.revision != _json(asdict(planned)) or revision.releases != [list(item) for item in planned.releases]
            or recorded != [tuple(item) for item in revision.releases]):
        raise ValidationError('转单追加退款释放金额异常')
    paid_payload = []
    for person in paid_people:
        paid_payload.append([person.pk, person.account_id, person.amount, person.segment_ids, person.report_versions,
            [[tx.pk, tx.wallet_id, tx.order_id, tx.tx_type, tx.status, tx.amount, tx.balance_before, tx.balance_after]
             if tx else None for tx in (person.income_transaction, person.deposit_debit, person.deposit_credit)]])
    from .models import OrderAddonRefundMainSegmentDeduction
    segment_deductions = list(OrderAddonRefundMainSegmentDeduction.objects.filter(
        participant__proposal__payment__intent__order=order,
        participant__proposal__refund__created_at__lt=second.created_at).order_by('pk')
        .values_list('pk', 'participant_id', 'segment_id', 'amount'))
    return ('RELEASE', f'order:{allocation.pk}', f'points-transfer-followup-release:{row.pk}', row.amount,
        _digest(['points-transfer-followup', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                 revision.pk, revision.evidence_hash, previous_source, proof.pk, proof.refund_id,
                 second.participants_hash, proposal.terms_hash, terms.evidence_hash]
                + ([paid_payload] if paid_payload else []) + ([version_source] if version_source else [])
                + ([earlier_version_sources] if earlier_version_sources else [])
                + ([addon_chain_sources] if addon_chain_sources else [])
                + ([addon_sources] if addon_sources else [])
                + ([segment_deductions] if segment_deductions else [])
                + ([[wallet_tx.pk, wallet_tx.wallet_id, wallet_tx.amount, wallet_tx.balance_before, wallet_tx.balance_after]
                    if wallet_tx else None,
                    [deficit.pk, deficit.profile_id, deficit.amount, deficit.direct_key,
                     deficit.source_store_transaction_id] if deficit else None] if wallet_tx or deficit else [])))
