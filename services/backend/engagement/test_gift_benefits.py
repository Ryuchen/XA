from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from django.utils import timezone
from rest_framework.test import APITestCase
from orders.tests.factories import make_user, make_order, make_service
from orders.models import ServiceCategory
from .tests import mapped
from .models import GiftBenefitRule, RewardItem, RewardClaim, MemberTag, Broadcast
from .services import award_gift_benefits, grant_entitlement, process_claim


class GiftBenefitTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.account = mapped(self.user)
        self.category = ServiceCategory.objects.create(name='中秋礼物', is_gift=True)
        self.service = make_service(service_category=self.category)
        self.reward = RewardItem.objects.create(code='gift-naming', name='永久冠名', kind='NAMING', in_shop=False)

    def order(self, amount):
        return make_order(self.user, service=self.service, customer_account=self.account,
            amount=amount, status='COMPLETED', payment_status='PAID')

    def test_strict_threshold_replay_and_once_per_order(self):
        GiftBenefitRule.objects.create(name='单次永久', reward=self.reward, threshold=131400, inclusive=False, enabled=True)
        award_gift_benefits(self.order(131400))
        self.assertFalse(RewardClaim.objects.exists())
        order = self.order(131401)
        award_gift_benefits(order); award_gift_benefits(order)
        claim = RewardClaim.objects.get()
        self.assertEqual(claim.source_order_id, order.pk)
        self.assertIsNone(claim.expires_at)
        self.assertEqual(Broadcast.objects.filter(kind='NAMING').count(), 1)

    def test_cumulative_category_threshold_grants_once_and_disabled_rules_do_nothing(self):
        self.reward.kind='TAG'; self.reward.duration_months=12; self.reward.save()
        rule = GiftBenefitRule.objects.create(name='活动累计', reward=self.reward, category=self.category,
            threshold=21000, inclusive=False, cumulative=True, once_per_account=True, enabled=False)
        award_gift_benefits(self.order(21000))
        self.assertFalse(RewardClaim.objects.exists())
        rule.enabled=True; rule.save()
        award_gift_benefits(self.order(100))
        award_gift_benefits(self.order(50000))
        claim = RewardClaim.objects.get()
        self.assertEqual(claim.duration_months, 12)
        self.assertEqual(claim.kind_snapshot, 'TAG')

    def test_calendar_month_expiry_and_tag_delivery_are_idempotent(self):
        self.reward.kind='TAG'; self.reward.duration_months=1; self.reward.highlighted=True; self.reward.save()
        claim=grant_entitlement(self.account, self.reward, 'vip:test:tag', 'VIP', {'tag': '我的专属标签'})
        now=datetime(2026, 1, 31, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        with patch('engagement.services.timezone.now', return_value=now):
            process_claim(claim.pk, 'COMPLETED', '已交付', None)
            process_claim(claim.pk, 'COMPLETED', '已交付', None)
        claim.refresh_from_db()
        self.assertEqual(timezone.localtime(claim.expires_at).date().isoformat(), '2026-02-28')
        tag=MemberTag.objects.get()
        self.assertTrue(tag.highlighted)
        self.assertEqual(tag.expires_at, claim.expires_at)
