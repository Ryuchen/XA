"""陪玩本人资料接口与工作台统计扩展测试。

覆盖：
- GET   /api/users/escorts/me/      陪玩读取本人资料
- PATCH /api/users/escorts/me/      可自助字段更新 + 受保护字段不可改 + 校验
- GET   /api/users/provider-stats/  total_income / escort_status 扩展
"""

import calendar
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import GameCategory, Order
from orders.tests.factories import (
    User,
    make_achievement,
    make_evaluation,
    make_order,
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from users.models import (
    Achievement, CheckinMonthProgress, CheckinRecord, CheckinRuleConfig,
    CustomerGameProfile, EscortProfile, ProviderPassPurchase,
)
from users.views import (
    MONTHLY_CHECKIN_REWARDS,
    _continuous_days,
    _reward_for_seq,
)
from wallet.models import Transaction


class CustomerGameProfileTest(APITestCase):
    URL = '/api/users/me/'

    def setUp(self):
        self.customer = make_user(role='CUSTOMER')
        self.client.force_authenticate(self.customer)
        self.wzry = GameCategory.objects.create(name='王者荣耀', sort_order=1)
        self.lol = GameCategory.objects.create(name='英雄联盟', sort_order=2)
        self.inactive = GameCategory.objects.create(name='停用游戏', is_active=False)

    def test_saves_profiles_by_admin_game_category(self):
        response = self.client.patch(self.URL, {
            'game_profiles': [
                {
                    'game_category': self.wzry.id,
                    'region': '微信区',
                    'nickname': '王者老板',
                    'uid': 'WZ-1001',
                    'is_default': True,
                },
                {
                    'game_category': self.lol.id,
                    'region': '艾欧尼亚',
                    'nickname': '峡谷老板',
                    'uid': 'LOL-1001',
                },
            ],
        }, format='json')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(CustomerGameProfile.objects.filter(user=self.customer).count(), 2)
        self.assertEqual(
            [item['game_category_name'] for item in response.data['data']['game_profiles']],
            ['王者荣耀', '英雄联盟'],
        )
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.game_region, '微信区')

    def test_rejects_inactive_game_category(self):
        response = self.client.patch(self.URL, {
            'game_profiles': [{
                'game_category': self.inactive.id,
                'region': '国服',
                'nickname': '测试',
                'uid': '1',
            }],
        }, format='json')
        self.assertEqual(response.data['code'], 400)

    def test_public_game_categories_only_returns_active_items(self):
        response = self.client.get('/api/orders/game-categories/')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual([item['name'] for item in response.data['data']], ['王者荣耀', '英雄联盟'])


class EscortPublicListOrderingTest(APITestCase):
    URL = '/api/users/escorts/'

    def setUp(self):
        self.customer = make_user(role='CUSTOMER')
        self.client.force_authenticate(self.customer)

    def _provider(self, name, tier='', scores=(), service_area=''):
        provider = make_provider(display_name=name, is_verified=True)
        profile = provider.escort_profile
        profile.pass_tier = tier
        profile.pass_expires_at = timezone.now() + timedelta(days=1) if tier else None
        profile.service_area = service_area
        profile.save(update_fields=['pass_tier', 'pass_expires_at', 'service_area'])
        for score in scores:
            boss = make_user(role='CUSTOMER')
            order = make_order(boss, provider=provider, status=Order.Status.COMPLETED)
            make_evaluation(order, customer=boss, provider=provider, score=score)
        return provider

    def test_orders_by_active_pass_then_favorable_rate_without_exposing_pass(self):
        self._provider('无卡高好评', scores=(5, 5))
        self._provider('黑卡低好评', EscortProfile.PassTier.BLACK, scores=(2,))
        self._provider('金卡高好评', EscortProfile.PassTier.GOLD, scores=(5,))
        res = self.client.get(self.URL)
        self.assertEqual([item['nickname'] for item in res.data['data']], ['黑卡低好评', '金卡高好评', '无卡高好评'])
        self.assertTrue(all('pass_tier' not in item and 'active_tier' not in item for item in res.data['data']))

    def test_same_pass_tier_orders_by_favorable_rate(self):
        self._provider('银卡低好评', EscortProfile.PassTier.SILVER, scores=(2, 5))
        self._provider('银卡高好评', EscortProfile.PassTier.SILVER, scores=(5, 5))
        res = self.client.get(self.URL)
        self.assertEqual([item['nickname'] for item in res.data['data']], ['银卡高好评', '银卡低好评'])
        self.assertEqual(res.data['data'][0]['favorableRate'], 100.0)

    def test_filters_by_game_service_area(self):
        self._provider('王者陪玩', service_area='王者荣耀、英雄联盟')
        self._provider('和平陪玩', service_area='和平精英')
        res = self.client.get(self.URL, {'game': '王者荣耀'})
        self.assertEqual([item['nickname'] for item in res.data['data']], ['王者陪玩'])


