"""Single-player window admission and allocation, using persisted receipts."""
import logging
from contextlib import ExitStack
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from stores.db import transaction

from .grab_round_facts import GrabRoundReviewRequired, latest_round_state, pool_exists
from .models import (Order, OrderGrabReceiptProbe, OrderGrabRound, OrderGrabSingleAdmission,
                     OrderGrabSingleRoundState, OrderStatusLog)

logger = logging.getLogger(__name__)
ADMISSION_STATES = ('OPEN', 'PARKED')


def enabled():
    from .grab_clock_contract import production_enabled
    return bool(getattr(settings, 'GRAB_SINGLE_WINDOW_ENABLED', False)
                and ((getattr(settings, 'GRAB_POOL_ENTRY_SNAPSHOT_ENABLED', False)
                      and getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False))
                     or production_enabled()))


def applies(order_id):
    # Existing rounds must never fall through to the old immediate assignment
    # or timeout refund merely because a process loses its clock contract.
    return pool_exists(order_id, seats=1)


def has_admitting_round(order_id):
    if not enabled():
        return False
    status = (OrderGrabRound.objects.filter(pool__order_id=order_id, pool__required_provider_count=1)
              .order_by('-number').values_list('single_state__status', flat=True).first())
    return status in ADMISSION_STATES


def _latest(order_id):
    return latest_round_state(order_id, seats=1)


def _advance_due_round(order_id):
    """Do not depend on a timely worker to move an expired window into its next state."""
    state = _latest(order_id)
    if state and state.status == 'OPEN':
        from .grab_receipt_probe import _db_clock
        now, precision, _ = _db_clock()
        if now - timedelta(microseconds=precision) > state.round.deadline:
            resolve(order_id)
    return _latest(order_id)


def enqueue(order_id, *, eta=None):
    from .tasks import finalize_single_grab
    try:
        finalize_single_grab.apply_async(args=[order_id], eta=eta)
    except Exception:
        logger.exception('抢单轮次任务入队失败，等待定时扫描：order=%s', order_id)


def submit(request, order_id):
    from .grab_receipt_probe import record_intent
    if request.account.account_type != 'PROVIDER':
        return Response({'code': 404, 'msg': '抢单订单不存在'}, status=404)
    if not enabled():
        return Response({'code': 503, 'msg': '抢单暂不可用，请联系客服核对订单'}, status=503)
    state = _advance_due_round(order_id)
    if not state:
        return Response({'code': 409, 'errorCode': 'GRAB_ROUND_REVIEW', 'msg': '当前抢单轮次待核对'}, status=409)
    request_id = request.data.get('client_request_id')
    if request_id is None:
        admission = (OrderGrabSingleAdmission.objects.filter(round_id=state.round_id, account=request.account)
                     .select_related('receipt').order_by('-pk').first()) if state.status not in ('OPEN', 'PARKED') else None
        if admission:
            request_id = admission.receipt.request_key
        else:
            previous = OrderGrabSingleAdmission.objects.filter(round_id=state.round_id, account=request.account,
                phase=state.status, status='DISQUALIFIED').count()
            request_id = f'single-{order_id}-{request.account.pk}-{state.status.lower()}-{previous}'
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
        return Response({'code': 400, 'msg': '抢单请求编号无效'}, status=400)
    if request.data.get('team_size', 1) != 1:
        return Response({'code': 400, 'msg': '当前订单为单陪'}, status=400)
    try:
        result = record_intent(state.round_id, request.account.pk, 'SINGLE', request_id, {},
                               single_flow=True, actor_snapshot=request.account)
    except ValueError as exc:
        return Response({'code': 400, 'msg': str(exc)}, status=400)
    if result.state in ('PENDING', 'REVIEW'):
        return Response({'code': 409, 'msg': '抢单登记时间待核对，请勿重复提交'}, status=409)
    if result.state == 'AFTER':
        return Response({'code': 409, 'msg': '本轮抢单已截止，请等待结果'}, status=409)
    if result.replayed and state.status == 'ASSIGNED':
        won = state.winner_id == result.receipt_id
        return Response({'code': 0, 'msg': '抢单成功' if won else '本次未抢到订单',
                         'data': {'receipt_id': result.receipt_id, 'status': 'WON' if won else 'NOT_WON'}})
    if result.replayed and state.status == 'CANCELLED':
        return Response({'code': 0, 'msg': '订单已取消',
                         'data': {'receipt_id': result.receipt_id, 'status': 'CANCELLED'}})
    if state.status == 'PARKED':
        outcome = resolve(order_id)
        if outcome == 'ASSIGNED':
            current = _latest(order_id)
            won = current.winner_id == result.receipt_id
            return Response({'code': 0, 'msg': '抢单成功' if won else '本次未抢到订单',
                             'data': {'receipt_id': result.receipt_id, 'status': 'WON' if won else 'NOT_WON'}})
    enqueue(order_id)
    return Response({'code': 0, 'msg': '抢单已提交，请等待系统分配',
                     'data': {'receipt_id': result.receipt_id, 'status': 'PENDING'}})


