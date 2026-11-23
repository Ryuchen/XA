"""One transaction-local capability for controlled MySQL points business.

These helpers are not endpoint authorization and do not claim the complete
checkout-to-settlement route is ready. Callers must still enforce role,
pricing, assignment and their stage's business rules in the same transaction.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.context import current_store
from stores.db import transaction


_scope = ContextVar('xa_points_business_capability', default=None)


def configured():
    """Configuration prerequisite only, not a user-facing availability result."""
    connection, store = transaction.get_connection(), current_store()
    return bool(getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
        and connection.vendor == 'mysql' and connection.in_atomic_block and store
        and store.database_alias == connection.alias)


def formal_service_read_candidate(order, *, phase):
    if phase not in ('report', 'settlement'):
        return False
    from .models import Order
    from .points_service_payment import display_has_points
    statuses = (Order.Status.IN_SERVICE, Order.Status.COMPLETED) if phase == 'report' else (Order.Status.COMPLETED,)
    return (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and order.status in statuses and display_has_points(order))


@contextmanager
def formal_service_read_scope_locked(order, *, phase):
    """Reenter the plain service capability after locking the tenant order."""
    from .addon_settlement import has_paid_addons
    from .models import Order, OrderOriginalShareTransfer
    if (not configured() or not formal_service_read_candidate(order, phase=phase)
            or order.payment_status != Order.PaymentStatus.PAID or order.refund_records.exists()
            or has_paid_addons(order)
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        yield False
        return
    from .financial_lock import lock_existing_order_money
    lock_existing_order_money()
    with service_order(order.pk, phase=phase):
        yield True


def checkout_shape_allowed(data):
    """Specified one- or two-person services with frozen assignment rules."""
    from .models import Order
    providers = data.get('provider_accounts') if isinstance(data, dict) else None
    if not isinstance(providers, (list, tuple)):
        return False
    required = data.get('required_provider_count')
    if required not in (1, 2):
        return False
    route, mode = data.get('dispatch_route'), data.get('dispatch_mode')
    return bool((route == Order.DispatchRoute.PUBLIC and len(providers) == required and mode == 'BOSS_SELECT'
                 and not data.get('commission_allocation_pending'))
        or (not providers and ((route == Order.DispatchRoute.PUBLIC and mode == 'AUTO_GRAB')
                              or (route == Order.DispatchRoute.STAFF and mode in ('AUTO_GRAB', 'BOSS_SELECT')))
            and data.get('commission_allocation_pending') is True))


def permits(order=None, *, phase=None, kind=None, account=None, service=None):
    state, store = _scope.get(), current_store()
    if not state or not configured() or not store:
        return False
    alias, slug, order_id, account_id, service_id, active_phase, active_kind = state
    return bool((alias, slug) == (transaction.get_connection().alias, store.slug)
        and (order is None or getattr(order, 'pk', None) == order_id)
        and (account is None or getattr(account, 'pk', None) == account_id)
        and (service is None or getattr(service, 'pk', None) == service_id)
        and (phase is None or phase == active_phase)
        and (kind is None or kind == active_kind or (active_kind == 'transfer_addon' and kind in ('transfer', 'addon'))))


@contextmanager
def transferred_refunded_addon(order_id, payment_id, *, replay_proposal_id=None):
    """Bind both capabilities only after the same order's transfer locks."""
    from .models import Order
    third_order = Order.objects.filter(pk=order_id).first()
    if third_order and third_order.refund_records.count() >= 3:
        from .points_transfer_refund_access import post_third_addon_refund
        with post_third_addon_refund(order_id, payment_id, replay_proposal_id=replay_proposal_id) as order:
            yield order
        return
    from .points_transfer_refund_access import after_transfer
    from .models import (OrderAddonPayment, PointsPostRefundTransferRevision, OrderSegmentSettlementManifest,
                         OrderPointsFundingAllocation, PointsNegotiationPosting)
    from .models import OrderAddonHandover, Order, PointsFollowupTransferRevision
    from contextlib import nullcontext
    from .points_voluntary_handover_event import entry as voluntary_entry
    from .models import PointsHandoverRefundInvalidation
    voluntary = (OrderAddonHandover.objects.filter(payment__intent__order_id=order_id, payment_id=payment_id,
        kind='VOLUNTARY', status__in=('PENDING', 'ACCEPTED', 'REJECTED', 'SUPERSEDED')).exists()
        or OrderAddonHandover.objects.filter(payment__intent__order_id=order_id,
                                              kind='VOLUNTARY', status='PENDING').exists()
        or PointsHandoverRefundInvalidation.objects.filter(handover__kind='VOLUNTARY',
            refund__order_id=order_id, refund__payment_id=payment_id).exists())
    order_hint = Order.objects.filter(pk=order_id).first()
    first_refund = order_hint.refund_records.order_by('created_at', 'pk').first() if order_hint else None
    postmain_payment = bool(first_refund and OrderAddonPayment.objects.filter(
        pk=payment_id, intent__order_id=order_id,
        buyer_transaction__created_at__gt=first_refund.created_at).exists())
    followup_payment = bool(order_hint and order_hint.refund_records.count() == 2
                            and OrderAddonPayment.objects.filter(pk=payment_id, intent__order_id=order_id).exists())
    pending_hint = []
    if order_hint and (order_hint.refund_records.count() in (1, 2) or voluntary):
        pending_hint = list(OrderAddonHandover.objects.filter(payment__intent__order_id=order_id,
            status='PENDING').values_list('payment_id', flat=True))
    pending_payment = (frozenset(pending_hint) if pending_hint
                       and len(pending_hint) == len(set(pending_hint)) else None)
    from .models import OrderAddonRefund
    settled_replay_hint = bool(replay_proposal_id is not None and order_hint and order_hint.settled_at
        and OrderAddonRefund.objects.filter(order_id=order_id, payment_id=payment_id,
                                           proposal_id=replay_proposal_id).exists())
    with (voluntary_entry(order_id, payment_id) if voluntary else nullcontext()), \
            after_transfer(order_id, phase='settlement' if settled_replay_hint else 'refund', pending_payment_id=pending_payment,
                           allow_voluntary_history=postmain_payment or followup_payment) as order:
        if replay_proposal_id is not None and order.refund_records.count() == 2:
            from .models import (OrderAddonRefund, OrderAddonFundingReleaseRun,
                OrderPointsTransferFollowupFundingRevision, PointsTransferAddonLiabilityRevision)
            previous = OrderAddonRefund.objects.select_for_update().filter(order=order, payment_id=payment_id,
                proposal_id=replay_proposal_id, proposal__status='ACCEPTED').first()
            if previous:
                followup = OrderPointsTransferFollowupFundingRevision.objects.filter(
                    posting__points_refund__redemption__order=order).select_related(
                        'posting__proposal__case__order', 'posting__points_refund__refund').first()
                records = list(PointsHandoverRefundInvalidation.objects.filter(refund=previous,
                    handover__payment_id=payment_id).select_related(
                        'handover__payment__intent', 'refund__payment__intent').order_by('pk'))
                snapshot = followup.pending_handover_snapshot if followup else None
                if (followup and isinstance(snapshot, list) and len(records) == 1
                        and previous.created_at > followup.posting.created_at
                        and any(isinstance(item, dict) and item.get('handover_id') == records[0].handover_id
                                and item.get('payment_id') == payment_id for item in snapshot)):
                    from .points_transfer_followup_pending import verify_snapshot
                    from .points_followup_handover_invalidation import verify as verify_invalidation
                    from .points_transfer_addon_liability import verify_locked as verify_liability
                    from .addon_funding_release import verify_run
                    run = OrderAddonFundingReleaseRun.objects.filter(refund=previous).first()
                    liability = PointsTransferAddonLiabilityRevision.objects.filter(
                        order=order, payment_id=payment_id, refund=previous, funding_run=run).first() if run else None
                    item = next(item for item in snapshot if isinstance(item, dict)
                        and item.get('handover_id') == records[0].handover_id and item.get('payment_id') == payment_id)
                    if not liability or liability.transfer_revision_id != item.get('transfer_revision_id'):
                        raise ValidationError('原加购退款责任版本缺失')
                    if order.settled_at:
                        if not settled_replay_hint:
                            raise ValidationError('已结算退款重试状态已变化')
                        from .points_transfer_followup_termination import verify_closed_or_settled_locked
                        verify_closed_or_settled_locked(followup.posting)
                    witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                    receipt_id = witness.receipt_id if witness else None
                    with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                               account_id=order.customer_account_id, service_id=order.service_id):
                        verify_snapshot(followup.posting, snapshot, receipt_id=receipt_id)
                        verify_liability(liability, witness_receipt_id=receipt_id)
                        verify_invalidation(records[0], receipt_id=receipt_id)
                        verify_run(run)
                        yield order
                    return
            if order.settled_at:
                raise ValidationError('已结算订单仅可回读原加购退款')
        post_transfer = PointsFollowupTransferRevision.objects.filter(order=order).first()
        if post_transfer:
            payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
            locked_pending = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                                  .values_list('payment_id', flat=True))
            if (len(locked_pending) != len(set(locked_pending)) or set(locked_pending) != set(pending_hint)):
                raise ValidationError('待确认接替已变化，请重新核对')
            if (not payment or order.settled_at
                    or not payment.buyer_transaction.created_at
                    or payment.buyer_transaction.created_at == post_transfer.transfer.created_at):
                raise ValidationError('新转单后的加购退款范围待核对')
            paid_or_manifest = (order.providers.filter(settled_at__isnull=False).exists()
                                or OrderSegmentSettlementManifest.objects.filter(order=order).exists())
            if paid_or_manifest and order.status != Order.Status.COMPLETED:
                raise ValidationError('整单完成前的已付清单待核对')
            from .points_followup_transfer_revision import verify_locked, verify_pending_or_completed_locked
            verify_locked(post_transfer)
            verify_pending_or_completed_locked(post_transfer, allow_unproposed=True)
            from .points_transfer_followup_addon_version import preflight_locked
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                with preflight_locked(order, payment_id=payment.pk):
                    yield order
            return
        locked_pending = list(OrderAddonHandover.objects.filter(
            payment__intent__order=order, status='PENDING').values_list('payment_id', flat=True))
        if pending_payment is not None and (len(locked_pending) != len(pending_payment)
                                             or set(locked_pending) != set(pending_payment)):
            raise ValidationError('待确认接替已变化，请重新核对')
        if voluntary:
            payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
            if (not payment or order.refund_records.count() not in (1, 2) or order.settled_at
                    or len(locked_pending) != len(set(locked_pending))
                    or OrderAddonHandover.objects.filter(payment__intent__order=order,
                        status='PENDING').exclude(kind='VOLUNTARY').exists()):
                raise ValidationError('自愿接替后的加购退款范围待核对')
            manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
            paid = order.providers.filter(settled_at__isnull=False).exists()
            if manifest or paid:
                from .points_voluntary_paid_evidence import capture_before_locked
                if not manifest or manifest.finalized_at or not capture_before_locked(order):
                    raise ValidationError('自愿接替退款前已付清单待核对')
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                if order.refund_records.count() == 2:
                    from .points_transfer_followup_addon_version import preflight_locked
                    with preflight_locked(order, payment_id=payment.pk):
                        yield order
                else:
                    from .points_transfer_refunded_funding import plan_refunded_transfer
                    from .points_funding_evidence import verify_order_funding
                    from .points_funding_plan import PointsFundingPlanError
                    try:
                        plan_refunded_transfer(order.pk)
                        verify_order_funding(order.pk)
                    except PointsFundingPlanError as exc:
                        raise ValidationError('自愿接替后的付款及补资凭证待核对') from exc
                    yield order
            return
        from .models import PointsZeroFollowupTermination
        zero = PointsZeroFollowupTermination.objects.filter(order=order, terminated_at__isnull=False).first()
        if zero and isinstance(zero.funding_snapshot, dict) and zero.funding_snapshot.get('kind') == 'TRANSFER_ZERO_V1':
            from .models import (PointsTransferZeroSettlementRevision, OrderSegmentPersonSettlement,
                                 OrderSegmentAddonSettlement)
            from .points_transfer_zero_followup import verify_record_locked
            from .points_transfer_zero_settlement import verify_revision
            payment = OrderAddonPayment.objects.select_related('intent').filter(pk=payment_id, intent__order=order).first()
            manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
            revision = (PointsTransferZeroSettlementRevision.objects.select_for_update().filter(
                termination=zero, manifest=manifest).first() if manifest else None)
            addons = zero.funding_snapshot.get('addons')
            frozen = addons.get('payments') if isinstance(addons, dict) else None
            if not isinstance(frozen, list) or any(not isinstance(item, dict) for item in frozen):
                raise ValidationError('零退款终止加购凭证格式异常')
            target = [item for item in frozen if isinstance(item, dict) and item.get('payment_id') == payment_id]
            post_refunds = list(order.addon_refunds.filter(created_at__gt=zero.terminated_at).order_by('pk'))
            handovers = list(payment.handovers.order_by('version', 'pk')) if payment else []
            accepted_rows = [row for row in handovers if row.status == 'ACCEPTED']
            accepted_handover = accepted_rows[-1] if accepted_rows else None
            from .models import OrderAddonHandover
            order_handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order)
                .select_related('payment__intent').order_by('accepted_at', 'pk'))
            independent_handovers = bool(order_handovers
                and len(order_handovers) <= len(frozen)
                and all(item.status == 'ACCEPTED' and item.accepted_at and not item.previous_id
                        and item.accepted_at < zero.prior_posting.created_at for item in order_handovers)
                and {item.payment_id for item in order_handovers}.issubset(
                    {row.get('payment_id') for row in frozen})
                and len({item.payment_id for item in order_handovers}) == len(order_handovers))
            invalidated_pre_main_history = False
            if (order_handovers and any(item.status == 'SUPERSEDED' for item in order_handovers)
                    and all(item.created_at < zero.prior_posting.created_at for item in order_handovers)):
                from .models import OrderOriginalShareTransfer
                from .points_transfer_refund_access import first_handover_refund
                transfers = list(OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).order_by('pk'))
                invalidated_pre_main_history = first_handover_refund(order, transfers)
            rehandover_history = False
            if order_handovers and any(item.created_at > zero.prior_posting.created_at for item in order_handovers):
                from .points_transfer_refund_access import post_refund_handover_history
                chains = {row.get('payment_id'): [] for row in frozen}
                for item in order_handovers:
                    if item.payment_id in chains and item.status == 'ACCEPTED':
                        chains[item.payment_id].append(item)
                from .models import PointsPostRefundTransferRevision
                liability_versions = list(PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'))
                untouched = {payment_id for payment_id, chain in chains.items() if not chain}
                rehandover_history = bool(len(chains) == len(frozen)
                    and {item.payment_id for item in order_handovers}.issubset(set(chains))
                    and all(item.status in ('ACCEPTED', 'REJECTED', 'SUPERSEDED') for item in order_handovers)
                    and any(chain and chain[-1].created_at > zero.prior_posting.created_at
                            for chain in chains.values())
                    and all(isinstance(version.snapshot, dict)
                        and isinstance(version.snapshot.get('pending_handover'), list)
                        and not any(isinstance(row, list) and row and row[0] in untouched
                                    for row in version.snapshot['pending_handover'])
                        for version in liability_versions)
                    and all(not chain or (all(item.status == 'ACCEPTED' and item.accepted_at
                        and item.accepted_at < zero.created_at for item in chain)
                        and not chain[0].previous_id
                        and (chain[0].accepted_at < zero.prior_posting.created_at
                             or zero.prior_posting.created_at < chain[0].created_at)
                        and all(current.previous_id == previous.pk
                            and previous.accepted_at < current.created_at
                            and zero.prior_posting.created_at < current.created_at
                            for previous, current in zip(chain, chain[1:])))
                        for chain in chains.values()) and post_refund_handover_history(order))
            if (not payment or order.settled_at or order.refund_records.count() != 1
                    or not frozen or len(target) != 1
                    or {row.get('payment_id') for row in frozen} != set(
                        OrderAddonPayment.objects.filter(intent__order=order,
                            buyer_transaction__created_at__lt=zero.terminated_at).values_list('pk', flat=True))
                    or len({row.get('payment_id') for row in frozen}) != len(frozen)
                    or (order_handovers and not independent_handovers and not rehandover_history
                        and not invalidated_pre_main_history
                        and (len(frozen) != 1 or not accepted_handover
                             or not accepted_handover.accepted_at
                             or accepted_handover.accepted_at >= zero.prior_posting.created_at))
                    or (accepted_handover and payment.refund_records.filter(
                        created_at=accepted_handover.accepted_at).exists())
                    or (len(frozen) > 1 and PointsPostRefundTransferRevision.objects.filter(order=order).exists()
                        and not rehandover_history)
                    or not revision or manifest.finalized_at or not zero.funding_snapshot.get('paid_snapshot')
                    or not isinstance(target[0].get('parts'), list)
                    or any(not isinstance(part, dict) for part in target[0].get('parts', []))
                    or (not accepted_handover and any(part.get('provider_id') != zero.assignment.provider_account_id
                        for part in target[0].get('parts', [])))
                    or (accepted_handover and (target[0].get('handover_id') != accepted_handover.pk
                        or not any(part.get('provider_id') == zero.assignment.provider_account_id
                                   and part.get('basis', 0) > 0 for part in target[0].get('parts', []))))
                    or not target[0].get('parts')
                    or any(item.payment_id not in {row.get('payment_id') for row in frozen} for item in post_refunds)
                    or OrderSegmentPersonSettlement.objects.filter(manifest=manifest,
                        account_id=zero.assignment.provider_account_id).exists()
                    or OrderSegmentAddonSettlement.objects.filter(manifest=manifest,
                        segment__provider_account_id=zero.assignment.provider_account_id).exists()):
                raise ValidationError('转单零退款后的加购退款范围尚未接通')
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                verify_revision(revision)
                verify_record_locked(zero)
                from .addon_payment import verify_receipt
                verify_receipt(payment)
                if order_handovers:
                    from .addon_handover import verify_handover
                    from .addon_funding_release import verify_run
                    from .models import OrderAddonFundingReleaseRun
                    handover_witness = OrderPointsFundingAllocation.objects.filter(
                        redemption__order=order).order_by('pk').first()
                    if invalidated_pre_main_history:
                        from .models import PointsHandoverRefundInvalidation
                        from .points_handover_refund_invalidation import verify_current_read
                        from django.core.exceptions import ValidationError as DjangoValidationError
                        if not handover_witness:
                            raise ValidationError('接替失效历史缺少到账见证')
                        for prior in (item for item in order_handovers if item.status == 'SUPERSEDED'):
                            event = PointsHandoverRefundInvalidation.objects.filter(handover=prior).first()
                            if not event:
                                raise ValidationError('接替失效记录缺少退款来源')
                            try:
                                verify_current_read(event, receipt_id=handover_witness.receipt_id)
                            except DjangoValidationError as exc:
                                raise ValidationError('接替失效退款凭证异常') from exc
                    for event in (item for item in order_handovers if item.status == 'ACCEPTED'):
                        verify_handover(event.payment, event, as_of=zero.created_at,
                            receipt_id=handover_witness.receipt_id if handover_witness else None)
                        handover_run = OrderAddonFundingReleaseRun.objects.filter(handover=event).first()
                        if not handover_run:
                            raise ValidationError('加购接替的资金释放凭证缺失')
                        verify_run(handover_run)
                if (order.addon_refunds.filter(created_at=zero.prior_posting.created_at).exists()
                        or order.addon_refunds.filter(created_at=zero.terminated_at).exists()):
                    raise ValidationError('加购退款与主单退款或零退款终止时间无法区分')
                prior_refunds = list(order.addon_refunds.filter(created_at__lt=zero.terminated_at)
                                     .select_related('payment__intent', 'proposal').order_by('created_at', 'pk'))
                if prior_refunds:
                    from .models import OrderAddonFundingReleaseRun, OrderOriginalShareTransfer
                    from .points_addon_receipt_usage import facts as refund_facts
                    from .addon_funding_release import verify_run
                    from django.core.exceptions import ValidationError as DjangoValidationError
                    witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                    first_transfer = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
                    if (not witness or not first_transfer
                            or any(item.created_at == first_transfer.created_at for item in prior_refunds)
                            or len({item.created_at for item in prior_refunds}) != len(prior_refunds)):
                        raise ValidationError('主单退款前的加购到账来源尚未接通')
                    for prior in prior_refunds:
                        run = OrderAddonFundingReleaseRun.objects.filter(refund=prior).first()
                        if not run:
                            raise ValidationError('主单退款前的加购释放凭证缺失')
                        try:
                            refund_facts(prior, read_receipt_id=witness.receipt_id)
                            verify_run(run)
                        except DjangoValidationError as exc:
                            raise ValidationError('主单退款前的加购资金来源异常') from exc
                from .points_transfer_addon_events import later_runs
                later_runs(order, zero.prior_posting)
                yield order
            return
        payment = OrderAddonPayment.objects.filter(pk=payment_id, intent__order=order).first()
        versions = list(PointsPostRefundTransferRevision.objects.filter(order=order).select_related('transfer').order_by('pk'))
        if order.refund_records.count() == 1 and payment and versions:
            pending = list(payment.handovers.select_for_update().filter(status='PENDING', kind='STANDARD')
                           .select_related('source_segment', 'target_segment'))
            if len(pending) == 1:
                from .points_transfer_addon_liability import first_refund_preflight, frozen_transfer_for_handover
                version = frozen_transfer_for_handover(order, payment.pk, pending[0].source_segment_id,
                                                       pending[0].target_segment_id, pending[0].created_at)
                if not version:
                    raise ValidationError('加购退款缺少原转单待交责任')
                with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    with first_refund_preflight(order, payment, pending[0], version):
                        yield order
                return
        if order.refund_records.count() == 2 and payment and not order.settled_at:
            second = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)\
                .order_by('-points_refund__refund_id').first()
            pending = list(payment.handovers.filter(status='PENDING', kind='STANDARD').select_related('target_segment'))
            from .models import OrderPointsTransferFollowupFundingRevision
            followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=second).first() if second else None
            snapshot = followup.pending_handover_snapshot if followup else None
            before_second = bool(len(pending) == 1 and isinstance(snapshot, list) and any(
                isinstance(item, dict) and item.get('handover_id') == pending[0].pk
                and item.get('payment_id') == payment.pk for item in snapshot))
            reoffered = bool(len(pending) == 1 and pending[0].created_at > second.created_at
                and isinstance(snapshot, list) and any(isinstance(item, dict)
                    and item.get('payment_id') == payment.pk for item in snapshot)) if second else False
            if (second and len(pending) == 1 and (before_second
                    or reoffered or payment.buyer_transaction.created_at > second.created_at)):
                from .points_followup_handover_invalidation import may_refund_pending_target
                from .points_transfer_followup_handover_scope import source_locked
                from .points_transfer_followup_addon_version import preflight_locked
                if not may_refund_pending_target(order, payment):
                    raise ValidationError('待确认接替的当前退款范围待核对')
                source_locked(order, payment, pending[0].target_segment.provider_account_id,
                              pending_handover_id=pending[0].pk)
                with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    with preflight_locked(order, payment_id=payment.pk):
                        yield order
                return
        if (order.refund_records.count() == 2 and payment and not order.settled_at
                and not payment.handovers.filter(status='PENDING').exists()):
            second = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)\
                .order_by('-points_refund__refund_id').first()
            if second and not payment.handovers.filter(status='ACCEPTED').exists():
                from .models import OrderPointsTransferFollowupFundingRevision, OrderProviderCompletionFact
                followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=second).first()
                snapshot = followup.pending_handover_snapshot if followup else None
                frozen = [item for item in snapshot if isinstance(item, dict) and item.get('payment_id') == payment.pk]\
                    if isinstance(snapshot, list) else []
                if len(frozen) == 1:
                    from django.utils import timezone
                    from .addon_handover import current_responsibilities
                    from .points_followup_handover_invalidation import verify as verify_invalidation
                    from .points_transfer_addon_liability import effective_at
                    from .points_transfer_followup_addon_version import preflight_locked
                    from .points_transfer_followup_pending import verify_snapshot
                    original = payment.handovers.select_related('target_segment').filter(
                        pk=frozen[0].get('handover_id'), status='SUPERSEDED').first()
                    invalidation = PointsHandoverRefundInvalidation.objects.filter(handover=original,
                        refund__order=order, refund__payment=payment, refund__created_at__gt=second.created_at
                        ).select_related('handover__payment__intent', 'refund__payment__intent').first() if original else None
                    version = next((row for row in versions if row.pk == frozen[0].get('transfer_revision_id')), None)
                    witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                    receipt_id = witness.receipt_id if witness else None
                    if original and invalidation and version:
                        verify_snapshot(second, snapshot, receipt_id=receipt_id)
                        verify_invalidation(invalidation, receipt_id=receipt_id)
                        liability = effective_at(version, payment, timezone.now(), witness_receipt_id=receipt_id)
                        _, shares = current_responsibilities(payment, lock=True)
                        active = [part for part in shares if part['is_current'] and part['basis'] > 0]
                        target = original.target_segment
                        if (len(active) != 1 or liability != [payment.pk, original.source_segment_id, active[0]['basis']]
                                or active[0]['segment_id'] != original.source_segment_id
                                or not target.started_at or target.released_at
                                or not order.providers.filter(provider_account_id=target.provider_account_id,
                                    released_at__isnull=True, settled_at__isnull=True).exists()
                                or OrderProviderCompletionFact.objects.filter(order=order, segment=target).exists()):
                            raise ValidationError('原待接替加购的剩余责任或目标已变化')
                        with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                                   account_id=order.customer_account_id, service_id=order.service_id):
                            with preflight_locked(order, payment_id=payment.pk):
                                yield order
                        return
            latest = payment.handovers.filter(status='ACCEPTED').order_by('-version', '-pk').first()
            if (second and latest and latest.kind != 'VOLUNTARY'
                    and payment.buyer_transaction.created_at > second.created_at
                    and latest.accepted_at > second.created_at):
                from .models import OrderAddonFundingReleaseRun
                from .points_transfer_followup_handover_scope import verify_event
                from .points_transfer_followup_addon_version import preflight_locked, _verify_prior_run
                run = OrderAddonFundingReleaseRun.objects.filter(handover=latest,
                    points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first()
                witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                receipt_id = witness.receipt_id if witness else None
                if not run or run.points_followup_snapshot.get('addon_handover_id') != latest.pk:
                    raise ValidationError('加购接替资金版本缺失')
                with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    verify_event(payment, latest, receipt_id=receipt_id)
                    _verify_prior_run(run, receipt_id=receipt_id)
                    with preflight_locked(order, payment_id=payment.pk):
                        yield order
                return
        if (order.refund_records.count() == 2 and payment and not payment.handovers.exists()
                and not payment.refund_records.exists() and not order.settled_at
                ):
            second = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)\
                .order_by('-points_refund__refund_id').first()
            if second and payment.buyer_transaction.created_at != second.created_at:
                from .models import OrderAddonFundingReleaseRun
                from .points_transfer_followup_addon_version import preflight_locked
                if payment.buyer_transaction.created_at > second.created_at:
                    run = OrderAddonFundingReleaseRun.objects.filter(payment=payment,
                        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first()
                    if not run or run.points_followup_snapshot.get('addon_payment_id') != payment.pk:
                        raise ValidationError('追加退款后的加购付款资金版本缺失')
                else:
                    from .addon_payment import verify_receipt
                    verify_receipt(payment)
                with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    with preflight_locked(order, payment_id=payment.pk):
                        yield order
                return
        if (postmain_payment and order.refund_records.count() == 1 and payment
                and not payment.handovers.exists() and not payment.refund_records.exists()
                and not order.settled_at and not order.providers.filter(settled_at__isnull=False).exists()
                and not OrderSegmentSettlementManifest.objects.filter(order=order).exists()):
            from .models import OrderAddonFundingReleaseRun
            from .addon_funding_release import verify_run
            from .points_transfer_refunded_funding import plan_refunded_transfer
            run = OrderAddonFundingReleaseRun.objects.filter(payment=payment).first()
            if not run or not isinstance(run.points_payment_snapshot, dict) or not run.refunded_main_snapshot:
                raise ValidationError('主单退款后的加购付款资金版本缺失')
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                verify_run(run)
                plan_refunded_transfer(order.pk)
                yield order
            return
        if not versions:
            from .models import OrderOriginalShareTransfer
            from .points_transfer_refund_access import first_handover_refund
            transfers = list(OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).order_by('pk'))
            first = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
            if (order.refund_records.count() != 1 or not payment or not first or order.settled_at
                    or OrderSegmentSettlementManifest.objects.filter(order=order).exists()
                    or not first_handover_refund(order, transfers)):
                raise ValidationError('主单退款后的加购售后范围尚未开放')
            from .points_transfer_refund_continuation import verify_locked as verify_posting
            from .points_transfer_refund_release import verify_release_locked
            from .points_transfer_followup_scope import addon_snapshot_from_posting
            verify_posting(order)
            evidence = getattr(first, 'funding_release_evidence', None)
            if not evidence:
                raise ValidationError('主单退款补资凭证缺失')
            verify_release_locked(evidence)
            history = addon_snapshot_from_posting(order, first)
            if payment.pk not in {item['payment_id'] for item in history}:
                raise ValidationError('加购付款不属于主单退款时的资金范围')
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order
            return
        from .points_transfer_refund_access import single_post_refund_handover, post_refund_handover_history
        if (payment and not payment.handovers.exclude(status__in=('REJECTED', 'PENDING', 'SUPERSEDED')).exists()
                and order.refund_records.count() == 2
                and post_refund_handover_history(order, pending_payment_id=pending_payment)):
            if (order.settled_at or order.providers.filter(settled_at__isnull=False).exists()
                    or OrderSegmentSettlementManifest.objects.filter(order=order).exists()):
                raise ValidationError('未接替加购的追加退款范围待核对')
            from .points_post_refund_transfer import verify_locked, unresolved_pending_locked
            from .addon_payment import verify_receipt
            from .points_transfer_followup_addon_version import preflight_locked
            from .points_transfer_followup_handover_scope import _verify_rejected
            postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                            .order_by('created_at', 'pk'))
            attempts = list(payment.handovers.order_by('version'))
            if (len(postings) != 2 or [row.version for row in attempts] != list(range(1, len(attempts) + 1))
                    or any(row.created_at == postings[-1].created_at or row.previous_id for row in attempts)
                    or sum(row.status == 'PENDING' for row in attempts) > 1
                    or any(row.status == 'PENDING' and index != len(attempts) - 1
                           for index, row in enumerate(attempts))):
                raise ValidationError('未接替付款的拒绝方案范围待核对')
            for index, attempt in enumerate(attempts):
                boundary = attempts[index + 1].created_at if index + 1 < len(attempts) else None
                if (attempt.created_at < postings[-1].created_at
                        and (boundary is None or postings[-1].created_at < boundary)):
                    boundary = postings[-1].created_at
                if attempt.status == 'PENDING' and attempt.created_at <= postings[-1].created_at:
                    raise ValidationError('待确认接替不能跨第二次主单退款')
                if attempt.status == 'SUPERSEDED':
                    from .models import PointsHandoverRefundInvalidation, OrderAddonFundingReleaseRun
                    from .points_followup_handover_invalidation import verify as verify_invalidation
                    record = PointsHandoverRefundInvalidation.objects.filter(handover=attempt).select_related(
                        'handover__payment__intent', 'refund__payment__intent').first()
                    witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                    if not record or attempt.created_at <= postings[-1].created_at:
                        raise ValidationError('未接替付款的失效凭证缺失')
                    verify_invalidation(record, receipt_id=witness.receipt_id if witness else None)
                    run = OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first()
                    if boundary and (not run or run.created_at >= boundary):
                        raise ValidationError('未接替付款的重发早于退款释放')
                else:
                    _verify_rejected(payment, attempt, before=boundary, pending=attempt.status == 'PENDING')
            for version in versions:
                verify_locked(version)
                if any(row[0] == payment.pk for row in unresolved_pending_locked(version)):
                    raise ValidationError('该加购仍有未完成接替责任')
                if any(row[0] == payment.pk for row in version.snapshot.get('pending_handover', [])):
                    raise ValidationError('该加购已被冻结为转单接替责任')
            verify_receipt(payment)
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                with preflight_locked(order, payment_id=payment.pk):
                    yield order
            return
        if (payment and payment.handovers.filter(status='ACCEPTED').exists()
                and (single_post_refund_handover(order, pending_payment_id=pending_payment)
                     or post_refund_handover_history(order, pending_payment_id=pending_payment))):
            if (not versions or len(versions) > 4 or versions[0].prior_id is not None or order.settled_at
                    or order.providers.filter(settled_at__isnull=False).exists()
                    or OrderSegmentSettlementManifest.objects.filter(order=order).exists()):
                raise ValidationError('退款后转单接替的加购售后范围待核对')
            responsible_versions = []
            for item in versions:
                pending = item.snapshot.get('pending_handover', []) if isinstance(item.snapshot, dict) else None
                if (not isinstance(pending, list) or any(not isinstance(row, list) or len(row) != 3
                        or any(type(value) is not int or value <= 0 for value in row) for row in pending)
                        or len({row[0] for row in pending}) != len(pending)):
                    raise ValidationError('退款后转单的加购负债格式异常')
                if any(row[0] == payment.pk for row in pending):
                    responsible_versions.append(item)
            if not responsible_versions:
                # A payment which was never frozen by a service transfer may
                # itself be handed over after refund two. Its own later refund
                # still needs the complete accepted responsibility and money
                # chain; it must not be mistaken for a transfer liability.
                postings = list(PointsNegotiationPosting.objects.filter(
                    points_refund__redemption__order=order).order_by('created_at', 'pk'))
                attempts = list(payment.handovers.order_by('version'))
                accepted = [row for row in attempts if row.status == 'ACCEPTED']
                if (order.refund_records.count() != 2 or len(postings) != 2 or len(accepted) != 1
                        or OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING').exists()
                        or any(row.status not in ('ACCEPTED', 'REJECTED') for row in attempts)
                        or not accepted[0].created_at or accepted[0].created_at <= postings[1].created_at
                        or not accepted[0].accepted_at or accepted[0].accepted_at <= accepted[0].created_at
                        or attempts[-1].pk != accepted[0].pk):
                    raise ValidationError('目标加购不属于已核验的转单负债或后续接替')
                from .points_post_refund_transfer import verify_locked
                from .points_transfer_followup_handover_scope import verify_event
                from .points_transfer_followup_addon_version import preflight_locked, _verify_prior_run
                from .models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation
                with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    for item in versions:
                        verify_locked(item)
                    witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                    receipt_id = witness.receipt_id if witness else None
                    verify_event(payment, accepted[0], receipt_id=receipt_id)
                    run = OrderAddonFundingReleaseRun.objects.filter(handover=accepted[0],
                        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first()
                    if not run or run.created_at < accepted[0].accepted_at:
                        raise ValidationError('后续接替的实际资金版本缺失')
                    _verify_prior_run(run, receipt_id=receipt_id)
                    with preflight_locked(order, payment_id=payment.pk):
                        yield order
                return
            version = responsible_versions[-1]
            liability = version.snapshot['pending_handover']
            handover = payment.handovers.filter(status='ACCEPTED',
                source_segment_id=version.transfer.previous_segment_id,
                target_segment_id=version.transfer.next_segment_id).order_by('-version', '-pk').first()
            if (not isinstance(liability, list) or not handover or not handover.accepted_at
                    or [payment.pk, handover.source_segment_id, handover.source_basis_before] not in liability):
                raise ValidationError('退款后转单的接替负债待核对')
            from .models import OrderAddonFundingReleaseRun
            run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
            if not run:
                raise ValidationError('退款后加购接替资金版本缺失')
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                from .points_post_refund_transfer import verify_locked, unresolved_pending_locked
                from .addon_funding_release import verify_run
                from .points_transfer_addon_events import later_runs
                for item in versions:
                    verify_locked(item)
                if any(row[0] == payment.pk for row in unresolved_pending_locked(version)):
                    raise ValidationError('退款后转单的该加购尚未完成接替')
                verify_run(run)
                first = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
                if not first:
                    raise ValidationError('退款后加购接替的主单凭证缺失')
                later_runs(order, first)
                if order.refund_records.count() == 2:
                    from .points_transfer_followup_addon_version import preflight_locked
                    with preflight_locked(order, payment_id=payment.pk):
                        yield order
                else:
                    yield order
            return
        if (order.refund_records.count() != 1 or not payment
                or OrderAddonPayment.objects.filter(intent__order=order).count() != 1
                or payment.handovers.exists() or order.settled_at
                or OrderSegmentSettlementManifest.objects.filter(order=order).exists()
                or len(versions) != 1 or versions[0].prior_id is not None):
            raise ValidationError('退款后转单加购售后范围尚未开放')
        first = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
        evidence = getattr(first, 'funding_release_evidence', None) if first else None
        history = versions[0].snapshot.get('addon_history') if isinstance(versions[0].snapshot, dict) else None
        if (not first or not evidence or not isinstance(history, list) or len(history) != 1
                or history[0].get('payment_id') != payment.pk or history[0].get('refund_ids')):
            raise ValidationError('退款后转单加购原付款或到账事实待核对')
        if payment.refund_records.exists():
            from .points_transfer_followup_scope import addon_snapshot
            if (payment.refund_records.filter(created_at__lte=first.created_at).exists()
                    or addon_snapshot(order, first) is None):
                raise ValidationError('主单退款后的加购退款版本待核对')
        from .points_post_refund_transfer import verify_locked
        verify_locked(versions[0])
        with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            yield order


@contextmanager
def _bind(*, phase, kind, order_id=None, account_id=None, service_id=None):
    if not configured():
        raise ValidationError('积分正式资金能力尚未开放')
    store = current_store()
    state = (transaction.get_connection().alias, store.slug, order_id, account_id, service_id, phase, kind)
    previous = _scope.get()
    if previous and (previous[:2] != state[:2]
            or any(old is not None and new is not None and old != new
                   for old, new in zip(previous[2:5], state[2:5]))):
        raise ValidationError('积分业务上下文不能跨门店、订单或账号')
    token = _scope.set(state)
    try:
        yield
    finally:
        _scope.reset(token)


def bind_checkout_order(order, account, service):
    """Pin the newly created order to this checkout until its scope exits."""
    if (not order or not order.pk or order.customer_account_id != getattr(account, 'pk', None)
            or order.service_id != getattr(service, 'pk', None)
            or not permits(account=account, service=service, phase='checkout', kind='service')):
        raise ValidationError('积分付款订单与本次上下文不一致')
    state = _scope.get()
    if state[2] is not None and state[2] != order.pk:
        raise ValidationError('本次积分付款已绑定其他订单')
    _scope.set((state[0], state[1], order.pk, state[3], state[4], state[5], state[6]))


@contextmanager
def checkout(*, account, service):
    """Bind a server-selected buyer and service; never authorize a purchase."""
    if (not configured() or getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False) is not True
            or not account or not service):
        raise ValidationError('积分付款正式范围尚未开放')
    from club_accounts.models import ClubAccount
    from .models import ServiceItem
    if account._state.db != transaction.get_connection().alias or service._state.db != transaction.get_connection().alias:
        raise ValidationError('积分付款账号或服务不属于当前门店')
    selected = ServiceItem.objects.filter(pk=service.pk, is_active=True).select_related('service_category').first()
    if not selected:
        raise ValidationError('积分付款账号或服务不可用')
    from .points_service_payment import _eligible, _enabled
    from engagement.points_usage import current_usage, FIELDS
    if not _enabled():
        raise ValidationError('本店积分付款未启用')
    _eligible(selected)
    try:
        issuance, switches = current_usage()
    except ValueError as exc:
        raise ValidationError('本店积分使用配置待核对') from exc
    if not issuance or not all(switches[field] for field in FIELDS):
        raise ValidationError('本店积分使用暂时暂停')
    buyer = ClubAccount.objects.filter(pk=account.pk,
        account_type=ClubAccount.AccountType.BOSS, is_active=True, can_login=True).first()
    if not buyer:
        raise ValidationError('积分付款账号或服务不可用')
    with _bind(phase='checkout', kind='service', account_id=buyer.pk, service_id=selected.pk):
        yield buyer, selected


