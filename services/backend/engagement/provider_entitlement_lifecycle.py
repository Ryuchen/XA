"""Serialize provider-owned entitlement edits against membership closure."""
from functools import wraps

from django.conf import settings
from rest_framework.response import Response
from stores.db import transaction


def provider_claim_source_current(claim, account):
    """A provider redemption's event key is its persisted cycle evidence."""
    return (claim.account_id == account.pk and claim.source == 'REDEMPTION'
            and claim.event_key.startswith(f'redeem:{account.pk}:{account.business_cycle}:'))


def provider_entitlement_edit(fn):
    @wraps(fn)
    def wrapped(view, request, *args, **kwargs):
        from club_accounts.models import ClubAccount
        account = request.account
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or account.account_type != ClubAccount.AccountType.PROVIDER):
            return fn(view, request, *args, **kwargs)
        from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                       lock_provider_operation)
        try:
            ensure_provider_lifecycle(account)
            with transaction.atomic():
                lock_provider_operation(account)
                if not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                        license_membership_active=True, business_cycle=account.business_cycle).exists():
                    return Response({'code': 409, 'msg': '陪玩账号周期已变化，请重新登录核对'}, status=409)
                from .models import RewardClaim
                claim_id = kwargs.get('pk') if 'pk' in kwargs else (args[0] if args else None)
                claim = RewardClaim.objects.select_for_update().filter(pk=claim_id, account=account).first()
                if claim and not provider_claim_source_current(claim, account):
                    return Response({'code': 409, 'msg': '权益来源周期待核对'}, status=409)
                return fn(view, request, *args, **kwargs)
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
    return wrapped


def staff_provider_claim_edit(fn):
    """Lock a provider recipient before staff lock their claim and tag."""
    @wraps(fn)
    def wrapped(view, request, *args, **kwargs):
        from club_accounts.models import ClubAccount
        from .models import RewardClaim
        if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return fn(view, request, *args, **kwargs)
        claim_id = kwargs.get('pk') if 'pk' in kwargs else (args[0] if args else None)
        initial = RewardClaim.objects.select_related('account').filter(pk=claim_id).first()
        if not initial or initial.account.account_type != ClubAccount.AccountType.PROVIDER:
            return fn(view, request, *args, **kwargs)
        account = initial.account
        from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                       lock_provider_operation)
        try:
            ensure_provider_lifecycle(account)
            with transaction.atomic():
                lock_provider_operation(account)
                if not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                        license_membership_active=True, business_cycle=account.business_cycle).exists():
                    return Response({'code': 409, 'msg': '陪玩账号周期已变化，请重新核对'}, status=409)
                claim = RewardClaim.objects.select_for_update().filter(pk=claim_id, account_id=account.pk,
                        reward_id=initial.reward_id, event_key=initial.event_key, source=initial.source).first()
                if not claim or not provider_claim_source_current(claim, account):
                    return Response({'code': 409, 'msg': '权益归属已变化，请重新核对'}, status=409)
                return fn(view, request, *args, **kwargs)
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
    return wrapped


def staff_provider_naming_edit(fn):
    """Protect the recipient's existing naming before its business row lock."""
    @wraps(fn)
    def wrapped(view, request, *args, **kwargs):
        from club_accounts.models import ClubAccount
        from .models import ProviderNaming
        if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return fn(view, request, *args, **kwargs)
        naming_id = kwargs.get('pk') if 'pk' in kwargs else (args[0] if args else None)
        initial = ProviderNaming.objects.select_related('provider').filter(pk=naming_id).first()
        if not initial or initial.provider.account_type != ClubAccount.AccountType.PROVIDER:
            return fn(view, request, *args, **kwargs)
        account = initial.provider
        from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                       lock_provider_operation)
        try:
            ensure_provider_lifecycle(account)
            with transaction.atomic():
                lock_provider_operation(account)
                if not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                        license_membership_active=True, business_cycle=account.business_cycle).exists():
                    return Response({'code': 409, 'msg': '陪玩账号周期已变化，请重新核对'}, status=409)
                if not ProviderNaming.objects.select_for_update().filter(pk=naming_id, provider_id=account.pk,
                        sponsor_id=initial.sponsor_id, provider_business_cycle=account.business_cycle).exists():
                    return Response({'code': 409, 'msg': '冠名权益归属已变化，请重新核对'}, status=409)
                return fn(view, request, *args, **kwargs)
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
    return wrapped
