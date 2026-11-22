"""Short-lived media capabilities, issued only to the resource's current reader.

Media elements cannot attach the API's Bearer header. A signed URL delegates
read access for 15 minutes; every read rechecks the issuing account and record.
It is not a login token and is never valid for a different file or API action.
"""
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from django.apps import apps
from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from django.views.static import serve
from PIL import Image, UnidentifiedImageError
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import get_account_permissions, is_console_account
from stores.context import current_store
from stores.media import media_root, store_slug, with_store_query

SALT = 'xa.private-media.v1'
MAX_AGE = 15 * 60
PRIVATE_FIELDS = {
    'users.voicerecording': ('audio',),
    'users.providerregistration': ('id_front', 'id_back', 'deposit_proof'),
    'wallet.providerreport': ('proof_image', 'entry_image', 'completion_image'),
    'wallet.providerreportimage': ('image',),
    'orders.orderparticipationsegment': ('entry_image', 'completion_image'),
    'orders.ordersegmentreportimage': ('image',),
    'orders.ordersegmentreportsubmission': ('entry_image', 'completion_image'),
    'orders.ordersegmentsubmissionimage': ('image',),
    'wallet.rechargerecord': ('proof_image',),
    'wallet.cashdeposittopup': ('proof_image',),
    'users.escortprofile': ('cheat_proof',),
    'chat.chatmessage': ('image',),
    'orders.orderroommessageimage': ('image',),
}
PUBLIC_PREFIXES = (
    'accounts/avatars/', 'customers/avatars/', 'escorts/avatars/',
    'escorts/videos/', 'escorts/voice_cards/', 'game_categories/', 'banners/', 'club-catalog/',
    'stores/logos/', 'platform/logos/',
)


def public_media_path(url):
    """Fail closed for unknown media folders, including encoded traversal."""
    path = unquote(urlsplit(str(url)).path)
    if path.startswith(settings.MEDIA_URL):
        path = path[len(settings.MEDIA_URL):]
    if '\\' in path or any(part in ('.', '..', '') for part in path.split('/')):
        return False
    return path.startswith(PUBLIC_PREFIXES)


def may_read(account, obj):
    if not account or not account.is_active or not account.can_login:
        return False
    if account.account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER):
        if not account.license_membership_active:
            return False
    label = obj._meta.label_lower
    if label == 'orders.orderroommessageimage':
        from orders.rooms import message_role
        role = message_role(obj.message.room.order, account)
        return role in ('BOSS', 'PROVIDER', 'STAFF') or (role == 'FORMER_PROVIDER' and obj.message.author_id == account.pk)
    if label == 'users.voicerecording':
        if account.account_type == ClubAccount.AccountType.PROVIDER and obj.owner_id == account.pk:
            return True
        submissions = obj.submissions.filter(application__status__in=['PENDING', 'SELECTED'])
        if account.account_type == ClubAccount.AccountType.BOSS:
            return submissions.filter(application__order__customer_account_id=account.pk).exists()
        return is_console_account(account) and 'order:view' in get_account_permissions(account) and submissions.exists()
    if label in ('orders.ordersegmentreportimage', 'orders.ordersegmentreportsubmission', 'orders.ordersegmentsubmissionimage'):
        if label == 'orders.ordersegmentreportimage':
            report = obj.report
            return account.account_type == ClubAccount.AccountType.PROVIDER and report.segment.provider_account_id == account.pk
        submission = obj.submission if label == 'orders.ordersegmentsubmissionimage' else obj
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            return submission.report.segment.provider_account_id == account.pk
        return is_console_account(account) and 'report:view' in get_account_permissions(account)
    if label == 'wallet.providerreportimage':
        obj = obj.report
        label = obj._meta.label_lower
    if label == 'orders.orderparticipationsegment':
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            return obj.provider_account_id == account.pk
        return is_console_account(account) and 'report:view' in get_account_permissions(account)
    if label == 'wallet.providerreport':
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            if obj.provider_account_id == account.pk:
                return True
            if obj.provider_account_id is None:
                from club_accounts.models import LegacyAccountMap
                if LegacyAccountMap.objects.filter(account=account, legacy_user_id=obj.provider_id).exists():
                    return True
        # The console report list intentionally excludes drafts.
        if obj.status == 'DRAFT':
            return False
        permission = 'report:view'
    elif label == 'wallet.rechargerecord':
        permission = 'recharge:view'
    elif label == 'wallet.cashdeposittopup':
        permission = 'escort:deposit_topup'
    elif label == 'users.escortprofile':
        if account.account_type == ClubAccount.AccountType.PROVIDER and obj.account_id == account.pk:
            return True
        permission = 'escort:view'
    elif label == 'users.providerregistration':
        permission = 'registration:view'
    elif label == 'chat.chatmessage':
        from chat.authorization import can_read_session
        return can_read_session(account, obj.session)
    else:
        return False
    return is_console_account(account) and permission in get_account_permissions(account)


def _fingerprint(field):
    return hashlib.sha256(field.name.encode('utf-8')).hexdigest()


def _ticket_matches_account(grant, account):
    """A link belongs to the identity and membership that received it."""
    if not account or grant.get('account') != account.pk or grant.get('account_type') != account.account_type:
        return False
    if account.account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER):
        return (account.license_membership_active and type(grant.get('business_cycle')) is int
                and grant['business_cycle'] == account.business_cycle)
    return True


