"""Recoverable control audit for already committed closure-return events."""
from __future__ import annotations

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from stores.context import store_context
from stores.db import transaction as tenant_transaction
from stores.models import PlatformAccount, PlatformAuditEvent, Store

from .models import ClosureReturnEvent, ClosureReturnOutbox


ACTIONS = {
    'PREPARED': 'closure_return.prepared',
    'PAYMENT_RECORDED': 'closure_return.payment_recorded',
    'RELEASED': 'closure_return.released',
}
TRANSITIONS = {
    'PREPARED': ('READY_TO_PREPARE', 'FROZEN'),
    'PAYMENT_RECORDED': ('FROZEN', 'PAID'),
    'RELEASED': ('FROZEN', 'RELEASED'),
}


def read_event(store, event_id):
    """Read and validate tenant facts before opening a control transaction."""
    with store_context(store):
        event = ClosureReturnEvent.objects.select_related('case').filter(pk=event_id).first()
        if event is None:
            raise ValueError('闭店返还事件不存在')
        transition = TRANSITIONS.get(event.kind)
        if (transition is None or (event.from_status, event.to_status) != transition
                or event.case.account_id is None or event.case.business_cycle <= 0
                or event.case.principal_units <= 0 or len(event.request_fingerprint) != 64
                or not isinstance(event.detail, dict)):
            raise ValueError('闭店返还事件事实待核对')
        return {
            'event_id': str(event.pk), 'case_id': str(event.case_id),
            'account_id': event.case.account_id, 'business_cycle': event.case.business_cycle,
            'principal_units': event.case.principal_units, 'actor_id': event.platform_actor_id,
            'action': ACTIONS[event.kind], 'request_id': event.client_request_id,
            'request_fingerprint': event.request_fingerprint, 'event_at': event.created_at.isoformat(),
            'event_detail': event.detail,
        }


def _audit_detail(facts):
    return {key: facts[key] for key in (
        'event_id', 'case_id', 'account_id', 'business_cycle', 'principal_units',
        'request_id', 'request_fingerprint', 'event_at', 'event_detail',
    )}


def _matches(audit, store, facts):
    return bool(
        audit.account_id == facts['actor_id'] and audit.action == facts['action']
        and audit.target_store_slug == store.slug and audit.target_type == 'closure_return_event'
        and audit.target_id == facts['event_id'] and isinstance(audit.detail, dict)
        and audit.detail == _audit_detail(facts)
    )


def record_committed_event(store, facts):
    """Serialize duplicate audit attempts without touching the tenant database."""
    with transaction.atomic(using='default'):
        current = Store.objects.select_for_update().filter(
            pk=store.pk, slug=store.slug, database_alias=store.database_alias,
        ).first()
        if current is None:
            raise ValueError('门店身份已变化，返还审计待核对')
        actor = PlatformAccount.objects.filter(pk=facts['actor_id']).first()
        if actor is None:
            raise ValueError('原平台操作人不存在，返还审计待核对')
        existing = list(PlatformAuditEvent.objects.filter(
            action=facts['action'], target_store_slug=current.slug,
            target_type='closure_return_event', target_id=facts['event_id'],
        ).order_by('id')[:2])
        if len(existing) > 1:
            raise ValueError('闭店返还审计存在重复记录')
        if existing:
            if not _matches(existing[0], current, facts):
                raise ValueError('闭店返还审计与店库事实不一致')
            return existing[0], False
        audit = PlatformAuditEvent.objects.create(
            account=actor, action=facts['action'], target_store_slug=current.slug,
            target_type='closure_return_event', target_id=facts['event_id'], detail=_audit_detail(facts),
        )
        return audit, True


def publish_committed_event(store, event_id):
    facts = read_event(store, event_id)
    return record_committed_event(store, facts)


def mark_recorded(store, event_id, audit_id):
    with store_context(store), tenant_transaction.atomic():
        outbox = ClosureReturnOutbox.objects.select_for_update().filter(event_id=event_id).first()
        if outbox is None:
            raise ValueError('闭店返还审计投递状态缺失')
        if outbox.status == 'RECORDED' and outbox.audit_id != audit_id:
            raise ValueError('闭店返还审计投递状态不一致')
        ClosureReturnOutbox.objects.filter(pk=outbox.pk).update(
            status='RECORDED', audit_id=audit_id, attempts=F('attempts') + 1,
            last_error='', updated_at=timezone.now(),
        )


def mark_pending(store, event_id, error='AUDIT_UNAVAILABLE'):
    """Best effort only; the immutable event/outbox already survived commit."""
    try:
        with store_context(store), tenant_transaction.atomic():
            ClosureReturnOutbox.objects.filter(event_id=event_id, status='PENDING').update(
                attempts=F('attempts') + 1, last_error=str(error)[:200],
                updated_at=timezone.now(),
            )
    except Exception:
        # Never convert a durable funds result into a claimed rollback merely
        # because its delivery bookkeeping is also unavailable.
        pass
