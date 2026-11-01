"""A purchased public team order from applications through actual settlement."""
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from orders.models import Order, OrderApplication
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class PublicTeamFullJourneyTest(APITransactionTestCase):
    def test_team_application_to_confirmed_settlement(self):
        media = TemporaryDirectory(prefix='xa-public-team-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = make_user()
        providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                                   pass_expires_at=timezone.now() + timedelta(days=7))
                     for name in ('双陪申请人', '双陪队友')]
        staff = make_console_user(['order:view', 'report:view', 'report:audit'])
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

        def auth(user):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[user.pk])[0])

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')

        auth(boss)
        data = ok(self.client.post('/api/orders/create/', {'service_id': service.pk,
            'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': 'public-team-full-journey'}, format='json'))
        order = Order.objects.get(pk=data['id'])
        self.assertEqual((order.status, order.required_provider_count), ('PENDING', 2))
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))

        auth(providers[0])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {'team_size': 2,
            'partner_id': accounts[providers[1].pk].pk}, format='json'))
        application = OrderApplication.objects.get(order=order, applicant=accounts[providers[0].pk])
        auth(boss)
        candidate = ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['applications'][0]
        self.assertFalse(candidate['ready'])
        auth(providers[1])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                            {'action': 'CONFIRM_TEAM'}, format='json'))
        auth(boss)
        candidate = ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['applications'][0]
        self.assertTrue(candidate['ready'])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'GRABBED')
        shares = {row.provider_account_id: row for row in order.providers.filter(released_at__isnull=True)}
        self.assertEqual(set(shares), {accounts[p.pk].pk for p in providers})
        self.assertEqual(sum(row.settlement_base for row in shares.values()), order.amount)

        for user in providers:
            auth(user)
            detail = ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual(detail['status'], 'GRABBED')
            ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        for user in providers:
            auth(user)
            ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        self.assertIsNone(order.settled_at)
        for user in providers:
            auth(user)
            report = ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json'))
            self.assertEqual(report['amount'], shares[accounts[user.pk].pk].settlement_base)
            ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
            ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
        auth(staff)
        self.assertEqual(ok(self.client.get(f'/api/admin/orders/{order.pk}/'))['status'], 'COMPLETED')
        for report in ProviderReport.objects.filter(order=order):
            ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertTrue(process_order(order.pk))
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 50000 - order.amount)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        for user in providers:
            share = shares[accounts[user.pk].pk]
            self.assertEqual(Wallet.objects.get(account=accounts[user.pk]).balance, share.provider_income)
            self.assertEqual(Transaction.objects.filter(order=order, wallet__account=accounts[user.pk], tx_type='INCOME').count(), 1)
