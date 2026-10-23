"""Public enrollment credentials never grant provider access before manual approval."""
import base64
import hashlib
import hmac
import re
from datetime import date
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from users.models import CustomUser, ProviderRegistration
from wallet.club_rules import deposit_target, deposit_rules

IMAGE_FIELDS = ('id_front', 'id_back', 'deposit_proof')


def _cipher(key):
    digest = hashlib.sha256(('xa.registration.identity.v1:' + key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_identity(value):
    return _cipher(settings.SECRET_KEY).encrypt(value.encode()).decode()


def decrypt_identity(value):
    for key in (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS):
        try:
            return _cipher(key).decrypt(value.encode()).decode()
        except InvalidToken:
            continue
    raise ValueError('实名资料解密失败')


def identity_hash(value, key=None):
    return hmac.new(
        (key or settings.SECRET_KEY).encode(),
        ('xa.registration.identity.v1:' + value).encode(), hashlib.sha256,
    ).hexdigest()


def validate_identity(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[1-9][0-9]{16}[0-9X]', value):
        raise serializers.ValidationError('请输入18位身份证号码')
    try:
        birth = date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
        if birth > timezone.localdate() or birth.year < 1900:
            raise ValueError
    except ValueError:
        raise serializers.ValidationError('身份证出生日期不正确') from None
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    expected = '10X98765432'[sum(int(n) * weight for n, weight in zip(value[:17], weights)) % 11]
    if not hmac.compare_digest(value[-1], expected):
        raise serializers.ValidationError('身份证号码校验不通过，请检查')
    return value


def validate_picture(upload):
    if upload.size > 5 * 1024 * 1024:
        raise serializers.ValidationError('每张照片不能超过5MB')
    try:
        picture = Image.open(upload)
        suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}.get(picture.format)
        if not suffix or picture.width * picture.height > 30_000_000:
            raise ValueError
        picture.verify()
        upload.seek(0)
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise serializers.ValidationError('请上传有效的JPG、PNG或WebP照片') from None
    upload.name = uuid4().hex + suffix
    return upload


class RegistrationCredentials(serializers.Serializer):
    username = serializers.CharField(max_length=32)
    password = serializers.CharField(min_length=8, max_length=128, trim_whitespace=False, write_only=True)

    def validate_username(self, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[a-z0-9_]{4,32}', value):
            raise serializers.ValidationError('账号需为4至32位字母、数字或下划线')
        return value


class RegistrationSubmission(RegistrationCredentials):
    phone = serializers.RegexField(r'^1[3-9][0-9]{9}$', max_length=11)
    real_name = serializers.CharField(min_length=2, max_length=50)
    id_number = serializers.CharField(max_length=18, validators=[validate_identity], write_only=True)
    display_name = serializers.CharField(max_length=50)
    id_front = serializers.ImageField(validators=[validate_picture])
    id_back = serializers.ImageField(validators=[validate_picture])
    deposit_mode = serializers.ChoiceField(choices=['CASH', 'EARNINGS'], default='CASH')
    deposit_proof = serializers.ImageField(validators=[validate_picture], required=False)

    def validate(self, attrs):
        if attrs['deposit_mode'] == 'CASH' and not attrs.get('deposit_proof'):
            raise serializers.ValidationError({'deposit_proof': '现金押金须上传交付凭证'})
        attrs['id_number'] = validate_identity(attrs['id_number'])
        return attrs


def public_summary(row):
    return {
        'id': row.pk, 'username': row.username, 'display_name': row.display_name,
        'status': row.status, 'status_display': row.get_status_display(),
        'review_note': row.review_note, 'reviewed_at': row.reviewed_at,
        'submitted_at': row.submitted_at, 'revision': row.revision,
        'deposit_required': row.deposit_required, 'deposit_mode': row.deposit_mode,
        'level_name': row.level.name if row.level_id else '',
    }


def _error(message, status=400, fields=None):
    body = {'code': status, 'msg': message}
    if fields:
        body['data'] = {'fields': fields}
    return Response(body, status=status)


def _validate(serializer):
    if not serializer.is_valid():
        return _error('请检查注册资料', fields=serializer.errors)
    return None


def _duplicates(data, exclude=None):
    registrations = ProviderRegistration.objects.all()
    if exclude:
        registrations = registrations.exclude(pk=exclude)
    hashes = [identity_hash(data['id_number'], key) for key in (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)]
    return (
        registrations.filter(username__iexact=data['username']).exists()
        or registrations.filter(phone=data['phone']).exists()
        or registrations.filter(id_number_hash__in=hashes).exists()
        or ClubAccount.objects.filter(username__iexact=data['username']).exists()
        or CustomUser.objects.filter(username__iexact=data['username']).exists()
        or ClubAccount.objects.filter(account_type='PROVIDER', phone=data['phone']).exists()
    )


def _assign(row, data):
    row.username = data['username']
    row.phone = data['phone']
    row.password_hash = make_password(data['password'])
    row.real_name = data['real_name']
    row.id_number_ciphertext = encrypt_identity(data['id_number'])
    row.id_number_hash = identity_hash(data['id_number'])
    row.id_number_last4 = data['id_number'][-4:]
    row.display_name = data['display_name']
    row.deposit_mode = data['deposit_mode']
    row.deposit_required = deposit_target(row.deposit_mode)
    for field in IMAGE_FIELDS:
        setattr(row, field, data.get(field, ''))


def _delete_files(files):
    for storage, name in files:
        if name:
            storage.delete(name)


def _saved_files(row):
    return [(getattr(row, name).storage, getattr(row, name).name)
            for name in IMAGE_FIELDS if getattr(row, name)._committed]


class PublicRegistrationView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        serializer = RegistrationSubmission(data=request.data)
        invalid = _validate(serializer)
        if invalid is not None:
            return invalid
        data = serializer.validated_data
        row = ProviderRegistration()
        try:
            with transaction.atomic():
                if _duplicates(data):
                    return _error('账号、手机号或实名资料已有申请，请查询申请状态或联系客服', 409)
                _assign(row, data)
                row.save()
        except IntegrityError:
            _delete_files(_saved_files(row))
            return _error('该资料已有申请，请查询申请状态或联系客服', 409)
        except Exception:
            _delete_files(_saved_files(row))
            raise
        return Response({'code': 0, 'data': public_summary(row)}, status=201)


class RegistrationStatusView(PublicRegistrationView):
    def post(self, request):
        serializer = RegistrationCredentials(data=request.data)
        invalid = _validate(serializer)
        if invalid is not None:
            return invalid
        data = serializer.validated_data
        row = ProviderRegistration.objects.select_related('level').filter(username__iexact=data['username']).first()
        encoded = row.password_hash if row else make_password('nonexistent-registration')
        if not check_password(data['password'], encoded) or row is None:
            return _error('账号或密码不正确', 400)
        return Response({'code': 0, 'data': public_summary(row)})


class RegistrationResubmitView(PublicRegistrationView):
    def post(self, request):
        serializer = RegistrationSubmission(data=request.data)
        invalid = _validate(serializer)
        if invalid is not None:
            return invalid
        data = serializer.validated_data
        row = None
        new_files = []
        replaced_images = False
        try:
            with transaction.atomic():
                row = ProviderRegistration.objects.select_for_update().filter(username__iexact=data['username']).first()
                if row is None or not check_password(data['password'], row.password_hash):
                    return _error('账号或密码不正确', 400)
                if row.status != 'REJECTED':
                    return _error('仅需补充资料的申请可以重新提交', 409)
                if _duplicates(data, row.pk):
                    return _error('手机号或实名资料已有申请，请联系客服', 409)
                old_files = [(getattr(row, name).storage, getattr(row, name).name) for name in IMAGE_FIELDS]
                _assign(row, data)
                replaced_images = True
                row.status = 'PENDING'
                row.revision += 1
                row.review_note = ''
                row.reviewer = None
                row.reviewed_at = None
                row.submitted_at = timezone.now()
                row.save()
                new_files = _saved_files(row)
                transaction.on_commit(lambda: _delete_files(old_files))
        except IntegrityError:
            _delete_files(_saved_files(row) if replaced_images else new_files)
            return _error('资料与其他申请冲突，请联系客服', 409)
        except Exception:
            _delete_files(_saved_files(row) if replaced_images else new_files)
            raise
        return Response({'code': 0, 'data': public_summary(row)})


class PublicRegistrationRulesView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'code': 0, 'data': deposit_rules()})
