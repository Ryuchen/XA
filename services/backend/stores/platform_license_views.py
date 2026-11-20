"""Platform-only license issuance. Codes are returned exactly once."""
from collections.abc import Mapping

from django.db import DatabaseError
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from .hosts import is_admin_host
from .license_codes import LicenseCodeError, issue, reissue, void
from .models import Store, StoreLicenseActivatorGrant, StoreLicenseCode
from .platform_accounts import AccountChangeError, _locked_actor
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may


def _response(payload, status=200):
    response = Response(payload, status=status)
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    return response


def _serialize(row, *, store_slug=None):
    return {'id': str(row.pk), 'store_slug': store_slug if store_slug is not None else row.store.slug, 'tier': row.tier, 'kind': row.kind,
            'contract_id': str(row.contract_id), 'reissued_from': str(row.reissued_from_id) if row.reissued_from_id else None,
            'starts_at': row.starts_at.isoformat(), 'ends_at': row.ends_at.isoformat(),
            'created_at': row.created_at.isoformat(),
            'activated_at': row.activated_at.isoformat() if row.activated_at else None,
            'voided_at': row.voided_at.isoformat() if row.voided_at else None,
            'void_reason': row.void_reason}


def _serialize_platform_code(row):
    """Platform audit fields; never reveal the one-time code or its digest."""
    created = row.created_by
    voided = row.voided_by
    return {**_serialize(row), 'backdate_reason': row.backdate_reason or None,
            'created_by': {'id': str(created.pk), 'username': created.username, 'nickname': created.nickname},
            'voided_by': ({'id': str(voided.pk), 'username': voided.username, 'nickname': voided.nickname}
                          if voided else None),
            'activated_by_account_id': row.activated_by_id}


class _PlatformLicenseBase(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def allowed(self, request):
        return is_admin_host(request.get_host()) and platform_may(request.platform_account, 'stores:manage')

    def denied(self):
        return _response({'code': 403, 'msg': '需要平台超级管理员权限'}, 403)


class PlatformStoreLicensesView(_PlatformLicenseBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        try:
            store = Store.objects.filter(pk=pk).first()
            if not store:
                return _response({'code': 404, 'msg': '门店不存在'}, 404)
            from uuid import UUID
            from django.core import signing
            from django.utils import timezone
            cursor = request.query_params.get('cursor')
            query = StoreLicenseCode.objects.filter(store=store).select_related('store', 'created_by', 'voided_by')
            if cursor:
                try:
                    payload = signing.loads(cursor, salt='stores.license-list.v1')
                    if not isinstance(payload, dict) or set(payload) != {'store_id', 'created_at', 'id'}:
                        raise ValueError
                    when = parse_datetime(payload['created_at'])
                    row_id = UUID(payload['id'])
                    if payload['store_id'] != store.pk or not when or timezone.is_naive(when):
                        raise ValueError
                except (signing.BadSignature, ValueError, TypeError, KeyError, AttributeError):
                    return _response({'code': 400, 'msg': '授权列表游标无效，请重新读取'}, 400)
                query = query.filter(Q(created_at__lt=when) | Q(created_at=when, id__lt=row_id))
            rows = list(query.order_by('-created_at', '-id')[:101])
            has_more = len(rows) > 100
            rows = rows[:100]
            next_cursor = None
            if has_more:
                last = rows[-1]
                next_cursor = signing.dumps({'store_id': store.pk, 'created_at': last.created_at.isoformat(),
                                             'id': str(last.pk)}, salt='stores.license-list.v1', compress=True)
            return _response({'code': 0, 'data': {'results': [_serialize_platform_code(row) for row in rows],
                                                   'next_cursor': next_cursor}})
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)

    def post(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping):
            return _response({'code': 400, 'msg': '授权信息无效'}, 400)
        required = {'tier', 'kind'}
        if not required <= set(request.data) or set(request.data) - (required | {'starts_at', 'ends_at', 'backdate_reason', 'amount_due_fen'}):
            return _response({'code': 400, 'msg': '授权信息无效'}, 400)
        try:
            start_value, end_value = request.data.get('starts_at'), request.data.get('ends_at')
            starts_at = parse_datetime(start_value) if isinstance(start_value, str) else None
            ends_at = parse_datetime(end_value) if isinstance(end_value, str) else None
            if (('starts_at' in request.data and starts_at is None)
                    or ('ends_at' in request.data and ends_at is None)):
                raise ValueError
        except ValueError:
            return _response({'code': 400, 'msg': '授权起止时间无效'}, 400)
        store = Store.objects.filter(pk=pk).first()
        if not store:
            return _response({'code': 404, 'msg': '门店不存在'}, 404)
        try:
            row, code = issue(request.auth, store_slug=store.slug, tier=request.data['tier'], kind=request.data['kind'],
                              starts_at=starts_at, ends_at=ends_at,
                              backdate_reason=request.data.get('backdate_reason', ''),
                              amount_due_fen=request.data.get('amount_due_fen'))
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except LicenseCodeError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except TypeError:
            return _response({'code': 400, 'msg': '授权信息无效'}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)
        return _response({'code': 0, 'data': {**_serialize(row), 'license_code': code}}, 201)


class PlatformLicenseVoidView(_PlatformLicenseBase):
    def post(self, request, code_id):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'reason'}:
            return _response({'code': 400, 'msg': '请填写作废原因'}, 400)
        try:
            row = void(request.auth, code_id=code_id, reason=request.data['reason'])
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except LicenseCodeError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)
        return _response({'code': 0, 'data': _serialize(row)})