@contextmanager
def service_order(order_id, *, phase, addon=False, combined=False, extra_receipt_id=None,
                  refund_payment_id=None):
    """Bind one persisted, untransferred service order to a single phase."""
    if not configured() or phase not in ('assignment', 'payment', 'handover', 'funding', 'report', 'refund', 'settlement') or type(order_id) is not int or order_id <= 0:
        raise ValidationError('积分订单业务阶段尚未开放')
    from django.db.models import Q
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_guard import isolated_allocation_state_locked
    from .financial_lock import lock_order_money
    from .models import (Order, OrderOriginalShareTransfer, OrderPointsFundingAllocation,
                         OrderPointsRedemption, OrderPrestartHandoff)
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('积分订单不存在')
    lock_order_money()
    if phase in ('payment', 'handover') or (phase == 'refund' and refund_payment_id is not None):
        from .models import PointsFirstRefundPaidPersonEvidence
        if PointsFirstRefundPaidPersonEvidence.objects.filter(
                posting__points_refund__redemption__order=order).exists():
            raise ValidationError('已有实付陪玩的首退后加购变更尚未接通')
    transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                     .select_related('previous_segment').order_by('pk'))
    prestarts = OrderPrestartHandoff.objects.filter(order=order).count()
    service_accounts = None
    if prestarts and not transfers:
        from .points_service_participation import service_snapshots
        service_accounts = [row.segment.provider_account_id for row in service_snapshots(order)]
        provider_count_valid = (len(service_accounts) == order.required_provider_count
                                and len(set(service_accounts)) == len(service_accounts))
    else:
        provider_count_valid = order.providers.count() in (1, 2)
    transfer_addon = bool(addon and phase in ('payment', 'refund') and transfers and not order.refund_records.exists())
    pending_other_refund = False
    invalidated_handover_history = False
    if transfer_addon:
        counts = {}
        for event in transfers:
            counts[event.previous_segment.slot] = counts.get(event.previous_segment.slot, 0) + 1
        if any(count > 2 for count in counts.values()):
            raise ValidationError('同一席位转单次数超过上限')
        if phase == 'refund' and refund_payment_id is not None:
            from .points_transfer_pending_handover_events import permits_other_refund
            pending_other_refund = permits_other_refund(order, transfers, refund_payment_id)
            if pending_other_refund:
                from .models import PointsHandoverRefundInvalidation
                invalidated_handover_history = PointsHandoverRefundInvalidation.objects.filter(
                    handover__payment__intent__order=order).exists()
            else:
                from .points_handover_refund_invalidation import provisional_structure
                invalidated_handover_history = provisional_structure(order)
    if phase == 'payment' and not addon:
        raise ValidationError('积分订单付款阶段不适用')
    team_handover = bool(addon and not transfers and not prestarts and order.required_provider_count == 2
                         and order.refund_records.count() <= 1 and not order.settled_at
                         and not order.providers.filter(settled_at__isnull=False).exists()
                         and phase == 'handover' and order.status == Order.Status.IN_SERVICE)
    team_pending_refund = False
    if addon and phase == 'refund' and not transfers and refund_payment_id is not None:
        from .points_handover_refund_invalidation import permits_other_payment_locked
        team_pending_refund = permits_other_payment_locked(order, refund_payment_id, allow_same_payment=True)
    from .models import OrderAddonHandover, PointsHandoverRefundInvalidation
    superseded_team = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='SUPERSEDED'))
    first_main_refund = order.refund_records.order_by('created_at', 'pk').first()
    team_invalidations = bool(not transfers and not prestarts and superseded_team and all(
        PointsHandoverRefundInvalidation.objects.filter(handover=item, refund__order=order).filter(
            (Q(refund__created_at__lt=first_main_refund.created_at) |
             Q(refund__created_at__gt=first_main_refund.created_at, snapshot__main_refund_id=first_main_refund.pk))
            if first_main_refund else Q()).exists()
        for item in superseded_team))
    if phase == 'handover' and not team_handover:
        raise ValidationError('同单双陪加购接替资金范围尚未开放')
    if team_handover:
        from .models import OrderAddonHandover, OrderAddonRefund, OrderAddonFundingReleaseRun
        existing = list(OrderAddonHandover.objects.filter(payment__intent__order=order).order_by('pk'))
        eligible_payments = {item.payment_id for item in existing if item.status == 'ACCEPTED'}
        from .points_team_other_addon_refund import applies as team_other_applies
        unmatched = OrderAddonRefund.objects.filter(order=order).exclude(payment_id__in=eligible_payments)
        if (any(not team_other_applies(item) for item in unmatched.select_related(
                'payment__intent', 'payment__buyer_transaction', 'order'))
                or any(item.status not in (('PENDING', 'ACCEPTED', 'REJECTED', 'SUPERSEDED')
                                           if team_invalidations else ('PENDING', 'ACCEPTED', 'REJECTED'))
                       for item in existing)
                or sum(item.status == 'PENDING' for item in existing) > 1):
            raise ValidationError('同单加购接替或退款范围待核对')
        from .points_team_addon_handover_facts import verify_rejected_attempts, has_ordered_funding_chain
        accepted_runs = list(OrderAddonFundingReleaseRun.objects.filter(
            handover__in=[item for item in existing if item.status == 'ACCEPTED'],
            points_handover_snapshot__kind='SAME_TEAM_V1').select_related('handover')
            .order_by('handover__accepted_at', 'pk'))
        if (len(accepted_runs) != sum(item.status == 'ACCEPTED' for item in existing)
                or not has_ordered_funding_chain(accepted_runs)):
            raise ValidationError('同单接替前次资金事件待核对')
        for payment_id in {item.payment_id for item in existing}:
            payment = next(item.payment for item in existing if item.payment_id == payment_id)
            verify_rejected_attempts(payment)
    if addon:
        from .models import OrderAddonPayment, OrderAddonHandover, OrderAddonFundingReleaseRun, OrderAddonRefund
        payments = OrderAddonPayment.objects.filter(intent__order=order)
        handovers = OrderAddonHandover.objects.filter(payment__intent__order=order)
        accepted_teams = list(handovers.filter(status='ACCEPTED').order_by('accepted_at', 'pk'))
        accepted_team = accepted_teams[0] if accepted_teams else None
        team_payment_ids = {item.payment_id for item in accepted_teams}
        team_refunds = OrderAddonRefund.objects.filter(order=order)
        main_refunds = list(order.refund_records.order_by('created_at', 'pk'))
        main_refund = main_refunds[0] if main_refunds else None
        paid_team_followup = False
        # A first main refund may continue an in-time B case after A's
        # actual original settlement. The refund preview and release writer
        # independently verify A's payout and this handover's funding event.
        from .models import OrderPointsSettlement, PointsNegotiationPosting
        initial_settlement = OrderPointsSettlement.objects.filter(order=order).first()
        initial_people = list(initial_settlement.persons.select_related('segment')[:2]) if initial_settlement else []
        paid_posting = (PointsNegotiationPosting.objects.filter(points_refund__refund=main_refund)
                        .select_related('proposal__case').first() if main_refund else None)
        paid_team_refunds = list(team_refunds.select_related('payment__intent').order_by('created_at', 'pk'))
        paid_refund_totals = {}
        for row in paid_team_refunds:
            paid_refund_totals[row.payment_id] = paid_refund_totals.get(row.payment_id, 0) + row.amount
        paid_team_refunds_valid = (not paid_team_refunds or (
            accepted_teams and len(initial_people) == 1
            and all(item.accepted_at for item in accepted_teams) and initial_people[0].settled_at
            and all(row.amount > 0 and paid_refund_totals[row.payment_id] <= row.payment.intent.amount
                and row.created_at < initial_people[0].settled_at
                for row in paid_team_refunds)))
        paid_team_first = bool(refund_payment_id is None and len(initial_people) == 1 and accepted_teams
            and not transfers and not prestarts and len(main_refunds) <= 1
            and initial_people[0].settled_at and paid_team_refunds_valid
            and all(item.accepted_at and item.accepted_at < initial_people[0].settled_at for item in accepted_teams)
            and not handovers.exclude(status__in=(('ACCEPTED', 'REJECTED', 'SUPERSEDED')
                                                   if team_invalidations else ('ACCEPTED', 'REJECTED'))).exists()
            and (not main_refund or (paid_posting and initial_people[0].settled_at < main_refund.created_at
                and initial_people[0].segment.provider_account_id not in
                    (paid_posting.proposal.case.affected_provider_account_ids or [])
                and not paid_posting.shares.filter(
                    account_id=initial_people[0].segment.provider_account_id).exists())))
        if main_refund and accepted_teams and len(main_refunds) <= 2 and not transfers and not prestarts:
            from .models import PointsNegotiationPosting, OrderPointsRefundSettlement
            first_posting = PointsNegotiationPosting.objects.filter(points_refund__refund=main_refund).first()
            settlement = (OrderPointsRefundSettlement.objects.filter(posting=first_posting).first()
                          if first_posting else None)
            people = list(settlement.persons.select_related('segment')[:2]) if settlement else []
            shares = list(first_posting.shares.all()[:2]) if first_posting else []
            paid_team_followup = bool(len(people) == len(shares) == 1
                and people[0].segment.provider_account_id == shares[0].account_id
                and people[0].settled_at
                and all(item.accepted_at and item.accepted_at < people[0].settled_at for item in accepted_teams)
                and OrderAddonFundingReleaseRun.objects.filter(handover__in=accepted_teams,
                    points_handover_snapshot__kind='SAME_TEAM_V1',
                    created_at__lt=people[0].settled_at).count() == len(accepted_teams)
                and (len(main_refunds) == 1
                    or people[0].settled_at < main_refunds[1].created_at))
            if paid_team_followup:
                from .points_team_other_addon_refund import continuing_purchase
                postmain = payments.filter(buyer_transaction__created_at__gte=main_refund.created_at).select_related(
                    'intent__segment', 'buyer_transaction')
                paid_team_followup = all(item.buyer_transaction.created_at > main_refund.created_at
                    and item.intent.provider_id != shares[0].account_id and continuing_purchase(order, item)
                    for item in postmain)
            if paid_team_followup:
                from .points_team_other_addon_refund import applies as other_team_refund_applies
                for item in team_refunds.select_related('payment__intent', 'payment__buyer_transaction', 'order'):
                    if item.created_at < main_refund.created_at:
                        valid = any(h.payment_id == item.payment_id and h.accepted_at
                                    and h.accepted_at < item.created_at for h in accepted_teams) or (
                                        item.payment_id not in team_payment_ids and other_team_refund_applies(item))
                    else:
                        participants = list(item.proposal.participants.values_list('account_id', flat=True))
                        valid = (item.created_at > main_refund.created_at and bool(participants)
                                 and (item.created_at < people[0].settled_at
                                      or shares[0].account_id not in participants)
                                 and (item.payment_id in team_payment_ids or
                                      (len(participants) == 1 and item.payment.intent.provider_id == participants[0]
                                       and other_team_refund_applies(item))))
                    if not valid:
                        paid_team_followup = False
                        break
        team_post_main = bool(main_refund and len(main_refunds) <= 2
                              and phase in ('payment', 'funding', 'refund', 'report', 'settlement')
                              and accepted_team and accepted_team.accepted_at
                              and all(item.accepted_at and (
                                  item.accepted_at < main_refund.created_at or
                                  (item.accepted_at > main_refund.created_at and
                                   OrderAddonFundingReleaseRun.objects.filter(handover=item,
                                       refunded_main_snapshot__isnull=False,
                                       points_handover_snapshot__kind='SAME_TEAM_V1').exists()))
                                  for item in accepted_teams)
                              and not team_refunds.filter(created_at=main_refund.created_at).exists()
                              and not payments.filter(buyer_transaction__created_at=main_refund.created_at).exists())
        if team_post_main:
            from .models import OrderProvider
            for purchased in payments.filter(buyer_transaction__created_at__gt=main_refund.created_at).select_related(
                    'buyer_transaction', 'intent__segment'):
                segment, at = purchased.intent.segment, purchased.buyer_transaction.created_at
                if (not segment.started_at or segment.started_at > at
                        or (segment.released_at and segment.released_at <= at)
                        or segment.provider_account_id != purchased.intent.provider_id
                        or not OrderProvider.objects.filter(order=order,
                            provider_account_id=segment.provider_account_id).exists()):
                    team_post_main = False
                    break
        if team_post_main and len(main_refunds) == 2:
            second_at = main_refunds[1].created_at
            team_post_main = bool(main_refund.created_at < second_at
                and all(item.accepted_at < second_at for item in accepted_teams)
                and not team_refunds.filter(created_at=second_at).exists()
                and (paid_team_followup or not order.providers.filter(settled_at__lte=second_at).exists()))
        other_team_refunds = team_refunds.exclude(payment_id__in=team_payment_ids)
        other_team_refunds_allowed = not other_team_refunds.exists()
        if accepted_team and len(main_refunds) <= 2 and other_team_refunds.exists():
            from .points_team_other_addon_refund import applies as other_team_refund_applies
            other_team_refunds_allowed = all(other_team_refund_applies(item) for item in other_team_refunds
                                            .select_related('payment__intent', 'payment__buyer_transaction', 'order'))
        # A pre-main refund of a not-yet-reassigned payment can be used to
        # prepare its handover, but main-order refund waits for that handover.
        if combined and not main_refunds and other_team_refunds.exists() and not paid_team_first:
            other_team_refunds_allowed = False
        from .points_team_addon_handover_facts import has_ordered_funding_chain
        team_history_runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__in=accepted_teams,
            points_handover_snapshot__kind='SAME_TEAM_V1').select_related('handover')
            .order_by('handover__accepted_at', 'pk'))
        team_handover_history = bool(not transfers and not prestarts
            and (not order.refund_records.exists() or team_post_main)
            and phase in ('payment', 'funding', 'refund', 'report', 'settlement') and accepted_team
            and not handovers.exclude(status__in=(('ACCEPTED', 'REJECTED')
                + (('PENDING',) if team_pending_refund else ())
                + (('SUPERSEDED',) if team_invalidations else ()))).exists()
            and len(team_history_runs) == len(accepted_teams)
            and has_ordered_funding_chain(team_history_runs)
            and other_team_refunds_allowed
            and (phase != 'refund' or (not order.settled_at
                and (not order.providers.filter(settled_at__isnull=False).exists()
                     or paid_team_followup or paid_team_first)))
            )
        multiple_before_main_refund = not transfers and order.refund_records.count() <= 1
        from .points_transfer_refund_access import independent_refund_after_handovers, first_handover_refund
        independent_handover_refund = (phase == 'refund' and transfer_addon
                                      and (independent_refund_after_handovers(order, transfers)
                                           or first_handover_refund(order, transfers) or pending_other_refund
                                           or invalidated_handover_history))
        multiple_transfer_addon = transfer_addon and (not prestarts or independent_handover_refund)
        multiple_after_followup = False
        if not transfers and order.refund_records.count() == 2:
            from .models import OrderPointsFollowupFundingRevision
            revision = OrderPointsFollowupFundingRevision.objects.filter(
                posting__points_refund__redemption__order=order).first()
            history = revision.addon_snapshot.get('addon_history') if revision and isinstance(
                revision.addon_snapshot, dict) else None
            if revision:
                from .models import OrderAddonFundingReleaseRun
                latest = OrderAddonFundingReleaseRun.objects.filter(
                    points_followup_snapshot__followup_revision_id=revision.pk).order_by('-pk').first()
                if latest and isinstance(latest.points_followup_snapshot, dict):
                    history = latest.points_followup_snapshot.get('addon_history')
            if revision and revision.addon_snapshot is None and not payments.exists():
                history = []
            if isinstance(history, list):
                ids = [item.get('payment_id') for item in history if isinstance(item, dict)]
                multiple_after_followup = (len(ids) == len(history) == payments.count()
                                           and len(set(ids)) == len(ids)
                                           and set(ids) == set(payments.values_list('pk', flat=True)))
        has_addon = (order.addon_intents.exists() if phase == 'payment' else payments.exists())
        if ((not transfer_addon and not provider_count_valid) or not has_addon
                or (not transfers and order.refund_records.count() == 2 and not multiple_after_followup)
                or (not multiple_before_main_refund and not multiple_after_followup and not multiple_transfer_addon
                    and payments.count() > 1)
                or (payments.filter(handovers__isnull=False).exists() and not independent_handover_refund
                    and not team_handover and not team_handover_history)
                or (phase in ('payment', 'refund') and order.settled_at and not order.refund_records.exists())):
            raise ValidationError('订单不在积分加购资金范围')
        if prestarts:
            last_handoff = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
            if order.addon_intents.filter(created_at__lte=last_handoff.created_at, payment__isnull=False).exists():
                raise ValidationError('改派与加购发生顺序待核对')
    if (order.is_gift_snapshot or order.source_order_id or Order.objects.filter(source_order_id=order.pk).exists()
            or (phase != 'assignment' and not transfer_addon and not provider_count_valid)
            or (transfer_addon and order.providers.count() > 3 * order.required_provider_count)
            or (not addon and order.addon_intents.filter(payment__isnull=False).exists()) or (transfers and not transfer_addon)
            or (prestarts and transfers and not transfer_addon)
            or order.refund_records.count() > 2
            or (order.refund_records.count() == 2 and
                (len(service_accounts) if service_accounts is not None else order.providers.count()) != 2)):
        raise ValidationError('订单不在受控积分服务范围')
    if not transfers and (phase == 'refund' or (phase == 'settlement' and order.refund_records.exists())):
        from .points_reservation_refund_identity import verify_dual_reservation
        verify_dual_reservation(order)
    ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).values_list('receipt_id', flat=True))
    from .models import ReferralRefundFunding
    ids.update(ReferralRefundFunding.objects.filter(order=order).values_list('receipt_id', flat=True))
    if extra_receipt_id is not None:
        if phase != 'funding' or type(extra_receipt_id) is not int or extra_receipt_id <= 0:
            raise ValidationError('积分补资到账参数无效')
        ids.add(extra_receipt_id)
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(receipts) != len(ids):
        raise ValidationError('积分订单到账凭证缺失')
    from .points_addon_receipt_usage import reuse_history_locked
    try:
        for receipt in receipts:
            if receipt.usage_version is None:
                raise ValidationError('积分订单到账用途账本缺失')
            with reuse_history_locked(order, {receipt.pk}):
                isolated_allocation_state_locked(receipt)
    except DjangoValidationError as exc:
        raise ValidationError('积分订单到账用途凭证异常') from exc
    from club_accounts.models import ClubAccount
    buyer = ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id,
        account_type=ClubAccount.AccountType.BOSS, business_cycle=order.business_cycle).first()
    redemption = OrderPointsRedemption.objects.filter(order=order).first()
    from .points_service_payment import MARKER
    if (not buyer or not redemption or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': redemption.pk}):
        raise ValidationError('积分订单账号或付款凭证异常')
    from .points_service_refund import _plan
    _plan(order, None, None, history_only=True)
    if prestarts and not transfers and not order.refund_records.exists():
        from .points_funding_plan import plan_verified_order, PointsFundingPlanError
        try:
            plan_verified_order(order.pk, allow_finalized=bool(order.settled_at))
        except PointsFundingPlanError as exc:
            raise ValidationError('积分订单服务前改派及收益凭证待核对') from exc
    if phase != 'assignment':
        assignments = order.providers
        if prestarts and service_accounts is not None:
            assignments = assignments.filter(provider_account_id__in=service_accounts)
        elif prestarts and transfers:
            from .models import OrderParticipationSegment
            involved = OrderParticipationSegment.objects.filter(
                attribution__order=order,
            ).filter(Q(started_at__isnull=False) | Q(released_at__isnull=True)).values('provider_account_id')
            assignments = assignments.filter(provider_account_id__in=involved)
        members = list(assignments.order_by('provider_account_id').values_list('provider_account_id', 'provider_id'))
        # Refund and settlement acquire the same existing profiles and wallets
        # in account order, including an unaffected second participant.
        from users.models import EscortProfile
        from wallet.models import Wallet
        accounts = [row[0] for row in members]
        if not all(accounts) or len(set(accounts)) != len(accounts):
            raise ValidationError('积分履约账号待核对')
        profiles = list(EscortProfile.objects.select_for_update().filter(account_id__in=accounts)
                        .order_by('account_id', 'pk'))
        wallets = list(Wallet.objects.select_for_update().filter(
            Q(account_id__in=accounts) | Q(user_id__in=[row[1] for row in members]))
            .order_by('account_id', 'pk'))
        expected = dict(members)
        if ([(row.account_id, row.user_id) for row in profiles] != members
                or len({row.account_id for row in wallets}) != len(wallets)
                or any(row.account_id not in expected or row.user_id != expected[row.account_id]
                       or not row.is_active for row in wallets)
                or (len(members) == 2 and len(wallets) != 2)):
            raise ValidationError('积分陪玩钱包或账号归属待核对')
    if phase == 'assignment' and (order.payment_status != Order.PaymentStatus.PAID or order.refund_records.exists()):
        raise ValidationError('积分订单当前不可安排陪玩')
    if phase == 'report' and order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED):
        raise ValidationError('积分订单履约付款状态异常')
    if phase == 'refund' and (order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
                              or (order.settled_at and not order.refund_records.exists())):
        raise ValidationError('积分订单当前不可协商退款')
    if phase == 'settlement' and order.status != Order.Status.COMPLETED:
        raise ValidationError('积分订单尚未完成，不能结算')
    with _bind(phase=phase, kind='addon' if addon else 'service', order_id=order.pk,
               account_id=buyer.pk, service_id=order.service_id):
        if team_invalidations:
            from .points_handover_refund_invalidation import _verify_facts
            witness = OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
            for event in PointsHandoverRefundInvalidation.objects.filter(
                    handover__in=superseded_team).select_related('handover__payment__intent', 'refund'):
                _verify_facts(event, receipt_id=witness.receipt_id if witness else None)
        if invalidated_handover_history:
            from .models import PointsHandoverRefundInvalidation
            from .points_handover_refund_invalidation import verify_current_read, verify_order_locked
            from django.core.exceptions import ValidationError as DjangoValidationError
            witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
            for event in PointsHandoverRefundInvalidation.objects.filter(
                    handover__payment__intent__order=order).select_related(
                    'handover__payment__intent', 'refund').order_by('pk'):
                try:
                    if witness:
                        verify_current_read(event, receipt_id=witness.receipt_id)
                    else:
                        with _bind(phase='refund', kind='transfer', order_id=order.pk,
                                   account_id=order.customer_account_id, service_id=order.service_id):
                            verify_order_locked(event)
                except DjangoValidationError as exc:
                    raise ValidationError('接替失效历史资金凭证异常') from exc
        if addon and phase == 'payment' and order.refund_records.exists():
            if transfers or order.refund_records.count() != 1 or order.status != Order.Status.IN_SERVICE:
                raise ValidationError('退款后的加购付款不在继续履约范围')
            from .negotiation_termination import continuing_team
            from .points_refunded_funding import plan_refunded_order
            from .points_funding_plan import PointsFundingPlanError
            if not continuing_team(order, verify=True):
                raise ValidationError('退款后的继续履约凭证待核对')
            try:
                plan_refunded_order(order.pk)
            except PointsFundingPlanError as exc:
                raise ValidationError('退款后的整单资金版本待核对') from exc
        if addon:
            from .models import OrderAddonPayment
            payments = list(OrderAddonPayment.objects.filter(intent__order=order).order_by('pk'))
            if payments:
                from .addon_payment import verify_receipt
                for payment in payments:
                    verify_receipt(payment)
            if phase in ('refund', 'funding') and not payments:
                raise ValidationError('积分加购付款凭证缺失')
            if team_handover and order.refund_records.exists():
                from .models import PointsNegotiationPosting, OrderPointsFundingReleaseEvidence
                from .points_negotiation_termination import verify_termination_locked
                from .points_funding_release import verify_release_locked
                posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
                if not posting:
                    raise ValidationError('主单退款后的接替缺少退款凭证')
                with _bind(phase='refund', kind='addon', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    verify_termination_locked(posting)
                    release = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first()
                    if not release:
                        raise ValidationError('主单退款后的接替缺少资金释放凭证')
                    verify_release_locked(release)
            if team_handover:
                from .addon_funding_release import verify_run
                from .models import OrderAddonFundingReleaseRun, OrderAddonRefund
                past_handovers = [item for item in existing if item.status == 'ACCEPTED']
                events = [(item.accepted_at, 'handover', item) for item in past_handovers]
                events += [(item.created_at, 'refund', item) for item in OrderAddonRefund.objects.filter(order=order)]
                if any(not event[0] for event in events) or len({event[0] for event in events}) != len(events):
                    raise ValidationError('同单接替与退款事件顺序待核对')
                previous_run_at = None
                for event_at, kind, item in sorted(events, key=lambda event: event[0]):
                    run = (OrderAddonFundingReleaseRun.objects.filter(handover=item,
                        points_handover_snapshot__kind='SAME_TEAM_V1').first() if kind == 'handover' else
                        OrderAddonFundingReleaseRun.objects.filter(refund=item,
                            points_addon_snapshot__isnull=False).first())
                    if (not run or run.created_at < event_at or (previous_run_at and event_at <= previous_run_at)):
                        raise ValidationError('同单接替或退款资金版本待核对')
                    if order.refund_records.exists():
                        with _bind(phase='refund', kind='addon', order_id=order.pk,
                                   account_id=order.customer_account_id, service_id=order.service_id):
                            verify_run(run)
                    else:
                        verify_run(run)
                    previous_run_at = run.created_at
            if team_handover_history:
                from .points_team_addon_handover_facts import verify_rejected_attempts
                for payment in payments:
                    verify_rejected_attempts(payment)
                from .addon_funding_release import verify_run
                events = [(item.accepted_at, 'handover', item) for item in accepted_teams]
                events += [(item.created_at, 'refund', item) for item in team_refunds]
                if any(not event[0] for event in events) or len({event[0] for event in events}) != len(events):
                    raise ValidationError('同单接替与退款事件时序无法区分')
                previous_run_at, accepted_payments = None, set()
                for event_at, kind, item in sorted(events, key=lambda event: event[0]):
                    if previous_run_at and event_at <= previous_run_at:
                        raise ValidationError('同单接替与退款资金版本时序异常')
                    if kind == 'handover':
                        run = OrderAddonFundingReleaseRun.objects.get(handover=item)
                        accepted_payments.add(item.payment_id)
                    else:
                        if item.payment_id in team_payment_ids and item.payment_id not in accepted_payments:
                            from .points_team_other_addon_refund import applies as team_other_applies
                            if not team_other_applies(item):
                                raise ValidationError('加购退款早于该付款的接替且来源尚未接入')
                        runs = list(OrderAddonFundingReleaseRun.objects.filter(refund=item))
                        if len(runs) != 1 or (runs[0].points_addon_snapshot is None
                                             and runs[0].points_followup_snapshot is None):
                            raise ValidationError('同单接替后的退款资金版本缺失')
                        run = runs[0]
                    if run.created_at < event_at:
                        raise ValidationError('同单资金版本早于实际事件')
                    verify_run(run)
                    previous_run_at = run.created_at
        if phase in ('report', 'settlement') and order.refund_records.exists():
            from .models import PointsNegotiationPosting, OrderPointsFundingReleaseEvidence
            from .points_negotiation_termination import verify_termination_locked
            from .points_funding_release import verify_release_locked
            postings = list(PointsNegotiationPosting.objects.filter(
                points_refund__redemption__order=order).order_by('points_refund__refund_id'))
            if len(postings) not in (1, 2) or len(postings) != order.refund_records.count():
                raise ValidationError('积分退款结案凭证待核对')
            posting = verify_termination_locked(postings[0], allow_verified_later_final=len(postings) == 2)
            release = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first()
            if not release:
                raise ValidationError('积分退款资金释放凭证缺失')
            verify_release_locked(release)
            if not transfers:
                from .models import PointsZeroFollowupTermination, OrderStatusLog
                zero = PointsZeroFollowupTermination.objects.select_for_update().filter(order=order).first()
                zero_log = OrderStatusLog.objects.filter(order=order,
                    reason='双方确认积分零退款协商终止').exists()
                if zero or zero_log:
                    if (not zero or (not zero_log and not (isinstance(zero.funding_snapshot, dict)
                                                           and zero.funding_snapshot.get('completed_at')))):
                        raise ValidationError('零退款协商终止凭证缺失')
                    from .points_zero_followup import verify_record_locked
                    verify_record_locked(zero)
            if len(postings) == 2:
                from .points_followup_termination import verify_followup_termination_locked
                verify_followup_termination_locked(postings[1])
        if addon and not combined and not transfers and order.refund_records.count() == 2 and phase in ('payment', 'refund'):
            if phase == 'payment':
                raise ValidationError('订单已完成，第二次退款后不能新增加购付款')
            from .points_followup_addon_version import preflight_locked
            with preflight_locked(order):
                yield order
            return
        yield order


@contextmanager
def case_refund_scope(case_id):
    """Bind only this non-transfer points case inside the caller's transaction."""
    from .models import AfterSale, OrderAddonPayment, OrderOriginalShareTransfer
    from .points_service_payment import needs_integrated_processing
    case = AfterSale.objects.select_related('order').filter(pk=case_id).first()
    if not configured() or not case or not needs_integrated_processing(case.order):
        yield
        return
    transferred = OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=case.order).exists()
    transfer_addon = bool(transferred and case.addon_payment_id and not case.order.refund_records.exists())
    addon_main = bool(not transferred and case.addon_payment_id is None
                      and OrderAddonPayment.objects.filter(intent__order=case.order).exists())
    kind = 'addon' if case.addon_payment_id else 'transfer' if transferred else 'addon' if addon_main else 'service'
    if permits(case.order, phase='refund', kind=kind):
        yield
        return
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    if transferred and case.addon_payment_id and case.order.refund_records.exists():
        with transferred_refunded_addon(case.order_id, case.addon_payment_id):
            yield
    elif transfer_addon:
        with service_order(case.order_id, phase='refund', addon=True, refund_payment_id=case.addon_payment_id):
            yield
    elif transferred:
        from .points_transfer_refund_access import after_transfer
        from .models import OrderAddonHandover
        if case.order.refund_records.count() >= 3 and (case.status == 'OPEN' or case.order.refund_records.count() > 3):
            from .points_round_refund_scope import locked as round_scope
            with round_scope(case.order_id):
                yield
            return
        if case.order.refund_records.count() == 3:
            # A completed third-round request needs its original scoped
            # context for replay. This does not authorize a fourth refund.
            from .models import Order, PointsRefundRoundEvidence
            from .financial_lock import lock_order_money
            locked = Order.objects.select_for_update().get(pk=case.order_id)
            lock_order_money()
            evidence = PointsRefundRoundEvidence.objects.filter(order=locked,
                posting__proposal__case_id=case_id, posting__termination_processed_at__isnull=False).first()
            if not evidence:
                raise ValidationError('第三轮退款原请求凭证缺失')
            with _bind(phase='refund', kind='transfer', order_id=locked.pk,
                       account_id=locked.customer_account_id, service_id=locked.service_id):
                yield
            return
        pending = list(OrderAddonHandover.objects.filter(payment__intent__order_id=case.order_id,
            status='PENDING').values_list('payment_id', flat=True)) if case.order.refund_records.count() == 1 else []
        with after_transfer(case.order_id, phase='refund', pending_payment_id=frozenset(pending) if pending else None) as locked:
            if pending:
                from .points_transfer_followup_pending import case_preflight
                current_case = AfterSale.objects.select_for_update().get(pk=case_id, order=locked)
                with case_preflight(locked, current_case.affected_provider_account_ids,
                                    current_case.affected_segment_ids):
                    yield
            else:
                yield
    else:
        with service_order(case.order_id, phase='refund', addon=bool(case.addon_payment_id) or addon_main,
                           combined=addon_main, refund_payment_id=case.addon_payment_id):
            yield


