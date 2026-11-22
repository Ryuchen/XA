from unittest.mock import patch
from datetime import timedelta
import uuid

from django.test import TestCase, override_settings
from django.db import DatabaseError, InterfaceError, connections
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from club_accounts.otp import encrypt_secret, hotp
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformLoginChallenge, PlatformOtpCredential, PlatformOtpEnrollment, PlatformRoleGrant
from stores.platform_auth import PlatformAuthError, issue_tokens, start_challenge
from stores.tests_foundation import admin
from stores.tests_hosts import xinan_store


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class PlatformAuthenticationTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.client = APIClient(HTTP_HOST='admin.esports.club')
        self.codes = []
        sender = patch('stores.platform_auth.send_verification_code',
                       side_effect=lambda phone, code: self.codes.append(code))
        sender.start()
        self.addCleanup(sender.stop)
        self.account = PlatformAccount(username='platform-finance', phone='13800001111')
        self.account.set_password('test-platform-password')
        self.account.save()
        self.grant = PlatformRoleGrant.objects.create(account=self.account, role='FINANCE', scope_slug='xinan')

    def login(self, **extra):
        return self.client.post('/api/admin/platform/auth/login', {
            'username': self.account.username, 'password': 'test-platform-password', **extra,
        }, format='json')

    def sms_login(self):
        draft = self.login().data['data']
        self.assertTrue(draft['requiresSms'])
        result = self.login(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1])
        self.assertEqual(result.data['code'], 0, result.data)
        return result.data['data']

    def test_sms_platform_identity_is_independent_and_scope_is_enforced(self):
        data = self.sms_login()
        token = data['token']
        self.assertEqual(RefreshToken(data['refresh'])['token_kind'], 'platform_account')
        self.assertNotIn('store_slug', RefreshToken(data['refresh']))
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        profile = self.client.get('/api/admin/platform/profile', **headers)
        self.assertEqual(profile.data['data']['identity_type'], 'platform')
        stores = self.client.get('/api/admin/platform/stores', **headers).data['data']['results']
        self.assertEqual([store['slug'] for store in stores], ['xinan'])
        self.assertEqual(self.client.get('/api/admin/platform/stores?store=shenglang', **headers).status_code, 400)
        self.assertEqual(self.client.get('/api/admin/auth/profile', **headers).status_code, 401)
        self.assertEqual(self.client.post('/api/admin/platform/auth/refresh', {'refresh': data['refresh']}, format='json').status_code, 200)
        self.grant.is_active = False
        self.grant.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/api/admin/platform/profile', **headers).status_code, 401)
        self.assertEqual(self.client.post('/api/admin/platform/auth/refresh', {'refresh': data['refresh']}, format='json').status_code, 401)

    def test_store_token_cannot_be_used_as_platform_identity(self):
        with store_context(self.first):
            account = admin('platform-finance')
            account.is_phone_verified = True
            account.save(update_fields=['is_phone_verified'])
            token, _ = issue_account_tokens(account, auth_method='password_sms')
        response = self.client.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(response.status_code, 401)

    def test_changed_phone_invalidates_existing_sms_session(self):
        data = self.sms_login()
        self.account.phone = '13800002222'
        self.account.save(update_fields=['phone'])
        response = self.client.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + data['token'])
        self.assertEqual(response.status_code, 401)

    def test_unified_login_can_retry_if_platform_identity_discovery_disconnects(self):
        url = '/api/admin/auth/password-login'
        credentials = {'username': self.account.username, 'password': 'test-platform-password'}

        def disconnect(execute, sql, params, many, context):
            if table in sql.lower():
                raise InterfaceError('test disconnected during identity discovery')
            return execute(sql, params, many, context)

        for table in ('stores_platformaccount', 'stores_store'):
            with self.subTest(table=table):
                with connections['default'].execute_wrapper(disconnect):
                    response = self.client.post(url, credentials, format='json')
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('data', response.data)
                self.assertFalse(PlatformLoginChallenge.objects.filter(account=self.account).exists())
                self.assertEqual(self.codes, [])

        retry = self.client.post(url, credentials, format='json')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data['data']['identity_kind'], 'platform')
        self.assertEqual(len(self.codes), 1)

    def test_platform_challenge_can_retry_when_profile_query_disconnects(self):
        from stores.platform_views import profile as original_profile

        draft = self.login().data['data']
        challenge_id = draft['smsChallenge']
        code = self.codes[-1]
        url = '/api/admin/auth/password-login'
        data = {'username': self.account.username, 'password': 'test-platform-password',
                'identityKind': 'platform', 'smsChallenge': challenge_id, 'smsCode': code}
        def disconnect(execute, sql, params, many, context):
            raise InterfaceError('test disconnected during platform profile read')

        def disconnected_profile(account):
            with connections['default'].execute_wrapper(disconnect):
                return original_profile(account)

        with patch('stores.platform_views.profile', side_effect=disconnected_profile):
            response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['errorCode'], 'PLATFORM_UNAVAILABLE')
        self.assertNotIn('data', response.data)
        challenge = PlatformLoginChallenge.objects.get(pk=challenge_id)
        self.assertFalse(challenge.consumed)
        self.assertEqual(challenge.attempts, 0)
        self.assertFalse(PlatformAuditEvent.objects.filter(
            account=self.account, action='auth.second_factor_verified').exists())

        retry = self.client.post(url, data, format='json')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data['data']['profile']['identity_type'], 'platform')
        challenge.refresh_from_db()
        self.assertTrue(challenge.consumed)
        self.assertEqual(challenge.attempts, 1)
        self.assertEqual(self.client.post(url, data, format='json').status_code, 400)

    def test_platform_refresh_can_retry_after_a_disconnected_control_database(self):
        tokens = self.sms_login()
        path = '/api/admin/platform/auth/refresh'
        payload = {'refresh': tokens['refresh']}

        def disconnect(execute, sql, params, many, context):
            raise InterfaceError('test disconnected during token validation')

        with connections['default'].execute_wrapper(disconnect):
            response = self.client.post(path, payload, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['errorCode'], 'PLATFORM_UNAVAILABLE')
        self.assertNotIn('data', response.data)

        retry = self.client.post(path, payload, format='json')
        self.assertEqual(retry.status_code, 200)
        fresh_access = retry.data['data']['token']
        self.assertEqual(self.client.get('/api/admin/platform/profile',
                        HTTP_AUTHORIZATION='Bearer ' + fresh_access).status_code, 200)

    def test_uncertain_delivery_does_not_erase_platform_cooldown(self):
        from users.sms_transport import SmsUnavailable
        with patch('stores.platform_auth.send_verification_code', side_effect=SmsUnavailable('发送结果不明')) as send:
            self.assertEqual(self.login().status_code, 503)
            self.assertEqual(PlatformLoginChallenge.objects.filter(account=self.account, method='SMS').count(), 1)
            blocked = self.login()
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.data['errorCode'], 'SMS_RATE_LIMITED')
            self.assertEqual(send.call_count, 1)

    def test_sms_attempts_are_persisted_and_old_phone_cannot_verify_new_phone(self):
        draft = self.login().data['data']
        wrong = '000000' if self.codes[-1] != '000000' else '999999'
        for _ in range(5):
            self.assertEqual(self.login(smsChallenge=draft['smsChallenge'], smsCode=wrong).status_code, 400)
        self.assertEqual(PlatformLoginChallenge.objects.get(pk=draft['smsChallenge']).attempts, 5)
        self.assertEqual(self.login(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1]).status_code, 400)
        PlatformLoginChallenge.objects.all().update(created_at=timezone.now()-timedelta(minutes=6))
        next_draft = self.login().data['data']
        self.account.phone = '13800002222'
        self.account.save(update_fields=['phone'])
        self.assertEqual(self.login(smsChallenge=next_draft['smsChallenge'], smsCode=self.codes[-1]).status_code, 400)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_phone_verified)

    def test_bound_otp_defaults_to_otp_and_replay_is_rejected(self):
        secret = 'JBSWY3DPEHPK3PXP'
        credential = PlatformOtpCredential.objects.create(account=self.account, secret_ciphertext=encrypt_secret(secret))
        draft = self.login().data['data']
        self.assertTrue(draft['requiresOTP'])
        code = hotp(secret, int(timezone.now().timestamp()) // 30)
        result = self.login(otpChallenge=draft['otpChallenge'], otpCode=code)
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(self.login(otpChallenge=draft['otpChallenge'], otpCode=code).status_code, 400)
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + result.data['data']['token']}
        sms = self.login(verificationMethod='sms').data['data']
        self.assertTrue(sms['requiresSms'])
        fallback = self.login(smsChallenge=sms['smsChallenge'], smsCode=self.codes[-1])
        self.assertEqual(RefreshToken(fallback.data['data']['refresh'])['auth_method'], 'SMS')
        self.assertEqual(self.login(smsChallenge=sms['smsChallenge'], smsCode=self.codes[-1]).status_code, 400)
        credential.delete()
        self.assertEqual(self.client.get('/api/admin/platform/profile', **headers).status_code, 401)

    def test_platform_login_is_restricted_to_admin_host_and_rejects_hints(self):
        foreign = APIClient(HTTP_HOST='xa.esports.club')
        response = foreign.post('/api/admin/platform/auth/login', {
            'username': self.account.username, 'password': 'test-platform-password',
        }, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post('/api/admin/platform/auth/login?store=xinan', {}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/admin/platform/auth/login', [], format='json').status_code, 400)

    @override_settings(PLATFORM_IDENTITY_ENABLED=False)
    def test_disabled_platform_login_does_not_query_unmigrated_tables(self):
        with patch('stores.platform_views.authenticate_platform', side_effect=AssertionError('unexpected query')):
            self.assertEqual(self.login().status_code, 503)
        self.assertEqual(self.client.get('/api/admin/platform/profile').status_code, 401)

    def test_changed_security_version_cannot_start_challenge_with_stale_password_check(self):
        PlatformAccount.objects.filter(pk=self.account.pk).update(security_version=uuid.uuid4())
        with self.assertRaises(PlatformAuthError):
            start_challenge(self.account)
        self.assertFalse(PlatformLoginChallenge.objects.exists())
        self.assertEqual(self.codes, [])

    def test_otp_enrollment_binds_current_session_and_recovery_code_is_single_use(self):
        token = self.sms_login()['token']
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        url = '/api/admin/platform/auth/otp-setup'
        self.assertFalse(self.client.get(url, **headers).data['data']['isOtpBound'])
        draft_response = self.client.post(url, {}, format='json', **headers)
        self.assertEqual(draft_response['Cache-Control'], 'no-store')
        draft = draft_response.data['data']
        self.assertTrue(draft['otpQr'].startswith('data:image/png;base64,'))
        self.assertNotEqual(PlatformOtpEnrollment.objects.get(pk=draft['otpChallenge']).secret_ciphertext, draft['otpSecret'])
        self.assertEqual(self.client.post(url, {'otpChallenge': draft['otpChallenge'], 'otpCode': 'notvalid'}, format='json', **headers).status_code, 400)
        self.assertEqual(PlatformOtpEnrollment.objects.get(pk=draft['otpChallenge']).attempts, 1)
        code = hotp(draft['otpSecret'], int(timezone.now().timestamp()) // 30)
        done = self.client.post(url, {'otpChallenge': draft['otpChallenge'], 'otpCode': code}, format='json', **headers)
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(done['Cache-Control'], 'no-store')
        codes = done.data['data']['recoveryCodes']
        self.assertEqual(len(codes), 8)
        self.assertTrue(PlatformOtpCredential.objects.filter(account=self.account).exists())
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.account, action='auth.otp_enrolled').exists())
        self.assertEqual(self.client.post(url, {}, format='json', **headers).status_code, 400)
        login = self.login().data['data']
        self.assertTrue(login['requiresOTP'])
        self.assertEqual(self.login(otpChallenge=login['otpChallenge'], otpCode=codes[0]).status_code, 200)
        next_login = self.login().data['data']
        self.assertEqual(self.login(otpChallenge=next_login['otpChallenge'], otpCode=codes[0]).status_code, 400)

    def test_otp_enrollment_cannot_cross_login_sessions_or_overwrite_binding(self):
        first = self.sms_login()['token']
        second, _ = issue_tokens(self.account, 'SMS', self.account.phone)
        first_headers = {'HTTP_AUTHORIZATION': 'Bearer ' + first}
        second_headers = {'HTTP_AUTHORIZATION': 'Bearer ' + second}
        url = '/api/admin/platform/auth/otp-setup'
        old = self.client.post(url, {}, format='json', **first_headers).data['data']
        code = hotp(old['otpSecret'], int(timezone.now().timestamp()) // 30)
        self.assertEqual(self.client.post(url, {'otpChallenge': old['otpChallenge'], 'otpCode': code}, format='json', **second_headers).status_code, 400)
        new = self.client.post(url, {}, format='json', **second_headers).data['data']
        self.assertEqual(self.client.post(url, {'otpChallenge': old['otpChallenge'], 'otpCode': code}, format='json', **first_headers).status_code, 400)
        self.grant.is_active = False
        self.grant.save(update_fields=['is_active'])
        code = hotp(new['otpSecret'], int(timezone.now().timestamp()) // 30)
        self.assertEqual(self.client.post(url, {'otpChallenge': new['otpChallenge'], 'otpCode': code}, format='json', **second_headers).status_code, 401)
        self.assertFalse(PlatformOtpCredential.objects.exists())

    def test_otp_enrollment_attempt_limit_and_cancel(self):
        token = self.sms_login()['token']
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        url = '/api/admin/platform/auth/otp-setup'
        draft = self.client.post(url, {}, format='json', **headers).data['data']
        for _ in range(5):
            response = self.client.post(url, {'otpChallenge': draft['otpChallenge'], 'otpCode': 'bad'}, format='json', **headers)
            self.assertEqual(response.status_code, 400)
        code = hotp(draft['otpSecret'], int(timezone.now().timestamp()) // 30)
        self.assertEqual(self.client.post(url, {'otpChallenge': draft['otpChallenge'], 'otpCode': code}, format='json', **headers).status_code, 400)
        cancelled = self.client.post(url, {'otpChallenge': draft['otpChallenge'], 'cancel': True}, format='json', **headers)
        self.assertFalse(cancelled.data['data']['isOtpBound'])
        self.assertTrue(PlatformOtpEnrollment.objects.get(pk=draft['otpChallenge']).consumed)
        self.assertFalse(PlatformOtpCredential.objects.exists())

    @override_settings(PLATFORM_IDENTITY_ENABLED=True)
    def test_unified_login_selects_platform_and_requires_explicit_choice_on_collision(self):
        url = '/api/admin/auth/password-login'
        data = {'username': self.account.username, 'password': 'test-platform-password'}
        draft = self.client.post(url, data, format='json')
        self.assertEqual(draft.data['data']['identity_kind'], 'platform')
        challenge = draft.data['data']['smsChallenge']
        result = self.client.post(url, {**data, 'identityKind': 'platform',
            'smsChallenge': challenge, 'smsCode': self.codes[-1]}, format='json')
        self.assertEqual(result.data['data']['profile']['identity_type'], 'platform')
        with store_context(self.first):
            account = admin(self.account.username)
            account.set_password('test-platform-password')
            account.save(update_fields=['password'])
        conflict = self.client.post(url, data, format='json')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['errorCode'], 'ADMIN_IDENTITY_AMBIGUOUS')
        # Choosing a domain does not replace a correct password.
        invalid = self.client.post(url, {**data, 'password': 'incorrect', 'identityKind': 'platform'}, format='json')
        self.assertEqual(invalid.status_code, 400)

    @override_settings(PLATFORM_IDENTITY_ENABLED=True)
    def test_unified_login_platform_fallback_and_resend_validation(self):
        secret = 'JBSWY3DPEHPK3PXP'
        PlatformOtpCredential.objects.create(account=self.account, secret_ciphertext=encrypt_secret(secret))
        url = '/api/admin/auth/password-login'
        data = {'username': self.account.username, 'password': 'test-platform-password'}
        otp = self.client.post(url, data, format='json').data['data']
        self.assertEqual(otp['identity_kind'], 'platform')
        self.assertTrue(otp['requiresOTP'])
        sms = self.client.post(url, {**data, 'identityKind': 'platform', 'verificationMethod': 'sms'}, format='json').data['data']
        self.assertTrue(sms['requiresSms'])
        code = hotp(secret, int(timezone.now().timestamp()) // 30)
        stale = self.client.post(url, {**data, 'identityKind': 'platform', 'otpChallenge': otp['otpChallenge'], 'otpCode': code}, format='json')
        self.assertEqual(stale.status_code, 400)
        bad = self.client.post(url, {**data, 'identityKind': 'platform', 'resend': True, 'smsChallenge': 'not-a-uuid'}, format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post(url, {**data, 'identityKind': 'platform', 'smsChallenge': sms['smsChallenge'],
            'smsCode': self.codes[-1]}, format='json').data['data']['profile']['identity_type'], 'platform')
        self.assertEqual(self.client.post(url, {**data, 'store': 'xinan'}, format='json').status_code, 400)

    def test_aggregate_reads_each_database_and_reports_unavailable_store(self):
        with store_context(self.first):
            ClubAccount.objects.create_account(username='same-customer', account_type='BOSS')
        with store_context(self.second):
            ClubAccount.objects.create_account(username='same-customer', account_type='BOSS')
            ClubAccount.objects.create_account(username='second-player', account_type='PROVIDER')
        token = self.sms_login()['token']
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        self.assertEqual(self.client.get('/api/admin/platform/dashboard', **headers).status_code, 403)
        PlatformRoleGrant.objects.create(account=self.account, role='SUPER_ADMIN', scope_slug='*')
        data = self.client.get('/api/admin/platform/dashboard', **headers).data['data']
        rows = {item['slug']: item for item in data['stores']}
        self.assertTrue(data['complete'])
        self.assertEqual(rows[self.first.slug]['counts']['bosses'], 1)
        self.assertEqual(rows['xinan']['counts']['bosses'], 1)
        self.assertEqual(rows['xinan']['counts']['providers'], 1)
        self.assertEqual(data['partial_totals']['bosses'], 2)
        for error in (DatabaseError, InterfaceError):
            def unavailable(execute, sql, params, many, context):
                raise error('test unavailable tenant database')

            with self.subTest(error=error.__name__):
                with connections[self.second.database_alias].execute_wrapper(unavailable):
                    data = self.client.get('/api/admin/platform/dashboard', **headers).data['data']
                rows = {item['slug']: item for item in data['stores']}
                self.assertFalse(data['complete'])
                self.assertEqual(rows['xinan']['status'], 'UNAVAILABLE')
                self.assertIsNone(rows['xinan']['counts'])
                self.assertIsNone(rows['xinan']['read_at'])
                self.assertIsNotNone(rows[self.first.slug]['read_at'])
                self.assertEqual(data['partial_totals']['bosses'], 1)
        recovered = self.client.get('/api/admin/platform/dashboard', **headers).data['data']
        self.assertTrue(recovered['complete'])
        self.assertEqual(recovered['partial_totals']['bosses'], 2)

    def test_aggregate_control_failure_during_final_permission_check_is_retryable(self):
        PlatformRoleGrant.objects.create(account=self.account, role='SUPER_ADMIN', scope_slug='*')
        token = self.sms_login()['token']
        path = '/api/admin/platform/dashboard'
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        control_offline = False

        def read_tenant(execute, sql, params, many, context):
            nonlocal control_offline
            result = execute(sql, params, many, context)
            if sql.lstrip().upper().startswith('SELECT'):
                control_offline = True
            return result

        def read_control(execute, sql, params, many, context):
            if control_offline:
                raise error('test unavailable control database')
            return execute(sql, params, many, context)

        for error in (DatabaseError, InterfaceError):
            with self.subTest(error=error.__name__):
                control_offline = False
                with (connections['default'].execute_wrapper(read_control),
                      connections[self.second.database_alias].execute_wrapper(read_tenant)):
                    unavailable = self.client.get(path, **headers)
                self.assertTrue(control_offline)
                self.assertEqual(unavailable.status_code, 503)
                self.assertEqual(unavailable.data['errorCode'], 'PLATFORM_UNAVAILABLE')
                self.assertNotIn('data', unavailable.data)

        recovered = self.client.get(path, **headers)
        self.assertEqual(recovered.status_code, 200)
        self.assertTrue(recovered.data['data']['complete'])

    def test_dashboard_authentication_with_disconnected_control_database_is_retryable(self):
        PlatformRoleGrant.objects.create(account=self.account, role='SUPER_ADMIN', scope_slug='*')
        token = self.sms_login()['token']

        def disconnected(execute, sql, params, many, context):
            raise InterfaceError('test disconnected control database')

        with connections['default'].execute_wrapper(disconnected):
            response = self.client.get('/api/admin/platform/dashboard', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('data', response.data)

    def test_aggregate_discards_results_when_global_grant_is_revoked_during_read(self):
        grant = PlatformRoleGrant.objects.create(account=self.account, role='SUPER_ADMIN', scope_slug='*')
        token = self.sms_login()['token']

        from stores.platform_dashboard import _read_store

        def read_and_revoke(store):
            counts = _read_store(store)
            if store.pk == self.second.pk:
                PlatformRoleGrant.objects.filter(pk=grant.pk).update(is_active=False)
            return counts

        with patch('stores.platform_dashboard._read_store', side_effect=read_and_revoke):
            response = self.client.get('/api/admin/platform/dashboard', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['errorCode'], 'PLATFORM_FORBIDDEN')
        self.assertNotIn('data', response.data)

    def test_aggregate_discards_results_when_session_changes_during_read(self):
        from stores.platform_dashboard import _read_store

        grant = PlatformRoleGrant.objects.create(account=self.account, role='SUPER_ADMIN', scope_slug='*')
        path = '/api/admin/platform/dashboard'
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + self.sms_login()['token']}
        self.assertEqual(self.client.get(path, **headers).status_code, 200)

        def read_and_rotate_session(store):
            counts = _read_store(store)
            if store.pk == self.second.pk:
                PlatformAccount.objects.filter(pk=self.account.pk).update(security_version=uuid.uuid4())
            return counts

        with patch('stores.platform_dashboard._read_store', side_effect=read_and_rotate_session):
            response = self.client.get(path, **headers)
        grant.refresh_from_db()
        self.assertTrue(grant.is_active)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('data', response.data)
        retry = self.client.get(path, **headers)
        self.assertEqual(retry.status_code, 401)
        self.assertEqual(response.data, retry.data)
        self.assertEqual(response['WWW-Authenticate'], retry['WWW-Authenticate'])