class PlatformLicenseReissueView(_PlatformLicenseBase):
    def post(self, request, code_id):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or request.data:
            return _response({'code': 400, 'msg': '重发请求格式无效'}, 400)
        try:
            row, code = reissue(request.auth, code_id=code_id)
            return _response({'code': 0, 'data': {**_serialize(row), 'license_code': code}}, 201)
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except LicenseCodeError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)


class PlatformLicenseActivatorListView(_PlatformLicenseBase):
    """List explicit delegations and, when available, the same-store identity."""

    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        from django.db import transaction
        from club_accounts.models import ClubAccount
        from console.permissions import is_console_account
        from .context import store_context
        from .dynamic_database import store_connection_ready
        try:
            with transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                if not platform_may(actor, 'stores:manage'):
                    return self.denied()
                store = Store.objects.filter(pk=pk).first()
                if not store:
                    return _response({'code': 404, 'msg': '门店不存在'}, 404)
                grants = list(StoreLicenseActivatorGrant.objects.filter(store=store).order_by('account_id'))
                identities = None
                try:
                    if store_connection_ready(store):
                        with store_context(store), transaction.atomic(using=store.database_alias):
                            identities = {account.pk: account for account in ClubAccount.objects.filter(
                                pk__in=[grant.account_id for grant in grants]).select_related('admin_membership')}
                            # Resolve membership and roles while tenant context is active.
                            identities = {pk: (account.account_no, account.nickname, is_console_account(account))
                                          for pk, account in identities.items()}
                except (DatabaseError, ValueError):
                    identities = None
                results = []
                for grant in grants:
                    identity = identities.get(grant.account_id) if identities is not None else None
                    if identities is None:
                        state, nickname, eligible = 'UNKNOWN', None, None
                    elif identity is None:
                        state, nickname, eligible = 'MISSING', None, False
                    elif identity[0] != grant.account_no:
                        state, nickname, eligible = 'MISMATCH', None, False
                    else:
                        state, nickname, eligible = 'MATCH', identity[1], identity[2]
                    results.append({'account_id': grant.account_id, 'account_no': grant.account_no,
                        'nickname': nickname, 'grant_active': grant.is_active, 'identity_status': state,
                        'account_eligible': eligible, 'created_at': grant.created_at.isoformat(),
                        'updated_at': grant.updated_at.isoformat()})
                return _response({'code': 0, 'data': {'store_slug': store.slug,
                    'identity_available': identities is not None, 'results': results}})
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)


