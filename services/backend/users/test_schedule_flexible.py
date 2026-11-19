"""Real provider schedule API, cross-midnight eligibility, and tenant isolation."""
import os
from datetime import datetime
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import EscortSchedule


URL = '/api/users/escorts/schedule/'


def local(year, month, day, hour, minute):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def provider_with_token(key):
    user = make_provider(deposit_mode='EARNINGS', wechat_avatar_legacy_exempt=True)
    account = get_or_create_account_for_legacy_user(user)
    account.openid, account.is_openid_bound = f'schedule-{key}', True
    account.save(update_fields=['openid', 'is_openid_bound'])
    profile = user.escort_profile
    profile.account = account
    profile.save(update_fields=['account'])
    return user, account, profile, issue_account_tokens(account)[0]


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=True, ALLOWED_HOSTS=['*'])
class FlexibleScheduleTest(TransactionTestCase):
    def setUp(self):
        self.user, self.account, self.profile, token = provider_with_token('main')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def save(self, slots):
        return self.client.put(URL, {'slots': slots}, format='json', HTTP_HOST='sl.esports.club')

    def test_overnight_roundtrip_and_both_cached_and_database_eligibility(self):
        slot = {'weekday': 0, 'start_minute': 1110, 'end_minute': 120}
        response = self.save([slot])
        self.assertEqual((response.data['code'], response.data['data']), (0, [slot]))
        self.assertEqual(self.client.get(URL, HTTP_HOST='sl.esports.club').data['data'], [slot])
        times = [(local(2026, 11, 9, 18, 29), False), (local(2026, 11, 9, 18, 30), True),
                 (local(2026, 11, 9, 23, 59), True), (local(2026, 11, 10, 0, 0), True),
                 (local(2026, 11, 10, 1, 59), True), (local(2026, 11, 10, 2, 0), False),
                 (local(2026, 11, 9, 1, 0), False)]
        cached_user = type(self.user).objects.prefetch_related('schedules').get(pk=self.user.pk)
        for instant, expected in times:
            with self.subTest(instant=instant):
                with self.assertNumQueries(1):
                    self.assertEqual(EscortSchedule.provider_is_scheduled_now(self.user, instant), expected)
                self.assertEqual(EscortSchedule.provider_is_scheduled_now(cached_user, instant), expected)

    def test_week_wrap_midnight_and_default_unrestricted_still_require_online_and_binding(self):
        now = local(2026, 11, 9, 0, 15)  # Monday follows Sunday.
        self.assertTrue(EscortSchedule.provider_is_scheduled_now(self.user, now))
        self.assertEqual(self.save([{'weekday': 6, 'start_minute': 1380, 'end_minute': 30}]).data['code'], 0)
        self.assertTrue(EscortSchedule.provider_is_scheduled_now(self.user, now))
        self.assertFalse(EscortSchedule.provider_is_scheduled_now(self.user, local(2026, 11, 9, 0, 30)))
        self.assertEqual(self.save([]).data['code'], 0)
        with patch('django.utils.timezone.now', return_value=now):
            self.assertEqual(self.profile.order_acceptance_error(), '')
            self.profile.status = 'OFFLINE'
            self.assertIn('离线', self.profile.order_acceptance_error())
            self.profile.status = 'AVAILABLE'
            self.account.is_openid_bound = False
            self.account.save(update_fields=['is_openid_bound'])
            self.profile._state.fields_cache.pop('account', None)
            self.assertIn('绑定本人微信', self.profile.order_acceptance_error())

    def test_overlapping_slots_preserve_each_input_and_match_their_union(self):
        slots = [{'weekday': 0, 'start_minute': 1080, 'end_minute': 1320},
                 {'weekday': 0, 'start_minute': 1200, 'end_minute': 120}]
        self.assertEqual(self.save(slots).data['data'], slots)
        self.assertTrue(EscortSchedule.provider_is_scheduled_now(self.user, local(2026, 11, 9, 19, 0)))
        self.assertTrue(EscortSchedule.provider_is_scheduled_now(self.user, local(2026, 11, 10, 1, 0)))
        self.assertFalse(EscortSchedule.provider_is_scheduled_now(self.user, local(2026, 11, 10, 2, 0)))

    def test_boss_picker_uses_the_actual_overnight_eligibility(self):
        self.assertEqual(self.save([{'weekday': 0, 'start_minute': 1110, 'end_minute': 120}]).data['code'], 0)
        service = make_service()
        boss = get_or_create_account_for_legacy_user(make_user())
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
        for instant, visible in ((local(2026, 11, 10, 1, 59), True),
                                 (local(2026, 11, 10, 2, 0), False)):
            with self.subTest(instant=instant), patch('django.utils.timezone.now', return_value=instant):
                response = self.client.get('/api/users/escorts/', {'picker': '1', 'service_item': service.pk},
                                           HTTP_HOST='sl.esports.club')
                self.assertEqual(response.data['code'], 0, response.data)
                self.assertEqual(self.account.pk in [item['id'] for item in response.data['data']], visible)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '通行证退款校验/语音文件审计门店要求/弹性档期抢单窗口/通行证退款多店隔离属正式契约深水区（18-盘点 通行证/语音审计/抢单/多店隔离待联调）；最终联调启用')
    def test_real_grab_api_checks_overnight_end_before_assigning(self):
        self.assertEqual(self.save([{'weekday': 0, 'start_minute': 1110, 'end_minute': 120}]).data['code'], 0)
        order = make_order(make_user(), service=make_service(), payment_status='PAID')
        Order.objects.filter(pk=order.pk).update(pool_entered_at=local(2026, 11, 10, 1, 0))
        with patch('django.utils.timezone.now', return_value=local(2026, 11, 10, 2, 0)):
            denied = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json',
                                      HTTP_HOST='sl.esports.club')
        self.assertNotEqual(denied.data['code'], 0, denied.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('PENDING', None))
        with patch('django.utils.timezone.now', return_value=local(2026, 11, 10, 1, 59)):
            accepted = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json',
                                        HTTP_HOST='sl.esports.club')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', self.account.pk))

    def test_bad_payload_does_not_erase_existing_schedule(self):
        valid = {'weekday': 1, 'start_minute': 123, 'end_minute': 456}
        self.assertEqual(self.save([valid]).data['code'], 0)
        bad_slots = [
            {'weekday': 0, 'start_minute': 100, 'end_minute': 100},
            {'weekday': 0, 'start_minute': 1440, 'end_minute': 100},
            {'weekday': 0, 'start_minute': 100, 'end_minute': 1441},
            {'weekday': True, 'start_minute': 100, 'end_minute': 200},
            {'weekday': 0, 'start_minute': 1.5, 'end_minute': 200},
            {'weekday': 7, 'start_minute': 100, 'end_minute': 200},
        ]
        for bad in bad_slots:
            with self.subTest(bad=bad):
                self.assertEqual(self.save([valid, bad]).data['code'], 400)
                self.assertEqual(self.client.get(URL, HTTP_HOST='sl.esports.club').data['data'], [valid])

    def test_existing_legacy_slots_are_visible_and_replaced_without_touching_another_provider(self):
        old = EscortSchedule.objects.create(provider=self.user, account=None, weekday=0,
                                            start_minute=0, end_minute=360)
        other, other_account, _, _ = provider_with_token('other')
        other_slot = EscortSchedule.objects.create(provider=other, account=other_account, weekday=0,
                                                   start_minute=360, end_minute=720)
        self.assertEqual(self.client.get(URL, HTTP_HOST='sl.esports.club').data['data'], [
            {'weekday': 0, 'start_minute': 0, 'end_minute': 360}])
        replacement = {'weekday': 2, 'start_minute': 1110, 'end_minute': 120}
        self.assertEqual(self.save([replacement]).data['data'], [replacement])
        self.assertFalse(EscortSchedule.objects.filter(pk=old.pk).exists())
        self.assertTrue(EscortSchedule.objects.filter(pk=other_slot.pk).exists())

    def test_failure_during_replacement_rolls_back_the_old_schedule(self):
        old = {'weekday': 0, 'start_minute': 0, 'end_minute': 360}
        self.assertEqual(self.save([old]).data['code'], 0)
        with patch('users.views.EscortSchedule.objects.bulk_create', side_effect=IntegrityError('isolated failure')):
            with self.assertRaises(IntegrityError):
                self.save([{'weekday': 0, 'start_minute': 1110, 'end_minute': 120}])
        self.assertEqual(self.client.get(URL, HTTP_HOST='sl.esports.club').data['data'], [old])


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class FlexibleScheduleStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_number_profiles_and_schedule_writes_are_store_isolated(self):
        client, entries = APIClient(), []
        for store, host, start in ((resolve_store(), 'sl.esports.club', 1110),
                                   (xinan_store(), 'xa.esports.club', 1200)):
            with store_context(store):
                _, account, _, token = provider_with_token(store.slug)
                account_id = account.pk
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            slot = {'weekday': 0, 'start_minute': start, 'end_minute': 120}
            response = client.put(URL, {'slots': [slot]}, format='json', HTTP_HOST=host)
            self.assertEqual((response.data['code'], response.data['data']), (0, [slot]))
            entries.append((host, token, account_id, slot))
        self.assertEqual(entries[0][2], entries[1][2])
        for host, token, _, slot in entries:
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            self.assertEqual(client.get(URL, HTTP_HOST=host).data['data'], [slot])
            other = entries[1 if host == entries[0][0] else 0][0]
            self.assertNotEqual(client.get(URL, HTTP_HOST=other).status_code, 200)
