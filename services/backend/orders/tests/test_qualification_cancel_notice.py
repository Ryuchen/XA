"""Real qualification/ordinary cancellation facts and the current native confirmation."""
import json
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.models import Order
from orders.tests.factories import make_console_user, make_coupon, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class QualificationCancelNoticeTest(LiveServerTestCase):
    def test_real_cancellation_discloses_frozen_coupon_fact_and_actual_outcome(self):
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view'])
        outsider = make_user()
        users = (boss, provider, staff, outsider)
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(price=10000)
        make_wallet(boss, balance=50000)
        coupons = [make_coupon(name='资格券', discount_type='QUALIFICATION', amount=0, threshold=0,
                               service_ids=[service.pk], limit_units=1),
                   make_coupon(name='普通券', discount_type='DIRECT', amount=1000, threshold=0)]
        clients = [APIClient() for _ in users]
        tokens = [issue_account_tokens(account) for account in accounts]
        for client, token in zip(clients, tokens):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')
        orders = []
        for index, coupon in enumerate(coupons):
            ok(clients[0].post(f'/api/coupons/{coupon.pk}/claim/'))
            claim = UserCoupon.objects.get(account=accounts[0], coupon=coupon)
            order = ok(clients[0].post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':claim.pk,
                'dispatch_route':'PUBLIC', 'client_request_id':f'qual-cancel-notice-{index}'}, format='json'))
            claim.refresh_from_db()
            self.assertEqual(claim.restore_on_refund, index == 1)
            orders.append({'id':order['id'], 'name':coupon.name})
        # Historical orders can still belong to a legacy customer identity.
        Order.objects.filter(pk=orders[0]['id']).update(customer_account=None)
        # Changing the template after use must not change the frozen refund policy.
        Coupon.objects.filter(pk=coupons[0].pk).update(discount_type='DIRECT', amount=1000)
        for index, order in enumerate(orders):
            self.assertEqual(ok(clients[0].get(f"/api/orders/orders/{order['id']}/"))['cancel_coupon_nonrefundable'], index == 0)
        with CaptureQueriesContext(connection) as queries:
            rows = ok(clients[0].get('/api/orders/orders/'))
        self.assertEqual({row['id']:row['cancel_coupon_nonrefundable'] for row in rows if row['id'] in {item['id'] for item in orders}},
                         {orders[0]['id']:True, orders[1]['id']:False})
        coupon_reads = [query['sql'] for query in queries.captured_queries
                        if 'coupons_usercoupon' in query['sql'].lower() and query['sql'].lstrip().upper().startswith('SELECT')]
        self.assertEqual(len(coupon_reads), 1, coupon_reads)
        for order in orders:
            self.assertEqual(clients[3].get(f"/api/orders/orders/{order['id']}/").status_code, 404)
            self.assertNotIn('cancel_coupon_nonrefundable', ok(clients[2].get(f"/api/admin/orders/{order['id']}/")))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/native-qualification-cancel-live.cjs')], cwd=repo,
            input=json.dumps({'base':self.live_server_url, 'orders':orders,
                'session':{'token':tokens[0][0], 'refresh':tokens[0][1],
                           'user':{'id':str(accounts[0].pk), 'role':'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        for index, (coupon, order) in enumerate(zip(coupons, orders)):
            claim = UserCoupon.objects.get(account=accounts[0], coupon=coupon)
            self.assertEqual(claim.status, 'USED' if index == 0 else 'UNUSED')
            self.assertEqual(clients[3].get(f"/api/orders/orders/{order['id']}/").status_code, 404)
            detail = ok(clients[2].get(f"/api/admin/orders/{order['id']}/"))
            self.assertEqual((detail['status'], detail['payment_status']), ('CANCELLED', 'REFUNDED'))
            self.assertEqual(Transaction.objects.filter(order_id=order['id'], tx_type='REFUND').count(), 1)
            self.assertNotIn(order['id'], [row['id'] for row in ok(clients[1].get('/api/orders/orders/', {'status':'pending'}))])
        self.assertEqual(Wallet.objects.get(user=boss).balance, 50000)
        # An explicit account remains authoritative over a stale legacy user.
        Order.objects.filter(pk=orders[1]['id']).update(customer=outsider)
        self.assertEqual(ok(clients[0].get(f"/api/orders/orders/{orders[1]['id']}/"))['refund_summary']['records'][0]['coupon_results'][0]['outcome'], 'RETURNED')
        self.assertEqual(clients[3].get(f"/api/orders/orders/{orders[1]['id']}/").status_code, 404)