class ProviderPassTest(APITestCase):
    URL = '/api/users/provider-pass/'

    def setUp(self):
        self.provider = make_provider()
        make_wallet(self.provider, balance=200000)
        self.client.force_authenticate(self.provider)

    def test_daily_black_pass_charges_daily_price(self):
        response = self.client.post(self.URL, {'tier': 'BLACK', 'days': 1})
        self.assertEqual(response.data['code'], 0)
        purchase = ProviderPassPurchase.objects.get(provider=self.provider)
        self.assertEqual(purchase.original_amount, 5000)
        self.assertEqual(purchase.paid_amount, 5000)
        self.assertEqual(purchase.discount_amount, 0)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 195000)
        self.assertEqual(purchase.transaction.tx_type, Transaction.TxType.PASS_PURCHASE)

    def test_same_tier_renewal_extends_from_expiry(self):
        self.client.post(self.URL, {'tier': 'BRONZE', 'days': 1})
        self.provider.escort_profile.refresh_from_db()
        first_expiry = self.provider.escort_profile.pass_expires_at
        self.client.post(self.URL, {'tier': 'BRONZE', 'days': 1})
        self.provider.escort_profile.refresh_from_db()
        self.assertAlmostEqual(
            (self.provider.escort_profile.pass_expires_at - first_expiry).total_seconds(),
            86400,
            delta=1,
        )

    def test_active_pass_cannot_switch_tier(self):
        self.client.post(self.URL, {'tier': 'SILVER', 'days': 1})
        response = self.client.post(self.URL, {'tier': 'GOLD', 'days': 1})
        self.assertEqual(response.data['code'], 400)


class ProviderPassOrderVisibilityTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.service = make_service()
        self.order = make_order(self.customer, service=self.service, status=Order.Status.PENDING)

    def _set_age(self, seconds):
        Order.objects.filter(pk=self.order.pk).update(
            created_at=timezone.now() - timedelta(seconds=seconds),
        )

    def test_black_sees_order_immediately(self):
        provider = make_provider()
        profile = provider.escort_profile
        profile.pass_tier = EscortProfile.PassTier.BLACK
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self.client.force_authenticate(provider)
        response = self.client.get('/api/orders/orders/?role=provider&status=pending')
        self.assertEqual(len(response.data['data']), 1)

    def test_bronze_waits_two_minutes_and_no_pass_waits_five(self):
        bronze = make_provider()
        profile = bronze.escort_profile
        profile.pass_tier = EscortProfile.PassTier.BRONZE
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self._set_age(130)
        self.client.force_authenticate(bronze)
        response = self.client.get('/api/orders/orders/?role=provider&status=pending')
        self.assertEqual(len(response.data['data']), 1)

        no_pass = make_provider()
        self.client.force_authenticate(no_pass)
        response = self.client.get('/api/orders/orders/?role=provider&status=pending')
        self.assertEqual(len(response.data['data']), 0)

    def test_grab_endpoint_cannot_bypass_visibility_delay(self):
        no_pass = make_provider()
        self.client.force_authenticate(no_pass)
        response = self.client.post(f'/api/orders/orders/{self.order.id}/grab/')
        self.assertEqual(response.data['code'], 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertIsNone(self.order.provider_id)


class EscortMeReadTest(APITestCase):
    def test_provider_reads_own_profile(self):
        provider = make_provider(
            display_name='夜枭',
            gender=EscortProfile.Gender.MALE,
            city='上海',
            service_area='王者荣耀上分',
            price_per_hour=5000,
        )
        self.client.force_authenticate(provider)
        res = self.client.get('/api/users/escorts/me/')
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['display_name'], '夜枭')
        self.assertEqual(data['gender'], 'MALE')
        self.assertEqual(data['city'], '上海')
        self.assertEqual(data['service_area'], '王者荣耀上分')
        self.assertEqual(data['price_per_hour'], 5000)

    def test_customer_cannot_read_escort_profile(self):
        customer = make_user()
        self.client.force_authenticate(customer)
        res = self.client.get('/api/users/escorts/me/')
        self.assertEqual(res.data['code'], 403)

    def test_requires_auth(self):
        res = self.client.get('/api/users/escorts/me/')
        self.assertEqual(res.status_code, 401)


