"""Closure guards limited to H5 account creation and provider cycle re-entry."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.test import APIClient, APITransactionTestCase
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.license_quota import LicenseAdmissionError
from club_accounts.models import ClubAccount, LicenseQuotaLock, WechatIdentity
from club_accounts.provider_retirement import preview_retirement, retire_provider
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import account_for_claims, issue_account_tokens
from console.exceptions import console_exception_handler
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider, make_wallet
from stores.closure_clock import CLOCK_SOURCE
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, StoreClosureEvent, StoreClosureNotice
from stores.tests_hosts import xinan_store
from users.h5_login import H5_CHANNEL
from users.models import WebLoginSession


@override_settings(
    ALLOWED_HOSTS=['*'],
    DEBUG=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LOGIN_OTP_REQUIRED=False,
    PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True,
    PROVIDER_RETIREMENT_ENABLED=True,
    PROVIDER_WECHAT_GATE_ENABLED=False,
    WECHAT_H5_APPID='wx-h5-closure-test',
    WECHAT_H5_SECRET='isolated-test-secret',
    WECHAT_H5_ALLOWED_ORIGINS=['http://sl.esports.club', 'http://xa.esports.club'],
)
class ClosureH5AndProviderRejoinTests(APITransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.first_client = APIClient(HTTP_HOST='sl.esports.club')
        self.second_client = APIClient(HTTP_HOST='xa.esports.club')
        self.actor = PlatformAccount.objects.create(username='closure-h5-rejoin-actor')
        self.clock_now = timezone.now()
        self.proof = {'openid': 'closure-h5-openid', 'unionid': 'closure-h5-unionid'}
        exchange = patch('users.h5_login.exchange_code', side_effect=lambda code: dict(self.proof))
        exchange.start()
        self.addCleanup(exchange.stop)

    def close(self, store):
        notice = StoreClosureNotice.objects.filter(store=store).first()
        if notice is not None:
            return notice
        return StoreClosureNotice.objects.create(
            store=store, reason_category='OPERATOR_REQUEST', reason='H5 重入测试内部闭店原因',
            applicant=self.actor, started_at=self.clock_now,
            notice_policy_version='SA02_NOTICE_30D_V1', notice_days=30,
            earliest_settlement_at=self.clock_now + timedelta(days=30),
            client_request_id=f'closure-h5-{store.slug}', request_fingerprint='c' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )

    def pending(self, store):
        notice = self.close(store)
        event = StoreClosureEvent.objects.filter(notice=notice).first()
        if event is not None:
            return event
        return StoreClosureEvent.objects.create(
            store=store, notice=notice, kind='NOTICE_EXPIRED', reason='H5 重入到期确认内部原因',
            actor=self.actor, occurred_at=notice.earliest_settlement_at,
            client_request_id=f'closure-h5-expiry-{store.slug}', request_fingerprint='d' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )

    def start(self, client, *, audience='boss', purpose='login'):
        response = client.post('/api/users/h5-login/start/', {
            'audience': audience, 'purpose': purpose,
            'return_origin': 'http://' + client.defaults['HTTP_HOST'],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.data['data']

    def callback(self, client, flow):
        state = parse_qs(urlsplit(flow['authorization_url']).query)['state'][0]
        return client.get('/api/users/h5-login/callback/', {
            'state': state, 'code': 'isolated-wechat-code',
        })

    def status(self, client, flow):
        return client.get('/api/users/h5-login/status/', {
            'ticket': flow['ticket'], 'poll_token': flow['poll_token'],
        })

    def assert_callback_success(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('微信授权已完成'.encode(), response.content)

    def assert_closure(self, response, status=409):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.data['code'], status)
        self.assertEqual(
            response.data['errorCode'],
            'STORE_CLOSURE_RESTRICTED' if status == 409 else 'STORE_CLOSURE_STATUS_UNAVAILABLE',
        )
        self.assertNotIn('H5 重入测试内部闭店原因', str(response.data))

    def session(self, store, flow):
        with store_context(store):
            return WebLoginSession.objects.get(ticket=flow['ticket'])

    def existing_boss(self, store, username, *, openid=None):
        with store_context(store):
            account = ClubAccount.objects.create_account(
                username=username, password='unused', account_type='BOSS',
                phone='13800001111', is_phone_verified=True,
                is_openid_bound=openid is not None,
            )
            if openid is not None:
                WechatIdentity.objects.create(
                    account=account, channel=H5_CHANNEL, appid='wx-h5-closure-test', openid=openid,
                )
            return account

    def provider(self, store, username, account_id, *, retired=False, with_history=False):
        with store_context(store):
            account = ClubAccount.objects.create_account(
                id=account_id, account_no=f'XA{account_id}', username=username,
                password='provider-password', account_type='PROVIDER',
                phone='13900001111', is_phone_verified=True,
            )
            user = profile = wallet = None
            if with_history:
                user = make_provider(deposit_mode='EARNINGS', deposit_paid=0, status='OFFLINE')
                profile = user.escort_profile
                profile.account = account
                profile.save(update_fields=['account'])
                wallet = make_wallet(user, balance=0, frozen_amount=0)
                wallet.account = account
                wallet.save(update_fields=['account'])
            if retired:
                ClubAccount.objects.filter(pk=account.pk).update(license_membership_active=False)
                account.refresh_from_db()
            return account, user, profile, wallet

    def reviewer(self, store):
        with store_context(store):
            user = make_console_user(['escort:retire'])
            account = get_or_create_account_for_legacy_user(user)
            AdminMembership.objects.filter(user=user).update(account=account)
            return account

    def test_new_h5_boss_blocks_notice_and_pending_without_confirming_session(self):
        flow = self.start(self.first_client)
        self.close(self.first)
        self.assert_closure(self.callback(self.first_client, flow))
        self.assertEqual(self.session(self.first, flow).confirmed_account_id, None)
        self.pending(self.first)
        self.assert_closure(self.callback(self.first_client, flow))
        recovered = self.status(self.first_client, flow)
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertEqual(recovered.data['data']['status'], 'PENDING')
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.exists())
            self.assertFalse(WechatIdentity.objects.exists())

    def test_new_h5_boss_control_failure_returns_503_without_wechat_success(self):
        flow = self.start(self.first_client)
        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control offline')):
            self.assert_closure(self.callback(self.first_client, flow), 503)
        self.assertIsNone(self.session(self.first, flow).confirmed_account_id)
        self.assertEqual(self.status(self.first_client, flow).data['data']['status'], 'PENDING')
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.exists())
            self.assertFalse(WechatIdentity.objects.exists())

    def test_new_h5_boss_rechecks_after_default_type_wait(self):
        from users.boss_types import default_boss_type
        flow = self.start(self.first_client)

        def type_then_close():
            boss_type = default_boss_type()
            self.close(self.first)
            return boss_type

        with patch('users.boss_types.default_boss_type', side_effect=type_then_close):
            self.assert_closure(self.callback(self.first_client, flow))
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.exists())
            self.assertFalse(WechatIdentity.objects.exists())

    def test_existing_h5_boss_login_refresh_and_original_session_recovery_continue(self):
        flow = self.start(self.first_client)
        self.assert_callback_success(self.callback(self.first_client, flow))
        created = self.status(self.first_client, flow)
        self.assertEqual(created.status_code, 200, created.content)
        account_id = created.data['data']['userInfo']['id']
        self.close(self.first)

        quick_flow = self.start(self.first_client)
        self.assert_callback_success(self.callback(self.first_client, quick_flow))
        quick = self.status(self.first_client, quick_flow)
        self.assertEqual(quick.status_code, 200, quick.content)
        self.assertEqual(quick.data['data']['userInfo']['id'], account_id)
        replay = self.status(self.first_client, quick_flow)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.data['data']['userInfo']['id'], account_id)
        refreshed = self.first_client.post('/api/users/refresh/', {
            'refresh': quick.data['data']['refreshToken'],
        }, format='json')
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        with store_context(self.first):
            self.assertEqual(ClubAccount.objects.count(), 1)
            self.assertEqual(WechatIdentity.objects.count(), 1)

    def test_existing_h5_binding_survives_closure_and_control_outage_without_new_account(self):
        account = self.existing_boss(self.first, 'h5-bind-existing')
        with store_context(self.first):
            token = issue_account_tokens(account)[0]
        client = APIClient(
            HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token,
        )
        flow = self.start(client, purpose='bind_wechat')
        client.credentials()
        self.close(self.first)
        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control offline')):
            self.assert_callback_success(self.callback(client, flow))
            bound = self.status(client, flow)
        self.assertEqual(bound.status_code, 200, bound.content)
        self.assertEqual(bound.data['data']['userInfo']['id'], str(account.pk))
        with store_context(self.first):
            account.refresh_from_db()
            self.assertTrue(account.is_openid_bound)
            self.assertEqual(ClubAccount.objects.count(), 1)
            self.assertEqual(WechatIdentity.objects.get(account=account).openid, self.proof['openid'])

    def test_new_h5_boss_guard_is_store_isolated_for_same_wechat_identity(self):
        first_flow = self.start(self.first_client)
        second_flow = self.start(self.second_client)
        self.close(self.first)
        self.assert_closure(self.callback(self.first_client, first_flow))
        self.assert_callback_success(self.callback(self.second_client, second_flow))
        second = self.status(self.second_client, second_flow)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.data['data']['status'], 'CONFIRMED')
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.exists())
            self.assertFalse(WechatIdentity.objects.exists())
        with store_context(self.second):
            self.assertEqual(ClubAccount.objects.count(), 1)
            self.assertEqual(WechatIdentity.objects.get().openid, self.proof['openid'])

    def test_provider_retirement_and_original_receipt_continue_but_new_cycle_reentry_blocks(self):
        account, user, profile, wallet = self.provider(
            self.first, 'closure-retiring-provider', 950001, with_history=True,
        )
        operator = self.reviewer(self.first)
        with store_context(self.first):
            old_token = issue_account_tokens(account)[0]
            old_cycle = account.business_cycle
        self.close(self.first)
        reason = '闭店期间陪玩退店'
        request_id = 'provider-retire-0001'
        with store_context(self.first):
            preview = preview_retirement(account, operator)
            self.assertTrue(preview['can_retire'], preview['facts']['blocking_reasons'])
            with patch('club_accounts.provider_retirement.lock_provider_closure', return_value=None):
                original = retire_provider(
                    account, operator, reason, preview['confirmation_token'],
                    confirm=True, request_id=request_id,
                )
            self.assertFalse(original['replayed'])
            self.assertEqual(original['business_cycle'], old_cycle + 1)
            account.refresh_from_db()
            retired_cycle = account.business_cycle
            self.assertFalse(account.license_membership_active)
            quota_before = LicenseQuotaLock.objects.get(account_type='PROVIDER').active_count
            with patch('club_accounts.provider_lifecycle.lock_provider_closure', return_value=None):
                with self.assertRaises(LicenseAdmissionError) as denied:
                    issue_account_tokens(account)
            self.assertEqual(denied.exception.status_code, 409)
            self.assertEqual(denied.exception.default_code, 'STORE_CLOSURE_RESTRICTED')
            account.refresh_from_db()
            self.assertFalse(account.license_membership_active)
            self.assertEqual(account.business_cycle, retired_cycle)
            self.assertEqual(LicenseQuotaLock.objects.get(account_type='PROVIDER').active_count, quota_before)
            with self.assertRaises(TokenError):
                account_for_claims(AccessToken(old_token))
            with patch('club_accounts.provider_retirement.lock_provider_closure', return_value=None):
                replay = retire_provider(
                    account, operator, reason, preview['confirmation_token'],
                    confirm=True, request_id=request_id,
                )
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['business_cycle'], retired_cycle)
            self.assertEqual(preview_retirement(account, operator)['facts']['business_cycle'], retired_cycle)

        self.pending(self.first)
        with store_context(self.first):
            with patch('club_accounts.provider_lifecycle.lock_provider_closure', return_value=None):
                with self.assertRaises(LicenseAdmissionError) as denied:
                    issue_account_tokens(account)
            self.assertEqual(denied.exception.status_code, 409)
            account.refresh_from_db()
            self.assertEqual(account.business_cycle, retired_cycle)

    def test_provider_reentry_control_failure_preserves_cycle_and_quota(self):
        account, *_ = self.provider(self.first, 'provider-control-offline', 950002, retired=True)
        self.close(self.first)
        with store_context(self.first):
            cycle = account.business_cycle
            quota = LicenseQuotaLock.objects.get(account_type='PROVIDER').active_count
            with patch('club_accounts.provider_lifecycle.lock_provider_closure', return_value=None):
                with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control offline')):
                    with self.assertRaises(LicenseAdmissionError) as denied:
                        issue_account_tokens(account)
            self.assertEqual(denied.exception.status_code, 503)
            self.assertEqual(denied.exception.default_code, 'STORE_CLOSURE_STATUS_UNAVAILABLE')
            account.refresh_from_db()
            self.assertFalse(account.license_membership_active)
            self.assertEqual(account.business_cycle, cycle)
            self.assertEqual(LicenseQuotaLock.objects.get(account_type='PROVIDER').active_count, quota)

    def test_provider_reentry_same_numeric_id_is_store_isolated(self):
        first, *_ = self.provider(self.first, 'same-provider-cycle', 950003, retired=True)
        second, *_ = self.provider(self.second, 'same-provider-cycle', 950003, retired=True)
        self.assertEqual(first.pk, second.pk)
        self.close(self.first)
        with store_context(self.first):
            with patch('club_accounts.provider_lifecycle.lock_provider_closure', return_value=None):
                with self.assertRaises(LicenseAdmissionError) as denied:
                    issue_account_tokens(first)
            self.assertEqual(denied.exception.status_code, 409)
            first.refresh_from_db()
            self.assertFalse(first.license_membership_active)
        with store_context(self.second):
            old_cycle = second.business_cycle
            with patch('club_accounts.provider_lifecycle.lock_provider_closure', return_value=None):
                token, _ = issue_account_tokens(second)
            self.assertTrue(token)
            second.refresh_from_db()
            self.assertTrue(second.license_membership_active)
            self.assertEqual(second.business_cycle, old_cycle + 1)

    def test_provider_reentry_completed_while_waiting_is_existing_result_not_new_cycle(self):
        account, *_ = self.provider(self.first, 'provider-concurrent-rejoin', 950004, retired=True)
        with store_context(self.first):
            ClubAccount.objects.filter(pk=account.pk).update(
                license_membership_active=True, business_cycle=account.business_cycle + 1,
            )
            completed_cycle = account.business_cycle + 1
        self.close(self.first)
        with store_context(self.first):
            with patch('club_accounts.provider_lifecycle.lock_provider_closure', return_value=None):
                with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('must not read')):
                    token, _ = issue_account_tokens(account)
            self.assertTrue(token)
            self.assertTrue(account.license_membership_active)
            self.assertEqual(account.business_cycle, completed_cycle)

    def test_provider_h5_original_session_allows_active_login_then_maps_reentry_errors(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                suffix = '503' if unavailable else '409'
                account, *_ = self.provider(self.first, 'provider-h5-rejoin-' + suffix, 950010 + int(unavailable))
                with store_context(self.first):
                    account.is_openid_bound = True
                    account.save(update_fields=['is_openid_bound', 'updated_at'])
                    WechatIdentity.objects.create(
                        account=account, channel=H5_CHANNEL, appid='wx-h5-closure-test',
                        openid='provider-h5-openid-' + suffix,
                    )
                self.proof = {
                    'openid': 'provider-h5-openid-' + suffix,
                    'unionid': 'provider-h5-unionid-' + suffix,
                }
                flow = self.start(self.first_client, audience='provider')
                self.assert_callback_success(self.callback(self.first_client, flow))
                self.close(self.first)
                active = self.status(self.first_client, flow)
                self.assertEqual(active.status_code, 200, active.content)
                self.assertEqual(active.data['data']['status'], 'CONFIRMED')
                with store_context(self.first):
                    ClubAccount.objects.filter(pk=account.pk).update(license_membership_active=False)
                    account.refresh_from_db()
                    cycle = account.business_cycle
                with patch('club_accounts.provider_lifecycle.lock_provider_closure', return_value=None):
                    if unavailable:
                        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control offline')):
                            denied = self.status(self.first_client, flow)
                    else:
                        denied = self.status(self.first_client, flow)
                self.assert_closure(denied, 503 if unavailable else 409)
                with store_context(self.first):
                    account.refresh_from_db()
                    self.assertFalse(account.license_membership_active)
                    self.assertEqual(account.business_cycle, cycle)
                self.assertEqual(self.session(self.first, flow).confirmed_account_id, account.pk)


class ClosureExceptionEnvelopeTests(SimpleTestCase):
    def test_non_closure_conflict_keeps_existing_envelope_without_new_error_code(self):
        class OtherConflict(APIException):
            status_code = 409
            default_code = 'OTHER_CONFLICT'

        response = console_exception_handler(OtherConflict('其他冲突'), {})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 409)
        self.assertEqual(response.data['msg'], '其他冲突')
        self.assertNotIn('errorCode', response.data)
