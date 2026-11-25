import copy
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.compliance import (
    BUILTIN_RULES_V1, PROVIDER_DISPLAY_NAME, check_content, record_content_receipt,
)
from console.models import ContentComplianceReceipt
from orders.tests.factories import make_console_user
from stores.models import ComplianceRuleState, ComplianceRuleVersion, PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from users.models import CustomUser, EscortLevel, EscortProfile, ProviderRegistration
from wallet.models import Transaction, Wallet, get_platform_wallet


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    PLATFORM_IDENTITY_ENABLED=True,
)
class ProviderRegistrationApprovalComplianceTests(APITestCase):
    display_name = '新版本禁用词昵称'
    rule_code = 'PROVIDER_DISPLAY_NAME_APPROVAL_BLOCK'

    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-registration-approval-compliance-')
        self.addCleanup(folder.cleanup)
        setting = override_settings(MEDIA_ROOT=folder.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.level = EscortLevel.objects.create(name='合规审核等级', commission_rate=20, sort_order=1)

    def picture(self, name):
        data = BytesIO()
        Image.new('RGB', (32, 32), '#a2b5cc').save(data, 'PNG')
        return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')

    def identity(self):
        stem = '11010119900101001'
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        return stem + '10X98765432'[sum(int(n) * w for n, w in zip(stem, weights)) % 11]

    def payload(self, **changes):
        return {
            'username': 'approval_compliance',
            'password': 'test-password-only',
            'phone': '13900001999',
            'real_name': '合规测试',
            'id_number': self.identity(),
            'display_name': self.display_name,
            **{name: self.picture(name + '.png') for name in ('id_front', 'id_back', 'deposit_proof')},
            **changes,
        }

    def submit(self, **changes):
        response = self.client.post('/api/provider-registration/', self.payload(**changes), format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        return ProviderRegistration.objects.get(pk=response.data['data']['id'])

    def authenticate_reviewer(self):
        user = make_console_user(perms=['registration:view', 'registration:audit'])
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        self.reviewer = account
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def authenticate_platform(self):
        account = PlatformAccount.objects.create(
            username='registration-compliance-owner', phone='13800001111', is_phone_verified=True,
        )
        PlatformRoleGrant.objects.create(
            account=account, role=PlatformRoleGrant.Role.SUPER_ADMIN, scope_slug='*',
        )
        client = APIClient(
            HTTP_HOST='admin.esports.club',
            HTTP_AUTHORIZATION='Bearer ' + issue_tokens(account, 'SMS', account.phone)[0],
        )
        return account, client

    def publish_provider_name_rule(self, term=None, code=None):
        term = term or self.display_name
        code = code or self.rule_code
        rules = copy.deepcopy(list(BUILTIN_RULES_V1))
        rules.append({
            'code': code,
            'category': 'EXTREME_WORD',
            'applies_to': ['PROVIDER_DISPLAY_NAME'],
            'terms': [term],
            'message': '该陪玩昵称不符合当前平台合规规则',
            'enabled': True,
        })
        _, client = self.authenticate_platform()
        created = client.post('/api/admin/platform/compliance-rules', {'rules': rules}, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        version = created.data['data']['version']
        published = client.post(f'/api/admin/platform/compliance-rules/{version}/publish', {}, format='json')
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(ComplianceRuleState.objects.get(pk=1).current_version_id, version)
        return version

    def approve(self, row):
        return self.client.post(
            f'/api/admin/provider-registrations/{row.pk}/approve/',
            {
                'revision': row.revision,
                'level_id': self.level.pk,
                'identity_confirmed': True,
                'deposit_confirmed': True,
                'deposit_paid': row.deposit_required,
            },
            format='json',
        )

    def test_new_published_rule_blocks_approval_before_any_account_or_receipt_is_created(self):
        row = self.submit()
        self.assertIsNone(row.account_id)
        submission_receipt = ContentComplianceReceipt.objects.get(
            content_type='provider_registration', object_id=row.pk,
            field_name='PROVIDER_REGISTRATION_DISPLAY_NAME',
        )
        self.authenticate_reviewer()
        version = self.publish_provider_name_rule()
        platform_balance_before = get_platform_wallet().balance
        transaction_count_before = Transaction.objects.count()
        wallet_count_before = Wallet.objects.count()

        response = self.approve(row)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data['errors']['detail'], '该陪玩昵称不符合当前平台合规规则')
        row.refresh_from_db()
        self.assertEqual((row.status, row.account_id, row.reviewed_at, row.reviewer_id),
                         ('PENDING', None, None, None))
        self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())
        self.assertFalse(CustomUser.objects.filter(username=row.username).exists())
        self.assertFalse(EscortProfile.objects.filter(display_name=self.display_name).exists())
        self.assertFalse(LegacyAccountMap.objects.filter(account__username=row.username).exists())
        self.assertFalse(Wallet.objects.filter(account__username=row.username).exists())
        self.assertEqual(Wallet.objects.count(), wallet_count_before)
        self.assertEqual(Transaction.objects.count(), transaction_count_before)
        self.assertEqual(get_platform_wallet().balance, platform_balance_before)
        self.assertFalse(ContentComplianceReceipt.objects.filter(
            content_type='escort_profile', field_name=PROVIDER_DISPLAY_NAME,
        ).exists())
        replay = self.approve(row)
        self.assertEqual(replay.status_code, 400, replay.content)
        row.refresh_from_db()
        self.assertEqual((row.status, row.account_id), ('PENDING', None))
        self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())
        self.assertFalse(EscortProfile.objects.filter(display_name=self.display_name).exists())
        self.assertEqual(ContentComplianceReceipt.objects.count(), 1)
        submission_receipt.refresh_from_db()
        self.assertEqual(submission_receipt.rule_version, 1)
        self.assertEqual(ComplianceRuleState.objects.get(pk=1).current_version_id, version)

    def test_legal_approval_records_current_profile_receipt_and_replay_is_idempotent(self):
        row = self.submit()
        self.authenticate_reviewer()
        version = self.publish_provider_name_rule(term='仅新版本禁用的其他词')
        with patch('console.provider_registration.check_content', wraps=check_content) as checked:
            with patch('console.provider_registration.record_content_receipt',
                       wraps=record_content_receipt) as recorded:
                response = self.approve(row)
                self.assertEqual(response.status_code, 200, response.content)
        checked.assert_called_once_with(PROVIDER_DISPLAY_NAME, self.display_name)
        recorded.assert_called_once()
        self.assertEqual(recorded.call_args.args[0], 'escort_profile')
        self.assertEqual(recorded.call_args.args[2], PROVIDER_DISPLAY_NAME)
        self.assertEqual(recorded.call_args.kwargs['actor_account'], self.reviewer)
        receipt = ContentComplianceReceipt.objects.get(
            content_type='escort_profile', object_id=recorded.call_args.args[1],
            field_name=PROVIDER_DISPLAY_NAME,
        )
        row.refresh_from_db()
        self.assertEqual(row.status, 'APPROVED')
        self.assertEqual(receipt.object_id, row.account.escort_profile.pk)
        self.assertEqual(receipt.object_id, recorded.call_args.args[1])
        self.assertEqual(receipt.rule_version, version)
        self.assertEqual(receipt.rule_set_sha256, ComplianceRuleVersion.objects.get(version=version).rules_sha256)
        self.assertEqual(receipt.actor_account_id, self.reviewer.pk)
        self.assertEqual(receipt.matched_rule_codes, [])
        self.assertEqual(receipt.exempted_rule_codes, [])
        self.assertNotIn(self.display_name, str(receipt.__dict__))

        account_ids = list(ClubAccount.objects.values_list('pk', flat=True))
        user_ids = list(CustomUser.objects.values_list('pk', flat=True))
        profile_ids = list(EscortProfile.objects.values_list('pk', flat=True))
        with patch('console.provider_registration.check_content', wraps=check_content) as replayed_check:
            with patch('console.provider_registration.record_content_receipt',
                       wraps=record_content_receipt) as replayed_record:
                replay = self.approve(row)
                self.assertEqual(replay.status_code, 200, replay.content)
        replayed_check.assert_not_called()
        replayed_record.assert_not_called()
        self.assertEqual(list(ClubAccount.objects.values_list('pk', flat=True)), account_ids)
        self.assertEqual(list(CustomUser.objects.values_list('pk', flat=True)), user_ids)
        self.assertEqual(list(EscortProfile.objects.values_list('pk', flat=True)), profile_ids)
        self.assertEqual(ContentComplianceReceipt.objects.filter(
            content_type='escort_profile', object_id=receipt.object_id,
            field_name=PROVIDER_DISPLAY_NAME,
        ).count(), 1)

    def test_receipt_failure_rolls_back_approval_account_and_profile(self):
        row = self.submit()
        self.authenticate_reviewer()
        account_count_before = ClubAccount.objects.count()
        user_count_before = CustomUser.objects.count()
        profile_count_before = EscortProfile.objects.count()
        wallet_count_before = Wallet.objects.count()
        transaction_count_before = Transaction.objects.count()
        platform_balance_before = get_platform_wallet().balance

        with patch('console.provider_registration.record_content_receipt',
                   side_effect=DatabaseError('receipt offline')):
            with self.assertRaises(DatabaseError):
                self.approve(row)

        row.refresh_from_db()
        self.assertEqual((row.status, row.account_id, row.reviewed_at, row.reviewer_id),
                         ('PENDING', None, None, None))
        self.assertEqual(ClubAccount.objects.count(), account_count_before)
        self.assertEqual(CustomUser.objects.count(), user_count_before)
        self.assertEqual(EscortProfile.objects.count(), profile_count_before)
        self.assertEqual(Wallet.objects.count(), wallet_count_before)
        self.assertEqual(Transaction.objects.count(), transaction_count_before)
        self.assertEqual(get_platform_wallet().balance, platform_balance_before)
        self.assertEqual(ContentComplianceReceipt.objects.filter(
            content_type='escort_profile', field_name=PROVIDER_DISPLAY_NAME,
        ).count(), 0)
