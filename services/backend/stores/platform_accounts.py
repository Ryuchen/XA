"""Audited control-plane account and grant changes, serialized per directory."""
from collections.abc import Mapping
import uuid

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework_simplejwt.exceptions import TokenError

from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store
from .platform_auth import account_for_token
from .platform_permissions import platform_may


class AccountChangeError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(data, name, limit, *, required=False):
    value = data.get(name, '')
    if not isinstance(value, str):
        raise AccountChangeError(f'{name} 格式不正确')
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise AccountChangeError(f'{name} 长度不正确')
    return value


def _grants(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise AccountChangeError('请至少配置一项平台职能授权')
    found = set()
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {'role', 'scope_slug'}:
            raise AccountChangeError('平台职能授权格式不正确')
        role, scope = item['role'], item['scope_slug']
        if role not in PlatformRoleGrant.Role.values or not isinstance(scope, str):
            raise AccountChangeError('平台职能或门店范围无效')
        if scope != '*' and not Store.objects.filter(slug=scope).exists():
            raise AccountChangeError('授权门店不存在')
        if role == 'SUPER_ADMIN' and scope != '*':
            raise AccountChangeError('平台超级管理员须授权全部门店')
        if (role, scope) in found:
            raise AccountChangeError('平台职能授权重复')
        found.add((role, scope))
    for role in PlatformRoleGrant.Role.values:
        if (role, '*') in found and any(r == role and scope != '*' for r, scope in found):
            raise AccountChangeError('全部门店授权不能与同职能的单店授权重复')
    return found


def _password(raw, account):
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise AccountChangeError('密码格式不正确')
    try:
        validate_password(raw, user=account)
    except ValidationError as error:
        raise AccountChangeError('密码不符合安全要求：' + '；'.join(error.messages)) from None
    account.set_password(raw)


def serialize(account):
    return {'id': str(account.pk), 'username': account.username, 'nickname': account.nickname,
            'phone': account.phone, 'is_phone_verified': account.is_phone_verified,
            'is_active': account.is_active, 'updated_at': account.updated_at.isoformat(),
            'grants': list(account.grants.filter(is_active=True).order_by('role', 'scope_slug').values('role', 'scope_slug'))}


def _locked_actor(token):
    # Lock the same stable anchor used by bootstrap. This serializes grant and
    # last-super-admin checks across concurrent management requests.
    from .database_identity import lock_store_registration
    try:
        lock_store_registration()
    except ValueError as exc:
        raise AccountChangeError('平台目录尚未初始化', 409) from exc
    try:
        actor = account_for_token(token)
    except TokenError:
        raise AccountChangeError('平台登录已失效，请重新登录', 401) from None
    if not platform_may(actor, 'platform_accounts:manage'):
        raise AccountChangeError('无平台人员管理权限', 403)
    return actor


def _audit(actor, action, target, detail):
    PlatformAuditEvent.objects.create(account=actor, action=action, target_type='platform_account',
                                      target_id=str(target.pk), detail=detail)


def create_account(token, data):
    if not isinstance(data, Mapping) or set(data) != {'username', 'password', 'nickname', 'phone', 'grants'}:
        raise AccountChangeError('账号信息格式不正确')
    username = _text(data, 'username', 150, required=True)
    nickname = _text(data, 'nickname', 80)
    phone = _text(data, 'phone', 20, required=True)
    if '/' in username or not phone.isascii() or not phone.isdigit() or len(phone) < 5:
        raise AccountChangeError('账号或手机号格式不正确')
    account = PlatformAccount(username=username, nickname=nickname, phone=phone)
    _password(data['password'], account)
    try:
        with transaction.atomic(using='default'):
            actor = _locked_actor(token)
            roles = _grants(data['grants'])
            if PlatformAccount.objects.filter(username=username).exists():
                raise AccountChangeError('平台账号已存在', 409)
            account.save()
            for role, scope in sorted(roles):
                PlatformRoleGrant.objects.create(account=account, role=role, scope_slug=scope)
            _audit(actor, 'platform.account_created', account, {'username': username, 'grants': [{'role': r, 'scope_slug': s} for r, s in sorted(roles)]})
            return serialize(account)
    except IntegrityError:
        raise AccountChangeError('平台账号或授权冲突，请刷新后重试', 409) from None


def update_account(token, account_id, data):
    allowed = {'expected_updated_at', 'nickname', 'phone', 'is_active', 'password', 'grants'}
    if not isinstance(data, Mapping) or 'expected_updated_at' not in data or not set(data) <= allowed or len(data) == 1:
        raise AccountChangeError('变更信息格式不正确')
    try:
        expected = parse_datetime(data['expected_updated_at']) if isinstance(data['expected_updated_at'], str) else None
    except ValueError:
        expected = None
    if expected is None or timezone.is_naive(expected):
        raise AccountChangeError('请刷新账号后重新提交')
    try:
        with transaction.atomic(using='default'):
            actor = _locked_actor(token)
            try:
                target = PlatformAccount.objects.select_for_update().get(pk=account_id)
            except PlatformAccount.DoesNotExist:
                raise AccountChangeError('平台账号不存在', 404) from None
            if target.updated_at != expected:
                raise AccountChangeError('账号已被其他人修改，请刷新后重试', 409)
            old_roles = set(target.grants.filter(is_active=True).values_list('role', 'scope_slug'))
            roles = _grants(data['grants']) if 'grants' in data else old_roles
            active = data.get('is_active', target.is_active)
            if type(active) is not bool:
                raise AccountChangeError('账号状态格式不正确')
            if target.is_active and ('SUPER_ADMIN', '*') in old_roles and (not active or ('SUPER_ADMIN', '*') not in roles):
                if not PlatformRoleGrant.objects.filter(role='SUPER_ADMIN', scope_slug='*', is_active=True,
                        account__is_active=True).exclude(account=target).exists():
                    raise AccountChangeError('不能停用或移除最后一个平台超级管理员', 409)
            changed = []
            security_changed = False
            if 'nickname' in data:
                target.nickname = _text(data, 'nickname', 80)
                changed.append('nickname')
            if 'phone' in data:
                phone = _text(data, 'phone', 20, required=True)
                if not phone.isascii() or not phone.isdigit() or len(phone) < 5:
                    raise AccountChangeError('手机号格式不正确')
                if phone != target.phone:
                    target.phone, target.is_phone_verified = phone, False
                    changed.append('phone')
                    security_changed = True
            if 'password' in data:
                _password(data['password'], target)
                changed.append('password')
            if 'is_active' in data:
                security_changed = security_changed or active != target.is_active
                target.is_active = active
                changed.append('is_active')
            if 'grants' in data:
                security_changed = security_changed or roles != old_roles
                target.grants.filter(is_active=True).update(is_active=False)
                for role, scope in sorted(roles):
                    PlatformRoleGrant.objects.update_or_create(account=target, role=role, scope_slug=scope,
                                                                defaults={'is_active': True})
                changed.append('grants')
            if security_changed:
                target.security_version = uuid.uuid4()
            target.save()
            _audit(actor, 'platform.account_updated', target, {'changed_fields': changed,
                'is_active': target.is_active, 'grants_before': sorted([list(item) for item in old_roles]),
                'grants_after': sorted([list(item) for item in roles])})
            return serialize(target)
    except IntegrityError:
        raise AccountChangeError('平台账号或授权冲突，请刷新后重试', 409) from None