class EscortMeUpdateTest(APITestCase):
    def setUp(self):
        self.provider = make_provider(
            display_name='初始昵称',
            price_per_hour=5000,
            rank_tier='钻石',
        )
        self.profile = self.provider.escort_profile

    def test_update_editable_fields(self):
        self.client.force_authenticate(self.provider)
        res = self.client.patch('/api/users/escorts/me/', {
            'display_name': '寒鸦',
            'gender': 'FEMALE',
            'city': '杭州',
            'service_area': '英雄联盟陪练',
            'bio': '钻石上分专精',
        })
        self.assertEqual(res.data['code'], 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.display_name, '寒鸦')
        self.assertEqual(self.profile.gender, 'FEMALE')
        self.assertEqual(self.profile.city, '杭州')
        self.assertEqual(self.profile.service_area, '英雄联盟陪练')
        self.assertEqual(self.profile.bio, '钻石上分专精')

    def test_protected_fields_not_modified(self):
        self.client.force_authenticate(self.provider)
        res = self.client.patch('/api/users/escorts/me/', {
            'display_name': '寒鸦',
            'price_per_hour': 99999,
            'rank_tier': '王者',
            'is_verified': True,
        })
        self.assertEqual(res.data['code'], 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.price_per_hour, 5000)
        self.assertEqual(self.profile.rank_tier, '钻石')
        self.assertFalse(self.profile.is_verified)

    def test_reject_blank_display_name(self):
        self.client.force_authenticate(self.provider)
        res = self.client.patch('/api/users/escorts/me/', {'display_name': '   '})
        self.assertEqual(res.data['code'], 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.display_name, '初始昵称')

    def test_reject_invalid_gender(self):
        self.client.force_authenticate(self.provider)
        res = self.client.patch('/api/users/escorts/me/', {'gender': 'ALIEN'})
        self.assertEqual(res.data['code'], 400)


class ProviderStatsExtensionTest(APITestCase):
    def test_stats_returns_income_and_status(self):
        provider = make_provider(status=EscortProfile.Status.AVAILABLE)
        wallet = make_wallet(provider, balance=8000)
        Transaction.objects.create(
            wallet=wallet,
            amount=5000,
            tx_type=Transaction.TxType.INCOME,
            status=Transaction.Status.SUCCESS,
            balance_before=0,
            balance_after=5000,
        )
        Transaction.objects.create(
            wallet=wallet,
            amount=3000,
            tx_type=Transaction.TxType.INCOME,
            status=Transaction.Status.SUCCESS,
            balance_before=5000,
            balance_after=8000,
        )
        # 失败/非收益流水不计入
        Transaction.objects.create(
            wallet=wallet,
            amount=1000,
            tx_type=Transaction.TxType.INCOME,
            status=Transaction.Status.FAILED,
            balance_before=8000,
            balance_after=8000,
        )
        self.client.force_authenticate(provider)
        res = self.client.get('/api/users/provider-stats/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total_income'], 8000)
        self.assertEqual(res.data['data']['escort_status'], 'AVAILABLE')

    def test_stats_without_profile_defaults_offline(self):
        provider = make_user(role='PROVIDER')
        self.client.force_authenticate(provider)
        res = self.client.get('/api/users/provider-stats/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total_income'], 0)
        self.assertEqual(res.data['data']['escort_status'], 'OFFLINE')

    def test_stats_income_trend_has_seven_zero_filled_days(self):
        provider = make_provider()
        make_wallet(provider, balance=0)
        self.client.force_authenticate(provider)
        res = self.client.get('/api/users/provider-stats/')
        trend = res.data['data']['income_trend']
        self.assertEqual(len(trend), 7)
        self.assertTrue(all(point['income'] == 0 for point in trend))
        # 日期升序，末位为今天
        dates = [point['date'] for point in trend]
        self.assertEqual(dates, sorted(dates))

    def test_stats_income_trend_aggregates_today(self):
        provider = make_provider()
        wallet = make_wallet(provider, balance=0)
        Transaction.objects.create(
            wallet=wallet,
            amount=4000,
            tx_type=Transaction.TxType.INCOME,
            status=Transaction.Status.SUCCESS,
            balance_before=0,
            balance_after=4000,
        )
        self.client.force_authenticate(provider)
        res = self.client.get('/api/users/provider-stats/')
        trend = res.data['data']['income_trend']
        self.assertEqual(trend[-1]['income'], 4000)

    def test_stats_completion_rate(self):
        from orders.models import Order
        from orders.tests.factories import make_order, make_user as make_customer

        provider = make_provider()
        customer = make_customer()
        make_order(customer, provider=provider, status=Order.Status.COMPLETED)
        make_order(customer, provider=provider, status=Order.Status.COMPLETED)
        make_order(customer, provider=provider, status=Order.Status.COMPLETED)
        make_order(customer, provider=provider, status=Order.Status.CANCELLED)
        self.client.force_authenticate(provider)
        res = self.client.get('/api/users/provider-stats/')
        # 3 完成 / (3+1) 终态 = 75%
        self.assertEqual(res.data['data']['completion_rate'], 75)

    def test_stats_completion_rate_zero_when_no_finished_orders(self):
        provider = make_provider()
        self.client.force_authenticate(provider)
        res = self.client.get('/api/users/provider-stats/')
        self.assertEqual(res.data['data']['completion_rate'], 0)


