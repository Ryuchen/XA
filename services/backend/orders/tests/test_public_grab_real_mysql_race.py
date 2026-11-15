"""Two real providers contend for one paid public order under MySQL locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_cancel_grab_mysql_race import waiting_on_order
from orders.tests.test_release_flow import screenshot
from wallet.models import Transaction, Wallet


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class PublicGrabRealRaceTest(TransactionTestCase):
    def test_two_providers_contend_and_winner_starts_service(self):
        media=TemporaryDirectory(prefix='xa-public-grab-race-');self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss=make_user();providers=[make_provider(display_name=name,deposit_mode='EARNINGS') for name in ('抢单陪玩甲','抢单陪玩乙')]
        staff=make_console_user(['wallet:recharge','order:view'])
        accounts=[]
        for user in (boss,*providers,staff):
            account=get_or_create_account_for_legacy_user(user);link_legacy_relations(user,account);accounts.append(account)
        staff.admin_membership.account=accounts[-1];staff.admin_membership.save(update_fields=['account'])
        for user,account in zip(providers,accounts[1:3]):
            user.escort_profile.account=account;user.escort_profile.save(update_fields=['account'])
        service=make_service(price=10000,billing_provider_count=1,commission_rate=20)
        tokens=[issue_account_tokens(account)[0] for account in accounts]
        def client(token):
            obj=APIClient();obj.credentials(HTTP_AUTHORIZATION='Bearer '+token);return obj
        def ok(response):
            self.assertEqual(response.data['code'],0,response.data);return response.data['data']
        ok(client(tokens[3]).post('/api/admin/wallets/recharge/',{'account_id':accounts[0].pk,'amount':50000,
            'gift_amount':0,'trade_no':'public-grab-race-recharge','proof_image':screenshot(),
            'client_request_id':'public-grab-race-recharge'},format='multipart'))
        created=ok(client(tokens[0]).post('/api/orders/create/',{'service_id':service.pk,'dispatch_route':'PUBLIC',
            'dispatch_mode':'AUTO_GRAB','client_request_id':'public-grab-race'},format='json'))
        order_id=created['id'];Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now()-timedelta(hours=1))
        def grab(token):
            close_old_connections()
            try:return client(token).post(f'/api/orders/orders/{order_id}/grab/',{},format='json').data
            finally:close_old_connections()
        observer=connections['default'].copy()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                with transaction.atomic():
                    Order.objects.select_for_update().get(pk=order_id)
                    futures=[pool.submit(grab,token) for token in tokens[1:3]]
                    deadline=monotonic()+15
                    while waiting_on_order(observer)<2 and monotonic()<deadline:sleep(.05)
                    self.assertGreaterEqual(waiting_on_order(observer),2,'Both real grabs must reach the order lock')
                results=[future.result(timeout=20) for future in futures]
        finally:observer.close()
        self.assertEqual(sum(row['code']==0 for row in results),1,results)
        order=Order.objects.get(pk=order_id);self.assertEqual(order.status,'GRABBED')
        winner_index=next(i for i,account in enumerate(accounts[1:3]) if account.pk==order.provider_account_id)
        self.assertEqual(order.providers.filter(released_at__isnull=True).count(),1)
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance,40000)
        self.assertEqual(Transaction.objects.filter(order=order,tx_type='PAY').count(),1)
        self.assertFalse(Transaction.objects.filter(order=order,tx_type='REFUND').exists())
        for token,url in ((tokens[0],f'/api/orders/orders/{order_id}/'),(tokens[3],f'/api/admin/orders/{order_id}/')):
            detail=ok(client(token).get(url));self.assertEqual(detail['status'],'GRABBED')
        winner=client(tokens[1+winner_index]);detail=ok(winner.get(f'/api/orders/orders/{order_id}/'))
        self.assertIn('START',detail['allowed_actions'])
        ok(winner.post(f'/api/orders/orders/{order_id}/start/',{'entry_image':screenshot()},format='multipart'))
        self.assertEqual(ok(client(tokens[0]).get(f'/api/orders/orders/{order_id}/'))['status'],'IN_SERVICE')
        self.assertEqual(ok(client(tokens[3]).get(f'/api/admin/orders/{order_id}/'))['status'],'IN_SERVICE')
