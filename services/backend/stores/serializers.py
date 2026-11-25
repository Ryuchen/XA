from django.conf import settings
from django.utils.html import strip_tags
from rest_framework import serializers

from .media import public_background_url
from .models import Store
from .hosts import store_entry_url


def validate_provider_name_prefix(value):
    value = value.strip().upper()
    if not value.isascii() or not value.isalpha() or not 2 <= len(value) <= 8:
        raise serializers.ValidationError('陪玩展示简称须为2至8位英文字母')
    return value


def validate_store_logo_url(value, request=None):
    if not value:
        return value
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(value)
        base = urlsplit(getattr(settings, 'MEDIA_BASE_URL', ''))
        if parts.scheme or parts.netloc:
            expected_scheme = base.scheme or (request.scheme if request else '')
            expected_host = base.netloc or (request.get_host() if request else '')
            if parts.scheme != expected_scheme or parts.netloc != expected_host:
                raise serializers.ValidationError('请通过门店 Logo 上传功能保存图片')
        if not parts.path.startswith(settings.MEDIA_URL):
            raise serializers.ValidationError('请通过门店 Logo 上传功能保存图片')
    except ValueError as exc:
        raise serializers.ValidationError('门店 Logo 地址无效') from exc
    return value


def public_store(store, request=None):
    from common.media import build_media_url_from_path
    return {
        'id': store.pk, 'slug': store.slug, 'name': store.name, 'currency_name': store.currency_name,
        'entity_prefix': store.entity_prefix,
        'provider_name_prefix': store.provider_name_prefix,
        'logo_url': build_media_url_from_path(request, store.logo_url, store=store),
        'background_url': public_background_url(store, request),
        'introduction': store.introduction,
        'content_compliance_enabled': store.content_compliance_enabled,
        'status': store.status, 'is_active': store.is_active,
        'boss_url': store_entry_url(store.boss_url, store.slug),
        'provider_url': store_entry_url(store.provider_url, store.slug),
        'admin_url': store_entry_url(store.admin_url, store.slug),
    }


def configured_database_aliases():
    from .dynamic_database import enabled
    template = getattr(settings, 'DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS', '') if enabled() else ''
    return [alias for alias in getattr(settings, 'STORE_DATABASE_ALIASES', ())
            if alias in settings.DATABASES and alias not in ('default', template)]


def validate_introduction(value):
    if strip_tags(value) != value:
        raise serializers.ValidationError('请填写纯文本介绍')


def _check_store_content(serializer, field, value, *, enabled):
    from common.compliance import check_content
    result = check_content(field, value, enabled=enabled)
    if result is not None:
        if not hasattr(serializer, '_compliance_results'):
            serializer._compliance_results = {}
        serializer._compliance_results[field] = result
    return value


def _validate_store_content(serializer, attrs):
    enabled = attrs.get(
        'content_compliance_enabled',
        getattr(serializer.instance, 'content_compliance_enabled', True),
    )
    fields = {'name': 'STORE_NAME', 'introduction': 'STORE_INTRODUCTION'}
    for name, compliance_field in fields.items():
        if name in attrs:
            _check_store_content(serializer, compliance_field, attrs[name], enabled=enabled)


