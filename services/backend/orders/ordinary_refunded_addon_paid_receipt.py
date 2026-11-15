"""Read-only evidence of paid people before a later ordinary add-on refund."""
from collections import defaultdict
from datetime import timedelta
from django.core.exceptions import ValidationError


def verify(run):
    from .models import OrdinaryRefundedAddonPaidRevision, OrderPointsSettlementPerson, OrderAddonSettlement
    from .ordinary_refunded_addon_paid_revision import posting_active
    from .ordinary_addon_paid_refund import digest
    from .ordinary_refunded_addon_settlement import _digest
    from .negotiation_settlement import negotiated_aftersale_start
    from .services import missing_report_evidence
    from wallet.models import ProviderReport, Transaction

    refund = run.refund
    revision = OrdinaryRefundedAddonPaidRevision.objects.filter(run=run).select_related(
        'settlement__ordinary_refund_addon_evidence__execution', 'refund__proposal__case', 'refund__order').first()
    if not revision:
        if posting_active(run):
            return
        raise ValidationError('普通首退后的已付加购退款缺少真实版本')
    data = revision.snapshot
    if (not isinstance(data, dict) or revision.evidence_hash != digest(data)
            or revision.refund_id != refund.pk or revision.settlement.order_id != refund.order_id
            or data.get('order_id') != refund.order_id or data.get('settlement_id') != revision.settlement_id
            or data.get('case_id') != refund.proposal.case_id or not refund.proposal.case_opened_at
            or data.get('case_opened_at') != refund.proposal.case_opened_at.isoformat()
            or not refund.created_at or run.created_at <= refund.created_at
            or revision.created_at < run.created_at):
        raise ValidationError('普通首退后已付退款版本身份异常')
    evidence = revision.settlement.ordinary_refund_addon_evidence
    before, after = data.get('before_facts'), data.get('after_facts')
    if (not isinstance(before, dict) or not isinstance(after, dict)
            or data.get('settlement_evidence_id') != evidence.pk
            or data.get('execution_id') != evidence.execution_id
            or data.get('before_hash') != _digest(before) or data.get('after_hash') != digest(after)
            or before.get('order_id') != refund.order_id or before.get('execution_id') != evidence.execution_id):
        raise ValidationError('普通首退前后结算事实异常')
    people = list(OrderPointsSettlementPerson.objects.filter(settlement=revision.settlement,
        settled_at__lt=refund.created_at).select_related('segment', 'report', 'income_transaction__wallet').order_by('pk'))
    addons = list(OrderAddonSettlement.objects.filter(order_settlement=revision.settlement,
        settled_at__lt=refund.created_at).select_related('payment__intent', 'segment', 'report',
        'income_transaction__wallet').order_by('pk'))
    if (not people or data.get('paid_people') != [[p.pk, p.segment_id, p.segment.provider_account_id,
            p.amount, p.report_id, p.income_transaction_id, p.settled_at.isoformat()] for p in people]
            or data.get('paid_addons') != [[p.pk, p.payment_id, p.segment_id, p.amount,
            p.report_id, p.income_transaction_id, p.settled_at.isoformat()] for p in addons]
            or set(refund.proposal.participants.values_list('account_id', flat=True)).intersection(
                p.segment.provider_account_id for p in people)):
        raise ValidationError('普通首退已付人员或加购流水发生变化')
    order, execution = refund.order, evidence.execution
    facts = {x['segment_id']: x for x in before.get('people', [])}
    assignments = {p.pk: p for p in order.providers.all()}
    parts = defaultdict(int)
    for item in before.get('addons', []):
        parts[(item['payment_id'], item['segment_id'])] += item['income']
    reports = {person.report_id: person for person in people}
    if {(p.payment_id, p.segment_id) for p in addons} != {key for key in parts
            if key[1] in {p.segment_id for p in people}}:
        raise ValidationError('已付加购份额不完整')
    def valid_credit(tx, amount, account, user):
        return ((amount == 0 and tx is None) or (amount > 0 and tx is not None
            and tx.order_id == order.pk and tx.wallet.account_id == account and tx.wallet.user_id == user
            and tx.tx_type == Transaction.TxType.INCOME and tx.status == Transaction.Status.SUCCESS
            and tx.amount == amount and tx.balance_after == tx.balance_before + amount
            and not tx.reversal_of_id and not tx.reversals.exists()))
    for person in people:
        fact = facts.get(person.segment_id)
        assignment = assignments.get(fact['assignment_id']) if fact else None
        share = execution.shares.filter(account_id=fact['account_id']).first() if fact else None
        report = person.report
        start = negotiated_aftersale_start(order, execution, assignment, share) if assignment else None
        if (not fact or not assignment or person.amount != fact['main_income']
                or assignment.provider_account_id != fact['account_id'] or assignment.provider_id != fact['user_id']
                or assignment.settled_at != person.settled_at or not start
                or person.settled_at < start + timedelta(hours=order.service_aftersale_hours)
                or person.settled_at <= refund.proposal.case_opened_at or report.order_id != order.pk
                or report.provider_account_id != fact['account_id'] or report.provider_id != fact['user_id']
                or report.status != ProviderReport.Status.APPROVED or not report.submitted_at or not report.audited_at
                or report.audited_at < report.submitted_at or report.audited_at > person.settled_at
                or person.settled_at < report.submitted_at + timedelta(hours=order.service_freeze_hours)
                or report.payout_amount != assignment.provider_income or missing_report_evidence(report)
                or not valid_credit(person.income_transaction, person.amount, fact['account_id'], fact['user_id'])):
            raise ValidationError('普通首退已付主单收益或报单异常')
        if share:
            if (share.settled_at != person.settled_at or share.income_transaction_id != person.income_transaction_id
                    or share.settled_report_id != report.pk or share.report_submitted_at != report.submitted_at):
                raise ValidationError('普通首退本人结算凭证异常')
        else:
            saved = execution.unaffected_report_evidence.get(str(assignment.pk))
            if (not isinstance(saved, dict) or saved.get('report_id') != report.pk
                    or saved.get('settled_at') != person.settled_at.isoformat()
                    or saved.get('submitted_at') != report.submitted_at.isoformat()
                    or saved.get('income_transaction_id') != person.income_transaction_id):
                raise ValidationError('普通首退其他陪玩结算凭证异常')
    for item in addons:
        person = reports.get(item.report_id)
        fact = facts.get(item.segment_id)
        if (not person or person.segment_id != item.segment_id or not fact
                or item.payment.intent.order_id != order.pk or item.amount != parts[(item.payment_id, item.segment_id)]
                or item.report.provider_account_id != fact['account_id']
                or not valid_credit(item.income_transaction, item.amount, fact['account_id'], fact['user_id'])):
            raise ValidationError('普通首退已付加购收益异常')
    run_facts = run.ordinary_refund_snapshot
    combined, run_combined = after.get('combined'), run_facts.get('combined') if isinstance(run_facts, dict) else None
    dynamic = {'allocated_units', 'release_units', 'remaining_units'}
    if (not isinstance(run_facts, dict) or not isinstance(combined, dict) or not isinstance(run_combined, dict)
            or after.get('order_id') != order.pk or after.get('execution_id') != execution.pk
            or {key: value for key, value in combined.items() if key not in dynamic}
               != {key: value for key, value in run_combined.items() if key not in dynamic}
            or after.get('addon_history') != run_facts.get('addon_history')):
        raise ValidationError('退款后结算与真实资金版本不一致')
    releases = dict(run.releases.values_list('allocation_id', 'amount'))
    try:
        uses = sorted([[row[0], row[1] - row[2] - releases.get(row[0], 0)]
                       for row in run.allocation_snapshot if row[1] - row[2] - releases.get(row[0], 0) > 0])
        if uses != after['funding_uses']:
            raise ValidationError('退款后结算的到账用途异常')
        allocated = sum(value for _, value in uses)
        if (combined.get('allocated_units') != allocated or combined.get('release_units') != 0
                or combined.get('remaining_units') != max(combined['required_units'] - allocated, 0)):
            raise ValidationError('退款后可用到账或补资缺口异常')
        from .models import OrderAddonPayment
        from .addon_handover import current_responsibilities
        actual_addons = []
        for payment in OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at__lt=run.created_at).order_by('pk'):
            _, responsibilities = current_responsibilities(payment, before=run.created_at)
            actual_addons.extend({'payment_id': payment.pk, 'segment_id': item['segment_id'],
                'account_id': item['provider_id'], 'basis': item['basis'], 'income': item['income']}
                for item in responsibilities)
        if actual_addons != after['addons']:
            raise ValidationError('退款后加购实际份额异常')
        deductions = defaultdict(int)
        for item in refund.proposal.participants.all():
            for deduction in item.main_segment_deductions.all():
                deductions[deduction.segment_id] += deduction.amount
        expected_people = [{**item, 'main_income': item['main_income'] - deductions[item['segment_id']]}
                           for item in before['people']]
        if expected_people != after['people']:
            raise ValidationError('退款后本人主单收益不一致')
    except (KeyError, TypeError, IndexError) as exc:
        raise ValidationError('退款后结算来源不完整') from exc
    return data
