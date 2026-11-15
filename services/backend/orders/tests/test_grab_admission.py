"""New-pool preliminary checks reuse real store eligibility and paid scope."""
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch
from types import SimpleNamespace

from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from orders.grab_admission import GrabAdmissionError, check_candidate
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.models import GameCategory, Order, OrderPaidServiceScope
from orders.paid_service_scope import capture, record
from orders.tests.factories import make_completed_order, make_escort_level, make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import RefundDepositRecoveryPermit, SystemConfig, get_platform_wallet
from wallet.services import get_wallet
from users.models import ProviderQualification


@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True,
                   PROVIDER_WECHAT_GATE_ENABLED=False)
class GrabAdmissionTest(TestCase):
    def setUp(self):
        self.user = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                  pass_expires_at=timezone.now() + timedelta(hours=1))
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.is_openid_bound = True
        self.account.openid = 'isolated-admission-test-openid'
        self.account.save(update_fields=['is_openid_bound', 'openid'])
        self.profile = self.user.escort_profile
        self.profile.account = self.account
        self.profile.wechat_avatar_verified_at = timezone.now()
        self.profile.save(update_fields=['account', 'wechat_avatar_verified_at'])
        self.level = make_escort_level(sort_order=10)
        self.profile.level = self.level; self.profile.save(update_fields=['level'])
        self.service = make_service(required_level=self.level)
        self.order = make_order(make_user(), service=self.service, payment_status='PAID')
        record(self.order, capture(self.service))
        save_grab_window(30, 0, lambda *args: None)
        save_capacity(2, 0, lambda *args: None)
        with transaction.atomic():
            self.pool = create_initial_snapshot(self.order.pk)

    def check(self):
        return check_candidate(self.pool.pk, self.account.pk)

    def test_wechat_binding_avatar_and_legacy_exemption_are_always_checked(self):
        self.assertEqual(self.check().entry_pass_tier, 'BLACK')
        self.account.is_openid_bound = False; self.account.save(update_fields=['is_openid_bound'])
        with self.assertRaisesMessage(GrabAdmissionError, '绑定本人微信'): self.check()
        self.account.is_openid_bound = True; self.account.save(update_fields=['is_openid_bound'])
        self.profile.wechat_avatar_verified_at = None; self.profile.save(update_fields=['wechat_avatar_verified_at'])
        with self.assertRaisesMessage(GrabAdmissionError, '微信获取头像'): self.check()
        self.profile.wechat_avatar_legacy_exempt = True; self.profile.save(update_fields=['wechat_avatar_legacy_exempt'])
        self.assertEqual(self.check().account_id, self.account.pk)

    def test_frozen_visibility_and_current_qualification_are_both_required(self):
        # Snapshot a no-card participant, then buy or upgrade after entry.
        other = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(other)
        account.is_openid_bound = True; account.openid = 'isolated-later-card'; account.save(update_fields=['is_openid_bound', 'openid'])
        profile = other.escort_profile
        profile.account = account; profile.level = self.level; profile.wechat_avatar_legacy_exempt = True
        profile.pass_tier = 'BLACK'; profile.pass_expires_at = timezone.now()+timedelta(hours=1)
        profile.save(update_fields=['account', 'level', 'wechat_avatar_legacy_exempt', 'pass_tier', 'pass_expires_at'])
        with self.assertRaisesMessage(GrabAdmissionError, '可见时间'):
            check_candidate(self.pool.pk, account.pk)
        with patch('orders.grab_admission.timezone') as clock:
            clock.now.return_value = self.pool.opened_at + timedelta(seconds=300)
            self.assertEqual(check_candidate(self.pool.pk, account.pk).entry_pass_tier, '')
        # Non-pass eligibility is still checked even before visibility time.
        profile.status = 'OFFLINE'; profile.save(update_fields=['status'])
        with self.assertRaisesMessage(GrabAdmissionError, '离线'):
            check_candidate(self.pool.pk, account.pk)
        # An active card at entry may expire after submission without making
        # an otherwise eligible participant fail the first-round recheck.
        self.profile.pass_tier = ''; self.profile.pass_expires_at = timezone.now() - timedelta(seconds=1)
        self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self.assertEqual(self.check().entry_pass_tier, 'BLACK')

    def test_paid_minimum_and_current_capacity_are_rechecked(self):
        # Removing the catalogue minimum must not broaden an already-paid order.
        self.service.required_level = None; self.service.save(update_fields=['required_level'])
        low = make_escort_level(sort_order=1)
        self.profile.level = low; self.profile.save(update_fields=['level'])
        with self.assertRaises(GrabAdmissionError): self.check()
        self.profile.level = self.level; self.profile.save(update_fields=['level'])
        for _ in range(2):
            make_order(make_user(), provider=self.user, provider_account=self.account,
                       status='IN_SERVICE', payment_status='PAID')
        with self.assertRaisesMessage(GrabAdmissionError, '接单名额已满'): self.check()

    def test_non_refund_cash_deposit_shortfall_still_blocks(self):
        self.profile.deposit_mode = 'CASH'; self.profile.deposit_paid = 0
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        with self.assertRaisesMessage(GrabAdmissionError, '押金未缴齐'): self.check()

    def test_game_qualification_and_service_skill_are_both_required(self):
        game = GameCategory.objects.create(name='隔离测试游戏')
        service = make_service(game_category=game, provider_kind='RANK', platform='PC',
                               security_level='ANY', provider_grade='OPEN')
        order = make_order(make_user(), service=service, payment_status='PAID')
        record(order, capture(service))
        with transaction.atomic(): pool = create_initial_snapshot(order.pk)
        with self.assertRaisesMessage(GrabAdmissionError, '未开通'):
            check_candidate(pool.pk, self.account.pk)
        ProviderQualification.objects.create(profile=self.profile, game_category=game, provider_kind='RANK',
                                             platform='PC', security_level='ANY', grade='OPEN')
        with self.assertRaisesMessage(GrabAdmissionError, '尚未配置该服务技能'):
            check_candidate(pool.pk, self.account.pk)
        self.profile.service_items.add(service)
        self.assertEqual(check_candidate(pool.pk, self.account.pk).account_id, self.account.pk)

    def test_unknown_scope_or_disabled_protection_never_falls_back_silently(self):
        OrderPaidServiceScope.objects.filter(order=self.order).delete()
        with self.assertRaisesMessage(GrabAdmissionError, '原服务范围'): self.check()
        record(self.order, capture(self.service))
        with self.settings(REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=False):
            with self.assertRaisesMessage(GrabAdmissionError, '缺口保护尚未启用'): self.check()
        SystemConfig.objects.filter(key='provider_capacity_rules').delete()
        with self.assertRaisesMessage(GrabAdmissionError, '接单名额尚未确认'): self.check()

    def test_order_and_account_changes_are_not_hidden_by_pass_snapshot(self):
        self.account.can_login = False; self.account.save(update_fields=['can_login'])
        with self.assertRaisesMessage(GrabAdmissionError, '停用'): self.check()
        self.account.can_login = True; self.account.save(update_fields=['can_login'])
        Order.objects.filter(pk=self.order.pk).update(status='CANCELLED')
        with self.assertRaisesMessage(GrabAdmissionError, '离开自动抢单池'): self.check()

    def test_inconsistent_paid_headcount_cannot_be_accepted(self):
        Order.objects.filter(pk=self.order.pk).update(required_provider_count=2)
        with self.assertRaisesMessage(GrabAdmissionError, '人数快照不一致'): self.check()

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_refund_gap_can_enter_but_existing_recovery_permit_blocks(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        self.profile.deposit_paid = 10000; self.profile.save(update_fields=['deposit_paid'])
        platform = get_platform_wallet(); platform.balance = 10000; platform.save(update_fields=['balance'])
        prior = make_completed_order(make_user(), self.user, amount=10000,
                                     provider_account=self.account, payment_status='PAID')
        fixture = SimpleNamespace(profile=self.profile, wallet=get_wallet(account=self.account, user=self.user),
                                  account=self.account, platform=platform, order=prior, assertEqual=self.assertEqual)
        RefundDepositDeficitTest.deficit(fixture, 200)
        self.assertEqual(self.check().account_id, self.account.pk)
        self.assertFalse(RefundDepositRecoveryPermit.objects.exists())  # A hint cannot consume the permit.
        existing = make_order(make_user(), provider=self.user, provider_account=self.account,
                              status='GRABBED', payment_status='PAID')
        RefundDepositRecoveryPermit.objects.create(profile=self.profile, account=self.account, order=existing)
        with self.assertRaisesMessage(GrabAdmissionError, '补差订单尚未完成结算'): self.check()


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
class GrabAdmissionMultiStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_ids_check_the_current_store_profile(self):
        stores, ids = (resolve_store(), xinan_store()), []
        for store, status in zip(stores, ('AVAILABLE', 'OFFLINE')):
            with store_context(store):
                save_capacity(2, 0, lambda *args: None)
                save_grab_window(30, 0, lambda *args: None)
                user = make_provider(deposit_mode='EARNINGS', status=status, pass_tier='BLACK',
                                     pass_expires_at=timezone.now()+timedelta(hours=1))
                account = get_or_create_account_for_legacy_user(user)
                account.is_openid_bound = True; account.openid = 'isolated-store-openid'
                account.save(update_fields=['is_openid_bound', 'openid'])
                profile = user.escort_profile; profile.account = account; profile.wechat_avatar_legacy_exempt = True
                profile.save(update_fields=['account', 'wechat_avatar_legacy_exempt'])
                service = make_service(); order = make_order(make_user(), service=service, payment_status='PAID')
                record(order, capture(service))
                with transaction.atomic(): pool = create_initial_snapshot(order.pk)
                ids.append((pool.pk, account.pk))
        self.assertEqual(ids[0], ids[1])
        with store_context(stores[0]):
            self.assertEqual(check_candidate(*ids[0]).account_id, ids[0][1])
        with store_context(stores[1]):
            with self.assertRaisesMessage(GrabAdmissionError, '离线'): check_candidate(*ids[0])