def private_media_url(request, field):
    if current_store() is None:
        return ''
    account = getattr(request, 'account', None)
    obj = field.instance
    label = obj._meta.label_lower
    if field.field.name not in PRIVATE_FIELDS.get(label, ()) or not may_read(account, obj):
        return ''
    if not obj.pk or not field:
        return ''
    ticket = signing.dumps({
        'account': account.pk, 'account_type': account.account_type,
        'business_cycle': account.business_cycle if account.account_type in
            (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER) else None,
        'model': label, 'pk': obj.pk,
        'field': field.field.name, 'file': _fingerprint(field),
        'store_slug': store_slug(),
    }, salt=SALT, compress=True)
    url = with_store_query(f'/api/media/private/{ticket}/')
    return request.build_absolute_uri(url) if hasattr(request, 'build_absolute_uri') else url


class RenewPrivateMediaView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        """Reissue a capability after rechecking the current reader and file."""
        ticket = request.data.get('ticket', '')
        try:
            if not isinstance(ticket, str) or len(ticket) > 2048:
                raise Http404
            if current_store() is None:
                raise Http404
            # An expired signature identifies the file only; current API login,
            # account identity, ownership and permissions are required anew.
            grant = signing.loads(ticket, salt=SALT)
            if grant.get('store_slug') != store_slug():
                raise Http404
            if not _ticket_matches_account(grant, request.account):
                raise Http404
            if grant['field'] not in PRIVATE_FIELDS.get(grant['model'], ()):
                raise Http404
            obj = apps.get_model(grant['model']).objects.filter(pk=grant['pk']).first()
            if obj is None or not may_read(request.account, obj):
                raise Http404
            field = getattr(obj, grant['field'])
            if not field or _fingerprint(field) != grant['file']:
                raise Http404
            if grant['model'] == 'users.voicerecording':
                from users.voice_availability import recording_available
                if not recording_available(obj, verify_content=True):
                    raise Http404
            return Response({'code': 0, 'data': {'url': private_media_url(request, field)}})
        except (signing.BadSignature, KeyError, TypeError, ValueError, Http404):
            return Response({'code': 404, 'msg': '图片已变更或无查看权限，请刷新列表'}, status=404)


def _local_file(field):
    """Existing local storage only. Never serve outside MEDIA_ROOT via symlinks."""
    name = field.name
    if not name or '\\' in name or PurePosixPath(name).is_absolute() or '..' in name.split('/'):
        raise Http404
    root = media_root()
    path = Path(field.path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise Http404
    return path


@never_cache
@require_safe
def private_media(request, ticket):
    try:
        if current_store() is None:
            raise Http404
        grant = signing.loads(ticket, salt=SALT, max_age=MAX_AGE)
        if grant.get('store_slug') != store_slug():
            raise Http404
        label, field_name = grant['model'], grant['field']
        if field_name not in PRIVATE_FIELDS.get(label, ()):
            raise Http404
        account = ClubAccount.objects.filter(pk=grant['account']).first()
        obj = apps.get_model(label).objects.filter(pk=grant['pk']).first()
        if obj is None or not _ticket_matches_account(grant, account) or not may_read(account, obj):
            raise Http404
        field = getattr(obj, field_name)
        if not field or _fingerprint(field) != grant['file']:
            raise Http404
        # Infer the media type from actual image bytes, never a user filename.
        handle = _local_file(field).open('rb')
        try:
            if label == 'orders.orderroommessageimage':
                from orders.room_image_integrity import image_matches
                if not image_matches(handle, obj.content_sha256):
                    raise Http404
                handle.seek(0)
            if label == 'users.voicerecording':
                from users.voice_availability import recording_matches
                if not recording_matches(obj, handle, verify_content=True):
                    raise Http404
                handle.seek(0)
                from .audio_ranges import audio_response
                response = audio_response(request, handle, obj.content_type)
                response['Cache-Control'] = 'private, no-store, max-age=0'
                response['Referrer-Policy'] = 'no-referrer'
                response['X-Content-Type-Options'] = 'nosniff'
                return response
            picture = Image.open(handle)
            content_type = Image.MIME.get(picture.format)
            if content_type not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
                raise Http404
            handle.seek(0)
            from .catalog_images import DISPLAY_MAX_BYTES, compress_catalog_image
            if field.size > DISPLAY_MAX_BYTES:
                display, _ = compress_catalog_image(handle)
                handle.close()
                handle = BytesIO(display)
                content_type = 'image/jpeg'
            response = FileResponse(handle, content_type=content_type)
        except Exception:
            handle.close()
            raise
    except (signing.BadSignature, KeyError, TypeError, ValueError, OSError,
            UnidentifiedImageError, Image.DecompressionBombError, Http404):
        response = HttpResponse(status=404)
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_safe
def public_media(request, path):
    if not public_media_path(path):
        raise Http404
    if path.startswith('platform/logos/'):
        root = Path(settings.MEDIA_ROOT).resolve() / 'platform'
        path = path[len('platform/'):]
    else:
        root = media_root()
    if not (root / path).resolve().is_relative_to(root):
        raise Http404
    return serve(request, path, document_root=root)
