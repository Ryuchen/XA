"""Recover committed team-grab results without repeating allocation."""
import logging

from django.utils import timezone
from stores.db import transaction

log = logging.getLogger(__name__)


def _deliver_one(state_id, position, intent):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderGrabTeamDraft, OrderGrabTeamRoundState
    from .models import GrabTeamResultNoticeDelivery, Message
    from .notifier import notify_messages_after_commit

    hint = OrderGrabTeamRoundState.objects.filter(pk=state_id).values('round__pool__order_id').first()
    if not hint:
        return
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=hint['round__pool__order_id']).first()
        account_id = intent.get('account_id') if isinstance(intent, dict) else None
        account = (ClubAccount.objects.select_for_update().filter(pk=account_id).first()
                   if type(account_id) is int and account_id > 0 else None)
        state = OrderGrabTeamRoundState.objects.select_for_update().filter(pk=state_id,
            status__in=('ASSIGNED', 'REOPENED')).first()
        if (not state or not isinstance(state.notification_intents, list)
                or position >= len(state.notification_intents) or state.notification_intents[position] != intent):
            return
        delivery, created = GrabTeamResultNoticeDelivery.objects.get_or_create(state=state, position=position)
        if not created:
            return
        cycle = intent.get('business_cycle') if isinstance(intent, dict) else None
        role = intent.get('account_type') if isinstance(intent, dict) else None
        source_ok = False
        if role == 'PROVIDER':
            from .grab_cycle import team_cycle
            team = OrderGrabTeamDraft.objects.filter(pk=intent.get('team_id'), round_id=state.round_id).first()
            source_ok = bool(team and account_id in (team.captain_id, team.partner_id)
                and team_cycle(team, account_id) == cycle
                and bool(intent.get('assignment_alert')) == bool(state.winner_id == team.pk))
        elif role == 'BOSS':
            source_ok = bool(order and state.status == 'ASSIGNED' and state.winner_id
                and order.customer_account_id == account_id and order.customer_id == intent.get('user_id')
                and order.business_cycle == cycle)
        if (not source_ok or not account or type(cycle) is not int or cycle <= 0
                or account.account_type != role or not account.license_membership_active
                or account.business_cycle != cycle or not order):
            delivery.skipped_reason = '原抢单来源不可核验或收件账号周期已变化'
            delivery.save(update_fields=['skipped_reason'])
            return
        if state.status == 'ASSIGNED' and (role == 'BOSS' or intent.get('assignment_alert') is True):
            from .grab_cycle import team_cycle
            winner = OrderGrabTeamDraft.objects.filter(pk=state.winner_id, round_id=state.round_id).first()
            members = {winner.captain_id, winner.partner_id} if winner else set()
            assignments = list(order.providers.filter(released_at__isnull=True).values_list(
                'provider_account_id', 'provider_business_cycle'))
            logs = list(order.status_logs.filter(created_at__lte=state.updated_at).order_by('-pk')[:1])
            proof = bool(winner and len(members) == 2 and len(logs) == 1
                         and logs[0].action == 'GRAB' and logs[0].from_status == 'PENDING'
                         and logs[0].to_status == 'GRABBED' and logs[0].operator_account_id == winner.captain_id
                         and len(assignments) == 2 and {item[0] for item in assignments} == members
                         and all(team_cycle(winner, member_id) == member_cycle and member_cycle
                                 for member_id, member_cycle in assignments))
            changed = bool(logs and order.status_logs.filter(pk__gt=logs[0].pk,
                action__in=('REJECT', 'TRANSFER', 'CANCEL', 'REFUND')).exists())
            if (not proof or changed or order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE)
                    or order.payment_status != Order.PaymentStatus.PAID
                    or (role == 'PROVIDER' and order.status != Order.Status.GRABBED)):
                delivery.skipped_reason = '分配事实、原席位或当前订单状态待核对'
                delivery.save(update_fields=['skipped_reason'])
                return
        elif state.status == 'REOPENED':
            latest = OrderGrabTeamRoundState.objects.filter(round__pool_id=state.round.pool_id).order_by(
                '-round__number').first()
            if (order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID
                    or not latest or latest.round.number <= state.round.number
                    or latest.status not in ('OPEN', 'PARKED')):
                delivery.skipped_reason = '重开轮次已结束或订单状态已变化'
                delivery.save(update_fields=['skipped_reason'])
                return
        delivery.message = Message.objects.create(recipient_account=account, recipient_id=intent.get('user_id'),
            recipient_business_cycle=cycle, type='ORDER', title=str(intent.get('title', ''))[:100],
            preview=str(intent.get('preview', ''))[:255], related_order_id=order.pk,
            assignment_alert=intent.get('assignment_alert') is True)
        delivery.save(update_fields=['message'])
        notify_messages_after_commit(account.pk, business_cycle=cycle,
            assignment_message_id=delivery.message_id if intent.get('assignment_alert') is True else None)


def deliver(state_id):
    from orders.models import OrderGrabTeamRoundState
    from .models import GrabTeamResultNoticeBatch, GrabTeamResultNoticeDelivery

    state = OrderGrabTeamRoundState.objects.filter(pk=state_id, status__in=('ASSIGNED', 'REOPENED')).first()
    intents = state.notification_intents if state else None
    if not isinstance(intents, list) or not intents:
        return
    for position, intent in enumerate(intents):
        try:
            _deliver_one(state_id, position, intent)
        except Exception:
            log.exception('抢单结果通知待恢复 state=%s position=%s', state_id, position)
    try:
        with transaction.atomic():
            state = OrderGrabTeamRoundState.objects.select_for_update().filter(pk=state_id).first()
            if not state or state.notification_intents != intents:
                return
            positions = set(GrabTeamResultNoticeDelivery.objects.filter(state=state).values_list('position', flat=True))
            if positions == set(range(len(intents))):
                batch, _ = GrabTeamResultNoticeBatch.objects.get_or_create(state=state)
                if not batch.completed_at:
                    batch.completed_at = timezone.now()
                    batch.save(update_fields=['completed_at'])
    except Exception:
        log.exception('抢单结果通知批次待恢复 state=%s', state_id)


def schedule(state_id):
    transaction.on_commit(lambda: deliver(state_id), robust=True)
