"""Double-player automatic grab: team submissions and round allocation."""
import logging
from contextlib import ExitStack
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from club_accounts.permissions import IsClubAccountAuthenticated
from .models import (Order, OrderGrabReceiptProbe, OrderGrabRound, OrderGrabTeamDraft,
                     OrderGrabTeamRoundState, OrderStatusLog)

logger = logging.getLogger(__name__)


def enabled():
    from .grab_clock_contract import production_enabled
    return bool(getattr(settings, 'GRAB_TEAM_WINDOW_ENABLED', False)
                and ((getattr(settings, 'GRAB_POOL_ENTRY_SNAPSHOT_ENABLED', False)
                      and getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False))
                     or production_enabled()))


def applies(order_id):
    return OrderGrabTeamRoundState.objects.filter(
        round__pool__order_id=order_id, round__pool__required_provider_count=2).exists()


def _latest(order_id):
    return (OrderGrabTeamRoundState.objects.filter(round__pool__order_id=order_id)
            .select_related('round__pool__order').order_by('-round__number').first())


def enqueue(order_id, *, eta=None):
    from .tasks import finalize_team_grab
    try:
        finalize_team_grab.apply_async(args=[order_id], eta=eta)
    except Exception:
        logger.exception('双陪抢单任务入队失败，等待定时扫描：order=%s', order_id)


def _request_key(request):
    key = request.data.get('client_request_id')
    if not isinstance(key, str) or not key.strip() or len(key) > 64:
        raise ValueError('请提供有效的抢单请求编号')
    return key.strip()


def _advance_due_round(order_id):
    state = _latest(order_id)
    if state and state.status == 'OPEN':
        from .grab_receipt_probe import _db_clock
        now, precision, _ = _db_clock()
        if now - timedelta(microseconds=precision) > state.round.deadline:
            resolve(order_id)
    return _latest(order_id)


def _release_late_team(team_id, *, confirmation, receipt_id=None):
    """A definitely late submission must not occupy a place in the next phase."""
    with transaction.atomic():
        order_id = OrderGrabTeamDraft.objects.filter(pk=team_id).values_list('round__pool__order_id', flat=True).get()
        Order.objects.select_for_update().get(pk=order_id)
        team = OrderGrabTeamDraft.objects.select_for_update().get(pk=team_id)
        member_id = team.partner_id if confirmation else team.captain_id
        member = team.members.select_for_update().get(account_id=member_id)
        evidence = member.receipt_pass_evidence
        if receipt_id is not None and (not evidence or evidence.receipt_id != receipt_id):
            return
        if confirmation and team.status == 'UNVERIFIED':
            member.joined, member.provisional_pass_tier = False, None
            member.provisional_pass_expires_at, member.receipt_pass_evidence = None, None
            member.save(update_fields=['joined', 'provisional_pass_tier', 'provisional_pass_expires_at',
                                       'receipt_pass_evidence'])
            team.occupied_seats.filter(account_id=team.partner_id).delete()
            team.status = 'INVITING'
            team.save(update_fields=['status', 'updated_at'])
        elif not confirmation and team.status in ('INVITING', 'UNVERIFIED'):
            team.occupied_seats.all().delete()
            team.status, team.disqualification_reason = 'DISQUALIFIED', '邀请登记已超过本轮截止时间'
            team.save(update_fields=['status', 'disqualification_reason', 'updated_at'])