def cancel(request, order_id):
    from .grab_customer_cancel import cancel as customer_cancel

    return customer_cancel(request, order_id, state=_latest(order_id), enabled=enabled(), resolve=resolve, team=False)


def _cancel_order(order, state, *, receipt=None):
    from .services import refund_to_customer
    from .state_machine import transition
    reason = (receipt.payload.get('reason') if receipt else '') or ('客户取消' if receipt else '超时未接单自动取消')
    refund_fact = {}
    def refund(locked):
        from .grab_zero_subsidy_cancel import before_cancel, after_cancel
        zero_subsidy = before_cancel(locked, state)
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(locked):
            from .points_service_timeout import refund_customer_cancel_locked, refund_expired_locked
            if receipt:
                refund_fact['refund'] = refund_customer_cancel_locked(locked, operator_account=receipt.actor, grab_single_state=state)
            else:
                refund_fact['refund'] = refund_expired_locked(locked, grab_single_state=state)
        else:
            refund_fact['refund'] = refund_to_customer(locked, reason=reason,
                                                       operator_account=receipt.actor if receipt else None)
        after_cancel(locked, refund_fact['refund'], zero_subsidy)
        locked.cancel_reason = reason
    cancelled = transition(order.pk, Order.Status.CANCELLED,
        operator_account=receipt.actor if receipt else None,
        action=OrderStatusLog.Action.CANCEL if receipt else OrderStatusLog.Action.AUTO_CANCEL,
        reason=reason, side_effect=refund, update_fields=['cancel_reason', 'payment_status', 'refunded_at'])
    state.status = 'CANCELLED'
    state.save(update_fields=['status', 'updated_at'])
    if not receipt:
        from .timeout import record_timeout_notice
        record_timeout_notice(cancelled, refund=refund_fact.get('refund'))
    else:
        from site_messages.order_customer_cancel_notice import prepare
        prepare(cancelled, refund=refund_fact.get('refund'), receipt=receipt)
    from .models import OrderGrabReviewCancellation
    OrderGrabReviewCancellation.objects.filter(order=order, status='PENDING').update(
        status='CANCELLED', completed_at=timezone.now())
    return cancelled


def _assign(order, state, admission):
    from .services import lock_provider_dispatch, mark_escort_busy, sync_single_provider_share
    from .state_machine import transition
    from .grab_admission import check_candidate, GrabAdmissionError
    from users.models import EscortProfile
    lock_provider_dispatch([admission.account_id])
    profile = EscortProfile.objects.select_for_update().select_related('user', 'account').get(account_id=admission.account_id)
    check_candidate(state.round.pool_id, admission.account_id, round_id=state.round_id, locked_profile=profile)
    def assign(locked):
        locked.provider, locked.provider_account = profile.user, profile.account
        locked.provider_name_snapshot = profile.display_name or profile.account.nickname or profile.user.nickname or profile.user.username
        sync_single_provider_share(locked)
        from wallet.refund_recovery_order import enabled as recovery_enabled, reserve, RecoveryIneligible
        if recovery_enabled():
            try:
                reserve(profile, locked)
            except RecoveryIneligible as exc:
                raise GrabAdmissionError(str(exc)) from exc
        mark_escort_busy(provider_ids=[profile.user_id], account_ids=[profile.account_id])
    assigned = transition(order.pk, Order.Status.GRABBED, operator=profile.user, operator_account=profile.account,
        side_effect=assign, update_fields=['provider', 'provider_account', 'provider_name_snapshot'],
        dispatch_account_ids=[profile.account_id])
    state.status, state.winner = 'ASSIGNED', admission.receipt
    state.save(update_fields=['status', 'winner', 'updated_at'])
    return assigned


