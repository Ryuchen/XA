from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from coupons.models import VipMonthlyCutover
from coupons.monthly_cutover import initialize_new_store_rules
from stores.context import resolve_store
from stores.provisioning import _initialize_store_data


class VipFirstInstallTest(TestCase):
    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True)
    def test_store_initialization_records_boundary_once(self):
        store = resolve_store()
        store.initialized_at = None
        with patch('stores.provisioning.call_command'), patch('stores.provisioning.adapt_initial_branding'), \
             patch('stores.provisioning.has_usable_store_admin', return_value=True):
            _initialize_store_data(store, stdout=None, admin_username=None, password_env=None,
                                   admin_password=None, admin_phone=None, admin_nickname=None)
        row = VipMonthlyCutover.objects.get(pk=1)
        self.assertEqual((row.platform_actor_id, row.operation_id), ('store-initialization', f'initial:{store.slug}'))
        initialize_new_store_rules(store.slug)
        self.assertEqual(VipMonthlyCutover.objects.count(), 1)

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=False, VIP_MONTHLY_DECISIONS_ENABLED=False)
    def test_disabled_rules_do_not_change_existing_behavior(self):
        initialize_new_store_rules('test')
        self.assertFalse(VipMonthlyCutover.objects.exists())

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True)
    def test_existing_boss_prevents_guessing_issuance_history(self):
        ClubAccount.objects.create_account(username='existing-boss', account_type='BOSS')
        with self.assertRaises(ValidationError):
            initialize_new_store_rules('test')
        self.assertFalse(VipMonthlyCutover.objects.exists())
