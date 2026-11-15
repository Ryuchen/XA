"""Store-local, private reminders for negotiation proposal changes."""
from stores.db import transaction


def schedule_proposal_notices(order, scope, outcome, *, proposal):
    from site_messages.negotiation_proposal_notice import enabled, record_all
    if enabled():
        record_all(proposal, order, scope, outcome)
        return
    from django.conf import settings
    from .services import push_message_safe
    from site_messages.order_cycle import recipient_cycle
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)

    text = {
        'CREATED': ('订单协商方案待确认', '客服已提交协商方案，请查看订单并确认。'),
        'ACCEPTED': ('订单协商方案已确认', '各方已同意本次方案，请等待客服处理；退款结果以实际流水为准。'),
        'REJECTED': ('订单协商方案有异议', '本次方案未达成一致，请查看订单并与客服沟通。'),
    }
    title, preview = text[outcome]
    recipients = [(order.customer_id, order.customer_account_id)]
    recipients.extend(order.providers.filter(provider_account_id__in=scope)
                      .values_list('provider_id', 'provider_account_id'))
    seen = set()
    for user_id, account_id in recipients:
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        cycle = recipient_cycle(order, account_id) if guarded else None
        if guarded and not cycle:
            import logging
            logging.getLogger(__name__).warning('协商通知原周期待核对 order=%s account=%s', order.pk, account_id)
            continue
        transaction.on_commit(lambda user_id=user_id, account_id=account_id, cycle=cycle:
            push_message_safe(recipient_id=user_id, recipient_account_id=account_id,
                title=title, preview=preview, msg_type='ORDER', related_order_id=order.pk,
                recipient_business_cycle=cycle, require_cycle=guarded))
