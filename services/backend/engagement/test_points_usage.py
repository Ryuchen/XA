from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from engagement.models import (LotteryCampaign, LotteryDraw, LotteryPrize, MemberProfile, OrderPointAllocation,
                               PointFundingLot, PointLedger, RewardClaim, RewardItem)
from engagement.points_usage import CONFIG_KEY, current_usage
from engagement.services import award_order_points, config, process_claim
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig

URL = '/api/admin/config/points-usage/'


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PointsUsageTests(APITestCase):
    def setUp(self):
        user = make_user(); self.user = user; self.account = get_or_create_account_for_legacy_user(user)
        self.profile = MemberProfile.objects.create(account=self.account, points_balance=100000)
        staff = make_console_user(['dictionary:view', 'dictionary:edit', 'engagement:edit'])
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        self.admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        self.operation = config()
        self.reward = RewardItem.objects.create(code='switch-shop', name='实物', kind='PHYSICAL',
                                               audience='ALL', in_shop=True, is_active=True, point_cost=10, stock=20)
        lottery_reward = RewardItem.objects.create(code='switch-lottery', name='中奖实物', kind='PHYSICAL',
                                                  audience='BOSS', in_shop=False, is_active=True, stock=20)
        self.campaign = LotteryCampaign.objects.create(name='开关奖池', point_cost=10, enabled=True)
        LotteryPrize.objects.create(campaign=self.campaign, reward=lottery_reward, probability=10000)

    def redeem(self, key):
        return self.client.post('/api/club/redeem/', {'reward_id': self.reward.pk, 'client_request_id': key}, format='json')

    def draw(self, key):
        return self.client.post('/api/club/lottery/', {'campaign_id': self.campaign.pk,
            'version': self.campaign.version, 'client_request_id': key, 'count': 1}, format='json')

    def change(self, **values):
        current = self.admin.get(URL).data['data']
        body = {field: current[field] for field in ('redemption_enabled', 'shop_enabled', 'lottery_enabled', 'version', 'issuance_enabled')}
        body['expected_issuance_enabled'] = current['issuance_enabled']
        return self.admin.put(URL, {**body, **values}, format='json')

    def test_any_disabled_switch_pauses_both_uses_and_successful_retries_still_work(self):
        self.assertTrue(settings.POINTS_USAGE_SWITCHES_ENABLED)
        initial_redeem = self.redeem('switch-redeem-existing')
        initial_draw = self.draw('switch-draw-existing')
        self.assertEqual(initial_redeem.data['code'], 0, initial_redeem.data)
        self.assertEqual(initial_draw.data['code'], 0, initial_draw.data)
        counts = (RewardClaim.objects.count(), LotteryDraw.objects.count(), PointLedger.objects.count())
        self.profile.refresh_from_db(); balance = self.profile.points_balance
        for field in ('redemption_enabled', 'shop_enabled', 'lottery_enabled'):
            with self.subTest(field=field):
                self.assertEqual(self.change(**{field: False}).status_code, 200)
                self.assertEqual(self.redeem('denied-redeem-' + field).status_code, 400)
                self.assertEqual(self.draw('denied-draw-' + field).status_code, 400)
                self.assertEqual(self.redeem('switch-redeem-existing').data['data']['id'], initial_redeem.data['data']['id'])
                self.assertEqual(self.draw('switch-draw-existing').data['data']['id'], initial_draw.data['data']['id'])
                self.assertEqual(counts, (RewardClaim.objects.count(), LotteryDraw.objects.count(), PointLedger.objects.count()))
                self.profile.refresh_from_db(); self.assertEqual(self.profile.points_balance, balance)
                self.assertEqual(self.change(**{field: True}).status_code, 200)
        self.assertEqual(self.admin.put('/api/admin/club-config/', {'points_enabled': False}, format='json').status_code, 200)
        self.assertEqual(self.redeem('issuance-off-redemption').status_code, 400)
        self.assertEqual(self.draw('issuance-off-draw').status_code, 400)
        self.assertEqual(self.admin.put('/api/admin/club-config/', {'points_enabled': True}, format='json').status_code, 200)
        self.assertEqual(self.redeem('switch-redeem-resumed').data['code'], 0)
        self.assertEqual(self.draw('switch-draw-resumed').data['code'], 0)

    def test_claim_cancellation_refunds_points_while_usage_is_paused(self):
        claim = self.redeem('return-before-pause').data['data']
        self.profile.refresh_from_db(); self.assertEqual(self.profile.points_balance, 99000)
        self.assertEqual(self.change(shop_enabled=False).status_code, 200)
        process_claim(claim['id'], 'CANCELLED', '核对后退回', self.account)
        self.profile.refresh_from_db(); self.assertEqual(self.profile.points_balance, 100000)
        self.assertEqual(PointLedger.objects.filter(account=self.account, kind='RETURN').count(), 1)

    def test_provider_shop_redemption_obeys_same_pause(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        MemberProfile.objects.create(account=account, points_balance=10000)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(self.change(lottery_enabled=False).status_code, 200)
        result = client.post('/api/club/redeem/', {'reward_id': self.reward.pk,
                             'client_request_id': 'provider-paused-redemption'}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(MemberProfile.objects.get(account=account).points_balance, 10000)
        self.assertFalse(RewardClaim.objects.filter(account=account).exists())
        self.assertEqual(self.change(lottery_enabled=True).status_code, 200)
        self.assertEqual(client.post('/api/club/redeem/', {'reward_id': self.reward.pk,
                         'client_request_id': 'provider-paused-redemption'}, format='json').data['code'], 0)

    def test_disabling_issuance_stops_legacy_allocated_order_points(self):
        order = make_order(self.user, make_service(price=1000), customer_account=self.account)
        lot = PointFundingLot.objects.create(account=self.account, source_key='switch-legacy', remaining=0)
        OrderPointAllocation.objects.create(order=order, lot=lot, amount=1000, points_amount=1500)
        self.operation.points_enabled = False; self.operation.save(update_fields=['points_enabled'])
        with transaction.atomic():
            award_order_points(order)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 100000)
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())

    def test_permission_invalid_version_and_audit_failure(self):
        buyer = self.client
        self.assertEqual(buyer.get(URL).status_code, 403)
        self.assertEqual(self.change(shop_enabled=False).status_code, 200)
        self.assertEqual(self.admin.put(URL, {'redemption_enabled': True, 'shop_enabled': True,
            'lottery_enabled': True, 'version': 0, 'issuance_enabled': True,
            'expected_issuance_enabled': True}, format='json').status_code, 409)
        before = current_usage()[1]
        with patch('console.points_usage.AdminAuditLog.objects.create', side_effect=DatabaseError('audit')):
            with self.assertRaises(DatabaseError):
                self.change(shop_enabled=True)
        self.assertEqual(current_usage()[1], before)
        self.assertEqual(AdminAuditLog.objects.filter(resource='points-usage-switches').count(), 1)

    def test_issuance_edit_requires_permission_and_detects_other_editor(self):
        user = make_console_user(['dictionary:edit'])
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account; user.admin_membership.save(update_fields=['account'])
        limited = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        body = {'redemption_enabled': True, 'shop_enabled': True, 'lottery_enabled': True,
                'version': 0, 'issuance_enabled': False, 'expected_issuance_enabled': True}
        self.assertEqual(limited.put(URL, body, format='json').status_code, 403)
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        self.assertEqual(self.change(issuance_enabled=False).status_code, 200)
        stale = {**body, 'version': 1, 'issuance_enabled': True}
        self.assertEqual(self.admin.put(URL, stale, format='json').status_code, 409)
        self.assertFalse(config().points_enabled)

    def test_corrupt_config_fails_closed_but_reading_existing_claim_remains_possible(self):
        claim = self.redeem('preexisting-claim').data['data']
        SystemConfig.objects.update_or_create(key=CONFIG_KEY, defaults={'value': 'bad'})
        self.assertEqual(self.redeem('corrupt-redeem').status_code, 400)
        self.assertEqual(self.draw('corrupt-draw').status_code, 400)
        self.assertEqual(self.client.get(f"/api/club/claims/{claim['id']}/").status_code, 200)


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsUsageStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @classmethod
    def setUpTestData(cls):
        from django.utils import timezone
        from stores.context import default_store_slug
        from stores.models import Store
        Store.objects.get_or_create(slug=default_store_slug(), defaults={
            'name': '隔离测试门店', 'database_alias': 'default', 'status': Store.Status.ACTIVE,
            'initialized_at': timezone.now()})

    def test_pausing_one_store_does_not_pause_another(self):
        from engagement.points_usage import assert_usage_allowed, lock_usage
        from rest_framework.exceptions import ValidationError
        first, second = resolve_store(), xinan_store()
        with store_context(first), transaction.atomic():
            _, row, values = lock_usage()
            row.value = '{"redemption_enabled":true,"shop_enabled":false,"lottery_enabled":true,"version":1}'
            row.save(update_fields=['value'])
            with self.assertRaises(ValidationError):
                assert_usage_allowed()
        with store_context(second), transaction.atomic():
            assert_usage_allowed()
            self.assertTrue(current_usage()[1]['shop_enabled'])

    @override_settings(POINTS_USAGE_SWITCHES_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_saved_pause_is_enforced_when_switch_interface_disabled(self):
        import json
        from engagement.points_usage import lock_usage
        from wallet.services import get_wallet
        stores = ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club'))
        clients, accounts, rewards, campaigns = [], [], [], []
        for store, host in stores:
            with store_context(store):
                account = get_or_create_account_for_legacy_user(make_user())
                MemberProfile.objects.create(account=account, points_balance=10000)
                reward = RewardItem.objects.create(code='pause-goods', name='实物', kind='PHYSICAL',
                    audience='ALL', in_shop=True, is_active=True, point_cost=10, stock=5)
                prize = RewardItem.objects.create(code='pause-balance', name='抽奖余额', kind='BALANCE',
                    audience='BOSS', in_shop=False, is_active=True, balance_amount=50, stock=5)
                campaign = LotteryCampaign.objects.create(name='余额抽奖', point_cost=10, enabled=True)
                LotteryPrize.objects.create(campaign=campaign, reward=prize, probability=10000)
                clients.append(APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
                accounts.append(account); rewards.append(reward); campaigns.append(campaign)
        with store_context(stores[0][0]), transaction.atomic():
            _, row, switches = lock_usage()
            switches['shop_enabled'] = False
            row.value = json.dumps(switches); row.save(update_fields=['value'])
        for i, (store, _) in enumerate(stores):
            with store_context(store):
                redeem = clients[i].post('/api/club/redeem/', {'reward_id': rewards[i].pk,
                    'client_request_id': 'paused-api-redeem'}, format='json')
                draw = clients[i].post('/api/club/lottery/', {'campaign_id': campaigns[i].pk,
                    'version': campaigns[i].version, 'client_request_id': 'paused-api-draw', 'count': 1}, format='json')
                self.assertEqual((redeem.status_code, draw.status_code), (400, 400) if i == 0 else (200, 200))
                self.assertEqual(MemberProfile.objects.get(account=accounts[i]).points_balance, 10000 if i == 0 else 8000)
                wallet = get_wallet(account=accounts[i])
                self.assertEqual((wallet.balance, wallet.total_gift), (0, 0) if i == 0 else (50, 50))
        with store_context(stores[0][0]), transaction.atomic():
            _, row, switches = lock_usage()
            switches['shop_enabled'] = True
            row.value = json.dumps(switches); row.save(update_fields=['value'])
        with store_context(stores[0][0]):
            result = clients[0].post('/api/club/redeem/', {'reward_id': rewards[0].pk,
                'client_request_id': 'resumed-api-redeem'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)

    def test_paused_shop_does_not_block_other_store_redemption(self):
        from engagement.points_usage import lock_usage
        from engagement.services import redeem
        from rest_framework.exceptions import ValidationError
        first, second = resolve_store(), xinan_store()
        accounts, rewards = [], []
        for store in (first, second):
            with store_context(store):
                account = get_or_create_account_for_legacy_user(make_user())
                MemberProfile.objects.create(account=account, points_balance=5000)
                reward = RewardItem.objects.create(code='isolated-shop', name='本店权益', kind='SERVICE',
                    point_cost=10, stock=1, in_shop=True, is_active=True)
                accounts.append(account); rewards.append(reward)
        with store_context(first), transaction.atomic():
            _, row, switches = lock_usage()
            import json
            switches['shop_enabled'] = False
            row.value = json.dumps(switches); row.save(update_fields=['value'])
        with store_context(first):
            with self.assertRaises(ValidationError):
                redeem(accounts[0], rewards[0].pk, 'isolation-redemption')
            self.assertEqual(MemberProfile.objects.get(account=accounts[0]).points_balance, 5000)
        with store_context(second):
            claim = redeem(accounts[1], rewards[1].pk, 'isolation-redemption')
            self.assertEqual(claim.account_id, accounts[1].pk)
            self.assertEqual(MemberProfile.objects.get(account=accounts[1]).points_balance, 4000)
        with store_context(first), transaction.atomic():
            _, row, switches = lock_usage()
            switches['shop_enabled'] = True
            row.value = json.dumps(switches); row.save(update_fields=['value'])
        with store_context(first):
            redeem(accounts[0], rewards[0].pk, 'isolation-redemption')
            self.assertEqual(MemberProfile.objects.get(account=accounts[0]).points_balance, 4000)
