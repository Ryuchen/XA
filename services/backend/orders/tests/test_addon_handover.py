import os
from unittest import skipUnless
from django.test import TestCase, override_settings
from django.utils import timezone

from engagement.models import PointFundingLot
from orders.models import (AfterSale, Order, OrderAddonHandover, OrderAddonSettlement, OrderParticipationSegment, OrderProvider,
                           OrderSegmentFinancialSnapshot)
from orders.tests import test_addon_intents
from orders.tests.factories import make_provider
from orders.tests.test_addon_configuration import staff_client
from orders.tests.test_addon_offers import authenticated
from wallet.services import get_wallet


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                   ADDON_HANDOVER_INTERNAL_TESTING=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonHandoverTests(TestCase):
    def setUp(self):
        self.fixture = test_addon_intents.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        self.fixture.setUp()
        fixture = self.fixture
        fixture.provider_user.escort_profile.account = fixture.provider
        fixture.provider_user.escort_profile.save(update_fields=['account'])
        fixture.config.condition_mode = 'NONE'
        fixture.config.conditions = ''
        fixture.config.save(update_fields=['condition_mode', 'conditions'])
        fixture.order.escort_mode = Order.EscortMode.DOUBLE
        fixture.order.required_provider_count = 2
        fixture.order.save(update_fields=['escort_mode', 'required_provider_count'])
        fixture.segment.original_share = 5000
        fixture.segment.save(update_fields=['original_share'])
        OrderProvider.objects.filter(order=fixture.order, provider_account=fixture.provider).update(
            settlement_base=5000, provider_income=4000)
        OrderSegmentFinancialSnapshot.objects.filter(segment=fixture.segment).update(incoming_basis=5000, income_at_entry=4000)
        second_user = make_provider()
        self.second, self.second_client = authenticated(second_user)
        second_user.escort_profile.account = self.second
        second_user.escort_profile.save(update_fields=['account'])
        OrderProvider.objects.create(order=fixture.order, provider=second_user, provider_account=self.second,
                                     settlement_base=5000, commission_rate=30, provider_income=3500)
        self.second_segment = OrderParticipationSegment.objects.create(attribution=fixture.segment.attribution,
            provider_account=self.second, slot=2, original_share=5000, accepted_at=timezone.now(), started_at=timezone.now())
        OrderSegmentFinancialSnapshot.objects.create(segment=self.second_segment, incoming_basis=5000,
            commission_type='PERCENT', commission_rate=30, commission_fixed=0, income_at_entry=3500)
        response = fixture.client.post(fixture.url, fixture.payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        wallet = get_wallet(account=fixture.boss, user=fixture.boss_user)
        wallet.balance = 3000
        wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1600), ('GIFT', 900), ('COMPENSATION', 500)]:
            PointFundingLot.objects.create(account=fixture.boss, source_key=f'handover-{kind}',
                                           kind=kind, remaining=amount)
        paid = fixture.client.post(fixture.url + f"{response.data['data']['id']}/pay/", {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        self.payment_id = paid.data['data']['payment_id']
        self.staff = staff_client(['order:view', 'order:dispatch'])
        self.staff_url = f'/api/admin/orders/{fixture.order.pk}/addons/payments/{self.payment_id}/handovers/'
        self.user_url = f'/api/orders/orders/{fixture.order.pk}/addons/payments/{self.payment_id}/handovers/'
        self.body = {'request_id': 'handover-1', 'target_provider_account_id': self.second.pk,
                     'retained_basis_raw': 600, 'completed_work': '甲完成前两局', 'remaining_work': '乙接替剩余三局'}

    def create(self, body=None):
        result = self.staff.post(self.staff_url, body or self.body, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data['data']

    def agree(self, proposal_id, clients):
        url = self.user_url + f'{proposal_id}/decision/'
        return [client.post(url, {'accepted': True}, format='json') for client in clients]

    def test_refund_basis_remainder_never_exceeds_any_person_or_segment(self):
        from orders.addon_refund import _allocate_refund_basis, _allocate_segment_burden
        # Naively giving the entire tail to ID 3 produces 2 against its
        # remaining capacity of 1. Largest remainder keeps every share valid.
        allocated = _allocate_refund_basis(10, [(1, 5), (2, 5), (3, 1)])
        self.assertEqual(allocated, {1: 5, 2: 4, 3: 1})
        self.assertEqual(sum(allocated.values()), 10)
        entries = [{'segment_id': 1, 'basis': 100, 'burden_capacity': 100, 'refund_basis': 60},
                   {'segment_id': 2, 'basis': 40, 'burden_capacity': 40, 'refund_basis': 30}]
        self.assertEqual(_allocate_segment_burden(entries, 100, [2, 1]), 0)
        self.assertEqual([row['burden'] for row in entries], [60, 40])
        self.assertEqual(_allocate_segment_burden(entries, 150, [2, 1]), 10)
        self.assertEqual([row['burden'] for row in entries], [100, 40])

    def test_all_parties_approve_actual_split_and_later_handover_preserves_history(self):
        proposal = self.create()
        self.assertEqual(proposal['status'], 'PENDING')
        self.assertEqual([(item['basis_raw'], item['income_raw']) for item in proposal['shares']], [(600, 480), (900, 630)])
        self.assertEqual(self.staff.post(self.staff_url, self.body, format='json').data['data']['id'], proposal['id'])
        responses = self.agree(proposal['id'], [self.fixture.provider_client, self.fixture.client, self.second_client])
        self.assertEqual([row.data['data']['status'] for row in responses], ['PENDING', 'PENDING', 'ACCEPTED'])
        from orders.addon_handover import current_responsibilities
        payment = OrderAddonHandover.objects.get(pk=proposal['id']).payment
        _, shares = current_responsibilities(payment)
        self.assertEqual([(row['provider_id'], row['basis'], row['income'], row['is_current']) for row in shares],
                         [(self.fixture.provider.pk, 600, 480, False), (self.second.pk, 900, 630, True)])
        self.assertEqual(payment.intent.provider_id, self.fixture.provider.pk)
        next_body = {'request_id': 'handover-2', 'target_provider_account_id': self.fixture.provider.pk,
                     'retained_basis_raw': 400, 'completed_work': '乙完成两局', 'remaining_work': '甲接替最后一局'}
        second = self.create(next_body)
        self.assertEqual([row.status_code for row in self.agree(second['id'],
            [self.fixture.client, self.second_client, self.fixture.provider_client])], [200, 200, 200])
        _, shares = current_responsibilities(payment)
        self.assertEqual([(row['basis'], row['income'], row['is_current']) for row in shares],
                         [(600, 480, False), (400, 280, False), (500, 400, True)])
        self.assertFalse(OrderAddonSettlement.objects.filter(payment=payment).exists())

    def test_only_main_order_participants_can_be_assigned_and_every_party_has_to_agree(self):
        denied = self.fixture.denied.post(self.staff_url, self.body, format='json')
        self.assertEqual(denied.status_code, 403)
        invalid = self.staff.post(self.staff_url, {**self.body, 'target_provider_account_id': self.fixture.other.pk}, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.data)
        proposal = self.create()
        url = self.user_url + f"{proposal['id']}/decision/"
        self.assertEqual(self.fixture.other_client.post(url, {'accepted': True}, format='json').status_code, 404)
        self.assertEqual(self.fixture.provider_client.post(url, {'accepted': True}, format='json').data['data']['status'], 'PENDING')
        self.assertEqual(self.second_client.post(url, {'accepted': False}, format='json').data['data']['status'], 'REJECTED')
        self.assertEqual(self.fixture.client.post(url, {'accepted': True}, format='json').status_code, 400)
        self.assertEqual(self.create({**self.body, 'request_id': 'replacement'})['version'], 2)

    def test_target_leaves_main_order_before_final_confirmation(self):
        proposal = self.create()
        url = self.user_url + f"{proposal['id']}/decision/"
        self.assertEqual(self.fixture.client.post(url, {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(self.fixture.provider_client.post(url, {'accepted': True}, format='json').status_code, 200)
        self.second_segment.released_at = timezone.now()
        self.second_segment.save(update_fields=['released_at'])
        self.assertEqual(self.second_client.post(url, {'accepted': True}, format='json').status_code, 400)
        self.assertEqual(OrderAddonHandover.objects.get(pk=proposal['id']).status, 'PENDING')
        # A stale proposal can still be declined so it does not block settlement indefinitely.
        self.assertEqual(self.second_client.post(url, {'accepted': False}, format='json').data['data']['status'], 'REJECTED')

    def test_affected_open_aftersale_blocks_handover_until_resolved(self):
        case = AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='乙的售后',
                                        affected_provider_account_ids=[self.second.pk])
        result = self.staff.post(self.staff_url, self.body, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        case.status = 'RESOLVED'
        case.save(update_fields=['status'])
        self.assertEqual(self.create()['status'], 'PENDING')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款整单补资与押金缺口保护属正式资金链深水区（18-盘点 加购退款补资/押金扣减待联调）；最终联调启用')
    def test_refund_waits_when_it_needs_another_addon_with_pending_handover(self):
        payload = {**self.fixture.payload, 'request_id': 'second-for-refund', 'quantity': 1,
                   'expected_amount': 500, 'gift_balance_amount': 100, 'compensation_balance_amount': 0}
        intent = self.fixture.client.post(self.fixture.url, payload, format='json')
        self.assertEqual(intent.status_code, 200, intent.data)
        payment = self.fixture.client.post(self.fixture.url + f"{intent.data['data']['id']}/pay/", {}, format='json')
        self.assertEqual(payment.status_code, 200, payment.data)
        other_id = payment.data['data']['payment_id']
        other_staff = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{other_id}/handovers/'
        pending = self.staff.post(other_staff, {**self.body, 'request_id': 'other-pending',
            'retained_basis_raw': 100}, format='json')
        self.assertEqual(pending.status_code, 200, pending.data)
        case = AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='原加购退款')
        staff = staff_client(['order:view', 'order:refund', 'order:settle'])
        refund_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        proposal = staff.post(refund_url, {'request_id': 'needs-other-income', 'amount_raw': 1500,
                                          'case_id': case.pk, 'explanation': '原加购未履约'}, format='json')
        self.assertEqual(proposal.status_code, 200, proposal.data)
        proposal_id = proposal.data['data']['id']
        decision = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/{proposal_id}/'
        for client in (self.fixture.client, self.fixture.provider_client):
            response = client.post(decision, {'accepted': True}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        execute = refund_url + f'{proposal_id}/execute/'
        blocked = staff.post(execute, {}, format='json')
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self.assertIn('等待确认', str(blocked.data))
        from orders.models import OrderAddonRefund
        self.assertFalse(OrderAddonRefund.objects.filter(proposal_id=proposal_id).exists())
        cancel = (f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{other_id}/handovers/'
                  f'{pending.data["data"]["id"]}/decision/')
        response = self.fixture.provider_client.post(cancel, {'accepted': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        posted = staff.post(execute, {}, format='json')
        self.assertEqual(posted.status_code, 200, posted.data)
        refund = OrderAddonRefund.objects.get(proposal_id=proposal_id)
        self.assertEqual(dict(refund.earning_deductions.values_list('payment_id', 'amount')),
                         {self.payment_id: 1200, other_id: 300})

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款整单补资与押金缺口保护属正式资金链深水区（18-盘点 加购退款补资/押金扣减待联调）；最终联调启用')
    def test_completed_partial_refund_preserves_remaining_addon_handover(self):
        case = AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='加购部分退款')
        staff = staff_client(['order:view', 'order:refund', 'order:settle'])
        staff_refund = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_refund = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        result = staff.post(staff_refund, {'request_id': 'refund-before-handover', 'amount_raw': 100,
                                          'explanation': '部分加购未履行'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        proposal_id = result.data['data']['id']
        for client in (self.fixture.client, self.fixture.provider_client):
            response = client.post(user_refund + f'{proposal_id}/', {'accepted': True}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        refunded = staff.post(staff_refund + f'{proposal_id}/execute/', {}, format='json')
        self.assertEqual(refunded.status_code, 200, refunded.data)
        resolved = staff.post(f'/api/admin/aftersales/{case.pk}/resolve/', {'resolution': '退款完成'}, format='json')
        self.assertEqual(resolved.status_code, 200, resolved.data)
        handover = self.create()
        self.assertEqual((handover['source_basis_before_raw'], handover['retained_basis_raw'],
                          handover['transferred_basis_raw']), (1400, 600, 800))
        self.assertEqual([(item['basis_raw'], item['income_raw']) for item in handover['shares']], [(600, 471), (800, 560)])
        for response in self.agree(handover['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(OrderAddonHandover.objects.get(pk=handover['id']).status, 'ACCEPTED')

    def test_multiplayer_refund_records_individual_burdens_and_rounding(self):
        handover = self.create()
        for response in self.agree(handover['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='两人加购售后')
        staff = staff_client(['order:view', 'order:refund', 'order:settle'])
        staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        data = {'request_id': 'multiplayer-refund', 'amount_raw': 510, 'explanation': '共同承担',
                'affected_provider_account_ids': [self.fixture.provider.pk, self.second.pk]}
        proposed = staff.post(staff_url, data, format='json')
        self.assertEqual(proposed.status_code, 200, proposed.data)
        proposal_id = proposed.data['data']['id']
        self.assertEqual([row['burden_raw'] for row in proposed.data['data']['participants']], [210, 310])
        private_view = self.second_client.get(user_url + f'{proposal_id}/')
        self.assertEqual(private_view.status_code, 200, private_view.data)
        self.assertEqual([row['account_id'] for row in private_view.data['data']['participants']], [self.second.pk])
        self.assertEqual(private_view.data['data']['provider_burden_raw'], 310)
        for client in (self.fixture.client, self.fixture.provider_client):
            response = client.post(user_url + f'{proposal_id}/', {'accepted': True}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(staff.post(staff_url + f'{proposal_id}/execute/', {}, format='json').status_code, 400)
        self.assertEqual(self.second_client.post(user_url + f'{proposal_id}/', {'accepted': True}, format='json').status_code, 200)
        posted = staff.post(staff_url + f'{proposal_id}/execute/', {}, format='json')
        self.assertEqual(posted.status_code, 200, posted.data)
        from orders.models import OrderAddonRefund
        refund = OrderAddonRefund.objects.get(proposal_id=proposal_id)
        self.assertEqual((refund.amount, refund.addon_pending_deduction, refund.rounding_surplus), (510, 520, 10))
        self.assertEqual(list(refund.earning_deductions.order_by('participant__account_id').values_list(
            'participant__account_id', 'amount')), [(self.fixture.provider.pk, 210), (self.second.pk, 310)])
        from orders.addon_handover import current_responsibilities
        from orders.addon_payment import verify_receipt
        _, shares = current_responsibilities(refund.payment)
        self.assertEqual([(row['basis'], row['income']) for row in shares], [(396, 270), (594, 320)])
        verify_receipt(refund.payment)
        self.assertEqual(staff.post(staff_url + f'{proposal_id}/execute/', {}, format='json').status_code, 200)
        self.assertEqual(OrderAddonRefund.objects.count(), 1)
        again = staff.post(staff_url, {**data, 'request_id': 'multiplayer-refund-next', 'amount_raw': 10}, format='json')
        self.assertEqual(again.status_code, 200, again.data)
        next_id = again.data['data']['id']
        for client in (self.fixture.client, self.fixture.provider_client, self.second_client):
            self.assertEqual(client.post(user_url + f'{next_id}/', {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(staff.post(staff_url + f'{next_id}/execute/', {}, format='json').status_code, 200)
        second_refund = OrderAddonRefund.objects.get(proposal_id=next_id)
        self.assertEqual((second_refund.amount, second_refund.rounding_surplus), (10, 10))
        _, shares = current_responsibilities(refund.payment)
        self.assertEqual([(row['basis'], row['income']) for row in shares], [(392, 260), (588, 310)])
        verify_receipt(refund.payment)

    def test_single_affected_player_does_not_expose_or_change_other_share(self):
        handover = self.create()
        for response in self.agree(handover['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        case = AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='乙的加购',
                                        affected_provider_account_ids=[self.second.pk])
        staff = staff_client(['order:view', 'order:refund', 'order:settle'])
        staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        response = staff.post(staff_url, {'request_id': 'only-second-provider', 'amount_raw': 100,
            'explanation': '只退乙的部分', 'case_id': case.pk,
            'affected_provider_account_ids': [self.second.pk]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        pk = response.data['data']['id']
        self.assertEqual(self.fixture.provider_client.get(user_url + f'{pk}/').status_code, 404)
        for client in (self.fixture.client, self.second_client):
            self.assertEqual(client.post(user_url + f'{pk}/', {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(staff.post(staff_url + f'{pk}/execute/', {}, format='json').status_code, 200)
        from orders.addon_handover import current_responsibilities
        from orders.models import OrderAddonPayment
        _, shares = current_responsibilities(OrderAddonPayment.objects.get(pk=self.payment_id))
        self.assertEqual([(row['basis'], row['income']) for row in shares], [(600, 480), (800, 530)])

    def test_refund_exposes_burden_above_person_remaining_basis(self):
        handover = self.create({**self.body, 'retained_basis_raw': 1})
        for response in self.agree(handover['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='较小份额取整')
        staff = staff_client(['order:view', 'order:refund'])
        staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        proposed = staff.post(staff_url, {'request_id': 'small-share-refund', 'amount_raw': 10,
            'explanation': '共同退款', 'affected_provider_account_ids': [self.fixture.provider.pk, self.second.pk]}, format='json')
        self.assertEqual(proposed.status_code, 200, proposed.data)
        first = proposed.data['data']['participants'][0]
        self.assertEqual((first['basis_raw'], first['burden_raw'], first['excess_burden_raw']), (1, 10, 9))
        self.assertEqual((first['share_plan'][0]['refund_basis'], first['share_plan'][0]['burden']), (0, 1))
        pk = proposed.data['data']['id']
        own = self.fixture.provider_client.get(user_url + f'{pk}/')
        self.assertEqual(own.data['data']['participants'][0]['excess_burden_raw'], 9)
        for client in (self.fixture.client, self.fixture.provider_client, self.second_client):
            self.assertEqual(client.post(user_url + f'{pk}/', {'accepted': True}, format='json').status_code, 200)
        executed = staff.post(staff_url + f'{pk}/execute/', {}, format='json')
        self.assertEqual(executed.status_code, 200, executed.data)
        self.assertEqual(executed.data['data']['participants'][0]['excess_burden_raw'], 9)
        for client, account in ((self.fixture.provider_client, self.fixture.provider),
                                (self.second_client, self.second)):
            visible = client.get(user_url + f'{pk}/')
            self.assertEqual(visible.status_code, 200, visible.data)
            participants = visible.data['data']['participants']
            self.assertEqual([row['account_id'] for row in participants], [account.pk])
            own = participants[0]
            self.assertEqual(own['burden_raw'], own['addon_pending_deduction_raw']
                             + own['main_pending_deduction_raw'] + own['wallet_deduction_raw']
                             + own['deposit_deduction_raw'])

    def test_repeated_small_refunds_do_not_reuse_original_burden_capacity(self):
        handover = self.create({**self.body, 'retained_basis_raw': 10})
        for response in self.agree(handover['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='两次小额退款')
        staff = staff_client(['order:view', 'order:refund'])
        staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        for index in range(2):
            response = staff.post(staff_url, {'request_id': f'small-repeat-{index}', 'amount_raw': 10,
                'explanation': '共同退款', 'affected_provider_account_ids': [self.fixture.provider.pk, self.second.pk]}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            first = response.data['data']['participants'][0]
            segment = first['share_plan'][0]
            self.assertEqual((segment['refund_basis'], segment['burden_capacity'], segment['burden'],
                              first['excess_burden_raw']), (0, 10, 10, 0) if index == 0 else (0, 0, 0, 10))
            pk = response.data['data']['id']
            for client in (self.fixture.client, self.fixture.provider_client, self.second_client):
                self.assertEqual(client.post(user_url + f'{pk}/', {'accepted': True}, format='json').status_code, 200)
            result = staff.post(staff_url + f'{pk}/execute/', {}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        from orders.models import OrderAddonRefund
        rows = list(OrderAddonRefund.objects.order_by('pk'))
        self.assertEqual(sum(row.rounding_surplus for row in rows), 20)
        self.assertEqual(sum(row.proposal.participants.get(account=self.fixture.provider).excess_burden for row in rows), 10)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款整单补资与押金缺口保护属正式资金链深水区（18-盘点 加购退款补资/押金扣减待联调）；最终联调启用')
    def test_multiplayer_wallet_and_deposit_are_atomic_and_auditable(self):
        handover = self.create()
        for response in self.agree(handover['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        first_user = self.fixture.provider_user
        second_user = OrderProvider.objects.get(order=self.fixture.order, provider_account=self.second).provider
        profiles = [first_user.escort_profile, second_user.escort_profile]
        wallets = []
        registrar = staff_client(['escort:view', 'escort:deposit_topup'])
        for index, (profile, account, user, wallet_balance, deposit) in enumerate(zip(profiles,
                (self.fixture.provider, self.second), (first_user, second_user), (70, 120), (50, 140))):
            profile.deposit_mode, profile.deposit_paid = 'CASH', 0
            profile.save(update_fields=['deposit_mode', 'deposit_paid'])
            wallet = get_wallet(account=account, user=user)
            wallet.balance = wallet_balance
            wallet.save(update_fields=['balance'])
            wallets.append(wallet)
            response = registrar.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': deposit, 'reason': '线下核验', 'reference': f'multi-deposit-{index}',
                'client_request_id': f'multi-deposit-{index}'}, format='multipart')
            self.assertEqual(response.status_code, 200, response.data)
        self.fixture.order.providers.update(settled_at=timezone.now())
        AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='双人完整退款')
        staff = staff_client(['order:view', 'order:refund', 'order:settle'])
        staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        proposal = staff.post(staff_url, {'request_id': 'multi-deposit', 'amount_raw': 1500,
            'explanation': '全部退款', 'affected_provider_account_ids': [self.fixture.provider.pk, self.second.pk]}, format='json')
        self.assertEqual(proposal.status_code, 200, proposal.data)
        pk = proposal.data['data']['id']
        for client in (self.fixture.client, self.fixture.provider_client, self.second_client):
            response = client.post(user_url + f'{pk}/', {'accepted': True}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        execute = staff_url + f'{pk}/execute/'
        self.assertEqual(staff.post(execute, {}, format='json').status_code, 400)
        for wallet, expected in zip(wallets, (70, 120)):
            wallet.refresh_from_db()
            self.assertEqual(wallet.balance, expected)
        from orders.models import OrderAddonRefund
        self.assertFalse(OrderAddonRefund.objects.exists())
        topup = registrar.post(f'/api/admin/escorts/{profiles[1].pk}/deposit-topups/', {
            'amount': 10, 'reason': '补足剩余押金', 'reference': 'multi-extra',
            'client_request_id': 'multi-extra'}, format='multipart')
        self.assertEqual(topup.status_code, 200, topup.data)
        result = staff.post(execute, {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        refund = OrderAddonRefund.objects.get()
        people = list(refund.proposal.participants.order_by('account_id'))
        self.assertEqual([(person.addon_pending_deduction, person.wallet_deduction, person.deposit_deduction)
                          for person in people], [(480, 70, 50), (630, 120, 150)])
        self.assertEqual(list(refund.deposit_deficits.order_by('account_id').values_list('amount', flat=True)), [50, 150])
        for client, person in zip((self.fixture.provider_client, self.second_client), people):
            history = client.get('/api/wallet/transactions/')
            self.assertEqual(history.status_code, 200, history.data)
            entry = next(row for row in history.data['data']['transactions'] if row['id'] == person.provider_transaction_id)
            self.assertEqual((entry['order_id'], entry['addon']['refund_id']), (self.fixture.order.pk, refund.pk))
        finance = staff_client(['transaction:view'])
        listing = finance.get('/api/admin/transactions/', {'order_no_exact': self.fixture.order.order_no})
        self.assertEqual(listing.status_code, 200, listing.data)
        transaction_ids = {item['id'] for item in listing.data['data']['list']}
        self.assertTrue({person.provider_transaction_id for person in people} <= transaction_ids)
        from orders.addon_payment import verify_receipt
        verify_receipt(refund.payment)
        for wallet in wallets:
            wallet.refresh_from_db()
            self.assertEqual(wallet.balance, 0)
