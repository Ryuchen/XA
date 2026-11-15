"""Durable notices for committed negotiation proposals and decisions."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'NEGOTIATION_PROPOSAL_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record_all(proposal, order, scope, outcome):
    from .models import NegotiationProposalNotice
    from .order_cycle import recipient_cycle
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('协商通知必须与真实方案同事务保存')
    recipients = [(order.customer_id, order.customer_account_id, 'BOSS')]
    user_ids = dict(order.providers.filter(provider_account_id__in=scope).values_list('provider_account_id', 'provider_id'))
    recipients.extend((user_ids.get(account_id), account_id, 'PROVIDER') for account_id in scope)
    for user_id, account_id, role in recipients:
        if not account_id:
            continue
        cycle = recipient_cycle(order, account_id)
        expected = {'legacy_user_id': user_id, 'role': role, 'business_cycle': cycle}
        intent, created = NegotiationProposalNotice.objects.get_or_create(proposal=proposal,
            account_id=account_id, outcome=outcome, defaults=expected)
        if not created and any(getattr(intent, key) != value for key, value in expected.items()):
            raise ValueError('协商通知来源不一致')
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from orders.models import Order, NegotiationProposal, NegotiationDecision
    from .models import Message, NegotiationProposalNotice
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle
    initial = NegotiationProposalNotice.objects.filter(pk=intent_id).values('account_id', 'proposal_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = NegotiationProposal.objects.filter(pk=initial['proposal_id']).values_list('case__order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        proposal = NegotiationProposal.objects.select_for_update().filter(pk=initial['proposal_id'], case__order=order).first() if order else None
        intent = NegotiationProposalNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], proposal_id=initial['proposal_id']).first()
        if not intent or intent.status != 'PENDING':
            return
        cycle = intent.business_cycle
        if type(cycle) is not int or cycle <= 0:
            intent.status, intent.note = 'REVIEW', '原订单收件周期缺失或冲突，待核对'
        else:
            decision = NegotiationDecision.objects.filter(proposal=proposal, account_id=intent.account_id,
                role=intent.role).first() if proposal else None
            mapping = (intent.legacy_user_id is None or LegacyAccountMap.objects.filter(
                account_id=intent.account_id, legacy_user_id=intent.legacy_user_id).exists())
            latest = bool(proposal and not NegotiationProposal.objects.filter(case_id=proposal.case_id,
                version__gt=proposal.version).exists())
            expected = {'CREATED': 'PENDING', 'ACCEPTED': 'ACCEPTED', 'REJECTED': 'REJECTED'}.get(intent.outcome)
            valid = bool(order and proposal and latest and expected and proposal.status == expected and decision
                and (intent.role != 'BOSS' or order.customer_account_id == intent.account_id)
                and (intent.role != 'PROVIDER' or intent.account_id in proposal.affected_accounts)
                and recipient_cycle(order, intent.account_id) == cycle and mapping and account
                and account.account_type == intent.role and account.business_cycle == cycle
                and account.license_membership_active)
            if intent.outcome == 'CREATED' and decision and decision.accepted is not None:
                valid = False
            if intent.outcome == 'ACCEPTED' and proposal:
                from orders.models import NegotiationExecution, PointsNegotiationPosting
                if (NegotiationExecution.objects.filter(proposal=proposal).exists()
                        or PointsNegotiationPosting.objects.filter(proposal=proposal).exists()):
                    valid = False
            if not valid:
                intent.status, intent.note = 'SKIPPED', '方案、收件资格或原账号周期已变化'
            else:
                title, preview = {
                    'CREATED': ('订单协商方案待确认', '客服已提交协商方案，请查看订单并确认。'),
                    'ACCEPTED': ('订单协商方案已确认', '各方已同意本次方案，请等待客服处理；退款结果以实际流水为准。'),
                    'REJECTED': ('订单协商方案有异议', '本次方案未达成一致，请查看订单并与客服沟通。'),
                }[intent.outcome]
                intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                    recipient_business_cycle=cycle, type='ORDER', title=title, preview=preview,
                    related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}')
                notify_messages_after_commit(account.pk, business_cycle=cycle)
                intent.status = 'SENT'
        intent.completed_at = timezone.now()
        intent.save(update_fields=['status', 'note', 'message', 'completed_at'])
