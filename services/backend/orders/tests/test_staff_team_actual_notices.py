"""Actual inbox records for a customer-created staff-dispatched two-person order."""
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class StaffTeamActualNoticesTest(APITransactionTestCase):
    def test_customer_order_staff_assignment_reaches_each_party_once(self):
        boss = make_user()
        staff = make_console_user(['order:view', 'order:dispatch'])
        providers = [make_provider(display_name='隔离派单甲'), make_provider(display_name='隔离派单乙')]
        other = make_provider(display_name='隔离无关陪玩')
        users = [boss, staff, *providers, other]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]
        staff.admin_membership.save(update_fields=['account'])
        for user, account in zip([*providers, other], accounts[2:]):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(name='隔离指派双陪', price=10000, billing_provider_count=2)
        client = APIClient()
        def auth(account):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        auth(accounts[0])
        order = ok(client.post('/api/orders/create/', {'service_id':service.pk, 'dispatch_route':'STAFF',
                   'client_request_id':'staff-two-actual-notices'}, format='json'))
        order_id = order['id']
        auth(accounts[4])
        self.assertEqual(client.get(f'/api/orders/orders/{order_id}/').status_code, 404)
        self.assertNotIn(order_id, [item['id'] for item in ok(client.get('/api/orders/orders/', {'status':'pending'}))])
        auth(accounts[1])
        self.assertIn(order_id, [item['id'] for item in ok(client.get('/api/admin/orders/', {'actionable_staff':'1'}))['list']])
        result = ok(client.post(f'/api/admin/orders/{order_id}/assign/',
                    {'provider_ids':[account.pk for account in accounts[2:4]]}, format='json'))
        self.assertEqual(result['status'], 'GRABBED')
        self.assertNotEqual(client.post(f'/api/admin/orders/{order_id}/assign/',
                            {'provider_ids':[account.pk for account in accounts[2:4]]}, format='json').data['code'], 0)
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='PAY').count(), 1)
        auth(accounts[0])
        order = ok(client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual({item['id'] for item in order['participants']}, {account.pk for account in accounts[2:4]})
        inbox = ok(client.get('/api/messages/'))['messages']
        self.assertEqual(sum(item['title']=='已为你指派双陪' and item['related_order_id']==order_id for item in inbox), 1)
        for account in accounts[2:4]:
            auth(account)
            inbox = ok(client.get('/api/messages/'))['messages']
            notice = [item for item in inbox if item['title']=='客服派单给你' and item['related_order_id']==order_id]
            self.assertEqual(len(notice), 1)
            self.assertEqual(ok(client.get('/api/messages/assignment-alerts/', {'after':0})),
                             {'latest_id':notice[0]['id'], 'pending':True})
            order = ok(client.get(f'/api/orders/orders/{order_id}/'))
            self.assertIn('START', order['allowed_actions'])
        auth(accounts[4])
        self.assertEqual(ok(client.get('/api/messages/assignment-alerts/', {'after':0})), {'latest_id':0, 'pending':False})
