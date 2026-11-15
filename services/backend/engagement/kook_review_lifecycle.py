"""Bind KOOK License review to the provider lifecycle lock of its source."""
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from rest_framework.exceptions import ValidationError

_provider = ContextVar('kook_license_review_provider', default=None)


def capture(source_id, outcome):
    if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False) or outcome != 'ALLOW':
        return None
    from console.models import AdminAuditLog
    from club_accounts.models import ClubAccount
    from .models import KookLicensePointsReview
    if KookLicensePointsReview.objects.filter(source_id=source_id).exists():
        return None  # Read-only replay never writes points.
    source = AdminAuditLog.objects.filter(pk=source_id).values('request_body').first()
    facts = source['request_body'] if source else None
    account_id = facts.get('account_id') if isinstance(facts, dict) else None
    if type(account_id) is not int or account_id <= 0:
        return None  # The source verifier will report invalid evidence.
    account = ClubAccount.objects.filter(pk=account_id, account_type='PROVIDER').first()
    if account:
        from club_accounts.provider_lifecycle import ensure_provider_lifecycle
        ensure_provider_lifecycle(account)
    return account


@contextmanager
def locked(account):
    if account is None:
        yield
        return
    from club_accounts.provider_lifecycle import lock_provider_operation
    lock_provider_operation(account)
    token = _provider.set((account.pk, account._state.db))
    try:
        yield
    finally:
        _provider.reset(token)


def require(account):
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False) and account.account_type == 'PROVIDER':
        if _provider.get() != (account.pk, account._state.db):
            raise ValidationError('陪玩 KOOK 补分生命周期来源待核对')
