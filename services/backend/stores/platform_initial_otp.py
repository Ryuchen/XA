"""Platform issuance of an initial store administrator enrollment grant."""
import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4
from django.core import signing
from django.utils import timezone
from club_accounts.models import ClubAccount, InitialAdminOtpGrant, OtpCredential
from console.models import AdminAuditLog, AdminMembership
from .models import Store, StoreProvisioningClaim
from .platform_store_setup import PlatformSetupBase, SetupError

SALT = 'xa.initial-admin-otp.v1'

def fingerprint(account):
    return hashlib.sha256(account.password.encode()).hexdigest()

def eligible(account):
    return (account.account_type == 'STAFF' and account.is_active and account.can_login
        and account.has_usable_password() and AdminMembership.objects.filter(
            account=account, is_active=True, roles__code='super_admin', roles__is_active=True).exists())

class PlatformInitialOtpGrantView(PlatformSetupBase):
    def post(self, request, pk):
        def issue(store, actor, operation_id):
            if store.status != Store.Status.CONFIGURING:
                raise SetupError('仅可为初始化中的门店签发首位管理员凭据')
            claim = StoreProvisioningClaim.objects.using('default').filter(store_id=store.pk).first()
            if not claim or not claim.admin_username:
                raise SetupError('缺少首次建店管理员记录，请联系平台核对')
            account = ClubAccount.objects.select_for_update().filter(username=claim.admin_username).first()
            if not account or not eligible(account) or OtpCredential.objects.filter(account=account).exists():
                raise SetupError('首位管理员不可绑定或已经绑定验证器')
            now = timezone.now()
            revoked = list(InitialAdminOtpGrant.objects.filter(account=account, consumed_at__isnull=True, revoked_at__isnull=True).values_list('pk', flat=True))
            InitialAdminOtpGrant.objects.filter(pk__in=revoked).update(revoked_at=now, secret_ciphertext='')
            grant_id = uuid4()
            token = signing.dumps({'purpose': 'initial-admin-otp', 'store_slug': store.slug,
                'account_id': account.pk, 'grant_id': str(grant_id), 'nonce': secrets.token_urlsafe(32)},
                salt=SALT, compress=True)
            expiry = now + timedelta(minutes=10)
            InitialAdminOtpGrant.objects.create(id=grant_id, account=account,
                token_hash=hashlib.sha256(token.encode()).hexdigest(), password_fingerprint=fingerprint(account),
                platform_account_id=actor.pk, operation_id=operation_id, expires_at=expiry)
            AdminAuditLog.objects.create(operator_name=('platform:' + actor.username)[:150], method='POST',
                path='/api/admin/platform/store-management/initial-otp', resource='initial_admin_otp',
                object_id=str(account.pk), request_body={'operation_id': operation_id, 'event': 'issued', 'grant_id': str(grant_id), 'revoked_grants': [str(value) for value in revoked]}, status_code=200)
            return {'token': token, 'expiresAt': expiry.isoformat(), 'username': account.username}
        response = self.mutate(request, pk, 'initial_admin_otp', issue)
        response['Cache-Control'] = 'no-store'
        return response