def scoped_refund_case_action(fn):
    """Wrap staff case actions without extending authority to other cases."""
    from functools import wraps
    @wraps(fn)
    def wrapped(self, request, *args, **kwargs):
        with transaction.atomic():
            case = self.get_object()
            with case_refund_scope(case.pk):
                return fn(self, request, *args, **kwargs)
    return wrapped


def scoped_refund_case_operation(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(case_id, *args, **kwargs):
        with case_refund_scope(case_id):
            return fn(case_id, *args, **kwargs)
    return wrapped


def scoped_customer_aftersale_action(fn):
    """Prelock an existing points refund before the customer's new case."""
    from functools import wraps
    @wraps(fn)
    def wrapped(self, request, order_id, *args, **kwargs):
        from .models import Order, OrderOriginalShareTransfer
        from .points_service_payment import needs_integrated_processing
        candidate = Order.objects.filter(pk=order_id).first()
        transferred = bool(candidate and OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=candidate).exists())
        if (configured() and candidate and needs_integrated_processing(candidate)
                and (candidate.refund_records.exists() or transferred)):
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            from collections.abc import Mapping
            requested_addon = bool(isinstance(getattr(request, 'data', None), Mapping)
                                   and request.data.get('addon_payment_id'))
            if transferred and requested_addon and candidate.refund_records.exists():
                from rest_framework import serializers
                payment_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('addon_payment_id'))
                with transferred_refunded_addon(candidate.pk, payment_id):
                    return fn(self, request, order_id, *args, **kwargs)
            if transferred and requested_addon and not candidate.refund_records.exists():
                from rest_framework import serializers
                payment_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('addon_payment_id'))
                with service_order(candidate.pk, phase='refund', addon=True, refund_payment_id=payment_id):
                    return fn(self, request, order_id, *args, **kwargs)
            if transferred:
                from .points_transfer_refund_access import after_transfer
                with after_transfer(candidate.pk, phase='refund'):
                    return fn(self, request, order_id, *args, **kwargs)
            with service_order(candidate.pk, phase='refund', addon=candidate.addon_intents.filter(payment__isnull=False).exists()):
                return fn(self, request, order_id, *args, **kwargs)
        return fn(self, request, order_id, *args, **kwargs)
    return wrapped


