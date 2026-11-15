import copy
import os
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.db.utils import load_backend
from django.utils import timezone

from .context import store_context
from .db import transaction
from .models import Store


def create_database(alias, *, admin_username=None):
    """Explicit deployment action; never drops or truncates an existing DB."""
    from .database_identity import assert_distinct_store_database, database_identity, identity_from_config, lock_store_registration
    from .provision_claim import (create_or_verify_mysql, create_or_verify_sqlite,
                                  fingerprint, reserve_admin_intent, reserve_claim)
    from .models import StoreProvisioningClaim
    from django.db import transaction as platform_transaction
    owner = Store.objects.filter(database_alias=alias).first()
    if owner is None:
        raise CommandError('请先登记门店后再创建数据库')
    if owner.initialized_at is not None or owner.status != Store.Status.PROVISIONING:
        raise CommandError('仅待初始化门店可创建或恢复数据库')
    try:
        assert_distinct_store_database(alias, exclude_store_id=owner.pk if owner else None)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    configuration = copy.deepcopy(connections[alias].settings_dict)
    try:
        if identity_from_config(alias, configuration) != database_identity(alias):
            raise CommandError('目标数据库连接配置已变化，不能继续初始化')
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    engine = configuration['ENGINE']
    if 'sqlite' in engine:
        name = str(configuration.get('NAME') or '')
        if not name:
            raise CommandError('目标数据库名称无效，无法安全创建')
        if name.startswith('file:') and 'mode=memory' not in name:
            raise CommandError('无法安全确认 SQLite URI 的库归属，请人工核对')
        if name == ':memory:' or (name.startswith('file:') and 'mode=memory' in name):
            # Volatile, isolated test databases have no durable retry contract.
            reserve_claim(owner)
            if admin_username:
                reserve_admin_intent(owner, admin_username)
            connections[alias].ensure_connection()
            return
    elif 'mysql' not in engine:
        raise CommandError('自动建库仅支持 MySQL/SQLite；其他数据库请先由部署人员创建')
    else:
        name = str(configuration.get('NAME') or '')
    if 'mysql' in engine and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name):
        raise CommandError('MySQL 门店库名须为字母开头的字母数字下划线，最长64位')
    claim = reserve_claim(owner)
    if admin_username:
        reserve_admin_intent(owner, admin_username)
    with platform_transaction.atomic(using='default'):
        lock_store_registration()
        current = Store.objects.select_for_update().get(pk=owner.pk)
        if current.initialized_at is not None or current.status != Store.Status.PROVISIONING:
            raise CommandError('仅待初始化门店可创建或恢复数据库')
        claim = StoreProvisioningClaim.objects.select_for_update().select_related('store').get(pk=claim.pk)
        if claim.database_fingerprint != fingerprint(alias):
            raise CommandError('目标数据库配置已变化，不能继续初始化；请人工核对')
        if 'sqlite' in engine:
            create_or_verify_sqlite(name, claim)
            return
        configuration['NAME'] = ''
        server = load_backend(engine).DatabaseWrapper(configuration, alias=f'provision_{alias}')
        try:
            with server.cursor() as cursor:
                create_or_verify_mysql(server, cursor, name, claim)
        finally:
            server.close()


def has_usable_store_admin(*, superuser_only=False):
    from console.models import AdminMembership
    from club_accounts.models import OtpCredential
    memberships = AdminMembership.objects.filter(is_active=True, account__can_login=True,
        account__is_active=True, account__account_type='STAFF', roles__is_active=True
    ).select_related('account').distinct()
    if superuser_only:
        memberships = memberships.filter(roles__code='super_admin', roles__is_active=True)
    for membership in memberships:
        account = membership.account
        phone = account.phone or ''
        password_second_factor = account.has_usable_password() and (
            (phone.isascii() and phone.isdigit() and 5 <= len(phone) <= 20)
            or OtpCredential.objects.filter(account=account).exists())
        wechat = account.is_openid_bound and bool(account.openid or account.wechat_identities.exists())
        if password_second_factor or wechat:
            return True
    return False


