"""闭店只限制新通行证购买，不影响原回执、报价、读取和既有退款。"""
from datetime import datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger
from engagement.services import config
from orders.tests.factories import make_provider, make_user
from stores.closure_clock import CLOCK_SOURCE
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, StoreClosureEvent, StoreClosureNotice
from stores.tests_hosts import xinan_store
from users.models import ProviderPassPurchase, ProviderPassRefund
from wallet.services import get_wallet


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
    ALLOWED_HOSTS=['*'],
)
class ClosureProviderPassEntryTests(APITestCase):
    databases = {'default', 'store_b'}
    url = '/api/users/provider-pass/'

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.closure_actor = PlatformAccount.objects.create(username='closure-pass-test-actor')
        self.clock_now = datetime(2026, 11, 25, 4, 0, tzinfo=datetime_timezone.utc)
        self.fixtures = {
            self.first.slug: self.build_fixture(self.first, 'sl.esports.club'),
            self.second.slug: self.build_fixture(self.second, 'xa.esports.club'),
        }
        self.assertEqual(
            self.fixtures[self.first.slug].account.pk,
            self.fixtures[self.second.slug].account.pk,
        )

    def pair_account(self, user, account_id, account_type):
        ClubAccount.objects.create(
            id=account_id, username=user.username, password=user.password,
            nickname=user.nickname, account_type=account_type,
        )
        account = get_or_create_account_for_legacy_user(user)
        self.assertEqual(account.pk, account_id)
        return account

    def build_fixture(self, store, host):
        with store_context(store):
            provider = make_provider(deposit_mode='EARNINGS')
            account = self.pair_account(provider, 930001, ClubAccount.AccountType.PROVIDER)
            profile = provider.escort_profile
            profile.account = account
            profile.save(update_fields=['account'])
            wallet = get_wallet(account=account, user=provider)
            wallet.balance = 100000
            wallet.save(update_fields=['balance'])
            points = MemberProfile.objects.create(account=account, points_balance=30000)
            config()
            save_points_terms(
                group_point_hundredths=10000, group_coin_units=10,
                max_ratio_basis_points=5000, version=0, audit=lambda *_: None,
            )
            client = APIClient(
                HTTP_HOST=host,
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0],
            )
            return SimpleNamespace(
                provider=provider, account=account, profile=profile,
                wallet=wallet, points=points, client=client,
            )

    def f(self, store):
        return self.fixtures[store.slug]

    def close(self, store, *, policy='SA02_NOTICE_30D_V1'):
        return StoreClosureNotice.objects.create(
            store=store, reason_category='OPERATOR_REQUEST', reason='内部闭店秘密原因',
            applicant=self.closure_actor, started_at=self.clock_now,
            notice_policy_version=policy, notice_days=30,
            earliest_settlement_at=self.clock_now + timedelta(days=30),
            client_request_id=f'closure-pass-{store.slug}', request_fingerprint='a' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )

    def pending(self, store):
        notice = StoreClosureNotice.objects.get(store=store)
        StoreClosureEvent.objects.create(
            store=store, notice=notice, kind='NOTICE_EXPIRED', reason='到期确认内部原因',
            actor=self.closure_actor, occurred_at=notice.earliest_settlement_at,
            client_request_id=f'expiry-pass-{store.slug}', request_fingerprint='b' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )

    def buy(self, fixture, request_id, quote=None):
        payload = {'tier': 'BRONZE', 'days': 1, 'client_request_id': request_id}
        if quote is not None:
            payload.update(
                expected_daily_price=quote['daily_price'],
                points_quote_token=quote['quote_token'],
            )
        return fixture.client.post(self.url, payload, format='json')

    def quote(self, fixture):
        response = fixture.client.post(self.url + 'points-quote/', {
            'tier': 'BRONZE', 'selected_point_hundredths': 20000,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def make_refundable(self, store):
        with store_context(store):
            fixture = self.f(store)
            purchase = ProviderPassPurchase.objects.get(account=fixture.account)
            start = timezone.now() + timedelta(hours=2)
            purchase.starts_at = start
            purchase.expires_at = start + timedelta(days=1)
            purchase.save(update_fields=['starts_at', 'expires_at'])
            fixture.profile.pass_tier = purchase.tier
            fixture.profile.pass_expires_at = purchase.expires_at
            fixture.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
            return purchase.pk

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data['code'], 409)
        self.assertEqual(response.data['errorCode'], 'STORE_CLOSURE_RESTRICTED')
        self.assertNotIn('内部闭店秘密原因', str(response.data))

    def assert_replayed(self, response):
        # 既有资金回执恢复使用 HTTP 200 + 信封 code 409。
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['code'], 409)
        self.assertEqual(response.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(response.data['fundResult']['code'], 0)

    def test_balance_purchase_replays_after_closure_while_new_purchase_blocks(self):
        fixture = self.f(self.first)
        created = self.buy(fixture, 'closure-pass-balance-before')
        self.assertEqual(created.data['code'], 0, created.data)
        purchase_id = self.make_refundable(self.first)
        with store_context(self.first):
            fixture.wallet.refresh_from_db()
            balance_after_purchase = fixture.wallet.balance

        self.close(self.first)
        self.assert_replayed(self.buy(fixture, 'closure-pass-balance-before'))
        self.assert_blocked(self.buy(fixture, 'closure-pass-balance-active'))
        with store_context(self.first):
            fixture.wallet.refresh_from_db()
            self.assertEqual(fixture.wallet.balance, balance_after_purchase)
            self.assertEqual(ProviderPassPurchase.objects.count(), 1)

        # 既有购买读取和退款不是新增权益，闭店后继续办理。
        self.assertEqual(fixture.client.get(self.url).status_code, 200)
        refunded = fixture.client.post(
            self.url + f'{purchase_id}/refund/',
            {'client_request_id': 'closure-pass-balance-refund'}, format='json',
        )
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        with store_context(self.first):
            fixture.wallet.refresh_from_db()
            self.assertEqual(fixture.wallet.balance, 100000)
            self.assertEqual(ProviderPassRefund.objects.count(), 1)

        self.pending(self.first)
        self.assert_blocked(self.buy(fixture, 'closure-pass-balance-pending'))
        other = self.f(self.second)
        other_created = self.buy(other, 'closure-pass-balance-other-store')
        self.assertEqual(other_created.data['code'], 0, other_created.data)
        with store_context(self.second):
            self.assertEqual(ProviderPassPurchase.objects.filter(account=other.account).count(), 1)

    def test_points_purchase_replays_and_existing_refund_remains_available(self):
        fixture = self.f(self.first)
        original_quote = self.quote(fixture)
        created = self.buy(fixture, 'closure-pass-points-before', original_quote)
        self.assertEqual(created.data['code'], 0, created.data)
        purchase_id = self.make_refundable(self.first)
        with store_context(self.first):
            fixture.wallet.refresh_from_db()
            fixture.points.refresh_from_db()
            money_snapshot = (fixture.wallet.balance, fixture.points.points_balance)

        self.close(self.first)
        self.assert_replayed(self.buy(fixture, 'closure-pass-points-before', original_quote))
        self.assert_blocked(self.buy(fixture, 'closure-pass-points-active', original_quote))
        with store_context(self.first):
            fixture.wallet.refresh_from_db()
            fixture.points.refresh_from_db()
            self.assertEqual((fixture.wallet.balance, fixture.points.points_balance), money_snapshot)
            self.assertEqual(PointLedger.objects.filter(kind='PASS_POINTS').count(), 1)

        refunded = fixture.client.post(
            self.url + f'{purchase_id}/refund/',
            {'client_request_id': 'closure-pass-points-refund'}, format='json',
        )
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        self.pending(self.first)
        self.assert_blocked(self.buy(fixture, 'closure-pass-points-pending', original_quote))

        other = self.f(self.second)
        other_quote = self.quote(other)
        other_created = self.buy(other, 'closure-pass-points-other-store', other_quote)
        self.assertEqual(other_created.data['code'], 0, other_created.data)
        with store_context(self.second):
            self.assertEqual(ProviderPassPurchase.objects.filter(account=other.account).count(), 1)
            self.assertEqual(PointLedger.objects.filter(kind='PASS_POINTS').count(), 1)

    def test_control_read_failure_and_evidence_review_fail_closed_but_reads_continue(self):
        fixture = self.f(self.first)
        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control read failed')):
            unavailable = self.buy(fixture, 'closure-pass-control-unavailable')
            self.assertEqual(unavailable.status_code, 503, unavailable.content)
            self.assertEqual(unavailable.data['errorCode'], 'STORE_CLOSURE_STATUS_UNAVAILABLE')
            self.assertEqual(fixture.client.get(self.url).status_code, 200)
            self.quote(fixture)

        # 无效公示证据同样只阻断新增，不暴露内部原因。
        self.close(self.first, policy='BROKEN_EVIDENCE')
        self.assert_blocked(self.buy(fixture, 'closure-pass-evidence-review'))
        self.assertEqual(fixture.client.get(self.url).status_code, 200)
        self.quote(fixture)

    def test_original_role_and_balance_rules_are_not_bypassed(self):
        fixture = self.f(self.first)
        with store_context(self.first):
            fixture.wallet.balance = 0
            fixture.wallet.save(update_fields=['balance'])
            boss = make_user()
            boss_account = self.pair_account(boss, 930002, ClubAccount.AccountType.BOSS)
            boss_client = APIClient(
                HTTP_HOST='sl.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0],
            )
        insufficient = self.buy(fixture, 'closure-pass-insufficient')
        self.assertEqual(insufficient.status_code, 200, insufficient.content)
        self.assertEqual(insufficient.data['code'], 400)
        denied = boss_client.post(self.url, {
            'tier': 'BRONZE', 'days': 1, 'client_request_id': 'closure-pass-boss-before',
        }, format='json')
        self.assertEqual(denied.status_code, 200, denied.content)
        self.assertEqual(denied.data['code'], 403)
        self.close(self.first)
        # 角色校验仍在闭店守卫之前，不能借闭店响应绕过原权限语义。
        denied = boss_client.post(self.url, {
            'tier': 'BRONZE', 'days': 1, 'client_request_id': 'closure-pass-boss-after',
        }, format='json')
        self.assertEqual(denied.status_code, 200, denied.content)
        self.assertEqual(denied.data['code'], 403)
        with store_context(self.first):
            self.assertFalse(ProviderPassPurchase.objects.exists())