class EscortScheduleTest(APITestCase):
    """陪玩每周循环档期（四段制）接口测试。"""

    SCHEDULE_URL = '/api/users/escorts/schedule/'

    def setUp(self):
        self.provider = make_provider()

    def test_requires_authentication(self):
        res = self.client.get(self.SCHEDULE_URL)
        self.assertEqual(res.status_code, 401)

    def test_non_provider_denied(self):
        customer = make_user(role='CUSTOMER')
        self.client.force_authenticate(customer)
        res = self.client.get(self.SCHEDULE_URL)
        self.assertEqual(res.data['code'], 403)

    def test_get_empty_by_default(self):
        self.client.force_authenticate(self.provider)
        res = self.client.get(self.SCHEDULE_URL)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data'], [])

    def test_put_saves_slots(self):
        self.client.force_authenticate(self.provider)
        slots = [
            {'weekday': 0, 'start_minute': 0, 'end_minute': 360},
            {'weekday': 0, 'start_minute': 1080, 'end_minute': 1440},
            {'weekday': 6, 'start_minute': 720, 'end_minute': 1080},
        ]
        res = self.client.put(self.SCHEDULE_URL, {'slots': slots}, format='json')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(len(res.data['data']), 3)
        get_res = self.client.get(self.SCHEDULE_URL)
        self.assertEqual(len(get_res.data['data']), 3)

    def test_put_overwrites_existing(self):
        self.client.force_authenticate(self.provider)
        self.client.put(
            self.SCHEDULE_URL,
            {'slots': [{'weekday': 0, 'start_minute': 0, 'end_minute': 360}]},
            format='json',
        )
        res = self.client.put(
            self.SCHEDULE_URL,
            {'slots': [{'weekday': 1, 'start_minute': 360, 'end_minute': 720}]},
            format='json',
        )
        self.assertEqual(len(res.data['data']), 1)
        self.assertEqual(res.data['data'][0]['weekday'], 1)

    def test_put_clears_with_empty_list(self):
        self.client.force_authenticate(self.provider)
        self.client.put(
            self.SCHEDULE_URL,
            {'slots': [{'weekday': 0, 'start_minute': 0, 'end_minute': 360}]},
            format='json',
        )
        res = self.client.put(self.SCHEDULE_URL, {'slots': []}, format='json')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data'], [])

    def test_put_rejects_invalid_segment(self):
        self.client.force_authenticate(self.provider)
        res = self.client.put(
            self.SCHEDULE_URL,
            {'slots': [{'weekday': 0, 'start_minute': 0, 'end_minute': 100}]},
            format='json',
        )
        self.assertEqual(res.data['code'], 400)

    def test_put_rejects_invalid_weekday(self):
        self.client.force_authenticate(self.provider)
        res = self.client.put(
            self.SCHEDULE_URL,
            {'slots': [{'weekday': 7, 'start_minute': 0, 'end_minute': 360}]},
            format='json',
        )
        self.assertEqual(res.data['code'], 400)

    def test_put_rejects_non_list(self):
        self.client.force_authenticate(self.provider)
        res = self.client.put(self.SCHEDULE_URL, {'slots': 'bad'}, format='json')
        self.assertEqual(res.data['code'], 400)

    def test_put_dedupes_duplicate_slots(self):
        self.client.force_authenticate(self.provider)
        slots = [
            {'weekday': 0, 'start_minute': 0, 'end_minute': 360},
            {'weekday': 0, 'start_minute': 0, 'end_minute': 360},
        ]
        res = self.client.put(self.SCHEDULE_URL, {'slots': slots}, format='json')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(len(res.data['data']), 1)


