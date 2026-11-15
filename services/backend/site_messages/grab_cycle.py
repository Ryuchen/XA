"""Resolve grab-notice recipients from guarded submission receipts."""


def receipt_cycle(receipt, account_id, *, round_id, kind):
    if (not receipt or receipt.actor_id != account_id or receipt.round_id != round_id
            or receipt.kind != kind or not receipt.actor_cycle_guarded
            or type(receipt.actor_business_cycle) is not int or receipt.actor_business_cycle <= 0):
        return None
    return receipt.actor_business_cycle


def team_cycle(team, account_id):
    member = team.members.filter(account_id=account_id).select_related('receipt_pass_evidence__receipt').first()
    evidence = member.receipt_pass_evidence if member else None
    receipt = evidence.receipt if evidence else None
    kind = 'INVITE' if account_id == team.captain_id else 'CONFIRM'
    expected = {'partner_id': team.partner_id} if kind == 'INVITE' else {'team_id': team.pk}
    if not receipt or receipt.payload != expected:
        return None
    return receipt_cycle(receipt, account_id, round_id=team.round_id, kind=kind)


def schedule(*, account_id, cycle, title, preview, order_id, recipient_id=None, assignment_alert=False,
             strict_cycle=False):
    from django.conf import settings
    from stores.db import transaction
    from .utils import create_cycle_message, create_message
    writer = (create_cycle_message if strict_cycle or getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
              else create_message)
    def send():
        message = writer(recipient_id=recipient_id, recipient_account_id=account_id,
            recipient_business_cycle=cycle, title=title, preview=preview, msg_type='ORDER',
            related_order_id=order_id, assignment_alert=assignment_alert)
        if strict_cycle and message is None:
            import logging
            logging.getLogger(__name__).warning('抢单结果通知未投递，原周期待核对 order=%s account=%s',
                                                order_id, account_id)
    transaction.on_commit(send, robust=True)