@contextmanager
def report_scope(report_id):
    from wallet.models import ProviderReport
    from .models import OrderOriginalShareTransfer
    from .points_service_payment import needs_integrated_processing
    report = ProviderReport.objects.select_related('order').filter(pk=report_id).first()
    if (not configured() or not report or not report.order_id or not needs_integrated_processing(report.order)
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=report.order).exists()):
        yield
        return
    if any(permits(report.order, phase=phase, kind=kind) for kind in ('service', 'addon') for phase in ('report', 'refund', 'settlement')):
        yield
        return
    with service_order(report.order_id, phase='report', addon=report.order.addon_intents.filter(payment__isnull=False).exists()):
        yield


def scoped_report_action(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(self, request, pk, *args, **kwargs):
        with report_scope(pk):
            return fn(self, request, pk, *args, **kwargs)
    return wrapped


def scoped_transfer_open_action(fn):
    """Bind an eligible transferred order before the first aftersale row lock."""
    from functools import wraps
    @wraps(fn)
    def wrapped(self, request, *args, **kwargs):
        from rest_framework import serializers
        from .models import Order, OrderAddonPayment, OrderOriginalShareTransfer
        from .points_service_payment import needs_integrated_processing
        order_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('order_id'))
        candidate = Order.objects.filter(pk=order_id).first()
        if configured() and candidate and needs_integrated_processing(candidate):
            transferred = OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=candidate).exists()
            addon = (request.data.get('addon_payment_id') is not None or
                     OrderAddonPayment.objects.filter(intent__order=candidate).exists())
            if transferred or addon or candidate.refund_records.exists():
                if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                    from console.payroll_event_barrier import lock_payment_refund_events
                    lock_payment_refund_events()
                if transferred:
                    if request.data.get('addon_payment_id') is not None and candidate.refund_records.exists():
                        payment_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('addon_payment_id'))
                        with transferred_refunded_addon(order_id, payment_id):
                            return fn(self, request, *args, **kwargs)
                    if request.data.get('addon_payment_id') is not None:
                        payment_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('addon_payment_id'))
                        with service_order(order_id, phase='refund', addon=True, refund_payment_id=payment_id):
                            return fn(self, request, *args, **kwargs)
                    if candidate.refund_records.count() >= 3:
                        from .points_round_refund_scope import locked as round_scope
                        with round_scope(order_id):
                            return fn(self, request, *args, **kwargs)
                    from .points_transfer_refund_access import after_transfer
                    from .models import OrderAddonHandover, OrderParticipationSegment
                    pending = list(OrderAddonHandover.objects.filter(payment__intent__order_id=order_id,
                        status='PENDING').values_list('payment_id', flat=True)) if candidate.refund_records.count() == 1 else []
                    with after_transfer(order_id, phase='refund', pending_payment_id=frozenset(pending) if pending else None) as locked:
                        if pending:
                            from .aftersale import normalize_scope
                            from .points_transfer_followup_pending import case_preflight
                            scope = normalize_scope(request.data.get('affected_provider_account_ids'))
                            segments = normalize_scope(request.data.get('affected_segment_ids'))
                            if scope is None and segments:
                                rows = list(OrderParticipationSegment.objects.filter(attribution__order=locked,
                                    pk__in=segments).values_list('pk', 'provider_account_id'))
                                if len(rows) != len(segments):
                                    raise ValidationError('售后履约段归属待核对')
                                scope = sorted({account for _, account in rows})
                            with case_preflight(locked, scope, segments):
                                return fn(self, request, *args, **kwargs)
                        return fn(self, request, *args, **kwargs)
                payment_id = (serializers.IntegerField(min_value=1).run_validation(request.data.get('addon_payment_id'))
                              if request.data.get('addon_payment_id') is not None else None)
                with service_order(order_id, phase='refund', addon=addon, refund_payment_id=payment_id):
                    return fn(self, request, *args, **kwargs)
        return fn(self, request, *args, **kwargs)
    return wrapped


def scoped_addon_refund_action(fn):
    """Bind the exact add-on order before proposal or decision row locks."""
    from functools import wraps
    @wraps(fn)
    def wrapped(self, request, order_id, payment_id, *args, **kwargs):
        from .models import Order
        from .points_service_payment import needs_integrated_processing
        with transaction.atomic():
            candidate = Order.objects.filter(pk=order_id).first()
            if (configured() and candidate and needs_integrated_processing(candidate)):
                if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                    from console.payroll_event_barrier import lock_payment_refund_events
                    lock_payment_refund_events()
                from .models import OrderOriginalShareTransfer
                transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=candidate).exists()
                scope = (transferred_refunded_addon(order_id, payment_id) if transferred and candidate.refund_records.exists()
                         else service_order(order_id, phase='refund', addon=True, refund_payment_id=payment_id))
                with scope:
                    return fn(self, request, order_id, payment_id, *args, **kwargs)
            return fn(self, request, order_id, payment_id, *args, **kwargs)
    return wrapped