class CheckinRewardHelperTest(TestCase):
    """签到纯函数：奖励档位取值 + 循环、连续天数计算。"""

    def test_reward_follows_configured_tiers(self):
        for seq, expected in enumerate(MONTHLY_CHECKIN_REWARDS, start=1):
            self.assertEqual(_reward_for_seq(seq), expected)

    def test_reward_cycles_after_last_tier(self):
        length = len(MONTHLY_CHECKIN_REWARDS)
        # 第 length+1 次回到首档
        self.assertEqual(_reward_for_seq(length + 1), MONTHLY_CHECKIN_REWARDS[0])
        self.assertEqual(_reward_for_seq(length + 2), MONTHLY_CHECKIN_REWARDS[1])

    def test_continuous_days_zero_when_today_unchecked(self):
        today = date(2026, 6, 15)
        self.assertEqual(_continuous_days({10, 11, 12}, today), 0)

    def test_continuous_days_counts_back_to_break(self):
        today = date(2026, 6, 15)
        # 13、14、15 连续，12 缺；连续 3 天
        self.assertEqual(_continuous_days({13, 14, 15, 10}, today), 3)

    def test_continuous_days_full_streak_from_month_start(self):
        today = date(2026, 6, 3)
        self.assertEqual(_continuous_days({1, 2, 3}, today), 3)