def initialize_store(store, *, stdout=None, admin_username=None, password_env=None,
                     admin_password=None, admin_phone=None, admin_nickname=None,
                     new_database_created=False):
    alias = store.database_alias
    if store.initialized_at is None:
        from .database_identity import assert_distinct_store_database
        try:
            assert_distinct_store_database(alias, exclude_store_id=store.pk)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if not new_database_created:
            raise CommandError('新门店目标库的归属尚未确认，不能自动迁移已有数据库')
        config = connections[alias].settings_dict
        name = str(config.get('NAME') or '')
        volatile = 'sqlite' in config['ENGINE'] and (name == ':memory:' or (name.startswith('file:') and 'mode=memory' in name))
        if not volatile:
            from django.db import transaction as platform_transaction
            from .database_identity import lock_store_registration
            from .models import StoreProvisioningClaim
            from .provision_claim import reserve_admin_intent, verify_owned_database
            reserve_admin_intent(store, admin_username)
            with platform_transaction.atomic(using='default'):
                lock_store_registration()
                current = Store.objects.select_for_update().get(pk=store.pk)
                if current.initialized_at is not None:
                    store.initialized_at, store.status = current.initialized_at, current.status
                    return
                claim = StoreProvisioningClaim.objects.select_for_update().select_related('store').get(store=current)
                verify_owned_database(alias, claim)
                _initialize_store_data(current, stdout=stdout, admin_username=admin_username,
                    password_env=password_env, admin_password=admin_password, admin_phone=admin_phone,
                    admin_nickname=admin_nickname, resume_claim=claim)
                store.initialized_at, store.status = current.initialized_at, current.status
            return
    _initialize_store_data(store, stdout=stdout, admin_username=admin_username, password_env=password_env,
        admin_password=admin_password, admin_phone=admin_phone, admin_nickname=admin_nickname)


def _initialize_store_data(store, *, stdout, admin_username, password_env, admin_password,
                           admin_phone, admin_nickname, resume_claim=None):
    alias = store.database_alias
    with store_context(store):
        # Model-level ORM calls inside historical data migrations are routed to
        # the same target as schema_editor. Registry tables remain on default.
        call_command('migrate', database=alias, interactive=False, verbosity=0, stdout=stdout)
        adapt_initial_branding(store)
        if store.initialized_at is None:
            # The membership setup and opening checks read this singleton
            # without creating it. New stores need the same baseline as the
            # regular membership service even when monthly-coupon features
            # are disabled. Never replace a partially configured retry.
            from engagement.models import ClubOperationConfig
            ClubOperationConfig.objects.get_or_create(pk=1)
            from coupons.monthly_cutover import initialize_new_store_rules
            initialize_new_store_rules(store.slug)
        if admin_username:
            _bootstrap_admin(admin_username, password_env, password=admin_password,
                             phone=admin_phone, nickname=admin_nickname,
                             resume_claim=resume_claim)
        elif resume_claim and resume_claim.admin_username:
            if not _claimed_admin_is_usable(resume_claim.admin_username):
                raise CommandError('原管理员尚未完成创建，请使用原管理员资料继续初始化')
        if store.initialized_at is None:
            if not has_usable_store_admin():
                raise CommandError('新门店尚无可登录后台管理员；请配置手机号、验证器或已绑定微信')
    if store.initialized_at is None:
        store.initialized_at = timezone.now()
        store.status = Store.Status.CONFIGURING
    store.save(update_fields=['initialized_at', 'status', 'updated_at'])


