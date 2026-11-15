"""Tenant-scoped license activation at the neutral admin origin."""
from collections.abc import Mapping
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser
from .hosts import is_admin_host
from .license_activation import LicenseActivationForbidden, activate_for_authenticated_staff
from .license_codes import LicenseCodeError
from .platform_license_views import _serialize


def _response(payload, status=200):
    response = Response(payload, status=status)
    response['Cache-Control'] = 'no-store'
    return response


class StoreLicenseActivationView(APIView):
    permission_classes = [IsClubAccountAuthenticated, IsConsoleUser]
    throttle_scope = 'login'

    def get(self, request):
        store = getattr(request, 'store', None)
        if not is_admin_host(request.get_host()) or not store:
            return _response({'code': 403, 'msg': '无门店授权查看权限'}, 403)
        from .license_snapshot import business_status
        try:
            status = business_status(store)
            # The signed payload is internal evidence, not a license code or
            # a credential that needs to be returned to a store administrator.
            status.pop('verified_snapshot', None)
            status['grace_settings_available'] = status['license_source'] == 'ONLINE'
            if not status['grace_settings_available']:
                status['default_grace_days'] = None
                status['grace_days_override'] = None
            status['grace_ends_at'] = None
            if status['current']:
                try:
                    end = parse_datetime(status['current']['ends_at'])
                    status['grace_ends_at'] = (end + timedelta(days=status['grace_days'])).isoformat()
                except (KeyError, TypeError, ValueError, OverflowError):
                    pass
            status['activation_service_available'] = status['license_source'] == 'ONLINE'
            membership = request.account.admin_membership
            is_super = membership.roles.filter(code='super_admin', is_active=True).exists()
            if is_super:
                status['can_activate'] = True
            elif status['license_source'] != 'ONLINE':
                status['can_activate'] = None
            else:
                from .models import StoreLicenseActivatorGrant
                try:
                    with transaction.atomic(using='default'):
                        status['can_activate'] = StoreLicenseActivatorGrant.objects.filter(
                            store=store, account_id=request.account.pk,
                            account_no=request.account.account_no, is_active=True).exists()
                except DatabaseError:
                    status['can_activate'] = None
                    status['activation_service_available'] = False
            if status.get('can_activate') is True:
                from club_accounts.models import LicenseQuotaLock
                from .license_policy import TIERS
                tier = TIERS.get(status.get('tier'))
                counts = dict(LicenseQuotaLock.objects.filter(
                    account_type__in=('BOSS', 'PROVIDER')).values_list('account_type', 'active_count'))
                status['account_quota'] = {
                    kind: {'used': counts.get(kind),
                           'limit': getattr(tier, field) if tier else None,
                           'unlimited': (getattr(tier, field) is None) if tier else None}
                    for kind, field in (('BOSS', 'boss_limit'), ('PROVIDER', 'provider_limit'))
                }
            return _response({'code': 0, 'data': status})
        except (DatabaseError, ValueError):
            return _response({'code': 503, 'errorCode': 'LICENSE_UNAVAILABLE',
                              'msg': '授权状态暂无法核实，请联系平台', 'data': {'status': 'UNKNOWN'}}, 503)

    def post(self, request):
        store = getattr(request, 'store', None)
        if not is_admin_host(request.get_host()) or not store:
            return _response({'code': 403, 'msg': '无门店授权激活权限'}, 403)
        if not isinstance(request.data, Mapping) or set(request.data) != {'license_code'}:
            return _response({'code': 400, 'msg': '授权码格式无效'}, 400)
        try:
            row, created = activate_for_authenticated_staff(store=store, claims=request.auth,
                account_id=request.account.pk, code=request.data['license_code'])
        except LicenseActivationForbidden:
            return _response({'code': 403, 'msg': '无门店授权激活权限'}, 403)
        except LicenseCodeError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)
        # The verified store instance avoids another directory lookup while
        # formatting an activation that has already committed.
        result = _serialize(row, store_slug=store.slug)
        # Activation is already committed in the control database. Recheck it
        # now so the normal signed-cache publication can run even if no other
        # store business request arrives before a control-plane outage.
        # Publication is best effort; never reinterpret a committed activation
        # as a failure or claim that the cache write itself succeeded.
        try:
            from .license_snapshot import business_status
            checked = business_status(store)
            recheck = 'ONLINE' if checked.get('license_source') == 'ONLINE' else 'CACHE'
        except (DatabaseError, ValueError):
            recheck = 'UNAVAILABLE'
        return _response({'code': 0, 'data': {**result, 'newly_activated': created,
                                              'license_recheck': recheck}})