class CheckinTest(APITestCase):
    """老板每月签到接口。"""

    URL = '/api/users/checkin/'

    def setUp(self):
        self.user = make_user(role='CUSTOMER')
        # ¥188 按 1:10 换算为 1880 兴安币（内部账务值 18800）。
        make_order(self.user, amount=18800)

    def test_requires_authentication(self):
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 401)

    def test_provider_cannot_checkin(self):
        provider = make_provider()
        self.client.force_authenticate(provider)
        res = self.client.post(self.URL)
        self.assertEqual(res.data['code'], 403)

    def test_daily_spend_below_188_cannot_checkin(self):
        Order.objects.filter(customer=self.user).delete()
        make_order(self.user, amount=18790)
        self.client.force_authenticate(self.user)
        res = self.client.post(self.URL)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('还需消费', res.data['msg'])

    def test_daily_spend_388_grants_one_makeup_card_only_once(self):
        Order.objects.filter(customer=self.user).delete()
        make_order(self.user, amount=38800)
        self.client.force_authenticate(self.user)
        first = self.client.get(self.URL)
        second = self.client.get(self.URL)
        self.assertEqual(first.data['data']['makeup_cards'], 1)
        self.assertEqual(second.data['data']['makeup_cards'], 1)

    def test_makeup_card_inventory_is_capped_at_three(self):
        today = timezone.localdate()
        progress = CheckinMonthProgress.objects.create(
            user=self.user, year=today.year, month=today.month, makeup_cards=3,
        )
        Order.objects.filter(customer=self.user).delete()
        make_order(self.user, amount=38800)
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        progress.refresh_from_db()
        self.assertEqual(res.data['data']['makeup_cards'], 3)
        self.assertEqual(progress.makeup_cards, 3)

    @patch('users.views.timezone.localdate', return_value=date(2026, 7, 15))
    def test_makeup_consumes_card_and_checks_past_day(self, _mock_date):
        CheckinMonthProgress.objects.create(
            user=self.user, year=2026, month=7, makeup_cards=1,
        )
        self.client.force_authenticate(self.user)
        res = self.client.post(self.URL, {'day': 8})
        self.assertEqual(res.data['code'], 0)
        record = CheckinRecord.objects.get(user=self.user, checkin_date=date(2026, 7, 8))
        self.assertTrue(record.is_makeup)
        self.assertEqual(res.data['data']['makeup_cards'], 0)

    @patch('users.views._daily_paid_amount', return_value=18800)
    @patch('users.views.timezone.localdate', return_value=date(2026, 7, 31))
    def test_full_month_awards_kook_tag(self, _mock_date, _mock_spend):
        for day in range(1, 31):
            CheckinRecord.objects.create(
                user=self.user, checkin_date=date(2026, 7, day),
                seq_in_month=day, reward_amount=0,
            )
        self.client.force_authenticate(self.user)
        res = self.client.post(self.URL)
        self.assertEqual(res.data['code'], 0)
        self.assertTrue(res.data['data']['full_attendance_awarded'])
        progress = CheckinMonthProgress.objects.get(user=self.user, year=2026, month=7)
        self.assertEqual(progress.full_attendance_reward_name, 'KOOK专属Tag')

    @patch('users.views.timezone.localdate', return_value=date(2026, 8, 1))
    def test_new_month_starts_with_fresh_progress(self, _mock_date):
        CheckinMonthProgress.objects.create(
            user=self.user, year=2026, month=7, makeup_cards=3,
        )
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['data']['checked_count'], 0)
        self.assertEqual(res.data['data']['makeup_cards'], 0)

    def test_get_calendar_empty_by_default(self):
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        today = timezone.localdate()
        self.assertEqual(data['year'], today.year)
        self.assertEqual(data['month'], today.month)
        self.assertEqual(
            data['days_in_month'],
            calendar.monthrange(today.year, today.month)[1],
        )
        self.assertFalse(data['today_checked'])
        self.assertEqual(data['checked_days'], [])
        self.assertEqual(data['checked_count'], 0)
        self.assertEqual(data['continuous_days'], 0)
        self.assertEqual(data['next_reward'], MONTHLY_CHECKIN_REWARDS[0])

    def test_first_checkin_grants_first_tier_reward(self):
        make_wallet(self.user, balance=0)
        self.client.force_authenticate(self.user)
        res = self.client.post(self.URL)
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['reward_amount'], MONTHLY_CHECKIN_REWARDS[0])
        self.assertEqual(data['seq_in_month'], 1)
        self.assertEqual(data['balance'], MONTHLY_CHECKIN_REWARDS[0])

    def test_checkin_creates_reward_transaction(self):
        make_wallet(self.user, balance=1000)
        self.client.force_authenticate(self.user)
        self.client.post(self.URL)
        tx = Transaction.objects.get(
            wallet__user=self.user, tx_type=Transaction.TxType.REWARD
        )
        self.assertEqual(tx.amount, MONTHLY_CHECKIN_REWARDS[0])
        self.assertEqual(tx.balance_before, 1000)
        self.assertEqual(tx.balance_after, 1000 + MONTHLY_CHECKIN_REWARDS[0])

    def test_duplicate_checkin_today_rejected(self):
        make_wallet(self.user, balance=0)
        self.client.force_authenticate(self.user)
        self.client.post(self.URL)
        res = self.client.post(self.URL)
        self.assertEqual(res.data['code'], 400)
        self.assertEqual(
            CheckinRecord.objects.filter(user=self.user).count(), 1
        )

    def test_seq_increments_with_prior_month_records(self):
        """本月已有 2 条历史签到记录时，今日签到为第 3 次，取第三档。"""
        make_wallet(self.user, balance=0)
        today = timezone.localdate()
        # 预置本月两条早于今天的记录（避免与今天冲突）
        day_a = today.replace(day=1)
        day_b = today.replace(day=2)
        # 若今天恰为 1/2 号则错峰，保证不与今日相同
        existing = [d for d in (day_a, day_b) if d != today][:2]
        for i, d in enumerate(existing, start=1):
            CheckinRecord.objects.create(
                user=self.user, checkin_date=d, seq_in_month=i,
                reward_amount=MONTHLY_CHECKIN_REWARDS[i - 1],
            )
        self.client.force_authenticate(self.user)
        res = self.client.post(self.URL)
        self.assertEqual(res.data['code'], 0)
        expected_seq = len(existing) + 1
        self.assertEqual(res.data['data']['seq_in_month'], expected_seq)
        self.assertEqual(
            res.data['data']['reward_amount'],
            _reward_for_seq(expected_seq),
        )


