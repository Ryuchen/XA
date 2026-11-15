"""Return excess order allocations to the shared receipt after an add-on refund."""
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (OrderAddonFundingRelease, OrderAddonFundingReleaseRun,
                     OrderPointsFundingAllocation)
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_plan import PointsFundingPlanError


def verify_run(run, *, _skip_usage=False, witness_receipt_id=None):
    from .models import PointsThirdAddonFundingRevision
    if PointsThirdAddonFundingRevision.objects.filter(run=run).exists():
        from .points_third_addon_receipt_usage import _verify, verify_run_locked
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            if witness_receipt_id is None:
                verify_run_locked(run)
            else:
                _verify(run, witness_receipt_id=witness_receipt_id)
        except DjangoValidationError as exc:
            raise ValidationError('第三轮后加购资金事件待核对') from exc
        return run
    snapshot = run.allocation_snapshot
    if not isinstance(snapshot, list):
        raise ValidationError('加购补资释放依据不完整')
    if witness_receipt_id is None and snapshot and (run.points_payment_snapshot is not None
            or run.points_addon_snapshot is not None or run.points_handover_snapshot is not None):
        witness_receipt_id = OrderPointsFundingAllocation.objects.filter(pk=snapshot[0][0]).values_list(
            'receipt_id', flat=True).first()
    if run.negotiation_id and not run.negotiation.refund_id:
        raise ValidationError('普通退款补资释放缺少退款凭证')
    order_id = (run.refund.order_id if run.refund_id else run.handover.payment.intent.order_id
                if run.handover_id else run.payment.intent.order_id if run.payment_id
                else run.prestart_handoff.order_id if run.prestart_handoff_id
                else run.ordinary_transfer_revision.order_id if run.ordinary_transfer_revision_id
                else run.negotiation.refund.order_id)
    if (witness_receipt_id is None and not _skip_usage
            and ((run.handover_id and (run.points_handover_snapshot is not None or
                  (isinstance(run.refunded_main_snapshot, dict)
                   and run.refunded_main_snapshot.get('transfer_revision_id'))))
                 or (run.payment_id and isinstance(run.points_payment_snapshot, dict)
                     and run.points_payment_snapshot.get('postmain')))):
        witness_receipt_id = OrderPointsFundingAllocation.objects.filter(
            redemption__order_id=order_id).order_by('pk').values_list('receipt_id', flat=True).first()
    from .models import OrderPointsFundingRelease, OrderPointsTransferFundingRelease
    normalized = []
    try:
        for item in snapshot:
            if (not isinstance(item, list) or len(item) not in (3, 4, 5, 6, 7)
                    or (len(item) == 7 and (not isinstance(run.points_followup_snapshot, dict)
                        or run.points_followup_snapshot.get('kind') != 'TRANSFER_FOLLOWUP_V1'
                        or not run.points_followup_snapshot.get('post_followup_transfer_id')))):
                raise ValidationError('加购补资释放依据不完整')
            allocation_id, amount, previous_addon = item[:3]
            refs = item[3] if len(item) >= 4 else []
            if not isinstance(refs, list):
                raise ValidationError('转单补资释放依据不完整')
            transfer_rows = list(OrderPointsTransferFundingRelease.objects.filter(
                allocation_id=allocation_id, revision__created_at__lte=run.created_at)
                .select_related('revision').order_by('pk'))
            if (any(row.revision.order_id != order_id or row.revision.created_at == run.created_at for row in transfer_rows)
                    or [[row.pk, row.amount] for row in transfer_rows] != refs):
                raise ValidationError('转单补资释放依据不一致')
            main_refs = item[4] if len(item) >= 5 else []
            main_rows = list(OrderPointsFundingRelease.objects.filter(allocation_id=allocation_id,
                created_at__lte=run.created_at).select_related('posting').order_by('pk'))
            if (any(row.created_at == run.created_at or row.posting.proposal.case.order_id != order_id for row in main_rows)
                    or [[row.pk, row.amount] for row in main_rows] != main_refs):
                raise ValidationError('主单补资释放依据不一致')
            post_refs = item[5] if len(item) >= 6 else []
            from .models import PointsPostRefundTransferRelease
            post_rows = list(PointsPostRefundTransferRelease.objects.filter(allocation_id=allocation_id,
                revision__created_at__lte=run.created_at).select_related('revision').order_by('pk'))
            if (not isinstance(post_refs, list) or any(row.revision.order_id != order_id
                    or row.revision.created_at == run.created_at for row in post_rows)
                    or [[row.pk, row.amount] for row in post_rows] != post_refs):
                raise ValidationError('退款后转单补资释放依据不一致')
            followup_rows = []
            if len(item) == 7:
                from .models import PointsFollowupTransferRelease
                followup_rows = list(PointsFollowupTransferRelease.objects.filter(allocation_id=allocation_id,
                    revision__created_at__lte=run.created_at).select_related('revision').order_by('pk'))
                if (not isinstance(item[6], list) or any(row.revision.order_id != order_id
                        or row.revision.created_at == run.created_at for row in followup_rows)
                        or [[row.pk, row.amount] for row in followup_rows] != item[6]):
                    raise ValidationError('二次退款后转单释放依据不一致')
            previous = (previous_addon + sum(row.amount for row in transfer_rows) + sum(row.amount for row in main_rows)
                        + sum(row.amount for row in post_rows) + sum(row.amount for row in followup_rows))
            normalized.append([allocation_id, amount, previous])
        before = sum(item[1] - item[2] for item in normalized)
        expected_amount = max(before - run.required_amount, 0)
        expected = plan_release(allocations=normalized, amount=expected_amount)
    except (TypeError, IndexError, PointsFundingAllocationError) as exc:
        raise ValidationError('加购补资释放依据不完整') from exc
    if (before != run.allocated_before or run.released_amount != expected_amount
            or [(row.allocation_id, row.amount) for row in run.releases.order_by('-allocation_id')]
            != [(row.allocation_id, row.amount) for row in expected]):
        raise ValidationError('加购补资释放金额不一致')
    for item in snapshot:
        allocation_id, amount, previous = item[:3]
        allocation = OrderPointsFundingAllocation.objects.select_related('redemption').filter(pk=allocation_id).first()
        if (not allocation or allocation.funding_order_id != order_id or allocation.amount != amount
                or sum(OrderAddonFundingRelease.objects.filter(allocation_id=allocation_id,
                    run_id__lt=run.pk).values_list('amount', flat=True)) != previous):
            raise ValidationError('加购补资释放对应的分配已变化')
    if run.refunded_main_snapshot is not None:
        from .models import OrderPointsFundingReleaseEvidence
        posting_id = run.refunded_main_snapshot.get('posting_id') if isinstance(run.refunded_main_snapshot, dict) else None
        evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting_id=posting_id).first()
        if not evidence:
            raise ValidationError('主单退款补资释放凭证缺失')
        if ((run.refunded_main_snapshot.get('transfer_revision_id')
                or run.refunded_main_snapshot.get('transfer_origin_posting_id'))
                and isinstance(evidence.posting.participants, dict)):
            from .points_transfer_refund_release import verify_release_locked
        else:
            from .points_funding_release import verify_release_locked
        if (_skip_usage and witness_receipt_id and isinstance(evidence.posting.participants, list)):
            from types import SimpleNamespace
            from users.receipt_usage_sources import first_negotiation_release_facts
            from django.core.exceptions import ValidationError as DjangoValidationError
            witness = OrderPointsFundingAllocation.objects.filter(redemption__order_id=order_id,
                receipt_id=witness_receipt_id).order_by('pk').first()
            original_id = evidence.allocations[0][0] if evidence.allocations else None
            original = OrderPointsFundingAllocation.objects.filter(pk=original_id).first() if original_id else witness
            if not witness or not original:
                raise ValidationError('主单退款到账见证缺失')
            try:
                first_negotiation_release_facts(SimpleNamespace(posting=evidence.posting,
                    allocation=original, pk=0, amount=0, read_receipt_id=witness_receipt_id))
            except DjangoValidationError as exc:
                raise ValidationError('主单退款到账来源异常') from exc
        else:
            verify_release_locked(evidence, _skip_usage=_skip_usage)
        allocations = {row.pk: row for row in OrderPointsFundingAllocation.objects.filter(
            pk__in=[item[0] for item in snapshot])}
        net = [[item[0], item[1] - item[2], allocations[item[0]].receipt_id]
               for item in normalized]
        from .refunded_addon_revision import verify
        from .models import OrderOriginalShareTransfer
        historical_pretransfer = (isinstance(evidence.posting.participants, list)
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=order_id,
                created_at__gt=run.created_at).exists())
        receipt_local = (_skip_usage and bool(run.allocation_snapshot or witness_receipt_id)
                         and bool(historical_pretransfer or run.refunded_main_snapshot.get('transfer_revision_id')
                                  or run.refunded_main_snapshot.get('transfer_origin_posting_id')))
        witness = (OrderPointsFundingAllocation.objects.filter(
            pk__in=[item[0] for item in run.allocation_snapshot], receipt_id=witness_receipt_id).first()
            if receipt_local and witness_receipt_id else
            OrderPointsFundingAllocation.objects.filter(pk=run.allocation_snapshot[0][0]).first() if receipt_local else None)
        if receipt_local and not witness:
            witness = OrderPointsFundingAllocation.objects.filter(redemption__order_id=order_id,
                receipt_id=witness_receipt_id).order_by('pk').first()
        verify(run, net, receipt_local=receipt_local, witness_allocation=witness)
    elif run.refunded_main_hash:
        raise ValidationError('主单退款后的加购资金版本缺失')
    if run.points_followup_snapshot is not None or run.points_followup_hash:
        transfer_followup = (isinstance(run.points_followup_snapshot, dict)
                             and run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1')
        if (not isinstance(run.points_followup_snapshot, dict)
                or run.refunded_main_snapshot is not None or run.points_payment_snapshot is not None
                or run.points_addon_snapshot is not None
                or (run.handover_id and not transfer_followup)
                or sum(bool(value) for value in (run.refund_id, run.payment_id, run.handover_id)) != 1):
            raise ValidationError('后续退款的加购资金版本来源异常')
        allocations = {row.pk: row for row in OrderPointsFundingAllocation.objects.filter(
            pk__in=[item[0] for item in snapshot])}
        net = [[item[0], item[1] - item[2], allocations[item[0]].receipt_id] for item in normalized]
        if run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1':
            from .points_transfer_followup_addon_version import verify
        else:
            from .points_followup_addon_version import verify
        verify(run, net)
    if run.prestart_handoff_id:
        from .points_prestart_release import verify
        verify(run)
    elif run.prestart_snapshot is not None or run.prestart_hash:
        raise ValidationError('改派补资版本来源缺失')
    if run.ordinary_transfer_revision_id:
        from .ordinary_post_refund_release import verify_run as verify_ordinary_transfer_run
        verify_ordinary_transfer_run(run)
    elif run.negotiation_id:
        if (_skip_usage and witness_receipt_id is not None
                and isinstance(run.ordinary_refund_snapshot, dict)
                and run.ordinary_refund_snapshot.get('ordinary_transfer_followup_version') == 1):
            from .ordinary_transfer_followup_receipt import verify as verify_followup_receipt
            verify_followup_receipt(run, witness_receipt_id)
        elif (_skip_usage and witness_receipt_id is not None
                and isinstance(run.ordinary_refund_snapshot, dict)
                and run.ordinary_refund_snapshot.get('ordinary_transfer_refund_version') == 1):
            from .ordinary_transfer_first_receipt import verify as verify_transfer_receipt
            verify_transfer_receipt(run, witness_receipt_id)
        elif (_skip_usage and witness_receipt_id is not None
                and isinstance(run.ordinary_refund_snapshot, dict)
                and 'combined' in run.ordinary_refund_snapshot
                and 'addon_history' in run.ordinary_refund_snapshot):
            from .ordinary_refund_receipt_evidence import verified_release
            verified_release(run, witness_receipt_id)
        else:
            from .ordinary_refunded_addon_release import verify_ordinary_run
            verify_ordinary_run(run)
    elif (run.handover_id and isinstance(run.ordinary_refund_snapshot, dict)
          and run.ordinary_refund_snapshot.get('kind') == 'ordinary_refund_addon_handover_v1'):
        from .ordinary_refund_addon_payment_release import verify as verify_handover
        verify_handover(run, witness_receipt_id=witness_receipt_id if _skip_usage else None)
    elif run.handover_id and isinstance(run.ordinary_refund_snapshot, dict) and (
            run.ordinary_refund_snapshot.get('kind') in ('zero_addon_handover_v1', 'refunded_addon_handover_v1')):
        from .zero_addon_handover_release import verify_zero_handover_run
        verify_zero_handover_run(run)
    elif (run.refund_id and isinstance(run.ordinary_refund_snapshot, dict)
          and run.ordinary_refund_snapshot.get('kind') == 'ordinary_followup_addon_refund_v1'):
        if _skip_usage:
            from .ordinary_followup_addon_receipt import verify
            verify(run, witness_receipt_id)
        else:
            from .ordinary_followup_addon_release import verify_locked
            verify_locked(run)
    elif (run.refund_id and isinstance(run.ordinary_refund_snapshot, dict)
          and run.ordinary_refund_snapshot.get('kind') == 'ordinary_addon_after_main_refund_v1'):
        if _skip_usage and witness_receipt_id is not None:
            from .ordinary_after_main_addon_receipt import verified_release
            verified_release(run, witness_receipt_id)
        else:
            from .ordinary_addon_after_main_release import verify_run as verify_ordinary_addon_refund
            verify_ordinary_addon_refund(run)
    elif (run.payment_id and isinstance(run.ordinary_refund_snapshot, dict)
          and run.ordinary_refund_snapshot.get('kind') == 'ordinary_refund_addon_payment_v1'):
        from .ordinary_refund_addon_payment_release import verify as verify_payment
        verify_payment(run, witness_receipt_id=witness_receipt_id if _skip_usage else None)
    elif run.ordinary_refund_snapshot is not None or run.ordinary_refund_hash:
        raise ValidationError('普通退款补资版本来源缺失')
    if run.ordinary_prestart_snapshot is not None or run.ordinary_prestart_hash:
        from .ordinary_prestart_release import capture, digest
        if (isinstance(run.ordinary_prestart_snapshot, dict)
                and run.ordinary_prestart_snapshot.get('kind') in
                    ('ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1', 'ORDINARY_TRANSFER_PRE_MAIN_PAYMENT_V1',
                     'ORDINARY_TRANSFER_PRE_MAIN_HANDOVER_V1')):
            from .ordinary_transfer_pre_main_addon import capture
        if (run.ordinary_prestart_hash != digest(run.ordinary_prestart_snapshot)
                or capture(run) != run.ordinary_prestart_snapshot):
            raise ValidationError('普通改派加购补资快照异常')
    if run.points_payment_snapshot is not None or run.points_payment_hash:
        from .points_addon_receipt_usage import digest, payment_facts
        if (not run.payment_id or run.points_payment_hash != digest(run.points_payment_snapshot)
                or payment_facts(run.payment, read_receipt_id=witness_receipt_id) != run.points_payment_snapshot
                or (run.refunded_main_snapshot is None
                    and run.required_amount != run.points_payment_snapshot['required'])):
            raise ValidationError('积分加购付款补资快照异常')
    if run.points_addon_snapshot is not None or run.points_addon_hash:
        from .points_addon_receipt_usage import digest, facts
        if (not run.refund_id or run.points_addon_hash != digest(run.points_addon_snapshot)
                or facts(run.refund, read_receipt_id=witness_receipt_id) != run.points_addon_snapshot
                or (run.refunded_main_snapshot is None
                    and run.required_amount != run.points_addon_snapshot['required'])):
            raise ValidationError('积分加购退款补资快照异常')
    if run.points_handover_snapshot is not None or run.points_handover_hash:
        from .points_addon_receipt_usage import digest
        first = OrderPointsFundingAllocation.objects.filter(pk__in=[item[0] for item in snapshot]).first()
        team = isinstance(run.points_handover_snapshot, dict) and run.points_handover_snapshot.get('kind') == 'SAME_TEAM_V1'
        if isinstance(run.points_handover_snapshot, dict) and run.points_handover_snapshot.get('kind') == 'POINTS_VOLUNTARY_HANDOVER_V1':
            from .points_voluntary_handover_event import facts as voluntary_facts
            facts = voluntary_facts(run, receipt_id=witness_receipt_id or (first.receipt_id if first else None))
        elif run.refunded_main_snapshot is not None and team:
            from .points_team_postmain_handover_facts import facts as team_postmain_facts
            facts = team_postmain_facts(run, receipt_id=witness_receipt_id or (first.receipt_id if first else None))
        elif run.refunded_main_snapshot is not None:
            from .points_addon_handover_receipt_usage import postmain_facts
            facts = postmain_facts(run, witness_receipt_id or (first.receipt_id if first else None))
        elif team:
            from .points_team_addon_handover_facts import funding_facts
            facts = {'kind': 'SAME_TEAM_V1', **funding_facts(run, receipt_id=witness_receipt_id or (first.receipt_id if first else None))}
        else:
            from .points_addon_handover_receipt_usage import facts as transfer_facts
            facts = transfer_facts(run, witness_receipt_id or (first.receipt_id if first else None))
        if (not run.handover_id
                or run.points_handover_hash != digest(run.points_handover_snapshot)
                or facts != run.points_handover_snapshot):
            raise ValidationError('积分加购接替补资快照异常')
    return run


def addon_release_totals(allocations):
    ids = [row.pk for row in allocations]
    rows = list(OrderAddonFundingRelease.objects.filter(allocation_id__in=ids).select_related(
        'run__refund', 'run__handover__payment__intent'))
    for run in {row.run_id: row.run for row in rows}.values():
        verify_run(run)
    totals = {pk: 0 for pk in ids}
    for row in rows:
        totals[row.allocation_id] += row.amount
    for allocation in allocations:
        if totals[allocation.pk] > allocation.amount:
            raise ValidationError('加购补资释放超过原分配金额')
    return totals


def receipt_addon_released(receipt):
    allocations = list(receipt.points_funding_allocations.all())
    return sum(addon_release_totals(allocations).values())


def record_addon_funding_release(order, refund):
    """Rebalance a confirmed allocation within the refund's transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购退款和补资释放须在同一门店事务内执行')
    if refund.order_id != order.pk:
        raise ValidationError('退款与订单不一致')
    previous_run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
    if previous_run:
        return verify_run(previous_run)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.count() >= 3:
        from .points_third_addon_refund import record_locked
        return record_locked(order, refund)
    if not needs_integrated_processing(order):
        from .models import OrdinaryTransferFollowupRound
        if OrdinaryTransferFollowupRound.objects.filter(order=order,
                execution__termination_processed_at__lt=refund.created_at).exists():
            from .ordinary_followup_addon_release import record_locked
            return record_locked(order, refund)
    if not needs_integrated_processing(order):
        from .negotiation_zero import ordinary_funding_enabled
        if ordinary_funding_enabled(order):
            from .ordinary_addon_after_main_release import record_locked
            return record_locked(order, refund)
    return _record_funding_release(order, refund=refund)


def record_handover_funding_release(order, handover):
    """Release unused allocations atomically with the accepted responsibility split."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购换人与补资释放须在同一门店事务内执行')
    if handover.payment.intent.order_id != order.pk or handover.status != 'ACCEPTED':
        raise ValidationError('加购换人方案与订单状态不一致')
    from .points_transfer_followup_addon_version import handover_preflight_active
    if handover_preflight_active(order, handover):
        from .points_transfer_followup_addon_version import record_locked
        return record_locked(order, handover=handover)
    if handover.kind == 'VOLUNTARY':
        from .points_voluntary_handover_event import entry_matches
        from .points_business_access import _bind
        if not entry_matches(order, handover.payment_id):
            raise ValidationError('自愿接替不属于当前资金事务')
        with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            return _record_after_main_refund(order, handover=handover, _handover_scope=True, _voluntary=True)
    from .points_business_access import permits
    if permits(order, phase='handover', kind='addon'):
        if order.refund_records.exists():
            from .points_business_access import _bind
            with _bind(phase='refund', kind='addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                return _record_after_main_refund(order, handover=handover, _team_scope=True)
        from .points_team_addon_handover_release import record_locked
        return record_locked(order, handover)
    previous_run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
    if previous_run:
        return verify_run(previous_run)
    return _record_funding_release(order, handover=handover)


def record_payment_funding_release(order, payment):
    """Rebalance the whole order within the add-on payment transaction."""
    if not transaction.get_connection().in_atomic_block or payment.intent.order_id != order.pk:
        raise ValidationError('加购付款和补资核对须在同一门店事务内执行')
    previous = OrderAddonFundingReleaseRun.objects.filter(payment=payment).first()
    if previous:
        return verify_run(previous)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.count() >= 3:
        from .points_third_addon_payment import record_locked
        return record_locked(order, payment)
    if not needs_integrated_processing(order):
        from .negotiation_zero import ordinary_funding_enabled
        if ordinary_funding_enabled(order):
            from .ordinary_zero_post_addon import continuing_account_locked
            if continuing_account_locked(order) != payment.intent.provider_id:
                raise ValidationError('零退款后仅继续履约的陪玩可负责新加购')
            from .ordinary_refund_addon_payment_release import record_locked
            return record_locked(order, payment)
    if not needs_integrated_processing(order) and order.refund_records.count() == 1:
        from .ordinary_refund_addon_payment_release import record_locked
        return record_locked(order, payment)
    if needs_integrated_processing(order) and order.refund_records.exists():
        return _record_after_main_refund(order, payment=payment)
    return _record_funding_release(order, payment=payment)


def record_prestart_funding_release(order, handoff, plan):
    """Return excess verified allocations in the reassignment transaction."""
    if not transaction.get_connection().in_atomic_block or handoff.order_id != order.pk:
        raise RuntimeError('改派补资释放须在门店事务内执行')
    from .points_funding_evidence import verified_order_allocations_locked
    from .points_prestart_release import capture, digest
    allocations = verified_order_allocations_locked(order)
    if not allocations:
        return None
    prior = addon_release_totals(allocations)
    snapshot = [[row.pk, row.amount, prior[row.pk], []] for row in allocations]
    normalized = [[row[0], row[1], row[2]] for row in snapshot]
    before = sum(row[1] - row[2] for row in normalized)
    amount = max(before - plan.additional_store_funding_units, 0)
    if not amount:
        return None
    try:
        parts = plan_release(allocations=normalized, amount=amount)
    except PointsFundingAllocationError as exc:
        raise ValidationError('改派补资释放金额异常') from exc
    facts = capture(order, handoff)
    if facts['funding']['additional_store_funding_units'] != plan.additional_store_funding_units:
        raise ValidationError('改派补资计算不一致')
    run = OrderAddonFundingReleaseRun.objects.create(prestart_handoff=handoff,
        required_amount=plan.additional_store_funding_units, allocated_before=before,
        released_amount=amount, allocation_snapshot=snapshot, prestart_snapshot=facts)
    run.prestart_hash = digest(run, facts)
    run.save(update_fields=['prestart_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    for release in run.releases.select_related('allocation__redemption', 'run__prestart_handoff').order_by('allocation__receipt_id', 'pk'):
        record_first_addon_release_locked(release)
    for receipt_id in sorted({row.receipt_id for row in allocations}):
        verify_allocations_locked(receipt_id)
    return verify_run(run)


def _record_funding_release(order, *, refund=None, handover=None, payment=None):
    owner = Q(redemption__order=order) | Q(service_order=order)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order) and order.refund_records.exists():
        return _record_after_main_refund(order, refund=refund, handover=handover, payment=payment)
    from .points_team_other_addon_refund import applies as team_other_applies
    team_refund = bool(refund is not None and (OrderAddonFundingReleaseRun.objects.filter(
        handover__payment=refund.payment, points_handover_snapshot__kind='SAME_TEAM_V1').exists()
        or team_other_applies(refund)))
    # A transferred order can refund an add-on before the store makes its
    # first funding allocation.  Persist that event with an empty allocation
    # snapshot: a later allocation must not be treated as having existed at
    # refund time, and the first main refund needs the real earlier version.
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    zero_allocation_transfer_refund = bool(
        refund is not None and not order.refund_records.exists()
        and ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True)
    )
    from .ordinary_refunded_addon_paid_revision import posting_refund_active
    ordinary_paid_refund = bool(refund is not None and posting_refund_active(refund))
    if (not OrderPointsFundingAllocation.objects.filter(owner).exists()
            and not team_refund and not zero_allocation_transfer_refund and not ordinary_paid_refund):
        return None
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    if (refund is not None and not needs_integrated_processing(order) and order.refund_records.count() == 1
            and (ordinary_addon_eligible(order, allow_open=True)
                 or getattr(settings, 'ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING', False))):
        from .ordinary_addon_after_main_release import record_locked
        return record_locked(order, refund)
    if handover is not None and getattr(settings, 'ORDINARY_ZERO_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING', False):
        from .negotiation_zero import ordinary_funding_enabled
        if ordinary_funding_enabled(order, has_addons=True):
            from .points_checkout_test import require_isolated_checkout
            from .zero_addon_handover_release import record_locked
            require_isolated_checkout()
            return record_locked(order, handover)
    if (handover is not None and not needs_integrated_processing(order) and order.refund_records.count() == 1
            and getattr(settings, 'ORDINARY_REFUNDED_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING', False)):
        from .points_checkout_test import require_isolated_checkout
        from .zero_addon_handover_release import record_locked
        require_isolated_checkout()
        return record_locked(order, handover)
    production_addon_refund = (refund is not None
                                and not order.refund_records.exists()
                                and ordinary_addon_eligible(order, allow_open=True))
    transferred_addon_refund = bool((refund is not None or payment is not None or handover is not None)
        and not order.refund_records.exists()
        and ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True))
    from .points_business_access import permits
    formal_points_addon = ((permits(order, phase='refund', kind='addon') and refund is not None)
                           or (permits(order, phase='payment', kind='addon') and payment is not None)
                           or (permits(order, phase='transfer', kind='transfer') and handover is not None))
    if not (formal_points_addon or getattr(settings, 'ADDON_ORDER_FUNDING_INTERNAL_TESTING', False)
            or ordinary_addon_eligible(order) or production_addon_refund or transferred_addon_refund):
        raise ValidationError('加购退款后的整单补资尚未开放')
    from .addon_funding import include_paid_addons
    from .points_funding_evidence import verified_order_allocations_locked
    try:
        if needs_integrated_processing(order):
            from .models import OrderOriginalShareTransfer
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                from .points_transfer_funding import plan_points_transfer
                from .points_transfer_refund_access import addon_payment_read
                with addon_payment_read(order):
                    main_plan = plan_points_transfer(order.pk, allow_recorded=True).funding
            else:
                from .points_funding_plan import plan_verified_order
                main_plan = plan_verified_order(order.pk)
        else:
            from .service_funding_plan import plan_verified_ordinary_order
            main_plan = plan_verified_ordinary_order(order.pk, _allow_ordinary_transfer=transferred_addon_refund)
        # A refund may precede handover of the remaining work. Compute the
        # funding release now; regular settlement still requires handover.
        plan = include_paid_addons(order, main_plan,
                                   allow_unassigned=(refund is not None or
                                       (handover is not None and (formal_points_addon or transferred_addon_refund))),
                                   allow_open_aftersale=production_addon_refund or transferred_addon_refund or formal_points_addon)
        if production_addon_refund or transferred_addon_refund:
            from .points_funding_evidence import _verified_allocations_for_locked_order
            allocations = _verified_allocations_for_locked_order(order)
        else:
            from .points_addon_receipt_usage import eligible as addon_usage_eligible
            allocations = verified_order_allocations_locked(order,
                _addon_refund_usage=bool(refund is not None and addon_usage_eligible(order)))
        from .models import (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                             ReservationPointsCancelFundingRelease)
        if (OrderPointsFundingRelease.objects.filter(allocation__in=allocations).exists()
                or ReservationPointsCancelFundingRelease.objects.filter(allocation__in=allocations).exists()):
            raise ValidationError('主单或预约已有补资释放，须先使用对应退款结算流程')
        previous = addon_release_totals(allocations)
        transfer_rows = list(OrderPointsTransferFundingRelease.objects.filter(allocation__in=allocations)
                             .select_related('revision').order_by('pk'))
        if transfer_rows:
            from .points_transfer_revision import verify_revisions_locked
            from .points_transfer_refund_access import addon_payment_read
            with addon_payment_read(order):
                verified, _ = verify_revisions_locked(order)
            for allocation in allocations:
                actual = sum(row.amount for row in transfer_rows if row.allocation_id == allocation.pk)
                if verified.get(allocation.pk, 0) != actual:
                    raise ValidationError('转单补资释放与版本凭证不一致')
        snapshot = [[item.pk, item.amount, previous[item.pk],
                     [[row.pk, row.amount] for row in transfer_rows if row.allocation_id == item.pk]]
                    for item in allocations]
        normalized = [[item[0], item[1], item[2] + sum(row[1] for row in item[3])] for item in snapshot]
        before = sum(item[1] - item[2] for item in normalized)
        amount = max(before - plan.additional_store_funding_units, 0)
        parts = plan_release(allocations=normalized, amount=amount)
    except (PointsFundingPlanError, PointsFundingAllocationError) as exc:
        raise ValidationError(str(exc)) from exc
    run = OrderAddonFundingReleaseRun.objects.create(refund=refund, handover=handover, payment=payment,
        required_amount=plan.additional_store_funding_units, allocated_before=before,
        released_amount=amount, allocation_snapshot=snapshot)
    from .ordinary_prestart_addon import supports as ordinary_prestart_supported
    from .ordinary_untransferred_scope import supports as ordinary_original_supported
    use_ordinary_prestart_ledger = bool(not needs_integrated_processing(order)
        and not order.refund_records.exists() and (refund is not None or payment is not None or handover is not None)
        and (ordinary_prestart_supported(order) or ordinary_original_supported(order)))
    use_ordinary_transfer_ledger = bool(transferred_addon_refund)
    if use_ordinary_prestart_ledger or use_ordinary_transfer_ledger:
        from .ordinary_prestart_release import capture, digest
        if use_ordinary_transfer_ledger:
            from .ordinary_transfer_pre_main_addon import capture as capture_transfer
            frozen = capture_transfer(run)
        else:
            frozen = capture(run)
        run.ordinary_prestart_snapshot, run.ordinary_prestart_hash = frozen, digest(frozen)
        run.save(update_fields=['ordinary_prestart_snapshot', 'ordinary_prestart_hash'])
    from .points_addon_receipt_usage import eligible as addon_usage_eligible
    use_receipt_ledger = (not use_ordinary_prestart_ledger and not use_ordinary_transfer_ledger and refund is not None and addon_usage_eligible(order)
                          and (team_refund or not allocations
                               or any(item.receipt.usage_version is not None for item in allocations)))
    if use_receipt_ledger:
        from dataclasses import asdict
        from .points_addon_receipt_usage import digest, facts
        # Bind the persisted database timestamp, not a pre-insert Python value.
        refund.refresh_from_db(fields=['created_at'])
        receipt_id = next((item.receipt_id for item in allocations if item.receipt.usage_version is not None), None)
        frozen = facts(refund, read_receipt_id=receipt_id)
        if frozen['main_plan'] != asdict(main_plan) or frozen['required'] != plan.additional_store_funding_units:
            raise ValidationError('加购退款整单补资快照不一致')
        run.points_addon_snapshot, run.points_addon_hash = frozen, digest(frozen)
        run.save(update_fields=['points_addon_snapshot', 'points_addon_hash'])
    use_payment_ledger = (not use_ordinary_prestart_ledger and payment is not None and permits(order, phase='payment', kind='addon')
                          and any(item.receipt.usage_version is not None for item in allocations))
    if use_payment_ledger:
        from dataclasses import asdict
        from .points_addon_receipt_usage import digest, payment_facts
        receipt_id = next(item.receipt_id for item in allocations if item.receipt.usage_version is not None)
        frozen = payment_facts(payment, read_receipt_id=receipt_id)
        if frozen['main_plan'] != asdict(main_plan) or frozen['required'] != plan.additional_store_funding_units:
            raise ValidationError('积分加购付款整单补资快照不一致')
        run.points_payment_snapshot, run.points_payment_hash = frozen, digest(frozen)
        run.save(update_fields=['points_payment_snapshot', 'points_payment_hash'])
    use_handover_ledger = (handover is not None and permits(order, phase='transfer', kind='transfer')
                           and (not allocations or any(item.receipt.usage_version is not None for item in allocations)))
    if use_handover_ledger:
        from .points_addon_receipt_usage import digest
        from .points_addon_handover_receipt_usage import facts
        receipt_id = next((item.receipt_id for item in allocations if item.receipt.usage_version is not None), None)
        run.refresh_from_db(fields=['created_at'])
        frozen = facts(run, receipt_id)
        run.points_handover_snapshot, run.points_handover_hash = frozen, digest(frozen)
        run.save(update_fields=['points_handover_snapshot', 'points_handover_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount)
        for part in parts])
    verified = verify_run(run)
    if use_ordinary_prestart_ledger or use_ordinary_transfer_ledger:
        from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
        touched = set()
        for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
            if row.allocation.receipt.usage_version is not None:
                record_first_addon_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    if use_handover_ledger:
        from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
        touched = set()
        for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
            if row.allocation.receipt.usage_version is not None:
                record_first_addon_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    if use_payment_ledger:
        from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
        touched = set()
        for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
            if row.allocation.receipt.usage_version is not None:
                record_first_addon_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    if use_receipt_ledger:
        from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
        touched = set()
        rows = run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk')
        for row in rows:
            if row.allocation.receipt.usage_version is not None:
                record_first_addon_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    return verified


def _record_after_main_refund(order, *, refund=None, handover=None, payment=None, _handover_scope=False, _team_scope=False,
                              _voluntary=False, _payment_scope=False):
    """Persist a new whole-order funding version after an add-on change."""
    from .points_transfer_refund_access import permits as transfer_permits, verification_scope
    if handover is not None and transfer_permits(order, phase='transfer') and not _handover_scope:
        with verification_scope(order):
            return _record_after_main_refund(order, handover=handover, _handover_scope=True)
    if order.refund_records.count() == 2:
        if handover is not None or order.settled_at:
            raise ValidationError('后续退款后的加购资金范围待核对')
        if transfer_permits(order, phase='refund'):
            from .points_transfer_followup_addon_version import record_locked
            return record_locked(order, refund, payment=payment)
        from .points_business_access import permits as business_permits
        if transfer_permits(order, phase='payment') and business_permits(order, phase='payment', kind='addon'):
            if payment is None or refund is not None:
                raise ValidationError('转单后续加购付款事件异常')
            from .points_transfer_followup_addon_version import record_locked
            return record_locked(order, payment=payment)
        from .points_followup_addon_version import record_locked
        return record_locked(order, refund=refund, payment=payment)
    from .points_business_access import permits
    if (payment is not None and order.refund_records.count() == 1
            and transfer_permits(order, phase='payment') and permits(order, phase='payment', kind='addon')
            and not _payment_scope):
        # The real payment entry already owns the order and money locks.  Bind
        # the existing refund verifier only for this transaction and order.
        from .points_business_access import _bind
        with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            return _record_after_main_refund(order, payment=payment, _payment_scope=True)
    formal_refund = permits(order, phase='refund', kind='addon') and refund is not None
    formal_payment = payment is not None and (permits(order, phase='payment', kind='addon')
        or (_payment_scope and permits(order, phase='refund', kind='transfer')))
    formal_handover = (handover is not None and
        ((_handover_scope and permits(order, phase='refund', kind='transfer'))
         or (_team_scope and permits(order, phase='refund', kind='addon'))))
    formal = formal_refund or formal_payment or formal_handover
    if not formal and not getattr(settings, 'SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING', False):
        raise ValidationError('主单退款后的加购资金版本尚未开放')
    if not formal:
        from .points_checkout_test import require_isolated_checkout
        require_isolated_checkout()
    from .models import (OrderOriginalShareTransfer, OrderPointsFundingRelease,
                         OrderPointsFundingReleaseEvidence, PointsNegotiationPosting)
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    from .points_transfer_refund_access import permits as transfer_permits
    if (order.settled_at or order.refund_records.count() != 1 or (handover is not None and not formal_handover)
            or (transferred and not (formal and transfer_permits(order, phase='refund')))):
        raise ValidationError('主单退款后的加购变更尚待核对')
    posting = PointsNegotiationPosting.objects.select_related('proposal__case__order',
        'points_refund__redemption').filter(points_refund__redemption__order=order).first()
    evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first() if posting else None
    if not evidence:
        raise ValidationError('主单退款补资凭证缺失')
    if transferred and isinstance(posting.participants, dict):
        from .points_transfer_refund_release import verify_release_locked
    else:
        from .points_funding_release import verify_release_locked
    verify_release_locked(evidence)
    from .points_funding_evidence import verified_order_allocations_locked
    allocations = verified_order_allocations_locked(order)
    previous = addon_release_totals(allocations)
    main_rows = list(OrderPointsFundingRelease.objects.filter(allocation__in=allocations).order_by('pk'))
    if any(row.posting_id != posting.pk for row in main_rows):
        raise ValidationError('主单退款补资释放归属待核对')
    from .models import OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease
    transfer_rows = list(OrderPointsTransferFundingRelease.objects.filter(allocation__in=allocations).order_by('pk')) if transferred else []
    post_rows = list(PointsPostRefundTransferRelease.objects.filter(allocation__in=allocations).order_by('pk')) if transferred else []
    snapshot = [[item.pk, item.amount, previous[item.pk],
                 [[row.pk, row.amount] for row in transfer_rows if row.allocation_id == item.pk],
                 [[row.pk, row.amount] for row in main_rows if row.allocation_id == item.pk]]
                + ([[[row.pk, row.amount] for row in post_rows if row.allocation_id == item.pk]] if transferred else [])
                for item in allocations]
    normalized = [[item[0], item[1], item[2] + sum(row[1] for row in item[3])
                   + sum(row[1] for row in item[4]) + (sum(row[1] for row in item[5]) if transferred else 0)]
                  for item in snapshot]
    before = sum(item[1] - item[2] for item in normalized)
    net = [[item[0], item[1] - item[2], allocation.receipt_id]
           for item, allocation in zip(normalized, allocations)]
    from django.utils import timezone
    from .refunded_addon_revision import capture, digest
    facts = capture(order, posting, net, timezone.now())
    required = facts['funding']['required_units']
    amount = max(before - required, 0)
    try:
        parts = plan_release(allocations=normalized, amount=amount)
    except PointsFundingAllocationError as exc:
        raise ValidationError('主单退款后的加购补资释放异常') from exc
    run = OrderAddonFundingReleaseRun.objects.create(refund=refund, payment=payment, handover=handover, required_amount=required,
        allocated_before=before, released_amount=amount, allocation_snapshot=snapshot, refunded_main_snapshot=facts)
    run.refunded_main_hash = digest(run, facts)
    run.save(update_fields=['refunded_main_hash'])
    if formal:
        from .points_addon_receipt_usage import facts as addon_facts, payment_facts, digest as addon_digest
        if formal_payment:
            run.points_payment_snapshot = payment_facts(payment)
            run.points_payment_hash = addon_digest(run.points_payment_snapshot)
            run.save(update_fields=['points_payment_snapshot', 'points_payment_hash'])
        elif formal_refund:
            run.points_addon_snapshot = addon_facts(
                refund, read_receipt_id=allocations[0].receipt_id if allocations else None)
            run.points_addon_hash = addon_digest(run.points_addon_snapshot)
            run.save(update_fields=['points_addon_snapshot', 'points_addon_hash'])
        elif not _voluntary:
            run.refresh_from_db(fields=['created_at'])
            if _team_scope:
                from .points_team_postmain_handover_facts import facts as postmain_facts
                run.points_handover_snapshot = postmain_facts(
                    run, receipt_id=allocations[0].receipt_id if allocations else None)
            else:
                from .points_addon_handover_receipt_usage import postmain_facts
                run.points_handover_snapshot = postmain_facts(run, allocations[0].receipt_id if allocations else None)
            run.points_handover_hash = addon_digest(run.points_handover_snapshot)
            run.save(update_fields=['points_handover_snapshot', 'points_handover_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    if _voluntary:
        from .points_voluntary_handover_event import write_locked
        verified = write_locked(run)
    else:
        verified = verify_run(run, _skip_usage=formal)
    if formal:
        from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
        rows = list(run.releases.select_related('allocation__receipt', 'run').order_by('allocation__receipt_id', 'pk'))
        touched = set()
        for row in rows:
            if row.allocation.receipt.usage_version is not None:
                record_first_addon_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    return verified
