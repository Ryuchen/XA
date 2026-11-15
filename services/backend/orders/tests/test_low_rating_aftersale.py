from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import AfterSale, Evaluation, Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider
from orders.tests import test_release_flow as flow
from site_messages.models import Message
from engagement.tasks import confirm_due_consumption
from wallet.models import ProviderReport, ProviderReportImage, Transaction
from wallet.services import get_wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LowRatingAfterSaleTests(APITestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok

    def setUp(self):
        flow.ReleaseFlowTest.setUp(self)
        self.second = make_provider(deposit_mode='EARNINGS')
        self.accounts[self.second.pk] = get_or_create_account_for_legacy_user(self.second)
        self.second.escort_profile.account = self.accounts[self.second.pk]
        self.second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.order = make_order(self.boss, self.service, self.provider,
            customer_account=self.accounts[self.boss.pk], provider_account=self.accounts[self.provider.pk],
            status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
            escort_mode=Order.EscortMode.DOUBLE, completed_at=timezone.now())
        for user in (self.provider, self.second):
            OrderProvider.objects.create(order=self.order, provider=user, provider_account=self.accounts[user.pk],
                provider_name_snapshot=user.escort_profile.display_name, settlement_base=5000, provider_income=4000)
        self.login(self.boss)

    def evaluate(self, provider, **scores):
        payload = {'order_id': self.order.pk, 'provider_account_id': self.accounts[provider.pk].pk,
                   'score': 5, 'skill_score': 5, 'attitude_score': 5, 'communication_score': 5}
        payload.update(scores)
        return self.client.post('/api/orders/evaluate/', payload, format='json')

    def test_each_rating_is_required(self):
        payload = {'order_id': self.order.pk, 'provider_account_id': self.accounts[self.provider.pk].pk,
                   'score': 5, 'skill_score': 5, 'attitude_score': 5, 'communication_score': 5}
        for key in ('score', 'skill_score', 'attitude_score', 'communication_score'):
            response = self.client.post('/api/orders/evaluate/', {k: v for k, v in payload.items() if k != key}, format='json')
            self.assertNotEqual(response.data['code'], 0, response.data)
        self.assertFalse(Evaluation.objects.filter(order=self.order).exists())
        self.assertFalse(AfterSale.objects.filter(order=self.order).exists())

    def test_each_low_dimension_opens_a_scoped_case(self):
        # A fresh order for each field; no score can stand in for another.
        for index, key in enumerate(('score', 'skill_score', 'attitude_score', 'communication_score')):
            if index:
                self.order = make_order(self.boss, self.service, self.provider,
                    customer_account=self.accounts[self.boss.pk], provider_account=self.accounts[self.provider.pk],
                    status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
                    escort_mode=Order.EscortMode.DOUBLE, completed_at=timezone.now())
                for user in (self.provider, self.second):
                    OrderProvider.objects.create(order=self.order, provider=user, provider_account=self.accounts[user.pk],
                        settlement_base=5000, provider_income=4000)
            self.ok(self.evaluate(self.provider, **{key: 2}))
            evaluation = Evaluation.objects.get(order=self.order, provider=self.provider)
            case = AfterSale.objects.get(order=self.order)
            self.assertEqual((evaluation.low_rating_aftersale_state, evaluation.low_rating_aftersale_id), ('OPENED', case.pk))
            self.assertEqual(case.affected_provider_account_ids, [self.accounts[self.provider.pk].pk])
            self.assertEqual(case.client_request_id, f'low-rating:{evaluation.pk}')
            self.assertEqual(case.history[0]['client_request_id'], case.client_request_id)
            self.assertEqual(case.history[-1]['evaluation_id'], evaluation.pk)

    def test_high_rating_has_no_case(self):
        self.ok(self.evaluate(self.provider, communication_score=3))
        self.assertEqual(Evaluation.objects.get(order=self.order).low_rating_aftersale_state, '')
        self.assertFalse(AfterSale.objects.filter(order=self.order).exists())

    def test_low_rating_after_deadline_records_alert_without_opening_or_changing_case(self):
        existing = AfterSale.objects.create(order=self.order, account=self.accounts[self.boss.pk],
            reason='旧申请', affected_provider_account_ids=[self.accounts[self.provider.pk].pk])
        deadline = self.order.completed_at + timedelta(hours=self.order.service_aftersale_hours)
        with patch('django.utils.timezone.now', return_value=deadline):
            self.ok(self.evaluate(self.second, score=1))
        row = Evaluation.objects.get(order=self.order)
        self.assertEqual((row.low_rating_aftersale_state, row.low_rating_aftersale_id), ('EXPIRED', None))
        existing.refresh_from_db()
        self.assertEqual(existing.affected_provider_account_ids, [self.accounts[self.provider.pk].pk])
        self.assertEqual(existing.history, [])
        admin = make_console_user(['evaluation:view'])
        account = get_or_create_account_for_legacy_user(admin)
        admin.admin_membership.account = account
        admin.admin_membership.save(update_fields=['account'])
        self.accounts[admin.pk] = account
        self.login(admin)
        rows = self.ok(self.client.get('/api/admin/evaluations/'))['list']
        self.assertEqual(next(item for item in rows if item['id'] == row.pk)['low_rating_aftersale_state'], 'EXPIRED')

    def test_existing_targeted_case_expands_without_breaking_original_request_retry(self):
        original = {'reason': '第一位的服务问题', 'client_request_id': 'original-case',
                    'affected_provider_account_ids': [self.accounts[self.provider.pk].pk]}
        case = self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/aftersale/', original, format='json'))
        with self.captureOnCommitCallbacks(execute=True):
            self.ok(self.evaluate(self.second, attitude_score=1))
        row = Evaluation.objects.get(order=self.order, provider=self.second)
        saved = AfterSale.objects.get(pk=case['id'])
        self.assertEqual(row.low_rating_aftersale_state, 'ATTACHED')
        self.assertEqual(saved.affected_provider_account_ids,
                         sorted([self.accounts[self.provider.pk].pk, self.accounts[self.second.pk].pk]))
        self.assertTrue(Message.objects.filter(recipient_account=self.accounts[self.second.pk],
                                              related_order_id=self.order.pk, title='订单售后处理中').exists())
        replay = self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/aftersale/', original, format='json'))
        self.assertEqual(replay['id'], case['id'])

    def test_low_rating_does_not_reopen_resolved_case(self):
        case = AfterSale.objects.create(order=self.order, account=self.accounts[self.boss.pk],
            reason='已处理', status='RESOLVED', resolved_at=timezone.now())
        self.ok(self.evaluate(self.second, skill_score=1))
        row = Evaluation.objects.get(order=self.order)
        case.refresh_from_db()
        self.assertEqual((row.low_rating_aftersale_state, row.low_rating_aftersale_id, case.status),
                         ('CLOSED', case.pk, 'RESOLVED'))

    def test_legacy_store_without_negotiation_tables_keeps_low_rating_behavior(self):
        from stores.db import transaction
        from orders.models import NegotiationExecution
        case = AfterSale.objects.create(order=self.order, account=self.accounts[self.boss.pk],
            reason='旧申请', status='RESOLVED', resolved_at=timezone.now())
        connection = transaction.get_connection()
        names = connection.introspection.table_names
        with patch.object(connection.introspection, 'table_names',
                          side_effect=lambda cursor=None, **kwargs: [name for name in names(cursor, **kwargs)
                              if name != NegotiationExecution._meta.db_table]):
            self.ok(self.evaluate(self.second, skill_score=1))
        row = Evaluation.objects.get(order=self.order)
        self.assertEqual((row.low_rating_aftersale_state, row.low_rating_aftersale_id), ('CLOSED', case.pk))

    def test_low_rating_blocks_only_affected_share_until_case_is_resolved(self):
        self.order.provider_income = 8000
        self.order.shop_income = 2000
        self.order.save(update_fields=['provider_income', 'shop_income'])
        wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
        wallet.balance = 0
        wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=wallet, order=self.order, tx_type='PAY', amount=-10000,
                                   balance_before=10000, balance_after=0)
        for user in (self.provider, self.second):
            report = ProviderReport.objects.create(order=self.order, provider=user, provider_account=self.accounts[user.pk],
                amount=5000, payout_amount=4000, status='APPROVED', game_name='双陪测试',
                submitted_at=timezone.now(), entry_image=flow.screenshot(), completion_image=flow.screenshot())
            ProviderReportImage.objects.create(report=report, image=flow.screenshot())
        self.ok(self.evaluate(self.provider, communication_score=2))
        case = AfterSale.objects.get(order=self.order)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertEqual(confirm_due_consumption(), 0)
            self.assertIsNone(OrderProvider.objects.get(order=self.order, provider=self.provider).settled_at)
            self.assertIsNotNone(OrderProvider.objects.get(order=self.order, provider=self.second).settled_at)
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/aftersales/{case.pk}/resolve/', {'resolution': '低星问题已处理'}, format='json'))
            self.assertEqual(confirm_due_consumption(), 1)
            self.assertEqual(confirm_due_consumption(), 0)
        self.assertEqual(self.order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).count(), 1)
        self.assertEqual(self.order.transactions.filter(tx_type='INCOME', wallet__user=self.second).count(), 1)

    def test_authorized_void_preserves_low_rating_case_and_prevents_reevaluation(self):
        self.ok(self.evaluate(self.provider, skill_score=1))
        evaluation = Evaluation.objects.get(order=self.order, provider=self.provider)
        case_id = evaluation.low_rating_aftersale_id
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.rating_count, 1)
        limited = make_console_user(['evaluation:view'])
        admin = make_console_user(['evaluation:view', 'evaluation:delete'])
        for user in (limited, admin):
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            self.accounts[user.pk] = account
        path = f'/api/admin/evaluations/{evaluation.pk}/'
        self.login(limited)
        self.assertEqual(self.client.delete(path, {'reply_version': 0, 'void_reason': '评价违规'}, format='json').status_code, 403)
        self.login(admin)
        missing = self.client.delete(path, {'reply_version': 0}, format='json')
        self.assertNotEqual(missing.data['code'], 0)
        self.ok(self.client.delete(path, {'reply_version': 0, 'void_reason': '评价违规'}, format='json'))
        evaluation.refresh_from_db()
        self.provider.escort_profile.refresh_from_db()
        self.assertIsNotNone(evaluation.voided_at)
        self.assertEqual((evaluation.void_reason, evaluation.voided_by_id), ('评价违规', self.accounts[admin.pk].pk))
        self.assertEqual(self.provider.escort_profile.rating_count, 0)
        self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'OPEN')
        self.assertEqual(self.client.delete(path, {'reply_version': 0, 'void_reason': '重复作废'}, format='json').status_code, 409)
        self.login(self.boss)
        self.assertNotEqual(self.evaluate(self.provider, score=5).data['code'], 0)
        detail = self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))
        target = next(row for row in detail['evaluation_targets'] if row['provider_account_id'] == self.accounts[self.provider.pk].pk)
        self.assertTrue(target['reviewed'])
        self.assertFalse(any(row['id'] == evaluation.pk for row in detail['evaluations']))
        public = self.ok(self.client.get(f'/api/orders/services/{self.service.pk}/evaluations/'))
        self.assertEqual(public['total'], 0)
        self.login(self.provider)
        self.assertEqual(self.ok(self.client.get('/api/orders/evaluations/mine/'))['total'], 0)
        reply = self.client.post(f'/api/orders/evaluations/{evaluation.pk}/reply/',
                                 {'reply_content': '回复', 'reply_version': 1}, format='json')
        self.assertNotEqual(reply.data['code'], 0)