class CustomerAchievementTest(APITestCase):
    """老板成就：按已完成订单实时计算解锁状态。"""

    URL = '/api/users/achievements/'

    def setUp(self):
        # 清空迁移预置的成就，保证用例独立于 seed 数据
        Achievement.objects.all().delete()
        self.user = make_user(role='CUSTOMER')

    def test_requires_authentication(self):
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 401)

    def test_empty_when_no_achievements_configured(self):
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data'], [])

    def test_orders_metric_unlocks_when_target_reached(self):
        make_achievement(
            code='first_order', title='初出茅庐',
            metric=Achievement.Metric.ORDERS, target=2,
        )
        make_order(self.user, status=Order.Status.COMPLETED)
        make_order(self.user, status=Order.Status.COMPLETED)
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        item = res.data['data'][0]
        self.assertTrue(item['unlocked'])
        self.assertEqual(item['current'], 2)
        self.assertEqual(item['target'], 2)

    def test_orders_metric_locked_below_target(self):
        make_achievement(metric=Achievement.Metric.ORDERS, target=3)
        make_order(self.user, status=Order.Status.COMPLETED)
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        item = res.data['data'][0]
        self.assertFalse(item['unlocked'])
        self.assertEqual(item['current'], 1)

    def test_amount_metric_sums_completed_orders(self):
        make_achievement(
            code='big_spender', title='一掷千金',
            metric=Achievement.Metric.AMOUNT, target=15000,
        )
        make_order(self.user, status=Order.Status.COMPLETED, amount=10000)
        make_order(self.user, status=Order.Status.COMPLETED, amount=8000)
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        item = res.data['data'][0]
        self.assertEqual(item['current'], 18000)
        self.assertTrue(item['unlocked'])

    def test_only_completed_orders_counted(self):
        make_achievement(metric=Achievement.Metric.ORDERS, target=1)
        make_order(self.user, status=Order.Status.PENDING)
        make_order(self.user, status=Order.Status.CANCELLED)
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        item = res.data['data'][0]
        self.assertEqual(item['current'], 0)
        self.assertFalse(item['unlocked'])

    def test_inactive_achievement_excluded(self):
        make_achievement(target=1, is_active=False)
        self.client.force_authenticate(self.user)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['data'], [])


@override_settings(WECHAT_MOCK_LOGIN=True)
class WechatLoginMockTest(APITestCase):
    """老板端微信登录（开发期 mock 分支）：仅服务 CUSTOMER 角色。"""

    URL = '/api/users/wechat-login/'

    def test_missing_code_rejected(self):
        res = self.client.post(self.URL, {}, format='json')
        self.assertEqual(res.data['code'], 400)

    def test_first_customer_login_creates_user(self):
        res = self.client.post(
            self.URL, {'code': 'wxcode0001'}, format='json'
        )
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertTrue(data['token'])
        self.assertEqual(data['userInfo']['role'], 'customer')
        self.assertEqual(data['userInfo']['bindStatus'], 'direct')
        user = User.objects.get(openid='wx_mock_wxcode0001')
        self.assertEqual(user.role, User.Role.CUSTOMER)

    def test_same_code_reuses_user(self):
        first = self.client.post(
            self.URL, {'code': 'wxcode0002'}, format='json'
        )
        second = self.client.post(
            self.URL, {'code': 'wxcode0002'}, format='json'
        )
        self.assertEqual(first.data['data']['userInfo']['id'],
                         second.data['data']['userInfo']['id'])
        self.assertEqual(
            User.objects.filter(openid='wx_mock_wxcode0002').count(), 1
        )

    def test_phone_code_fills_phone(self):
        res = self.client.post(
            self.URL,
            {'code': 'wxcode0004', 'phoneCode': 'pc'},
            format='json',
        )
        self.assertEqual(res.data['code'], 0)
        self.assertTrue(res.data['data']['userInfo']['phone'])
        user = User.objects.get(openid='wx_mock_wxcode0004')
        self.assertTrue(user.is_phone_verified)

    def test_non_customer_openid_rejected(self):
        # 该 openid 已属于陪玩账号，老板端登录应拒绝且不改写角色。
        make_user(role=User.Role.PROVIDER, openid='wx_mock_wxcode0009')
        res = self.client.post(
            self.URL, {'code': 'wxcode0009'}, format='json'
        )
        self.assertEqual(res.data['code'], 403)
        self.assertEqual(
            User.objects.get(openid='wx_mock_wxcode0009').role,
            User.Role.PROVIDER,
        )


