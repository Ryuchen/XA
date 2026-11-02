"""Two real clients evaluate the same fulfilled order at the same time."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from io import StringIO

from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from orders.models import Evaluation, Order
from orders.tests import test_dispatch_real_workflow as workflow


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ORDER_SETTLEMENT_MODE='COMPLETION')
@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locking')
class EvaluationConcurrentWorkflowTest(APITransactionTestCase):
    setUp = workflow.DispatchRealWorkflowTest.setUp
    quote = workflow.DispatchRealWorkflowTest.quote
    payload = workflow.DispatchRealWorkflowTest.payload
    run_fulfillment_flow = workflow.DispatchRealWorkflowTest.run_fulfillment_flow

    def test_two_boss_clients_create_only_one_review_after_real_fulfillment(self):
        order = self.run_fulfillment_flow('COMPLETION')
        token, _ = issue_account_tokens(self.boss_account)
        barrier = Barrier(2)

        def submit(content):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                def synchronize(execute, sql, params, many, context):
                    if 'orders_order' in sql and 'FOR UPDATE' in sql.upper():
                        barrier.wait(timeout=10)
                    return execute(sql, params, many, context)
                # Coordinate just before both genuine lock queries execute.
                with connection.execute_wrapper(synchronize):
                    response = client.post('/api/orders/evaluate/', {
                        'order_id': order.pk, 'score': 4, 'content': content,
                    }, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, content) for content in ('第一份评价', '第二份评价')]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(result['code'] for _, result in results), [0, 400], results)
        self.assertTrue(all(status < 500 for status, _ in results), results)
        self.assertEqual(Evaluation.objects.filter(order=order).count(), 1)
        order.provider.escort_profile.refresh_from_db()
        self.assertEqual(order.provider.escort_profile.rating_count, 1)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(self.client.get(f'/api/orders/orders/{order.pk}/').data['data']['evaluation']['score'], 4)

    def test_two_orders_for_same_provider_refresh_one_accurate_summary(self):
        first = self.run_fulfillment_flow('COMPLETION')
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        second = Order.objects.get(pk=created.data['data']['id'])
        for action, body in (('assign', {'provider_id': first.provider_account_id}), ('start', {}), ('complete', {})):
            response = self.client.post(f'/api/admin/orders/{second.pk}/{action}/', body, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        token, _ = issue_account_tokens(self.boss_account)
        barrier = Barrier(2)

        def submit(order_id, score):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                def synchronize(execute, sql, params, many, context):
                    if 'users_escortprofile' in sql and 'FOR UPDATE' in sql.upper():
                        barrier.wait(timeout=10)
                    return execute(sql, params, many, context)
                with connection.execute_wrapper(synchronize):
                    return client.post('/api/orders/evaluate/', {'order_id': order_id, 'score': score}, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, first.pk, 5), pool.submit(submit, second.pk, 1)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual([result['code'] for result in results], [0, 0], results)
        profile = first.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual((profile.rating_count, float(profile.rating_avg)), (2, 3.0))
        provider_token, _ = issue_account_tokens(first.provider_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        actual = self.client.get('/api/orders/evaluations/mine/').data['data']
        self.assertEqual((actual['total'], actual['avg_score']), (2, 3.0))

    def test_rating_repair_and_new_review_serialize_on_profile(self):
        first = self.run_fulfillment_flow('COMPLETION')
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        second = Order.objects.get(pk=created.data['data']['id'])
        for action, body in (('assign', {'provider_id': first.provider_account_id}), ('start', {}), ('complete', {})):
            response = self.client.post(f'/api/admin/orders/{second.pk}/{action}/', body, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        initial = self.client.post('/api/orders/evaluate/', {'order_id': first.pk, 'score': 4}, format='json')
        self.assertEqual(initial.data['code'], 0, initial.data)
        profile = first.provider.escort_profile
        type(profile).objects.filter(pk=profile.pk).update(rating_avg=3)
        barrier = Barrier(2)

        def synchronize(execute, sql, params, many, context):
            if 'users_escortprofile' in sql and 'FOR UPDATE' in sql.upper():
                barrier.wait(timeout=10)
            return execute(sql, params, many, context)

        def repair():
            close_old_connections()
            try:
                with connection.execute_wrapper(synchronize):
                    output = StringIO()
                    call_command('audit_escort_ratings', '--repair-averages', stdout=output)
                    return output.getvalue()
            finally:
                close_old_connections()

        def submit():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                with connection.execute_wrapper(synchronize):
                    return client.post('/api/orders/evaluate/', {'order_id': second.pk, 'score': 1}, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            repair_future, review_future = pool.submit(repair), pool.submit(submit)
            self.assertIn('scanned=', repair_future.result(timeout=30))
            self.assertEqual(review_future.result(timeout=30)['code'], 0)
        profile.refresh_from_db()
        self.assertEqual((profile.rating_count, float(profile.rating_avg)), (2, 2.5))
