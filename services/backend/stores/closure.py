"""SA02 closure notice, expiry evidence and an explicitly unwired new-activity guard."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.db import transaction

from .closure_clock import CLOCK_SOURCE, ControlClockEvidence, read_control_clock
from .models import PlatformAuditEvent, Store, StoreClosureEvent, StoreClosureNotice
from .platform_permissions import platform_may

NOTICE_POLICY_VERSION = 'SA02_NOTICE_30D_V1'
NOTICE_DAYS = 30
_REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}')


class ClosureError(ValueError):
    def __init__(self, message, status=400, code='CLOSURE_VALIDATION_ERROR'):
        super().__init__(message)
        self.status = status
        self.code = code


class ClosureClockUnavailable(ClosureError):
    def __init__(self, message='控制库时钟不可用，闭店状态未确认'):
        super().__init__(message, 503, 'CLOSURE_CLOCK_UNAVAILABLE')


def _request_id(value):
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value.strip()):
        raise ClosureError('client_request_id 无效')
    return value.strip()


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 500:
        raise ClosureError('请填写 500 字以内的原因')
    return value.strip()


def _fingerprint(store_id, operation, **values):
    raw = json.dumps(
        {'store_id': store_id, 'operation': operation, **values},
        sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _sample_clock():
    try:
        evidence = read_control_clock()
        if (not isinstance(evidence, ControlClockEvidence) or evidence.source != CLOCK_SOURCE
                or not isinstance(evidence.identity, str) or not evidence.identity
                or len(evidence.identity) > 80 or not isinstance(evidence.instant, datetime)
                or evidence.instant.utcoffset() is None):
            raise ClosureClockUnavailable('控制库时钟证据不完整')
        return ControlClockEvidence(
            evidence.instant.astimezone(datetime_timezone.utc), evidence.source, evidence.identity,
        )
    except ClosureError:
        raise
    except Exception as exc:
        raise ClosureClockUnavailable() from exc


def _locked_store(store_id):
    store = Store.objects.select_for_update().filter(pk=store_id).first()
    if store is None:
        raise ClosureError('门店不存在', 404, 'STORE_NOT_FOUND')
    return store


def _authorize(actor, action, store):
    if not platform_may(actor, action, store_slug=store.slug):
        raise ClosureError('缺少该门店的闭店平台权限', 403, 'PLATFORM_FORBIDDEN')


def _actor_data(actor):
    return {'id': str(actor.pk), 'username': actor.username, 'nickname': actor.nickname}


def _notice_valid(notice):
    return bool(
        notice.notice_policy_version == NOTICE_POLICY_VERSION
        and type(notice.notice_days) is int and notice.notice_days >= 30
        and isinstance(notice.started_at, datetime) and notice.started_at.utcoffset() is not None
        and isinstance(notice.earliest_settlement_at, datetime)
        and notice.earliest_settlement_at.utcoffset() is not None
        and notice.earliest_settlement_at == notice.started_at + timedelta(days=notice.notice_days)
    )


def _event_valid(event, notice):
    if event is None:
        return True
    return bool(
        event.kind == StoreClosureEvent.Kind.NOTICE_EXPIRED
        and event.store_id == notice.store_id and event.notice_id == notice.pk
        and isinstance(event.occurred_at, datetime) and event.occurred_at.utcoffset() is not None
        and event.occurred_at >= notice.earliest_settlement_at
    )


def _state_at(notice, instant, event=None):
    if not _notice_valid(notice) or not _event_valid(event, notice):
        return 'EVIDENCE_REVIEW'
    if not isinstance(instant, datetime) or instant.utcoffset() is None or instant < notice.started_at:
        return 'CLOCK_REVIEW'
    if event is not None:
        # The immutable authorized expiry observation prevents status regression
        # when a later clock sample moves backwards but remains after start.
        return 'SETTLEMENT_PENDING'
    return 'NOTICE_ACTIVE' if instant < notice.earliest_settlement_at else 'SETTLEMENT_PENDING'


def _expiry(notice, *, lock=False):
    rows = StoreClosureEvent.objects.filter(notice=notice)
    if lock:
        rows = rows.select_for_update()
    return rows.select_related('actor').first()


def event_summary(event):
    return {
        'id': str(event.pk), 'kind': event.kind, 'reason': event.reason,
        'actor': _actor_data(event.actor), 'occurred_at': event.occurred_at.isoformat(),
        'client_request_id': event.client_request_id, 'clock_source': event.clock_source,
        'clock_identity': event.clock_identity, 'created_at': event.created_at.isoformat(),
    }


def notice_summary(notice, status):
    return {
        'id': str(notice.pk), 'store_slug': notice.store.slug, 'status': status,
        'reason_category': notice.reason_category, 'reason': notice.reason,
        'applicant': _actor_data(notice.applicant), 'started_at': notice.started_at.isoformat(),
        'notice_policy_version': notice.notice_policy_version, 'notice_days': notice.notice_days,
        'earliest_settlement_at': notice.earliest_settlement_at.isoformat(),
        'client_request_id': notice.client_request_id, 'clock_source': notice.clock_source,
        'clock_identity': notice.clock_identity, 'created_at': notice.created_at.isoformat(),
    }


def read_platform_closure(store):
    notice = StoreClosureNotice.objects.filter(store=store).select_related('store', 'applicant').first()
    if notice is None:
        return {
            'status': 'NOT_STARTED', 'notice': None, 'expiry_event': None,
            'new_activity_allowed': True, 'store_status': store.status,
        }
    event = _expiry(notice)
    status = _state_at(notice, _sample_clock().instant, event)
    return {
        'status': status, 'notice': notice_summary(notice, status),
        'expiry_event': event_summary(event) if event else None,
        'new_activity_allowed': False, 'store_status': store.status,
    }


def initiate_closure(actor, *, store_id, reason_category, reason, client_request_id):
    client_request_id = _request_id(client_request_id)
    reason = _reason(reason)
    if reason_category not in StoreClosureNotice.ReasonCategory.values:
        raise ClosureError('闭店原因类别无效')
    fingerprint = _fingerprint(
        store_id, 'NOTICE_STARTED', reason_category=reason_category, reason=reason,
        notice_policy_version=NOTICE_POLICY_VERSION, notice_days=NOTICE_DAYS,
    )
    with transaction.atomic(using='default'):
        store = _locked_store(store_id)
        _authorize(actor, 'closure:manage', store)
        notice = StoreClosureNotice.objects.select_for_update().filter(store=store).select_related(
            'store', 'applicant',
        ).first()
        if notice is not None:
            if notice.client_request_id == client_request_id:
                if not hmac.compare_digest(notice.request_fingerprint, fingerprint):
                    raise ClosureError('请求编号已用于其他闭店申请内容', 409, 'REQUEST_ID_CONFLICT')
                return notice, 'REPLAY'
            return notice, 'EXISTING'
        if store.status != Store.Status.ACTIVE:
            raise ClosureError('仅营业中门店可发起闭店公示', 409, 'STORE_CLOSURE_STATE_INVALID')
        clock = _sample_clock()
        earliest = clock.instant + timedelta(days=NOTICE_DAYS)
        notice = StoreClosureNotice.objects.create(
            store=store, reason_category=reason_category, reason=reason, applicant=actor,
            started_at=clock.instant, notice_policy_version=NOTICE_POLICY_VERSION,
            notice_days=NOTICE_DAYS, earliest_settlement_at=earliest,
            client_request_id=client_request_id, request_fingerprint=fingerprint,
            clock_source=clock.source, clock_identity=clock.identity,
        )
        PlatformAuditEvent.objects.create(
            account=actor, action='closure.notice_started', target_store_slug=store.slug,
            target_type='closure_notice', target_id=str(notice.pk),
            detail={
                'reason_category': reason_category, 'reason': reason,
                'client_request_id': client_request_id, 'started_at': clock.instant.isoformat(),
                'notice_policy_version': NOTICE_POLICY_VERSION, 'notice_days': NOTICE_DAYS,
                'earliest_settlement_at': earliest.isoformat(), 'clock_source': clock.source,
                'clock_identity': clock.identity,
            },
        )
        return notice, 'CREATED'


def confirm_expiry(actor, *, store_id, reason, client_request_id):
    client_request_id = _request_id(client_request_id)
    reason = _reason(reason)
    fingerprint = _fingerprint(store_id, 'NOTICE_EXPIRED', reason=reason)
    with transaction.atomic(using='default'):
        store = _locked_store(store_id)
        _authorize(actor, 'closure:manage', store)
        notice = StoreClosureNotice.objects.select_for_update().filter(store=store).select_related(
            'store', 'applicant',
        ).first()
        if notice is None:
            raise ClosureError('门店尚未发起闭店公示', 409, 'CLOSURE_NOT_STARTED')
        if notice.client_request_id == client_request_id:
            raise ClosureError('请求编号已用于闭店申请', 409, 'REQUEST_ID_CONFLICT')
        event = _expiry(notice, lock=True)
        if event is not None:
            if event.client_request_id == client_request_id:
                if not hmac.compare_digest(event.request_fingerprint, fingerprint):
                    raise ClosureError('请求编号已用于其他到期确认内容', 409, 'REQUEST_ID_CONFLICT')
                return event, 'REPLAY'
            return event, 'EXISTING'
        clock = _sample_clock()
        status = _state_at(notice, clock.instant)
        if status == 'EVIDENCE_REVIEW':
            raise ClosureError('闭店公示原始证据需核对', 409, 'CLOSURE_EVIDENCE_REVIEW')
        if status == 'CLOCK_REVIEW':
            raise ClosureError('控制库时钟早于公示启动时点', 409, 'CLOSURE_CLOCK_REVIEW')
        if status != 'SETTLEMENT_PENDING':
            raise ClosureError('30 天公示期尚未届满', 409, 'CLOSURE_NOTICE_ACTIVE')
        event = StoreClosureEvent.objects.create(
            store=store, notice=notice, kind=StoreClosureEvent.Kind.NOTICE_EXPIRED,
            reason=reason, actor=actor, occurred_at=clock.instant,
            client_request_id=client_request_id, request_fingerprint=fingerprint,
            clock_source=clock.source, clock_identity=clock.identity,
        )
        PlatformAuditEvent.objects.create(
            account=actor, action='closure.notice_expiry_confirmed', target_store_slug=store.slug,
            target_type='closure_event', target_id=str(event.pk),
            detail={
                'notice_id': str(notice.pk), 'reason': reason,
                'client_request_id': client_request_id, 'started_at': notice.started_at.isoformat(),
                'earliest_settlement_at': notice.earliest_settlement_at.isoformat(),
                'occurred_at': clock.instant.isoformat(), 'clock_source': clock.source,
                'clock_identity': clock.identity, 'result': 'SETTLEMENT_PENDING',
            },
        )
        return event, 'CREATED'


def closure_guard(store):
    """Query-only guard; general non-order entry points remain separately unwired."""
    recorded = StoreClosureNotice.objects.filter(store=store).exists()
    return {
        'closure_recorded': recorded,
        'new_activity_allowed': not recorded,
        'restriction_scope': 'NEW_ACTIVITY_ONLY',
        'guard_coverage': 'NOT_ENFORCED_AT_BUSINESS_ENTRYPOINTS',
        'service_order_create_coverage': 'BOSS_GIFT_STAFF_AND_RESERVATION_CREATE_V1',
    }


def new_activity_allowed(store):
    return closure_guard(store)['new_activity_allowed']


def assert_new_activity_allowed(store):
    if not new_activity_allowed(store):
        raise ClosureError(
            '门店已有闭店公示；新增业务入口须接入闭店守卫', 409, 'STORE_CLOSURE_RESTRICTED',
        )


def closure_refusal_response(store):
    """Translate the query-only guard to a stable create-view response.

    Any existing notice fails closed without consulting the public clock, so an
    UNKNOWN public projection is still restricted. A missing/unreadable control
    store is a 503 rather than permission to create a paid order.
    """
    from django.db import DatabaseError, InterfaceError
    from rest_framework.response import Response
    try:
        if store is not None and new_activity_allowed(store):
            return None
    except (DatabaseError, InterfaceError):
        store = None
    if store is None:
        return Response({
            'code': 503, 'errorCode': 'STORE_CLOSURE_STATUS_UNAVAILABLE',
            'msg': '暂时无法核验本店闭店状态，请稍后重试',
        }, status=503)
    return Response({
        'code': 409, 'errorCode': 'STORE_CLOSURE_RESTRICTED',
        'msg': '本店处于闭店公示或待结清阶段，暂不接受新增服务单或预约',
    }, status=409)


def _public_times(notice):
    return {
        'started_at': notice.started_at.isoformat(),
        'earliest_settlement_at': notice.earliest_settlement_at.isoformat(),
        'notice_days': notice.notice_days,
    }


def public_announcement(store):
    if store.status == Store.Status.DISABLED:
        return {
            'status': 'DISABLED', 'announcement_visible': True, 'clock_status': 'NOT_REQUIRED',
            'announcement': '本店已停用，不再接受新增业务。既有订单、售后与余额返还情况需另行核对；停用不代表已完成退款或结清。',
        }
    notice = StoreClosureNotice.objects.filter(store=store).first()
    if notice is None:
        return {'status': 'NOT_STARTED', 'announcement_visible': False}
    event = _expiry(notice)
    if not _notice_valid(notice) or not _event_valid(event, notice):
        return {'status': 'UNKNOWN', 'announcement_visible': False, 'clock_status': 'EVIDENCE_REVIEW'}
    try:
        instant = _sample_clock().instant
    except ClosureClockUnavailable:
        return {
            'status': 'UNKNOWN', 'announcement_visible': True, 'clock_status': 'UNAVAILABLE',
            'announcement': '本店闭店公示状态暂时待核，请以平台后续公告为准。',
            **_public_times(notice),
        }
    state = _state_at(notice, instant, event)
    if state in ('CLOCK_REVIEW', 'EVIDENCE_REVIEW'):
        return {
            'status': 'UNKNOWN', 'announcement_visible': True, 'clock_status': 'REVIEW',
            'announcement': '本店闭店公示状态暂时待核，请以平台后续公告为准。',
            **_public_times(notice),
        }
    announcement = (
        '本店正在进行闭店公示。既有订单、售后与权益查询仍按原入口办理。'
        if state == 'NOTICE_ACTIVE' else
        '本店闭店公示期已届满，待平台完成后续结清核对；本状态不代表余额已返还。'
    )
    return {
        'status': state, 'announcement_visible': True, 'clock_status': 'VERIFIED',
        'announcement': announcement, **_public_times(notice),
    }
