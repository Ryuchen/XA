"""Reservation creation follows the existing catalog and skill contracts."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from orders.models import GameCategory, Gameplay, Reservation
from orders.tests.factories import make_service_category
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase
from users.models import EscortSchedule, ProviderQualification


@override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationCatalogEligibilityTest(ReservationApiTestBase):
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
        now = timezone.localtime()
        EscortSchedule.objects.create(provider=self.provider, account=self.provider_account,
            weekday=(now.weekday() + 1) % 7, start_minute=0, end_minute=60)
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