class TeamInviteView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        if not enabled() or request.account.account_type != 'PROVIDER':
            return Response({'code': 404, 'msg': '抢单订单不存在'}, status=404)
        state = _advance_due_round(order_id)
        partner = request.data.get('partner_id')
        if not state or type(partner) is not int or partner <= 0 or partner == request.account.pk:
            return Response({'code': 400, 'msg': '请选择其他陪玩组队'}, status=400)
        from .grab_receipt_probe import GrabRequestConflict, record_intent
        try:
            key = _request_key(request)
            result = record_intent(state.round_id, request.account.pk, 'INVITE', key,
                                   {'partner_id': partner}, team_flow=True, actor_snapshot=request.account)
        except GrabRequestConflict as exc:
            return Response({'code': 409, 'errorCode': 'REQUEST_ID_CONFLICT', 'msg': str(exc)}, status=409)
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        if result.state == 'PENDING':
            return Response({'code': 409, 'errorCode': 'GRAB_INVITE_PENDING', 'receipt_id': result.receipt_id,
                             'msg': '邀请已登记，结果待确认，请用原请求号重试'}, status=409)
        if result.state not in ('BEFORE', 'AFTER'):
            return Response({'code': 409, 'errorCode': 'GRAB_INVITE_REVIEW',
                             'msg': '邀请时间待核对，请勿重新发起'}, status=409)
        team = OrderGrabTeamDraft.objects.filter(round__pool__order_id=order_id,
            round__receipt_probes__pk=result.receipt_id, captain=request.account, partner_id=partner, request_key=key).first()
        if not team:
            return Response({'code': 409, 'msg': '原组队登记待核对'}, status=409)
        if result.state == 'AFTER':
            if team.round_id == state.round_id:
                _release_late_team(team.pk, confirmation=False, receipt_id=result.receipt_id)
            return Response({'code': 409, 'errorCode': 'GRAB_INVITE_CLOSED', 'msg': '本轮邀请已截止'}, status=409)
        if team.round_id == state.round_id:
            from site_messages.grab_invite import schedule
            schedule(result.receipt_id)
        return Response({'code': 0, 'data': {'team_id': team.pk, 'status': team.status}})


def _invitation_visibility(team, account_id):
    from .grab_admission import GrabAdmissionError, check_candidate
    from .grab_clock_contract import production_enabled
    from .grab_pool_visibility import initial_visibility
    from .grab_round_opening import opening_bounds

    try:
        if team.round.simultaneous_visibility:
            visible_at = opening_bounds(team.round)[1] if production_enabled() else team.round.opened_at
        else:
            visibility = initial_visibility(team.round.pool_id, account_id)
            if visibility.opening_pending:
                return None, False, 'PENDING'
            visible_at = visibility.visible_at
    except ValueError:
        return None, False, 'REVIEW'
    try:
        check_candidate(team.round.pool_id, account_id, round_id=team.round_id)
    except GrabAdmissionError:
        return visible_at, False, 'KNOWN'
    return visible_at, True, 'KNOWN'


class TeamInvitationsView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if not enabled() or request.account.account_type != 'PROVIDER':
            return Response({'code': 404, 'msg': '组队邀请不存在'}, status=404)
        from .grab_receipt_probe import _effective_state, team_invitation_cycles_match
        before = request.query_params.get('before_id')
        if before is not None and (not before.isascii() or not before.isdigit() or len(before) > 19
                                   or not 0 < int(before) <= 9223372036854775807):
            return Response({'code': 400, 'msg': '邀请位置无效，请刷新列表'}, status=400)
        rows = (OrderGrabTeamDraft.objects.filter(partner=request.account, status__in=['INVITING', 'UNVERIFIED'],
            round__team_state__status__in=['OPEN', 'PARKED']).select_related('captain', 'round__pool__order')
            .order_by('-pk'))
        if before is not None:
            rows = rows.filter(pk__lt=int(before))
        data = []
        for team in rows.iterator(chunk_size=100):
            captain_member = team.members.filter(account_id=team.captain_id).select_related(
                'receipt_pass_evidence__receipt__actor').first()
            evidence = captain_member.receipt_pass_evidence if captain_member else None
            receipt = evidence.receipt if evidence else None
            if (not team.captain.is_active or not team.captain.can_login
                    or not team.captain.license_membership_active
                    or not team_invitation_cycles_match(team, receipt,
                        captain_cycle=team.captain.business_cycle, partner_cycle=request.account.business_cycle)):
                continue
            receipt_state = _effective_state(receipt, evidence)
            if receipt_state == 'AFTER':
                continue
            visible_at, visible, visibility_state = (None, False, receipt_state)
            if receipt_state == 'BEFORE':
                visible_at, visible, visibility_state = _invitation_visibility(team, request.account.pk)
            item = {'team_id': team.pk, 'captain_name': team.captain.nickname or '陪玩', 'status': team.status,
                    'visible_at': visible_at, 'order_visible': visible, 'visibility_state': visibility_state}
            if visible:
                item.update(order_id=team.round.pool.order_id,
                            service_name=team.round.pool.order.service_name_snapshot)
            data.append(item)
            if len(data) > 50:
                break
        has_more = len(data) > 50
        data = data[:50]
        return Response({'code': 0, 'data': data, 'has_more': has_more,
                         'next_before_id': data[-1]['team_id'] if has_more else None})


class TeamActionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, team_id, action):
        if not enabled() or request.account.account_type != 'PROVIDER':
            return Response({'code': 404, 'msg': '组队不存在'}, status=404)
        team = OrderGrabTeamDraft.objects.select_related('round__pool').filter(pk=team_id).first()
        if not team or request.account.pk not in (team.captain_id, team.partner_id):
            return Response({'code': 404, 'msg': '组队不存在'}, status=404)
        if action not in ('confirm', 'withdraw'):
            return Response({'code': 404, 'msg': '操作不存在'}, status=404)
        try:
            _advance_due_round(team.round.pool.order_id)
            key = _request_key(request)
            from .grab_receipt_probe import record_intent
            result = record_intent(team.round_id, request.account.pk, action.upper(), key,
                                   {'team_id': team.pk}, team_flow=True, actor_snapshot=request.account)
            if result.state == 'BEFORE' and action == 'withdraw':
                from .grab_team_draft import withdraw_with_receipt
                with transaction.atomic():
                    withdraw_with_receipt(team.round_id, team.pk, result.receipt_id)
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        if result.state == 'AFTER' and action == 'confirm':
            _release_late_team(team.pk, confirmation=True, receipt_id=result.receipt_id)
        if result.state != 'BEFORE':
            return Response({'code': 409, 'msg': '操作时间已截止或待核对'}, status=409)
        team.refresh_from_db()
        if action == 'confirm':
            state = _latest(team.round.pool.order_id)
            if state and state.status == 'PARKED':
                resolve(team.round.pool.order_id)
            else:
                enqueue(team.round.pool.order_id)
        # Read this team's own persisted round after any synchronous resolve.
        # A task being queued is not evidence of an allocation. A retry from a
        # later account cycle must not expose the old confirmation's result.
        team.refresh_from_db()
        data = {'team_id': team.pk, 'status': team.status}
        if action == 'confirm':
            receipt = OrderGrabReceiptProbe.objects.filter(pk=result.receipt_id, round_id=team.round_id,
                actor_id=request.account.pk, kind='CONFIRM',
                actor_business_cycle=request.account.business_cycle).first()
            state = OrderGrabTeamRoundState.objects.filter(round_id=team.round_id).first()
            if receipt and receipt.payload == {'team_id': team.pk} and state:
                if state.status == 'ASSIGNED' and state.winner_id:
                    data['allocation_result'] = 'WON' if state.winner_id == team.pk else 'NOT_WON'
                elif state.status in ('CANCELLED', 'REOPENED', 'REVIEW'):
                    data['allocation_result'] = state.status
                elif team.status in ('WITHDRAWN', 'DISQUALIFIED'):
                    data['allocation_result'] = team.status
                else:
                    data['allocation_result'] = 'PENDING'
        return Response({'code': 0, 'data': data})


def cancel(request, order_id):
    state = _latest(order_id)
    if (not state or request.account.account_type != 'BOSS'
            or state.round.pool.order.customer_account_id != request.account.pk):
        return Response({'code': 404, 'msg': '订单不存在'}, status=404)
    if not enabled():
        return Response({'code': 503, 'msg': '订单取消暂需客服核对，请联系客服'}, status=503)
    if state.status == 'CANCELLED':
        return Response({'code': 0, 'msg': '订单已取消，资金及权益已按规则退回', 'data': {'status': 'CANCELLED'}})
    if state.status == 'REVIEW':
        from .grab_customer_review import request_cancellation
        return request_cancellation(request, order_id)
    if state.status not in ('OPEN', 'PARKED', 'CANCELLED'):
        return Response({'code': 409, 'msg': '订单已分配或正在核对，请查看订单状态'}, status=409)
    try:
        key = request.data.get('client_request_id') or f'cancel-team-{order_id}-{request.account.pk}'
        reason = request.data.get('reason') or ''
        if not isinstance(reason, str):
            raise ValueError('取消原因无效')
        from .grab_receipt_probe import record_intent
        result = record_intent(state.round_id, request.account.pk, 'BOSS_CANCEL', key,
                               {'reason': reason.strip()[:255]}, team_flow=True)
    except ValueError as exc:
        return Response({'code': 400, 'msg': str(exc)}, status=400)
    if result.state == 'AFTER':
        return Response({'code': 409, 'errorCode': 'GRAB_CANCEL_CLOSED', 'msg': '订单取消时限已到'}, status=409)
    if result.state != 'BEFORE':
        from .grab_customer_review import request_cancellation
        return request_cancellation(request, order_id)
    if resolve(order_id) != 'CANCELLED':
        return Response({'code': 409, 'msg': '取消请求待核对，请查看订单状态'}, status=409)
    return Response({'code': 0, 'msg': '订单已取消，资金及权益已按规则退回', 'data': {'status': 'CANCELLED'}})


