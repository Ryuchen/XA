from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from chat.models import ChatMessage
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import (AfterSale, GameCategory, Order, OrderAddonIntent, OrderAddonPayment,
                           OrderParticipationSegment, OrderProvider, OrderRoomCompletionRequest,
                           OrderRoomConfirmation, ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_console_user, make_service, make_user
from wallet.models import SystemConfig, Transaction
from wallet.services import get_wallet


@override_settings(
    ORDINARY_SERVICE_FUNDING_ENABLED=True,
    RECEIPT_USAGE_LEDGER_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    PLATFORM_IDENTITY_ENABLED=True,
    PLATFORM_PASS_REPAYMENT_ENABLED=True,
    ADDON_PAYMENT_INTERNAL_TESTING=False,
    ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
    ADDON_SETTLEMENT_INTERNAL_TESTING=False,
    SERVICE_POINTS_CHECKOUT_TESTING=False,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True,
    ROOM_COMPLETION_REVIEW_ENABLED=True,
    ROOM_BOSS_CONFIRMATION_ENABLED=True,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
)
class RoomVerifiedAddonPurchaseTest(APITestCase):
    databases = {'default'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order

    def client_for(self, account, *, host=None):
        return APIClient(**({'HTTP_HOST': host} if host else {}),
                         HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def prepare_verified_room(self):
        game = GameCategory.objects.create(name='房间核实后加购')
        self.service.room_enabled = True
        self.service.game_category = game
        self.service.save(update_fields=['room_enabled', 'game_category'])
        self.provider.escort_profile.service_items.add(self.service)
        self.addon = make_service(name='房间核实后追加项目', is_addon=True, game_category=game)
        self.config = ServiceAddonConfiguration.objects.create(
            service=self.service, addon=self.addon, enabled=True,
            condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(
            configuration=self.config, unit='HOUR', price=1000)
        SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
            defaults={'value': '{"minutes":30,"version":1}'})

        self.ok(self.recharge())
        self.order = self.new_order()
        self.order_path = f'/api/orders/orders/{self.order.pk}/'
        self.provider_account = self.accounts[self.provider.pk]
        self.buyer_account = self.accounts[self.boss.pk]

        self.login(self.provider)
        self.ok(self.client.post(self.order_path + 'start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        self.selection = {
            'addon_id': self.addon.pk,
            'unit': 'HOUR',
            'quantity': 1,
            'configuration_version': self.config.version,
            'provider_account_id': self.provider_account.pk,
        }
        self.intent_payload = {
            **self.selection,
            'request_id': 'room-verified-addon-intent',
            'expected_amount': 1000,
            'gift_balance_amount': 0,
            'compensation_balance_amount': 0,
        }
        sent = self.ok(self.client.post('/api/chat/send/', {
            'content': '从原订单卡片继续咨询',
            'order_ref': str(self.order.pk),
            'client_message_id': 'room-verified-addon-card',
        }, format='json'))
        self.card_message = ChatMessage.objects.get(pk=sent['id'])
        self.assertEqual(self.card_message.related_order_id, self.order.pk)

        first_intent = self.ok(self.client.post(self.order_path + 'addons/intents/', {
            **self.intent_payload, 'request_id': 'room-before-verification-addon',
        }, format='json'))
        first_paid = self.ok(self.client.post(
            self.order_path + f'addons/intents/{first_intent["id"]}/pay/', {}, format='json'))
        self.first_payment = OrderAddonPayment.objects.select_related(
            'intent', 'buyer_transaction').get(pk=first_paid['payment_id'])
        self.assertEqual(self.first_payment.buyer_transaction.amount, -1000)

        self.login(self.provider)
        self.ok(self.client.post(self.order_path + 'complete/',
            {'completion_image': flow.screenshot(),
             'client_request_id': 'room-verified-addon-evidence'}, format='multipart'))
        requested = self.ok(self.client.post(self.order_path + 'room/completion-requests/', {
            'description': '当前履约已完成，等待老板确认',
            'client_request_id': 'room-verified-addon-request',
        }, format='json'))
        reviewer = make_console_user(['order:view', 'support:edit'])
        reviewer_account = get_or_create_account_for_legacy_user(reviewer)
        self.accounts[reviewer.pk] = reviewer_account
        reviewer.admin_membership.account = reviewer_account
        reviewer.admin_membership.save(update_fields=['account'])
        self.login(reviewer)
        self.ok(self.client.post(
            f'/api/admin/room-completion-requests/{requested["id"]}/review/', {
                'decision': 'VERIFIED',
                'note': '已核实当前履约段',
                'client_request_id': 'room-verified-addon-review',
            }, format='json'))

        self.order.refresh_from_db()
        self.room_request = OrderRoomCompletionRequest.objects.get(pk=requested['id'])
        self.room_confirmation = OrderRoomConfirmation.objects.get(request=self.room_request)
        self.assertEqual((self.order.status, self.room_request.status,
                          self.room_confirmation.status),
                         (Order.Status.IN_SERVICE, 'VERIFIED', 'RUNNING'))
        AfterSale.objects.create(
            order=self.order, account=self.buyer_account, reason='原咨询历史轮次',
            status='RESOLVED', resolution='历史咨询已核对',
            history=[{'action': 'OPEN', 'note': '原咨询'},
                     {'action': 'RESOLVED', 'note': '历史咨询已核对'}],
            client_request_id='room-verified-addon-history')
        self.main_order_state = (
            self.order.status, self.order.payment_status, self.order.amount,
            self.order.customer_account_id, self.order.provider_account_id,
            self.order.business_cycle, self.order.service_id,
            self.order.room_required_snapshot, self.order.source_order_id,
            self.order.settled_at,
        )
        self.main_transactions = list(Transaction.objects.filter(order=self.order).order_by('pk').values_list(
            'pk', 'tx_type', 'amount', 'status', 'balance_before', 'balance_after'))
        self.provider_rows = list(OrderProvider.objects.filter(order=self.order).order_by('pk').values_list(
            'pk', 'provider_id', 'provider_account_id', 'provider_business_cycle',
            'released_at', 'settled_at'))
        self.segment_rows = list(OrderParticipationSegment.objects.filter(
            attribution__order=self.order).order_by('pk').values_list(
            'pk', 'attribution_id', 'provider_account_id', 'provider_business_cycle',
            'started_at', 'released_at', 'financial_snapshot__pk'))
        self.aftersale_rows = list(AfterSale.objects.filter(order=self.order).order_by('pk').values_list(
            'pk', 'status', 'history', 'client_request_id'))
        self.room_state = (
            self.room_request.status, self.room_request.participants,
            self.room_request.reviewed_by_id, self.room_request.review_request_id,
            self.room_request.reviewed_at, self.room_confirmation.started_at,
            self.room_confirmation.deadline, self.room_confirmation.status,
        )
        self.card_state = (
            self.card_message.related_order_id, self.card_message.order_snapshot,
            self.card_message.content, self.card_message.client_message_id,
        )
        self.first_payment_state = (
            self.first_payment.intent_id, self.first_payment.intent.request_id,
            self.first_payment.intent.amount, self.first_payment.intent.segment_id,
            self.first_payment.intent.provider_id, self.first_payment.principal,
            self.first_payment.provider_income, self.first_payment.commission_fee,
            self.first_payment.paid_at, self.first_payment.buyer_transaction_id,
            self.first_payment.buyer_transaction.amount,
            self.first_payment.buyer_transaction.balance_before,
            self.first_payment.buyer_transaction.balance_after,
        )

    def assert_original_facts_unchanged(self):
        self.order.refresh_from_db()
        self.assertEqual((
            self.order.status, self.order.payment_status, self.order.amount,
            self.order.customer_account_id, self.order.provider_account_id,
            self.order.business_cycle, self.order.service_id,
            self.order.room_required_snapshot, self.order.source_order_id,
            self.order.settled_at,
        ), self.main_order_state)
        self.assertEqual(list(Transaction.objects.filter(order=self.order).order_by('pk').values_list(
            'pk', 'tx_type', 'amount', 'status', 'balance_before', 'balance_after')),
            self.main_transactions)
        self.assertEqual(list(OrderProvider.objects.filter(order=self.order).order_by('pk').values_list(
            'pk', 'provider_id', 'provider_account_id', 'provider_business_cycle',
            'released_at', 'settled_at')), self.provider_rows)
        self.assertEqual(list(OrderParticipationSegment.objects.filter(
            attribution__order=self.order).order_by('pk').values_list(
            'pk', 'attribution_id', 'provider_account_id', 'provider_business_cycle',
            'started_at', 'released_at', 'financial_snapshot__pk')), self.segment_rows)
        self.assertEqual(list(AfterSale.objects.filter(order=self.order).order_by('pk').values_list(
            'pk', 'status', 'history', 'client_request_id')), self.aftersale_rows)
        self.room_request.refresh_from_db()
        self.room_confirmation.refresh_from_db()
        self.assertEqual((
            self.room_request.status, self.room_request.participants,
            self.room_request.reviewed_by_id, self.room_request.review_request_id,
            self.room_request.reviewed_at, self.room_confirmation.started_at,
            self.room_confirmation.deadline, self.room_confirmation.status,
        ), self.room_state)
        self.card_message.refresh_from_db()
        self.assertEqual((
            self.card_message.related_order_id, self.card_message.order_snapshot,
            self.card_message.content, self.card_message.client_message_id,
        ), self.card_state)
        self.first_payment.refresh_from_db()
        self.assertEqual((
            self.first_payment.intent_id, self.first_payment.intent.request_id,
            self.first_payment.intent.amount, self.first_payment.intent.segment_id,
            self.first_payment.intent.provider_id, self.first_payment.principal,
            self.first_payment.provider_income, self.first_payment.commission_fee,
            self.first_payment.paid_at, self.first_payment.buyer_transaction_id,
            self.first_payment.buyer_transaction.amount,
            self.first_payment.buyer_transaction.balance_before,
            self.first_payment.buyer_transaction.balance_after,
        ), self.first_payment_state)

    def test_boss_can_quote_create_and_pay_from_original_card_after_room_verification(self):
        self.prepare_verified_room()
        self.login(self.boss)

        messages = self.ok(self.client.get('/api/chat/session/'))['messages']
        card = next(row['order_card'] for row in messages if row['id'] == self.card_message.pk)
        self.assertTrue(card['available'])
        self.assertTrue(card['can_open'])
        self.assertEqual((card['id'], card['status'], card['snapshot']['status']),
                         (self.order.pk, Order.Status.IN_SERVICE, Order.Status.IN_SERVICE))

        # The frontend follows the formal order reference on the original card;
        # it does not create a new order or a new consultation episode.
        self.order_path = f'/api/orders/orders/{card["id"]}/'
        quote = self.ok(self.client.post(self.order_path + 'addons/quote/',
            self.selection, format='json'))
        self.assertTrue(quote['intent_available'])
        self.assertEqual((quote['amount_raw'], quote['segment_id']),
                         (1000, self.segment_rows[0][0]))
        self.assertEqual((OrderAddonIntent.objects.count(), OrderAddonPayment.objects.count()), (1, 1))

        wallet = get_wallet(account=self.buyer_account, user=self.boss)
        balance_before = wallet.balance
        intent = self.ok(self.client.post(self.order_path + 'addons/intents/',
            self.intent_payload, format='json'))
        self.assertTrue(intent['payment_available'])
        replay = self.ok(self.client.post(self.order_path + 'addons/intents/',
            self.intent_payload, format='json'))
        self.assertEqual(replay['id'], intent['id'])
        conflict = self.client.post(self.order_path + 'addons/intents/',
            {**self.intent_payload, 'quantity': 2}, format='json')
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(OrderAddonIntent.objects.count(), 2)

        paid = self.ok(self.client.post(
            self.order_path + f'addons/intents/{intent["id"]}/pay/', {}, format='json'))
        paid_replay = self.ok(self.client.post(
            self.order_path + f'addons/intents/{intent["id"]}/pay/', {}, format='json'))
        self.assertTrue(paid['paid'])
        self.assertEqual((paid_replay['id'], paid_replay['payment_id']),
                         (paid['id'], paid['payment_id']))
        payment = OrderAddonPayment.objects.get(pk=paid['payment_id'])
        self.assertEqual((payment.intent_id, payment.intent.segment_id,
                          payment.intent.provider_id, payment.buyer_transaction.amount),
                         (intent['id'], self.segment_rows[0][0],
                          self.provider_account.pk, -1000))
        self.assertEqual(OrderAddonIntent.objects.count(), 2)
        self.assertEqual(OrderAddonPayment.objects.count(), 2)
        self.assertEqual(Transaction.objects.filter(pk=payment.buyer_transaction_id).count(), 1)
        self.assertIsNone(payment.buyer_transaction.order_id)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, balance_before - 1000)
        self.assert_original_facts_unchanged()

        outsider = make_user()
        outsider_account = get_or_create_account_for_legacy_user(outsider)
        denied = self.client_for(outsider_account).post(
            self.order_path + 'addons/quote/', self.selection, format='json')
        self.assertEqual(denied.status_code, 404, denied.data)
        provider_denied = self.client_for(self.provider_account).post(
            self.order_path + 'addons/quote/', self.selection, format='json')
        self.assertEqual(provider_denied.status_code, 404, provider_denied.data)
        wrong_store = self.client_for(self.buyer_account, host='xa.esports.club').get(
            self.order_path + 'addons/intents/')
        self.assertNotEqual(wrong_store.status_code, 200)
        self.assertEqual((OrderAddonIntent.objects.count(), OrderAddonPayment.objects.count()), (2, 2))
        self.assert_original_facts_unchanged()

    @override_settings(RECEIPT_USAGE_LEDGER_ENABLED=False)
    def test_second_addon_requires_existing_receipt_ledger_capability(self):
        self.prepare_verified_room()
        self.login(self.boss)
        wallet = get_wallet(account=self.buyer_account, user=self.boss)
        balance_before = wallet.balance

        quote = self.ok(self.client.post(self.order_path + 'addons/quote/',
            self.selection, format='json'))
        self.assertTrue(quote['intent_available'])
        intent = self.ok(self.client.post(self.order_path + 'addons/intents/',
            self.intent_payload, format='json'))
        self.assertFalse(intent['payment_available'])
        rejected = self.client.post(
            self.order_path + f'addons/intents/{intent["id"]}/pay/', {}, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertEqual((OrderAddonIntent.objects.count(), OrderAddonPayment.objects.count()), (2, 1))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, balance_before)
        self.assertEqual(Transaction.objects.filter(
            pk=self.first_payment.buyer_transaction_id).count(), 1)
        self.assert_original_facts_unchanged()