class StoreCreateSerializer(serializers.ModelSerializer):
    database_alias = serializers.CharField(max_length=64, required=False)
    entity_prefix = serializers.CharField(max_length=4, required=True, allow_blank=False)
    provider_name_prefix = serializers.CharField(max_length=8, required=False)
    currency_name = serializers.CharField(max_length=16, required=False, allow_blank=True)
    logo_url = serializers.CharField(max_length=500, required=False, allow_blank=True)
    introduction = serializers.CharField(max_length=2000, required=False, allow_blank=True, validators=[validate_introduction])
    admin_username = serializers.CharField(max_length=150, required=False, write_only=True)
    admin_password = serializers.CharField(required=False, write_only=True, trim_whitespace=False)
    admin_phone = serializers.CharField(max_length=20, required=False, write_only=True)
    admin_nickname = serializers.CharField(max_length=50, required=False, write_only=True)

    class Meta:
        model = Store
        fields = ['slug', 'name', 'entity_prefix', 'provider_name_prefix', 'currency_name', 'logo_url', 'introduction', 'content_compliance_enabled', 'database_alias', 'boss_url', 'provider_url', 'admin_url',
                  'admin_username', 'admin_password', 'admin_phone', 'admin_nickname']

    def validate_logo_url(self, value):
        return validate_store_logo_url(value, self.context.get('request'))

    def validate_entity_prefix(self, value):
        value = value.strip().upper()
        if not value.isascii() or not value.isalnum() or not 2 <= len(value) <= 4:
            raise serializers.ValidationError('编号前缀须为2至4位大写字母或数字')
        if Store.objects.filter(entity_prefix=value).exists():
            raise serializers.ValidationError('该编号前缀已被其他门店使用')
        return value

    def validate_provider_name_prefix(self, value):
        return validate_provider_name_prefix(value)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('请输入门店名称')
        return value

    def validate_introduction(self, value):
        validate_introduction(value)
        return value

    def validate_currency_name(self, value):
        return value.strip()

    def validate_admin_phone(self, value):
        if not value.isascii() or not value.isdigit() or not 5 <= len(value) <= 20:
            raise serializers.ValidationError('请输入可用于短信验证的手机号')
        return value

    def validate(self, attrs):
        _validate_store_content(self, attrs)
        from .dynamic_database import alias_for_slug, enabled, materialize
        # A newly provisioned store must choose its own coin name before
        # opening; the model's default belongs to neither a new SaaS tenant
        # nor an unknown brand.
        attrs.setdefault('currency_name', '')
        slug, alias = attrs.get('slug', ''), attrs.get('database_alias')
        if not alias:
            if not enabled():
                raise serializers.ValidationError({'database_alias': '请先配置独立业务库'})
            try:
                attrs['database_alias'] = materialize(slug)
            except ValueError as exc:
                raise serializers.ValidationError({'database_alias': str(exc)}) from exc
            if Store.objects.filter(database_alias=attrs['database_alias']).exists():
                raise serializers.ValidationError({'database_alias': '该数据库已分配给其他门店'})
            from .database_identity import assert_distinct_store_database
            try:
                assert_distinct_store_database(attrs['database_alias'])
            except ValueError as exc:
                raise serializers.ValidationError({'database_alias': str(exc)}) from exc
        elif enabled() and alias.startswith('tenant_') and alias != alias_for_slug(slug):
            raise serializers.ValidationError({'database_alias': '门店数据库标识不匹配'})
        fields = ('admin_username', 'admin_password', 'admin_phone', 'admin_nickname')
        if not any(attrs.get(field) for field in fields):
            return attrs
        if any(not attrs.get(field) for field in fields):
            raise serializers.ValidationError('首位门店管理员需要完整填写账号、密码、手机号和昵称')
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(attrs['admin_password'], user=get_user_model()(username=attrs['admin_username']))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'admin_password': exc.messages}) from exc
        return attrs

    def validate_database_alias(self, value):
        if value not in configured_database_aliases():
            raise serializers.ValidationError('请先由部署人员配置该门店独立数据库')
        if Store.objects.filter(database_alias=value).exists():
            raise serializers.ValidationError('该数据库已分配给其他门店')
        from .database_identity import assert_distinct_store_database
        try:
            assert_distinct_store_database(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value


class StoreResumeSerializer(serializers.Serializer):
    admin_username = serializers.CharField(max_length=150, required=False)
    admin_password = serializers.CharField(required=False, trim_whitespace=False, write_only=True)
    admin_phone = serializers.CharField(max_length=20, required=False)
    admin_nickname = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def validate(self, attrs):
        supplied = [attrs.get(key) for key in ('admin_username', 'admin_password', 'admin_phone')]
        if any(supplied) and not all(supplied):
            raise serializers.ValidationError('恢复时请完整填写原管理员账号、密码和手机号')
        if attrs.get('admin_nickname') and not all(supplied):
            raise serializers.ValidationError('填写管理员昵称时须同时提供管理员账号、密码和手机号')
        phone = attrs.get('admin_phone', '')
        if phone and (not phone.isascii() or not phone.isdigit() or not 5 <= len(phone) <= 20):
            raise serializers.ValidationError({'admin_phone': '请输入可用于短信验证的手机号'})
        return attrs


class StoreUpdateSerializer(serializers.ModelSerializer):
    logo_url = serializers.CharField(max_length=500, required=False, allow_blank=True)
    introduction = serializers.CharField(max_length=2000, required=False, allow_blank=True, validators=[validate_introduction])
    provider_name_prefix = serializers.CharField(max_length=8, required=False)

    class Meta:
        model = Store
        fields = ['name', 'provider_name_prefix', 'currency_name', 'logo_url', 'background_url', 'introduction',
                  'content_compliance_enabled', 'status', 'boss_url', 'provider_url', 'admin_url']

    def validate_provider_name_prefix(self, value):
        return validate_provider_name_prefix(value)

    def validate_logo_url(self, value):
        if value and self.instance:
            from common.media import build_media_url_from_path
            if value == build_media_url_from_path(None, self.instance.logo_url, store=self.instance):
                return self.instance.logo_url
        return validate_store_logo_url(value, self.context.get('request'))

    def validate_background_url(self, value):
        if not value:
            return value
        from django.core.files.storage import default_storage
        from .context import store_context
        from .media import verified_background_path
        with store_context(self.instance):
            name = verified_background_path(value)
            if not name:
                raise serializers.ValidationError('请上传并选择本店可读取的背景图片')
            return default_storage.url(name)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('请输入门店名称')
        return value

    def validate_introduction(self, value):
        validate_introduction(value)
        return value

    def validate_status(self, value):
        if value in (Store.Status.PROVISIONING, Store.Status.CONFIGURING):
            raise serializers.ValidationError('初始化状态仅由部署命令维护')
        if self.instance.status == Store.Status.CONFIGURING:
            raise serializers.ValidationError('门店仍在配置中，需完成开店核验后由平台启用')
        if value == Store.Status.DISABLED and self.instance.database_alias == 'default':
            raise serializers.ValidationError('平台所属门店承担平台登录入口，不能在此停用')
        if value == Store.Status.ACTIVE:
            if not self.instance.initialized_at:
                raise serializers.ValidationError('请先执行 provision_store 完成数据库迁移和管理员初始化')
            from .dynamic_database import store_connection_ready
            if not store_connection_ready(self.instance):
                raise serializers.ValidationError('门店数据库未配置，不能营业')
        return value

    def validate(self, attrs):
        _validate_store_content(self, attrs)
        return attrs
