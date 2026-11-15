from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from orders.addon_refund_execution import _main_pending_income
from orders.models import (AfterSale, GameCategory, OrderAddonRefund, OrderParticipationSegment,
                           ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_release_flow as flow, test_segment_reports as fixtures
from orders.tests.factories import make_service
from stores.db import transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False, ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
    CHANNEL_LAYERS={'default': {'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class AddonRefundTransferIncomeTests(APITransactionTestCase):
    setUp = fixtures.SegmentReportsTest.setUp
    login = fixtures.SegmentReportsTest.login
    ok = fixtures.SegmentReportsTest.ok
    recharge = fixtures.SegmentReportsTest.recharge
    new_order = fixtures.SegmentReportsTest.new_order
    replacement = fixtures.SegmentReportsTest.replacement
    transfer = fixtures.SegmentReportsTest.transfer
    prepare = fixtures.SegmentReportsTest.prepare

    def test_real_transfer_uses_each_persons_saved_participation_income(self):
        order, replacement, old = self.prepare()
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': incoming.pk, 'entry_image': flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        with transaction.atomic():
            self.assertEqual(_main_pending_income(order, self.accounts[self.provider.pk].pk), 3200)
            self.assertEqual(_main_pending_income(order, self.accounts[replacement.pk].pk), 4800)

    def test_addon_refund_uses_new_providers_actual_transfer_share(self):
        order, replacement, old = self.prepare()
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': incoming.pk, 'entry_image': flow.screenshot()}, format='multipart'))
        game = GameCategory.objects.create(name='转单测试游戏')
        self.service.game_category = game
        self.service.save(update_fields=['game_category'])
        addon = make_service(name='接替后加购', is_addon=True, game_category=game)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
            enabled=True, condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=10000)
        self.login(self.boss)
        path = f'/api/orders/orders/{order.pk}/addons/intents/'
        intent = self.ok(self.client.post(path, {'addon_id':addon.pk, 'unit':'HOUR', 'quantity':1,
            'configuration_version':1, 'provider_account_id':self.accounts[replacement.pk].pk,
            'request_id':'transferred-addon', 'expected_amount':10000,
            'gift_balance_amount':0, 'compensation_balance_amount':0}, format='json'))
        paid = self.ok(self.client.post(path + f"{intent['id']}/pay/", {}, format='json'))
        AfterSale.objects.create(order=order, account=self.accounts[self.boss.pk], reason='协商退加购',
                                 affected_provider_account_ids=[self.accounts[replacement.pk].pk])
        self.login(self.admin)
        staff_path = f"/api/admin/orders/{order.pk}/addons/payments/{paid['payment_id']}/refund-proposals/"
        proposal = self.ok(self.client.post(staff_path, {'request_id':'transferred-addon-refund',
            'amount_raw':10000, 'explanation':'双方确认退还加购'}, format='json'))
        user_path = f"/api/orders/orders/{order.pk}/addons/payments/{paid['payment_id']}/refund-proposals/{proposal['id']}/"
        for user in (self.boss, replacement):
            self.login(user)
            self.ok(self.client.post(user_path, {'accepted':True}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(staff_path + f"{proposal['id']}/execute/", {}, format='json'))
        refund = OrderAddonRefund.objects.get(payment_id=paid['payment_id'])
        self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction), (8000, 2000))
