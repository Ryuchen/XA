"""Send pending add-on refund agreements to their actual parties."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'ADDON_REFUND_PROPOSAL_NOTICE_RECOVERY_ENABLED', False))


def _person_segments(person):
    entries = person.share_plan
    if not isinstance(entries, list):
        return []
    return sorted({item['segment_id'] for item in entries if isinstance(item, dict)
                   and type(item.get('segment_id')) is int and (item.get('refund_basis') or item.get('burden'))})


def record_all(proposal):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购退款方案通知须与方案同事务保存')
    from orders.models import OrderParticipationSegment
    from .models import AddonRefundProposalNotice
    order = proposal.payment.intent.order
    targets = [('BOSS', proposal.payment.intent.buyer_id, order.business_cycle, [])]
    for person in proposal.participants.all():
        ids = _person_segments(person)
        segments = list(OrderParticipationSegment.objects.filter(pk__in=ids,
            attribution__order=order, provider_account_id=person.account_id))
        cycles = {row.provider_business_cycle for row in segments}
        cycle = next(iter(cycles)) if ids and len(segments) == len(ids) and len(cycles) == 1 else None
        targets.append(('PROVIDER', person.account_id, cycle, ids))
    for role, account_id, cycle, ids in targets:
        notice, created = AddonRefundProposalNotice.objects.get_or_create(proposal=proposal, account_id=account_id,
            defaults={'role': role, 'business_cycle': cycle, 'segment_ids': ids})
        if not created and (notice.role != role or notice.business_cycle != cycle or notice.segment_ids != ids):
            raise ValueError('加购退款方案通知来源不一致')
        transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def refresh_delivered(proposal):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购退款方案消息须与决定同事务更新')
    from .models import AddonRefundProposalNotice
    from .notifier import notify_messages_after_commit
    decisions = {row.account_id: row.accepted for row in proposal.decisions.all()}
    for notice in AddonRefundProposalNotice.objects.select_for_update().filter(
            proposal=proposal, status='SENT', message__isnull=False).select_related('message'):
        if proposal.status == 'ACCEPTED':
            title, preview = '加购退款方案已确认', '各方已确认方案，请查看订单处理进度。'
        elif proposal.status == 'REJECTED':
            title, preview = '加购退款方案未通过', '方案未通过，请查看订单。'
        elif proposal.status == 'SUPERSEDED':
            title, preview = '加购退款方案已更新', '原方案已失效，请查看订单当前状态。'
        elif decisions.get(notice.account_id) is not None:
            title, preview = '已处理加购退款方案', '你的决定已记录，请查看订单。'
        else:
            continue
        notice.message.title, notice.message.preview = title, preview
        notice.message.save(update_fields=['title', 'preview'])
        notify_messages_after_commit(notice.account_id, business_cycle=notice.business_cycle)


def deliver(notice_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, AfterSale, OrderAddonPayment, OrderAddonRefundProposal, OrderParticipationSegment
    from orders.addon_payment import verify_receipt
    from orders.addon_refund import _participants, _source_plan
    from orders.addon_refund_cross_plan import require_current
    from .models import AddonRefundProposalNotice, Message
    from .notifier import notify_messages_after_commit
    initial = AddonRefundProposalNotice.objects.filter(pk=notice_id).values('account_id', 'proposal_id',
        'proposal__payment_id', 'proposal__case_id', 'proposal__payment__intent__order_id').first()
    if not initial:
        return
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=initial['proposal__payment__intent__order_id']).first()
        case = AfterSale.objects.select_for_update().filter(pk=initial['proposal__case_id'], order=order).first() if order else None
        payment = OrderAddonPayment.objects.select_for_update().select_related('intent', 'buyer_transaction__wallet').filter(
            pk=initial['proposal__payment_id'], intent__order=order).first() if order else None
        proposal = OrderAddonRefundProposal.objects.select_for_update().filter(pk=initial['proposal_id'],
            payment=payment, case=case).first() if payment and case else None
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        notice = AddonRefundProposalNotice.objects.select_for_update().filter(pk=notice_id).first()
        if not notice or notice.status != 'PENDING':
            return
        if not proposal or proposal.status != 'PENDING' or case.status != 'OPEN':
            notice.status, notice.note = 'SKIPPED', '方案或售后已结束'
        else:
            decisions = list(proposal.decisions.all())
            people = list(proposal.participants.order_by('account_id'))
            own_decision = next((row for row in decisions if row.account_id == notice.account_id and row.role == notice.role), None)
            ids = notice.segment_ids
            source_cycle = order.business_cycle if notice.role == 'BOSS' else None
            segments = list(OrderParticipationSegment.objects.filter(pk__in=ids if isinstance(ids, list) else [],
                attribution__order=order, provider_account_id=notice.account_id))
            if notice.role == 'PROVIDER' and ids and len(segments) == len(ids):
                cycles = {row.provider_business_cycle for row in segments}
                if len(cycles) == 1:
                    source_cycle = next(iter(cycles))
            person = next((row for row in people if row.account_id == notice.account_id), None)
            identity = (notice.account_id == payment.intent.buyer_id and not ids if notice.role == 'BOSS'
                        else bool(notice.role == 'PROVIDER' and person and ids == _person_segments(person)))
            expected = {(payment.intent.buyer_id, 'BOSS')} | {(row.account_id, 'PROVIDER') for row in people}
            valid = bool(identity and own_decision and proposal.case_opened_at == case.created_at
                and proposal.case_request_id == case.client_request_id and type(notice.business_cycle) is int
                and notice.business_cycle > 0 and notice.business_cycle == source_cycle
                and payment.intent.buyer_id == order.customer_account_id and case.account_id == order.customer_account_id
                and len(decisions) == len(expected) and {(row.account_id, row.role) for row in decisions} == expected
                and all((row.accepted is None) == (row.decided_at is None) for row in decisions))
            if valid:
                try:
                    verify_receipt(payment)
                    saved = [{'account_id': row.account_id, 'basis': row.basis, 'burden': row.burden,
                              'excess_burden': row.excess_burden, 'share_plan': row.share_plan} for row in people]
                    valid = (proposal.source_plan == _source_plan(payment, proposal.amount)
                             and saved == _participants(payment, case, proposal.amount, [row.account_id for row in people]))
                    if valid:
                        require_current(proposal, saved)
                except ValidationError:
                    valid = False
            if not valid:
                notice.status, notice.note = 'REVIEW', '方案、责任份额或原业务周期待核对'
            elif not account or account.account_type != notice.role or not account.license_membership_active or account.business_cycle != notice.business_cycle:
                notice.status, notice.note = 'SKIPPED', '原账号业务周期已变化'
            elif own_decision.accepted is not None:
                notice.status, notice.note = 'SKIPPED', '本人已处理该方案'
            else:
                notice.message = Message.objects.create(recipient_account=account,
                    recipient_business_cycle=notice.business_cycle, type='ORDER', title='加购退款方案待确认',
                    preview=f'请查看并确认「{payment.intent.addon_name}」的退款方案。'[:255],
                    related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}')
                notice.status = 'SENT'
                notify_messages_after_commit(account.pk, business_cycle=notice.business_cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])
