"""三端共用订单流转契约测试。"""

from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from users.models import EscortProfile
from users.test_voice_recordings import audio_file
from wallet.models import ProviderReport, Transaction


@override_settings(CELERY_TASK_ALWAYS_EAGER=False, LEGACY_FORCE_AUTH_COMPAT=False)
class CrossEndOrderLifecycleTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider(deposit_paid=40000, status=EscortProfile.Status.AVAILABLE)
        self.provider.escort_profile.account = get_or_create_account_for_legacy_user(self.provider)
        self.provider.escort_profile.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)

    def login(self, user):
        account = get_or_create_account_for_legacy_user(user)
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def _create_visible_order(self):
        staff = make_console_user(['wallet:recharge', 'report:audit'])
        staff.admin_membership.account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.save(update_fields=['account'])
        self.login(staff)
        recharge = self.client.post('/api/admin/wallets/recharge/', {
            'account_id': get_or_create_account_for_legacy_user(self.customer).pk,
            'amount': 20000, 'trade_no': 'cross-end-receipt', 'proof_image': screenshot(),
            'client_request_id': 'cross-end-recharge',
        }, format='multipart')
        self.assertEqual(recharge.data['code'], 0, recharge.data)
        self.login(self.customer)
        response = self.client.post('/api/orders/create/', {
            'service_id': self.service.id, 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': 'cross-end-audition',
        })
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(id=response.data['data']['id'])
        old = timezone.now() - timedelta(minutes=10)
        Order.objects.filter(id=order.id).update(created_at=old)
        order.refresh_from_db()
        return order, staff

    @override_settings(AUDITION_RECORDINGS_REQUIRED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_audition_assignment_cannot_be_self_rejected_but_can_be_completed(self):
        media = self.enterContext(TemporaryDirectory(prefix='xa-order-lifecycle-'))
        self.enterContext(override_settings(MEDIA_ROOT=media))
        order, staff = self._create_visible_order()

        # 老板只能取消自己的已支付待接单订单。
        boss_list = self.client.get('/api/orders/orders/?status=pending')
        self.assertEqual(boss_list.data['data'][0]['allowed_actions'], ['CANCEL', 'SELECT_PROVIDER'])

        # 陪玩看到后端授权的抢单动作。
        self.login(self.provider)
        pool = self.client.get('/api/orders/orders/?role=provider&status=pending')
        self.assertEqual({row['id']: row['allowed_actions'] for row in pool.data['data']}, {order.pk: ['GRAB']})

        voice = self.client.post('/api/users/recordings/', {
            'audio': audio_file(), 'title': '本人试音', 'client_request_id': 'cross-end-audition-recording',
        }, format='multipart')
        self.assertEqual(voice.data['code'], 0, voice.data)
        grabbed = self.client.post(f'/api/orders/orders/{order.id}/grab/',
                                   {'recording_id': voice.data['data']['id']}, format='json')
        self.assertEqual(grabbed.data['code'], 0, grabbed.data)
        application_id = grabbed.data['data']['my_applications'][0]['id']
        pool = self.client.get('/api/orders/orders/?status=pending')
        pool_order, = pool.data['data']
        self.assertEqual([row['id'] for row in pool_order['my_applications']], [application_id])
        self.login(self.customer)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/applications/{application_id}/select/').data['code'],0)
        self.login(self.provider)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertIsNone(order.auto_cancel_at)
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.status, EscortProfile.Status.BUSY)

        accepted = self.client.get('/api/orders/orders/?status=grabbed')
        self.assertEqual(accepted.data['data'][0]['allowed_actions'], ['START'])

        rejected = self.client.post(f'/api/orders/orders/{order.id}/reject/', {'reason': '档期冲突'})
        self.assertEqual(rejected.data['code'], 400)
        self.assertEqual(rejected.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_id, order.auto_cancel_at), ('GRABBED', self.provider.pk, None))
        self.assertFalse(order.status_logs.filter(action='REJECT').exists())
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/start/', {
            'entry_image': screenshot(),
        }, format='multipart').data['code'], 0)
        in_service = self.client.get('/api/orders/orders/?status=in_progress')
        self.assertEqual(in_service.data['data'][0]['allowed_actions'], ['COMPLETE'])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/complete/', {
            'completion_image': screenshot(),
        }, format='multipart').data['code'], 0)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertFalse(order.transactions.filter(tx_type=Transaction.TxType.INCOME).exists())
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        uploaded = self.client.post(f'/api/wallet/reports/{report.pk}/images/', {
            'kind': 'RESULT', 'image': screenshot(),
        }, format='multipart')
        self.assertEqual(uploaded.data['code'], 0, uploaded.data)
        submitted = self.client.post(f'/api/wallet/reports/{report.pk}/submit/')
        self.assertEqual(submitted.data['code'], 0, submitted.data)
        self.login(staff)
        approved = self.client.post(f'/api/admin/reports/{report.pk}/approve/')
        self.assertEqual(approved.data['code'], 0, approved.data)
        order.refresh_from_db()
        from engagement.confirmation import process_order
        with patch('django.utils.timezone.now', return_value=order.consumption_ready_at + timedelta(seconds=1)):
            self.assertTrue(process_order(order.pk))
            process_order(order.pk)

        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)
        self.assertCountEqual(order.transactions.values_list('tx_type', 'amount'), [
            (Transaction.TxType.PAY, -10000),
            (Transaction.TxType.INCOME, 8000),
            (Transaction.TxType.SHOP_INCOME, 2000),
        ])
        # 终态不能重复结算。
        self.login(self.provider)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/complete/').data['code'], 400)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)

        self.login(self.customer)
        completed = self.client.get('/api/orders/orders/?status=completed')
        self.assertEqual(
            completed.data['data'][0]['allowed_actions'],
            ['EVALUATE', 'TIP'],
        )

    def test_customer_cannot_spoof_provider_pool_with_query_parameter(self):
        mine = make_order(
            self.customer, service=self.service, status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
        )
        other_customer = make_user()
        other = make_order(
            other_customer, service=self.service, status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
        )
        self.login(self.customer)
        response = self.client.get('/api/orders/orders/?role=provider&status=pending')
        ids = {row['id'] for row in response.data['data']}
        self.assertIn(mine.id, ids)
        self.assertNotIn(other.id, ids)

    def test_expired_audition_is_hidden_and_cannot_receive_an_application(self):
        order = make_order(
            self.customer,
            service=self.service,
            status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
            requires_boss_selection=True,
            auto_cancel_at=timezone.now() - timedelta(seconds=1),
        )
        Order.objects.filter(id=order.id).update(created_at=timezone.now() - timedelta(minutes=10))
        self.login(self.provider)
        pool = self.client.get('/api/orders/orders/?role=provider&status=pending')
        self.assertNotIn(order.id, {row['id'] for row in pool.data['data']})
        grabbed = self.client.post(f'/api/orders/orders/{order.id}/grab/')
        self.assertEqual(grabbed.data['code'], 400)
        self.assertIn('已结束报名', grabbed.data['msg'])

    def test_paid_automatic_order_without_round_is_hidden_from_unassigned_provider(self):
        order = make_order(
            self.customer, service=self.service, payment_status=Order.PaymentStatus.PAID,
            requires_boss_selection=False, auto_cancel_at=timezone.now() + timedelta(minutes=10),
        )
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        path = f'/api/orders/orders/{order.pk}/'
        self.login(self.provider)
        pool = self.client.get('/api/orders/orders/?status=pending')
        self.assertNotIn(order.pk, {row['id'] for row in pool.data['data']})
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(path + 'grab/').status_code, 503)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, order.provider_id, order.provider_account_id),
                         ('PENDING', 'PAID', None, None))
        self.assertFalse(order.status_logs.exists())
        self.assertFalse(order.transactions.exists())
        OrderProvider.objects.create(order=order, provider=self.provider,
                                     provider_account=get_or_create_account_for_legacy_user(self.provider),
                                     released_at=timezone.now())
        detail = self.client.get(path)
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn('GRAB', detail.data['data']['allowed_actions'])
        self.login(self.customer)
        detail = self.client.get(path)
        self.assertEqual(detail.data['data']['allowed_actions'], ['CANCEL'])

    def test_double_order_cannot_be_rejected_by_primary_provider(self):
        order = make_order(
            self.customer,
            service=self.service,
            provider=self.provider,
            status=Order.Status.GRABBED,
            payment_status=Order.PaymentStatus.PAID,
            escort_mode=Order.EscortMode.DOUBLE,
        )
        self.login(self.provider)
        response = self.client.post(f'/api/orders/orders/{order.id}/reject/', {})
        self.assertEqual(response.data['code'], 400)
        self.assertEqual(response.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, order.provider_id), ('GRABBED', 'PAID', self.provider.pk))
        self.assertFalse(order.status_logs.exists())
        self.assertFalse(order.transactions.exists())