@transaction.atomic
def _assign(order, state, team):
    from .grab_admission import check_candidate, GrabAdmissionError
    from .services import lock_provider_dispatch, mark_escort_busy
    from .state_machine import transition
    from .pricing import club_provider_specs
    from .settlement import build_provider_shares, aggregate_order_split
    from users.models import EscortProfile
    from .models import OrderProvider
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        from .points_checkout_test import assignment_enabled
        if not assignment_enabled():
            raise ValueError('积分订单的双陪收益尚未接通')
    if team.status == 'WITHDRAWN':
        raise GrabAdmissionError('首轮队伍已在最终分配前退出')
    ids = [team.captain_id, team.partner_id]
    lock_provider_dispatch(ids)
    profiles = {p.account_id: p for p in EscortProfile.objects.select_for_update().select_related('account', 'user')
                .filter(account_id__in=ids).order_by('account_id', 'pk')}
    if len(profiles) != 2:
        raise ValueError('陪玩资料不存在')
    for account_id in sorted(ids):
        check_candidate(state.round.pool_id, account_id, round_id=state.round_id,
                        locked_profile=profiles[account_id])
    people = [profiles[pk] for pk in ids]
    personal_evidence = None
    if (order.commission_allocation_pending or
            (not order.in_service_at and (order.game_commission_snapshot or {}).get('personal_rules'))):
        from .delta_personal_commission import pending_assignment_terms
        shares, split, personal_evidence = pending_assignment_terms(order, people, allow_reassignment=True)
    else:
        specs = club_provider_specs(order, people, order.amount)
        if specs is None:
            specs = [{'commission_rate': order.commission_rate} for _ in people]
        shares = build_provider_shares(order.amount, specs)
        split = aggregate_order_split(order.amount, shares,
            order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0)

    def assign(current):
        current.provider, current.provider_account = people[0].user, people[0].account
        current.provider_name_snapshot = people[0].display_name or people[0].account.nickname
        current.escort_mode = Order.EscortMode.DOUBLE
        current.requires_boss_selection = True
        current.commission_rate, current.provider_income = split.commission_rate, split.provider_income
        current.inviter_commission, current.shop_income = split.inviter_commission, split.shop_income
        if personal_evidence is not None:
            current.commission_allocation_pending = False
            current.game_commission_snapshot = personal_evidence[0]
        for index, (profile, share) in enumerate(zip(people, shares)):
            OrderProvider.objects.create(order=current, provider=profile.user, provider_account=profile.account,
                provider_business_cycle=profile.account.business_cycle,
                provider_name_snapshot=profile.display_name, settlement_base=share.settlement_base,
                commission_type=share.commission_type, commission_rate=share.commission_rate,
                commission_fixed=share.commission_fixed, provider_income=share.provider_income,
                game_commission_snapshot=(personal_evidence[index] if personal_evidence is not None
                                          else current.game_commission_snapshot))
        from wallet.refund_recovery_order import enabled as recovery_enabled, reserve_many
        if recovery_enabled():
            reserve_many(people, current)
        mark_escort_busy(provider_ids=[p.user_id for p in people], account_ids=ids)
    assigned = transition(order.pk, Order.Status.GRABBED, operator=people[0].user,
        operator_account=people[0].account, side_effect=assign,
        update_fields=['provider', 'provider_account', 'provider_name_snapshot', 'escort_mode', 'requires_boss_selection', 'commission_rate',
                       'provider_income', 'inviter_commission', 'shop_income', 'commission_allocation_pending',
                       'game_commission_snapshot'], dispatch_account_ids=ids)
    state.status, state.winner = 'ASSIGNED', team
    state.save(update_fields=['status', 'winner', 'updated_at'])
    return assigned