class PlatformLicenseActivatorView(_PlatformLicenseBase):
    def put(self, request, pk, account_id):
        return self._change(request, pk, account_id, True)

    def delete(self, request, pk, account_id):
        return self._change(request, pk, account_id, False)

    def _change(self, request, pk, account_id, active):
        if not self.allowed(request):
            return self.denied()
        from django.conf import settings
        from django.db import transaction
        from club_accounts.models import ClubAccount
        from console.permissions import is_console_account
        from .context import store_context
        from .models import PlatformAuditEvent

        try:
            with transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = Store.objects.select_for_update().filter(pk=pk).first()
                if not store:
                    return _response({'code': 404, 'msg': '门店不存在'}, 404)
                from .dynamic_database import store_connection_ready
                if not store_connection_ready(store):
                    return _response({'code': 503, 'msg': '门店数据暂不可用'}, 503)
                grant = StoreLicenseActivatorGrant.objects.select_for_update().filter(store=store, account_id=account_id).first()
                if active:
                    with store_context(store):
                        staff = ClubAccount.objects.filter(pk=account_id).first()
                        if not is_console_account(staff):
                            return _response({'code': 400, 'msg': '目标须为本店可登录的后台管理员'}, 400)
                        account_no = staff.account_no
                    if grant is None:
                        grant = StoreLicenseActivatorGrant.objects.create(store=store, account_id=account_id,
                            account_no=account_no, is_active=True, updated_by=actor)
                    else:
                        grant.account_no, grant.is_active, grant.updated_by = account_no, True, actor
                        grant.save(update_fields=['account_no', 'is_active', 'updated_by', 'updated_at'])
                elif grant is None:
                    return _response({'code': 404, 'msg': '授权记录不存在'}, 404)
                else:
                    grant.is_active, grant.updated_by = False, actor
                    grant.save(update_fields=['is_active', 'updated_by', 'updated_at'])
                PlatformAuditEvent.objects.create(account=actor, action='license.activator_granted' if active else 'license.activator_revoked',
                    target_store_slug=store.slug, target_type='license_activator', target_id=str(account_id),
                    detail={'account_no': grant.account_no})
                return _response({'code': 0, 'data': {'store_slug': store.slug, 'account_id': account_id, 'is_active': active}})
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务或门店数据暂不可用'}, 503)


class PlatformLicenseSettingsView(_PlatformLicenseBase):
    def get(self, request):
        if not self.allowed(request):
            return self.denied()
        from .models import PlatformLicenseSettings
        try:
            days = PlatformLicenseSettings.objects.filter(pk=1).values_list('default_grace_days', flat=True).first()
            return _response({'code': 0, 'data': {'default_grace_days': 7 if days is None else days}})
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)

    def put(self, request):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'default_grace_days'}:
            return _response({'code': 400, 'msg': '请填写宽限期天数'}, 400)
        from .license_status import change_grace
        try:
            change_grace(request.auth, days=request.data['default_grace_days'])
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except ValueError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)
        return self.get(request)


class PlatformStoreLicenseStatusView(_PlatformLicenseBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        from .license_status import status_for_store
        try:
            store = Store.objects.filter(pk=pk).first()
            if not store:
                return _response({'code': 404, 'msg': '门店不存在'}, 404)
            return _response({'code': 0, 'data': status_for_store(store)})
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)

    def put(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'grace_days_override'}:
            return _response({'code': 400, 'msg': '请填写门店宽限期或恢复平台默认'}, 400)
        from .license_status import change_grace
        try:
            change_grace(request.auth, days=request.data['grace_days_override'], store_id=pk)
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except ValueError as error:
            return _response({'code': 400, 'msg': str(error)}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)
        return self.get(request, pk)


class PlatformLicenseTierFeaturesView(_PlatformLicenseBase):
    def get(self, request, tier):
        if not self.allowed(request):
            return self.denied()
        from .license_features import tier_features
        from .license_policy import TIERS
        try:
            features = tier_features(tier)
            plan = TIERS[tier]
            return _response({'code': 0, 'data': {'tier': tier, 'annual_price_fen': plan.annual_price_fen,
                'boss_limit': plan.boss_limit, 'provider_limit': plan.provider_limit, 'features': features}})
        except ValueError:
            return _response({'code': 400, 'msg': '档位或功能配置无效'}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)


