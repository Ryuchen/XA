"""Idempotently project a committed, cycle-bound team invitation."""
import logging

from stores.db import transaction

log = logging.getLogger(__name__)


def deliver(receipt_id):
    from club_accounts.models import ClubAccount
    from orders.models import OrderGrabReceiptProbe, OrderGrabTeamDraft
    from .models import GrabInviteNoticeDelivery, Message
    from .notifier import notify_messages_after_commit

    initial = OrderGrabReceiptProbe.objects.filter(pk=receipt_id).values(
        'kind', 'state', 'payload', 'invitee_business_cycle', 'actor_business_cycle', 'actor_cycle_guarded', 'round_id', 'actor_id', 'request_key').first()
    if (not initial or initial['kind'] != 'INVITE' or initial['state'] != 'BEFORE'
            or not isinstance(initial['payload'], dict)
            or type(initial['payload'].get('partner_id')) is not int):
        return
    target_id, cycle = initial['payload']['partner_id'], initial['invitee_business_cycle']
    with transaction.atomic():
        # Use the order -> account -> team -> receipt direction used by the
        # allocator, and never infer a missing invitation cycle at delivery.
        from orders.grab_receipt_probe import _db_clock, _effective_state, _lock_round
        order, round_ = _lock_round(initial['round_id'])
        if not initial['actor_cycle_guarded'] or type(cycle) is not int or cycle <= 0:
            receipt = OrderGrabReceiptProbe.objects.select_for_update().filter(pk=receipt_id, round=round_).first()
            if receipt:
                delivery, created = GrabInviteNoticeDelivery.objects.get_or_create(receipt=receipt)
                if created:
                    delivery.skipped_reason = '原邀请或受邀人周期待核对'
                    delivery.save(update_fields=['skipped_reason'])
            return
        # Lock both real participants in stable order; the invitation must
        # not survive the captain's departure and re-entry either.
        accounts = {row.pk: row for row in ClubAccount.objects.select_for_update().filter(
            pk__in=[initial['actor_id'], target_id], account_type='PROVIDER').order_by('pk')}
        actor, account = accounts.get(initial['actor_id']), accounts.get(target_id)
        if account and (not account.license_membership_active or account.business_cycle != cycle):
            account = None
        actor_cycle = initial['actor_business_cycle']
        if (not actor or not actor.is_active or not actor.can_login or not actor.license_membership_active
                or type(actor_cycle) is not int or actor_cycle <= 0 or actor.business_cycle != actor_cycle):
            actor = None
        team = OrderGrabTeamDraft.objects.select_for_update().filter(round=round_, captain_id=initial['actor_id'],
            partner_id=target_id, request_key=initial['request_key'], status='INVITING').first()
        receipts = OrderGrabReceiptProbe.objects.select_for_update().select_related(
            'round__pool__order').filter(pk=receipt_id, kind='INVITE', state='BEFORE')
        receipts = receipts.filter(actor_cycle_guarded=True, invitee_business_cycle=cycle)
        receipt = receipts.first()
        if (not receipt or receipt.payload != initial['payload'] or receipt.round_id != initial['round_id']
                or receipt.actor_id != initial['actor_id'] or receipt.request_key != initial['request_key']):
            return
        if _effective_state(receipt, getattr(receipt, 'pass_evidence', None)) != 'BEFORE':
            return
        from orders.grab_customer_review import has_pending_review
        if has_pending_review(order.pk, lock=True):
            GrabInviteNoticeDelivery.objects.get_or_create(receipt=receipt, defaults={
                'skipped_reason': '订单取消申请待核对，邀请未发送',
            })
            return
        from orders.grab_round_facts import current_pending_round_state, paid_pending_grab_order
        from orders.models import OrderGrabRound
        state = current_pending_round_state(order, round_.pk)
        if state is None and paid_pending_grab_order(order) and not OrderGrabRound.objects.select_for_update().filter(
                pool_id=round_.pool_id, number__gt=round_.number).exists():
            return
        if state and state.status == 'REVIEW':
            return
        live_state = bool(state and state.status in ('OPEN', 'PARKED'))
        from datetime import timedelta
        from orders.grab_clock_contract import production_enabled, round_identity_valid
        definitely_expired = False
        if live_state and actor and account and team:
            now, precision, _ = _db_clock()
            lower_cutoff = upper_cutoff = round_.total_deadline
            if production_enabled():
                try:
                    from orders.grab_round_opening import cutoff_bounds
                    if not round_identity_valid(round_, lock=True):
                        return
                    lower_cutoff, upper_cutoff = cutoff_bounds(round_, 'PARKED', lock=True)
                except ValueError:
                    return
            definitely_live = now + timedelta(microseconds=precision) < lower_cutoff
            definitely_expired = now - timedelta(microseconds=precision) >= upper_cutoff
            if not definitely_live and not definitely_expired:
                return  # The clock interval straddles the deadline; retry.
        delivery, created = GrabInviteNoticeDelivery.objects.get_or_create(receipt=receipt)
        if not created:
            return
        if not actor or not account or not team or not live_state or definitely_expired:
            delivery.skipped_reason = '原邀请已结束或收件人周期已变化'
            delivery.save(update_fields=['skipped_reason'])
            return
        delivery.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
            type='ORDER', title='收到双陪组队邀请', preview='一位陪玩邀请你组队，请在可见时间后确认。')
        delivery.save(update_fields=['message'])
        notify_messages_after_commit(account.pk, business_cycle=cycle)


def schedule(receipt_id):
    transaction.on_commit(lambda: deliver(receipt_id), robust=True)
