"""Recover control audit from committed store withdrawal facts, never money."""
from django.db import DatabaseError, transaction

from .context import store_context
from .models import PlatformAccount, PlatformAuditEvent, PlatformWithdrawalAuditReceipt, Store


ACTION_TRANSITIONS = {
    'approve': ({'STORE_APPROVED'}, 'PLATFORM_APPROVED'),
    'mark-unknown': ({'PLATFORM_APPROVED'}, 'PAYMENT_UNKNOWN'),
    'paid': ({'PLATFORM_APPROVED', 'PAYMENT_UNKNOWN'}, 'PAID'),
    'reject': ({'STORE_APPROVED', 'PLATFORM_APPROVED', 'PAYMENT_UNKNOWN'}, 'REJECTED'),
    'cancel': ({'STORE_APPROVED', 'PLATFORM_APPROVED', 'PAYMENT_UNKNOWN'}, 'CANCELLED'),
}


def read_event(store, withdrawal_id, event_id):
    from wallet.models import WithdrawalPlatformEvent
    with store_context(store):
        row = WithdrawalPlatformEvent.objects.filter(pk=event_id, withdrawal_id=withdrawal_id).first()
        if not row:
            raise ValueError('门店提现事件不存在')
        transition = ACTION_TRANSITIONS.get(row.action)
        if (not transition or row.from_status not in transition[0] or row.to_status != transition[1]
                or not row.request_id or len(row.request_id) > 64 or len(row.request_fingerprint) != 64
                or not isinstance(row.detail, dict)):
            raise ValueError('门店提现事件需要人工核对')
        return {'event_id': row.pk, 'withdrawal_id': row.withdrawal_id, 'actor_id': row.platform_actor_id,
                'actor_name': row.actor_name, 'action': row.action, 'request_id': row.request_id,
                'fingerprint': row.request_fingerprint, 'from_status': row.from_status,
                'to_status': row.to_status, 'detail': row.detail, 'event_at': row.created_at.isoformat()}


def audit_detail(facts):
    return {'event_id': facts['event_id'], 'request_id': facts['request_id'], 'actor_name': facts['actor_name'],
            'request_fingerprint': facts['fingerprint'], 'from_status': facts['from_status'],
            'to_status': facts['to_status'], 'event_at': facts['event_at'], 'event_detail': facts['detail']}


def receipt_matches(receipt, store, facts):
    audit = receipt.audit
    detail = audit_detail(facts)
    saved = {key: audit.detail.get(key) for key in detail} if isinstance(audit.detail, dict) else None
    return (audit.account_id == facts['actor_id'] and audit.action == 'withdraw.' + facts['action']
            and audit.target_store_slug == store.slug and audit.target_type == 'withdrawal'
            and audit.target_id == str(facts['withdrawal_id']) and saved == detail)


def record_committed_event(store, facts, *, token=None):
    """Write the audit and receipt atomically; token=None is the original commit callback."""
    from .database_identity import lock_store_registration
    from .platform_auth import account_for_token
    from .platform_permissions import platform_may
    with transaction.atomic(using='default'):
        lock_store_registration()
        current = Store.objects.filter(pk=store.pk, slug=store.slug, database_alias=store.database_alias).first()
        if current is None:
            raise ValueError('门店身份已变化，需人工核对')
        if token is not None:
            operator = account_for_token(token)
            action = 'withdraw:record_payment' if facts['action'] in ('paid', 'mark-unknown') else 'withdraw:final_review'
            if not platform_may(operator, action, store_slug=store.slug):
                raise PermissionError('无权补记该提现事件')
        else:
            operator = None
        original = PlatformAccount.objects.filter(pk=facts['actor_id']).first()
        if original is None:
            raise ValueError('原平台操作人不存在，需人工核对')
        receipt = PlatformWithdrawalAuditReceipt.objects.select_related('audit').filter(
            store=current, tenant_event_id=facts['event_id']).first()
        detail = audit_detail(facts)
        if receipt:
            if not receipt_matches(receipt, store, facts):
                raise ValueError('已有提现审计与门店事件不一致，需人工核对')
            return receipt, False
        detail['recovered_by'] = str(operator.pk) if operator else None
        audit = PlatformAuditEvent.objects.create(account=original, action='withdraw.' + facts['action'],
            target_store_slug=store.slug, target_type='withdrawal', target_id=str(facts['withdrawal_id']), detail=detail)
        receipt = PlatformWithdrawalAuditReceipt.objects.create(store=current, tenant_event_id=facts['event_id'], audit=audit)
        return receipt, True


def publish_committed_event(store, withdrawal_id, event_id):
    """On-commit best effort; failures leave the tenant event available for recovery."""
    facts = read_event(store, withdrawal_id, event_id)
    return record_committed_event(store, facts)
