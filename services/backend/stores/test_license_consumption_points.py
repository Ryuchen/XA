import os
from unittest import skipUnless
from datetime import date, timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation, LicenseConsumptionPointsDecision, PointLedger
from engagement.services import award_order_points
from orders.models import Order
from orders.tests.test_service_funding_api import ServiceFundingApiTest
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.license_codes import _activate_after_verified_store_auth, issue
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_foundation import admin
from stores.tests_hosts import xinan_store


@override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LICENSE_NOTICE_DISPATCH_ENABLED=False, PLATFORM_IDENTITY_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class LicenseConsumptionPointsTests(TestCase):
    databases = {'default', 'store_b'}
    payload = ServiceFundingApiTest.payload
    mature = ServiceFundingApiTest.mature

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        actor = PlatformAccount.objects.create(username='points-license-owner', phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        raw, _ = issue_tokens(actor, 'SMS', actor.phone)
        self.platform = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + raw)
        with store_context(self.second):
            owner = admin('points-license-admin', super_admin=True)
        now = timezone.now()
        self.license, code = issue(AccessToken(raw), store_slug=self.second.slug, tier='PRO', kind='INITIAL',
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=30), backdate_reason='隔离测试')
        _activate_after_verified_store_auth(store=self.second, actor_id=owner.pk, code=code)
        with store_context(self.second):
            ServiceFundingApiTest.setUp(self)
            self.boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])

    def create_order(self):
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=200,
            compensation_balance_amount=100), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return Order.objects.get(pk=response.data['data']['id'])

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_kook_automatic_points_stop_after_grace_without_backfilling_expired_time(self):
        from engagement.kook_points import checkpoint, process_event
        from engagement.models import KookBinding, KookPointsConfig, MemberProfile
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_user
        now = timezone.now()
        with store_context(self.second):
            other = get_or_create_account_for_legacy_user(make_user())
            KookPointsConfig.objects.create(enabled=True, guild_id='guild', checkin_channel_id='hall',
                voice_channel_ids=['voice'], started_at=now - timedelta(minutes=1))
            binding = KookBinding.objects.create(account=self.account, user_id='first', channel_id='voice',
                accounted_at=now, online_milliseconds=7190000)
            KookBinding.objects.create(account=other, user_id='second')
            def message(user, event_id):
                return {'type': 1, 'channel_type': 'GROUP', 'target_id': 'hall', 'author_id': user,
                    'msg_id': event_id, 'msg_timestamp': int(now.timestamp() * 1000), 'content': '签到',
                    'extra': {'guild_id': 'guild', 'author': {'username': user, 'bot': False}}}
            self.end_license_days_ago(1)
            self.assertTrue(process_event(message('first', 'grace-checkin'), now))
            checkpoint(now + timedelta(seconds=30))
            profile = MemberProfile.objects.get(account=self.account)
            self.assertEqual(profile.points_balance, 600)

            self.end_license_days_ago(8)
            self.assertFalse(process_event(message('second', 'expired-checkin'), now))
            checkpoint(now + timedelta(seconds=60))
            binding.refresh_from_db()
            self.assertEqual(binding.online_milliseconds, 7220000)
            profile.refresh_from_db()
            self.assertEqual(profile.points_balance, 600)
            self.assertFalse(MemberProfile.objects.filter(account=other).exists())

            self.license.ends_at = timezone.now() + timedelta(days=30)
            self.license.save(update_fields=['ends_at'])
            checkpoint(now + timedelta(seconds=90))
            binding.refresh_from_db()
            self.assertEqual(binding.online_milliseconds, 7250000)
            self.assertFalse(process_event(message('second', 'expired-checkin'), now))

    def test_kook_manual_existing_retry_survives_expiry_but_new_award_is_denied(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from console.models import AdminRole
        from orders.tests.factories import make_user
        with store_context(self.second):
            other = get_or_create_account_for_legacy_user(make_user())
            staff_account = admin('kook-manual-license-staff')
            AdminRole.objects.filter(code=f'foundation_{staff_account.pk}').update(permissions=['engagement:points'])
            staff_account.is_phone_verified = True
            staff_account.save(update_fields=['is_phone_verified'])
            token, _ = issue_account_tokens(staff_account, auth_method='password_sms')
        staff = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        body = {'account_id': self.account.pk, 'kind': 'KOOK_CHECKIN',
                'day': timezone.localdate().isoformat(), 'source_id': 'manual-grace'}
        self.end_license_days_ago(1)
        first = staff.post('/api/admin/external-points/', body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.end_license_days_ago(8)
        retry = staff.post('/api/admin/external-points/', body, format='json')
        self.assertEqual(retry.data['data']['id'], first.data['data']['id'])
        denied = staff.post('/api/admin/external-points/', {**body, 'account_id': other.pk}, format='json')
        self.assertEqual(denied.status_code, 400, denied.data)
        with store_context(self.second):
            self.assertEqual(PointLedger.objects.filter(kind='KOOK_CHECKIN').count(), 1)

    def end_license_days_ago(self, days):
        self.license.starts_at = timezone.now() - timedelta(days=30)
        self.license.activated_at = timezone.now() - timedelta(days=29)
        self.license.ends_at = timezone.now() - timedelta(days=days)
        self.license.save(update_fields=['starts_at', 'activated_at', 'ends_at'])

    @patch('users.views._daily_paid_amount', return_value=38800)
    @patch('users.views.timezone.localdate', return_value=date(2026, 7, 15))
    def test_checkin_expiry_preserves_earned_makeup_but_stops_new_rewards(self, _date, _spend):
        from users.models import CheckinMonthProgress, CheckinRecord
        from engagement.services import wallet_funding_parts
        from wallet.services import get_wallet
        with store_context(self.second):
            progress = CheckinMonthProgress.objects.create(
                account=self.account, user=self.user, year=2026, month=7, makeup_cards=1)
            gift_before = wallet_funding_parts(get_wallet(account=self.account, user=self.user))['gift_balance']
            self.end_license_days_ago(8)
            read = self.boss.get('/api/users/checkin/')
            self.assertEqual(read.data['data']['makeup_cards'], 1, read.data)
            denied = self.boss.post('/api/users/checkin/', {}, format='json')
            self.assertEqual(denied.data['code'], 403, denied.data)
            makeup = self.boss.post('/api/users/checkin/', {'day': 8}, format='json')
            self.assertEqual(makeup.data['code'], 0, makeup.data)
            progress.refresh_from_db()
            self.assertEqual(progress.makeup_cards, 0)
            self.assertTrue(CheckinRecord.objects.filter(account=self.account, checkin_date=date(2026, 7, 8)).exists())
            wallet = get_wallet(account=self.account, user=self.user)
            self.assertEqual(wallet_funding_parts(wallet)['gift_balance'], gift_before + makeup.data['data']['reward_amount'])
            self.license.ends_at = timezone.now() + timedelta(days=30)
            self.license.save(update_fields=['ends_at'])
            self.assertEqual(self.boss.get('/api/users/checkin/').data['data']['makeup_cards'], 1)

    def test_boss_can_choose_between_open_campaigns_with_store_isolation(self):
        from engagement.models import LotteryCampaign, LotteryPrize, MemberProfile, RewardItem
        self.license.tier = 'ULTRA'
        self.license.save(update_fields=['tier'])
        with store_context(self.first):
            other = LotteryCampaign.objects.create(pk=900001, name='其他门店活动', enabled=True, point_cost=10)
        with store_context(self.second):
            first = LotteryCampaign.objects.create(name='本店活动甲', enabled=True, point_cost=10)
            second = LotteryCampaign.objects.create(name='本店活动乙', enabled=True, point_cost=20)
            future = LotteryCampaign.objects.create(name='未开始', enabled=True, point_cost=10,
                                                    starts_at=timezone.now() + timedelta(days=1))
            LotteryCampaign.objects.create(name='已结束', enabled=True, point_cost=10,
                                           ends_at=timezone.now() - timedelta(seconds=1))
            LotteryCampaign.objects.create(name='已停用', enabled=False, point_cost=10)
            reward = RewardItem.objects.create(code='multi-campaign-prize', name='抽奖商品',
                                               kind='PHYSICAL', audience='BOSS', is_active=True, stock=10)
            LotteryPrize.objects.create(campaign=first, reward=reward, probability=10000)
            LotteryPrize.objects.create(campaign=second, reward=reward, probability=10000)
            MemberProfile.objects.update_or_create(account=self.account, defaults={'points_balance': 100000})
            response = self.boss.get('/api/club/lottery/campaigns/', {'page_size': 1})
            self.assertEqual([r['id'] for r in response.data['data']['items']], [second.pk])
            self.assertEqual(self.boss.get('/api/club/lottery/').data['data']['id'], second.pk)
            page = self.boss.get('/api/club/lottery/campaigns/', {'page_size': 1, 'before': response.data['data']['next']})
            self.assertEqual([r['id'] for r in page.data['data']['items']], [first.pk])
            self.assertIsNone(page.data['data']['next'])
            self.assertEqual(self.boss.get('/api/club/lottery/', {'campaign_id': first.pk}).data['data']['name'], first.name)
            self.assertEqual(self.boss.get('/api/club/lottery/', {'campaign_id': other.pk}).status_code, 404)
            self.assertEqual(self.boss.get('/api/club/lottery/', {'campaign_id': future.pk}).status_code, 404)
            self.assertEqual(self.boss.get('/api/club/lottery/campaigns/', {'page_size': 0}).status_code, 400)
            for campaign in (first, second):
                result = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk,
                    'version': campaign.version, 'client_request_id': f'choose-{campaign.pk}', 'count': 1}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)

    def test_campaign_pity_settings_reset_only_when_limit_changes(self):
        from engagement.models import LotteryCampaign, LotteryPrize, LotteryPityState, MemberProfile, RewardItem
        self.license.tier = 'ULTRA'
        self.license.save(update_fields=['tier'])
        with store_context(self.second):
            staff = admin('pity-settings-owner', super_admin=True)
            from console.models import AdminRole
            role = AdminRole.objects.get(code=f'foundation_{staff.pk}')
            role.permissions = ['engagement:view', 'engagement:edit']
            role.save(update_fields=['permissions'])
            staff_client = APIClient(HTTP_HOST='admin.esports.club',
                                     HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            campaign = LotteryCampaign.objects.create(name='配置保底', point_cost=10)
            first = RewardItem.objects.create(code='pity-alpha', name='目标甲', kind='PHYSICAL',
                                              audience='BOSS', is_active=True, stock=100)
            second = RewardItem.objects.create(code='pity-beta', name='目标乙', kind='PHYSICAL',
                                               audience='BOSS', is_active=True, stock=100)
            rows = [{'reward': first.pk, 'probability': 1}, {'reward': second.pk, 'probability': 9999}]
            path = f'/api/admin/lotteries/{campaign.pk}/configure/'
            response = staff_client.post(path, {'version': campaign.version, 'enabled': True, 'pity_limit': 3,
                'pity_target_reward': first.pk, 'prizes': rows}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            campaign.refresh_from_db()
            original_version = campaign.version
            MemberProfile.objects.update_or_create(account=self.account, defaults={'points_balance': 100000})
            with patch('engagement.services.secrets.randbelow', return_value=1):
                result = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk,
                    'version': campaign.version, 'client_request_id': 'pity-setting-first'}, format='json')
            self.assertEqual(result.data['data']['pity']['count'], 1, result.data)
            switched = staff_client.post(path, {'version': campaign.version, 'pity_target_reward': second.pk,
                                                'prizes': rows}, format='json')
            self.assertEqual(switched.status_code, 200, switched.data)
            campaign.refresh_from_db()
            self.assertEqual(LotteryPityState.objects.get(account=self.account, campaign=campaign).count, 1)
            reset = staff_client.post(path, {'version': campaign.version, 'pity_limit': 2, 'prizes': rows}, format='json')
            self.assertEqual(reset.status_code, 200, reset.data)
            campaign.refresh_from_db()
            self.assertEqual(LotteryPityState.objects.get(account=self.account, campaign=campaign).count, 0)
            replay = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk,
                'version': original_version, 'client_request_id': 'pity-setting-first'}, format='json')
            self.assertEqual(replay.data['data']['batch_id'], result.data['data']['batch_id'])
            self.assertEqual(LotteryPityState.objects.get(account=self.account, campaign=campaign).count, 0)
            with patch('engagement.services.secrets.randbelow', return_value=0):
                initial = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk,
                    'version': campaign.version, 'client_request_id': 'pity-setting-after-reset-1'}, format='json')
                guaranteed = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk,
                    'version': campaign.version, 'client_request_id': 'pity-setting-after-reset-2'}, format='json')
            self.assertEqual(initial.data['data']['pity']['count'], 1, initial.data)
            self.assertTrue(guaranteed.data['data']['draws'][0]['is_pity'])
            self.assertEqual(guaranteed.data['data']['draws'][0]['claim']['reward'], second.pk)
            extended = staff_client.post(path, {'version': campaign.version, 'pity_limit': 300, 'prizes': rows}, format='json')
            self.assertEqual(extended.status_code, 200, extended.data)
            campaign.refresh_from_db()
            LotteryPityState.objects.filter(account=self.account, campaign=campaign).update(count=239)
            with patch('engagement.services.secrets.randbelow', return_value=0):
                longer = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk,
                    'version': campaign.version, 'client_request_id': 'pity-setting-above-old-limit'}, format='json')
            self.assertEqual(longer.data['data']['pity']['count'], 240, longer.data)
            self.assertFalse(longer.data['data']['draws'][0]['is_pity'])
            very_long = staff_client.post(path, {'version': campaign.version, 'pity_limit': 70000, 'prizes': rows}, format='json')
            self.assertEqual(very_long.status_code, 200, very_long.data)
            campaign.refresh_from_db()
            LotteryPityState.objects.filter(account=self.account, campaign=campaign).update(count=65535)
            with patch('engagement.services.secrets.randbelow', return_value=0):
                beyond_smallint = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk,
                    'version': campaign.version, 'client_request_id': 'pity-setting-above-smallint'}, format='json')
            self.assertEqual(beyond_smallint.data['data']['draws'][0]['pity_before'], 65535, beyond_smallint.data)
            self.assertEqual(beyond_smallint.data['data']['pity']['count'], 65536)

    def test_license_downgrade_blocks_new_shop_and_lottery_not_existing_claims(self):
        from engagement.models import LotteryCampaign, LotteryPrize, MemberProfile, RewardClaim, RewardItem
        from engagement.services import process_claim
        self.license.tier = 'ULTRA'
        self.license.save(update_fields=['tier'])
        with store_context(self.second):
            MemberProfile.objects.update_or_create(account=self.account, defaults={'points_balance': 100000})
            reward = RewardItem.objects.create(code='license-shop-and-lottery', name='可交付商品',
                kind='PHYSICAL', audience='BOSS', in_shop=True, is_active=True, point_cost=10, stock=20)
            campaign = LotteryCampaign.objects.create(name='授权奖池', enabled=True, point_cost=10)
            LotteryPrize.objects.create(campaign=campaign, reward=reward, probability=10000)
            shop = {'reward_id': reward.pk, 'client_request_id': 'license-redeem-existing'}
            draw = {'campaign_id': campaign.pk, 'version': campaign.version, 'client_request_id': 'license-draw-existing'}
            bought = self.boss.post('/api/club/redeem/', shop, format='json')
            won = self.boss.post('/api/club/lottery/', draw, format='json')
            self.assertEqual(bought.data['code'], 0, bought.data)
            self.assertEqual(won.data['code'], 0, won.data)
            before = MemberProfile.objects.get(account=self.account).points_balance
            self.license.tier = 'BASIC'
            self.license.save(update_fields=['tier'])
            self.assertEqual(self.boss.post('/api/club/redeem/', {**shop, 'client_request_id': 'license-redeem-new'}, format='json').status_code, 400)
            self.assertEqual(self.boss.post('/api/club/lottery/', {**draw, 'client_request_id': 'license-draw-new'}, format='json').status_code, 400)
            self.assertEqual(self.boss.post('/api/club/redeem/', shop, format='json').data['data']['id'], bought.data['data']['id'])
            self.assertEqual(self.boss.post('/api/club/lottery/', draw, format='json').data['data']['batch_id'], won.data['data']['batch_id'])
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, before)
            self.license.tier = 'ULTRA'
            self.license.save(update_fields=['tier'])
            self.end_license_days_ago(8)
            self.assertEqual(self.boss.post('/api/club/redeem/', {**shop, 'client_request_id': 'expired-redeem-new'}, format='json').status_code, 400)
            self.assertEqual(self.boss.post('/api/club/lottery/', {**draw, 'client_request_id': 'expired-draw-new'}, format='json').status_code, 400)
            process_claim(won.data['data']['claim']['id'], 'COMPLETED', '按既有中奖记录交付', self.account)
            self.assertEqual(RewardClaim.objects.get(pk=won.data['data']['claim']['id']).status, 'COMPLETED')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_expired_license_does_not_block_settlement_or_retroactively_award(self):
        with store_context(self.second):
            order = self.create_order()
            self.end_license_days_ago(8)
            self.assertTrue(self.mature(order))
            self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 1000)
            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
            self.assertEqual((decision.outcome, decision.points_amount, decision.license_evidence['status']),
                             ('DENY', 1050, 'EXPIRED'))
            self.license.ends_at = timezone.now() + timedelta(days=30)
            self.license.save(update_fields=['ends_at'])
            order.refresh_from_db()
            with transaction.atomic():
                award_order_points(order, service_confirmed=True)
            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        with store_context(self.first):
            self.assertFalse(LicenseConsumptionPointsDecision.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_grace_allows_the_original_principal_points_once(self):
        with store_context(self.second):
            order = self.create_order()
            self.end_license_days_ago(1)
            self.assertTrue(self.mature(order))
            self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 1050)
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
            self.assertEqual((decision.outcome, decision.license_evidence['status']), ('ALLOW', 'GRACE'))
            with transaction.atomic():
                award_order_points(order, service_confirmed=True)
            self.assertEqual(PointLedger.objects.filter(order=order, kind='CONSUMPTION').count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_control_unavailable_defers_decision_without_blocking_settlement(self):
        with store_context(self.second):
            order = self.create_order()
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated outage')):
                self.assertTrue(self.mature(order))
            self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
            self.assertEqual((decision.outcome, decision.license_evidence['status']),
                             ('UNKNOWN', 'CONTROL_UNAVAILABLE'))
            order.refresh_from_db()
            with transaction.atomic():
                award_order_points(order, service_confirmed=True)
            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review'
        response = self.platform.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['total'], 1)
        self.assertEqual(response.data['data']['list'][0]['order_id'], order.pk)
        other = self.platform.get(f'/api/admin/platform/store-management/{self.first.pk}/license-points-review')
        self.assertEqual(other.data['data']['total'], 0)
        finance = PlatformAccount.objects.create(username='points-finance', phone='13800001112', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(finance, 'SMS', finance.phone)
        denied = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token).get(url)
        self.assertEqual(denied.status_code, 403)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_unknown_order_points_review_awards_net_and_refunds_once(self):
        from uuid import uuid4
        from engagement.funding_wallet import refund_locked_order_funding
        from engagement.models import LicenseConsumptionPointsReview, MemberProfile
        from engagement.services import reverse_order_points
        from orders.models import OrderRefund
        with store_context(self.second):
            order = self.create_order()
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated outage')):
                self.assertTrue(self.mature(order))
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
            order.refresh_from_db()
            with transaction.atomic():
                refund = OrderRefund.objects.create(order=order, amount=200, reason='核对前退款')
                refund_locked_order_funding(order, self.wallet, 200)
                reverse_order_points(order, refund=refund)
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 0)
        url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review/{decision.pk}/resolve'
        body = {'request_id': str(uuid4()), 'outcome': 'ALLOW', 'reason': '已核对实销时有效授权记录'}
        response = self.platform.post(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['awarded_points_hundredths'], 750)
        self.assertEqual(self.platform.post(url, body, format='json').data, response.data)
        self.assertEqual(self.platform.post(url, {**body, 'outcome': 'DENY'}, format='json').status_code, 409)
        listing = self.platform.get(f'/api/admin/platform/store-management/{self.second.pk}/license-points-review')
        self.assertEqual(listing.data['data']['total'], 0)
        with store_context(self.second):
            decision.refresh_from_db()
            self.assertEqual(decision.outcome, 'UNKNOWN')
            self.assertEqual(LicenseConsumptionPointsReview.objects.get(decision=decision).awarded_points, 750)
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 750)
            with transaction.atomic():
                refund = OrderRefund.objects.create(order=order, amount=100, reason='核对后退款')
                refund_locked_order_funding(order, self.wallet, 100)
                reverse_order_points(order, refund=refund)
                reverse_order_points(order, refund=refund)
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 600)
            self.assertEqual(PointLedger.objects.get(order=order, kind='LICENSE_REVIEW_REFUND').amount, -150)
        with store_context(self.first):
            self.assertFalse(LicenseConsumptionPointsReview.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_unknown_order_points_review_does_not_restore_closed_cycle(self):
        from uuid import uuid4
        from engagement.models import LicenseConsumptionPointsReview, MemberProfile
        with store_context(self.second):
            order = self.create_order()
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated outage')):
                self.assertTrue(self.mature(order))
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
        url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review/{decision.pk}/resolve'
        with store_context(self.second):
            self.account.business_cycle += 1
            self.account.save(update_fields=['business_cycle'])
        body = {'request_id': str(uuid4()), 'outcome': 'ALLOW', 'reason': '已核实原授权，账户已退店'}
        response = self.platform.post(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['data']['awarded_points_hundredths'], response.data['data']['skip_reason']),
                         (0, 'CYCLE_CLOSED'))
        with store_context(self.second):
            self.assertFalse(PointLedger.objects.filter(order=order).exists())
            self.assertEqual(LicenseConsumptionPointsReview.objects.get(decision=decision).outcome, 'ALLOW')
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 0)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_unknown_order_points_review_denial_requires_platform_super_admin(self):
        from uuid import uuid4
        from engagement.models import LicenseConsumptionPointsReview, MemberProfile
        with store_context(self.second):
            order = self.create_order()
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated outage')):
                self.assertTrue(self.mature(order))
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
        url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review/{decision.pk}/resolve'
        body = {'request_id': str(uuid4()), 'outcome': 'DENY', 'reason': '核对后确认当时授权已失效'}
        finance = PlatformAccount.objects.create(username='review-finance', phone='13800001112', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(finance, 'SMS', finance.phone)
        denied = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token).post(url, body, format='json')
        self.assertEqual(denied.status_code, 403)
        response = self.platform.post(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['awarded_points_hundredths'], 0)
        self.assertEqual(self.platform.post(url, body, format='json').data, response.data)
        with store_context(self.second):
            self.assertEqual(LicenseConsumptionPointsReview.objects.get(decision=decision).outcome, 'DENY')
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 0)
            self.assertFalse(PointLedger.objects.filter(order=order).exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_unknown_order_points_review_recovers_after_platform_audit_failure(self):
        from uuid import uuid4
        from engagement.models import LicenseConsumptionPointsReview, MemberProfile
        from stores.models import PlatformAuditEvent
        with store_context(self.second):
            order = self.create_order()
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated outage')):
                self.assertTrue(self.mature(order))
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
        url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review/{decision.pk}/resolve'
        body = {'request_id': str(uuid4()), 'outcome': 'ALLOW', 'reason': '已核实原时点授权有效'}
        original = PlatformAuditEvent.objects.create
        def fail_audit(**kwargs):
            if kwargs.get('action') == 'license.points_reviewed':
                raise DatabaseError('模拟平台审计失败')
            return original(**kwargs)
        with patch('stores.models.PlatformAuditEvent.objects.create', side_effect=fail_audit):
            response = self.platform.post(url, body, format='json')
        self.assertEqual(response.status_code, 503, response.data)
        with store_context(self.second):
            self.assertEqual(LicenseConsumptionPointsReview.objects.filter(decision=decision).count(), 1)
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 1050)
        list_url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review'
        pending = self.platform.get(list_url, {'kind': 'ORDER_REVIEWED'}).data['data']['list'][0]
        self.assertEqual((pending['status'], pending['request_id'], pending['reason']),
                         ('AUDIT_PENDING', body['request_id'], body['reason']))
        replacement = PlatformAccount.objects.create(username='replacement-super', phone='13800001113', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=replacement, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(replacement, 'SMS', replacement.phone)
        other = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        response = other.post(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.platform.post(url, body, format='json').data, response.data)
        self.assertEqual(self.platform.get(list_url, {'kind': 'ORDER_REVIEWED'}).data['data']['list'][0]['status'], 'REVIEWED')
        with store_context(self.second):
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 1050)
            self.assertEqual(PointLedger.objects.filter(order=order, kind='LICENSE_REVIEW_AWARD').count(), 1)
        audit = PlatformAuditEvent.objects.get(action='license.points_reviewed')
        self.assertEqual(audit.account_id, replacement.pk)
        self.assertEqual(audit.detail['original_actor_id'], pending['original_actor_id'])

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
    def test_unknown_gift_points_review_and_refund(self):
        from uuid import uuid4
        from engagement.models import MemberProfile
        from orders.tests.test_gift_basket import GiftBasketTests
        from stores.license_benefits import check_new_benefits
        with store_context(self.second):
            self.client = APIClient(HTTP_HOST='xa.esports.club')
            GiftBasketTests.setUp(self)
            body = {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk, 'quantity': 1,
                    'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'license-gift-review'}
            def points_outage(feature=None):
                return ('UNKNOWN', {'status': 'CONTROL_UNAVAILABLE'}) if feature is None else check_new_benefits(feature)
            with patch('stores.license_benefits.check_new_benefits', side_effect=points_outage):
                created = self.client.post('/api/orders/gifts/send/', body, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            gift = Order.objects.get(pk=created.data['data']['id'])
            decision = LicenseConsumptionPointsDecision.objects.get(order=gift, outcome='UNKNOWN')
        url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review/{decision.pk}/resolve'
        resolved = self.platform.post(url, {'request_id': str(uuid4()), 'outcome': 'ALLOW',
            'reason': '已核对礼物支付时授权有效'}, format='json')
        self.assertEqual(resolved.status_code, 200, resolved.data)
        self.assertEqual(resolved.data['data']['awarded_points_hundredths'], decision.points_amount)
        with store_context(self.second):
            admin_client = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff)[0])
            preview = admin_client.get(f'/api/admin/orders/{gift.pk}/refund-preview/')
            self.assertEqual(preview.data['code'], 0, preview.data)
            refunded = admin_client.post(f'/api/admin/orders/{gift.pk}/refund/', {'reason': '礼物退款核对积分',
                'plan_token': preview.data['data']['plan_token'], 'client_request_id': 'license-gift-review-refund'}, format='json')
            self.assertEqual(refunded.data['code'], 0, refunded.data)
            self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 0)
            self.assertEqual(PointLedger.objects.get(order=gift, kind='LICENSE_REVIEW_REFUND').amount, -decision.points_amount)

    def test_pending_gift_vip_review_locks_wallet_before_review_row(self):
        from engagement.models import LicenseGiftVipDecision, LicenseGiftVipReview
        from engagement.services import confirm_gift_vip_consumption
        from orders.models import ServiceCategory
        from orders.tests.factories import make_service
        from uuid import uuid4

        def confirm_during_outage(order):
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated VIP outage')):
                return confirm_gift_vip_consumption(order)

        with store_context(self.second):
            category = ServiceCategory.objects.create(name='隔离会员礼物', is_gift=True)
            gift = make_service(price=100, service_category=category)
            with patch('engagement.services.confirm_gift_vip_consumption', side_effect=confirm_during_outage):
                paid = self.boss.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk,
                    'provider_id': self.provider.pk, 'quantity': 1, 'expected_amount': 100,
                    'client_request_id': str(uuid4())}, format='json')
            self.assertEqual(paid.data['code'], 0, paid.data)
            decision = LicenseGiftVipDecision.objects.get(order_id=paid.data['data']['id'])

        self._assert_pending_vip_review_lock_order(decision, LicenseGiftVipReview, 'license-gift-vip-review')

    @override_settings(BOSS_FUNDING_SELECTION_ENABLED=False, ORDINARY_SERVICE_FUNDING_ENABLED=False)
    def test_pending_service_vip_review_locks_wallet_before_review_row(self):
        from tempfile import TemporaryDirectory
        from engagement.confirmation import process_order
        from engagement.models import LicenseRefundVipDecision, LicenseRefundVipReview
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport

        def success(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        with store_context(self.second), TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.admin.defaults['HTTP_HOST'] = 'admin.esports.club'
            role = self.staff.admin_membership.roles.get()
            role.permissions += ['report:audit', 'report:view']
            role.save(update_fields=['permissions'])
            player = APIClient(
                HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0],
            )
            paid = success(self.boss.post('/api/orders/create/', self.payload(), format='json'))
            order = Order.objects.get(pk=paid['id'])
            order_url = f'/api/orders/orders/{order.pk}/'
            success(player.post(order_url + 'start/', {'entry_image': screenshot()}, format='multipart'))
            success(player.post(order_url + 'complete/', {'completion_image': screenshot()}, format='multipart'))
            report = ProviderReport.objects.get(order=order)
            report_url = f'/api/wallet/reports/{report.pk}/'
            success(player.post(report_url + 'images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
            success(player.post(report_url + 'submit/'))
            success(self.admin.post(f'/api/admin/reports/{report.pk}/approve/'))
            matured_at = timezone.now() + timedelta(hours=80)
            with (patch('django.utils.timezone.now', return_value=matured_at),
                  patch('stores.license_status.status_for_store',
                        side_effect=DatabaseError('isolated VIP outage'))):
                self.assertFalse(process_order(order.pk))
            decision = LicenseRefundVipDecision.objects.get(order=order, outcome='UNKNOWN')

        self._assert_pending_vip_review_lock_order(decision, LicenseRefundVipReview, 'license-vip-review')

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                       SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                       ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LICENSE_SNAPSHOT_CACHE_ENABLED=False)
    def test_structured_service_vip_review_uses_finalized_settlement(self):
        from tempfile import TemporaryDirectory
        from uuid import uuid4
        from engagement import license_refund_vip_review
        from engagement.confirmation import process_order
        from engagement.models import LicenseRefundVipDecision, LicenseRefundVipReview
        from engagement.services import completed_consumption
        from orders.models import OrderPointsSettlement
        from orders.points_checkout_test import require_order_funding_access
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport, Transaction

        def success(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        with store_context(self.second), TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.admin.defaults['HTTP_HOST'] = 'admin.esports.club'
            role = self.staff.admin_membership.roles.get()
            role.permissions += ['report:audit', 'report:view']
            role.save(update_fields=['permissions'])
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0])
            paid = success(self.boss.post('/api/orders/create/', self.payload(), format='json'))
            order = Order.objects.get(pk=paid['id'])
            order_url = f'/api/orders/orders/{order.pk}/'
            success(player.post(order_url + 'start/', {'entry_image': screenshot()}, format='multipart'))
            success(player.post(order_url + 'complete/', {'completion_image': screenshot()}, format='multipart'))
            report = ProviderReport.objects.get(order=order)
            report_url = f'/api/wallet/reports/{report.pk}/'
            success(player.post(report_url + 'images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
            success(player.post(report_url + 'submit/'))
            success(self.admin.post(f'/api/admin/reports/{report.pk}/approve/'))
            record = license_refund_vip_review.record_locked

            def record_during_outage(*args, **kwargs):
                with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated VIP outage')):
                    return record(*args, **kwargs)

            settled_at = timezone.now() + timedelta(hours=80)
            with (patch('django.utils.timezone.now', return_value=settled_at),
                  patch.object(license_refund_vip_review, 'record_locked', side_effect=record_during_outage),
                  override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False)):
                process_order(order.pk)
            order.refresh_from_db()
            settlement = OrderPointsSettlement.objects.get(order=order, finalized_at=order.settled_at, redemption=None)
            self.assertIsNotNone(settlement.finalized_at)
            self.assertEqual(order.service_clock_version, 0)
            self.assertIsNone(order.providers.get().settled_at)
            decision = LicenseRefundVipDecision.objects.get(order=order, outcome='UNKNOWN')
            self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
            before = list(order.transactions.order_by('pk').values())
            points = list(PointLedger.objects.filter(order=order).order_by('pk').values())

            url = f'/api/admin/platform/store-management/{self.second.pk}/license-vip-review/{decision.pk}/resolve'
            body = {'request_id': str(uuid4()), 'outcome': 'ALLOW', 'reason': '按原结算凭证核对'}
            with (patch('django.utils.timezone.now', return_value=settled_at + timedelta(minutes=1)),
                  override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False)):
                with self.assertRaises(ValidationError):
                    require_order_funding_access(order)
                with patch.object(OrderPointsSettlement.objects, 'select_for_update',
                                  return_value=OrderPointsSettlement.objects.none()):
                    denied = self.platform.post(url, body, format='json')
                self.assertEqual(denied.status_code, 409, denied.data)
                self.assertEqual(denied.data['msg'], '普通服务人员结算及补资凭证待核对')
                with transaction.atomic():
                    credit = settlement.persons.get().income_transaction_id
                    Transaction.objects.filter(pk=credit).update(created_at=settled_at - timedelta(seconds=1))
                    early = self.platform.post(url, body, format='json')
                    self.assertEqual(early.status_code, 409, early.data)
                    self.assertEqual(early.data['msg'], '普通服务原时点收益流水待核对')
                    transaction.set_rollback(True)
                self.assertFalse(LicenseRefundVipReview.objects.filter(decision=decision).exists())
                reviewed = success(self.platform.post(url, body, format='json'))
                self.assertTrue(reviewed['vip_applied'])
                replayed = success(self.platform.post(url, body, format='json'))
                self.assertEqual(replayed['id'], reviewed['id'])
                self.assertFalse(replayed['vip_recalculation_pending'])
            self.assertEqual(completed_consumption(self.account), order.amount)
            self.assertEqual(list(order.transactions.order_by('pk').values()), before)
            self.assertEqual(list(PointLedger.objects.filter(order=order).order_by('pk').values()), points)

    def _assert_pending_vip_review_lock_order(self, decision, model, endpoint):
        from django.db.models import QuerySet
        from uuid import uuid4
        from wallet.services import get_wallet

        url = f'/api/admin/platform/store-management/{self.second.pk}/{endpoint}/{decision.pk}/resolve'
        body = {'request_id': str(uuid4()), 'outcome': 'ALLOW', 'reason': '按原时点核对 VIP 权限'}
        with patch('stores.license_benefits.check_new_benefits', return_value=('DENY', {'status': 'EXPIRED'})):
            first = self.platform.post(url, body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(first.data['data']['vip_recalculation_pending'])
        select_for_update = QuerySet.select_for_update
        locked_reviews = []

        def check_review_lock(query, *args, **kwargs):
            if query.model is model:
                wallet.assert_called_once_with(account_id=self.account.pk, for_update=True, create=False)
                locked_reviews.append(query.model)
            return select_for_update(query, *args, **kwargs)

        with (patch('wallet.services.get_wallet', wraps=get_wallet) as wallet,
              patch.object(QuerySet, 'select_for_update', new=check_review_lock)):
            resolved = self.platform.post(url, body, format='json')
        self.assertEqual(resolved.status_code, 200, resolved.data)
        self.assertFalse(resolved.data['data']['vip_recalculation_pending'])
        self.assertEqual(locked_reviews, [model])
        with patch('wallet.services.get_wallet', wraps=get_wallet) as wallet:
            replayed = self.platform.post(url, body, format='json')
        wallet.assert_not_called()
        self.assertEqual(replayed.data, resolved.data)

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_control_outage_preserves_real_provider_settlement(self):
        from engagement.confirmation import process_order
        from wallet.models import ProviderReport, ProviderReportImage, Transaction
        from wallet.services import get_wallet
        with store_context(self.second):
            order = self.create_order()
            past = timezone.now() - timedelta(hours=80)
            Order.objects.filter(pk=order.pk).update(status='COMPLETED', completed_at=past)
            report = ProviderReport.objects.create(order=order, provider=self.provider_user,
                provider_account=self.provider, status='APPROVED', game_name='隔离测试', amount=order.amount,
                payout_amount=order.provider_income, entry_image='test/entry.png', completion_image='test/finish.png',
                submitted_at=past)
            ProviderReportImage.objects.create(report=report, image='test/result.png')
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('isolated outage')):
                self.assertTrue(process_order(order.pk))
            order.refresh_from_db()
            self.assertIsNotNone(order.settled_at)
            self.assertTrue(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertGreater(get_wallet(account=self.provider).balance, 0)
            self.assertEqual(LicenseConsumptionPointsDecision.objects.get(order=order).outcome, 'UNKNOWN')
            self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
    def test_new_gift_after_expiry_is_rejected_before_payment(self):
        from orders.tests.test_gift_basket import GiftBasketTests
        with store_context(self.second):
            self.client = APIClient(HTTP_HOST='xa.esports.club')
            GiftBasketTests.setUp(self)
            self.end_license_days_ago(8)
            response = self.client.post('/api/orders/gifts/send/', GiftBasketTests.payload(self), format='json')
            self.assertEqual(response.status_code, 403, response.data)
            self.assertEqual(response.data['errorCode'], 'LICENSE_GIFTS_UNAVAILABLE')
            self.assertFalse(Order.objects.filter(customer_account=self.account, is_gift_snapshot=True).exists())
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.balance, 100000)
            context = self.client.get('/api/orders/gifts/context/', {'browse': '1'})
            self.assertFalse(context.data['data']['gifting_available'])
            self.assertEqual(context.data['data']['gifts'], [])

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
    def test_order_tip_cannot_bypass_gift_license_restriction(self):
        from orders.tests.factories import make_completed_order, make_service
        from orders.tests.test_gift_basket import GiftBasketTests
        with store_context(self.second):
            self.client = APIClient(HTTP_HOST='xa.esports.club')
            GiftBasketTests.setUp(self)
            source = make_completed_order(self.boss, self.provider, service=make_service(price=1000),
                customer_account=self.account, provider_account=self.recipient, payment_status='PAID')
            self.end_license_days_ago(8)
            body = {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk, 'quantity': 1,
                    'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'license-order-tip'}
            response = self.client.post(f'/api/orders/orders/{source.pk}/tip/', body, format='json')
            self.assertEqual(response.status_code, 403, response.data)
            self.assertEqual(response.data['errorCode'], 'LICENSE_GIFTS_UNAVAILABLE')
            self.assertFalse(Order.objects.filter(customer_account=self.account, is_gift_snapshot=True).exists())
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.balance, 100000)

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
    def test_existing_gift_can_be_replayed_and_refunded_after_expiry(self):
        from engagement.models import GiftNamingGrant, ProviderNaming
        from orders.tests.test_gift_basket import GiftBasketTests
        with store_context(self.second):
            self.client = APIClient(HTTP_HOST='xa.esports.club')
            GiftBasketTests.setUp(self)
            body = {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk, 'quantity': 1,
                    'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'license-gift-existing'}
            created = self.client.post('/api/orders/gifts/send/', body, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            gift = Order.objects.get(pk=created.data['data']['id'])
            naming = ProviderNaming.objects.get()
            expiry = naming.expires_at
            self.end_license_days_ago(8)
            replay = self.client.post('/api/orders/gifts/send/', body, format='json')
            self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
            self.assertEqual(str(replay.data['fundResult']['data']['id']), str(gift.pk))
            admin_client = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff)[0])
            preview = admin_client.get(f'/api/admin/orders/{gift.pk}/refund-preview/')
            self.assertEqual(preview.data['code'], 0, preview.data)
            refunded = admin_client.post(f'/api/admin/orders/{gift.pk}/refund/', {'reason': '授权到期前订单退款',
                'plan_token': preview.data['data']['plan_token'], 'client_request_id': 'license-gift-refund'}, format='json')
            self.assertEqual(refunded.data['code'], 0, refunded.data)
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.balance, 100000)
            naming.refresh_from_db()
            self.assertEqual(naming.expires_at, expiry)
            self.assertTrue(GiftNamingGrant.objects.filter(order=gift).exists())

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
    def test_gift_tier_override_takes_effect_without_disturbing_previous_receipt(self):
        from orders.tests.test_gift_basket import GiftBasketTests
        with store_context(self.second):
            self.client = APIClient(HTTP_HOST='xa.esports.club')
            GiftBasketTests.setUp(self)
            self.license.tier = 'BASIC'
            self.license.save(update_fields=['tier'])
            body = {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk, 'quantity': 1,
                    'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'license-gift-switch'}
            blocked = self.client.post('/api/orders/gifts/send/', body, format='json')
            self.assertEqual(blocked.status_code, 403)
            self.assertFalse(Order.objects.filter(customer_account=self.account).exists())
        changed = self.platform.put('/api/admin/platform/license-tiers/BASIC/features/GIFTS',
                                    {'mode': 'FULL'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        with store_context(self.second):
            context = self.client.get('/api/orders/gifts/context/', {'browse': '1'})
            self.assertTrue(context.data['data']['gifting_available'])
            self.assertTrue(context.data['data']['gifts'])
            sent = self.client.post('/api/orders/gifts/send/', body, format='json')
            self.assertEqual(sent.data['code'], 0, sent.data)
        changed = self.platform.put('/api/admin/platform/license-tiers/BASIC/features/GIFTS',
                                    {'mode': 'OFF'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        with store_context(self.second):
            replay = self.client.post('/api/orders/gifts/send/', body, format='json')
            self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
            new = self.client.post('/api/orders/gifts/send/', {**body, 'client_request_id': 'license-gift-switch-2'}, format='json')
            self.assertEqual(new.status_code, 403)
            self.assertEqual(Order.objects.filter(customer_account=self.account, is_gift_snapshot=True).count(), 1)

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
    def test_gift_already_admitted_completes_if_license_expires_before_points_award(self):
        from engagement.models import GiftNamingGrant
        from orders.tests.test_gift_basket import GiftBasketTests
        from stores.license_status import status_for_store
        with store_context(self.second):
            self.client = APIClient(HTTP_HOST='xa.esports.club')
            GiftBasketTests.setUp(self)
            body = {'gift_service_id': self.one.pk, 'provider_id': self.recipient.pk, 'quantity': 1,
                    'naming_title': '测试冠名', 'expected_amount': 2100, 'client_request_id': 'license-gift-inflight'}
            calls = []
            def expires_after_admission(store, **kwargs):
                result = status_for_store(store, **kwargs)
                calls.append(1)
                if len(calls) == 1:
                    self.end_license_days_ago(8)
                return result
            with patch('stores.license_status.status_for_store', side_effect=expires_after_admission):
                response = self.client.post('/api/orders/gifts/send/', body, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            gift = Order.objects.get(pk=response.data['data']['id'])
            self.assertTrue(GiftNamingGrant.objects.filter(order=gift).exists())
            self.assertFalse(PointLedger.objects.filter(order=gift, kind='CONSUMPTION').exists())
            self.assertEqual(LicenseConsumptionPointsDecision.objects.get(order=gift).outcome, 'DENY')

    @override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_expired_license_keeps_existing_coupon_usable_but_prevents_new_claims(self):
        from coupons.models import Coupon, UserCoupon
        with store_context(self.second):
            original = Coupon.objects.create(name='已有券', discount_type='DIRECT', amount=100)
            future = Coupon.objects.create(name='待领取', discount_type='DIRECT', amount=100)
            claimed = self.boss.post(f'/api/coupons/{original.pk}/claim/', {}, format='json')
            self.assertEqual(claimed.data['code'], 0, claimed.data)
            owned = UserCoupon.objects.get(account=self.account, coupon=original)
            self.end_license_days_ago(8)
            denied = self.boss.post(f'/api/coupons/{future.pk}/claim/', {}, format='json')
            self.assertEqual(denied.status_code, 400)
            self.assertFalse(UserCoupon.objects.filter(account=self.account, coupon=future).exists())
            future.refresh_from_db()
            self.assertEqual(future.claimed_qty, 0)
            shown = self.boss.get('/api/coupons/mine/')
            self.assertEqual(shown.data['code'], 0)
            self.assertTrue(any(item['id'] == owned.pk for item in shown.data['data']))
            options = self.boss.post('/api/orders/discount-options-preview/',
                {'service_id': self.service.pk}, format='json')
            self.assertEqual(options.data['code'], 0, options.data)
            chosen = next(row for row in options.data['data']['options'] if row['coupon_id'] == owned.pk)
            result = self.boss.post('/api/orders/create/', self.payload(
                discount_selection_token=chosen['selection_token'], expected_amount=chosen['amount']), format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            owned.refresh_from_db()
            self.assertEqual(owned.status, 'USED')

    @override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_activity_discount_requires_license_but_existing_coupon_and_paid_order_survive(self):
        from coupons.models import Coupon, UserCoupon
        from promotions.models import Promotion
        with store_context(self.second):
            now = timezone.now()
            promo = Promotion.objects.create(title='门店活动', discount_rate=80, scope='ALL',
                start_at=now - timedelta(days=1), end_at=now + timedelta(days=30))
            coupon = Coupon.objects.create(name='已领取金额券', discount_type='DIRECT', amount=100)
            self.assertEqual(self.boss.post(f'/api/coupons/{coupon.pk}/claim/', {}, format='json').data['code'], 0)
            owned = UserCoupon.objects.get(account=self.account, coupon=coupon)
            def options():
                response = self.boss.post('/api/orders/discount-options-preview/',
                                          {'service_id': self.service.pk}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                return response.data['data']['options']
            offer = next(row for row in options() if row['promotion_id'] == promo.pk and row['coupon_id'] is None)
            result = self.boss.post('/api/orders/create/', self.payload(client_request_id='licensed-activity-order',
                discount_selection_token=offer['selection_token'], expected_amount=offer['amount']), format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            paid = Order.objects.get(pk=result.data['data']['id'])
            self.assertEqual((paid.amount, paid.promotion_id), (800, promo.pk))
            self.end_license_days_ago(1)
            self.assertTrue(any(row['promotion_id'] == promo.pk for row in options()))
            stale = next(row for row in options() if row['promotion_id'] == promo.pk and row['coupon_id'] is None)
            self.end_license_days_ago(8)
            current = options()
            self.assertFalse(any(row['promotion_id'] == promo.pk for row in current))
            rejected = self.boss.post('/api/orders/create/', self.payload(client_request_id='expired-activity-token',
                discount_selection_token=stale['selection_token'], expected_amount=stale['amount']), format='json')
            self.assertNotEqual(rejected.data.get('code'), 0, rejected.data)
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.balance, 900)
            available = next(row for row in current if row['coupon_id'] == owned.pk and row['promotion_id'] is None)
            purchase = self.boss.post('/api/orders/create/', self.payload(client_request_id='expired-owned-coupon',
                discount_selection_token=available['selection_token'], expected_amount=available['amount'],
                gift_balance_amount=400, compensation_balance_amount=300), format='json')
            self.assertEqual(purchase.data['code'], 0, str(purchase.data).encode('unicode_escape').decode())
            self.assertEqual(Order.objects.get(pk=purchase.data['data']['id']).amount, 900)
            paid.refresh_from_db()
            self.assertEqual((paid.amount, paid.promotion_id), (800, promo.pk))

    @override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_activity_entitlement_changes_apply_without_affecting_core_order(self):
        from promotions.models import Promotion
        with store_context(self.second):
            now = timezone.now()
            promo = Promotion.objects.create(title='档位活动', discount_rate=80, scope='ALL',
                start_at=now - timedelta(days=1), end_at=now + timedelta(days=30))
            self.license.tier = 'BASIC'
            self.license.save(update_fields=['tier'])
            def promotion_shown():
                response = self.boss.post('/api/orders/discount-options-preview/',
                                          {'service_id': self.service.pk}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                return any(row['promotion_id'] == promo.pk for row in response.data['data']['options'])
            self.assertFalse(promotion_shown())
        allowed = self.platform.put('/api/admin/platform/license-tiers/BASIC/features/PROMOTIONS',
                                    {'mode': 'FULL'}, format='json')
        self.assertEqual(allowed.status_code, 200, allowed.data)
        with store_context(self.second):
            self.assertTrue(promotion_shown())
            self.end_license_days_ago(8)
            self.assertFalse(promotion_shown())
            quotes = self.boss.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
            self.assertEqual(quotes.data['code'], 0, quotes.data)
            plain = next(row for row in quotes.data['data']['options']
                         if row['promotion_id'] is None and row['coupon_id'] is None)
            result = self.boss.post('/api/orders/create/', self.payload(client_request_id='expired-core-order',
                discount_selection_token=plain['selection_token'], expected_amount=plain['amount']), format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual((Order.objects.get(pk=result.data['data']['id']).amount,
                              Order.objects.get(pk=result.data['data']['id']).promotion_id), (1000, None))

    def test_legacy_order_quote_drops_new_activity_after_license_expiry(self):
        from promotions.models import Promotion
        from orders.purchase_benefits import prepare_purchase
        with store_context(self.second):
            now = timezone.now()
            promo = Promotion.objects.create(title='普通活动', discount_rate=80, commission_rate=11, scope='ALL',
                start_at=now - timedelta(days=1), end_at=now + timedelta(days=30))
            price, chosen, _ = prepare_purchase(self.account, self.service)
            self.assertEqual((price.amount, chosen.pk), (800, promo.pk))
            self.admin.defaults['HTTP_HOST'] = 'admin.esports.club'
            active = self.admin.get('/api/admin/orders/suggest-commission/', {'service_id': self.service.pk})
            self.assertEqual(active.data['code'], 0, active.data)
            self.assertEqual(active.data['data']['commission_source'], 'promotion')
            self.end_license_days_ago(8)
            price, chosen, _ = prepare_purchase(self.account, self.service)
            self.assertEqual((price.amount, chosen), (1000, None))
            expired = self.admin.get('/api/admin/orders/suggest-commission/', {'service_id': self.service.pk})
            self.assertEqual(expired.data['code'], 0, expired.data)
            self.assertNotEqual(expired.data['data']['commission_source'], 'promotion')

    @override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, LICENSE_SNAPSHOT_CACHE_ENABLED=False)
    def test_missing_license_snapshot_hides_new_activity_keeps_owned_coupon(self):
        from coupons.models import Coupon, UserCoupon
        from promotions.models import Promotion
        with store_context(self.second):
            now = timezone.now()
            promo = Promotion.objects.create(title='离线活动', discount_rate=80, scope='ALL',
                start_at=now - timedelta(days=1), end_at=now + timedelta(days=30))
            coupon = Coupon.objects.create(name='已有金额券', discount_type='DIRECT', amount=100)
            self.assertEqual(self.boss.post(f'/api/coupons/{coupon.pk}/claim/', {}, format='json').data['code'], 0)
            owned = UserCoupon.objects.get(account=self.account, coupon=coupon)
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('control unavailable')):
                result = self.boss.post('/api/orders/discount-options-preview/',
                                        {'service_id': self.service.pk}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            options = result.data['data']['options']
            self.assertFalse(any(row['promotion_id'] == promo.pk for row in options))
            self.assertTrue(any(row['coupon_id'] == owned.pk for row in options))

    @override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, LICENSE_SNAPSHOT_CACHE_ENABLED=True)
    def test_cached_activity_license_expires_during_control_outage(self):
        from promotions.models import Promotion
        from stores.license_snapshot import business_status
        with store_context(self.second):
            now = timezone.now()
            promo = Promotion.objects.create(title='缓存活动', discount_rate=80, scope='ALL',
                start_at=now - timedelta(days=1), end_at=now + timedelta(days=90))
            with self.captureOnCommitCallbacks(using='default', execute=True), \
                    self.captureOnCommitCallbacks(using='store_b', execute=True):
                self.assertEqual(business_status(self.second)['status'], 'ACTIVE')
            deadline = self.license.ends_at + timedelta(days=7)
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('control unavailable')):
                with patch('django.utils.timezone.now', return_value=deadline - timedelta(microseconds=1)):
                    allowed = self.boss.post('/api/orders/discount-options-preview/',
                                             {'service_id': self.service.pk}, format='json')
                with patch('django.utils.timezone.now', return_value=deadline):
                    expired = self.boss.post('/api/orders/discount-options-preview/',
                                             {'service_id': self.service.pk}, format='json')
            self.assertEqual(allowed.data['code'], 0, allowed.data)
            self.assertEqual(expired.data['code'], 0, expired.data)
            self.assertTrue(any(row['promotion_id'] == promo.pk for row in allowed.data['data']['options']))
            self.assertFalse(any(row['promotion_id'] == promo.pk for row in expired.data['data']['options']))

    def test_expired_license_does_not_issue_additional_periodic_vip_coupons(self):
        from coupons.models import UserCoupon
        from engagement.models import VipTier
        from orders.tests.factories import make_boss_type
        with store_context(self.second):
            tier = VipTier.objects.create(level=2, boss_type=make_boss_type(discount_rate=90), recharge_threshold=1)
            self.account.boss_type = tier.boss_type
            self.account.save(update_fields=['boss_type'])
            self.assertEqual(self.boss.get('/api/coupons/mine/').status_code, 200)
            before = list(UserCoupon.objects.filter(account=self.account).values_list('pk', flat=True))
            self.assertTrue(before)
            tier.level = 3
            tier.save(update_fields=['level'])
            self.end_license_days_ago(8)
            response = self.boss.get('/api/coupons/mine/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(list(UserCoupon.objects.filter(account=self.account).values_list('pk', flat=True)), before)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_expired_license_does_not_create_vip_upgrade_rights_at_confirmation(self):
        from coupons.models import UserCoupon
        from engagement.models import MemberBenefitGrant, VipTier
        from orders.tests.factories import make_boss_type
        with store_context(self.second):
            tier = VipTier.objects.create(level=2, boss_type=make_boss_type(discount_rate=90), recharge_threshold=1)
            order = self.create_order()
            self.end_license_days_ago(8)
            self.assertTrue(self.mature(order))
            self.account.refresh_from_db()
            self.assertNotEqual(self.account.boss_type_id, tier.boss_type_id)
            self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account).exists())
            self.assertFalse(UserCoupon.objects.filter(account=self.account).exists())
            self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_tier_features_and_platform_overrides_apply_to_new_coupon_and_vip_grants(self):
        from coupons.models import Coupon, UserCoupon
        from engagement.models import VipTier
        from orders.tests.factories import make_boss_type
        with store_context(self.second):
            self.license.tier = 'BASIC'
            self.license.save(update_fields=['tier'])
            coupon = Coupon.objects.create(name='档位优惠券', discount_type='DIRECT', amount=100)
            tier = VipTier.objects.create(level=2, boss_type=make_boss_type(discount_rate=90), recharge_threshold=1)
            self.account.boss_type = tier.boss_type
            self.account.save(update_fields=['boss_type'])
            self.assertEqual(self.boss.post(f'/api/coupons/{coupon.pk}/claim/', {}, format='json').status_code, 400)
            self.assertEqual(self.boss.get('/api/coupons/mine/').status_code, 200)
            self.assertFalse(UserCoupon.objects.filter(account=self.account).exists())
        promo = self.platform.put('/api/admin/platform/license-tiers/BASIC/features/PROMOTIONS',
                                  {'mode': 'FULL'}, format='json')
        self.assertEqual(promo.status_code, 200, promo.data)
        vip = self.platform.put('/api/admin/platform/license-tiers/BASIC/features/VIP_GROWTH',
                                {'mode': 'FULL'}, format='json')
        self.assertEqual(vip.status_code, 200, vip.data)
        with store_context(self.second):
            result = self.boss.post(f'/api/coupons/{coupon.pk}/claim/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(self.boss.get('/api/coupons/mine/').status_code, 200)
            self.assertTrue(UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-month:').exists())

    def test_existing_vip_grant_retries_are_safe_but_new_grants_are_rejected(self):
        from coupons.benefits import issue as issue_vip_coupon
        from coupons.models import Coupon, UserCoupon
        from engagement.models import RewardClaim, RewardItem, VipTier
        from engagement.services import grant_entitlement
        from engagement.vip_threshold_change import _issue_new_rewards
        from orders.tests.factories import make_boss_type
        with store_context(self.second):
            tier = VipTier.objects.create(level=2, boss_type=make_boss_type(discount_rate=90), recharge_threshold=1)
            reward = RewardItem.objects.create(code='licensed-vip-gift', name='VIP 权益', kind='SERVICE')
            coupon = issue_vip_coupon(self.account, 'license-vip-existing', name='已发券', rate=90)[0]
            claim = grant_entitlement(self.account, reward, 'license-vip-existing-claim', 'VIP')
            self.end_license_days_ago(8)
            self.assertEqual(issue_vip_coupon(self.account, 'license-vip-existing', name='已发券', rate=90)[0].pk,
                             coupon.pk)
            self.assertEqual(grant_entitlement(self.account, reward, 'license-vip-existing-claim', 'VIP').pk, claim.pk)
            with self.assertRaises(ValidationError):
                issue_vip_coupon(self.account, 'license-vip-existing', name='已发券', rate=90, quantity=2)
            self.assertFalse(Coupon.objects.filter(grant_key='license-vip-existing:1').exists())
            self.assertEqual(UserCoupon.objects.filter(account=self.account, coupon__grant_key='license-vip-existing:0').count(), 1)
            with self.assertRaises(ValidationError):
                grant_entitlement(self.account, reward, 'license-vip-new-claim', 'VIP')
            with self.assertRaises(ValidationError):
                _issue_new_rewards(self.account, (tier.pk,))
            self.assertFalse(RewardClaim.objects.filter(event_key='license-vip-new-claim').exists())

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                       VIP_MONTHLY_DECISIONS_ENABLED=True)
    def test_license_expiry_rolls_back_admin_vip_threshold_recalculation(self):
        from engagement.models import MemberBenefitGrant, RewardClaim
        from engagement.test_vip_threshold_change import VipThresholdChangeTest
        with store_context(self.second):
            VipThresholdChangeTest.setUp(self)
            self.client.defaults['HTTP_HOST'] = 'admin.esports.club'
            token = VipThresholdChangeTest.token(self, 500)
            self.end_license_days_ago(8)
            response = VipThresholdChangeTest.change(self, 500, token)
            self.assertEqual(response.status_code, 400, response.data)
            self.high.refresh_from_db()
            self.account.refresh_from_db()
            self.assertEqual((self.high.recharge_threshold, self.account.boss_type_id), (900, self.low.boss_type_id))
            self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=self.high).exists())
            self.assertFalse(RewardClaim.objects.filter(account=self.account, reward=self.reward).exists())

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_license_unavailable_during_vip_grant_rolls_back_rights_not_settlement(self):
        from engagement.confirmation import process_order
        from engagement.models import MemberBenefitGrant, VipTier
        from orders.tests.factories import make_boss_type
        from stores.license_status import status_for_store
        from wallet.models import ProviderReport, ProviderReportImage, Transaction
        with store_context(self.second):
            tier = VipTier.objects.create(level=2, boss_type=make_boss_type(discount_rate=90), recharge_threshold=1)
            order = self.create_order()
            past = timezone.now() - timedelta(hours=80)
            Order.objects.filter(pk=order.pk).update(status='COMPLETED', completed_at=past)
            report = ProviderReport.objects.create(order=order, provider=self.provider_user,
                provider_account=self.provider, status='APPROVED', game_name='隔离测试', amount=order.amount,
                payout_amount=order.provider_income, entry_image='test/entry.png', completion_image='test/finish.png',
                submitted_at=past)
            ProviderReportImage.objects.create(report=report, image='test/result.png')
            calls = []
            def fail_during_grant(store, **kwargs):
                calls.append(store.slug)
                if len(calls) >= 3:
                    raise DatabaseError('control unavailable during entitlement issuance')
                return status_for_store(store, **kwargs)
            with patch('stores.license_status.status_for_store', side_effect=fail_during_grant):
                self.assertTrue(process_order(order.pk))
            order.refresh_from_db()
            self.account.refresh_from_db()
            self.assertIsNotNone(order.settled_at)
            self.assertTrue(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertTrue(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
            self.assertNotEqual(self.account.boss_type_id, tier.boss_type_id)
            self.assertFalse(MemberBenefitGrant.objects.filter(account=self.account, tier=tier).exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_existing_points_can_still_be_used_for_products_and_lottery_after_expiry(self):
        from engagement.models import LotteryCampaign, LotteryPrize, MemberProfile, RewardItem
        with store_context(self.second):
            profile, _ = MemberProfile.objects.get_or_create(account=self.account)
            profile.points_balance = 10000
            profile.save(update_fields=['points_balance'])
            product = RewardItem.objects.create(code='license-physical', name='兑换商品', kind='PHYSICAL',
                                                point_cost=10, stock=1, is_active=True)
            prize = RewardItem.objects.create(code='license-draw', name='抽奖权益', kind='SERVICE',
                                              is_active=True, in_shop=False)
            campaign = LotteryCampaign.objects.create(name='积分抽奖', point_cost=20, enabled=True)
            LotteryPrize.objects.create(campaign=campaign, reward=prize, probability=10000)
            self.end_license_days_ago(8)
            payload = {'reward_id': product.pk, 'client_request_id': 'license-product-01'}
            redeemed = self.boss.post('/api/club/redeem/', payload, format='json')
            self.assertEqual(redeemed.data['code'], 0, redeemed.data)
            self.assertEqual(self.boss.post('/api/club/redeem/', payload, format='json').data['data']['id'],
                             redeemed.data['data']['id'])
            result = self.boss.post('/api/club/lottery/', {'campaign_id': campaign.pk, 'version': campaign.version,
                'client_request_id': 'license-lottery-01'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            profile.refresh_from_db()
            self.assertEqual(profile.points_balance, 7000)


@override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LICENSE_NOTICE_DISPATCH_ENABLED=False, PLATFORM_IDENTITY_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class LicensePointsReviewMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}
    payload = LicenseConsumptionPointsTests.payload
    mature = LicenseConsumptionPointsTests.mature
    create_order = LicenseConsumptionPointsTests.create_order
    test_review_then_refund = LicenseConsumptionPointsTests.test_unknown_order_points_review_awards_net_and_refunds_once
    test_review_recovers_after_control_failure = LicenseConsumptionPointsTests.test_unknown_order_points_review_recovers_after_platform_audit_failure

    def setUp(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('requires disposable MySQL with separate tenant database')
        LicenseConsumptionPointsTests.setUp(self)
