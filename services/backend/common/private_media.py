"""Short-lived image capabilities, issued only to the resource's current reader.

Images cannot attach the API's Bearer header. A signed URL therefore delegates
read access for 15 minutes; every read rechecks the issuing account and record.
It is not a login token and is never valid for a different file or API action.
"""
import hashlib
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

SALT = 'xa.private-media.v1'
MAX_AGE = 15 * 60
PRIVATE_FIELDS = {
    'users.providerregistration': ('id_front', 'id_back', 'deposit_proof'),
    'wallet.providerreport': ('proof_image', 'entry_image', 'completion_image'),
    'wallet.providerreportimage': ('image',),
    'wallet.rechargerecord': ('proof_image',),
    'users.escortprofile': ('cheat_proof',),
    'chat.chatmessage': ('image',),
}
PUBLIC_PREFIXES = (
    'accounts/avatars/', 'customers/avatars/', 'escorts/avatars/',
    'escorts/videos/', 'escorts/voice_cards/', 'game_categories/', 'banners/', 'club-catalog/',
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
    label = obj._meta.label_lower
    if label == 'wallet.providerreportimage':
        obj = obj.report
        label = obj._meta.label_lower
    if label == 'wallet.providerreport':
        if account.account_type == ClubAccount.AccountType.PROVIDER and obj.provider_account_id == account.pk:
            return True
        # The console report list intentionally excludes drafts.
        if obj.status == 'DRAFT':
            return False
        permission = 'report:view'
    elif label == 'wallet.rechargerecord':
        permission = 'recharge:view'
    elif label == 'users.escortprofile':
        if account.account_type == ClubAccount.AccountType.PROVIDER and obj.account_id == account.pk:
            return True
        permission = 'escort:view'
    elif label == 'users.providerregistration':
        permission = 'registration:view'
    elif label == 'chat.chatmessage':
        if account.account_type != ClubAccount.AccountType.STAFF and obj.session.account_id == account.pk:
            return True
        permission = 'chat:view'
    else:
        return False
    return is_console_account(account) and permission in get_account_permissions(account)


def _fingerprint(field):
    return hashlib.sha256(field.name.encode('utf-8')).hexdigest()


def private_media_url(request, field):
    account = getattr(request, 'account', None)
    obj = field.instance
    label = obj._meta.label_lower
    if field.field.name not in PRIVATE_FIELDS.get(label, ()) or not may_read(account, obj):
        return ''
    if not obj.pk or not field:
        return ''
    ticket = signing.dumps({
        'account': account.pk, 'model': label, 'pk': obj.pk,
        'field': field.field.name, 'file': _fingerprint(field),
    }, salt=SALT, compress=True)
    url = f'/api/media/private/{ticket}/'
    return request.build_absolute_uri(url) if hasattr(request, 'build_absolute_uri') else url


class RenewPrivateMediaView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        """Refresh a displayed image without reloading forms or chat drafts."""
        ticket = request.data.get('ticket', '')
        try:
            if not isinstance(ticket, str) or len(ticket) > 2048:
                raise Http404
            # An expired signature identifies the file only; current API login,
            # account identity, ownership and permissions are required anew.
            grant = signing.loads(ticket, salt=SALT)
            if grant['account'] != request.account.pk:
                raise Http404
            if grant['field'] not in PRIVATE_FIELDS.get(grant['model'], ()):
                raise Http404
            obj = apps.get_model(grant['model']).objects.filter(pk=grant['pk']).first()
            if obj is None or not may_read(request.account, obj):
                raise Http404
            field = getattr(obj, grant['field'])
            if not field or _fingerprint(field) != grant['file']:
                raise Http404
            return Response({'code': 0, 'data': {'url': private_media_url(request, field)}})
        except (signing.BadSignature, KeyError, TypeError, ValueError, Http404):
            return Response({'code': 404, 'msg': '图片已变更或无查看权限，请刷新列表'}, status=404)


def _local_file(field):
    """Existing local storage only. Never serve outside MEDIA_ROOT via symlinks."""
    name = field.name
    if not name or '\\' in name or PurePosixPath(name).is_absolute() or '..' in name.split('/'):
        raise Http404
    root = Path(settings.MEDIA_ROOT).resolve()
    path = Path(field.path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise Http404
    return path


@never_cache
@require_safe
def private_media(request, ticket):
    try:
        grant = signing.loads(ticket, salt=SALT, max_age=MAX_AGE)
        label, field_name = grant['model'], grant['field']
        if field_name not in PRIVATE_FIELDS.get(label, ()):
            raise Http404
        account = ClubAccount.objects.filter(pk=grant['account']).first()
        obj = apps.get_model(label).objects.filter(pk=grant['pk']).first()
        if obj is None or not may_read(account, obj):
            raise Http404
        field = getattr(obj, field_name)
        if not field or _fingerprint(field) != grant['file']:
            raise Http404
        # Infer the media type from actual image bytes, never a user filename.
        handle = _local_file(field).open('rb')
        try:
            picture = Image.open(handle)
            content_type = Image.MIME.get(picture.format)
            if content_type not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
                raise Http404
            handle.seek(0)
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
    root = Path(settings.MEDIA_ROOT).resolve()
    if not (root / path).resolve().is_relative_to(root):
        raise Http404
    return serve(request, path, document_root=root)
