"""闭店守卫只接后台新增充值和实际建号 create，不接公共资金或编辑函数。"""
from datetime import datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from engagement.models import RechargePackage
from orders.tests.factories import make_console_user, make_provider, make_user, make_wallet
from stores.closure_clock import CLOCK_SOURCE
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, StoreClosureEvent, StoreClosureNotice
from stores.tests_hosts import xinan_store
from users.models import EscortProfile
from wallet.models import RechargeRecord, Wallet


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
    ALLOWED_HOSTS=['*'],
)
class ClosureConsoleNewActivityTests(APITestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.closure_actor = PlatformAccount.objects.create(username='closure-console-test-actor')
        self.clock_now = datetime(2026, 11, 25, 4, 0, tzinfo=datetime_timezone.utc)
        self.fixtures = {
            self.first.slug: self.build_fixture(self.first),
            self.second.slug: self.build_fixture(self.second),
        }
        for field in ('staff_account', 'boss_account', 'provider_account', 'member_account'):
            self.assertEqual(
                getattr(self.fixtures[self.first.slug], field).pk,
                getattr(self.fixtures[self.second.slug], field).pk,
            )

    def pair_account(self, user, account_id, account_type):
        ClubAccount.objects.create(
            id=account_id, username=user.username, password=user.password,
            nickname=user.nickname, account_type=account_type,
        )
        account = get_or_create_account_for_legacy_user(user)
        self.assertEqual(account.pk, account_id)
        return account

    def client_for(self, account):
        return APIClient(
            HTTP_HOST='admin.esports.club',
            HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0],
        )

    def build_fixture(self, store):
        with store_context(store):
            staff = make_console_user([
                'wallet:view', 'wallet:recharge', 'recharge:view',
                'user:view', 'user:edit', 'escort:view', 'escort:edit',
                'admin:view', 'admin:edit',
            ])
            staff_account = self.pair_account(staff, 920001, ClubAccount.AccountType.STAFF)
            staff.admin_membership.account = staff_account
            staff.admin_membership.save(update_fields=['account'])

            boss = make_user()
            boss_account = self.pair_account(boss, 920002, ClubAccount.AccountType.BOSS)
            make_wallet(boss, balance=0)
            Wallet.objects.filter(user=boss).update(account=boss_account)

            provider = make_provider(deposit_mode='EARNINGS')
            provider_account = self.pair_account(provider, 920003, ClubAccount.AccountType.PROVIDER)
            provider.escort_profile.account = provider_account
            provider.escort_profile.save(update_fields=['account'])
            Wallet.objects.filter(user=provider).update(account=provider_account)

            member = make_console_user(['order:view'])
            member_account = self.pair_account(member, 920004, ClubAccount.AccountType.STAFF)
            member.admin_membership.account = member_account
            member.admin_membership.save(update_fields=['account'])
            role = AdminRole.objects.create(
                code='closure-new-staff-role', name='闭店测试新员工角色', permissions=['order:view'],
            )
            other_role = AdminRole.objects.create(
                code='closure-other-staff-role', name='闭店测试调整角色', permissions=['user:view'],
            )
            RechargePackage.objects.create(amount=10000, gift_amount=0, active=True)
            return SimpleNamespace(
                staff=staff, staff_account=staff_account, client=self.client_for(staff_account),
                boss=boss, boss_account=boss_account,
                provider=provider, provider_account=provider_account,
                profile=provider.escort_profile,
                member=member, member_account=member_account,
                membership=member.admin_membership, member_client=self.client_for(member_account),
                role=role, other_role=other_role,
            )

    def f(self, store):
        return self.fixtures[store.slug]

    def close(self, store, *, policy='SA02_NOTICE_30D_V1'):
        return StoreClosureNotice.objects.create(
            store=store, reason_category='OPERATOR_REQUEST', reason='内部闭店秘密原因',
            applicant=self.closure_actor, started_at=self.clock_now,
            notice_policy_version=policy, notice_days=30,
            earliest_settlement_at=self.clock_now + timedelta(days=30),
            client_request_id=f'closure-console-{store.slug}', request_fingerprint='a' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )

    def pending(self, store):
        notice = StoreClosureNotice.objects.get(store=store)
        StoreClosureEvent.objects.create(
            store=store, notice=notice, kind='NOTICE_EXPIRED', reason='到期确认内部原因',
            actor=self.closure_actor, occurred_at=notice.earliest_settlement_at,
            client_request_id=f'expiry-console-{store.slug}', request_fingerprint='b' * 64,
            clock_source=CLOCK_SOURCE, clock_identity='sqlite-test:default',
        )

    def recharge_payload(self, fixture, label, **overrides):
        payload = {
            'account_id': fixture.boss_account.pk, 'amount': 10000, 'gift_amount': 0,
            'trade_no': f'closure-recharge-{label}', 'client_request_id': f'closure-recharge-{label}',
        }
        payload.update(overrides)
        return payload

    def recharge(self, fixture, label, **overrides):
        return fixture.client.post(
            '/api/admin/wallets/recharge/', self.recharge_payload(fixture, label, **overrides),
            format='json',
        )

    def provider_payload(self, label):
        return {
            'username': f'closure-provider-{label}', 'password': 'pw12345678',
            'phone': '13800001111', 'display_name': '闭店测试陪玩', 'city': '广州',
        }

    def create_provider(self, fixture, label):
        return fixture.client.post('/api/admin/escorts/', self.provider_payload(label), format='json')

    def admin_payload(self, fixture, label):
        return {
            'username': f'closure-staff-{label}', 'password': 'pass1234',
            'phone': '13800002222', 'nickname': '闭店测试员工', 'roles': [fixture.role.pk],
        }

    def create_admin(self, fixture, label):
        return fixture.client.post('/api/admin/admins/', self.admin_payload(fixture, label), format='json')

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data['code'], 409)
        self.assertEqual(response.data['errorCode'], 'STORE_CLOSURE_RESTRICTED')
        self.assertNotIn('内部闭店秘密原因', str(response.data))

    def assert_unavailable(self, response):
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.data['code'], 503)
        self.assertEqual(response.data['errorCode'], 'STORE_CLOSURE_STATUS_UNAVAILABLE')

    def test_recharge_replays_original_and_blocks_only_new_writes(self):
        fixture = self.f(self.first)
        original_payload = self.recharge_payload(fixture, 'before')
        created = fixture.client.post('/api/admin/wallets/recharge/', original_payload, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        with store_context(self.first):
            wallet = Wallet.objects.get(account=fixture.boss_account)
            self.assertEqual(wallet.balance, 10000)

        self.close(self.first)
        replay = fixture.client.post('/api/admin/wallets/recharge/', original_payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.data['code'], 0, replay.data)
        self.assertTrue(replay.data['data']['replayed'])
        self.assert_blocked(self.recharge(fixture, 'active'))
        self.pending(self.first)
        self.assert_blocked(self.recharge(fixture, 'pending'))
        with store_context(self.first):
            self.assertEqual(RechargeRecord.objects.count(), 1)
            self.assertEqual(Wallet.objects.get(account=fixture.boss_account).balance, 10000)

        # 读取、报价及原权限均不受新增守卫影响。
        self.assertEqual(fixture.client.get('/api/admin/recharge-records/').data['code'], 0)
        self.assertEqual(fixture.client.get('/api/admin/wallets/recharge-quote/', {'amount': 10000}).data['code'], 0)
        forbidden = fixture.member_client.post(
            '/api/admin/wallets/recharge/', self.recharge_payload(fixture, 'forbidden'), format='json',
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.content)

        other = self.f(self.second)
        other_created = self.recharge(other, 'other-store')
        self.assertEqual(other_created.data['code'], 0, other_created.data)
        with store_context(self.second):
            self.assertEqual(RechargeRecord.objects.count(), 1)
            self.assertEqual(Wallet.objects.get(account=other.boss_account).balance, 10000)

    def test_provider_create_blocks_active_and_pending_but_edit_read_continue(self):
        fixture = self.f(self.first)
        created = self.create_provider(fixture, 'before')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data['code'], 0, created.data)

        self.close(self.first)
        self.assert_blocked(self.create_provider(fixture, 'active'))
        edited = fixture.client.patch(
            f'/api/admin/escorts/{fixture.profile.pk}/', {'city': '闭店后仍可编辑'}, format='json',
        )
        self.assertEqual(edited.data['code'], 0, edited.data)
        self.assertEqual(fixture.client.get(f'/api/admin/escorts/{fixture.profile.pk}/').data['code'], 0)
        with store_context(self.first):
            fixture.profile.refresh_from_db()
            self.assertEqual(fixture.profile.city, '闭店后仍可编辑')
            self.assertFalse(ClubAccount.objects.filter(username='closure-provider-active').exists())

        self.pending(self.first)
        self.assert_blocked(self.create_provider(fixture, 'pending'))
        other = self.f(self.second)
        other_created = self.create_provider(other, 'other-store')
        self.assertEqual(other_created.status_code, 201, other_created.content)
        self.assertEqual(other_created.data['code'], 0, other_created.data)

    def test_admin_create_blocks_active_and_pending_but_edit_role_disable_read_continue(self):
        fixture = self.f(self.first)
        created = self.create_admin(fixture, 'before')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data['code'], 0, created.data)

        self.close(self.first)
        self.assert_blocked(self.create_admin(fixture, 'active'))
        edited = fixture.client.patch(f'/api/admin/admins/{fixture.membership.pk}/', {
            'nickname': '闭店后调整成员', 'roles': [fixture.other_role.pk], 'is_active': False,
        }, format='json')
        self.assertEqual(edited.data['code'], 0, edited.data)
        self.assertEqual(fixture.client.get(f'/api/admin/admins/{fixture.membership.pk}/').data['code'], 0)
        with store_context(self.first):
            fixture.membership.refresh_from_db()
            fixture.member.refresh_from_db()
            self.assertEqual(fixture.member.nickname, '闭店后调整成员')
            self.assertFalse(fixture.membership.is_active)
            self.assertEqual(
                list(fixture.membership.roles.values_list('pk', flat=True)), [fixture.other_role.pk],
            )
            self.assertFalse(ClubAccount.objects.filter(username='closure-staff-active').exists())

        self.pending(self.first)
        self.assert_blocked(self.create_admin(fixture, 'pending'))
        other = self.f(self.second)
        other_created = self.create_admin(other, 'other-store')
        self.assertEqual(other_created.status_code, 201, other_created.content)
        self.assertEqual(other_created.data['code'], 0, other_created.data)

    def test_control_failure_is_503_for_every_new_entry_without_changing_reads_or_validation(self):
        fixture = self.f(self.first)
        # 未闭店时原有金额和充值对象校验照常执行。
        zero_amount = self.recharge(fixture, 'zero', amount=0)
        self.assertEqual(zero_amount.status_code, 200, zero_amount.content)
        self.assertEqual(zero_amount.data['code'], 400)
        original_target = self.recharge_payload(fixture, 'provider-target', account_id=fixture.provider_account.pk)
        self.assertEqual(fixture.client.post(
            '/api/admin/wallets/recharge/', original_target, format='json',
        ).status_code, 403)

        with patch('stores.closure.new_activity_allowed', side_effect=DatabaseError('control read failed')):
            self.assert_unavailable(self.recharge(fixture, 'unavailable'))
            self.assert_unavailable(self.create_provider(fixture, 'unavailable'))
            self.assert_unavailable(self.create_admin(fixture, 'unavailable'))
            self.assertEqual(fixture.client.get('/api/admin/escorts/').status_code, 200)
            self.assertEqual(fixture.client.get('/api/admin/admins/').status_code, 200)
            self.assertEqual(fixture.client.get('/api/admin/wallets/').status_code, 200)
        with store_context(self.first):
            self.assertFalse(RechargeRecord.objects.exists())
            self.assertFalse(ClubAccount.objects.filter(username='closure-provider-unavailable').exists())
            self.assertFalse(ClubAccount.objects.filter(username='closure-staff-unavailable').exists())

    def test_evidence_review_is_same_409_for_every_new_entry(self):
        fixture = self.f(self.first)
        self.close(self.first, policy='BROKEN_EVIDENCE')
        self.assert_blocked(self.recharge(fixture, 'evidence'))
        self.assert_blocked(self.create_provider(fixture, 'evidence'))
        self.assert_blocked(self.create_admin(fixture, 'evidence'))
        with store_context(self.first):
            self.assertFalse(RechargeRecord.objects.exists())
            self.assertFalse(ClubAccount.objects.filter(username='closure-provider-evidence').exists())
            self.assertFalse(ClubAccount.objects.filter(username='closure-staff-evidence').exists())

    def test_provider_create_rechecks_closure_inside_business_transaction(self):
        fixture = self.f(self.first)
        from console.closure_recharge_accounts import account_create_closure_refusal as actual
        calls = []

        def close_before_locked_write():
            calls.append(1)
            if len(calls) == 2:
                self.close(self.first)
            return actual()

        with patch('console.closure_recharge_accounts.account_create_closure_refusal',
                   side_effect=close_before_locked_write):
            response = self.create_provider(fixture, 'lock-race')
        self.assert_blocked(response)
        self.assertEqual(len(calls), 2)
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.filter(username='closure-provider-lock-race').exists())
            self.assertFalse(EscortProfile.objects.filter(user__username='closure-provider-lock-race').exists())

    def test_admin_create_rechecks_closure_inside_business_transaction(self):
        fixture = self.f(self.first)
        from console.closure_recharge_accounts import account_create_closure_refusal as actual
        calls = []

        def close_before_locked_write():
            calls.append(1)
            if len(calls) == 2:
                self.close(self.first)
            return actual()

        with patch('console.closure_recharge_accounts.account_create_closure_refusal',
                   side_effect=close_before_locked_write):
            response = self.create_admin(fixture, 'lock-race')
        self.assert_blocked(response)
        self.assertEqual(len(calls), 2)
        with store_context(self.first):
            self.assertFalse(ClubAccount.objects.filter(username='closure-staff-lock-race').exists())
            self.assertFalse(AdminMembership.objects.filter(user__username='closure-staff-lock-race').exists())

    def test_boss_create_remains_disabled_and_existing_boss_edit_read_continue(self):
        fixture = self.f(self.first)
        payload = {'username': 'closure-boss-not-supported', 'password': 'pass1234'}
        self.assertEqual(fixture.client.post('/api/admin/users/', payload, format='json').status_code, 405)
        self.close(self.first)
        self.assertEqual(fixture.client.post('/api/admin/users/', payload, format='json').status_code, 405)
        edited = fixture.client.patch(f'/api/admin/users/{fixture.boss_account.pk}/', {
            'nickname': '闭店后老板资料可编辑',
        }, format='json')
        self.assertEqual(edited.data['code'], 0, edited.data)
        self.assertEqual(fixture.client.get(f'/api/admin/users/{fixture.boss_account.pk}/').data['code'], 0)
        with store_context(self.first):
            fixture.boss_account.refresh_from_db()
            self.assertEqual(fixture.boss_account.nickname, '闭店后老板资料可编辑')
            self.assertFalse(ClubAccount.objects.filter(username='closure-boss-not-supported').exists())
