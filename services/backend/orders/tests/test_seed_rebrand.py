"""New seed labels and compatibility with existing, unmodified records.

These commands create data and one of them deletes demo-prefixed users. Run this
module only in an explicitly guarded disposable xa_test MySQL database.
"""
import io
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from coupons.models import Coupon
from engagement.models import LotteryCampaign
from common.mysql_test_support import require_disposable_mysql


@skipUnless(connection.vendor == 'mysql', 'Requires the isolated container MySQL test database')
class SeedRebrandTest(TestCase):
    def setUp(self):
        super().setUp()
        require_disposable_mysql(connection)
        media = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=media))

    def seed_stage(self):
        call_command('seed_stage_test_data', customers=1, providers=1, stdout=io.StringIO())

    def seed_engagement(self):
        # The command normally admits only the persistent xa_verify database.
        # Override that guard only after verifying this is the isolated test DB.
        with patch('orders.management.commands.seed_club_catalog.validate_import_database'):
            call_command('seed_club_engagement', apply=True, stdout=io.StringIO())

    def test_stage_seed_creates_new_names_in_a_clean_database(self):
        self.seed_stage()
        self.assertTrue(Coupon.objects.filter(name='联调无门槛100浪花券', amount=1000).exists())
        self.assertTrue(Coupon.objects.filter(name='联调满500减80浪花券', threshold=5000, amount=800).exists())
        self.assertFalse(Coupon.objects.filter(name__in=['联调无门槛100兴安币券', '联调满500减80兴安币券']).exists())

    def test_stage_seed_reuses_existing_legacy_coupons_without_renaming(self):
        first = Coupon.objects.create(name='联调无门槛100兴安币券', amount=1000, discount_type='DIRECT')
        second = Coupon.objects.create(name='联调满500减80兴安币券', amount=800, threshold=5000)
        self.seed_stage()
        self.assertEqual(Coupon.objects.get(pk=first.pk).name, '联调无门槛100兴安币券')
        self.assertEqual(Coupon.objects.get(pk=second.pk).name, '联调满500减80兴安币券')
        self.assertFalse(Coupon.objects.filter(name__in=['联调无门槛100浪花券', '联调满500减80浪花券']).exists())
        self.assertEqual(Coupon.objects.filter(pk__in=[first.pk, second.pk]).count(), 2)

    def test_engagement_seed_creates_new_campaign_and_reuses_it(self):
        self.seed_engagement()
        self.seed_engagement()
        self.assertEqual(LotteryCampaign.objects.filter(name='声浪幸运转盘').count(), 1)
        self.assertFalse(LotteryCampaign.objects.filter(name='兴安幸运转盘').exists())

    def test_engagement_seed_reuses_legacy_campaign_without_renaming(self):
        legacy = LotteryCampaign.objects.create(name='兴安幸运转盘', point_cost=123, enabled=False)
        self.seed_engagement()
        self.seed_engagement()
        legacy.refresh_from_db()
        self.assertEqual((legacy.name, legacy.point_cost), ('兴安幸运转盘', 123))
        self.assertEqual(LotteryCampaign.objects.filter(name='兴安幸运转盘').count(), 1)
        self.assertFalse(LotteryCampaign.objects.filter(name='声浪幸运转盘').exists())
