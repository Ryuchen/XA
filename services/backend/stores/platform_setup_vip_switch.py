"""Store-local VIP switch transition for an authenticated platform operator."""
from types import SimpleNamespace

from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def change_switch(operation, enabled, locked_accounts, actor, operation_id, request):
    from console.models import AdminAuditLog
    from engagement.serializers import ConfigSerializer
    from engagement.vip_transition import (freeze_existing_vip_terms, transition_guard_available,
                                           transition_guard_enabled, resume_guard_available)
    from engagement.vip_threshold_change import _audit_actor, _audit_body, apply_resume, preview_resume

    serializer = ConfigSerializer(operation, data={'vip_enabled': enabled}, partial=True)
    serializer.is_valid(raise_exception=True)
    if operation.vip_enabled == enabled:
        return serializer.save()
    audit_request = SimpleNamespace(platform_setup_actor=actor, platform_setup_operation_id=operation_id,
                                    method=request.method, path=request.path)
    if enabled:
        if not resume_guard_available():
            raise ValidationError('会员既有权益规则尚不可用，请先核对')
        token = preview_resume()['token']
        apply_resume(serializer, token, audit_request, locked_accounts)
        from engagement.notifier import notify_vip_config_changed
        from coupons.notifier import notify_coupon_catalog_changed
        transaction.on_commit(notify_vip_config_changed)
        transaction.on_commit(notify_coupon_catalog_changed)
        return operation
    if not getattr(settings, 'VIP_THRESHOLD_RECALC_ENABLED', False) or not transition_guard_available():
        if not enabled:
            # Capture the actual rules before disabling even while the
            # recalculation rollout remains closed. Existing voucher safety
            # checks still apply when there are members.
            # A default-off optional rollout does not disable the ordinary
            # coupon materialization path. A cutover that actually requires
            # unavailable protection must still stop the switch.
            if locked_accounts and transition_guard_enabled() and not transition_guard_available():
                raise ValidationError('会员既有权益保护尚未开放')
            if locked_accounts:
                result = freeze_existing_vip_terms()
                AdminAuditLog.objects.create(**_audit_actor(audit_request), method=request.method, path=request.path[:255],
                    resource='vip-terms-disable', object_id='1',
                    request_body=_audit_body(audit_request, result), status_code=200)
            from engagement.vip_deferred import capture_disable_baseline, disable_evidence
            baseline = capture_disable_baseline()
            AdminAuditLog.objects.create(**_audit_actor(audit_request), method=request.method, path=request.path[:255],
                resource='vip-deferred-begin', object_id=str(baseline.epoch),
                request_body=_audit_body(audit_request, disable_evidence(baseline)), status_code=200)
            return serializer.save()
        if not locked_accounts:
            from engagement.vip_deferred import close_empty_setup_period
            closed = close_empty_setup_period()
            if closed:
                AdminAuditLog.objects.create(**_audit_actor(audit_request), method=request.method, path=request.path[:255],
                    resource='vip-empty-resume', object_id=str(closed.epoch),
                    request_body=_audit_body(audit_request, {'reason': '首次配置期间无老板账号'}), status_code=200)
            return serializer.save()
        raise ValidationError('会员重算和既有权益保护尚未开放')
    result = freeze_existing_vip_terms()
    from engagement.vip_deferred import capture_disable_baseline, disable_evidence
    baseline = capture_disable_baseline()
    AdminAuditLog.objects.create(**_audit_actor(audit_request), method=request.method, path=request.path[:255],
        resource='vip-terms-disable', object_id='1', request_body=_audit_body(audit_request, result), status_code=200)
    AdminAuditLog.objects.create(**_audit_actor(audit_request), method=request.method, path=request.path[:255],
        resource='vip-deferred-begin', object_id=str(baseline.epoch),
        request_body=_audit_body(audit_request, disable_evidence(baseline)), status_code=200)
    serializer.save()
    from engagement.notifier import notify_vip_config_changed
    from coupons.notifier import notify_coupon_catalog_changed
    transaction.on_commit(notify_vip_config_changed)
    transaction.on_commit(notify_coupon_catalog_changed)
    return operation
