from django.test import TestCase, override_settings

from engagement.models import PointFundingLot
from orders.models import (GameCategory, OrderAddonRefund, OrderSegmentFinancialCoverage,
    OrderSegmentFinancialSnapshot, ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_negotiation_termination
from orders.tests.factories import make_service


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AddonRefundUnaffectedTeammateTests(TestCase):
    def test_b_addon_refund_can_use_b_pending_main_income_after_a_ends(self):
        base = test_negotiation_termination.NegotiationTerminationTests(
            methodName='test_only_affected_provider_stops_and_other_provider_continues')
        base.setUp()
        self.addCleanup(base.doCleanups)
        game = GameCategory.objects.create(name='测试游戏')
        base.service.game_category = game
        base.service.save(update_fields=['game_category'])
        addon = make_service(name='测试加购', is_addon=True, game_category=game)
        config = ServiceAddonConfiguration.objects.create(service=base.service, addon=addon,
            enabled=True, condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=500)
        attribution = base.order.original_attribution
        OrderSegmentFinancialCoverage.objects.create(attribution=attribution, deal_amount=10000,
                                                     source='WALLET_NO_POINTS', status='ACTIVE')
        segment = attribution.segments.get(provider_account=base.b_account)
        OrderSegmentFinancialSnapshot.objects.create(segment=segment, incoming_basis=5000,
            commission_type='PERCENT', commission_rate=20, commission_fixed=0, income_at_entry=4000)
        PointFundingLot.objects.create(account=base.boss_account, source_key='addon-b-prepaid',
                                       kind='PREPAID', remaining=9000)
        buyer = base.client_for(base.boss_account)
        intent_url = f'/api/orders/orders/{base.order.pk}/addons/intents/'
        intent = buyer.post(intent_url, {'addon_id':addon.pk, 'unit':'HOUR', 'quantity':1,
            'configuration_version':1, 'provider_account_id':base.b_account.pk,
            'request_id':'addon-b', 'expected_amount':500, 'gift_balance_amount':0,
            'compensation_balance_amount':0}, format='json')
        self.assertEqual(intent.status_code, 200, intent.data)
        paid = buyer.post(intent_url + f"{intent.data['data']['id']}/pay/", {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        payment_id = paid.data['data']['payment_id']

        base.order.requires_boss_selection = True
        base.order.save(update_fields=['requires_boss_selection'])
        case, proposal = base.accepted(amount=1000)
        base.execute(case, proposal)
        case_result = base.client.post(base.url, {'order_id':base.order.pk, 'reason':'处理 B 加购',
            'affected_provider_account_ids':[base.b_account.pk], 'client_request_id':'addon-b-case'}, format='json')
        self.assertEqual(case_result.status_code, 200, case_result.data)
        refund_staff = base.client_for(base.operator_account)
        path = f'/api/admin/orders/{base.order.pk}/addons/payments/{payment_id}/refund-proposals/'
        draft = refund_staff.post(path, {'request_id':'refund-b-addon', 'amount_raw':500,
                                         'explanation':'协商退还 B 加购'}, format='json')
        self.assertEqual(draft.status_code, 200, draft.data)
        proposal_id = draft.data['data']['id']
        accept_path = f'/api/orders/orders/{base.order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
        for account in (base.boss_account, base.b_account):
            accepted = base.client_for(account).post(accept_path, {'accepted':True}, format='json')
            self.assertEqual(accepted.status_code, 200, accepted.data)
        result = refund_staff.post(path + f'{proposal_id}/execute/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        refund = OrderAddonRefund.objects.get()
        self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction), (400, 100))
        self.assertEqual(refund.payment.intent.provider_id, base.b_account.pk)
        self.assertEqual(base.order.refund_records.count(), 1)
