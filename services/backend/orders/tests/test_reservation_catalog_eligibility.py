"""Reservation creation follows the existing catalog and skill contracts."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from orders.models import GameCategory, Gameplay, Reservation, ServiceItem
from orders.tests.factories import make_service_category
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase, slot
from users.models import EscortSchedule, ProviderQualification


@override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False)
class ReservationCatalogEligibilityTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        from club_accounts.tokens import issue_account_tokens
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])

    def post(self):
        return self.client.post(LIST_URL, self.create_payload(), format='json').data

    def test_non_purchasable_products_do_not_hold_a_future_slot(self):
        gameplay = Gameplay.objects.create(name='预约玩法', code='reservation-catalog')
        game = GameCategory.objects.create(name='预约游戏')
        self.service.gameplay = gameplay
        self.service.game_category = game
        self.service.platform = 'PC'
        self.service.security_level = 'CONFIDENTIAL'
        self.service.save()
        self.provider.escort_profile.service_items.add(self.service)
        for change, expected in [({'purchase_mode': 'CONSULT'}, '联系客服'),
                                 ({'purchase_mode': 'DIRECT', 'is_addon': True}, '主订单')]:
            for key, value in change.items():
                setattr(self.service, key, value)
            self.service.save()
            result = self.post()
            self.assertNotEqual(result['code'], 0, result)
            self.assertIn(expected, result['msg'])
            self.assertFalse(Reservation.objects.exists())
        self.service.is_addon = False
        self.service.service_category = make_service_category(is_gift=True)
        self.service.save()
        self.assertIn('礼物', self.post()['msg'])
        self.service.service_category = None
        self.service.save()
        gameplay.is_active = False
        gameplay.save(update_fields=['is_active'])
        self.assertIn('玩法', self.post()['msg'])
        self.assertFalse(Reservation.objects.exists())

    def test_skills_qualifications_and_offline_future_booking(self):
        game = GameCategory.objects.create(name='预约技能游戏')
        self.service.game_category = game
        self.service.provider_kind = 'GUARD'
        self.service.provider_grade = 'MASTER'
        self.service.platform = 'PC'
        self.service.security_level = 'CONFIDENTIAL'
        self.service.save()
        profile = self.provider.escort_profile
        profile.service_items.add(self.service)
        self.assertIn('资格', self.post()['msg'])
        qualification = ProviderQualification.objects.create(profile=profile, game_category=game,
            provider_kind='GUARD', platform='PC', security_level='CONFIDENTIAL', grade='MASTER')
        profile.service_items.clear()
        self.assertIn('技能', self.post()['msg'])
        self.assertFalse(Reservation.objects.exists())
        profile.service_items.add(self.service)
        profile.status = profile.Status.OFFLINE
        profile.save(update_fields=['status'])
        start = timezone.localtime(slot(hour=10))
        start_minute = start.hour * 60 + start.minute
        EscortSchedule.objects.create(provider=self.provider, account=self.provider_account,
            weekday=start.weekday(), start_minute=start_minute, end_minute=start_minute + 120)
        result = self.post()
        self.assertNotEqual(result['code'], 0, result)
        self.assertIn('离线', result['msg'])
        self.assertFalse(Reservation.objects.exists())
        profile.status = profile.Status.AVAILABLE
        profile.save(update_fields=['status'])
        result = self.post()
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(result['data']['status'], 'PENDING')
        self.assertTrue(qualification.is_active)

    def test_unverified_provider_cannot_hold_a_reservation(self):
        profile = self.provider.escort_profile
        profile.is_verified = False
        profile.save(update_fields=['is_verified'])
        result = self.post()
        self.assertNotEqual(result['code'], 0, result)
        self.assertIn('认证', result['msg'])
        self.assertFalse(Reservation.objects.exists())

    def test_catalog_change_after_initial_validation_cannot_hold_slot(self):
        from orders.gameplay import validate_service_gameplay
        for change in ({'is_active': False}, {'billing_provider_count': 2}, {'is_addon': True}):
            calls = []
            def update_after_read(service, rounds=1, **kwargs):
                if not calls:
                    ServiceItem.objects.filter(pk=service.pk).update(**change)
                calls.append(True)
                return validate_service_gameplay(service, rounds, **kwargs)
            with patch('orders.gameplay.validate_service_gameplay', side_effect=update_after_read):
                result = self.post()
            self.assertNotEqual(result['code'], 0, result)
            self.assertFalse(Reservation.objects.exists())
            ServiceItem.objects.filter(pk=self.service.pk).update(
                is_active=True, billing_provider_count=1, is_addon=False)