@override_settings(WECHAT_MOCK_LOGIN=False)
class WechatLoginRealTest(APITestCase):
    """微信登录（生产分支）：patch 掉对微信服务器的 HTTP 调用。"""

    URL = '/api/users/wechat-login/'

    @patch('users.views._wechat_code2session', return_value='openid_real_1')
    def test_real_login_creates_user(self, _mock):
        res = self.client.post(
            self.URL, {'code': 'realcode'}, format='json'
        )
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(
            User.objects.filter(openid='openid_real_1').count(), 1
        )

    @patch('users.views._wechat_get_phone', return_value='13800001111')
    @patch('users.views._wechat_code2session', return_value='openid_real_2')
    def test_real_login_with_phone(self, _mock_session, _mock_phone):
        res = self.client.post(
            self.URL,
            {'code': 'realcode2', 'phoneCode': 'pc'},
            format='json',
        )
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['userInfo']['phone'], '13800001111')

    @patch(
        'users.views._wechat_code2session',
        side_effect=ValueError('微信登录失败'),
    )
    def test_real_login_failure_returns_400(self, _mock):
        res = self.client.post(
            self.URL, {'code': 'badcode'}, format='json'
        )
        self.assertEqual(res.data['code'], 400)


@override_settings(WECHAT_MOCK_LOGIN=True)
class AccountLoginMockTest(APITestCase):
    """陪玩端账号登录（开发期 mock 分支）：免密自动建陪玩号/登录。"""

    URL = '/api/users/account-login/'

    def test_missing_username_rejected(self):
        res = self.client.post(self.URL, {}, format='json')
        self.assertEqual(res.data['code'], 400)

    def test_first_login_creates_provider(self):
        res = self.client.post(
            self.URL, {'username': 'escort1'}, format='json'
        )
        self.assertEqual(res.data['code'], 0)
        self.assertTrue(res.data['data']['token'])
        info = res.data['data']['userInfo']
        self.assertEqual(info['role'], 'provider')
        self.assertEqual(info['backendRole'], 'PROVIDER')
        user = User.objects.get(username='escort1')
        self.assertEqual(user.role, User.Role.PROVIDER)
        self.assertEqual(user.escort_profile.display_name, 'escort1')
        self.assertEqual(user.escort_profile.status, EscortProfile.Status.OFFLINE)

    def test_same_username_reuses_user(self):
        first = self.client.post(
            self.URL, {'username': 'escort2'}, format='json'
        )
        second = self.client.post(
            self.URL, {'username': 'escort2'}, format='json'
        )
        self.assertEqual(first.data['data']['userInfo']['id'],
                         second.data['data']['userInfo']['id'])
        self.assertEqual(User.objects.filter(username='escort2').count(), 1)

    def test_non_provider_account_rejected(self):
        # 老板账号不能从陪玩端登录，且角色不被改写。
        make_user(role=User.Role.CUSTOMER, username='bossX')
        res = self.client.post(
            self.URL, {'username': 'bossX', 'password': 'pass1234'}, format='json'
        )
        self.assertEqual(res.data['code'], 403)
        self.assertEqual(User.objects.get(username='bossX').role, User.Role.CUSTOMER)


@override_settings(WECHAT_MOCK_LOGIN=False)
class AccountLoginRealTest(APITestCase):
    """陪玩端账号登录（生产分支）：走 Django authenticate 校验真实密码。"""

    URL = '/api/users/account-login/'

    def test_missing_password_rejected(self):
        make_user(role=User.Role.PROVIDER, username='realescort', password='secret123')
        res = self.client.post(
            self.URL, {'username': 'realescort'}, format='json'
        )
        self.assertEqual(res.data['code'], 400)

    def test_wrong_password_rejected(self):
        make_user(role=User.Role.PROVIDER, username='realescort2', password='secret123')
        res = self.client.post(
            self.URL,
            {'username': 'realescort2', 'password': 'wrong'},
            format='json',
        )
        self.assertEqual(res.data['code'], 400)

    def test_correct_password_logs_in(self):
        make_user(role=User.Role.PROVIDER, username='realescort3', password='secret123')
        res = self.client.post(
            self.URL,
            {'username': 'realescort3', 'password': 'secret123'},
            format='json',
        )
        self.assertEqual(res.data['code'], 0)
        self.assertTrue(res.data['data']['token'])

    def test_customer_account_rejected(self):
        make_user(role=User.Role.CUSTOMER, username='realboss', password='secret123')
        res = self.client.post(
            self.URL,
            {'username': 'realboss', 'password': 'secret123'},
            format='json',
        )
        self.assertEqual(res.data['code'], 403)
