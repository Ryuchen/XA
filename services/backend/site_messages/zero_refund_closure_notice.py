"""Project actual zero-refund agreement closure to its confirmed parties."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ZERO_REFUND_CLOSURE_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def prepare(order, case):
    from orders.models import NegotiationExecution, PointsZeroFollowupTermination, SubsidyAgreementExecution
    if case.status != 'RESOLVED' or not case.resolved_at or not isinstance(case.history, list) or not case.history:
        return None
    event = case.history[-1]
    if (not isinstance(event, dict) or event.get('status') != 'RESOLVED'
            or event.get('resolved_at') != case.resolved_at.isoformat()):
        return None
    terms = dict(proposal__case=case, proposal__case_opened_at=case.created_at,
                 proposal__case_request_id=case.client_request_id, proposal__status='ACCEPTED',
                 proposal__refund_units=0)
    found = []
    found += [('NORMAL', row) for row in NegotiationExecution.objects.filter(
        **terms, zero_order=order, refund__isnull=True, customer_transaction__isnull=True,
        termination_processed_at__isnull=False).select_related('proposal')[:2]]
    found += [('POINTS', row) for row in PointsZeroFollowupTermination.objects.filter(
        **terms, order=order, terminated_at__isnull=False).select_related('proposal')[:2]]
    found += [('SUBSIDY', row) for row in SubsidyAgreementExecution.objects.filter(
        **terms, order=order, processed_at__isnull=False).select_related('proposal')[:2]]
    if len(found) != 1:
        return None
    proposal = found[0][1].proposal
    people = proposal.affected_accounts
    if (not isinstance(people, list) or not people or any(type(pk) is not int or pk <= 0 for pk in people)
            or len(set(people)) != len(people) or order.customer_account_id in people):
        return None
    return found[0]


def record_all(order, case, source):
    from .aftersale_closure_notice import _fingerprint
    from .models import ZeroRefundClosureNotice
    from .order_cycle import recipient_cycle
    kind, execution = source
    proposal = execution.proposal
    recipients = [(order.customer_account_id, 'BOSS', order.business_cycle)]
    recipients += [(account_id, 'PROVIDER', recipient_cycle(order, account_id))
                   for account_id in proposal.affected_accounts]
    for account_id, role, cycle in recipients:
        if not account_id:
            continue
        title = '协商结案已完成'
        preview = ('本次协商已结案，无退款，请查看订单处理结果。' if role == 'BOSS'
                   else '本次协商已结案，无退款；收益及后续结算请查看订单和钱包流水。')
        expected = {'proposal_id': proposal.pk, 'source_kind': kind, 'source_id': execution.pk,
                    'history_fingerprint': _fingerprint(case.history[-1]), 'role': role, 'business_cycle': cycle,
                    'title': title, 'preview': preview}
        intent, created = ZeroRefundClosureNotice.objects.get_or_create(case=case, history_index=len(case.history)-1,
                                                                         account_id=account_id, defaults=expected)
        if not created and any(getattr(intent, key) != value for key, value in expected.items()):
            raise ValueError('零退款协商通知来源不一致')
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import (AfterSale, Order, NegotiationExecution, PointsZeroFollowupTermination,
                               SubsidyAgreementExecution, NegotiationProposal)
    from .aftersale_closure_notice import _fingerprint
    from .models import Message, ZeroRefundClosureNotice
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = ZeroRefundClosureNotice.objects.filter(pk=intent_id).values('account_id', 'case_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = AfterSale.objects.filter(pk=initial['case_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        case = AfterSale.objects.select_for_update().filter(pk=initial['case_id'], order=order).first() if order else None
        intent = ZeroRefundClosureNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], case_id=initial['case_id']).first()
        if not intent or intent.completed_at:
            return
        proposal = NegotiationProposal.objects.filter(pk=intent.proposal_id, case=case).first() if case else None
        source_exists = False
        if proposal and order:
            if intent.source_kind == 'NORMAL':
                source_exists = NegotiationExecution.objects.filter(pk=intent.source_id, proposal=proposal,
                    zero_order=order, refund__isnull=True, customer_transaction__isnull=True,
                    termination_processed_at__isnull=False).exists()
            elif intent.source_kind == 'POINTS':
                source_exists = PointsZeroFollowupTermination.objects.filter(pk=intent.source_id, proposal=proposal,
                    order=order, terminated_at__isnull=False).exists()
            elif intent.source_kind == 'SUBSIDY':
                source_exists = SubsidyAgreementExecution.objects.filter(pk=intent.source_id, proposal=proposal,
                    order=order, processed_at__isnull=False).exists()
        entry = (case.history[intent.history_index] if case and isinstance(case.history, list)
                 and intent.history_index < len(case.history) else None)
        people = proposal.affected_accounts if proposal else None
        expected = ({(order.customer_account_id, 'BOSS')} | {(pk, 'PROVIDER') for pk in people}
                    if order and isinstance(people, list) and all(type(pk) is int and pk > 0 for pk in people) else set())
        decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at')) if proposal else []
        agreed = bool(expected and len(decisions) == len(expected)
            and {(pk, role) for pk, role, _, _ in decisions} == expected
            and all(value is True and decided for _, _, value, decided in decisions)
            and proposal.accepted_at == max(decided for _, _, _, decided in decisions))
        cycle = intent.business_cycle
        valid = bool(source_exists and agreed and proposal.status == 'ACCEPTED' and proposal.refund_units == 0
            and proposal.case_opened_at == case.created_at and proposal.case_request_id == case.client_request_id
            and isinstance(entry, dict) and intent.history_index == len(case.history)-1
            and _fingerprint(entry) == intent.history_fingerprint and entry.get('status') == 'RESOLVED'
            and case.status == 'RESOLVED' and case.resolved_at and entry.get('resolved_at') == case.resolved_at.isoformat()
            and (intent.account_id, intent.role) in expected and type(cycle) is int and cycle > 0
            and recipient_cycle(order, intent.account_id) == cycle and account and account.account_type == intent.role
            and account.license_membership_active and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '协商结案来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title=intent.title, preview=intent.preview, related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
