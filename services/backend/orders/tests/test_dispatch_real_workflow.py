"""隔离数据库中的真实接口验收：鉴权、试算、派单与余额变动。"""
from datetime import datetime, time, timedelta
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from orders.models import AfterSale, Evaluation, GameCategory, KookDispatchRecord, Order, WecomDispatchRecord
from orders.tests.factories import make_console_user, make_promotion, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import DisposeRecord, ProviderReport, SystemConfig, Transaction, Wallet
from support.models import SupportContactCard


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DispatchRealWorkflowTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.staff = make_console_user(['dashboard:view', 'order:view', 'order:dispatch', 'order:settle', 'order:cancel', 'report:view', 'report:audit'])
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        self.staff.admin_membership.account = self.staff_account
        self.staff.admin_membership.save(update_fields=['account'])
        self.service = make_service(price=10000, billing_provider_count=1, commission_rate=20)
        make_wallet(self.boss, balance=100000)
        token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def quote(self):
        response = self.client.get('/api/admin/orders/dispatch-quote/', {
            'customer_id': self.boss_account.pk, 'service_id': self.service.pk, 'game_rounds': 1,
        })
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def payload(self, expected):
        return {
            'customer_id': self.boss_account.pk, 'service_id': self.service.pk,
            'game_rounds': 1, 'escort_mode': 'SINGLE', 'providers': [],
            'expected_amount': expected, 'client_request_id': str(uuid4()),
        }

    def test_quote_then_dispatch_to_lobby_debits_once(self):
        quote = self.quote()
        response = self.client.post('/api/admin/orders/dispatch/', self.payload(quote['amount']), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.status, order.amount), ('PENDING', quote['amount']))
        self.assertFalse(order.providers.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000 - quote['amount'])
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def test_dashboard_paid_pending_routes_match_real_lists_and_transitions(self):
        boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        ids = {}
        for route in ('STAFF', 'PUBLIC'):
            response = self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'dispatch_route': route, 'client_request_id': str(uuid4())}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            ids[route] = response.data['data']['id']
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        stats = self.client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual((stats['pending_paid_total'], stats['pending_paid_routes']), (2, {'staff': 1, 'public': 1}))
        for route in ('STAFF', 'PUBLIC'):
            rows = self.client.get('/api/admin/orders/', {'status': 'PENDING', 'payment_status': 'PAID', 'dispatch_route': route}).data['data']
            self.assertEqual([row['id'] for row in rows['list']], [ids[route]])
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        assigned = self.client.post(f'/api/admin/orders/{ids["STAFF"]}/assign/', {'provider_id': account.pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        stats = self.client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual((stats['pending_paid_total'], stats['pending_paid_routes']), (1, {'staff': 0, 'public': 1}))
        cancelled = self.client.post(f'/api/admin/orders/{ids["PUBLIC"]}/cancel/', {'reason': '隔离验收'}, format='json')
        self.assertEqual(cancelled.data['code'], 0, cancelled.data)
        stats = self.client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual((stats['pending_paid_total'], stats['pending_paid_routes']), (0, {'staff': 0, 'public': 0}))

    def test_staff_queue_orders_real_orders_by_deadline_across_pages(self):
        boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        ids = []
        for _ in range(4):
            result = self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': str(uuid4())}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            ids.append(result.data['data']['id'])
        now = timezone.now()
        deadlines = [now + timedelta(minutes=20), now + timedelta(minutes=5), None, now + timedelta(minutes=5)]
        created = [now - timedelta(seconds=4), now - timedelta(seconds=3), now - timedelta(seconds=1), now - timedelta(seconds=3)]
        for order_id, deadline, created_at in zip(ids, deadlines, created):
            Order.objects.filter(pk=order_id).update(auto_cancel_at=deadline, created_at=created_at)
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        pages = [self.client.get('/api/admin/orders/', {'actionable_staff': '1', 'page_size': 1, 'page': page}).data['data'] for page in (1, 2, 3, 4)]
        self.assertTrue(all(page['staff_queue_order'] == 'deadline_asc' and page['total'] == 4 for page in pages))
        self.assertEqual([page['list'][0]['id'] for page in pages], [ids[1], ids[3], ids[0], ids[2]])
        regular = self.client.get('/api/admin/orders/', {'page_size': 10}).data['data']
        self.assertEqual([item['id'] for item in regular['list']], [ids[2], ids[3], ids[1], ids[0]])
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        assigned = self.client.post(f'/api/admin/orders/{ids[1]}/assign/', {'provider_id': account.pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        remaining = self.client.get('/api/admin/orders/', {'actionable_staff': '1'}).data['data']
        self.assertEqual([item['id'] for item in remaining['list']], [ids[3], ids[0], ids[2]])

    def test_order_team_identity_survives_real_transfer_and_legacy_fallback(self):
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        providers = [make_provider(display_name='同名陪玩', escort_no=f'QA-TEAM-{index}') for index in range(3)]
        accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
        for user, account in zip(providers, accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        created = self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': str(uuid4())}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        assigned = self.client.post(f'/api/admin/orders/{order_id}/assign/', {'provider_ids': [a.pk for a in accounts[:2]]}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        self.assertEqual(self.client.post(f'/api/admin/orders/{order_id}/start/', {}, format='json').data['code'], 0)
        listing = self.client.get('/api/admin/orders/', {'order_no_exact': created.data['data']['order_no']}).data['data']['list'][0]
        self.assertEqual([(p['provider_no'], p['provider_username'], p['provider_identity_source']) for p in listing['providers']],
                         [(f'QA-TEAM-{i}', accounts[i].username, 'ACCOUNT') for i in range(2)])
        moved = self.client.post(f'/api/admin/orders/{order_id}/transfer/', {'transfers': [{
            'old_provider_id': accounts[0].pk, 'new_provider_id': accounts[2].pk,
            'split_type': 'PERCENT', 'split_value': 50, 'penalty_amount': 0,
        }], 'reason': '隔离同名陪玩转单'}, format='json')
        self.assertEqual(moved.data['code'], 0, moved.data)
        detail = self.client.get(f'/api/admin/orders/{order_id}/').data['data']
        by_number = {p['provider_no']: p for p in detail['providers']}
        self.assertTrue(by_number['QA-TEAM-0']['released_at'])
        self.assertFalse(by_number['QA-TEAM-1']['released_at'])
        self.assertFalse(by_number['QA-TEAM-2']['released_at'])
        self.assertEqual(by_number['QA-TEAM-2']['provider_username'], accounts[2].username)
        dashboard = next(row for row in self.client.get('/api/admin/dashboard/stats').data['data']['recent_orders'] if row['id'] == order_id)
        active = [p for p in dashboard['providers'] if not p['released_at']]
        self.assertEqual({(p['provider_no'], p['provider_username']) for p in active},
                         {(f'QA-TEAM-{i}', accounts[i].username) for i in (1, 2)})
        # Historical rows may have a legacy user but no business-account link.
        order = Order.objects.get(pk=order_id)
        old_row = order.providers.get(provider_account=accounts[0])
        old_row.provider_account = None
        old_row.save(update_fields=['provider_account'])
        historical = self.client.get(f'/api/admin/orders/{order_id}/').data['data']
        old = next(p for p in historical['providers'] if p['id'] == old_row.pk)
        self.assertEqual((old['provider_no'], old['provider_username'], old['provider_identity_source']),
                         ('QA-TEAM-0', providers[0].username, 'LEGACY'))
        order.provider_account = None
        order.save(update_fields=['provider_account'])
        historical = self.client.get(f'/api/admin/orders/{order_id}/').data['data']
        self.assertEqual((historical['provider_no'], historical['provider_username'], historical['provider_identity_source']),
                         ('QA-TEAM-2', providers[2].username, 'LEGACY'))
        role = self.staff.admin_membership.roles.get()
        role.permissions = [perm for perm in role.permissions if perm != 'order:view']
        role.save(update_fields=['permissions'])
        self.assertEqual(self.client.get(f'/api/admin/orders/{order_id}/').status_code, 403)
        dashboard = next(row for row in self.client.get('/api/admin/dashboard/stats').data['data']['recent_orders'] if row['id'] == order_id)
        self.assertNotIn('provider_username', dashboard)
        self.assertNotIn('provider_username', dashboard['providers'][0])

    def test_exact_order_number_does_not_match_longer_historical_number(self):
        ids = []
        for _ in range(2):
            created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            ids.append(created.data['data']['id'])
        # Model possible historic identifiers with a prefix relationship.
        Order.objects.filter(pk=ids[0]).update(order_no='QA123')
        Order.objects.filter(pk=ids[1]).update(order_no='QA1234')
        normal = self.client.get('/api/admin/orders/', {'order_no': 'QA123'})
        self.assertEqual({row['id'] for row in normal.data['data']['list']}, set(ids))
        exact = self.client.get('/api/admin/orders/', {'order_no': 'QA123', 'order_no_exact': 'QA123'})
        self.assertIs(exact.data['data']['order_no_exact_applied'], True)
        self.assertEqual([row['id'] for row in exact.data['data']['list']], [ids[0]])
        absent = self.client.get('/api/admin/orders/', {'order_no_exact': 'QA12'})
        self.assertEqual(absent.data['data']['total'], 0)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 80000)

    def test_order_customer_filters_follow_displayed_account_with_legacy_fallback(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'user:view', 'user:edit']
        role.save(update_fields=['permissions'])
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        update = self.client.patch(f'/api/admin/users/{self.boss_account.pk}/', {'nickname': '当前老板资料', 'phone': '18800001111'}, format='json')
        self.assertEqual(update.data['code'], 0, update.data)
        # Historical records may retain values different from the current business account.
        self.boss.username = 'legacy_owner_filter'
        self.boss.nickname = '历史老板资料'
        self.boss.phone = '18800002222'
        self.boss.save(update_fields=['username', 'nickname', 'phone'])
        for field, value in [('customer_username', self.boss_account.username), ('customer_nickname', '当前老板资料'), ('customer_phone', '18800001111'), ('keyword', '18800001111')]:
            response = self.client.get('/api/admin/orders/', {field: value})
            self.assertEqual([item['id'] for item in response.data['data']['list']], [order.pk], (field, response.data))
        for field, value in [('customer_username', 'legacy_owner_filter'), ('customer_nickname', '历史老板资料'), ('customer_phone', '18800002222')]:
            self.assertEqual(self.client.get('/api/admin/orders/', {field: value}).data['data']['total'], 0)
        combined = self.client.get('/api/admin/orders/', {'customer_nickname': '当前老板资料', 'customer_phone': '18800001111', 'status': 'PENDING'})
        self.assertEqual([item['id'] for item in combined.data['data']['list']], [order.pk])
        Order.objects.filter(pk=order.pk).update(customer_account=None)
        historical = self.client.get('/api/admin/orders/', {'customer_username': 'legacy_owner_filter'})
        self.assertEqual([item['id'] for item in historical.data['data']['list']], [order.pk])
        self.assertEqual(historical.data['data']['list'][0]['customer_nickname'], '历史老板资料')
        self.assertEqual(self.client.get('/api/admin/orders/', {'customer_nickname': '当前老板资料'}).data['data']['total'], 0)

    def test_support_contacts_follow_real_order_and_fallback(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'support:view', 'support:edit', 'support:delete']
        role.save(update_fields=['permissions'])
        first = self.client.post('/api/admin/support-cards/', {'name': '隔离客服甲', 'sort_order': 0, 'is_active': True}, format='json')
        second = self.client.post('/api/admin/support-cards/', {'name': '隔离客服乙', 'sort_order': 1, 'is_active': True}, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        self.assertEqual(second.data['code'], 0, second.data)
        first_id, second_id = first.data['data']['id'], second.data['data']['id']
        invalid = self.client.patch(f'/api/admin/support-cards/{first_id}/', {'wecom_corp_id': 'wwtest'}, format='json')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(SupportContactCard.objects.get(pk=first_id).wecom_corp_id, '')
        quote = self.quote()
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(quote['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        self.assertEqual(order.support_contact_id, first_id)
        staff_token, _ = issue_account_tokens(self.staff_account)
        boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        contact_url = '/api/support/contact-card/'
        self.assertEqual(self.client.get(contact_url, {'order_id': order.pk}).data['data']['id'], first_id)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        disabled = self.client.patch(f'/api/admin/support-cards/{first_id}/', {'is_active': False}, format='json')
        self.assertEqual(disabled.data['code'], 0, disabled.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        self.assertEqual(self.client.get(contact_url, {'order_id': order.pk}).data['data']['id'], second_id)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        self.assertEqual(self.client.patch(f'/api/admin/support-cards/{second_id}/', {'is_active': False}, format='json').data['code'], 0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        self.assertIsNone(self.client.get(contact_url, {'order_id': order.pk}).data['data'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        self.assertEqual(self.client.patch(f'/api/admin/support-cards/{second_id}/', {'is_active': True}, format='json').data['code'], 0)
        self.assertEqual(self.client.delete(f'/api/admin/support-cards/{first_id}/').status_code, 200)
        order.refresh_from_db()
        self.assertIsNone(order.support_contact_id)
        self.assertEqual(order.support_contact_name_snapshot, '隔离客服甲')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        self.assertEqual(self.client.get(contact_url, {'order_id': order.pk}).data['data']['id'], second_id)

    def test_support_search_and_readonly_detail_use_real_accounts(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'support:view', 'support:edit']
        role.save(update_fields=['permissions'])
        for name, company, wechat in [('同名隔离客服', '隔离甲组', 'qa_first'), ('同名隔离客服', '隔离乙组', 'qa_second'), ('其他客服', '测试组', 'qa_other')]:
            response = self.client.post('/api/admin/support-cards/', {
                'name': name, 'company': company, 'wechat_id': wechat,
                'wecom_corp_id': 'wwtest', 'wecom_service_url': 'https://work.weixin.qq.com/kfid/qa_test',
            }, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        search = self.client.get('/api/admin/support-cards/', {'keyword': '同名隔离客服', 'page_size': 1})
        self.assertEqual(search.data['data']['total'], 2)
        self.assertEqual(len(search.data['data']['list']), 1)
        row_id = search.data['data']['list'][0]['id']
        self.assertEqual(self.client.get('/api/admin/support-cards/', {'keyword': 'qa_second'}).data['data']['total'], 1)
        role.permissions = [p for p in role.permissions if p != 'support:edit']
        role.save(update_fields=['permissions'])
        detail = self.client.get(f'/api/admin/support-cards/{row_id}/')
        self.assertEqual(detail.data['data']['wecom_corp_id'], 'wwtest')
        self.assertEqual(detail.data['data']['wecom_service_url'], 'https://work.weixin.qq.com/kfid/qa_test')
        self.assertTrue(detail.data['data']['updated_at'])
        self.assertEqual(self.client.patch(f'/api/admin/support-cards/{row_id}/', {'name': '无权修改'}, format='json').status_code, 403)
        self.assertEqual(self.client.get('/api/admin/support-cards/999999/').status_code, 404)

    def test_service_creation_and_filtered_lookup_use_real_records(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'service:view', 'service:edit']
        role.save(update_fields=['permissions'])
        zero = self.client.post('/api/admin/service-items/', {'name': '无效零价', 'price': 0}, format='json')
        self.assertEqual(zero.status_code, 400, zero.data)
        self.assertIn('商品价格必须大于 0', str(zero.data))
        created = self.client.post('/api/admin/service-items/', {
            'name': '隔离商品筛选测试', 'price': 1, 'billing_provider_count': 2,
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        item_id = created.data['data']['id']
        by_name = self.client.get('/api/admin/service-items/', {'keyword': '隔离商品筛选'})
        self.assertEqual([r['id'] for r in by_name.data['data']['list']], [item_id])
        by_id = self.client.get('/api/admin/service-items/', {'item_id': item_id})
        self.assertEqual(by_id.data['data']['list'][0]['price'], 1)
        self.assertEqual(by_id.data['data']['list'][0]['billing_provider_count'], 2)
        rejected = self.client.get('/api/admin/service-items/', {'item_id': '1x'})
        self.assertEqual(rejected.status_code, 400)

    def test_provider_verification_changes_real_acceptance(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'escort:view', 'escort:verify']
        role.save(update_fields=['permissions'])
        provider = make_provider(is_verified=False)
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        profile = EscortProfile.objects.get(user=provider)
        self.assertEqual(profile.order_acceptance_error(), '陪玩尚未通过认证')

        verified = self.client.post(f'/api/admin/escorts/{profile.pk}/verify/', {'is_verified': True}, format='json')
        self.assertEqual(verified.data['code'], 0, verified.data)
        profile.refresh_from_db()
        self.assertTrue(profile.is_verified)
        self.assertNotEqual(profile.order_acceptance_error(), '陪玩尚未通过认证')

        revoked = self.client.post(f'/api/admin/escorts/{profile.pk}/verify/', {'is_verified': False}, format='json')
        self.assertEqual(revoked.data['code'], 0, revoked.data)
        profile.refresh_from_db()
        self.assertFalse(profile.is_verified)
        self.assertEqual(profile.order_acceptance_error(), '陪玩尚未通过认证')

    def test_provider_deposit_mode_uses_current_rules_and_rejects_paid_change(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'escort:view', 'escort:edit']
        role.save(update_fields=['permissions'])
        provider = make_provider(deposit_mode='CASH', deposit_required=7300, deposit_paid=0)
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        profile = EscortProfile.objects.get(user=provider)
        config, _ = SystemConfig.objects.update_or_create(key='club_income_rules_enabled', defaults={'value': '0'})
        url = f'/api/admin/escorts/{profile.pk}/'

        changed = self.client.patch(url, {'deposit_mode': 'EARNINGS'}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_mode, profile.deposit_required, profile.effective_deposit_required), ('EARNINGS', 7300, 7300))

        config.value = '1'
        config.save(update_fields=['value'])
        changed = self.client.patch(url, {'deposit_mode': 'CASH'}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_mode, profile.deposit_required, profile.effective_deposit_required), ('CASH', 20000, 20000))
        changed = self.client.patch(url, {'deposit_mode': 'EARNINGS'}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_mode, profile.deposit_required, profile.effective_deposit_required), ('EARNINGS', 40000, 40000))

        profile.deposit_paid = 1000
        profile.save(update_fields=['deposit_paid'])
        rejected = self.client.patch(url, {'deposit_mode': 'CASH'}, format='json')
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('已有押金', str(rejected.data))
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_mode, profile.deposit_paid), ('EARNINGS', 1000))

    def test_provider_creation_uses_real_account_and_rejects_duplicate_username(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'escort:view', 'escort:edit']
        role.save(update_fields=['permissions'])
        username = 'qa_create_' + uuid4().hex[:12]
        payload = {'username': username, 'password': 'isolated-test-only', 'display_name': '隔离陪玩草稿'}
        created = self.client.post('/api/admin/escorts/', payload, format='multipart')
        self.assertEqual(created.data['code'], 0, created.data)
        self.assertEqual(ClubAccount.objects.filter(username=username).count(), 1)
        self.assertEqual(EscortProfile.objects.filter(account__username=username).count(), 1)
        exact = self.client.get('/api/admin/escorts/', {'account_username': username})
        self.assertEqual([row['username'] for row in exact.data['data']['list']], [username])
        partial = self.client.get('/api/admin/escorts/', {'account_username': username[:-1]})
        self.assertEqual(partial.data['data']['list'], [])
        invalid = self.client.get('/api/admin/escorts/', {'account_username': ' '})
        self.assertEqual(invalid.status_code, 400)
        duplicate = self.client.post('/api/admin/escorts/', payload, format='multipart')
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('登录账号已存在', str(duplicate.data))
        self.assertEqual(EscortProfile.objects.filter(account__username=username).count(), 1)

    def test_real_escort_reward_penalty_replay_and_provider_visibility(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'escort:view', 'escort:dispose', 'dispose:view']
        role.save(update_fields=['permissions'])
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        profile = EscortProfile.objects.get(user=provider)
        make_wallet(provider, balance=10000)
        url = f'/api/admin/escorts/{profile.pk}/dispose/'
        key = str(uuid4())
        payload = {'dispose_type': 'REWARD', 'amount': 1234, 'reason': '隔离奖励', 'client_request_id': key}
        first = self.client.post(url, payload, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        record = DisposeRecord.objects.get(pk=first.data['data']['record']['id'])
        self.assertEqual((record.account_id, record.operator_account_id), (account.pk, self.staff_account.pk))
        self.assertEqual((record.transaction.amount, record.transaction.operator_account_id), (1234, self.staff_account.pk))
        repeated = self.client.post(url, payload, format='json')
        self.assertEqual(repeated.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(repeated.data['fundResult']['data']['record']['id'], record.pk)
        altered = self.client.post(url, {**payload, 'amount': 1235}, format='json')
        self.assertEqual(altered.data['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertEqual(Wallet.objects.get(user=provider).balance, 11234)
        self.assertEqual(DisposeRecord.objects.filter(user=provider).count(), 1)
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        EscortProfile.objects.filter(user=other).update(account=other_account)
        self.assertEqual(self.client.get('/api/admin/dispose-records/', {'account_id': other_account.pk}).data['data']['total'], 0)
        self.assertEqual(self.client.get('/api/admin/dispose-records/', {'account_id': account.pk}).data['data']['list'][0]['id'], record.pk)
        penalty_key = str(uuid4())
        penalty = {'dispose_type': 'PENALTY', 'amount': 20000, 'reason': '隔离罚款', 'client_request_id': penalty_key}
        self.assertEqual(self.client.post(url, penalty, format='json').data['code'], 400)
        penalty['amount'] = 234
        saved = self.client.post(url, penalty, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        self.assertEqual(Wallet.objects.get(user=provider).balance, 11000)
        profile.refresh_from_db()
        self.assertEqual((profile.total_reward, profile.total_penalty), (1234, 234))
        provider_token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        benefits = self.client.get('/api/wallet/provider-benefits/').data['data']
        self.assertEqual({r['id'] for r in benefits['dispose_records']}, set(DisposeRecord.objects.filter(user=provider).values_list('id', flat=True)))
        self.assertEqual((benefits['summary']['total_reward'], benefits['summary']['total_penalty']), (1234, 234))

    def test_provider_qualification_scope_is_validated_by_real_api(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'escort:view', 'escort:edit']
        role.save(update_fields=['permissions'])
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        profile = EscortProfile.objects.get(user=provider)
        game = GameCategory.objects.create(name='隔离资格游戏')
        row = {'game_category': game.pk, 'provider_kind': 'GUARD', 'platform': 'MOBILE',
               'security_level': 'TOP_SECRET', 'grade': 'LEGEND', 'is_active': True}
        url = f'/api/admin/escorts/{profile.pk}/'
        saved = self.client.patch(url, {'qualifications': [row]}, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        self.assertEqual(profile.qualifications.count(), 1)
        self.assertTrue(profile.game_categories.filter(pk=game.pk).exists())
        duplicate = self.client.patch(url, {'qualifications': [row, row]}, format='json')
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(profile.qualifications.count(), 1)
        unsupported = self.client.patch(url, {'qualifications': [{**row, 'grade': 'STAR'}]}, format='json')
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(profile.qualifications.get().grade, 'LEGEND')
        game.is_active = False
        game.save(update_fields=['is_active'])
        rejected = self.client.patch(url, {'qualifications': [row]}, format='json')
        self.assertEqual(rejected.status_code, 400)
        disabled = self.client.patch(url, {'qualifications': [{**row, 'is_active': False}]}, format='json')
        self.assertEqual(disabled.data['code'], 0, disabled.data)
        self.assertFalse(profile.qualifications.get().is_active)
    def test_changed_price_requires_new_quote_before_debit(self):
        original = self.quote()
        payload = self.payload(original['amount'])
        self.service.price = 15000
        self.service.save(update_fields=['price'])
        rejected = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(rejected.data.get('errorCode'), 'PRICE_CHANGED', rejected.data)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        self.assertFalse(Order.objects.exists())
        payload['expected_amount'] = self.quote()['amount']
        accepted = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 85000)

    def test_insufficient_balance_rejects_without_order_or_debit(self):
        make_wallet(self.boss, balance=5000)
        response = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertNotEqual(response.data['code'], 0, response.data)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 5000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_double_package_assigns_two_distinct_providers(self):
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        providers = [make_provider(), make_provider()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
        for user, account in zip(providers, accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        quote = self.quote()
        payload = self.payload(quote['amount'])
        payload.update(escort_mode='DOUBLE', providers=[{'provider_id': account.pk} for account in accounts])
        response = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.escort_mode, order.amount), ('DOUBLE', quote['amount']))
        self.assertEqual(set(order.providers.values_list('provider_id', flat=True)), {user.pk for user in providers})
        recent = self.client.get('/api/admin/dashboard/stats').data['data']['recent_orders'][0]
        self.assertEqual(recent['id'], order.pk)
        self.assertEqual({provider['provider'] for provider in recent['providers']}, {account.pk for account in accounts})
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000 - quote['amount'])
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def test_invalid_team_selection_does_not_debit_before_real_double_dispatch(self):
        providers = [make_provider(), make_provider()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
        for user, account in zip(providers, accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        single_quote = self.quote()
        single_as_double = self.payload(single_quote['amount'])
        single_as_double.update(escort_mode='DOUBLE', providers=[{'provider_id': account.pk} for account in accounts])
        rejected = self.client.post('/api/admin/orders/dispatch/', single_as_double, format='json')
        self.assertNotEqual(rejected.data['code'], 0, rejected.data)
        self.assertIn('单陪套餐', rejected.data['msg'])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        double_quote = self.quote()
        repeated = self.payload(double_quote['amount'])
        repeated.update(escort_mode='DOUBLE', providers=[{'provider_id': accounts[0].pk}, {'provider_id': accounts[0].pk}])
        rejected = self.client.post('/api/admin/orders/dispatch/', repeated, format='json')
        self.assertNotEqual(rejected.data['code'], 0, rejected.data)
        self.assertIn('不可重复指派', rejected.data['msg'])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

        valid = self.payload(double_quote['amount'])
        valid.update(escort_mode='DOUBLE', providers=[{'provider_id': account.pk} for account in accounts])
        response = self.client.post('/api/admin/orders/dispatch/', valid, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(order.amount, double_quote['amount'])
        self.assertEqual(set(order.providers.values_list('provider_account_id', flat=True)), {account.pk for account in accounts})
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000 - double_quote['amount'])
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def test_double_manual_percentage_uses_integer_cut_for_each_provider(self):
        self.service.billing_provider_count = 2
        self.service.price = 10003
        self.service.save(update_fields=['billing_provider_count', 'price'])
        providers = [make_provider(), make_provider()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
        for user, account in zip(providers, accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        quote = self.quote()
        payload = self.payload(quote['amount'])
        payload.update(escort_mode='DOUBLE', commission_type='PERCENT', providers=[
            {'provider_id': accounts[0].pk, 'commission_type': 'PERCENT', 'commission_rate': 33},
            {'provider_id': accounts[1].pk, 'commission_type': 'PERCENT', 'commission_rate': 47},
        ])
        response = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(order.amount, 20006)
        self.assertEqual(list(order.providers.order_by('id').values_list('provider_account_id', 'settlement_base', 'commission_rate', 'provider_income')),
                         [(accounts[0].pk, 10003, 33, 6703), (accounts[1].pk, 10003, 47, 5302)])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000 - order.amount)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def test_dashboard_counts_real_dispatch_and_creation_dates(self):
        payload = self.payload(self.quote()['amount'])
        older = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(older.data['code'], 0, older.data)
        old_time = timezone.now() - timedelta(days=2)
        Order.objects.filter(pk=older.data['data']['id']).update(created_at=old_time)
        recent = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(recent.data['code'], 0, recent.data)
        response = self.client.get('/api/admin/dashboard/stats')
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']
        self.assertEqual((data['order_total'], data['pending_paid_total'], data['pending_unpaid_total']), (2, 2, 0))
        waiting = self.client.get('/api/admin/orders/', {'status': 'PENDING', 'payment_status': 'PAID'}).data['data']
        self.assertEqual(waiting['total'], data['pending_paid_total'])
        self.assertEqual((data['order_amount_total'], data['paid_order_total']), (20000, 2))
        self.assertEqual(data['today_order_total'], 1)
        trend = {row['date']: row['count'] for row in data['daily_new_orders']}
        self.assertEqual(len(trend), 7)
        self.assertEqual(trend[timezone.localdate(old_time).isoformat()], 1)
        self.assertEqual(trend[timezone.localdate().isoformat()], 1)
        # The dashboard's today KPI drills into the final server-timezone day.
        today_row = data['daily_new_orders'][-1]
        listed = self.client.get('/api/admin/orders/', {'created_day': today_row['date']}).data['data']
        self.assertEqual(today_row['count'], data['today_order_total'])
        self.assertEqual(listed['total'], data['today_order_total'])
        self.assertEqual([row['id'] for row in listed['list']], [recent.data['data']['id']])
        self.assertTrue(data['snapshot_at'])

    def test_daily_order_drilldown_matches_local_date_boundaries(self):
        with timezone.override('Asia/Shanghai'):
            day = timezone.localdate() - timedelta(days=1)
            start = timezone.make_aware(datetime.combine(day, time.min))
            times = [start - timedelta(microseconds=1), start,
                     start + timedelta(days=1) - timedelta(microseconds=1), start + timedelta(days=1)]
            ids = []
            for created_at in times:
                created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order_id = created.data['data']['id']
                Order.objects.filter(pk=order_id).update(created_at=created_at)
                ids.append(order_id)
            data = self.client.get('/api/admin/dashboard/stats').data['data']
            trend = {row['date']: row['count'] for row in data['daily_new_orders']}
            result = self.client.get('/api/admin/orders/', {'created_day': day.isoformat()}).data['data']
            self.assertEqual(result['created_day_applied'], {'date': day.isoformat(), 'timezone': 'Asia/Shanghai'})
            self.assertEqual(result['total'], trend[day.isoformat()])
            self.assertEqual({row['id'] for row in result['list']}, set(ids[1:3]))
            empty = self.client.get('/api/admin/orders/', {'created_day': (day - timedelta(days=10)).isoformat()}).data['data']
            self.assertEqual(empty['total'], 0)
            self.assertEqual(empty['created_day_applied']['timezone'], 'Asia/Shanghai')
            self.assertEqual(self.client.get('/api/admin/orders/', {'created_day': '2026-02-30'}).status_code, 400)
            self.assertNotIn('created_day_applied', self.client.get('/api/admin/orders/').data['data'])

    def test_dashboard_status_counts_match_real_order_filters(self):
        ids = {}
        for target in ('PENDING', 'CANCELLED', 'GRABBED', 'IN_SERVICE', 'COMPLETED'):
            payload = self.payload(self.quote()['amount'])
            if target in ('GRABBED', 'IN_SERVICE', 'COMPLETED'):
                provider = make_provider()
                account = get_or_create_account_for_legacy_user(provider)
                EscortProfile.objects.filter(user=provider).update(account=account)
                payload['providers'] = [{'provider_id': account.pk}]
            created = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order_id = created.data['data']['id']
            ids[target] = order_id
            if target == 'CANCELLED':
                result = self.client.post(f'/api/admin/orders/{order_id}/cancel/', {'reason': '隔离状态验收'}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
            if target in ('IN_SERVICE', 'COMPLETED'):
                result = self.client.post(f'/api/admin/orders/{order_id}/start/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
            if target == 'COMPLETED':
                result = self.client.post(f'/api/admin/orders/{order_id}/complete/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)

        stats = self.client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual(stats['order_total'], len(ids))
        for status, order_id in ids.items():
            with self.subTest(status=status):
                self.assertEqual(stats['order_status'].get(status), 1)
                result = self.client.get('/api/admin/orders/', {'status': status}).data['data']
                self.assertEqual(result['total'], 1)
                self.assertEqual([row['id'] for row in result['list']], [order_id])

    def test_order_filter_uses_exact_business_account_and_legacy_mapping(self):
        amount = self.quote()['amount']
        first = self.client.post('/api/admin/orders/dispatch/', self.payload(amount), format='json')
        legacy = self.client.post('/api/admin/orders/dispatch/', self.payload(amount), format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        self.assertEqual(legacy.data['code'], 0, legacy.data)
        Order.objects.filter(pk=legacy.data['data']['id']).update(customer_account=None)
        other = make_user(nickname=self.boss.nickname)
        other_account = get_or_create_account_for_legacy_user(other)
        make_wallet(other, balance=100000)
        other_payload = self.payload(amount)
        other_payload['customer_id'] = other_account.pk
        other_order = self.client.post('/api/admin/orders/dispatch/', other_payload, format='json')
        self.assertEqual(other_order.data['code'], 0, other_order.data)
        results = self.client.get('/api/admin/orders/', {'customer_account_id': self.boss_account.pk})
        self.assertEqual(results.data['code'], 0, results.data)
        self.assertEqual(results.data['data']['customer_account_id_applied'], str(self.boss_account.pk))
        self.assertEqual({row['id'] for row in results.data['data']['list']}, {first.data['data']['id'], legacy.data['data']['id']})
        self.assertNotIn(other_order.data['data']['id'], {row['id'] for row in results.data['data']['list']})
        other_results = self.client.get('/api/admin/orders/', {'customer_account_id': other_account.pk}).data['data']
        self.assertEqual(other_results['customer_account_id_applied'], str(other_account.pk))
        self.assertEqual([row['id'] for row in other_results['list']], [other_order.data['data']['id']])
        empty_account = get_or_create_account_for_legacy_user(make_user())
        empty = self.client.get('/api/admin/orders/', {'customer_account_id': empty_account.pk}).data['data']
        self.assertEqual(empty['customer_account_id_applied'], str(empty_account.pk))
        self.assertEqual(empty['total'], 0)
        self.assertNotIn('customer_account_id_applied', self.client.get('/api/admin/orders/').data['data'])
        self.assertEqual(self.client.get('/api/admin/orders/', {'customer_account_id': '1x'}).status_code, 400)

    def test_boss_list_exposes_login_permission_separately_from_active_filter(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'user:view']
        role.save(update_fields=['permissions'])
        self.boss_account.can_login = False
        self.boss_account.save(update_fields=['can_login'])
        unlinked = self.client.get('/api/admin/users/', {'is_active': 'true', 'nickname': self.boss_account.nickname})
        unlinked_record = next(row for row in unlinked.data['data']['list'] if row['id'] == self.boss_account.pk)
        self.assertIsNone(unlinked_record['wallet_balance'])
        Wallet.objects.filter(user=self.boss).update(account=self.boss_account)
        response = self.client.get('/api/admin/users/', {'is_active': 'true', 'nickname': self.boss_account.nickname})
        self.assertEqual(response.data['code'], 0, response.data)
        record = next(row for row in response.data['data']['list'] if row['id'] == self.boss_account.pk)
        self.assertTrue(record['is_active'])
        self.assertFalse(record['can_login'])
        self.assertEqual(record['wallet_balance'], 100000)

    def test_boss_staff_route_then_assign_two_providers(self):
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        providers = [make_provider(), make_provider()]
        accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
        for user, account in zip(providers, accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        created = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        self.assertEqual((order.status, order.dispatch_route, order.amount), ('PENDING', 'STAFF', 20000))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 80000)
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        queue = self.client.get('/api/admin/orders/', {'actionable_staff': '1'})
        self.assertEqual(queue.data['code'], 0, queue.data)
        self.assertIn(order.pk, [row['id'] for row in queue.data['data']['list']])
        assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {
            'provider_ids': [account.pk for account in accounts],
        }, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.escort_mode), ('GRABBED', 'DOUBLE'))
        self.assertEqual(set(order.providers.values_list('provider_id', flat=True)), {user.pk for user in providers})
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def test_single_lobby_assignment_changes_order_without_second_debit(self):
        provider = make_provider()
        provider_account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': provider_account.pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_id), ('GRABBED', provider.pk))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        repeated = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': provider_account.pk}, format='json')
        self.assertNotEqual(repeated.data['code'], 0, repeated.data)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)

    def test_real_order_message_preview_and_unconfigured_channel(self):
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        url = f'/api/admin/orders/{order.pk}/dispatch-message/'
        preview = self.client.get(url)
        self.assertEqual(preview.data['code'], 0, preview.data)
        self.assertTrue(preview.data['data']['can_send'])
        self.assertIn(order.order_no, preview.data['data']['content'])
        self.assertTrue(all(not channel['configured'] for channel in preview.data['data']['channels']))
        key = next(item['confirmation_key'] for item in preview.data['data']['channels'] if item['channel'] == 'WECOM')
        rejected = self.client.post(url, {'channels': ['WECOM'], 'confirmation_keys': {'WECOM': key}}, format='json')
        self.assertNotEqual(rejected.data['code'], 0, rejected.data)
        self.assertFalse(KookDispatchRecord.objects.filter(order=order).exists())
        self.assertFalse(WecomDispatchRecord.objects.filter(order=order).exists())

    def test_transfer_checks_penalty_and_splits_real_order_income(self):
        old, new = make_provider(), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (old, new)]
        for user, account in zip((old, new), accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': accounts[0].pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        started = self.client.post(f'/api/admin/orders/{order.pk}/start/', {}, format='json')
        self.assertEqual(started.data['code'], 0, started.data)
        url = f'/api/admin/orders/{order.pk}/transfer/'
        command = {'old_provider_id': accounts[0].pk, 'new_provider_id': accounts[1].pk,
                   'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 100000}
        rejected = self.client.post(url, {'transfers': [command]}, format='json')
        self.assertNotEqual(rejected.data['code'], 0, rejected.data)
        order.refresh_from_db()
        self.assertEqual(order.provider_id, old.pk)
        self.assertFalse(order.providers.filter(provider=new).exists())
        command['penalty_amount'] = 0
        transferred = self.client.post(url, {'transfers': [command], 'reason': '隔离转单验收'}, format='json')
        self.assertEqual(transferred.data['code'], 0, transferred.data)
        order.refresh_from_db()
        self.assertEqual(order.provider_id, new.pk)
        self.assertEqual(order.providers.get(provider=old).provider_income, 3200)
        self.assertEqual(order.providers.get(provider=new).provider_income, 4800)
        recent = self.client.get('/api/admin/dashboard/stats').data['data']['recent_orders'][0]
        self.assertEqual(recent['id'], order.pk)
        rows = {item['provider']: item for item in recent['providers']}
        self.assertTrue(rows[accounts[0].pk]['released_at'])
        self.assertIsNone(rows[accounts[1].pk]['released_at'])
        detail = self.client.get(f'/api/admin/orders/{order.pk}/').data['data']
        self.assertEqual({item['provider'] for item in detail['providers']}, set(rows))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def test_fixed_transfer_uses_integer_units_and_caps_at_available_income(self):
        for requested, expected_old in ((1, 1), (99999, 8000)):
            with self.subTest(requested=requested):
                old, new = make_provider(), make_provider()
                accounts = [get_or_create_account_for_legacy_user(user) for user in (old, new)]
                for user, account in zip((old, new), accounts):
                    EscortProfile.objects.filter(user=user).update(account=account)
                created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order = Order.objects.get(pk=created.data['data']['id'])
                assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': accounts[0].pk}, format='json')
                self.assertEqual(assigned.data['code'], 0, assigned.data)
                started = self.client.post(f'/api/admin/orders/{order.pk}/start/', {}, format='json')
                self.assertEqual(started.data['code'], 0, started.data)
                transferred = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': [{
                    'old_provider_id': accounts[0].pk, 'new_provider_id': accounts[1].pk,
                    'split_type': 'FIXED', 'split_value': requested, 'penalty_amount': 0,
                }]}, format='json')
                self.assertEqual(transferred.data['code'], 0, transferred.data)
                order.refresh_from_db()
                self.assertEqual(order.provider_id, new.pk)
                self.assertEqual(order.providers.get(provider=old).provider_income, expected_old)
                self.assertIsNotNone(order.providers.get(provider=old).released_at)
                self.assertEqual(order.providers.get(provider=new).provider_income, 8000 - expected_old)
                self.assertIsNone(order.providers.get(provider=new).released_at)
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)

    def run_fulfillment_flow(self, settlement_mode):
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        self.assertEqual(order.settlement_mode, settlement_mode)
        assigned = self.client.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': account.pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        premature = self.client.post(f'/api/admin/orders/{order.pk}/complete/', {}, format='json')
        self.assertNotEqual(premature.data['code'], 0, premature.data)
        started = self.client.post(f'/api/admin/orders/{order.pk}/start/', {}, format='json')
        self.assertEqual(started.data['code'], 0, started.data)
        repeated_start = self.client.post(f'/api/admin/orders/{order.pk}/start/', {}, format='json')
        self.assertNotEqual(repeated_start.data['code'], 0, repeated_start.data)
        completed = self.client.post(f'/api/admin/orders/{order.pk}/complete/', {}, format='json')
        self.assertEqual(completed.data['code'], 0, completed.data)
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        self.assertTrue(ProviderReport.objects.filter(order=order, provider=provider).exists())
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        repeated_complete = self.client.post(f'/api/admin/orders/{order.pk}/complete/', {}, format='json')
        self.assertNotEqual(repeated_complete.data['code'], 0, repeated_complete.data)
        return order

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_real_order_deposit_follows_frozen_snapshot_and_current_ratio(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'deposit_rule:view', 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        flag, _ = SystemConfig.objects.update_or_create(key='club_income_rules_enabled', defaults={'value': '0'})
        orders = []
        providers = []
        for enabled in ('0', '1'):
            flag.value = enabled; flag.save(update_fields=['value'])
            provider = make_provider(deposit_mode='EARNINGS', deposit_required=40000, deposit_paid=0)
            account = get_or_create_account_for_legacy_user(provider)
            EscortProfile.objects.filter(user=provider).update(account=account)
            created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            self.assertEqual(order.income_hold_hours, 72 if enabled == '1' else 0)
            for action, data in [('assign', {'provider_id': account.pk}), ('start', {})]:
                result = self.client.post(f'/api/admin/orders/{order.pk}/{action}/', data, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
            orders.append(order); providers.append(provider)
        configured = self.client.put('/api/admin/config/deposit-rules', {'deposit_percent': 25, 'expected_deposit_percent': 60}, format='json')
        self.assertEqual(configured.data['code'], 0, configured.data)
        flag.value = '0'; flag.save(update_fields=['value'])
        for order in orders:
            result = self.client.post(f'/api/admin/orders/{order.pk}/complete/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        first, second = [Wallet.objects.get(user=p) for p in providers]
        self.assertEqual((first.balance, first.frozen_amount), (8000, 0))
        self.assertEqual((second.balance, second.frozen_amount), (0, 6000))
        self.assertEqual(Transaction.objects.filter(order=orders[0], tx_type='DEPOSIT').count(), 0)
        self.assertEqual(list(Transaction.objects.filter(order=orders[1], tx_type='DEPOSIT').values_list('amount', flat=True)), [-2000])

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_complete_service_waits_for_report_approval(self):
        order = self.run_fulfillment_flow('REPORT_APPROVAL')
        self.assertIsNone(order.settled_at)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_dashboard_pending_report_count_tracks_real_submission_and_approval(self):
        order = self.run_fulfillment_flow('REPORT_APPROVAL')
        report = ProviderReport.objects.get(order=order)
        provider_token, _ = issue_account_tokens(report.provider_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        image = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
            uploaded = self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind': kind, 'image': SimpleUploadedFile(kind + '.gif', image, content_type='image/gif')}, format='multipart')
            self.assertEqual(uploaded.data['code'], 0, uploaded.data)
        submitted = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        self.assertEqual(submitted.data['code'], 0, submitted.data)
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        dashboard = self.client.get('/api/admin/dashboard/stats').data['data']
        listed = self.client.get('/api/admin/reports/', {'status': 'PENDING'}).data['data']
        self.assertEqual(dashboard['pending_report_total'], 1)
        self.assertEqual(listed['total'], 1)
        self.assertEqual(listed['list'][0]['id'], report.pk)
        approved = self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
        self.assertEqual(approved.data['code'], 0, approved.data)
        self.assertEqual(self.client.get('/api/admin/dashboard/stats').data['data']['pending_report_total'], 0)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [permission for permission in role.permissions if permission != 'report:view']
        role.save(update_fields=['permissions'])
        self.assertIsNone(self.client.get('/api/admin/dashboard/stats').data['data']['pending_report_total'])

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_report_identity_search_matches_current_account_and_profile(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'escort:view', 'escort:edit']
        role.save(update_fields=['permissions'])
        order = self.run_fulfillment_flow('REPORT_APPROVAL')
        report = ProviderReport.objects.get(order=order)
        profile = EscortProfile.objects.get(account=report.provider_account)
        edited = self.client.patch(f'/api/admin/escorts/{profile.pk}/', {'display_name': '隔离结单昵称'}, format='json')
        self.assertEqual(edited.data['code'], 0, edited.data)
        provider_token, _ = issue_account_tokens(report.provider_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        updated = self.client.patch('/api/users/me/', {'nickname': '隔离账号昵称'}, format='json')
        self.assertEqual(updated.data['code'], 0, updated.data)
        image = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
            uploaded = self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': kind, 'image': SimpleUploadedFile(kind + '.gif', image, content_type='image/gif')}, format='multipart')
            self.assertEqual(uploaded.data['code'], 0, uploaded.data)
        self.assertEqual(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
        # Simulate historical data where the legacy user's identity diverges.
        legacy = report.provider
        legacy.username = 'qa_legacy_report_user'
        legacy.nickname = '历史陪玩昵称'
        legacy.save(update_fields=['username', 'nickname'])
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        for field, value in [('provider_username', report.provider_account.username), ('provider_nickname', '隔离账号昵称'), ('provider_nickname', '隔离结单昵称'), ('provider_nickname', profile.escort_no), ('keyword', '隔离结单昵称')]:
            response = self.client.get('/api/admin/reports/', {field: value, 'status': 'PENDING'})
            self.assertEqual([item['id'] for item in response.data['data']['list']], [report.pk], response.data)
        for keyword in (report.provider_account.username, '隔离账号昵称', '隔离结单昵称', profile.escort_no, report.game_name):
            self.assertTrue(keyword)
            response = self.client.get('/api/admin/reports/', {'keyword': keyword, 'status': 'PENDING'})
            self.assertEqual([item['id'] for item in response.data['data']['list']], [report.pk], response.data)
        combined = {'keyword': profile.escort_no, 'game_name': report.game_name, 'provider_username': report.provider_account.username, 'status': 'PENDING'}
        self.assertEqual([item['id'] for item in self.client.get('/api/admin/reports/', combined).data['data']['list']], [report.pk])
        combined['status'] = 'APPROVED'
        self.assertEqual(self.client.get('/api/admin/reports/', combined).data['data']['total'], 0)
        data = self.client.get(f'/api/admin/reports/{report.pk}/').data['data']
        self.assertEqual((data['provider_name'], data['provider_username'], data['provider_no']), ('隔离结单昵称', report.provider_account.username, profile.escort_no))
        order_data = self.client.get(f'/api/admin/orders/{order.pk}/').data['data']
        self.assertEqual(order_data['provider_name'], '隔离结单昵称')
        self.assertEqual(order_data['providers'][0]['provider_name'], '隔离结单昵称')
        dashboard_row = next(row for row in self.client.get('/api/admin/dashboard/stats').data['data']['recent_orders'] if row['id'] == order.pk)
        self.assertEqual(dashboard_row['providers'][0]['provider_name'], '隔离结单昵称')
        self.assertEqual(self.client.get('/api/admin/reports/', {'provider_username': legacy.username}).data['data']['total'], 0)
        ProviderReport.objects.filter(pk=report.pk).update(provider_account=None)
        legacy_result = self.client.get('/api/admin/reports/', {'provider_username': legacy.username}).data['data']['list']
        self.assertEqual(legacy_result[0]['provider_name'], '隔离结单昵称')
        self.assertEqual(legacy_result[0]['provider_no'], profile.escort_no)

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_complete_service_uses_legacy_settlement_mode(self):
        order = self.run_fulfillment_flow('COMPLETION')
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_multiple_real_reviews_and_admin_deletion_keep_three_roles_in_sync(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:delete', 'escort:view']
        role.save(update_fields=['permissions'])
        first = self.run_fulfillment_flow('COMPLETION')
        provider_account = first.provider_account
        profile = first.provider.escort_profile
        staff_token, _ = issue_account_tokens(self.staff_account)
        boss_token, _ = issue_account_tokens(self.boss_account)
        provider_token, _ = issue_account_tokens(provider_account)
        reviews = []
        for index, score in enumerate((1, 1, 1, 1, 1, 2, 1)):
            if index == 0:
                order = first
            else:
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
                created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order = Order.objects.get(pk=created.data['data']['id'])
                for action, body in (('assign', {'provider_id': provider_account.pk}), ('start', {}), ('complete', {})):
                    result = self.client.post(f'/api/admin/orders/{order.pk}/{action}/', body, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
            result = self.client.post('/api/orders/evaluate/', {'order_id': order.pk, 'score': score}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            reviews.append(result.data['data']['id'])

        def check_roles(expected, count):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
            public = self.client.get(f'/api/users/escorts/{provider_account.pk}/').data['data']
            self.assertEqual((public['rating'], public['ratingCount']), (expected, count))
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
            own = self.client.get('/api/users/escorts/me/').data['data']
            self.assertEqual((own['rating_avg'], own['rating_count']), (expected, count))
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
            admin = self.client.get(f'/api/admin/escorts/{profile.pk}/').data['data']
            self.assertEqual((float(admin['rating_avg']), admin['rating_count']), (expected, count))

        check_roles(1.14, 7)
        removed = self.client.delete(f'/api/admin/evaluations/{reviews[5]}/', {'reply_version': 0}, format='json')
        self.assertEqual(removed.data['code'], 0, removed.data)
        check_roles(1.0, 6)

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_real_evaluation_reply_filter_and_rating_rollback(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply', 'evaluation:delete']
        role.save(update_fields=['permissions'])
        order = self.run_fulfillment_flow('COMPLETION')
        token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        created = self.client.post('/api/orders/evaluate/', {
            'order_id': order.pk, 'score': 4, 'skill_score': 5, 'attitude_score': 4,
            'communication_score': 3, 'content': '隔离评价内容',
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        evaluation = Evaluation.objects.get(order=order)
        self.assertEqual((evaluation.customer_account_id, evaluation.provider_account_id),
                         (order.customer_account_id, order.provider_account_id))
        profile = order.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.rating_count, 1)
        token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        path = f'/api/admin/evaluations/{evaluation.pk}/reply/'
        pending = self.client.get('/api/admin/evaluations/', {'only_unreplied': '1'}).data['data']
        self.assertEqual([row['id'] for row in pending['list']], [evaluation.pk])
        response = self.client.post(path, {'reply_content': '隔离回复', 'reply_version': 0}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(self.client.get('/api/admin/evaluations/', {'only_unreplied': '1'}).data['data']['total'], 0)
        response = self.client.post(path, {'reply_content': '隔离修改回复', 'reply_version': 1}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        detail = self.client.get(f'/api/admin/evaluations/{evaluation.pk}/').data['data']
        self.assertEqual((detail['reply_content'], detail['avg_score']), ('隔离修改回复', 4.0))
        deleted = self.client.delete(f'/api/admin/evaluations/{evaluation.pk}/', {'reply_version': detail['reply_version']}, format='json')
        self.assertEqual(deleted.data['code'], 0, deleted.data)
        profile.refresh_from_db()
        self.assertEqual(profile.rating_count, 0)
        self.assertFalse(Evaluation.objects.filter(pk=evaluation.pk).exists())

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_evaluation_follows_explicit_accounts_after_identity_migration(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply', 'evaluation:delete']
        role.save(update_fields=['permissions'])
        order = self.run_fulfillment_flow('COMPLETION')
        # Historical orders can carry explicit current business identities while
        # retaining legacy user references. These accounts have no legacy map.
        boss = ClubAccount.objects.create(username='qa-review-current-boss', nickname='老板当前身份', account_type='BOSS')
        provider = ClubAccount.objects.create(username='qa-review-current-provider', nickname='陪玩当前身份', account_type='PROVIDER')
        current_user = make_provider(display_name='陪玩当前身份')
        current_profile = current_user.escort_profile
        current_profile.account = provider
        current_profile.save(update_fields=['account'])
        historical_profile = order.provider.escort_profile
        Order.objects.filter(pk=order.pk).update(customer_account=boss, provider_account=provider)
        old_boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + old_boss_token)
        rejected = self.client.post('/api/orders/evaluate/', {'order_id': order.pk, 'score': 4}, format='json')
        self.assertEqual(rejected.data['code'], 400, rejected.data)
        boss_token, _ = issue_account_tokens(boss)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        created = self.client.post('/api/orders/evaluate/', {'order_id': order.pk, 'score': 4, 'content': '服务评价'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        evaluation = Evaluation.objects.get(order=order)
        self.assertEqual((evaluation.customer_account_id, evaluation.provider_account_id), (boss.pk, provider.pk))
        current_profile.refresh_from_db(); historical_profile.refresh_from_db()
        self.assertEqual((current_profile.rating_count, historical_profile.rating_count), (1, 0))
        provider_token, _ = issue_account_tokens(provider)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        received = self.client.get('/api/orders/evaluations/mine/').data['data']
        self.assertEqual([row['id'] for row in received['list']], [evaluation.pk])
        self.assertEqual((received['list'][0]['customer_name'], received['list'][0]['provider_name']), ('老板当前身份', '陪玩当前身份'))
        replied = self.client.post(f'/api/orders/evaluations/{evaluation.pk}/reply/', {'reply_content': '感谢老板', 'reply_version': 0}, format='json')
        self.assertEqual(replied.data['code'], 0, replied.data)
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        detail = self.client.get(f'/api/admin/evaluations/{evaluation.pk}/').data['data']
        self.assertEqual(detail['reply_content'], '感谢老板')
        self.assertEqual((detail['customer_username'], detail['customer_nickname'], detail['provider_username'], detail['provider_nickname']),
                         (boss.username, boss.nickname, provider.username, provider.nickname))
        for field, value in [('customer_username', boss.username), ('customer_nickname', boss.nickname),
                             ('provider_username', provider.username), ('provider_nickname', provider.nickname), ('keyword', provider.username)]:
            results = self.client.get('/api/admin/evaluations/', {field: value}).data['data']
            self.assertEqual([row['id'] for row in results['list']], [evaluation.pk])
        self.assertEqual(self.client.get('/api/admin/evaluations/', {'customer_username': self.boss.username}).data['data']['total'], 0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        detail = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
        self.assertEqual(detail['evaluation']['reply_content'], '感谢老板')
        service_reviews = self.client.get(f'/api/orders/services/{self.service.pk}/evaluations/').data['data']
        self.assertEqual(service_reviews['list'][0]['customer_name'], boss.nickname)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        deleted = self.client.delete(f'/api/admin/evaluations/{evaluation.pk}/', {'reply_version': 1}, format='json')
        self.assertEqual(deleted.data['code'], 0, deleted.data)
        current_profile.refresh_from_db(); historical_profile.refresh_from_db()
        self.assertEqual((current_profile.rating_count, historical_profile.rating_count), (0, 0))

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_evaluation_rating_uses_mapped_legacy_profile_if_unlinked(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:delete']
        role.save(update_fields=['permissions'])
        order = self.run_fulfillment_flow('COMPLETION')
        profile = order.provider.escort_profile
        profile.account = None
        profile.save(update_fields=['account'])
        boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        created = self.client.post('/api/orders/evaluate/', {'order_id': order.pk, 'score': 4}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        evaluation = Evaluation.objects.get(order=order)
        profile.refresh_from_db()
        self.assertEqual(profile.rating_count, 1)
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        deleted = self.client.delete(f'/api/admin/evaluations/{evaluation.pk}/', {'reply_version': 0}, format='json')
        self.assertEqual(deleted.data['code'], 0, deleted.data)
        profile.refresh_from_db()
        self.assertEqual(profile.rating_count, 0)

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_staff_and_provider_replies_reject_stale_versions(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply', 'evaluation:delete']
        role.save(update_fields=['permissions'])
        order = self.run_fulfillment_flow('COMPLETION')
        token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        result = self.client.post('/api/orders/evaluate/', {'order_id': order.pk, 'score': 4}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        evaluation = Evaluation.objects.get(order=order)
        staff_token, _ = issue_account_tokens(self.staff_account)
        provider_token, _ = issue_account_tokens(order.provider_account)
        admin_path = f'/api/admin/evaluations/{evaluation.pk}/reply/'
        provider_path = f'/api/orders/evaluations/{evaluation.pk}/reply/'
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        before = self.client.get(f'/api/admin/evaluations/{evaluation.pk}/').data['data']
        self.assertEqual(before['reply_version'], 0)
        missing = self.client.post(admin_path, {'reply_content': '旧客户端'}, format='json')
        self.assertEqual(missing.status_code, 400)
        invalid = self.client.post(admin_path, {'reply_content': '错误版本', 'reply_version': True}, format='json')
        self.assertEqual(invalid.status_code, 400)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        seen = self.client.get('/api/orders/evaluations/mine/').data['data']['list'][0]
        self.assertEqual(seen['reply_version'], 0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        first = self.client.post(admin_path, {'reply_content': '后台回复', 'reply_version': before['reply_version']}, format='json')
        self.assertEqual(first.data['data']['reply_version'], 1)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        stale = self.client.post(provider_path, {'reply_content': '陪玩旧草稿', 'reply_version': seen['reply_version']}, format='json')
        self.assertEqual((stale.status_code, stale.data['errorCode']), (409, 'EVALUATION_REPLY_CONFLICT'))
        latest = self.client.get('/api/orders/evaluations/mine/').data['data']['list'][0]
        self.assertEqual((latest['reply_content'], latest['reply_version']), ('后台回复', 1))
        saved = self.client.post(provider_path, {'reply_content': '陪玩新回复', 'reply_version': latest['reply_version']}, format='json')
        self.assertEqual(saved.data['data']['reply_version'], 2)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        stale = self.client.post(admin_path, {'reply_content': '后台旧草稿', 'reply_version': 1}, format='json')
        self.assertEqual((stale.status_code, stale.data['errorCode']), (409, 'EVALUATION_REPLY_CONFLICT'))
        current = self.client.get(f'/api/admin/evaluations/{evaluation.pk}/').data['data']
        self.assertEqual((current['reply_content'], current['reply_version']), ('陪玩新回复', 2))
        profile = order.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.rating_count, 1)
        delete_path = f'/api/admin/evaluations/{evaluation.pk}/'
        missing = self.client.delete(delete_path, {}, format='json')
        self.assertEqual(missing.status_code, 400)
        stale_delete = self.client.delete(delete_path, {'reply_version': 1}, format='json')
        self.assertEqual((stale_delete.status_code, stale_delete.data['errorCode']), (409, 'EVALUATION_REPLY_CONFLICT'))
        evaluation.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((evaluation.reply_content, profile.rating_count), ('陪玩新回复', 1))
        deleted = self.client.delete(delete_path, {'reply_version': current['reply_version']}, format='json')
        self.assertEqual(deleted.data['code'], 0, deleted.data)
        profile.refresh_from_db()
        self.assertEqual(profile.rating_count, 0)
        repeated = self.client.delete(delete_path, {'reply_version': current['reply_version']}, format='json')
        self.assertEqual(repeated.status_code, 404)

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_dashboard_distinguishes_discounted_order_amount_from_partial_refund_net(self):
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'order:refund']
        role.save(update_fields=['permissions'])
        make_promotion(discount_rate=80, scope='ITEMS', items=[self.service])
        order = self.run_fulfillment_flow('COMPLETION')
        self.assertEqual((order.original_amount, order.amount), (10000, 8000))
        # Refunds on a later day should revise the order's creation-day cohort.
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=1))
        order.refresh_from_db()
        created_day = timezone.localtime(order.created_at).date().isoformat()
        refund_day = timezone.localdate().isoformat()
        def daily(stats, day):
            return next(row for row in stats['daily_new_orders'] if row['date'] == day)
        before = self.client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual((daily(before, created_day)['count'], daily(before, created_day)['net_amount']), (1, 8000))
        self.assertEqual(daily(before, refund_day)['net_amount'], 0)
        preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500})
        self.assertEqual(preview.data['code'], 0, preview.data)
        refunded = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'reason': '隔离部分退款验收', 'refund_amount': 2500,
            'plan_token': preview.data['data']['plan_token'], 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        after = self.client.get('/api/admin/dashboard/stats').data['data']
        recent = next(row for row in after['recent_orders'] if row['id'] == order.pk)
        self.assertEqual((recent['original_amount'], recent['amount']), (10000, 8000))
        self.assertEqual((recent['status'], recent['payment_status']), ('COMPLETED', 'PARTIALLY_REFUNDED'))
        self.assertEqual(after['order_amount_total'], before['order_amount_total'] - 2500)
        self.assertEqual(daily(after, created_day)['net_amount'], 5500)
        self.assertEqual(daily(after, refund_day)['net_amount'], 0)
        preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refunded = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'reason': '隔离剩余退款验收', 'plan_token': preview.data['data']['plan_token'],
            'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        final = self.client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual((daily(final, created_day)['count'], daily(final, created_day)['net_amount']), (1, 0))

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION',
                       CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
    def test_player_dashboard_cross_day_partial_and_remaining_refund_follow_order_date(self):
        import asyncio
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        role = self.staff.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'order:refund', 'player_dashboard:view']
        role.save(update_fields=['permissions'])
        order = self.run_fulfillment_flow('COMPLETION')
        # Move only the creation timestamp to exercise a refund on a later day;
        # dispatch, payment, fulfillment, settlement and refunds use real endpoints.
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=1))
        order.refresh_from_db()
        created_day = timezone.localtime(order.created_at).date().isoformat()
        refund_day = timezone.localdate().isoformat()
        def stats(day):
            response = self.client.get('/api/admin/dashboard/player', {'start_date': day, 'end_date': day})
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        before = stats(created_day)
        self.assertEqual(before['kpi']['total_amount'], order.amount)
        self.assertEqual(stats(refund_day)['kpi']['total_amount'], 0)
        self.assertTrue(before['snapshot_at'])
        self.assertEqual(before['timezone'], str(timezone.get_current_timezone()))
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)('operators', channel)
        for amount in (2500, None):
            params = {} if amount is None else {'refund_amount': amount}
            preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', params)
            self.assertEqual(preview.data['code'], 0, preview.data)
            data = {'reason': '隔离跨日退款验收', 'plan_token': preview.data['data']['plan_token'], 'client_request_id': str(uuid4())}
            if amount is not None:
                data['refund_amount'] = amount
            with self.captureOnCommitCallbacks(execute=True):
                result = self.client.post(f'/api/admin/orders/{order.pk}/refund/', data, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            async def receive_event():
                return await asyncio.wait_for(layer.receive(channel), timeout=1)
            event = async_to_sync(receive_event)()
            self.assertEqual((event['type'], event['data']['id'], event['data']['payment_status']),
                             ('order_status_update', order.pk, 'PARTIALLY_REFUNDED' if amount else 'REFUNDED'))
            expected = order.amount - 2500 if amount else 0
            current = stats(created_day)
            self.assertEqual(current['kpi']['total_amount'], expected)
            self.assertEqual(stats(refund_day)['kpi']['total_amount'], 0)
            if expected:
                self.assertEqual(current['rank']['amount'][0]['value'], expected)
            else:
                self.assertEqual(current['rank']['amount'], [])

    def test_cancel_paid_pending_order_refunds_wallet_once(self):
        created = self.client.post('/api/admin/orders/dispatch/', self.payload(self.quote()['amount']), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
        result = self.client.post(f'/api/admin/orders/{order.pk}/cancel/', {'reason': '隔离取消验收'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)
        repeated = self.client.post(f'/api/admin/orders/{order.pk}/cancel/', {}, format='json')
        self.assertNotEqual(repeated.data['code'], 0, repeated.data)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_resolve_real_aftersale_without_refunding(self):
        order = self.run_fulfillment_flow('REPORT_APPROVAL')
        boss_token, _ = issue_account_tokens(self.boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        opened = self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {'reason': '隔离售后验收'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        ticket = AfterSale.objects.get(order=order)
        staff_token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        dashboard = self.client.get('/api/admin/dashboard/stats').data['data']
        listed = self.client.get('/api/admin/orders/', {'aftersale_open': '1'}).data['data']
        self.assertEqual(dashboard['open_aftersale_order_total'], 1)
        self.assertEqual(listed['total'], 1)
        self.assertEqual(listed['list'][0]['id'], order.pk)
        excluded = self.client.get('/api/admin/orders/', {'aftersale_open': '1', 'status': 'PENDING'}).data['data']
        self.assertEqual(excluded['total'], 0)
        invalid = self.client.post(f'/api/admin/aftersales/{ticket.pk}/resolve/', {'resolution': '短'}, format='json')
        self.assertEqual(invalid.status_code, 400)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'OPEN')
        resolved = self.client.post(f'/api/admin/aftersales/{ticket.pk}/resolve/', {'resolution': '已沟通确认'}, format='json')
        self.assertEqual(resolved.data['code'], 0, resolved.data)
        ticket.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((ticket.status, order.payment_status), ('RESOLVED', 'PAID'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 90000)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())
        self.assertEqual(self.client.get('/api/admin/dashboard/stats').data['data']['open_aftersale_order_total'], 0)
        self.assertEqual(self.client.get('/api/admin/orders/', {'aftersale_open': '1'}).data['data']['total'], 0)
        repeated = self.client.post(f'/api/admin/aftersales/{ticket.pk}/resolve/', {'resolution': '再次处理'}, format='json')
        self.assertEqual(repeated.status_code, 400)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [permission for permission in role.permissions if permission != 'order:view']
        role.save(update_fields=['permissions'])
        self.assertIsNone(self.client.get('/api/admin/dashboard/stats').data['data']['open_aftersale_order_total'])
        self.assertEqual(self.client.get('/api/admin/orders/', {'aftersale_open': '1'}).status_code, 403)