class PlatformLicenseTierFeatureView(_PlatformLicenseBase):
    def get(self, request, tier, feature):
        if not self.allowed(request):
            return self.denied()
        from django.db import transaction
        from .license_features import tier_features
        from .models import PlatformAuditEvent
        try:
            with transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                if not platform_may(actor, 'stores:manage'):
                    return self.denied()
                features = tier_features(tier)
                if feature not in features:
                    return _response({'code': 400, 'msg': '档位或功能配置无效'}, 400)
                events = PlatformAuditEvent.objects.filter(action='license.tier_feature_changed',
                    target_type='license_tier_feature', target_id=f'{tier}:{feature}').select_related('account')
                audit = []
                for event in events.order_by('-created_at', '-id'):
                    detail = event.detail if isinstance(event.detail, dict) else {}
                    audit.append({'id': event.pk, 'created_at': event.created_at.isoformat(),
                        'actor': {'id': str(event.account_id), 'username': event.account.username,
                                  'nickname': event.account.nickname},
                        'before_recorded': 'before' in detail, 'before_override': detail.get('before'),
                        'after_recorded': 'after' in detail, 'after_override': detail.get('after')})
                return _response({'code': 0, 'data': {'tier': tier, 'feature': feature,
                                                       **features[feature], 'audit_events': audit}})
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except ValueError:
            return _response({'code': 400, 'msg': '档位或功能配置无效'}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)

    def put(self, request, tier, feature):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'mode'}:
            return _response({'code': 400, 'msg': '请填写功能状态'}, 400)
        from .license_features import change_feature, tier_features
        try:
            change_feature(request.auth, tier=tier, feature=feature, mode=request.data['mode'])
            return _response({'code': 0, 'data': {'tier': tier, 'feature': feature, **tier_features(tier)[feature]}})
        except AccountChangeError as error:
            return _response({'code': error.status, 'msg': str(error)}, error.status)
        except (ValueError, TypeError):
            return _response({'code': 400, 'msg': '档位或功能配置无效'}, 400)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权服务暂不可用'}, 503)


class PlatformLicenseNoticesView(_PlatformLicenseBase):
    def get(self, request):
        if not self.allowed(request):
            return self.denied()
        from .license_notices import _text
        from .models import PlatformLicenseNotice
        try:
            queryset = PlatformLicenseNotice.objects.filter(account=request.platform_account).select_related('notice__store').order_by('-created_at', '-id')
            unread = queryset.filter(is_read=False).count()
            before = request.query_params.get('before')
            if before is not None:
                if len(before) > 19 or not before.isascii() or not before.isdigit() or not 0 < int(before) <= 9223372036854775807:
                    return _response({'code': 400, 'msg': '提醒分页参数无效'}, 400)
                anchor = queryset.filter(pk=int(before)).values('created_at', 'pk').first()
                if anchor is None:
                    return _response({'code': 404, 'msg': '提醒分页位置不存在'}, 404)
                queryset = queryset.filter(Q(created_at__lt=anchor['created_at']) |
                    Q(created_at=anchor['created_at'], pk__lt=anchor['pk']))
            page = list(queryset[:101])
            result = []
            for delivery in page[:100]:
                title, detail = _text(delivery.notice)
                result.append({'id': delivery.pk, 'store_slug': delivery.notice.store.slug,
                    'source': 'BUSINESS_CERTIFICATE' if delivery.notice.business_certificate_id else 'LICENSE',
                    'kind': delivery.notice.kind, 'title': title, 'detail': detail,
                    'is_read': delivery.is_read, 'created_at': delivery.created_at.isoformat()})
            return _response({'code': 0, 'data': {'results': result, 'unread': unread,
                              'next_before': page[99].pk if len(page) > 100 else None}})
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权提醒暂不可用'}, 503)

class PlatformLicenseNoticeReadView(_PlatformLicenseBase):
    def post(self, request, notice_id):
        if not self.allowed(request):
            return self.denied()
        from .models import PlatformLicenseNotice
        try:
            updated = PlatformLicenseNotice.objects.filter(pk=notice_id, account=request.platform_account).update(is_read=True)
            return _response({'code': 0, 'msg': '已读'}) if updated else _response({'code': 404, 'msg': '提醒不存在'}, 404)
        except DatabaseError:
            return _response({'code': 503, 'msg': '授权提醒暂不可用'}, 503)