def _reopen(order, state, now):
    from datetime import timedelta
    previous = state.round
    deadline = now + timedelta(seconds=previous.pool.window_seconds)
    total = max(previous.total_deadline, deadline)
    round_ = OrderGrabRound.objects.create(pool=previous.pool, number=previous.number + 1,
        opened_at=now, deadline=deadline, total_deadline=total, simultaneous_visibility=True)
    from .grab_round_opening import stage
    stage(round_)
    OrderGrabTeamRoundState.objects.create(round=round_)
    state.status = 'REOPENED'
    state.save(update_fields=['status', 'updated_at'])
    order.auto_cancel_at = total
    order.save(update_fields=['auto_cancel_at', 'updated_at'])
    transaction.on_commit(lambda: enqueue(order.pk, eta=deadline))


def _notify_result(order, state, teams, *, winner=None, reopened=False):
    from site_messages.grab_cycle import schedule, team_cycle
    recovery = (getattr(settings, 'GRAB_RESULT_NOTICE_RECOVERY_ENABLED', False)
                and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
    intents = []
    for team in teams:
        won = bool(winner and team.pk == winner.pk)
        title = '抢单需重新参加' if reopened else ('抢单成功' if won else '本次未抢到订单')
        preview = (f'「{order.service_name_snapshot}」抢单窗口已重新开放' if reopened
                   else f'「{order.service_name_snapshot}」抢单结果已公布')
        for account_id in (team.captain_id, team.partner_id):
            cycle = team_cycle(team, account_id)
            if recovery:
                intents.append({'account_id': account_id, 'business_cycle': cycle, 'account_type': 'PROVIDER',
                                'team_id': team.pk, 'title': title, 'preview': preview, 'assignment_alert': won})
            else:
                schedule(account_id=account_id, cycle=cycle, title=title, preview=preview,
                         order_id=order.pk, assignment_alert=won, strict_cycle=True)
    if winner:
        if recovery:
            intents.append({'account_id': order.customer_account_id, 'user_id': order.customer_id,
                            'business_cycle': order.business_cycle, 'account_type': 'BOSS',
                            'title': '陪玩已接单', 'preview': f'你的「{order.service_name_snapshot}」已有陪玩接单',
                            'assignment_alert': False})
        else:
            schedule(account_id=order.customer_account_id, recipient_id=order.customer_id, cycle=order.business_cycle,
                     title='陪玩已接单', preview=f'你的「{order.service_name_snapshot}」已有陪玩接单',
                     order_id=order.pk, strict_cycle=True)
    if recovery and intents:
        state.notification_intents = intents
        state.save(update_fields=['notification_intents'])
        from site_messages.grab_team_result_notice import schedule as schedule_recovery
        schedule_recovery(state.pk)


def _cancel_order(order, state, *, receipt=None):
    """Return the original wallet and points assets in the order transition."""
    from .services import refund_to_customer
    from .state_machine import transition
    reason = (receipt.payload.get('reason') if receipt else '') or ('客户取消' if receipt else '超时未接单自动取消')
    refund_fact = {}

    def refund(locked):
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(locked):
            from .points_service_timeout import refund_customer_cancel_locked, refund_expired_locked
            if receipt:
                refund_fact['refund'] = refund_customer_cancel_locked(locked, operator_account=receipt.actor, grab_team_state=state)
            else:
                refund_fact['refund'] = refund_expired_locked(locked, grab_team_state=state)
        else:
            refund_fact['refund'] = refund_to_customer(locked, reason=reason,
                                                       operator_account=receipt.actor if receipt else None)
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


def resolve(order_id, *, _cancel_only=False):
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
            if seal(order_id, team=True) == 'REVIEW':
                return 'WAIT_WATERMARK_REVIEW'
        except ValueError:
            return 'WAIT_WATERMARK_REVIEW'
    if lifecycle and not _cancel_only:
        from .grab_allocation_lifecycle import capture
        snapshot = capture(order_id, team=True)
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
        from .applications import ApplicationError, _enter_assignment_scope
        try:
            _enter_assignment_scope(stack, order)
        except ApplicationError:
            return 'WAIT_POINTS_FUNDING_REVIEW'
        state = (OrderGrabTeamRoundState.objects.select_for_update().select_related('round__pool')
                 .filter(round__pool__order=order).order_by('-round__number').first())
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
        query = OrderGrabReceiptProbe.objects.select_for_update().filter(round=state.round).order_by('pk')
        if not _cancel_only:
            query = query.select_related('actor', 'round__pool__order', 'pass_evidence')
        receipts = list(query)
        relevant = [row for row in receipts if row.round_phase == state.status
                    or (state.status == 'OPEN' and row.round_phase == 'PARKED'
                        and row.kind in ('BOSS_CANCEL', 'WITHDRAW'))]
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
                if not verify(state.round, receipts, observed_at=now, precision_us=precision):
                    return 'WAIT_WATERMARK_REVIEW'
            except ValueError:
                return 'WAIT_WATERMARK_REVIEW'
        if lifecycle:
            from .grab_allocation_lifecycle import accounts_unchanged, matches
            if not matches(snapshot, state, team=True) or not accounts_unchanged(snapshot):
                return 'WAIT_LIFECYCLE_RETRY'
        for row in relevant:
            if row.kind == 'WITHDRAW' and classified[row.pk] == 'BEFORE':
                if not OrderGrabTeamDraft.objects.filter(pk=row.payload.get('team_id'), round=state.round,
                                                          withdrawal_receipt=row, status='WITHDRAWN').exists():
                    from .grab_team_draft import withdraw_with_receipt
                    try:
                        withdraw_with_receipt(state.round_id, row.payload.get('team_id'), row.pk)
                    except ValueError:
                        # Do not guess which team or earlier confirmation this
                        # receipt applies to; retain it for a real review.
                        return 'WAIT_APPLY'
        if state.status == 'OPEN' and now - timedelta(microseconds=precision) <= window_upper:
            return 'WAIT'
        from django.db.models import Prefetch, Q
        from .models import OrderGrabTeamDraftMember, OrderGrabReceiptPassEvidence
        team_status = Q(status='UNVERIFIED')
        if state.status == 'OPEN':
            team_status |= Q(status='WITHDRAWN', withdrawal_receipt__round_phase='PARKED')
        # Prefetches are separate SELECTs; make each a current read as well,
        # under the already held order and receipt locks.
        current_evidence = OrderGrabReceiptPassEvidence.objects.select_for_update().select_related('receipt')
        current_members = OrderGrabTeamDraftMember.objects.select_for_update().prefetch_related(
            Prefetch('receipt_pass_evidence', queryset=current_evidence))
        teams = list(OrderGrabTeamDraft.objects.select_for_update().filter(team_status, round=state.round)
                     .prefetch_related(Prefetch('members', queryset=current_members)).order_by('pk'))
        eligible = []
        for team in teams:
            members = {member.account_id: member for member in team.members.all()}
            if set(members) != {team.captain_id, team.partner_id}:
                if lifecycle:
                    return 'WAIT_LIFECYCLE_FACTS'
                continue
            evidence = [members[pk].receipt_pass_evidence for pk in (team.captain_id, team.partner_id)]
            if not all(evidence):
                # An incomplete invitation may have been withdrawn after the
                # OPEN seal; it never became a first-window candidate.
                if team.status == 'WITHDRAWN':
                    continue
                if lifecycle:
                    return 'WAIT_LIFECYCLE_FACTS'
                continue
            first, second = [item.receipt for item in evidence]
            if (second.round_phase == state.status and _effective_state(first, evidence[0]) == 'BEFORE'
                    and _effective_state(second, evidence[1]) == 'BEFORE'):
                eligible.append((team, second))
        if not eligible:
            if state.status == 'OPEN' and any(
                    row.round_phase == 'PARKED' and row.kind == 'CONFIRM'
                    and _effective_state(row, getattr(row, 'pass_evidence', None)) in ('BEFORE', 'PENDING', 'REVIEW')
                    for row in receipts):
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
            eligible_membership = {}
            for team, _ in eligible:
                valid_membership = True
                for member in team.members.all():
                    evidence = member.receipt_pass_evidence
                    receipt = evidence.receipt if evidence else None
                    expected_kind = 'INVITE' if member.account_id == team.captain_id else 'CONFIRM'
                    expected_payload = ({'partner_id': team.partner_id} if expected_kind == 'INVITE'
                                        else {'team_id': team.pk})
                    if (not receipt or receipt.actor_id != member.account_id or receipt.round_id != state.round_id
                            or receipt.kind != expected_kind or receipt.payload != expected_payload
                            or not actor_membership_evidence_present(receipt)):
                        return 'WAIT_LIFECYCLE_FACTS'
                    valid_membership = locked_actor_membership_matches(receipt) and valid_membership
                eligible_membership[team.pk] = valid_membership
        if state.status == 'OPEN':
            from .grab_metric_snapshot import MetricSnapshotUnavailable
            from .grab_bound_ranking import snapshot_data
            try:
                result = preview(state.round_id, [team.pk for team, _ in eligible])
            except MetricSnapshotUnavailable:
                return 'WAIT_METRIC_SNAPSHOT_RETRY'
            if result.status != 'POTENTIAL_WINNER':
                from .grab_review import mark_round_review
                mark_round_review(state, '候选排序时间或指标待核对')
                return 'REVIEW'
            chosen = next(team for team, _ in eligible if team.pk == result.winner_id)
            state.ranking_snapshot = snapshot_data(result)
            state.save(update_fields=['ranking_snapshot', 'updated_at'])
            try:
                if lifecycle and not eligible_membership[chosen.pk]:
                    raise GrabAdmissionError('陪玩门店成员身份已变化')
                assigned = _assign(order, state, chosen)
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
                _notify_result(order, state, [team for team, _ in eligible], reopened=True)
                return 'REOPENED'
            except ValueError as exc:
                from .grab_review import mark_round_review
                mark_round_review(state, str(exc)[:255])
                return 'REVIEW'
        else:
            from .services import lock_provider_dispatch
            from .grab_parked_eligibility import reason as parked_ineligibility
            from users.models import EscortProfile
            ids = sorted({account_id for team, _ in eligible for account_id in (team.captain_id, team.partner_id)})
            lock_provider_dispatch(ids)
            profiles = {profile.account_id: profile for profile in
                        EscortProfile.objects.select_for_update().select_related('account', 'user')
                        .filter(account_id__in=ids).order_by('account_id', 'pk')}
            for candidate in list(eligible):
                team, _ = candidate
                if any(account_id not in profiles for account_id in (team.captain_id, team.partner_id)):
                    continue  # Missing facts remain for the existing review path.
                try:
                    why = next((value for account_id in sorted((team.captain_id, team.partner_id))
                                if (value := parked_ineligibility(profiles[account_id], membership_valid=(
                                    eligible_membership[team.pk] if lifecycle else True)))), '')
                except ValueError as exc:
                    from .grab_review import mark_round_review
                    mark_round_review(state, str(exc))
                    return 'REVIEW'
                if why:
                    team.occupied_seats.all().delete()
                    team.status, team.disqualification_reason = 'DISQUALIFIED', why[:255]
                    team.save(update_fields=['status', 'disqualification_reason', 'updated_at'])
                    eligible.remove(candidate)
                    from site_messages.grab_disqualification_notice import record_team
                    record_team(order, team)
            while eligible:
                winner = eligible[0]
                for candidate in eligible[1:]:
                    comparison = compare_candidate_bounds(winner[1].pk, candidate[1].pk)
                    if comparison == 'REVIEW':
                        from .grab_review import mark_round_review
                        mark_round_review(state, '留池队伍提交时间待核对')
                        return 'REVIEW'
                    if comparison == 'SECOND_EARLIER':
                        winner = candidate
                chosen = winner[0]
                try:
                    if lifecycle and not eligible_membership[chosen.pk]:
                        raise GrabAdmissionError('陪玩门店成员身份已变化')
                    assigned = _assign(order, state, chosen)
                    break
                except GrabAdmissionError as exc:
                    chosen.occupied_seats.all().delete()
                    chosen.status, chosen.disqualification_reason = 'DISQUALIFIED', str(exc)[:255]
                    chosen.save(update_fields=['status', 'disqualification_reason', 'updated_at'])
                    from site_messages.grab_disqualification_notice import record_team
                    record_team(order, chosen)
                    eligible.remove(winner)
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
        _notify_result(order, state, [team for team, _ in eligible], winner=chosen)
        transaction.on_commit(lambda: notify_order_update(assigned))
        return 'ASSIGNED'
