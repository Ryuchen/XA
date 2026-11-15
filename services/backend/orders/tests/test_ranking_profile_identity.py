from django.utils import timezone
from rest_framework.test import APITestCase
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderOriginalAttribution, OrderParticipationSegment
from .factories import make_user, make_provider, make_order


class RankingProfileIdentityTests(APITestCase):
    def setUp(self):
        for index in range(3):
            ClubAccount.objects.create_account(username=f'ranking-offset-{index}', account_type='STAFF')
        self.boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.provider = make_provider()
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        profile = self.provider.escort_profile
        profile.account = self.provider_account
        profile.save(update_fields=['account'])
        now = timezone.now()
        order = make_order(self.boss, provider=self.provider, provider_account=self.provider_account,
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            original_amount=10000, completed_at=now, in_service_at=now)
        attribution = OrderOriginalAttribution.objects.create(order=order, original_amount=10000, status='ACTIVE')
        OrderParticipationSegment.objects.create(attribution=attribution, provider_account=self.provider_account,
            slot=1, original_share=10000, accepted_at=now, started_at=now)
        token, _ = issue_account_tokens(boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_rank_navigation_uses_business_account_not_legacy_user_id(self):
        self.assertNotEqual(self.provider.id, self.provider_account.id)
        row = self.client.get('/api/orders/rankings/').data['data']['order_rank'][0]
        self.assertEqual(row['user_id'], self.provider.id)
        self.assertEqual(row['provider_account_id'], self.provider_account.id)
        detail = self.client.get(f'/api/users/escorts/{row["provider_account_id"]}/')
        self.assertEqual(detail.data['code'], 0)

    def test_hidden_provider_has_no_public_profile_navigation_target(self):
        self.provider_account.can_view = False
        self.provider_account.save(update_fields=['can_view'])
        row = self.client.get('/api/orders/rankings/').data['data']['order_rank'][0]
        self.assertIsNone(row['provider_account_id'])
