"""A multi-gift purchase followed by refunds of only one gift."""
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import GiftBenefitRule, GiftNamingGrant, ProviderNaming, RewardClaim, RewardItem
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftBasketRefundJourneyTest(LiveServerTestCase):
    host = '127.0.0.1'
    port = 19210
    def test_refunding_one_line_updates_naming_and_keeps_other_line(self):
        media = TemporaryDirectory(prefix='xa-basket-refund-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider(is_verified=True, deposit_mode='CASH')
        staff = make_console_user(['wallet:recharge', 'order:view', 'order:refund'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='冠名礼物', is_gift=True)
        gifts = [make_service(name=name, price=price, commission_rate=20, service_category=category, gift_naming_days=days)
                 for name, price, days in [('两天礼物', 4000, 2), ('三天礼物', 6000, 3)]]
        reward = RewardItem.objects.create(code='basket-refund-gift-benefit', name='累计赠礼权益', kind='SERVICE', in_shop=False)
        GiftBenefitRule.objects.create(name='累计赠礼九百币', reward=reward, category=category,
            threshold=9000, inclusive=True, cumulative=True, once_per_account=True, enabled=True)
        clients = []
        for account in accounts:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        ok(clients[2].post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk,
            'amount': 20000, 'gift_amount': 0, 'trade_no': 'basket-refund-recharge',
            'proof_image': screenshot(), 'client_request_id': 'basket-refund-recharge'}, format='multipart'))
        receipt = ok(clients[0].post('/api/orders/gifts/send/', {
            'items': [{'gift_service_id': gift.pk, 'quantity': 1} for gift in gifts],
            'provider_id': accounts[1].pk, 'naming_title': '隔离冠名',
            'expected_amount': 10000, 'client_request_id': 'basket-refund-send'}, format='json'))
        self.assertEqual((len(receipt['orders']), receipt['naming_days']), (2, 5))
        claim = RewardClaim.objects.get(account=accounts[0], reward=reward)
        self.assertEqual(claim.status, 'PENDING')
        first, second = [Order.objects.get(pk=row['id']) for row in receipt['orders']]
        self.assertEqual(GiftNamingGrant.objects.filter(order__in=[first, second]).count(), 2)
        naming = ProviderNaming.objects.get(provider=accounts[1], sponsor=accounts[0])
        initial_expiry = naming.expires_at
        details = []
        for amount, expected_grants in ((1000, 2), (None, 1)):
            params = {'refund_amount': amount} if amount else {}
            preview = ok(clients[2].get(f'/api/admin/orders/{first.pk}/refund-preview/', params))
            payload = {'reason': '隔离冠名退款', 'plan_token': preview['plan_token'],
                       'client_request_id': f'basket-refund-{amount or "remaining"}'}
            if amount:
                payload['refund_amount'] = amount
            ok(clients[2].post(f'/api/admin/orders/{first.pk}/refund/', payload, format='json'))
            naming.refresh_from_db()
            detail = ok(clients[0].get(f'/api/orders/orders/{first.pk}/'))
            result = detail['refund_summary']['records'][0]['gift_naming_result']
            self.assertEqual(result['remaining_paid_grants'], expected_grants)
            self.assertEqual(result['expires_at'], naming.expires_at.isoformat())
            provider_detail = ok(clients[1].get(f'/api/orders/orders/{first.pk}/'))
            self.assertEqual(provider_detail['refund_summary']['records'][0]['gift_naming_result'], result)
            self.assertLess(naming.expires_at, initial_expiry)
            claim.refresh_from_db()
            if amount:
                self.assertEqual(claim.status, 'PENDING')
                self.assertNotIn('gift_claim_results', detail['refund_summary']['records'][0])
            else:
                self.assertEqual(claim.status, 'CANCELLED')
                self.assertEqual(detail['refund_summary']['records'][0]['gift_claim_results'],
                    [{'id': claim.pk, 'name': '累计赠礼权益', 'outcome': 'CANCELLED'}])
                self.assertIn('赠礼退款', ok(clients[0].get(f'/api/club/claims/{claim.pk}/'))['delivery_note'])
                notices = [item for item in ok(clients[0].get('/api/messages/'))['messages'] if item['title'] == '权益状态已调整']
                self.assertEqual(len(notices), 1)
                self.assertIn('1 项未交付权益已撤销', notices[0]['preview'])
                self.assertEqual(ok(clients[0].get(f"/api/messages/{notices[0]['id']}/"))['action_url'], '/pages/points/index?tab=claims')
                if os.environ.get('XA_NATIVE_CLAIM_REFUND_NOTICE') == '1':
                    repo = Path(settings.BASE_DIR).parent.parent
                    check = subprocess.run(['node', str(repo / 'scripts/native-claim-refund-notice-375.cjs')], cwd=repo,
                        input=json.dumps({'noticeId': notices[0]['id'], 'claimId': claim.pk,
                            'session': {'token': issue_account_tokens(accounts[0])[0],
                                'user': {'id': str(accounts[0].pk), 'role': 'customer'}}}),
                        text=True, encoding='utf-8', capture_output=True, timeout=55)
                    self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            admin = ok(clients[2].get(f'/api/admin/orders/{first.pk}/'))
            self.assertEqual(admin['refund_summary']['records'][0]['benefit_effects']['gift_naming']['expires_at'], result['expires_at'])
            if amount and os.environ.get('XA_TEAM_GIFT_BROWSER') == '1':
                repo = Path(settings.BASE_DIR).parent.parent
                browser = subprocess.run(['node', str(repo / 'scripts/team-gift-origin-375.cjs')], cwd=repo,
                    input=json.dumps({'base': self.live_server_url, 'sourceNo': '', 'giftNo': first.order_no,
                        'refundStatus': 'PARTIAL', 'walletIncome': '+240币', 'serviceIncome': '240',
                        'recoveredIncome': '80', 'hasNaming': True, 'namingExpiry': result['expires_at'],
                        'staffToken': issue_account_tokens(accounts[2])[0],
                        'providerRoot': os.environ['XA_TEAM_GIFT_PROVIDER_ROOT'], 'adminRoot': os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                        'provider': {'token': issue_account_tokens(accounts[1])[0], 'id': accounts[1].pk}}),
                    text=True, encoding='utf-8', capture_output=True, timeout=90)
                self.assertEqual(browser.returncode, 0, browser.stdout + browser.stderr)
            if os.environ.get('XA_NATIVE_NAMING_REFUND') == '1':
                self.check_native(accounts[0], first.pk, result, 'partial' if amount else 'first-final')
            details.append(detail)
        second.refresh_from_db()
        self.assertEqual(second.payment_status, 'PAID')
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 14000)
        benefits = ok(clients[1].get('/api/wallet/provider-benefits/'))['gift_records']
        self.assertEqual({row['id']: row['settlement_status'] for row in benefits}, {first.pk: 'REFUNDED', second.pk: 'SETTLED'})
        preview = ok(clients[2].get(f'/api/admin/orders/{second.pk}/refund-preview/'))
        ok(clients[2].post(f'/api/admin/orders/{second.pk}/refund/', {'reason': '隔离剩余礼物退款',
            'plan_token': preview['plan_token'], 'client_request_id': 'basket-refund-second'}, format='json'))
        final = ok(clients[0].get(f'/api/orders/orders/{second.pk}/'))
        self.assertEqual(final['refund_summary']['records'][0]['gift_naming_result']['remaining_paid_grants'], 0)
        if os.environ.get('XA_TEAM_GIFT_BROWSER') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            browser = subprocess.run(['node', str(repo / 'scripts/team-gift-origin-375.cjs')], cwd=repo,
                input=json.dumps({'base': self.live_server_url, 'sourceNo': '', 'giftNo': second.order_no,
                    'refundStatus': 'FULL', 'walletIncome': '0币', 'serviceIncome': '0',
                    'recoveredIncome': '480', 'hasNaming': True, 'noNaming': True,
                    'staffToken': issue_account_tokens(accounts[2])[0],
                    'providerRoot': os.environ['XA_TEAM_GIFT_PROVIDER_ROOT'], 'adminRoot': os.environ['XA_TEAM_GIFT_ADMIN_ROOT'],
                    'provider': {'token': issue_account_tokens(accounts[1])[0], 'id': accounts[1].pk}}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(browser.returncode, 0, browser.stdout + browser.stderr)
        if os.environ.get('XA_NATIVE_NAMING_REFUND') == '1':
            self.check_native(accounts[0], second.pk, final['refund_summary']['records'][0]['gift_naming_result'], 'all-final')
        details.append(final)
        repo = Path(settings.BASE_DIR).parent.parent
        check = subprocess.run(['node', str(repo / 'scripts/native-gift-naming-refund.cjs')], cwd=repo,
            input=json.dumps(details, default=str), text=True, encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def check_native(self, account, order_id, naming_result, phase):
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / '.tools/native-recharge-checkout-375-20261102/native-naming-refund-375.cjs')],
            cwd=repo, input=json.dumps({'base': self.live_server_url, 'orderId': order_id, 'naming': naming_result,
                'phase': phase, 'session': {'token': issue_account_tokens(account)[0],
                    'user': {'id': str(account.pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
