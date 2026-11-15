from django.test import override_settings
from unittest.mock import patch

from engagement.models import ConsumptionConfirmation
from orders.models import (GameCategory, OrderAddonConsumption, OrderSegmentAddonSettlement,
                           OrderParticipationSegment, ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_ordinary_transfer_funding as fixture
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_service
from stores.context import store_context
from stores.models import Store
from wallet.models import Transaction


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                   ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_SEGMENT_SETTLEMENT_INTERNAL_TESTING=True,
                   ADDON_HANDOVER_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                   ADDON_SEGMENT_REFUND_REVISION_INTERNAL_TESTING=True)
class SegmentAddonSettlementTest(fixture.OrdinaryTransferFundingTest):
    def completed_addon_after_transfer(self, *, addon_compensation=0):
        order, replacement, old = self.prepare()
        segment = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': segment.pk, 'entry_image': flow.screenshot()}, format='multipart'))
        game = GameCategory.objects.create(name='转单加购结算游戏')
        self.service.game_category = game
        self.service.save(update_fields=['game_category'])
        addon = make_service(name='转单后加购', is_addon=True, game_category=game)
        option = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
            enabled=True, condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=option, unit='HOUR', price=5000)
        if addon_compensation:
            from club_accounts.services import get_or_create_account_for_legacy_user
            from orders.tests.factories import make_console_user
            from wallet.services import get_wallet
            staff = make_console_user(['wallet:adjust'])
            account = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = account
            staff.admin_membership.save(update_fields=['account'])
            self.accounts[staff.pk] = account
            self.login(staff)
            wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
            self.ok(self.client.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {
                'amount': addon_compensation, 'balance_kind': 'COMPENSATION', 'remark': '售后赔付',
                'client_request_id': 'addon-compensation'}, format='json'))
        self.login(self.boss)
        path = f'/api/orders/orders/{order.pk}/addons/intents/'
        intent = self.ok(self.client.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': self.accounts[replacement.pk].pk,
            'request_id': 'segment-addon', 'expected_amount': 5000,
            'gift_balance_amount': 0, 'compensation_balance_amount': addon_compensation}, format='json'))
        paid = self.ok(self.client.post(path + f"{intent['id']}/pay/", {}, format='json'))
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        return order, replacement, old, segment, paid['payment_id']

    def test_real_transfer_then_addon_settles_and_confirms_buyer_consumption(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        with store_context(Store.objects.get(slug='shenglang')):
            from engagement.confirmation import process_order
            self.assertFalse(process_order(order.pk))
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
            self.assertFalse(OrderSegmentAddonSettlement.objects.exists())
            self.submit_and_approve(order, segment, replacement)
            self.mature(order)
            with patch('orders.segment_addon_settlement.OrderSegmentAddonSettlement.objects.create',
                       side_effect=RuntimeError('加购结算凭证写入失败')):
                with self.assertRaisesMessage(RuntimeError, '加购结算凭证写入失败'):
                    process_order(order.pk)
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
            self.assertFalse(OrderSegmentAddonSettlement.objects.exists())
            self.assertTrue(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
            result = OrderSegmentAddonSettlement.objects.get(payment_id=payment_id)
            self.assertEqual((result.segment_id, result.amount, result.income_transaction.amount),
                             (segment.pk, 4000, 4000))
            order.refresh_from_db()
            self.assertEqual((order.provider_income, order.shop_income), (8000, 2000))
            self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 10000)
            self.assertEqual(OrderAddonConsumption.objects.get(payment_id=payment_id).paid_units, 5000)
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 3)
            self.login(replacement)
            history = self.ok(self.client.get('/api/wallet/transactions/'))['transactions']
            line = next(item for item in history if item['id'] == result.income_transaction_id)
            self.assertEqual((line['order_id'], line['addon']['payment_id']), (order.pk, payment_id))

    def test_addon_refund_after_other_player_settles_keeps_prior_income(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.login(self.boss)
        case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '加购部分未履行', 'affected_provider_account_ids': [self.accounts[replacement.pk].pk],
            'client_request_id': 'segment-addon-case'}, format='json'))
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        with store_context(Store.objects.get(slug='shenglang')):
            from engagement.confirmation import process_order
            self.assertFalse(process_order(order.pk))
            first = Transaction.objects.get(order=order, tx_type='INCOME')
            self.assertEqual(first.amount, 3200)
            self.login(self.admin)
            staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            proposal = self.ok(self.client.post(staff_url, {'request_id': 'partial-addon-after-first',
                'amount_raw': 1000, 'explanation': '双方确认部分退款', 'case_id': case['id']}, format='json'))
            user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal["id"]}/'
            for user in (self.boss, replacement):
                self.login(user)
                self.ok(self.client.post(user_url, {'accepted': True}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(staff_url + f'{proposal["id"]}/execute/', {}, format='json'))
            self.assertFalse(process_order(order.pk))
            from orders.models import OrderSegmentAddonSettlementRevision
            self.assertEqual(OrderSegmentAddonSettlementRevision.objects.filter(manifest__order=order).count(), 1)
            self.login(self.admin)
            second = self.ok(self.client.post(staff_url, {'request_id': 'second-addon-after-first',
                'amount_raw': 500, 'explanation': '双方确认追加部分退款', 'case_id': case['id']}, format='json'))
            second_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{second["id"]}/'
            for user in (self.boss, replacement):
                self.login(user)
                self.ok(self.client.post(second_url, {'accepted': True}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(staff_url + f'{second["id"]}/execute/', {}, format='json'))
            self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/',
                {'resolution': '加购部分退款完成'}, format='json'))
            self.submit_and_approve(order, segment, replacement)
            self.mature(order)
            with patch('orders.segment_addon_settlement.OrderSegmentAddonSettlement.objects.create',
                       side_effect=RuntimeError('结算写入失败')):
                with self.assertRaisesMessage(RuntimeError, '结算写入失败'):
                    process_order(order.pk)
            self.assertEqual(OrderSegmentAddonSettlementRevision.objects.filter(manifest__order=order).count(), 1)
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
            self.assertTrue(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
            self.assertEqual(OrderSegmentAddonSettlementRevision.objects.filter(manifest__order=order).count(), 2)
            self.assertEqual(Transaction.objects.filter(pk=first.pk, amount=3200).count(), 1)
            result = OrderSegmentAddonSettlement.objects.get(payment_id=payment_id)
            self.assertEqual(result.amount, 2500)
            consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
            self.assertEqual((consumption.paid_units, consumption.refunded_units), (5000, 1500))

    def test_full_addon_refund_deducts_transferred_main_after_other_player_paid(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.login(self.boss)
        case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '加购未履行', 'affected_provider_account_ids': [self.accounts[replacement.pk].pk],
            'client_request_id': 'segment-full-addon-case'}, format='json'))
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        with store_context(Store.objects.get(slug='shenglang')):
            from engagement.confirmation import process_order
            from orders.models import OrderAddonRefund, OrderSegmentPersonSettlement
            self.assertFalse(process_order(order.pk))
            first = Transaction.objects.get(order=order, tx_type='INCOME')
            self.assertEqual(first.amount, 3200)
            self.login(self.admin)
            path = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            proposal = self.ok(self.client.post(path, {'request_id': 'full-addon-after-first',
                'amount_raw': 5000, 'explanation': '双方确认加购全退', 'case_id': case['id']}, format='json'))
            decision = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal["id"]}/'
            for user in (self.boss, replacement):
                self.login(user)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(path + f'{proposal["id"]}/execute/', {}, format='json'))
            refund = OrderAddonRefund.objects.get(proposal_id=proposal['id'])
            self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction), (4000, 1000))
            self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/',
                {'resolution': '加购退款完成'}, format='json'))
            self.submit_and_approve(order, segment, replacement)
            self.mature(order)
            self.assertTrue(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
            self.assertEqual(Transaction.objects.filter(pk=first.pk, amount=3200).count(), 1)
            paid = OrderSegmentPersonSettlement.objects.get(manifest__order=order,
                                                            account=self.accounts[replacement.pk])
            self.assertEqual((paid.amount, paid.income_transaction.amount), (3800, 3800))
            self.assertFalse(OrderSegmentAddonSettlement.objects.filter(payment_id=payment_id).exists())
            order.refresh_from_db()
            self.assertEqual(order.provider_income, 7000)
            self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 10000)

    def handover_addon_after_transfer(self):
        replacement = self.replacement()
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        game = GameCategory.objects.create(name='转单前加购游戏')
        self.service.game_category = game
        self.service.save(update_fields=['game_category'])
        addon = make_service(name='转单前加购', is_addon=True, game_category=game)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
            enabled=True, condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=5000)
        self.login(self.boss)
        path = f'/api/orders/orders/{order.pk}/addons/intents/'
        intent = self.ok(self.client.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'request_id': 'addon-before-transfer', 'expected_amount': 5000,
            'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json'))
        paid = self.ok(self.client.post(path + f"{intent['id']}/pay/", {}, format='json'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained': 4000, 'transferred': 6000, 'agreement': '双方确认转单份额'}))
        old = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=False)
        current = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id': old.pk, 'completion_image': flow.screenshot()}, format='multipart'))
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': current.pk, 'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{paid["payment_id"]}/handovers/'
        handover = self.ok(self.client.post(staff_url, {'request_id': 'addon-handover-after-transfer',
            'target_provider_account_id': self.accounts[replacement.pk].pk, 'retained_basis_raw': 2000,
            'completed_work': '甲完成部分', 'remaining_work': '乙接替剩余部分'}, format='json'))
        decision_url = f'/api/orders/orders/{order.pk}/addons/payments/{paid["payment_id"]}/handovers/{handover["id"]}/decision/'
        for user in (self.boss, self.provider, replacement):
            self.login(user)
            self.ok(self.client.post(decision_url, {'accepted': True}, format='json'))
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        return order, replacement, old, current, paid['payment_id']

    def test_addon_handover_after_main_transfer_pays_each_completed_share(self):
        order, replacement, old, current, payment_id = self.handover_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        with store_context(Store.objects.get(slug='shenglang')):
            self.assertCountEqual(self.settle(order), [self.provider.pk, replacement.pk])
            rows = OrderSegmentAddonSettlement.objects.filter(payment_id=payment_id).order_by('segment_id')
            self.assertEqual(list(rows.values_list('segment_id', 'amount')), [(old.pk, 1600), (current.pk, 2400)])
            from engagement.confirmation import process_order
            self.assertTrue(process_order(order.pk))
            self.assertEqual(OrderAddonConsumption.objects.get(payment_id=payment_id).paid_units, 5000)

    def test_unaffected_player_keeps_already_paid_addon_after_teammates_refund(self):
        order, replacement, old, current, payment_id = self.handover_addon_after_transfer()
        self.login(self.boss)
        case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
            'reason': '接替陪玩加购部分未履行', 'affected_provider_account_ids': [self.accounts[replacement.pk].pk],
            'client_request_id': 'handover-addon-case'}, format='json'))
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        with store_context(Store.objects.get(slug='shenglang')):
            from engagement.confirmation import process_order
            self.assertFalse(process_order(order.pk))
            old_addon = OrderSegmentAddonSettlement.objects.get(payment_id=payment_id, segment=old)
            self.assertEqual(old_addon.amount, 1600)
            self.login(self.admin)
            url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            proposal = self.ok(self.client.post(url, {'request_id': 'handover-refund-b',
                'amount_raw': 1000, 'explanation': '双方同意部分退款', 'case_id': case['id'],
                'affected_provider_account_ids': [self.accounts[replacement.pk].pk]}, format='json'))
            user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal["id"]}/'
            for user in (self.boss, replacement):
                self.login(user)
                self.ok(self.client.post(user_url, {'accepted': True}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(url + f'{proposal["id"]}/execute/', {}, format='json'))
            self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/',
                {'resolution': '接替陪玩加购已退款'}, format='json'))
            self.submit_and_approve(order, current, replacement)
            self.mature(order)
            self.assertTrue(process_order(order.pk))
            old_addon.refresh_from_db()
            self.assertEqual((old_addon.amount, old_addon.income_transaction.amount), (1600, 1600))
            self.assertEqual(OrderSegmentAddonSettlement.objects.get(payment_id=payment_id, segment=current).amount, 1400)
            self.assertEqual(OrderAddonConsumption.objects.get(payment_id=payment_id).refunded_units, 1000)
