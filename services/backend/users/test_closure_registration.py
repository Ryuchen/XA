"""SA02 closure guards for public account creation and boss membership re-entry."""
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from club_accounts.license_quota import LicenseAdmissionError
from club_accounts.models import ClubAccount, LicenseQuotaLock
from club_accounts.tokens import issue_account_tokens
from engagement.retirement import preview_retirement, retire
from orders.tests.factories import make_console_user
from stores.closure_clock import CLOCK_SOURCE
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, StoreClosureEvent, StoreClosureNotice
from stores.tests_hosts import xinan_store
from users.models import BossRegistrationChallenge, EscortLevel, ProviderRegistration
from wallet.models import Transaction


@override_settings(
    ALLOWED_HOSTS=['*'],
    LEGACY_FORCE_AUTH_COMPAT=False,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LOGIN_OTP_REQUIRED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    PROVIDER_WECHAT_GATE_ENABLED=False,
    WECHAT_MOCK_LOGIN=False,
)
class ClosurePublicAccountTests(APITestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.first_client = APIClient(HTTP_HOST='sl.esports.club')
        self.second_client = APIClient(HTTP_HOST='xa.esports.club')
        self.actor = PlatformAccount.objects.create(username='closure-public-account-actor')
        self.clock_now = timezone.now()
        self.sms_codes = []
        sender = patch(
            'users.boss_registration.send_verification_code',
            side_effect=lambda phone, code: self.sms_codes.append((phone, code)),
        )
        sender.start()
        self.addCleanup(sender.stop)
        folder = TemporaryDirectory(prefix='xa-closure-registration-')
        self.addCleanup(folder.cleanup)
        media = override_settings(MEDIA_ROOT=folder.name)
        media.enable()
        self.addCleanup(media.disable)
        self.levels = {}
        for store in (self.first, self.second):
            with store_context(store):
                self.levels[store.slug] = EscortLevel.objects.create(
                    id=88001, name='闭店审核等级', commission_rate=20, sort_order=1,
                )

    def close(self, store):
        notice = StoreClosureNotice.objects.filter(store=store).first()
        if notice is not None:
            return notice
        return StoreClosureNotice.objects.create(
            store=store, reason_category='OPERATOR_REQUEST', reason='闭店注册测试内部原因',
            applicant=self.actor, started_at=self.clock_now,
            notice_policy_version='SA02_NOTICE_30D_V1', notice_days=30,
            earliest_settlement_at=self.clock_now + timedelta(days=30),
            client_request_id=f'closure-registration-{store.slug}', request_fingerprint='a' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )

    def pending(self, store):
        notice = self.close(store)
        event = StoreClosureEvent.objects.filter(notice=notice).first()
        if event is not None:
            return event
        return StoreClosureEvent.objects.create(
            store=store, notice=notice, kind='NOTICE_EXPIRED', reason='到期确认内部原因',
            actor=self.actor, occurred_at=notice.earliest_settlement_at,
            client_request_id=f'closure-registration-expiry-{store.slug}',
            request_fingerprint='b' * 64, clock_source=CLOCK_SOURCE,
            clock_identity='sqlite-test:default',
        )

    def assert_closure(self, response, status=409):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.data['code'], status)
        self.assertEqual(
            response.data['errorCode'],
            'STORE_CLOSURE_RESTRICTED' if status == 409 else 'STORE_CLOSURE_STATUS_UNAVAILABLE',
        )
        self.assertNotIn('闭店注册测试内部原因', str(response.data))

    def boss_start(self, phone='13800001111', client=None):
        return (client or self.first_client).post(
            '/api/users/boss-registration/sms/', {'phone': phone}, format='json',
        )

    def boss_register(self, challenge, code, *, username='closure-boss-new', client=None):
        return (client or self.first_client).post('/api/users/boss-registration/', {
            'smsChallenge': challenge, 'smsCode': code, 'username': username,
            'password': 'Example-Password-2026', 'nickname': '闭店测试老板',
        }, format='json')

    def picture(self, name):
        data = BytesIO()
        Image.new('RGB', (32, 32), '#a2b5cc').save(data, 'PNG')
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')

    def identity(self, sequence='001'):
        stem = '11010119900101' + sequence
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        return stem + '10X98765432'[sum(int(n) * w for n, w in zip(stem, weights)) % 11]

    def provider_payload(self, **changes):
        return {
            'username': 'closure_provider', 'password': 'test-password-only',
            'phone': '13900001001', 'real_name': '闭店注册测试',
            'id_number': self.identity(), 'display_name': '闭店测试陪玩',
            **{name: self.picture(name + '.png') for name in ('id_front', 'id_back', 'deposit_proof')},
            **changes,
        }

    def submit_provider(self, store, client=None, **changes):
        response = (client or self.first_client).post(
            '/api/provider-registration/', self.provider_payload(**changes), format='multipart',
        )
        self.assertEqual(response.status_code, 201, response.content)
        with store_context(store):
            return ProviderRegistration.objects.get(pk=response.data['data']['id'])

    def reviewer(self, store, perms=None):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from console.models import AdminMembership
        with store_context(store):
            user = make_console_user(perms=perms or ['registration:view', 'registration:audit'])
            account = get_or_create_account_for_legacy_user(user)
            AdminMembership.objects.filter(user=user).update(account=account)
            token = issue_account_tokens(account)[0]
        return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def approve(self, client, store, row):
        return client.post(f'/api/admin/provider-registrations/{row.pk}/approve/', {
            'revision': row.revision, 'level_id': self.levels[store.slug].pk,
            'identity_confirmed': True, 'deposit_confirmed': True,
            'deposit_paid': row.deposit_required,
        }, format='json')

    def retired_account(self, store, username, account_id):
        with store_context(store):
            account = ClubAccount.objects.create_account(
                id=account_id, account_no=f'XA{account_id}', username=username,
                password='unused', account_type='BOSS',
            )
            ClubAccount.objects.filter(pk=account.pk).update(
                license_membership_active=False, business_cycle=account.business_cycle + 1,
            )
            account.refresh_from_db()
            return account

    def test_new_boss_sms_and_formal_creation_block_in_notice_and_settlement_pending(self):
        started = self.boss_start()
        self.assertEqual(started.status_code, 200, started.content)
        challenge = started.data['data']['smsChallenge']
        code = self.sms_codes[-1][1]
        self.close(self.first)

        self.assert_closure(self.boss_start('13800001112'))
        self.assertEqual(len(self.sms_codes), 1)
        self.assert_closure(self.boss_register(challenge, code))
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.filter(username='closure-boss-new').exists())
            self.assertFalse(BossRegistrationChallenge.objects.filter(phone='13800001112').exists())
            self.assertFalse(BossRegistrationChallenge.objects.get(phone='13800001111').consumed)

        self.pending(self.first)
        self.assert_closure(self.boss_start('13800001113'))
        self.assert_closure(self.boss_register(challenge, code))
        self.assertEqual(len(self.sms_codes), 1)

    def test_existing_boss_sms_flow_can_recover_but_still_cannot_create_account(self):
        started = self.boss_start()
        old_challenge = started.data['data']['smsChallenge']
        with store_context(self.first):
            BossRegistrationChallenge.objects.filter(phone='13800001111').update(
                sent_at=timezone.now() - timedelta(seconds=61),
            )
        self.close(self.first)

        recovered = self.boss_start()
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertNotEqual(recovered.data['data']['smsChallenge'], old_challenge)
        self.assertEqual(len(self.sms_codes), 2)
        self.assert_closure(self.boss_register(
            recovered.data['data']['smsChallenge'], self.sms_codes[-1][1],
        ))

    def test_boss_control_read_failure_fails_closed_without_sms_or_account(self):
        started = self.boss_start()
        challenge = started.data['data']['smsChallenge']
        code = self.sms_codes[-1][1]
        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control offline')):
            self.assert_closure(self.boss_start('13800001112'), 503)
            self.assert_closure(self.boss_register(challenge, code), 503)
        self.assertEqual(len(self.sms_codes), 1)
        with store_context(self.first):
            self.assertFalse(BossRegistrationChallenge.objects.filter(phone='13800001112').exists())
            self.assertFalse(ClubAccount.objects.filter(username='closure-boss-new').exists())

    def test_boss_creation_rechecks_closure_after_waiting_for_phone_lock(self):
        started = self.boss_start()
        import users.boss_registration as registration
        original_lock = registration._lock_wechat_phone

        def lock_then_close(phone):
            result = original_lock(phone)
            self.close(self.first)
            return result

        with patch('users.boss_registration._lock_wechat_phone', side_effect=lock_then_close):
            self.assert_closure(self.boss_register(
                started.data['data']['smsChallenge'], self.sms_codes[-1][1],
            ))
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.filter(username='closure-boss-new').exists())

    def test_new_provider_application_blocks_in_notice_and_settlement_pending(self):
        self.close(self.first)
        denied = self.first_client.post(
            '/api/provider-registration/', self.provider_payload(), format='multipart',
        )
        self.assert_closure(denied)
        self.pending(self.first)
        denied = self.first_client.post(
            '/api/provider-registration/', self.provider_payload(
                username='closure_provider_2', phone='13900001002', id_number=self.identity('002'),
            ), format='multipart',
        )
        self.assert_closure(denied)
        with store_context(self.first):
            self.assertFalse(ProviderRegistration.objects.exists())

    def test_provider_application_rechecks_after_duplicate_validation(self):
        from users import provider_registration as registration
        original = registration._duplicates

        def duplicates_then_close(data, exclude=None):
            result = original(data, exclude)
            self.close(self.first)
            return result

        with patch('users.provider_registration._duplicates', side_effect=duplicates_then_close):
            response = self.first_client.post(
                '/api/provider-registration/', self.provider_payload(), format='multipart',
            )
        self.assert_closure(response)
        with store_context(self.first):
            self.assertFalse(ProviderRegistration.objects.exists())

    def test_provider_rejection_resubmit_status_and_rules_remain_available(self):
        row = self.submit_provider(self.first)
        reviewer = self.reviewer(self.first)
        self.close(self.first)
        rejected = reviewer.post(f'/api/admin/provider-registrations/{row.pk}/reject/', {
            'revision': row.revision, 'review_note': '闭店期间仍允许补交旧资料',
        }, format='json')
        self.assertEqual(rejected.status_code, 200, rejected.content)
        status = self.first_client.post('/api/provider-registration/status/', {
            'username': row.username, 'password': 'test-password-only',
        }, format='json')
        self.assertEqual(status.status_code, 200, status.content)
        self.assertEqual(status.data['data']['status'], 'REJECTED')
        with self.captureOnCommitCallbacks(execute=True):
            resubmitted = self.first_client.post(
                '/api/provider-registration/resubmit/',
                self.provider_payload(display_name='闭店期间补交昵称'), format='multipart',
            )
        self.assertEqual(resubmitted.status_code, 200, resubmitted.content)
        self.assertEqual(resubmitted.data['data']['revision'], 2)
        rules = self.first_client.get('/api/provider-registration/rules/')
        self.assertEqual(rules.status_code, 200, rules.content)
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())

    def test_new_provider_approval_blocks_but_original_approval_replays(self):
        approved = self.submit_provider(self.first, username='closure_approved')
        pending = self.submit_provider(
            self.first, username='closure_pending', phone='13900001002', id_number=self.identity('002'),
        )
        reviewer = self.reviewer(self.first)
        original = self.approve(reviewer, self.first, approved)
        self.assertEqual(original.status_code, 200, original.content)
        account_id = original.data['data']['account_id']
        self.close(self.first)
        with store_context(self.first):
            transaction_count = Transaction.objects.count()

        replay = self.approve(reviewer, self.first, approved)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.data['data']['account_id'], account_id)
        self.assert_closure(self.approve(reviewer, self.first, pending))
        self.pending(self.first)
        self.assert_closure(self.approve(reviewer, self.first, pending))
        with store_context(self.first):
            pending.refresh_from_db()
            self.assertEqual((pending.status, pending.account_id), ('PENDING', None))
            self.assertEqual(ClubAccount.objects.filter(username=approved.username).count(), 1)
            self.assertFalse(ClubAccount.objects.filter(username=pending.username).exists())
            self.assertEqual(Transaction.objects.count(), transaction_count)

    def test_approval_control_failure_preserves_original_replay_and_blocks_new_account(self):
        approved = self.submit_provider(self.first, username='closure_approved')
        pending = self.submit_provider(
            self.first, username='closure_pending', phone='13900001002', id_number=self.identity('002'),
        )
        reviewer = self.reviewer(self.first)
        original = self.approve(reviewer, self.first, approved)
        self.assertEqual(original.status_code, 200, original.content)
        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control offline')):
            replay = self.approve(reviewer, self.first, approved)
            self.assertEqual(replay.status_code, 200, replay.content)
            self.assertEqual(replay.data['data']['account_id'], original.data['data']['account_id'])
            self.assert_closure(self.approve(reviewer, self.first, pending), 503)
        with store_context(self.first):
            pending.refresh_from_db()
            self.assertEqual((pending.status, pending.account_id), ('PENDING', None))

    def test_provider_approval_rechecks_after_late_compliance_validation(self):
        row = self.submit_provider(self.first)
        reviewer = self.reviewer(self.first)

        def compliance_then_close(*args):
            self.close(self.first)
            return None

        with patch('console.provider_registration.check_content', side_effect=compliance_then_close) as checked:
            self.assert_closure(self.approve(reviewer, self.first, row))
        checked.assert_called_once()
        with store_context(self.first):
            row.refresh_from_db()
            self.assertEqual((row.status, row.account_id), ('PENDING', None))
            self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())

    def test_closure_does_not_cross_stores_with_same_registration_id_and_phone(self):
        first_row = self.submit_provider(self.first)
        second_row = self.submit_provider(self.second, self.second_client)
        self.assertEqual(first_row.pk, second_row.pk)
        first_sms = self.boss_start()
        first_code = self.sms_codes[-1][1]
        second_sms = self.boss_start(client=self.second_client)
        second_code = self.sms_codes[-1][1]
        self.close(self.first)

        self.assert_closure(self.boss_register(
            first_sms.data['data']['smsChallenge'], first_code,
        ))
        second_boss = self.boss_register(
            second_sms.data['data']['smsChallenge'], second_code, client=self.second_client,
        )
        self.assertEqual(second_boss.status_code, 201, second_boss.content)
        self.assert_closure(self.approve(self.reviewer(self.first), self.first, first_row))
        second_approval = self.approve(self.reviewer(self.second), self.second, second_row)
        self.assertEqual(second_approval.status_code, 200, second_approval.content)
        other_application = self.second_client.post(
            '/api/provider-registration/', self.provider_payload(
                username='other_store_new', phone='13900001003', id_number=self.identity('003'),
            ), format='multipart',
        )
        self.assertEqual(other_application.status_code, 201, other_application.content)
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.filter(username='closure-boss-new').exists())
            self.assertFalse(ClubAccount.objects.filter(username=first_row.username).exists())
        with store_context(self.second):
            self.assertTrue(ClubAccount.objects.filter(username='closure-boss-new').exists())
            self.assertTrue(ClubAccount.objects.filter(username=second_row.username).exists())

    def test_active_boss_login_and_retirement_preview_continue_during_closure(self):
        started = self.boss_start()
        registered = self.boss_register(
            started.data['data']['smsChallenge'], self.sms_codes[-1][1],
            username='closure-active-boss',
        )
        self.assertEqual(registered.status_code, 201, registered.content)
        with store_context(self.first):
            account = ClubAccount.objects.get(username='closure-active-boss')
        staff = self.reviewer(self.first, ['user:retire'])
        self.close(self.first)

        login_codes = []
        with patch('users.password_login.send_verification_code',
                   side_effect=lambda phone, code: login_codes.append(code)):
            first = self.first_client.post('/api/users/password-login/', {
                'audience': 'boss', 'username': account.username,
                'password': 'Example-Password-2026',
            }, format='json')
            self.assertEqual(first.status_code, 200, first.content)
            logged_in = self.first_client.post('/api/users/password-login/', {
                'audience': 'boss', 'username': account.username,
                'password': 'Example-Password-2026',
                'smsChallenge': first.data['data']['smsChallenge'], 'smsCode': login_codes[-1],
            }, format='json')
        self.assertEqual(logged_in.status_code, 200, logged_in.content)
        preview = staff.get(f'/api/admin/users/{account.pk}/retire-preview/')
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.data['data']['facts']['account_id'], account.pk)

    def test_real_boss_retirement_stays_available_but_new_membership_cycle_blocks(self):
        started = self.boss_start()
        registered = self.boss_register(
            started.data['data']['smsChallenge'], self.sms_codes[-1][1],
            username='closure-returning-boss',
        )
        self.assertEqual(registered.status_code, 201, registered.content)
        self.close(self.first)
        with store_context(self.first):
            account = ClubAccount.objects.get(username='closure-returning-boss')
            operator = ClubAccount.objects.create_account(
                username='closure-retire-operator', account_type='STAFF',
            )
            preview = preview_retirement(account, operator)
            self.assertTrue(preview['can_retire'], preview['facts']['blocking_reasons'])
            old_cycle = account.business_cycle
            account = retire(account, operator, '闭店期间退店测试', preview['confirmation_token'])
            self.assertFalse(account.license_membership_active)
            self.assertEqual(account.business_cycle, old_cycle + 1)
            quota_before = LicenseQuotaLock.objects.get(account_type='BOSS').active_count

            with self.assertRaises(LicenseAdmissionError) as denied:
                issue_account_tokens(account)
            self.assertEqual(denied.exception.status_code, 409)
            self.assertEqual(denied.exception.default_code, 'STORE_CLOSURE_RESTRICTED')
            account.refresh_from_db()
            self.assertFalse(account.license_membership_active)
            self.assertEqual(account.business_cycle, old_cycle + 1)
            self.assertEqual(
                LicenseQuotaLock.objects.get(account_type='BOSS').active_count, quota_before,
            )

        self.pending(self.first)
        with store_context(self.first):
            with self.assertRaises(LicenseAdmissionError) as denied:
                issue_account_tokens(account)
            self.assertEqual(denied.exception.status_code, 409)
            self.assertEqual(denied.exception.default_code, 'STORE_CLOSURE_RESTRICTED')

    def test_reentry_control_failure_and_same_numeric_account_id_are_store_isolated(self):
        first = self.retired_account(self.first, 'same-cycle-boss', 930001)
        second = self.retired_account(self.second, 'same-cycle-boss', 930001)
        self.assertEqual(first.pk, second.pk)
        self.close(self.first)
        with store_context(self.first):
            with self.assertRaises(LicenseAdmissionError) as denied:
                issue_account_tokens(first)
            self.assertEqual(denied.exception.status_code, 409)
            first.refresh_from_db()
            self.assertFalse(first.license_membership_active)
        with store_context(self.second):
            token, _ = issue_account_tokens(second)
            self.assertTrue(token)
            second.refresh_from_db()
            self.assertTrue(second.license_membership_active)

        unavailable = self.retired_account(self.first, 'unavailable-cycle-boss', 930002)
        with store_context(self.first):
            with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control offline')):
                with self.assertRaises(LicenseAdmissionError) as denied:
                    issue_account_tokens(unavailable)
            self.assertEqual(denied.exception.status_code, 503)
            self.assertEqual(denied.exception.default_code, 'STORE_CLOSURE_STATUS_UNAVAILABLE')
            unavailable.refresh_from_db()
            self.assertFalse(unavailable.license_membership_active)

    def test_reentry_preserves_cycle_and_quota_checks_then_rechecks_after_lock(self):
        with store_context(self.first):
            stale = ClubAccount.objects.create_account(username='stale-cycle-boss', account_type='BOSS')
            ClubAccount.objects.filter(pk=stale.pk).update(
                license_membership_active=False, business_cycle=stale.business_cycle + 1,
            )
            # Simulate an authenticated stale snapshot: it knows retirement
            # happened, but a later retirement has advanced the cycle again.
            stale.license_membership_active = False
        self.close(self.first)
        with store_context(self.first):
            with self.assertRaises(LicenseAdmissionError) as denied:
                issue_account_tokens(stale)
            self.assertEqual(denied.exception.status_code, 403)
            self.assertEqual(denied.exception.default_code, 'license_admission_denied')

        returning = self.retired_account(self.second, 'late-cycle-boss', 940001)
        with store_context(self.second), override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=True):
            with patch('club_accounts.license_quota.assert_available') as quota_check:
                with patch('club_accounts.license_quota._assert_certificate_admission',
                           side_effect=lambda: self.close(self.second)):
                    with self.assertRaises(LicenseAdmissionError) as denied:
                        issue_account_tokens(returning)
            self.assertEqual(denied.exception.status_code, 409)
            self.assertEqual(denied.exception.default_code, 'STORE_CLOSURE_RESTRICTED')
            quota_check.assert_called_once()
            self.assertEqual(quota_check.call_args.args[0], 'BOSS')
            returning.refresh_from_db()
            self.assertFalse(returning.license_membership_active)
