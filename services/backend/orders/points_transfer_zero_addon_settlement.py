"""Preflight the paid-person boundary for an add-on refund after zero termination."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def guard_unpaid_participants_locked(payment, participants):
    """Disallow changing a participant whose service or add-on is already paid."""
    from .models import PointsZeroFollowupTermination, OrderSegmentPersonSettlement, OrderSegmentAddonSettlement
    from .points_voluntary_handover_event import entry_matches
    from .points_transfer_followup_addon_version import refund_preflight_active
    order = payment.intent.order
    zero = PointsZeroFollowupTermination.objects.select_for_update().filter(order=order,
        terminated_at__isnull=False).first()
    if (not entry_matches(order, payment.pk) and not refund_preflight_active(order, payment) and
            (not zero or not isinstance(zero.funding_snapshot, dict)
             or zero.funding_snapshot.get('kind') != 'TRANSFER_ZERO_V1')):
        return
    if not transaction.get_connection().in_atomic_block or not isinstance(participants, list):
        raise ValidationError('加购退款已付人员范围须在订单事务内核对')
    accounts = [row.get('account_id') for row in participants if isinstance(row, dict)]
    if (len(accounts) != len(participants) or not accounts or len(accounts) != len(set(accounts))
            or any(type(account) is not int or account <= 0 for account in accounts)):
        raise ValidationError('加购退款受影响人员范围异常')
    if (OrderSegmentPersonSettlement.objects.select_for_update().filter(
            manifest__order=order, account_id__in=accounts).exists()
            or OrderSegmentAddonSettlement.objects.select_for_update().filter(
                manifest__order=order, segment__provider_account_id__in=accounts).exists()
            or order.providers.filter(provider_account_id__in=accounts, settled_at__isnull=False).exists()):
        raise ValidationError('已结算人员的加购退款承担尚未接通')


def preflight_locked(order, manifest, plan, terms, zero):
    """Reconcile actual refund releases and preserve every paid segment.

    This function does not write a settlement version and cannot authorize a
    refund. A writer must additionally link its result into the manifest history.
    """
    from .models import OrderAddonRefund, OrderAddonFundingReleaseRun
    from .addon_funding_release import verify_run
    from .points_transfer_paid_addons import unchanged
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    from .points_transfer_zero_followup import verify_funding_at_termination_locked

    if (not transaction.get_connection().in_atomic_block or order.pk != manifest.order_id
            or order.pk != zero.order_id or order.settled_at or manifest.finalized_at
            or not isinstance(manifest.plan, dict) or manifest.plan_hash != _hash(manifest.plan)
            or not isinstance(terms, dict) or not isinstance(zero.funding_snapshot, dict)):
        raise ValidationError('零退款后的加购结算上下文异常')
    verify_funding_at_termination_locked(zero)
    before = manifest.plan
    stable = ('deal_amount', 'segments', 'fixed_fee_shortfall', 'buyer_account_id',
              'inviter_account_id', 'inviter_id', 'inviter_rate', 'deposit_percent',
              'aftersale_hours', 'freeze_hours', 'completed_at')
    if any(before.get(key) != terms.get(key) for key in stable):
        raise ValidationError('加购退款改变了已结束的主单履约事实')
    old_people = {row['account_id']: row for row in before.get('people', [])}
    new_people = {row['account_id']: row for row in terms.get('people', [])}
    if (len(old_people) != len(before.get('people', [])) or len(new_people) != len(terms.get('people', []))
            or set(old_people) != set(new_people) or any(
                old_people[account].get('segment_ids') != row.get('segment_ids')
                or row.get('income', -1) < 0 or row['income'] > old_people[account]['income']
                for account, row in new_people.items())):
        raise ValidationError('加购退款改变了履约人员或异常增加收益')
    old_ids = before.get('points_funding', {}).get('addon_refund_ids', [])
    new_ids = terms.get('points_funding', {}).get('addon_refund_ids', [])
    if (not isinstance(old_ids, list) or not isinstance(new_ids, list)
            or len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids)
            or not set(old_ids).issubset(new_ids)):
        raise ValidationError('加购退款结算事件序列异常')
    added = sorted(set(new_ids) - set(old_ids))
    refunds = list(OrderAddonRefund.objects.filter(order=order, pk__in=added).order_by('created_at', 'pk'))
    if not added or len(refunds) != len(added) or any(row.created_at <= zero.terminated_at for row in refunds):
        raise ValidationError('缺少零退款终止后的真实加购退款')
    for refund in refunds:
        run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
        if not run or not isinstance(run.refunded_main_snapshot, dict) or (
                run.refunded_main_snapshot.get('zero_termination_id') != zero.pk):
            raise ValidationError('加购退款缺少关联零退款的真实资金释放')
        verify_run(run)
    from .models import OrderSegmentPersonSettlement
    all_paid = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(manifest=manifest).order_by('pk'))
    if all_paid:
        from .points_transfer_refund_evidence import _prior_settlement
        current_paid = _prior_settlement(order)
        unchanged(current_paid, terms.get('points_funding', {}).get('addons', []))
        if any(new_people.get(row.account_id) != old_people.get(row.account_id) for row in all_paid):
            raise ValidationError('加购退款改变了已付人员的主单收益')
        try:
            _validate_records(order, manifest, plan, snapshot_ids=[row.pk for row in all_paid],
                paid_addon_snapshot=current_paid if before.get('points_funding', {}).get('addons') else None,
                paid_addon_cutoff=refunds[0].created_at)
        except SettlementError as exc:
            raise ValidationError('加购退款前已付收益流水待核对') from exc
    saved = zero.funding_snapshot.get('paid_snapshot')
    if saved:
        paid = saved.get('people') if isinstance(saved, dict) else None
        if not isinstance(paid, list) or not paid:
            raise ValidationError('零退款之前的已付人员凭证异常')
        unchanged(saved, terms.get('points_funding', {}).get('addons', []))
        if any(new_people.get(row['account_id']) != old_people.get(row['account_id']) for row in paid):
            raise ValidationError('加购退款改变了已付人员的主单收益')
        try:
            _validate_records(order, manifest, plan, snapshot_ids=[row['id'] for row in paid],
                paid_addon_snapshot=saved if before.get('points_funding', {}).get('addons') else None,
                paid_addon_cutoff=zero.created_at)
        except SettlementError as exc:
            raise ValidationError('加购退款前已付收益流水待核对') from exc
    return {'refund_ids': added, 'before_hash': manifest.plan_hash, 'after_hash': _hash(terms)}


def transition_locked(order, manifest, plan, terms, zero):
    """Write a version only after all paid facts and actual releases reconcile."""
    from .models import OrderSegmentAddonSettlementRevision
    from .segment_settlement import _hash, _validate_records
    from .segment_addon_settlement_revision import verify_revisions
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('加购退款结算版本须在订单事务内写入')
    if manifest.plan == terms and manifest.plan_hash == _hash(terms):
        verify_revisions(manifest)
        return _validate_records(order, manifest, plan)
    facts = preflight_locked(order, manifest, plan, terms, zero)
    previous = list(OrderSegmentAddonSettlementRevision.objects.select_for_update().filter(
        manifest=manifest).order_by('version'))
    if previous:
        verify_revisions(manifest)
    with transaction.atomic():
        OrderSegmentAddonSettlementRevision.objects.create(manifest=manifest, version=len(previous) + 1,
            refund_ids=facts['refund_ids'], before_plan=manifest.plan, before_plan_hash=facts['before_hash'],
            after_plan=terms, after_plan_hash=facts['after_hash'])
        manifest.plan, manifest.plan_hash = terms, facts['after_hash']
        manifest.save(update_fields=['plan', 'plan_hash'])
        return _validate_records(order, manifest, plan)


def advance_after_refund_locked(order):
    """Update the independently paid manifest in the refund transaction."""
    from .models import (PointsZeroFollowupTermination, PointsTransferZeroSettlementRevision,
                         OrderSegmentSettlementManifest)
    from .segment_settlement import _points_transfer_terms, _terms
    zero = PointsZeroFollowupTermination.objects.select_for_update().filter(order=order,
        terminated_at__isnull=False).first()
    if not zero or not isinstance(zero.funding_snapshot, dict) or zero.funding_snapshot.get('kind') != 'TRANSFER_ZERO_V1':
        return
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not manifest or not PointsTransferZeroSettlementRevision.objects.filter(termination=zero,
            manifest=manifest).exists():
        raise ValidationError('零退款后加购退款缺少已付结算版本')
    plan, funding = _points_transfer_terms(order)
    if funding is None:
        raise ValidationError('加购退款后的真实补资不足')
    transition_locked(order, manifest, plan, _terms(order, plan, points_funding=funding), zero)


def verify_revisions_current_read(manifest, zero_revision, *, receipt_id):
    """Replay subsequent settlement versions while holding only the receipt lock."""
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.receipt_usage_sources import after_main_addon_release_facts, _require_current_unlocked_reads
    from users.receipt_usage_ledger import _locked_receipt
    from .models import OrderAddonFundingReleaseRun
    from .points_transfer_addon_zero_receipt import facts as zero_release_facts
    from .points_transfer_zero_settlement import verify_revision
    from .points_transfer_paid_addons import unchanged
    from .segment_settlement import _hash
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    current_hash = verify_revision(zero_revision)
    previous = zero_revision.after_plan
    saved = zero_revision.paid_snapshot
    versions = list(manifest.addon_revisions.order_by('version'))
    for index, version in enumerate(versions, start=1):
        before, after = version.before_plan, version.after_plan
        if (version.version != index or before != previous or version.before_plan_hash != current_hash
                or version.before_plan_hash != _hash(before) or version.after_plan_hash != _hash(after)):
            raise DjangoValidationError('零退款后的加购结算版本链异常')
        old_ids = before.get('points_funding', {}).get('addon_refund_ids', [])
        new_ids = after.get('points_funding', {}).get('addon_refund_ids', [])
        if (not isinstance(old_ids, list) or not isinstance(new_ids, list) or len(set(new_ids)) != len(new_ids)
                or not set(old_ids).issubset(new_ids) or sorted(set(new_ids) - set(old_ids)) != version.refund_ids):
            raise DjangoValidationError('加购退款与结算版本不一致')
        try:
            unchanged(saved, after.get('points_funding', {}).get('addons', []))
            old_people = {item['account_id']: item for item in before.get('people', [])}
            new_people = {item['account_id']: item for item in after.get('people', [])}
            if any(old_people.get(item['account_id']) != new_people.get(item['account_id'])
                   for item in saved.get('people', [])):
                raise ValidationError('已付主单收益发生变化')
            for refund_id in version.refund_ids:
                run = OrderAddonFundingReleaseRun.objects.filter(refund_id=refund_id,
                    refund__order_id=manifest.order_id).select_related('refund__order').first()
                if not run or not isinstance(run.refunded_main_snapshot, dict) or (
                        run.refunded_main_snapshot.get('zero_termination_id') != zero_revision.termination_id):
                    raise ValidationError('加购退款缺少零退款终止资金来源')
                release = run.releases.select_related('run', 'allocation').order_by('pk').first()
                if release:
                    after_main_addon_release_facts(release, witness_receipt_id=receipt_id)
                else:
                    zero_release_facts(run, receipt_id=receipt_id)
        except (ValidationError, KeyError, TypeError) as exc:
            raise DjangoValidationError('零退款后的加购结算资金凭证异常') from exc
        previous, current_hash = after, version.after_plan_hash
    if manifest.plan != previous or manifest.plan_hash != current_hash:
        raise DjangoValidationError('零退款后的结算版本与清单不一致')
    return current_hash
