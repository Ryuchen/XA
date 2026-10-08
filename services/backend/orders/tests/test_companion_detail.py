"""Public companion details use live roster identity, metrics and eligibility."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_service, make_user
from users.models import EscortLevel


@override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CompanionDetailTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.client.force_authenticate(self.boss)
        self.service = make_service()
        user = make_provider(display_name='听风', gender='FEMALE', bio='一起开心开黑',
                             pass_tier='BLACK', pass_expires_at=timezone.now() + timedelta(days=1))
        self.profile = user.escort_profile
        self.profile.account = get_or_create_account_for_legacy_user(user)
        self.profile.save(update_fields=['account'])
        self.profile.service_items.add(self.service)
        self.url = f'/api/users/escorts/{self.profile.account_id}/'

    def detail(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def test_detail_matches_public_card_identity_and_refreshes_monthly_amount(self):
        level = EscortLevel.objects.create(name='高级陪玩', sort_order=5, commission_rate=20)
        self.profile.level = level
        self.profile.save(update_fields=['level'])
        row = self.detail(service_item=self.service.pk, gender='FEMALE')
        self.assertEqual(row['id'], self.profile.account_id)
        self.assertEqual(row['nickname'], '听风')
        self.assertEqual(row['level_name'], '高级陪玩')
        self.assertEqual(row['bio'], '一起开心开黑')
        self.assertTrue(row['can_order'])
        self.assertNotIn('active_pass_tier', row)
        self.assertEqual(row['monthly_order_amount'], 0)
        order = make_order(self.boss, service=self.service, provider=self.profile.user,
                           customer_account=self.boss_account, provider_account=self.profile.account,
                           status='COMPLETED', payment_status='PAID', amount=2050)
        self.assertEqual(self.detail()['monthly_order_amount'], 2050)
        Order.objects.filter(pk=order.pk).update(payment_status='REFUNDED')
        self.assertEqual(self.detail()['monthly_order_amount'], 0)

    def test_unavailable_and_busy_profiles_remain_readable_with_selection_reason(self):
        for status in ('OFFLINE', 'BUSY'):
            with self.subTest(status=status):
                self.profile.status = status
                self.profile.save(update_fields=['status'])
                row = self.detail(service_item=self.service.pk)
                self.assertEqual(row['nickname'], '听风')
                self.assertFalse(row['can_order'])
                self.assertTrue(row['order_unavailable_reason'])

    def test_gender_and_removed_service_skill_block_selection_without_hiding_detail(self):
        row = self.detail(service_item=self.service.pk, gender='MALE')
        self.assertFalse(row['can_order'])
        self.assertIn('性别', row['order_unavailable_reason'])
        self.profile.service_items.clear()
        row = self.detail(service_item=self.service.pk)
        self.assertFalse(row['can_order'])
        self.assertIn('技能', row['order_unavailable_reason'])

    def test_capacity_is_current_and_other_profiles_are_not_used(self):
        for _ in range(Order.MAX_CONCURRENT_ORDERS):
            make_order(self.boss, service=self.service, provider=self.profile.user,
                       customer_account=self.boss_account, provider_account=self.profile.account,
                       status='GRABBED', payment_status='PAID')
        row = self.detail(service_item=self.service.pk)
        self.assertFalse(row['can_order'])
        self.assertIn('接单已满', row['order_unavailable_reason'])

    def test_hidden_disabled_unverified_and_missing_profiles_are_not_exposed(self):
        for field in ('can_view', 'can_login', 'is_active'):
            with self.subTest(field=field):
                setattr(self.profile.account, field, False)
                self.profile.account.save(update_fields=[field])
                self.assertEqual(self.client.get(self.url).status_code, 404)
                setattr(self.profile.account, field, True)
                self.profile.account.save(update_fields=[field])
        self.profile.is_verified = False
        self.profile.save(update_fields=['is_verified'])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get('/api/users/escorts/999999999/').status_code, 404)

    def test_detail_requires_login_and_valid_current_service(self):
        self.assertEqual(self.client.get(self.url, {'gender': 'INVALID'}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {'service_item': 'invalid'}).status_code, 400)
        self.service.is_active = False
        self.service.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.url, {'service_item': self.service.pk}).status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
