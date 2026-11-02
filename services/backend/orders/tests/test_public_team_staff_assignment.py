"""A public team application remains selectable when staff assignment is unsupported."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class PublicTeamStaffAssignmentTest(APITransactionTestCase):
    def test_public_team_cannot_be_staff_assigned_and_boss_can_still_select(self):
        boss = make_user()
        providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                                   pass_expires_at=timezone.now() + timedelta(days=7))
                     for name in ('隔离申请人', '隔离队友')]
        staff = make_console_user(['order:view', 'order:dispatch'])
        accounts = {}
        for user in (boss, *providers, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts[user.pk] = account
        staff.admin_membership.account = accounts[staff.pk]
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2, commission_rate=20)
        for user in providers:
            user.escort_profile.account = accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = accounts[boss.pk]
        wallet.save(update_fields=['account'])

        def login(user):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[user.pk])[0])

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')

        login(boss)
        data = ok(self.client.post('/api/orders/create/', {'service_id': service.pk,
            'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': 'public-team-staff-assign-check'}, format='json'))
        order = Order.objects.get(pk=data['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        login(providers[0])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {'team_size': 2,
            'partner_id': accounts[providers[1].pk].pk}, format='json'))
        application = OrderApplication.objects.get(order=order, applicant=accounts[providers[0].pk])
        login(providers[1])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                            {'action': 'CONFIRM_TEAM'}, format='json'))

        login(staff)
        assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/',
            {'provider_ids': [accounts[p.pk].pk for p in providers]}, format='json')
        self.assertEqual(assigned.data['code'], 400, assigned.data)
        self.assertIn('仅客服待派双陪订单', assigned.data['msg'])
        for endpoint in (f'/api/admin/orders/{order.pk}/assign/', f'/api/orders/orders/{order.pk}/assign/'):
            single = self.client.post(endpoint, {'provider_id': accounts[providers[0].pk].pk}, format='json')
            self.assertEqual(single.data['code'], 400, single.data)
            self.assertIn('双陪', single.data['msg'])
        application.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((order.status, application.status), ('PENDING', 'PENDING'))
        self.assertFalse(order.providers.exists())

        login(boss)
        candidates = ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['applications']
        self.assertTrue(next(row for row in candidates if row['id'] == application.pk)['ready'])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json'))
        order.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual((order.status, application.status), ('GRABBED', 'SELECTED'))
        self.assertEqual(set(order.providers.values_list('provider_account_id', flat=True)),
                         {accounts[p.pk].pk for p in providers})
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