def _notify_result(order, admissions, winner=None, *, reopened=False, state=None):
    from site_messages.grab_cycle import receipt_cycle, schedule
    recovery = (getattr(settings, 'GRAB_RESULT_NOTICE_RECOVERY_ENABLED', False)
                and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
    intents = []
    for admission in admissions:
        won = bool(winner and admission.pk == winner.pk)
        title = '抢单需重新参加' if reopened else ('抢单成功' if won else '本次未抢到订单')
        cycle = receipt_cycle(admission.receipt, admission.account_id, round_id=admission.round_id, kind='SINGLE')
        if recovery:
            intents.append({'account_id': admission.account_id, 'business_cycle': cycle, 'account_type': 'PROVIDER',
                'receipt_id': admission.receipt_id, 'title': title,
                'preview': f'「{order.service_name_snapshot}」{title}', 'assignment_alert': won})
        else:
            schedule(account_id=admission.account_id, cycle=cycle, title=title,
                preview=f'「{order.service_name_snapshot}」{title}', order_id=order.pk, assignment_alert=won,
                strict_cycle=True)
    if winner:
        if recovery:
            intents.append({'account_id': order.customer_account_id, 'user_id': order.customer_id,
                'business_cycle': order.business_cycle, 'account_type': 'BOSS', 'receipt_id': None,
                'title': '陪玩已接单', 'preview': f'你的「{order.service_name_snapshot}」已有陪玩接单',
                'assignment_alert': False})
        else:
            schedule(account_id=order.customer_account_id, recipient_id=order.customer_id, cycle=order.business_cycle,
                title='陪玩已接单', preview=f'你的「{order.service_name_snapshot}」已有陪玩接单', order_id=order.pk,
                strict_cycle=True)
    if recovery and state is not None and intents:
        state.notification_intents = intents
        state.save(update_fields=['notification_intents'])
        from site_messages.grab_result_notice import schedule as schedule_recovery
        schedule_recovery(state.pk)


def _reopen(order, state, now):
    from datetime import timedelta
    previous = state.round
    deadline = now + timedelta(seconds=previous.pool.window_seconds)
    total = max(previous.total_deadline, deadline)
    round_ = OrderGrabRound.objects.create(pool=previous.pool, number=previous.number + 1,
        opened_at=now, deadline=deadline, total_deadline=total, simultaneous_visibility=True)
    from .grab_round_opening import stage
    stage(round_)
    OrderGrabSingleRoundState.objects.create(round=round_)
    state.status = 'REOPENED'
    state.save(update_fields=['status', 'updated_at'])
    order.auto_cancel_at = total
    order.save(update_fields=['auto_cancel_at', 'updated_at'])
    transaction.on_commit(lambda: enqueue(order.pk, eta=deadline))


def resolve(order_id, *, _cancel_only=False):
    """Process committed receipts under the order lock; never infer unknown times."""
    if not enabled():
        return 'DISABLED'
    from .grab_receipt_probe import _db_clock, _effective_state, compare_candidate_bounds
    from .grab_bound_ranking import preview
    from .grab_admission import GrabAdmissionError
    from .notifier import notify_order_update
    from .grab_cutoff_watermark import enabled as watermark_enabled
    lifecycle = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    watermark = watermark_enabled()
    snapshot = None
    if (lifecycle or watermark) and not _cancel_only:
        if transaction.get_connection().in_atomic_block:
            raise RuntimeError('抢单分配必须使用独立的最外层事务')
        outcome = resolve(order_id, _cancel_only=True)
        if outcome != 'NO_CANCELLATION':
            return outcome
    if watermark and not _cancel_only:
        from .grab_cutoff_watermark import seal
        try:
            if seal(order_id, team=False) == 'REVIEW':
                return 'WAIT_WATERMARK_REVIEW'
        except ValueError:
            return 'WAIT_WATERMARK_REVIEW'
    if lifecycle and not _cancel_only:
        from .grab_allocation_lifecycle import capture
        snapshot = capture(order_id, team=False)
    with transaction.atomic(), ExitStack() as stack:
        if lifecycle and not _cancel_only:
            from club_accounts.provider_lifecycle import ProviderLifecycleError
            from .grab_allocation_lifecycle import lock
            try:
                lock(snapshot)
            except ProviderLifecycleError:
                return 'WAIT_LIFECYCLE_RETRY'
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            return 'NOT_FOUND'
        if order.status == Order.Status.PENDING:
            from .applications import ApplicationError, _enter_assignment_scope
            try:
                _enter_assignment_scope(stack, order)
            except ApplicationError:
                return 'WAIT_POINTS_FUNDING_REVIEW'
        if order.required_provider_count != 1:
            return 'WAIT_ROUND_REVIEW'
        try:
            state = latest_round_state(order.pk, seats=1, lock=True)
        except GrabRoundReviewRequired as exc:
            from .grab_review import report_invalid_round
            report_invalid_round(order, exc)
            return 'WAIT_ROUND_REVIEW'
        if not state or state.status not in ('OPEN', 'PARKED') or order.status != Order.Status.PENDING:
            return state.status if state else 'NOT_FOUND'
        now, precision, _ = _db_clock()
        from .grab_clock_contract import production_enabled
        if production_enabled():
            try:
                from .grab_round_opening import cutoff_bounds
                window_upper = cutoff_bounds(state.round, 'OPEN')[1]
                total_lower, total_upper = cutoff_bounds(state.round, 'PARKED')
            except ValueError:
                return 'WAIT_OPENING_REVIEW'
        else:
            window_upper = state.round.deadline
            total_lower = total_upper = state.round.total_deadline
        from django.db.models import Prefetch
        query = (OrderGrabReceiptProbe.objects.select_for_update().filter(round=state.round).order_by('pk')
                 .prefetch_related(Prefetch('single_admission',
                     queryset=OrderGrabSingleAdmission.objects.select_for_update())))
        # The cancellation-only pass does not hold lifecycle locks. Avoid
        # locking joined account rows before the normal lifecycle lock order.
        if not _cancel_only:
            query = query.select_related('actor', 'round__pool__order', 'pass_evidence')
        rows = list(query)
        relevant = [row for row in rows if row.round_phase == state.status
                    or (state.status == 'OPEN' and row.round_phase == 'PARKED'
                        and row.kind == 'BOSS_CANCEL')]
        classified = {row.pk: _effective_state(row, getattr(row, 'pass_evidence', None)) for row in relevant}
        cancellation = next((row for row in relevant if row.kind == 'BOSS_CANCEL' and classified[row.pk] == 'BEFORE'), None)
        if cancellation:
            cancelled = _cancel_order(order, state, receipt=cancellation)
            transaction.on_commit(lambda: notify_order_update(cancelled))
            return 'CANCELLED'
        from .models import OrderGrabReviewCancellation
        if OrderGrabReviewCancellation.objects.filter(order=order, status='PENDING').exists():
            return 'WAIT_REVIEW'
        if any(value in ('PENDING', 'REVIEW') for value in classified.values()):
            if _cancel_only and watermark:
                return 'NO_CANCELLATION'
            return 'WAIT_REVIEW'
        if _cancel_only:
            return 'NO_CANCELLATION'
        if watermark_enabled() and (state.status == 'PARKED'
                                    or now - timedelta(microseconds=precision) > window_upper):
            from .grab_cutoff_watermark import verify
            try:
                if not verify(state.round, rows, observed_at=now, precision_us=precision):
                    return 'WAIT_WATERMARK_REVIEW'
            except ValueError:
                return 'WAIT_WATERMARK_REVIEW'
        if lifecycle:
            from .grab_allocation_lifecycle import accounts_unchanged, matches
            if not matches(snapshot, state, team=False) or not accounts_unchanged(snapshot):
                return 'WAIT_LIFECYCLE_RETRY'
        if state.status == 'OPEN' and now - timedelta(microseconds=precision) <= window_upper:
            return 'WAIT'
        # This transaction may already have an older consistent snapshot.
        # The order and receipts are locked, so read matching admissions as
        # current rows as well; otherwise a valid committed claim can vanish.
        admissions = list(OrderGrabSingleAdmission.objects.select_for_update().filter(
            round=state.round, phase=state.status, status='ACTIVE',
            receipt_id__in=[row.pk for row in relevant if classified[row.pk] == 'BEFORE'])
            .select_related('receipt').order_by('receipt_id'))
        if not admissions:
            if state.status == 'OPEN':
                parked_ids = [row.pk for row in rows if row.round_phase == 'PARKED' and row.kind == 'SINGLE'
                              and _effective_state(row, getattr(row, 'pass_evidence', None))
                              in ('BEFORE', 'PENDING', 'REVIEW')]
                if parked_ids and OrderGrabSingleAdmission.objects.select_for_update().filter(
                        round=state.round, phase='PARKED', status='ACTIVE', receipt_id__in=parked_ids).exists():
                    state.status = 'PARKED'
                    state.save(update_fields=['status', 'updated_at'])
                    transaction.on_commit(lambda: enqueue(order.pk), robust=True)
                    return 'PARKED'
            if now - timedelta(microseconds=precision) >= total_upper:
                cancelled = _cancel_order(order, state)
                transaction.on_commit(lambda: notify_order_update(cancelled))
                return 'CANCELLED'
            if state.status == 'OPEN':
                state.status = 'PARKED'
                state.save(update_fields=['status', 'updated_at'])
            return 'PARKED'
        if lifecycle:
            from .grab_receipt_probe import actor_membership_evidence_present, locked_actor_membership_matches
            if not all(actor_membership_evidence_present(row.receipt) for row in admissions):
                return 'WAIT_LIFECYCLE_FACTS'
            eligible_membership = {row.receipt_id: locked_actor_membership_matches(row.receipt) for row in admissions}
        if state.status == 'OPEN':
            from .grab_metric_snapshot import MetricSnapshotUnavailable
            from .grab_bound_ranking import snapshot_data
            try:
                result = preview(state.round_id, [row.receipt_id for row in admissions])
            except MetricSnapshotUnavailable:
                return 'WAIT_METRIC_SNAPSHOT_RETRY'
            if result.status != 'POTENTIAL_WINNER':
                from .grab_review import mark_round_review
                mark_round_review(state, '候选排序时间或指标待核对')
                return 'REVIEW'
            winner = next(row for row in admissions if row.receipt_id == result.winner_id)
            state.ranking_snapshot = snapshot_data(result)
            state.save(update_fields=['ranking_snapshot', 'updated_at'])
            try:
                if lifecycle and not eligible_membership[winner.receipt_id]:
                    raise GrabAdmissionError('陪玩门店成员身份已变化')
                with transaction.atomic():
                    assigned = _assign(order, state, winner)
            except GrabAdmissionError:
                now, precision, _ = _db_clock()
                if now - timedelta(microseconds=precision) >= total_upper:
                    cancelled = _cancel_order(order, state)
                    transaction.on_commit(lambda: notify_order_update(cancelled))
                    return 'CANCELLED'
                if now + timedelta(microseconds=precision) >= total_lower:
                    from .grab_review import mark_round_review
                    mark_round_review(state, '首名失格时总等待截止区间尚不能确定')
                    return 'REVIEW'
                _reopen(order, state, now)
                _notify_result(order, admissions, reopened=True, state=state)
                return 'REOPENED'
            except ValueError as exc:
                from .grab_review import mark_round_review
                mark_round_review(state, str(exc)[:255])
                return 'REVIEW'
        else:
            from .services import lock_provider_dispatch
            from .grab_parked_eligibility import reason as parked_ineligibility
            from users.models import EscortProfile
            lock_provider_dispatch([row.account_id for row in admissions])
            profiles = {profile.account_id: profile for profile in
                        EscortProfile.objects.select_for_update().select_related('account', 'user')
                        .filter(account_id__in=[row.account_id for row in admissions]).order_by('account_id', 'pk')}
            for candidate in list(admissions):
                profile = profiles.get(candidate.account_id)
                if not profile:
                    continue  # Missing facts remain for the existing review path.
                try:
                    why = parked_ineligibility(profile, membership_valid=(
                        eligible_membership[candidate.receipt_id] if lifecycle else True))
                except ValueError as exc:
                    from .grab_review import mark_round_review
                    mark_round_review(state, str(exc))
                    return 'REVIEW'
                if why:
                    candidate.status, candidate.disqualification_reason = 'DISQUALIFIED', why[:255]
                    candidate.save(update_fields=['status', 'disqualification_reason'])
                    admissions.remove(candidate)
                    from site_messages.grab_disqualification_notice import record_single
                    record_single(order, candidate)
            while admissions:
                winner = admissions[0]
                for candidate in admissions[1:]:
                    comparison = compare_candidate_bounds(winner.receipt_id, candidate.receipt_id)
                    if comparison == 'REVIEW':
                        from .grab_review import mark_round_review
                        mark_round_review(state, '留池候选提交时间待核对')
                        return 'REVIEW'
                    if comparison == 'SECOND_EARLIER':
                        winner = candidate
                try:
                    if lifecycle and not eligible_membership[winner.receipt_id]:
                        raise GrabAdmissionError('陪玩门店成员身份已变化')
                    with transaction.atomic():
                        assigned = _assign(order, state, winner)
                    break
                except GrabAdmissionError as exc:
                    winner.status, winner.disqualification_reason = 'DISQUALIFIED', str(exc)[:255]
                    winner.save(update_fields=['status', 'disqualification_reason'])
                    admissions.remove(winner)
                    from site_messages.grab_disqualification_notice import record_single
                    record_single(order, winner)
                except ValueError as exc:
                    from .grab_review import mark_round_review
                    mark_round_review(state, str(exc)[:255])
                    return 'REVIEW'
            else:
                now, precision, _ = _db_clock()
                if now - timedelta(microseconds=precision) < total_upper:
                    return 'PARKED'
                cancelled = _cancel_order(order, state)
                transaction.on_commit(lambda: notify_order_update(cancelled))
                return 'CANCELLED'
        _notify_result(assigned, admissions, winner, state=state)
        transaction.on_commit(lambda: notify_order_update(assigned))
        return 'ASSIGNED'