def adapt_initial_branding(store):
    """Adapt display-only configuration seeds once; never rewrite business data.

    Stable dictionary codes, pay rates and monetary values are deliberately not
    derived from a store's name. Subsequent provisioning/upgrades preserve any
    names or text edited by its administrator.
    """
    if store.database_alias == 'default' or store.initialized_at is not None:
        return
    from console.dictionary_models import BusinessDictionaryItem
    from console.payroll_models import SalaryPlan
    from engagement.models import (BroadcastRule, ClubContent, LotteryCampaign,
                                    RewardItem)
    display_fields = (
        (BusinessDictionaryItem, ('name', 'remark')),
        (SalaryPlan, ('name', 'remark')),
        (ClubContent, ('title', 'content')),
        (LotteryCampaign, ('name', 'rules')),
        (BroadcastRule, ('template',)),
        (RewardItem, ('name', 'description')),
    )
    with store_context(store), transaction.atomic():
        for model, fields in display_fields:
            for item in model.objects.all().iterator():
                updates = {}
                for field_name in fields:
                    value = getattr(item, field_name)
                    if not isinstance(value, str) or '声浪' not in value:
                        continue
                    replacement = re.sub(r'声浪电竞|声浪', lambda _: store.name, value)
                    max_length = model._meta.get_field(field_name).max_length
                    updates[field_name] = replacement[:max_length] if max_length else replacement
                if updates:
                    model.objects.filter(pk=item.pk).update(**updates)


def _claimed_admin_is_usable(username):
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.filter(username=username, account_type='STAFF',
                                         is_active=True, can_login=True).first()
    membership = getattr(account, 'admin_membership', None) if account else None
    if not membership or not membership.is_active or not membership.roles.filter(code='super_admin', is_active=True).exists():
        return False
    phone = account.phone or ''
    return bool(account.has_usable_password() and phone.isascii() and phone.isdigit() and 5 <= len(phone) <= 20)


def _bootstrap_admin(username, password_env, *, password=None, phone=None, nickname=None, resume_claim=None):
    from club_accounts.models import ClubAccount
    from club_accounts.services import get_or_create_account_for_legacy_user
    from console.models import AdminMembership, AdminRole
    from console.permissions import ALL_PERMISSION_SET

    username = username.strip()
    if not username or len(username) > 150:
        raise CommandError('管理员用户名必须为1到150位')
    if not isinstance(phone, str) or not phone.isascii() or not phone.isdigit() or not 5 <= len(phone) <= 20:
        raise CommandError('新管理员需要可接收短信的手机号')
    password = password if password is not None else os.environ.get(password_env or '', '')
    if not password:
        raise CommandError('初始化新管理员必须使用 --password-env 指定已设置的密码环境变量')
    existing = ClubAccount.objects.filter(username=username).first()
    legacy = get_user_model().objects.filter(username=username).first()
    if existing or legacy:
        mapping = getattr(existing, 'legacy_mapping', None) if existing else None
        if (resume_claim and resume_claim.admin_username == username and existing and legacy and mapping
                and mapping.legacy_user_id == legacy.pk and existing.phone == phone
                and existing.check_password(password) and _claimed_admin_is_usable(username)):
            return
        raise CommandError('管理员账号已存在或与首次初始化资料不一致；不会覆盖账号、密码或权限')
    user = get_user_model()(username=username, role='ADMIN')
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        raise CommandError('；'.join(exc.messages)) from exc
    with transaction.atomic():
        user.set_password(password)
        user.save()
        account = get_or_create_account_for_legacy_user(user)
        if phone is not None:
            account.phone = phone
            # The first successful SMS challenge proves ownership of this number.
            account.is_phone_verified = False
        account.nickname = nickname or account.nickname or username
        account.save(update_fields=['phone', 'nickname', 'is_phone_verified', 'updated_at'])
        role, _ = AdminRole.objects.get_or_create(code='super_admin', defaults={
            'name': '门店超级管理员', 'permissions': sorted(ALL_PERMISSION_SET),
        })
        membership = AdminMembership.objects.create(user=user, account=account)
        membership.roles.add(role)
