from django.test import TestCase, override_settings
from django.conf import settings
from django.utils import timezone
from unittest import skipUnless

from console.models import AdminAuditLog
from orders import addon_payment
from orders.aftersale import append_history
from orders.models import AfterSale, OrderOriginalShareTransfer, OrderParticipationSegment
from orders.tests import test_addon_intents
from orders.tests.factories import make_provider
from orders.tests.test_addon_configuration import staff_client
from orders.tests.test_addon_offers import authenticated
from orders.tests.test_addon_payment import prepare
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from site_messages.models import AddonRefundProposalNotice
from wallet.models import Transaction, Wallet


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonRefundFlowTests(TestCase):
    def setUp(self):
        self.fixture = test_addon_intents.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        intent_id, self.wallet = prepare(self.fixture)
        self.payment = addon_payment.pay(intent_id, actor=self.fixture.boss)
        self.case = AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss,
            reason='加购项目协商退款', affected_provider_account_ids=[self.fixture.provider.pk])
        self.staff = staff_client(['order:refund'])
        self.staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment.pk}/refund-proposals/'
        self.user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment.pk}/refund-proposals/'
        self.discovery_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/refund-proposals/'
        self.body = {'request_id': 'addon-refund-1', 'amount_raw': 500, 'explanation': '加购部分服务未履行'}

    def first_proposal_body(self):
        self.case.addon_payment_id = self.payment.pk
        self.case.client_request_id = 'case-opening'
        self.case.affected_segment_ids = [self.fixture.segment.pk]
        append_history(self.case)
        self.case.save(update_fields=['addon_payment', 'client_request_id', 'affected_segment_ids', 'history'])
        return {**self.body, 'case_id': self.case.pk, 'case_request_id': self.case.client_request_id,
                'create_only': True, 'affected_provider_account_ids': [self.fixture.provider.pk]}

    def test_first_proposal_preserves_other_staff_attempt_and_original_receipt(self):
        body = self.first_proposal_body()
        second_staff = staff_client(['order:refund'])
        balances = list(Wallet.objects.order_by('pk').values_list('pk', 'balance'))
        transaction_count = Transaction.objects.count()
        for staff in (self.staff, second_staff):
            self.assertEqual(staff.get(self.staff_url).data, {'code': 0, 'data': []})

        opened = self.staff.post(self.staff_url, body, format='json')
        self.assertEqual(opened.status_code, 200, opened.data)
        proposal = opened.data['data']
        self.assertEqual((proposal['case_id'], proposal['payment_id'], proposal['case_request_id'],
                          proposal['request_id'], proposal['status'], proposal['refund_executed']),
                         (self.case.pk, self.payment.pk, self.case.client_request_id, body['request_id'], 'PENDING', False))
        self.assertCountEqual([(row['role'], row['account_id'], row['accepted']) for row in proposal['decisions']],
                              [('BOSS', self.fixture.boss.pk, None), ('PROVIDER', self.fixture.provider.pk, None)])
        self.assertEqual([row['segment_id'] for row in proposal['participants'][0]['share_plan']],
                         self.case.affected_segment_ids)
        for staff, changes in ((second_staff, {'request_id': 'other-staff'}), (second_staff, {}),
                               (self.staff, {'amount_raw': 400}),
                               (self.staff, {'affected_provider_account_ids': [self.fixture.other.pk]})):
            denied = staff.post(self.staff_url, {**body, **changes}, format='json')
            self.assertEqual((denied.status_code, denied.data['code']), (409, 409), denied.data)
        retry = self.staff.post(self.staff_url, body, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data['data'], proposal)
        self.assertEqual(self.staff.get(self.staff_url).data['data'], [proposal])
        self.assertEqual(second_staff.get(self.staff_url).data['data'],
                         [{key: value for key, value in proposal.items() if key != 'request_id'}])
        for party in (self.fixture.client, self.fixture.provider_client):
            detail = party.get(self.user_url + f"{proposal['id']}/").data['data']
            self.assertNotIn('request_id', detail)
            self.assertNotIn('case_request_id', detail)
        self.assertEqual(self.payment.refund_proposals.count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(resource='addon-refund-proposal').count(), 1)
        self.assertEqual(AddonRefundProposalNotice.objects.filter(proposal_id=proposal['id']).count(), 2)
        self.assertFalse(self.payment.refund_records.exists())
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance')), balances)
        self.assertEqual(Transaction.objects.count(), transaction_count)

    def test_first_proposal_requires_matching_payment_and_opening_round(self):
        body = self.first_proposal_body()
        for field in ('case_id', 'case_request_id'):
            incomplete = {key: value for key, value in body.items() if key != field}
            response = self.staff.post(self.staff_url, incomplete, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        wrong_payment = self.staff_url.replace(f'/payments/{self.payment.pk}/', f'/payments/{self.payment.pk + 1}/')
        for url, payload in ((wrong_payment, body), (self.staff_url, {**body, 'case_request_id': 'outdated'})):
            response = self.staff.post(url, payload, format='json')
            self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(self.payment.refund_proposals.exists())

        opened = self.staff.post(self.staff_url, body, format='json')
        self.assertEqual(opened.status_code, 200, opened.data)
        self.case.client_request_id = 'reopened-case'
        self.case.created_at = timezone.now()
        append_history(self.case)
        self.case.save(update_fields=['client_request_id', 'created_at', 'history'])
        for changes in ({}, {'case_request_id': self.case.client_request_id},
                        {'case_request_id': self.case.client_request_id, 'request_id': 'new-round-attempt'}):
            response = self.staff.post(self.staff_url, {**body, **changes}, format='json')
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.payment.refund_proposals.get().status, 'PENDING')

    def test_first_proposal_never_replaces_while_legacy_creation_can_replace(self):
        body = self.first_proposal_body()
        first = self.staff.post(self.staff_url, body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        proposal_id = first.data['data']['id']
        rejected = self.fixture.provider_client.post(self.user_url + f'{proposal_id}/', {'accepted': False}, format='json')
        self.assertEqual(rejected.data['data']['status'], 'REJECTED')
        denied = self.staff.post(self.staff_url, {**body, 'request_id': 'create-only-again'}, format='json')
        self.assertEqual(denied.status_code, 409, denied.data)

        for key in ('legacy-next', 'legacy-replacement'):
            response = self.staff.post(self.staff_url, {**self.body, 'request_id': key}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(list(self.payment.refund_proposals.order_by('pk').values_list('status', flat=True)),
                         ['REJECTED', 'SUPERSEDED', 'PENDING'])

    def test_only_decision_accounts_can_find_and_read_back_their_proposals_from_the_order(self):
        buyer = self.fixture.client
        provider = self.fixture.provider_client
        other = self.fixture.other_client
        _, other_provider = authenticated(make_provider())
        empty = {'code': 0, 'data': []}
        self.assertEqual(provider.get(self.discovery_url).data, empty)
        response = self.staff.post(self.staff_url, self.body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        proposal_id = response.data['data']['id']
        pending = {'payment_id': self.payment.pk, 'proposal_id': proposal_id,
                   'status': 'PENDING', 'my_accepted': None}

        self.assertEqual(buyer.get(self.discovery_url).data, {'code': 0, 'data': [pending]})
        self.assertEqual(provider.get(self.discovery_url).data, {'code': 0, 'data': [pending]})
        self.assertEqual(other.get(self.discovery_url).data, empty)
        self.assertEqual(other_provider.get(self.discovery_url).data, empty)
        unknown_url = f'/api/orders/orders/{self.fixture.order.pk + 1}/addons/refund-proposals/'
        self.assertEqual(provider.get(unknown_url).data, empty)
        self.assertEqual(provider.get('/api/orders/orders/9223372036854775808/addons/refund-proposals/').data, empty)

        detail_url = self.user_url + f'{proposal_id}/'
        response = provider.get(detail_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['id'], proposal_id)
        self.assertEqual(provider.post(detail_url, {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(provider.get(self.discovery_url).data,
                         {'code': 0, 'data': [{**pending, 'my_accepted': True}]})
        self.assertEqual(buyer.get(self.discovery_url).data, {'code': 0, 'data': [pending]})
        self.assertEqual(buyer.post(detail_url, {'accepted': True}, format='json').status_code, 200)
        signed = {**pending, 'status': 'ACCEPTED', 'my_accepted': True}
        self.assertEqual(provider.get(self.discovery_url).data, {'code': 0, 'data': [signed]})
        self.assertEqual(other.get(self.discovery_url).data, empty)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, tx_type='REFUND').exists())

    def test_staff_boss_and_player_confirm_one_paid_addon_without_premature_refund(self):
        self.assertEqual(self.fixture.denied.post(self.staff_url, self.body, format='json').status_code, 403)
        response = self.staff.post(self.staff_url, self.body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        proposal = response.data['data']
        self.assertEqual(proposal['provider_burden_raw'], 500)
        self.assertEqual(proposal['provider_income_retained_raw'], 700)
        self.assertEqual([(row['kind'], row['amount_raw']) for row in proposal['refund_sources']], [('PREPAID', 500)])
        self.assertFalse(proposal['execution_available'])
        self.assertEqual(self.staff.post(self.staff_url, self.body, format='json').data['data']['id'], proposal['id'])
        self.assertEqual(self.fixture.other_client.get(self.user_url).data['data'], [])
        self.assertEqual(self.fixture.client.get(self.user_url).data['data'][0]['id'], proposal['id'])
        url = self.user_url + f"{proposal['id']}/"
        self.assertEqual(self.fixture.other_client.post(url, {'accepted': True}, format='json').status_code, 404)
        first = self.fixture.client.post(url, {'accepted': True}, format='json')
        self.assertEqual(first.data['data']['status'], 'PENDING')
        second = self.fixture.provider_client.post(url, {'accepted': True}, format='json')
        self.assertEqual(second.data['data']['status'], 'ACCEPTED')
        self.assertFalse(second.data['data']['refund_executed'])
        self.assertEqual(self.fixture.provider_client.post(url, {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(self.staff.post(self.staff_url, {**self.body, 'request_id': 'next'}, format='json').status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(resource='addon-refund-proposal').count(), 1)

    def test_rejected_terms_can_be_replaced_and_case_scope_is_enforced(self):
        response = self.staff.post(self.staff_url, self.body, format='json')
        url = self.user_url + f"{response.data['data']['id']}/"
        result = self.fixture.provider_client.post(url, {'accepted': False}, format='json')
        self.assertEqual(result.data['data']['status'], 'REJECTED')
        replacement = self.staff.post(self.staff_url, {**self.body, 'request_id': 'replacement', 'amount_raw': 400}, format='json')
        self.assertEqual(replacement.status_code, 200, replacement.data)
        self.assertEqual(self.fixture.provider_client.get(self.discovery_url).data['data'], [
            {'payment_id': self.payment.pk, 'proposal_id': replacement.data['data']['id'],
             'status': 'PENDING', 'my_accepted': None},
            {'payment_id': self.payment.pk, 'proposal_id': response.data['data']['id'],
             'status': 'REJECTED', 'my_accepted': False},
        ])
        self.case.affected_provider_account_ids = [self.fixture.other.pk]
        self.case.save(update_fields=['affected_provider_account_ids'])
        url = self.user_url + f"{replacement.data['data']['id']}/"
        self.assertEqual(self.fixture.client.post(url, {'accepted': True}, format='json').status_code, 400)

    def test_transferred_provider_can_agree_addon_refund_before_handover(self):
        old = self.fixture.segment
        replacement = OrderParticipationSegment.objects.create(attribution=old.attribution,
            provider_account=self.fixture.other, slot=old.slot, original_share=old.original_share,
            accepted_at=timezone.now())
        OrderOriginalShareTransfer.objects.create(previous_segment=old, next_segment=replacement,
            original_share_before=old.original_share, retained_share=0, transferred_share=old.original_share,
            operator=self.fixture.boss, agreement='协商转单', created_at=timezone.now())
        response = self.staff.post(self.staff_url, self.body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        url = self.user_url + f"{response.data['data']['id']}/"
        self.assertEqual(self.fixture.client.post(url, {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(self.fixture.provider_client.post(url, {'accepted': True}, format='json').data['data']['status'],
                         'ACCEPTED')


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonRefundStoresTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_identifiers_keep_refund_proposals_in_their_own_store(self):
        records = []
        for index, store in enumerate((resolve_store(), xinan_store())):
            with store_context(store):
                fixture = test_addon_intents.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
                intent_id, _ = prepare(fixture, host='xa.esports.club' if index else None)
                payment = addon_payment.pay(intent_id, actor=fixture.boss)
                AfterSale.objects.create(order=fixture.order, account=fixture.boss, reason='加购退款')
                staff = staff_client(['order:refund'])
                url = f'/api/admin/orders/{fixture.order.pk}/addons/payments/{payment.pk}/refund-proposals/'
                response = staff.post(url, {'request_id': 'same', 'amount_raw': 500 + 100 * index,
                                             'explanation': '加购项目协商退款'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                records.append((fixture, payment.pk, response.data['data']))
                if index:
                    user_url = f'/api/orders/orders/{fixture.order.pk}/addons/payments/{payment.pk}/refund-proposals/'
                    signed = fixture.client.post(user_url + f"{response.data['data']['id']}/",
                                                 {'accepted': True}, format='json')
                    self.assertEqual(signed.status_code, 200, signed.data)
        self.assertEqual(records[0][1], records[1][1])
        self.assertEqual(records[0][2]['id'], records[1][2]['id'])
        for index, (fixture, payment_id, expected) in enumerate(records):
            url = f'/api/orders/orders/{fixture.order.pk}/addons/payments/{payment_id}/refund-proposals/'
            response = fixture.client.get(url)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['data'][0]['amount_raw'], 500 + 100 * index)
            discovery = f'/api/orders/orders/{fixture.order.pk}/addons/refund-proposals/'
            response = fixture.client.get(discovery)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['data'], [
                {'payment_id': payment_id, 'proposal_id': expected['id'],
                 'status': 'PENDING', 'my_accepted': True if index else None},
            ])
            wrong_host = 'sl.esports.club' if index else 'xa.esports.club'
            self.assertEqual(fixture.client.get(discovery, HTTP_HOST=wrong_host).status_code, 401)
